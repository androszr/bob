"""Broadcast runner — sends scheduled AI-generated messages to Teams channels.

Usage:
    python scripts/broadcast/broadcast_runner.py --job <id> [--phase generate|dry-run|send|generate-send]

Two generation modes:
- brief_file (per-section): each ### Wiadomosc section = one separate Claude call
  with its own timeout. Sections are generated and sent one-by-one.
- prompt (single-call, legacy): one Claude call produces all messages separated
  by ---MSG--- which are then split and sent.

Phase flag:
  generate       — generate all sections, save to generated/<job_id>.json, no sending
  dry-run        — read generated/<job_id>.json, send to control chat (Notatki) as preview
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
import logts  # noqa: E402, F401, I001 -- side-effect: CET timestamps na stdout/stderr
import user_memory  # noqa: E402

# Import _MCP_GROUPS from ai.py (single source of truth; side-effect of
# import is _refresh_mcp_config() — this is acceptable).
from ai import _MCP_GROUPS  # noqa: E402
from session import ensure_fresh_session  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLACEHOLDER_CHANNEL = "NOTATKI_CONV_ID_PLACEHOLDER"
_CHAT_CONFIG_FILE = _PROJECT_ROOT / "chat-config.json"
_SECTION_TIMEOUT_S = 600   # timeout per individual section call
_GENERATE_TIMEOUT_S = 600  # timeout for legacy single-call mode
_MSG_SEPARATOR = "---MSG---"
_INTER_MSG_DELAY_S = 1     # pause between consecutive messages
_GIF_PREFIX = "GIF:"
_BOB_PREFIX = "🤖 **Bob**\n"

# Header pattern that marks the start of a brief section.
_SECTION_RE = re.compile(r"(?m)^### Wiadomość ")

# ---------------------------------------------------------------------------
# Progress reporting (to control chat)
# ---------------------------------------------------------------------------

_control_channel_cache: str | None = None


def _load_control_channel() -> str | None:
    """Return conv_id of the chat with control_chat=True, or None."""
    try:
        cfg = json.loads(_CHAT_CONFIG_FILE.read_text(encoding="utf-8"))
        for chat in cfg.get("chats", {}).values():
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
    """'### Wiadomość 3 — Nieobecności w Sprincie' → 'Nieobecności w Sprincie'."""
    m = re.search(r"—\s*(.+)$", header)
    return m.group(1).strip() if m else header.lstrip("# ").strip()


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def load_job(job_id: str) -> dict | None:
    """Load a job definition from jobs.json by id.

    Returns the job dict, or None if the id is not found.
    Exits with code 1 if the job is disabled (enabled=false).
    """
    try:
        jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[broadcast] Blad odczytu jobs.json: {exc}", file=sys.stderr, flush=True)
        return None

    for job in jobs:
        if job.get("id") == job_id:
            if not job.get("enabled", True):
                print(
                    f"[broadcast] Job '{job_id}' jest wylaczony (enabled=false). Przerywam.",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(1)
            return job

    print(f"[broadcast] Job '{job_id}' nie znaleziony w jobs.json.", file=sys.stderr, flush=True)
    return None


def _build_tools_str(tool_groups: list[str]) -> str:
    """Map tool group names (keys of _MCP_GROUPS) to --allowedTools string."""
    parts = []
    for group in tool_groups:
        if group in _MCP_GROUPS:
            parts.append(_MCP_GROUPS[group])
        else:
            print(
                f"[broadcast] Nieznana grupa narzedzi '{group}' — pomijam.",
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
            f"[broadcast] claude CLI przekroczyl timeout ({timeout_s}s).",
            file=sys.stderr,
            flush=True,
        )
        return ""
    except FileNotFoundError:
        print("[broadcast] claude CLI nie znaleziony w PATH.", file=sys.stderr, flush=True)
        return ""

    if result.returncode != 0:
        print(
            f"[broadcast] claude CLI zwrocil kod {result.returncode}. stderr: {result.stderr[:500]}",
            file=sys.stderr,
            flush=True,
        )
        return ""

    try:
        parsed = json.loads(result.stdout)
        if parsed.get("is_error"):
            print("[broadcast] claude CLI zwrocil is_error=true.", file=sys.stderr, flush=True)
            return ""
        return parsed.get("result", "").strip()
    except (json.JSONDecodeError, AttributeError) as exc:
        print(f"[broadcast] Blad parsowania odpowiedzi claude: {exc}", file=sys.stderr, flush=True)
        return ""


# Keep backward-compatible alias used in legacy single-call mode.
def generate(prompt: str, tools_str: str) -> str:
    return _call_claude(prompt, tools_str, _GENERATE_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Brief parsing (per-section mode)
# ---------------------------------------------------------------------------


def _parse_brief_sections(text: str) -> tuple[str, list[str]]:
    """Split a brief file into (preamble, sections).

    Preamble = everything before the first '### Wiadomosc' header.
    Sections  = list of '### Wiadomosc N ...' blocks (header + body).
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


# Pattern to detect a template reference line: **Szablon:** `path`
_TEMPLATE_REF_RE = re.compile(r"\*\*Szablon:\*\*\s*`([^`]+)`")

# Patterns that mark "thinking out loud" preamble lines to strip.
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
        print(f"[broadcast] Template nie znaleziony: {path}", file=sys.stderr, flush=True)
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
            or re.search(r"Obliczenia|cross-referenced|Suma:|Łącznie:", earlier, re.IGNORECASE)
            or re.search(r"`[^`\n]+`\s*=\s*\S", earlier)   # backtick math: `expr` = value
            or len(bullet_lines) >= 3                        # 3+ bullet reasoning lines
        ):
            result = parts[-1].strip()

    # Strip leaked brief section content (### Wiadomość ... header and below).
    result = re.split(r"(?m)^### Wiadomość", result)[0].rstrip()

    return result


_MAX_SECTION_RETRIES = 4  # max re-tries per section when validation fails

_TEMPLATE_INJECT = (
    "\n\n---\n\n"
    "Twoje zadanie: pobierz dane zgodnie z powyższymi instrukcjami, "
    "a następnie wypełnij poniższy szablon.\n"
    "ZWRÓĆ WYŁĄCZNIE wypełniony szablon — zero słów poza nim.\n"
    "ZAKAZ: obliczeń pośrednich, notatek roboczych, tabel pomocniczych, nagłówków sekcji.\n"
    "ZAKAZ tabel markdown (| kolumna |) — tylko listy punktowe (-).\n"
    "Zastąp TYLKO {{PLACEHOLDERS}} prawdziwymi danymi. Nie zmieniaj żadnego innego tekstu.\n\n"
    "SZABLON:\n"
)

_NO_TEMPLATE_INJECT = (
    "\n\n---\n\n"
    "Twoje zadanie teraz: wygeneruj TYLKO tę jedną wiadomość opisaną poniżej.\n"
    "Zwróć WYŁĄCZNIE treść wiadomości — bez nagłówka sekcji, bez separatora, bez komentarza.\n"
    "Dla wiadomości GIF: zwróć WYŁĄCZNIE jedną linię 'GIF: <query>'.\n"
    "KRYTYCZNE: pierwsza linia odpowiedzi MUSI być pierwszym zdaniem posta — zero wstępów.\n\n"
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
        issues.append("nieuzupełnione placeholdery {{...}}")
    if re.search(r"(?m)^### Wiadomość", text):
        issues.append("wyciek instrukcji sekcji (### Wiadomość)")
    if re.search(r"(?m)^\*\*Szablon:\*\*", text):
        issues.append("wyciek nazwy szablonu (**Szablon:**)")
    if re.search(r"(?m)^\s*\|.+\|", text):
        issues.append("tabela markdown — użyj listy punktowej (-)")
    if _PREAMBLE_LINE_RE.match(text.lstrip()):
        issues.append("wiadomość zaczyna się od linii technicznej/narracyjnej — usuń ją")
    if is_gif and not text.upper().startswith("GIF:"):
        issues.append("sekcja GIF musi zaczynać się od 'GIF: <query>'")
    if re.search(r"(?m)^---$", text):
        issues.append("separator --- w treści — prawdopodobnie niezestripowana preambuła")
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
            f"[broadcast] Walidacja nieudana (próba {attempt + 1}): {', '.join(issues)}",
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
                + f"\n\n---\n\nPOPRZEDNIA PRÓBA ZAWIERAŁA BŁĘDY:\n{issues_str}\n\n"
                "Wygeneruj POPRAWNĄ wersję eliminując wszystkie powyższe błędy. "
                "Zwróć WYŁĄCZNIE wypełniony szablon."
            )
        else:
            print(
                f"[broadcast] Sekcja nie spełniła wymagań po {_MAX_SECTION_RETRIES + 1} próbach — "
                "wysyłam ostatni wynik.",
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
# **...**, e.g. "👤 **Imię Nazwisko:**", "- **Imię Nazwisko** → ...").
_BOLD_SPAN_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_NAME_CHARS_RE = re.compile(r"^[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ'-]+$")


def _looks_like_full_name(candidate: str) -> bool:
    """True if *candidate* is 2-3 title-case words (e.g. "Ewa Nowak")."""
    words = candidate.split()
    if not (2 <= len(words) <= 3):
        return False
    return all(_NAME_CHARS_RE.match(w) and w[0].isupper() for w in words)


def _split_name_candidates(text: str) -> list[str]:
    """Split a bold span's content on ", " / " i " / " oraz " (a name list,
    e.g. the {{CONTRIBUTORS}} placeholder: "Ewa Nowak, Ala Kowalska")."""
    parts = re.split(r"\s*,\s*|\s+i\s+|\s+oraz\s+", text.strip())
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
        print(f"[broadcast] [{idx}/{total}] Tekst {len(msg)} znakow ...", flush=True)
        ok = api_client.send_html_message(html, channel, mentions) is not None

    if not ok:
        token_info = api_client._token_debug_info()
        print(
            f"[broadcast] [{idx}/{total}] Wysylka nieudana. Token: {token_info}",
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
    tools_str = _build_tools_str(job.get("tools", []))

    if brief_file:
        brief_path = _BROADCAST_DIR / brief_file
        if not brief_path.exists():
            print(f"[broadcast] brief_file nie istnieje: {brief_path}", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "phase": "generate",
                         "success": False, "chars_sent": 0, "error": "brief_not_found"})
            return False

        preamble, sections = _parse_brief_sections(brief_path.read_text(encoding="utf-8"))
        if not sections:
            print("[broadcast] Brief nie zawiera sekcji '### Wiadomosc'.", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "phase": "generate",
                         "success": False, "chars_sent": 0, "error": "no_sections"})
            return False

        n = len(sections)
        job_name = job.get("name") or job.get("team", job_id)
        print(
            f"[broadcast] [generate] Tryb per-sekcja: {n} sekcji, timeout {_SECTION_TIMEOUT_S}s/sekcja.",
            flush=True,
        )
        _progress(f"[generate] Broadcast *{job_name}* — generuje {n} sekcji...", skip_channel=channel)

        results: list[dict] = []
        for i, section in enumerate(sections, 1):
            header = section.splitlines()[0] if section else f"sekcja {i}"
            label = _section_label(header)
            print(f"[broadcast] Generowanie sekcji {i}/{n}: {header} ...", flush=True)

            t0 = time.monotonic()
            msg = _generate_section(preamble, section, tools_str,
                                    brief_dir=brief_path.parent, job=job,
                                    label=f"[{i}/{n}] {label}", skip_channel=channel)
            elapsed = int(time.monotonic() - t0)

            if not msg:
                print(
                    f"[broadcast] Sekcja {i} — generowanie nieudane, pomijam.",
                    file=sys.stderr,
                    flush=True,
                )
                _progress(f"[generate] [{i}/{n}] {label} — blad ({elapsed}s)", skip_channel=channel)
            else:
                _progress(f"[generate] [{i}/{n}] {label} ({elapsed}s)", skip_channel=channel)
                results.append({"section_idx": i, "content": msg})

        # Atomically write generated messages to generated/<job_id>.json.
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _generated_path(job_id)
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[broadcast] [generate] Zapisano {len(results)}/{n} sekcji -> {out_path}", flush=True)

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
            print(f"[broadcast] Brak promptu dla joba '{job_id}'.", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "phase": "generate",
                         "success": False, "chars_sent": 0, "error": "no_prompt"})
            return False

        print(f"[broadcast] [generate] Tryb single-call dla joba '{job_id}' ...", flush=True)
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
        print(f"[broadcast] [generate] Zapisano {len(results)} wiadomosci -> {out_path}", flush=True)

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

    dry-run -> post + coverage report to Notatki; send / generate-send -> team channel,
    unless any member's data failed to load (then Notatki only). 'generate' is a no-op
    preview on stdout (nothing to persist -- data is fetched fresh at send time)."""
    import jira_client  # noqa: PLC0415 -- scripts/jira is on sys.path (top of module)
    import worklog_daily as wd  # noqa: PLC0415

    def _log(success: bool, channel: str, error: str = "", chars: int = 0) -> None:
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id, "channel": channel,
                     "phase": phase, "success": success, "chars_sent": chars,
                     **({"error": error} if error else {})})

    today = date.today()
    if not wd.is_workday(today):
        print(f"[worklog-daily] {today} to nie dzien roboczy -- pomijam.", flush=True)
        _log(True, "", "not_workday")
        return True
    # "worklog-missing" = midday recap over several teams (job["teams"]), no AI.
    recap = job.get("generator") == "worklog-missing"
    teams = job.get("teams", []) if recap else [job.get("team", "")]
    day = wd.prev_workday(today)
    vacations, urlopy_at = wd.load_vacations(day)
    if not recap and job.get("channel"):
        # @tags: learn MRIs of people who ever wrote in the team chat (best-effort).
        try:
            added = wd.harvest_mris(api_client.get_messages(job["channel"], n=200))
            if added:
                print(f"[worklog-daily] nowe MRI z historii czatu: {', '.join(added)}", flush=True)
        except Exception as exc:  # noqa: BLE001 -- tagging is optional, the post is not
            print(f"[worklog-daily] zbieranie MRI nieudane: {exc}", file=sys.stderr, flush=True)
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
        print("[worklog-daily] Sesja Teams wygasla.", file=sys.stderr, flush=True)
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
    report = wd.control_report(team, day, members, urlopy_at, untagged)
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
            f"[broadcast] Brak pliku generated: {path}. Najpierw uruchom --phase generate.",
            file=sys.stderr,
            flush=True,
        )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[broadcast] Blad odczytu {path}: {exc}", file=sys.stderr, flush=True)
        return None


def _run_phase_dry_run(job_id: str, job: dict) -> bool:
    """Phase dry-run: read generated/<job_id>.json, send all messages to control chat.

    Requires a fresh Teams session. Returns True on success.
    """
    if not ensure_fresh_session():
        print(
            "[broadcast] Sesja Teams wygasla lub nie istnieje.",
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

    notatki_conv = _load_control_channel()
    if not notatki_conv:
        print("[broadcast] Brak kanalu kontrolnego (control_chat) w chat-config.json.",
              file=sys.stderr, flush=True)
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": "", "phase": "dry-run",
                     "success": False, "chars_sent": 0, "error": "no_control_channel"})
        return False

    team = job.get("team", job_id)
    n = len(messages)
    print(f"[broadcast] [dry-run] Wysylanie {n} wiadomosci na kanal kontrolny ...", flush=True)
    _progress(f"[dry-run] Broadcast *{team}* — {n} wiadomosci podglad na Notatki...")

    sent_count = 0
    total_chars = 0
    for idx, item in enumerate(messages):
        msg = item.get("content", "")
        section_idx = item.get("section_idx", idx + 1)
        if not msg:
            continue
        ok = _send_one(msg, notatki_conv, section_idx, n)
        if ok:
            sent_count += 1
            total_chars += len(msg)
        if idx < len(messages) - 1:
            time.sleep(_INTER_MSG_DELAY_S)

    success = sent_count == n
    _append_log({
        "ts": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "channel": notatki_conv,
        "phase": "dry-run",
        "success": success,
        "messages_total": n,
        "messages_sent": sent_count,
        "chars_sent": total_chars,
    })
    if success:
        _progress(f"[dry-run] Broadcast *{team}* gotowy — {sent_count}/{n} wyslanych na Notatki")
    else:
        _progress(f"[dry-run] Broadcast *{team}* — {sent_count}/{n} wyslanych na Notatki")
    return success


def _run_phase_send(job_id: str, job: dict) -> bool:
    """Phase send: read generated/<job_id>.json, send all messages to the real channel.

    Requires a fresh Teams session. Returns True on success.
    """
    if not ensure_fresh_session():
        print(
            "[broadcast] Sesja Teams wygasla lub nie istnieje.",
            file=sys.stderr, flush=True,
        )
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": "", "phase": "send",
                     "success": False, "chars_sent": 0, "error": "no_session"})
        return False

    channel = job.get("channel", "")
    if channel == _PLACEHOLDER_CHANNEL:
        print(
            "[broadcast] Kanal docelowy to placeholder. Uzupelnij 'channel' w jobs.json.",
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
    print(f"[broadcast] [send] Wysylanie {n} wiadomosci na kanal {channel} ...", flush=True)
    _progress(f"[send] Broadcast *{team}* — wysylam {n} wiadomosci...", skip_channel=channel)

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
        _progress(f"[send] Broadcast *{team}* gotowy — {sent_count}/{n} wyslanych", skip_channel=channel)
    else:
        _progress(f"[send] Broadcast *{team}* — {sent_count}/{n} wyslanych", skip_channel=channel)
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
        ok = _run_phase_generate(job_id, job)
        if not ok:
            return False
        return _run_phase_send(job_id, job)

    # -------------------------------------------------------------------
    # Legacy 'all' mode: generate + send in one run (original behaviour).
    # -------------------------------------------------------------------
    if not ensure_fresh_session():
        print(
            "[broadcast] Sesja Teams wygasla lub nie istnieje. Uruchom Boba zeby odswiezyl sesje.",
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
            "[broadcast] Kanal docelowy to placeholder. Uzupelnij 'channel' w jobs.json.",
            file=sys.stderr,
            flush=True,
        )
        _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                     "channel": channel, "success": False, "chars_sent": 0,
                     "error": "placeholder_channel"})
        return False

    tools_str = _build_tools_str(job.get("tools", []))

    # ------------------------------------------------------------------
    # Per-section mode (brief_file) vs. single-call mode (prompt)
    # ------------------------------------------------------------------
    brief_file = job.get("brief_file")

    if brief_file:
        brief_path = _BROADCAST_DIR / brief_file
        if not brief_path.exists():
            print(f"[broadcast] brief_file nie istnieje: {brief_path}", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "success": False, "chars_sent": 0,
                         "error": "brief_not_found"})
            return False

        preamble, sections = _parse_brief_sections(brief_path.read_text(encoding="utf-8"))
        if not sections:
            print("[broadcast] Brief nie zawiera sekcji '### Wiadomosc'.", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "success": False, "chars_sent": 0,
                         "error": "no_sections"})
            return False

        n = len(sections)
        team = job.get("team", job_id)
        print(
            f"[broadcast] Tryb per-sekcja: {n} sekcji, timeout {_SECTION_TIMEOUT_S}s/sekcja.",
            flush=True,
        )
        _progress(f"[all] Broadcast *{team}* — generuje {n} sekcji...", skip_channel=channel)

        # Phase 1: generate all sections before sending anything.
        generated: list[tuple[int, str]] = []
        for i, section in enumerate(sections, 1):
            header = section.splitlines()[0] if section else f"sekcja {i}"
            label = _section_label(header)
            print(f"[broadcast] Generowanie sekcji {i}/{n}: {header} ...", flush=True)

            t0 = time.monotonic()
            msg = _generate_section(preamble, section, tools_str,
                                    brief_dir=brief_path.parent, job=job,
                                    label=f"[{i}/{n}] {label}", skip_channel=channel)
            elapsed = int(time.monotonic() - t0)

            if not msg:
                print(
                    f"[broadcast] Sekcja {i} — generowanie nieudane, pomijam wysylke.",
                    file=sys.stderr,
                    flush=True,
                )
                _progress(f"[all] [{i}/{n}] {label} — blad ({elapsed}s)", skip_channel=channel)
            else:
                _progress(f"[all] [{i}/{n}] {label} ({elapsed}s)", skip_channel=channel)
            generated.append((i, msg))

        # Phase 2: send all generated messages in sequence.
        ready = sum(1 for _, m in generated if m)
        print(f"[broadcast] Wysylanie {ready}/{n} wiadomosci ...", flush=True)
        _progress(f"[all] Wysylam {ready}/{n} wiadomosci -> *{team}*...", skip_channel=channel)

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
            _progress(f"[all] Broadcast *{team}* gotowy — {sent_count}/{n} wyslanych", skip_channel=channel)
        else:
            _progress(f"[all] Broadcast *{team}* — {sent_count}/{n} wyslanych", skip_channel=channel)

        success = sent_count == n

    else:
        # Legacy single-call mode.
        prompt = job.get("prompt", "")
        if not prompt:
            print(f"[broadcast] Brak promptu dla joba '{job_id}'.", file=sys.stderr, flush=True)
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "success": False, "chars_sent": 0,
                         "error": "no_prompt"})
            return False

        print(f"[broadcast] Tryb single-call dla joba '{job_id}' ...", flush=True)
        text = generate(prompt, tools_str)

        if not text:
            _append_log({"ts": datetime.now(UTC).isoformat(), "job_id": job_id,
                         "channel": channel, "success": False, "chars_sent": 0,
                         "error": "empty_generation"})
            return False

        messages = _split_messages(text)
        print(f"[broadcast] Wygenerowano {len(messages)} wiadomosc(i).", flush=True)

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
        description="Bob broadcast runner — wysyla zaplanowane wiadomosci na kanalach Teams."
    )
    parser.add_argument(
        "--job",
        required=True,
        metavar="ID",
        help="ID joba do uruchomienia (musi istniec w jobs.json i miec enabled=true).",
    )
    parser.add_argument(
        "--phase",
        choices=["generate", "dry-run", "send", "generate-send"],
        default=None,
        metavar="PHASE",
        help=(
            "Faza do wykonania: generate (generuj i zapisz), "
            "dry-run (wyslij podglad na Notatki), send (wyslij na kanal docelowy), "
            "generate-send (generuj i wyslij bez posrednika), "
            "Bez flagi: tryb all-in-one (legacy)."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    phase = args.phase if args.phase is not None else "all"
    sys.exit(0 if run_job(args.job, phase) else 1)
