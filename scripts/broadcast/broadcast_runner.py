"""Broadcast runner — sends scheduled AI-generated messages to Teams channels.

Usage:
    python scripts/broadcast/broadcast_runner.py --job <id> [--phase generate|dry-run|send|generate-send]

Two generation modes:
- brief_file (per-section): each ### Message section = one separate Claude call
  with its own timeout. Sections are generated and sent one-by-one.
- prompt (single-call, legacy): one Claude call produces all messages separated
  by ---MSG--- which are then split and sent.

Phase flag:
  generate       — generate all sections, save to generated/<job_id>.json, no sending
  dry-run        — read generated/<job_id>.json, send to the control chat (the owner's notes) as preview
  send           — read generated/<job_id>.json, send to the real channel
  generate-send  — generate then immediately send to the real channel (no intermediate file)
  (omitted)      — legacy all-in-one: generate + send in one run (original behaviour)

Exit codes: 0 = success, 1 = any failure (job disabled, no session,
generation failed, send failed, placeholder channel).
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load .env from project root before any env-dependent imports.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BROADCAST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BROADCAST_DIR.parents[1]

JOBS_FILE = _BROADCAST_DIR / "jobs.json"
LOG_FILE = _BROADCAST_DIR / "broadcast.log.jsonl"
MCP_CONFIG = _PROJECT_ROOT / "mcp-config.json"
GENERATED_DIR = _BROADCAST_DIR / "generated"


def _generated_path(job_id: str) -> Path:
    """Return path to the generated messages file for a job."""
    return GENERATED_DIR / f"{job_id}.json"

_TEAMS_DIR = _PROJECT_ROOT / "scripts" / "teams_agent"

# ---------------------------------------------------------------------------
# Import teams_agent modules
# ---------------------------------------------------------------------------

_JIRA_DIR = _PROJECT_ROOT / "scripts" / "jira"

sys.path.insert(0, str(_JIRA_DIR))
sys.path.insert(0, str(_TEAMS_DIR))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(_BROADCAST_DIR))

import api_client  # noqa: E402
import logts  # noqa: E402, F401, I001 -- side-effect: CET timestamps on stdout/stderr
import org_config  # noqa: E402
import user_memory  # noqa: E402

# Import _MCP_GROUPS from ai.py (single source of truth; side-effect of
# import is _refresh_mcp_config() — this is acceptable).
from ai import _MCP_GROUPS  # noqa: E402
from session import ensure_fresh_session  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLACEHOLDER_CHANNEL = "NOTATKI_CONV_ID_PLACEHOLDER"
_LOCAL_TZ = ZoneInfo(getattr(org_config, "TIMEZONE", "Europe/Warsaw"))
_CHAT_CONFIG_FILE = _PROJECT_ROOT / "chat-config.json"
_SECTION_TIMEOUT_S = 600   # timeout per individual section call
_GENERATE_TIMEOUT_S = 600  # timeout for legacy single-call mode
_MSG_SEPARATOR = "---MSG---"
_INTER_MSG_DELAY_S = 1     # pause between consecutive messages
_GIF_PREFIX = "GIF:"
_BOB_PREFIX = "🤖 **Bob**\n"

# Header pattern that marks the start of a brief section. "Message" is canonical; the
# Polish "Wiadomość"/"Wiadomosc" of older briefs is still accepted.
_SECTION_WORD = r"(?:Message|Wiadomo[śs][ćc])"
_SECTION_RE = re.compile(rf"(?m)^### {_SECTION_WORD} ")

# Tool group names in jobs.json: English canonical <- the older Polish name.
_TOOL_GROUP_ALIASES = {"urlopy": "leave"}


def _job_tools(job: dict) -> list[str]:
    """The job's tool groups with old Polish group names mapped to the English ones."""
    return list(dict.fromkeys(_TOOL_GROUP_ALIASES.get(g, g) for g in job.get("tools") or []))

# ---------------------------------------------------------------------------
# Progress reporting (to control chat)
# ---------------------------------------------------------------------------

_control_channel_cache: str | None = None


def _load_control_channel() -> str | None:
    """Return conv_id of the chat with control_chat=True, or None."""
    try:
        cfg = json.loads(_CHAT_CONFIG_FILE.read_text(encoding="utf-8"))
        import roles  # noqa: PLC0415 -- on the path once the teams_agent imports above have run

        for raw in cfg.get("chats", {}).values():
            chat = roles.normalize_chat(raw)
            if chat.get("control_chat"):
                return chat.get("conv_id")
    except Exception:
        pass
    return None


def _progress(msg: str, skip_channel: str = "") -> None:
    """Send a progress update to the control chat. Fire-and-forget; never raises."""
    global _control_channel_cache
    if _control_channel_cache is None:
        _control_channel_cache = _load_control_channel() or ""
    ch = _control_channel_cache
    if not ch or ch == skip_channel:
        return
    try:
        api_client.send_message(msg, ch)
    except Exception:
        pass


def _section_label(header: str) -> str:
    """'### Message 3 — Absences in the sprint' → 'Absences in the sprint'."""
    m = re.search(r"—\s*(.+)$", header)
    return m.group(1).strip() if m else header.lstrip("# ").strip()


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _team_only_on_outside_chat(job: dict) -> list[str]:
    """The job's team-only groups, when its channel is a chat flagged external_chat in chat-config.json.
    The same rule a chat reply follows; a channel that is not in chat-config.json has no flag to read
    (docs/snowflake-reports.md)."""
    import roles  # noqa: PLC0415 -- on the path once the teams_agent imports above have run

    try:
        chats = json.loads(_CHAT_CONFIG_FILE.read_text(encoding="utf-8")).get("chats", {})
    except (OSError, ValueError, AttributeError):
        chats = {}
    channel = job.get("channel")
    cfgs = [roles.normalize_chat(c) for c in chats.values() if isinstance(c, dict)]
    outside = any(c.get("external_chat") and c.get("conv_id") == channel for c in cfgs)
    return sorted(set(_job_tools(job)) & roles.team_only(roles.load())) if outside else []


def load_job(job_id: str) -> dict | None:
    """Load a job definition from jobs.json by id.

    Returns the job dict, or None if the id is not found.
    Exits with code 1 if the job is disabled (enabled=false).
    """
    try:
        jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[broadcast] ERROR reading jobs.json: {exc}", file=sys.stderr, flush=True)
        return None

    for job in jobs:
        if job.get("id") == job_id:
            if not job.get("enabled", True):
                print(
                    f"[broadcast] Job '{job_id}' is disabled (enabled=false). Stopping.",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(1)
            blocked = _team_only_on_outside_chat(job)
            if blocked:
                print(
                    f"[broadcast] Job '{job_id}' uses team-only groups {blocked} "
                    "in a chat with outside people. Stopping.",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(1)
            return job

    print(f"[broadcast] Job '{job_id}' not found in jobs.json.", file=sys.stderr, flush=True)
    return None


def _build_tools_str(tool_groups: list[str]) -> str:
    """Map tool group names (keys of _MCP_GROUPS) to --allowedTools string."""
    parts = []
    for group in tool_groups:
        if group in _MCP_GROUPS:
            parts.append(_MCP_GROUPS[group])
        else:
            print(
                f"[broadcast] Unknown tool group '{group}' — skipping.",
                file=sys.stderr,
                flush=True,
            )
    return ",".join(parts)


def _call_claude(prompt: str, tools_str: str, timeout_s: int) -> str:
    """Call claude CLI and return the generated text.

    Returns empty string on any failure (timeout, non-zero exit, parse error).
    """
    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--model", "claude-sonnet-5-5",
        # No built-ins at all; --allowedTools lists every MCP tool the job needs
        # explicitly (dontAsk denies anything else, whatever the global permission mode).
        "--tools", "",
        "--allowedTools", tools_str,
        "--mcp-config", str(MCP_CONFIG),
        "--strict-mcp-config",
        "--permission-mode", "dontAsk",
    ]
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[broadcast] claude CLI timed out ({timeout_s}s).",
            file=sys.stderr,
            flush=True,
        )
        return ""
    except FileNotFoundError:
        print("[broadcast] claude CLI not found in PATH.", file=sys.stderr, flush=True)
        return ""

    if result.returncode != 0:
        print(
            f"[broadcast] claude CLI returned code {result.returncode}. stderr: {result.stderr[:500]}",
            file=sys.stderr,
            flush=True,
        )
        return ""

    try:
        parsed = json.loads(result.stdout)
        if parsed.get("is_error"):
            print("[broadcast] claude CLI returned is_error=true.", file=sys.stderr, flush=True)
            return ""
        return parsed.get("result", "").strip()
    except (json.JSONDecodeError, AttributeError) as exc:
        print(f"[broadcast] ERROR parsing the claude reply: {exc}", file=sys.stderr, flush=True)
        return ""


# Keep backward-compatible alias used in legacy single-call mode.
def generate(prompt: str, tools_str: str) -> str:
    return _call_claude(prompt, tools_str, _GENERATE_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Brief parsing (per-section mode)
# ---------------------------------------------------------------------------


def _parse_brief_sections(text: str) -> tuple[str, list[str]]:
    """Split a brief file into (preamble, sections).

    Preamble = everything before the first '### Message' header.
    Sections  = list of '### Message N ...' blocks (header + body).
    """
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return text, []

    preamble = text[: matches[0].start()].rstrip()
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(text[m.start() : end].strip())
    return preamble, sections


# Pattern to detect a template reference line: **Template:** `path` (older briefs: **Szablon:**)
_TEMPLATE_REF_RE = re.compile(r"\*\*(?:Template|Szablon):\*\*\s*`([^`]+)`")

# Patterns that mark "thinking out loud" preamble lines to strip. The Polish openers
# stay: a brief written in Polish still gets Polish narration back from the model.
_PREAMBLE_LINE_RE = re.compile(
    r"^(Mam |Teraz |Pobieram |Obliczam |Generam |Generuję |Piszę |OK,|Sprawdzam |"
    r"Zbiera|Znalaz|Analizuj|Wylicz|Obliczenia:|TeamB members|Suma: |"
    r"I have |I've |I'll |Now I|Now composing|Let me |Based on |Here is|Here's |"
    r"Looking at|Gathering|Fetching|Computing|Calculating|Generating|"
    r"The template file|Sprint number calc|sprint_nr\s*=|Since today is)",
    re.IGNORECASE,
)


def _load_template(brief_dir: Path, template_ref: str) -> str:
    """Load a template file relative to the brief directory."""
    path = brief_dir / template_ref
    if not path.exists():
        print(f"[broadcast] Template not found: {path}", file=sys.stderr, flush=True)
        return ""
    return path.read_text(encoding="utf-8").strip()


def _prev_workday(today: date) -> date:
    """Return the most recent business day before today."""
    dow = today.weekday()  # 0=Mon … 6=Sun
    if dow == 0:    # Monday -> Friday
        return today - timedelta(days=3)
    elif dow == 6:  # Sunday -> Friday
        return today - timedelta(days=2)
    elif dow == 5:  # Saturday -> Friday
        return today - timedelta(days=1)
    else:
        return today - timedelta(days=1)


def _substitute_static_vars(text: str, job: dict) -> str:
    """Replace static vars that the runner knows upfront ({{TEAM}}, {{TODAY}}, {{PREV_WORKDAY}})."""
    team = job.get("team", "")
    if team:
        text = text.replace("{{TEAM}}", team)
    today = date.today()
    prev = _prev_workday(today)
    text = text.replace("{{TODAY}}", today.strftime("%d.%m.%Y"))
    text = text.replace("{{TODAY_ISO}}", today.isoformat())
    text = text.replace("{{PREV_WORKDAY}}", prev.strftime("%d.%m.%Y"))
    text = text.replace("{{PREV_WORKDAY_ISO}}", prev.isoformat())
    return text


def _strip_preamble(text: str) -> str:
    """Remove leading 'thinking out loud' content and stray --- separators.

    Two-pass: first strips leading preamble lines, then checks for a ---
    delimiter that separates remaining calculation blocks from real content.
    """
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "---" or _PREAMBLE_LINE_RE.match(stripped):
            start = i + 1
        else:
            break
    result = "\n".join(lines[start:]).strip()

    # Secondary pass: if a standalone --- appears and earlier content looks like
    # intermediate calculations, strip everything before the last ---.
    parts = re.split(r"(?m)^---$", result)
    if len(parts) > 1:
        earlier = "\n".join(parts[:-1])
        bullet_lines = [ln for ln in earlier.splitlines() if ln.strip().startswith("- ")]
        if (
            re.search(r"(?m)^\s*\|", earlier)  # markdown table
            or re.search(r"Obliczenia|cross-referenced|Suma:|Łącznie:|Calculations|Total:", earlier, re.IGNORECASE)
            or re.search(r"`[^`\n]+`\s*=\s*\S", earlier)   # backtick math: `expr` = value
            or len(bullet_lines) >= 3                        # 3+ bullet reasoning lines
        ):
            result = parts[-1].strip()

    # Strip leaked brief section content (### Message ... header and below).
    result = re.split(rf"(?m)^### {_SECTION_WORD}", result)[0].rstrip()

    return result


_MAX_SECTION_RETRIES = 4  # max re-tries per section when validation fails

_TEMPLATE_INJECT = (
    "\n\n---\n\n"
    "Your task: fetch the data as the instructions above say, "
    "then fill in the template below.\n"
    "RETURN ONLY the filled template — not a word outside it.\n"
    "NO intermediate calculations, working notes, helper tables or section headers.\n"
    "NO markdown tables (| column |) — bullet lists (-) only.\n"
    "Replace ONLY the {{PLACEHOLDERS}} with real data. Do not change any other text.\n"
    "Write in the language the template is written in.\n\n"
    "TEMPLATE:\n"
)

_NO_TEMPLATE_INJECT = (
    "\n\n---\n\n"
    "Your task now: generate ONLY the one message described below.\n"
    "Return ONLY the message text — no section header, no separator, no comment.\n"
    "For a GIF message: return ONLY one line 'GIF: <query>'.\n"
    "CRITICAL: the first line of the reply MUST be the first sentence of the post — no intro.\n\n"
)


def _extract_gif_line(text: str) -> str:
    """Find the first 'GIF: ...' line in text. Returns the line if found, else original text."""
    for line in text.splitlines():
        if line.strip().upper().startswith("GIF:"):
            return line.strip()
    return text


def _validate_section(text: str, is_gif: bool = False) -> list[str]:
    """Return list of requirement violations. Empty = OK to send."""
    issues = []
    if "{{" in text:
        issues.append("unfilled placeholders {{...}}")
    if re.search(rf"(?m)^### {_SECTION_WORD}", text):
        issues.append("leaked section instructions (### Message)")
    if re.search(r"(?m)^\*\*(?:Template|Szablon):\*\*", text):
        issues.append("leaked template name (**Template:**)")
    if re.search(r"(?m)^\s*\|.+\|", text):
        issues.append("markdown table — use a bullet list (-)")
    if _PREAMBLE_LINE_RE.match(text.lstrip()):
        issues.append("message starts with a technical/narration line — remove it")
    if is_gif and not text.upper().startswith("GIF:"):
        issues.append("GIF section must start with 'GIF: <query>'")
    if re.search(r"(?m)^---$", text):
        issues.append("--- separator in the text — probably an unstripped preamble")
    return issues


def _generate_section(preamble: str, section: str, tools_str: str,
                       brief_dir: Path | None = None, job: dict | None = None,
                       label: str = "", skip_channel: str = "") -> str:
    """Call Claude CLI for one brief section, retrying if validation fails."""
    job = job or {}

    template_text = ""
    m = _TEMPLATE_REF_RE.search(section)
    if m and brief_dir:
        template_text = _load_template(brief_dir, m.group(1))
        if template_text:
            template_text = _substitute_static_vars(template_text, job)

    is_gif = template_text.upper().startswith("GIF:") if template_text else False

    if template_text:
        # Section instructions first, template last — Claude returns only the filled template.
        base_prompt = preamble + "\n\n---\n\n" + section + _TEMPLATE_INJECT + template_text
    else:
        base_prompt = preamble + _NO_TEMPLATE_INJECT + section

    prompt = base_prompt
    result = ""

    for attempt in range(1 + _MAX_SECTION_RETRIES):
        raw = _call_claude(prompt, tools_str, _SECTION_TIMEOUT_S)
        result = _strip_preamble(raw)

        if not result:
            break

        # For GIF sections: extract the GIF: line even if surrounded by narration.
        if is_gif:
            result = _extract_gif_line(result)

        issues = _validate_section(result, is_gif=is_gif)
        if not issues:
            break

        issues_str = "\n".join(f"- {i}" for i in issues)
        print(
            f"[broadcast] Validation failed (attempt {attempt + 1}): {', '.join(issues)}",
            file=sys.stderr,
            flush=True,
        )
        if label:
            _progress(
                f"⚠️ {label} — retry {attempt + 2}/{1 + _MAX_SECTION_RETRIES}: {', '.join(issues)}",
                skip_channel=skip_channel,
            )

        if attempt < _MAX_SECTION_RETRIES:
            prompt = (
                base_prompt
                + f"\n\n---\n\nTHE PREVIOUS ATTEMPT HAD ERRORS:\n{issues_str}\n\n"
                "Generate a CORRECT version that fixes all of the errors above. "
                "Return ONLY the filled template."
            )
        else:
            print(
                f"[broadcast] Section did not meet the requirements after {_MAX_SECTION_RETRIES + 1} attempts — "
                "sending the last result.",
                file=sys.stderr,
                flush=True,
            )

    return result


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


def _split_messages(text: str) -> list[str]:
    """Split generated text on ---MSG--- separator (legacy single-call mode)."""
    parts = [m.strip() for m in text.split(_MSG_SEPARATOR) if m.strip()]
    return parts if parts else [text.strip()]


def _is_gif_message(msg: str) -> bool:
    """Return True if the message is a GIF directive (starts with 'GIF:')."""
    return msg.startswith(_GIF_PREFIX)


def _gif_query(msg: str) -> str:
    """Extract the search query from a GIF directive message."""
    return msg[len(_GIF_PREFIX):].strip()


# ---------------------------------------------------------------------------
# Name mention resolution (daily standups / planning briefs)
# ---------------------------------------------------------------------------

# Matches a **bolded** span (brief templates always wrap person names in
# **...**, e.g. "👤 **First Last:**", "- **First Last** → ...").
_BOLD_SPAN_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_NAME_CHARS_RE = re.compile(r"^[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ'-]+$")


def _looks_like_full_name(candidate: str) -> bool:
    """True if *candidate* is 2-3 title-case words (e.g. "Ewa Nowak")."""
    words = candidate.split()
    if not (2 <= len(words) <= 3):
        return False
    return all(_NAME_CHARS_RE.match(w) and w[0].isupper() for w in words)


def _split_name_candidates(text: str) -> list[str]:
    """Split a bold span's content on ", " / " and " (Polish: " i " / " oraz ") (a name list,
    e.g. the {{CONTRIBUTORS}} placeholder: "Ewa Nowak, Ala Kowalska")."""
    parts = re.split(r"\s*,\s*|\s+(?:and|i|oraz)\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _inject_name_mentions(markdown_text: str) -> tuple[str, list[dict]]:
    """Turn **bolded person names** that resolve to a saved Teams profile into
    real @mention chips (never guesses — same one-exact-match-or-skip
    resolution as user_memory.find_profile_by_name). Names with no saved profile are left
    as plain bold text.

    Chip HTML is stashed as \\x00M{i}\\x00 placeholders and substituted back in
    only AFTER api_client.md_to_html() runs, mirroring that function's own
    link/code-block stash — otherwise its HTML-escaping pass would mangle the
    chip markup.
    """
    entries: list[dict] = []
    placeholders: list[str] = []
    itemid = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal itemid
        raw = m.group(1)
        trailing_colon = raw.rstrip().endswith(":")
        core = raw.rstrip(": \t").strip() if trailing_colon else raw.strip()
        candidates = _split_name_candidates(core)
        if not candidates or not all(_looks_like_full_name(c) for c in candidates):
            return m.group(0)

        out_pieces = []
        any_resolved = False
        for name in candidates:
            person = user_memory.find_profile_by_name(name)
            if person:
                any_resolved = True
                chip_html, chip_entries, itemid = api_client.build_mention_chip(
                    person[0], person[1], itemid
                )
                placeholders.append(chip_html)
                out_pieces.append(f"\x00M{len(placeholders) - 1}\x00")
                entries.extend(chip_entries)
            else:
                out_pieces.append(f"**{name}**")
        if not any_resolved:
            return m.group(0)

        result = ", ".join(out_pieces)
        return result + ":" if trailing_colon else result

    stashed = _BOLD_SPAN_RE.sub(_replace, markdown_text)
    html = api_client.md_to_html(stashed)
    for i, chip_html in enumerate(placeholders):
        html = html.replace(f"\x00M{i}\x00", chip_html)
    return html, entries


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------


def _send_one(msg: str, channel: str, idx: int, total: int) -> bool:
    """Send a single message or GIF. Returns True on success."""
    if _is_gif_message(msg):
        query = _gif_query(msg)
        print(f"[broadcast] [{idx}/{total}] GIF query='{query}' ...", flush=True)
        ok = api_client.send_gif(query, channel)
        if not ok:
            fallback = " ".join(query.split()[:2]) or "lets go"
            print(f"[broadcast] GIF fallback 1/2 query='{fallback}' ...", flush=True)
            ok = api_client.send_gif(fallback, channel)
        if not ok:
            print("[broadcast] GIF fallback 2/2 query='lets go' ...", flush=True)
            ok = api_client.send_gif("lets go", channel)
    else:
        html_body, mentions = _inject_name_mentions(msg)
        html = f"<p>🧑‍💻<strong>[Bob]</strong>: {html_body}</p>"
        print(f"[broadcast] [{idx}/{total}] Text {len(msg)} chars ...", flush=True)
        ok = api_client.send_html_message(html, channel, mentions) is not None

    if not ok:
        token_info = api_client._token_debug_info()
        print(
            f"[broadcast] [{idx}/{total}] ERROR: send failed. Token: {token_info}",
            file=sys.stderr,
            flush=True,
        )
    return ok


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------


def _append_log(record: dict) -> None:
    """Append a JSON record to broadcast.log.jsonl (always append, ensure_ascii=False)."""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------


def _run_phase_generate(job_id: str, job: dict) -> bool:
    """Phase generate: call Claude for each section, persist to generated/<job_id>.json.

    Does NOT require a fresh Teams session (no sending). Returns True on success.
    """
    brief_file = job.get("brief_file")
    channel = job.get("channel", "")
    tools_str = _build_tools_str(_job_tools(job))

    if brief_file:
        brief_path = _BROADCAST_DIR / brief_file
        if not brief_path.exists():
            print(f"[broadcast] ERROR: brief_file does not exist: {brief_path}", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "phase": "generate",
                         "success": False, "chars_sent": 0, "error": "brief_not_found"})
            return False

        preamble, sections = _parse_brief_sections(brief_path.read_text(encoding="utf-8"))
        if not sections:
            print("[broadcast] ERROR: the brief has no '### Message' sections.", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "phase": "generate",
                         "success": False, "chars_sent": 0, "error": "no_sections"})
            return False

        n = len(sections)
        job_name = job.get("name") or job.get("team", job_id)
        print(
            f"[broadcast] [generate] Per-section mode: {n} sections, timeout {_SECTION_TIMEOUT_S}s/section.",
            flush=True,
        )
        _progress(f"[generate] Broadcast *{job_name}* — generating {n} sections...", skip_channel=channel)

        results: list[dict] = []
        for i, section in enumerate(sections, 1):
            header = section.splitlines()[0] if section else f"section {i}"
            label = _section_label(header)
            print(f"[broadcast] Generating section {i}/{n}: {header} ...", flush=True)

            t0 = time.monotonic()
            msg = _generate_section(preamble, section, tools_str,
                                    brief_dir=brief_path.parent, job=job,
                                    label=f"[{i}/{n}] {label}", skip_channel=channel)
            elapsed = int(time.monotonic() - t0)

            if not msg:
                print(
                    f"[broadcast] Section {i} — generation failed, skipping.",
                    file=sys.stderr,
                    flush=True,
                )
                _progress(f"[generate] [{i}/{n}] {label} — error ({elapsed}s)", skip_channel=channel)
            else:
                _progress(f"[generate] [{i}/{n}] {label} ({elapsed}s)", skip_channel=channel)
                results.append({"section_idx": i, "content": msg})

        # Atomically write generated messages to generated/<job_id>.json.
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _generated_path(job_id)
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[broadcast] [generate] Saved {len(results)}/{n} sections -> {out_path}", flush=True)

        success = len(results) == n
        _append_log({
            "ts": datetime.now(UTC).isoformat(),
            "job_id": job_id,
            "channel": channel,
            "phase": "generate",
            "success": success,
            "messages_total": n,
            "messages_generated": len(results),
            "chars_sent": 0,
        })
        return success

    else:
        # Legacy single-call mode — generate and write to generated/<job_id>.json.
        prompt = job.get("prompt", "")
        if not prompt:
            print(f"[broadcast] ERROR: no prompt for job '{job_id}'.", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "phase": "generate",
                         "success": False, "chars_sent": 0, "error": "no_prompt"})
            return False

        print(f"[broadcast] [generate] Single-call mode for job '{job_id}' ...", flush=True)
        text = generate(prompt, tools_str)
        if not text:
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "phase": "generate",
                         "success": False, "chars_sent": 0, "error": "empty_generation"})
            return False

        messages = _split_messages(text)
        results = [{"section_idx": i, "content": m} for i, m in enumerate(messages, 1)]

        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _generated_path(job_id)
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[broadcast] [generate] Saved {len(results)} messages -> {out_path}", flush=True)

        _append_log({
            "ts": datetime.now(UTC).isoformat(),
            "job_id": job_id,
            "channel": channel,
            "phase": "generate",
            "success": True,
            "messages_total": len(results),
            "messages_generated": len(results),
            "chars_sent": 0,
        })
        return True


def _render_worklog_post(post) -> tuple[str, list[dict], list[str]]:
    """Worklog post markdown -> (html, mentions, names left untagged).

    Person tokens become @mention chips when an MRI is known (roster `mri`, else
    the saved Teams profile); otherwise bold names. Chips are substituted AFTER
    md_to_html for the same reason as in _inject_name_mentions."""
    entries: list[dict] = []
    chips: dict[int, str] = {}
    untagged: list[str] = []
    md = post.markdown
    itemid = 0
    for i, (m, mention) in enumerate(zip(post.people, post.tag, strict=True)):
        target = None
        if mention:
            target = (m.name, m.mri) if m.mri else user_memory.find_profile_by_name(m.name)
            if not target:
                untagged.append(m.name)
        if target:
            chip_html, chip_entries, itemid = api_client.build_mention_chip(target[0], target[1], itemid)
            chips[i] = chip_html
            entries.extend(chip_entries)
            md = md.replace(f"\x00P{i}\x00", f"\x00M{i}\x00")
        else:
            md = md.replace(f"\x00P{i}\x00", f"**{m.name}**")
    html = api_client.md_to_html(md)
    for i, chip_html in chips.items():
        html = html.replace(f"\x00M{i}\x00", chip_html)
    return html, entries, untagged


def _run_worklog_daily(job_id: str, job: dict, phase: str) -> bool:
    """Daily worklog post (worklog_daily.py): Jira/Tempo fetched directly, per person.

    dry-run -> post + coverage report to the control chat; send / generate-send -> team channel,
    unless any member's data failed to load (then the control chat only). 'generate' is a no-op
    preview on stdout (nothing to persist -- data is fetched fresh at send time)."""
    import jira_client  # noqa: PLC0415 -- scripts/jira is on sys.path (top of module)
    import worklog_daily as wd  # noqa: PLC0415

    def _log(success: bool, channel: str, error: str = "", chars: int = 0) -> None:
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id, "channel": channel,
                     "phase": phase, "success": success, "chars_sent": chars,
                     **({"error": error} if error else {})})

    today = date.today()
    if not wd.is_workday(today):
        print(f"[worklog-daily] {today} is not a working day -- skipping.", flush=True)
        _log(True, "", "not_workday")
        return True
    # "worklog-missing" = midday recap over several teams (job["teams"]), no AI.
    recap = job.get("generator") == "worklog-missing"
    teams = job.get("teams", []) if recap else [job.get("team", "")]
    day = wd.prev_workday(today)
    vacations, leave_at = wd.load_vacations(day)
    if not recap and job.get("channel"):
        # @tags: learn MRIs of people who ever wrote in the team chat (best-effort).
        try:
            added = wd.harvest_mris(api_client.get_messages(job["channel"], n=200))
            if added:
                print(f"[worklog-daily] new MRIs from chat history: {', '.join(added)}", flush=True)
        except Exception as exc:  # noqa: BLE001 -- tagging is optional, the post is not
            print(f"[worklog-daily] collecting MRIs failed: {exc}", file=sys.stderr, flush=True)
    by_team: dict[str, list] = {}
    for team in teams:
        try:
            roster = wd.load_roster(team)
        except (OSError, KeyError, ValueError) as exc:
            print(f"[worklog-daily] {exc}", file=sys.stderr, flush=True)
            _log(False, "", "roster")
            return False
        by_team[team] = wd.collect_team_day(team, day, jira_client._get, roster, vacations)
    members = [m for ms in by_team.values() for m in ms]
    if recap:
        team = job.get("name", job_id)
        post = wd.build_missing_post(day, by_team)
    else:
        team = teams[0]
        intro = wd.summarize(team, day, members,
                             lambda prompt: _call_claude(prompt, "", _SECTION_TIMEOUT_S))
        post = wd.build_post(team, day, members, intro)

    if phase == "generate":
        print(post.markdown.replace("\x00", "|"), flush=True)
        _log(True, "")
        return True

    if not ensure_fresh_session():
        print("[worklog-daily] ERROR: the Teams session has expired.", file=sys.stderr, flush=True)
        _log(False, "", "no_session")
        return False

    control = _load_control_channel() or api_client.NOTES_CONV_ID
    has_errors = any(m.status == "error" for m in members)
    channel = control if (phase == "dry-run" or has_errors) else job.get("channel", "")
    if not channel or channel == _PLACEHOLDER_CHANNEL:
        _log(False, channel, "placeholder_channel")
        return False

    html_body, mentions, untagged = _render_worklog_post(post)
    html = f"<p>🧑‍💻<strong>[Bob]</strong>: {html_body}</p>"
    ok = api_client.send_html_message(html, channel, mentions) is not None
    report = wd.control_report(team, day, members, leave_at, untagged)
    print(report, flush=True)
    if phase == "dry-run" or has_errors or untagged or "⚠️" in report:
        api_client.send_message(report, control)
    _log(ok and not has_errors, channel, "fetch_errors" if has_errors else "", len(post.markdown))
    return ok and not has_errors


def _load_generated(job_id: str) -> list[dict] | None:
    """Load generated messages from generated/<job_id>.json.

    Returns the list, or None if the file is missing or unreadable.
    """
    path = _generated_path(job_id)
    if not path.exists():
        print(
            f"[broadcast] ERROR: no generated file: {path}. Run --phase generate first.",
            file=sys.stderr,
            flush=True,
        )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[broadcast] ERROR reading {path}: {exc}", file=sys.stderr, flush=True)
        return None


def _sent_before(job_id: str, channel: str, since: datetime | None = None) -> bool:
    """True when broadcast.log.jsonl has a successful send of this job to this channel (after ``since``)."""
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if (e.get("job_id") == job_id and e.get("channel") == channel and e.get("success")
                and e.get("phase") in ("send", "generate-send") and e.get("chars_sent")):
            if since is None:
                return True
            try:
                if datetime.fromisoformat(e.get("ts", "")) >= since:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _skip_generate_send(job_id: str, job: dict, now: datetime | None = None) -> str:
    """Why a scheduled generate-send must not run now ("" = go ahead).

    * a "weekly" job only fires on its own weekday (org_config.TIMEZONE; 0 = Monday) - a run started on another day
      (a manual call, a watch loop that restarted) does nothing;
    * the same job already sent to its channel today - a watch-loop restart inside the firing window
      must not post the report twice."""
    now = now or datetime.now(_LOCAL_TZ)
    if job.get("schedule_type") == "weekly" and now.weekday() != int(job.get("weekday", 0)):
        return f"today is not the job's day (weekday={job.get('weekday', 0)})"
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if _sent_before(job_id, job.get("channel", ""), since=midnight):
        return "already sent today"
    return ""


def _run_phase_dry_run(job_id: str, job: dict) -> bool:
    """Phase dry-run: read generated/<job_id>.json, send all messages to control chat.

    Requires a fresh Teams session. Returns True on success.
    """
    if not ensure_fresh_session():
        print(
            "[broadcast] ERROR: the Teams session has expired or does not exist.",
            file=sys.stderr, flush=True,
        )
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": "", "phase": "dry-run",
                     "success": False, "chars_sent": 0, "error": "no_session"})
        return False

    messages = _load_generated(job_id)
    if messages is None:
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": "", "phase": "dry-run",
                     "success": False, "chars_sent": 0, "error": "no_generated_file"})
        return False

    control_conv = _load_control_channel()
    if not control_conv:
        print("[broadcast] ERROR: no control chat (control_chat) in chat-config.json.",
              file=sys.stderr, flush=True)
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": "", "phase": "dry-run",
                     "success": False, "chars_sent": 0, "error": "no_control_channel"})
        return False

    team = job.get("team", job_id)
    n = len(messages)
    print(f"[broadcast] [dry-run] Sending {n} messages to the control chat ...", flush=True)
    _progress(f"[dry-run] Broadcast *{team}* — previewing {n} messages in the control chat...")

    sent_count = 0
    total_chars = 0
    for idx, item in enumerate(messages):
        msg = item.get("content", "")
        section_idx = item.get("section_idx", idx + 1)
        if not msg:
            continue
        ok = _send_one(msg, control_conv, section_idx, n)
        if ok:
            sent_count += 1
            total_chars += len(msg)
        if idx < len(messages) - 1:
            time.sleep(_INTER_MSG_DELAY_S)

    success = sent_count == n
    _append_log({
        "ts": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "channel": control_conv,
        "phase": "dry-run",
        "success": success,
        "messages_total": n,
        "messages_sent": sent_count,
        "chars_sent": total_chars,
    })
    if success:
        _progress(f"[dry-run] Broadcast *{team}* done — {sent_count}/{n} sent to the control chat")
    else:
        _progress(f"[dry-run] Broadcast *{team}* — {sent_count}/{n} sent to the control chat")
    return success


def _run_phase_send(job_id: str, job: dict) -> bool:
    """Phase send: read generated/<job_id>.json, send all messages to the real channel.

    Requires a fresh Teams session. Returns True on success.
    """
    if not ensure_fresh_session():
        print(
            "[broadcast] ERROR: the Teams session has expired or does not exist.",
            file=sys.stderr, flush=True,
        )
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": "", "phase": "send",
                     "success": False, "chars_sent": 0, "error": "no_session"})
        return False

    channel = job.get("channel", "")
    if channel == _PLACEHOLDER_CHANNEL:
        print(
            "[broadcast] ERROR: the target channel is a placeholder. Fill in 'channel' in jobs.json.",
            file=sys.stderr, flush=True,
        )
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": channel, "phase": "send",
                     "success": False, "chars_sent": 0, "error": "placeholder_channel"})
        return False

    messages = _load_generated(job_id)
    if messages is None:
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": channel, "phase": "send",
                     "success": False, "chars_sent": 0, "error": "no_generated_file"})
        return False

    team = job.get("team", job_id)
    n = len(messages)
    print(f"[broadcast] [send] Sending {n} messages to channel {channel} ...", flush=True)
    _progress(f"[send] Broadcast *{team}* — sending {n} messages...", skip_channel=channel)

    sent_count = 0
    total_chars = 0
    for idx, item in enumerate(messages):
        msg = item.get("content", "")
        section_idx = item.get("section_idx", idx + 1)
        if not msg:
            continue
        ok = _send_one(msg, channel, section_idx, n)
        if ok:
            sent_count += 1
            total_chars += len(msg)
        if idx < len(messages) - 1:
            time.sleep(_INTER_MSG_DELAY_S)

    success = sent_count == n
    _append_log({
        "ts": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "channel": channel,
        "phase": "send",
        "success": success,
        "messages_total": n,
        "messages_sent": sent_count,
        "chars_sent": total_chars,
    })
    if success:
        _progress(f"[send] Broadcast *{team}* done — {sent_count}/{n} sent", skip_channel=channel)
    else:
        _progress(f"[send] Broadcast *{team}* — {sent_count}/{n} sent", skip_channel=channel)
    return success


def run_job(job_id: str, phase: str = "all") -> bool:
    """Execute a broadcast job. Returns True on success, False on any failure.

    phase: 'generate' | 'dry-run' | 'send' | 'all' (legacy all-in-one)
    """
    # For generate phase: session check is skipped (no Teams API calls).
    # For dry-run / send: each phase function handles session check independently.
    # For 'all' (legacy): we need session upfront.

    if phase == "generate":
        job = load_job(job_id)
        if job is None:
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": "", "phase": "generate",
                         "success": False, "chars_sent": 0, "error": "job_not_found"})
            return False
        if job.get("generator") in ("worklog-daily", "worklog-missing"):
            return _run_worklog_daily(job_id, job, "generate")
        return _run_phase_generate(job_id, job)

    if phase == "dry-run":
        job = load_job(job_id)
        if job is None:
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": "", "phase": "dry-run",
                         "success": False, "chars_sent": 0, "error": "job_not_found"})
            return False
        if job.get("generator") in ("worklog-daily", "worklog-missing"):
            return _run_worklog_daily(job_id, job, "dry-run")
        return _run_phase_dry_run(job_id, job)

    if phase == "send":
        job = load_job(job_id)
        if job is None:
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": "", "phase": "send",
                         "success": False, "chars_sent": 0, "error": "job_not_found"})
            return False
        if job.get("generator") in ("worklog-daily", "worklog-missing"):
            return _run_worklog_daily(job_id, job, "send")
        return _run_phase_send(job_id, job)

    if phase == "generate-send":
        job = load_job(job_id)
        if job is None:
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": "", "phase": "generate-send",
                         "success": False, "chars_sent": 0, "error": "job_not_found"})
            return False
        if job.get("generator") in ("worklog-daily", "worklog-missing"):
            return _run_worklog_daily(job_id, job, "generate-send")
        skip = _skip_generate_send(job_id, job)
        if skip:
            print(f"[broadcast] generate-send {job_id}: {skip} -- skipping.", flush=True)
            return True
        ok = _run_phase_generate(job_id, job)
        if not ok:
            return False
        return _run_phase_send(job_id, job)

    # -------------------------------------------------------------------
    # Legacy 'all' mode: generate + send in one run (original behaviour).
    # -------------------------------------------------------------------
    if not ensure_fresh_session():
        print(
            "[broadcast] ERROR: the Teams session has expired or does not exist. Start Bob to refresh it.",
            file=sys.stderr,
            flush=True,
        )
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": "", "success": False, "chars_sent": 0, "error": "no_session"})
        return False

    job = load_job(job_id)
    if job is None:
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": "", "success": False, "chars_sent": 0, "error": "job_not_found"})
        return False

    channel = job.get("channel", "")

    if channel == _PLACEHOLDER_CHANNEL:
        print(
            "[broadcast] ERROR: the target channel is a placeholder. Fill in 'channel' in jobs.json.",
            file=sys.stderr,
            flush=True,
        )
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": channel, "success": False, "chars_sent": 0,
                     "error": "placeholder_channel"})
        return False

    tools_str = _build_tools_str(_job_tools(job))

    # ------------------------------------------------------------------
    # Per-section mode (brief_file) vs. single-call mode (prompt)
    # ------------------------------------------------------------------
    brief_file = job.get("brief_file")

    if brief_file:
        brief_path = _BROADCAST_DIR / brief_file
        if not brief_path.exists():
            print(f"[broadcast] ERROR: brief_file does not exist: {brief_path}", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "success": False, "chars_sent": 0,
                         "error": "brief_not_found"})
            return False

        preamble, sections = _parse_brief_sections(brief_path.read_text(encoding="utf-8"))
        if not sections:
            print("[broadcast] ERROR: the brief has no '### Message' sections.", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "success": False, "chars_sent": 0,
                         "error": "no_sections"})
            return False

        n = len(sections)
        team = job.get("team", job_id)
        print(
            f"[broadcast] Per-section mode: {n} sections, timeout {_SECTION_TIMEOUT_S}s/section.",
            flush=True,
        )
        _progress(f"[all] Broadcast *{team}* — generating {n} sections...", skip_channel=channel)

        # Phase 1: generate all sections before sending anything.
        generated: list[tuple[int, str]] = []
        for i, section in enumerate(sections, 1):
            header = section.splitlines()[0] if section else f"section {i}"
            label = _section_label(header)
            print(f"[broadcast] Generating section {i}/{n}: {header} ...", flush=True)

            t0 = time.monotonic()
            msg = _generate_section(preamble, section, tools_str,
                                    brief_dir=brief_path.parent, job=job,
                                    label=f"[{i}/{n}] {label}", skip_channel=channel)
            elapsed = int(time.monotonic() - t0)

            if not msg:
                print(
                    f"[broadcast] Section {i} — generation failed, not sending it.",
                    file=sys.stderr,
                    flush=True,
                )
                _progress(f"[all] [{i}/{n}] {label} — error ({elapsed}s)", skip_channel=channel)
            else:
                _progress(f"[all] [{i}/{n}] {label} ({elapsed}s)", skip_channel=channel)
            generated.append((i, msg))

        # Phase 2: send all generated messages in sequence.
        ready = sum(1 for _, m in generated if m)
        print(f"[broadcast] Sending {ready}/{n} messages ...", flush=True)
        _progress(f"[all] Sending {ready}/{n} messages -> *{team}*...", skip_channel=channel)

        sent_count = 0
        total_chars = 0

        for idx, (i, msg) in enumerate(generated):
            if not msg:
                continue
            ok = _send_one(msg, channel, i, n)
            if ok:
                sent_count += 1
                total_chars += len(msg)
            if idx < len(generated) - 1:
                time.sleep(_INTER_MSG_DELAY_S)

        if sent_count == n:
            _progress(f"[all] Broadcast *{team}* done — {sent_count}/{n} sent", skip_channel=channel)
        else:
            _progress(f"[all] Broadcast *{team}* — {sent_count}/{n} sent", skip_channel=channel)

        success = sent_count == n

    else:
        # Legacy single-call mode.
        prompt = job.get("prompt", "")
        if not prompt:
            print(f"[broadcast] ERROR: no prompt for job '{job_id}'.", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "success": False, "chars_sent": 0,
                         "error": "no_prompt"})
            return False

        print(f"[broadcast] Single-call mode for job '{job_id}' ...", flush=True)
        text = generate(prompt, tools_str)

        if not text:
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "success": False, "chars_sent": 0,
                         "error": "empty_generation"})
            return False

        messages = _split_messages(text)
        print(f"[broadcast] Generated {len(messages)} message(s).", flush=True)

        sent_count = 0
        total_chars = 0
        for i, msg in enumerate(messages, 1):
            ok = _send_one(msg, channel, i, len(messages))
            if ok:
                sent_count += 1
                total_chars += len(msg)
            if i < len(messages):
                time.sleep(_INTER_MSG_DELAY_S)

        success = sent_count == len(messages)

    _append_log({
        "ts": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "channel": channel,
        "success": success,
        "messages_total": len(sections) if brief_file else len(messages),  # type: ignore[possibly-undefined]
        "messages_sent": sent_count,
        "chars_sent": total_chars,
    })

    return success


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bob broadcast runner — sends scheduled messages to Teams channels."
    )
    parser.add_argument(
        "--job",
        required=True,
        metavar="ID",
        help="ID of the job to run (must exist in jobs.json and have enabled=true).",
    )
    parser.add_argument(
        "--phase",
        choices=["generate", "dry-run", "send", "generate-send"],
        default=None,
        metavar="PHASE",
        help=(
            "Phase to run: generate (generate and save), "
            "dry-run (send a preview to the control chat), send (send to the target channel), "
            "generate-send (generate and send with no intermediate file). "
            "No flag: all-in-one mode (legacy)."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    phase = args.phase if args.phase is not None else "all"
    sys.exit(0 if run_job(args.job, phase) else 1)
