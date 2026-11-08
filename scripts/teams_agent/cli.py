"""Terminal orchestrator for Bob — Teams agent.

Usage:
    python scripts/teams_agent/cli.py [--model MODEL] [--max-chats N]
                                       [--force-login] [--log-file PATH]

First run: run login.py once to establish the SSO session, then use this script.
Subsequent runs: session is reused from session.json (valid for 7 days by default).
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time as _time_mod
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv  # noqa: I001

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import importlib.util  # noqa: E402

# Module-relative imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 — side-effect: patches sys.stdout/stderr with CET timestamps
import ai  # noqa: E402
import api_client  # noqa: E402
import org_config  # noqa: E402
import bob_gifs  # noqa: E402
import attachments as _attachments  # noqa: E402
import polls  # noqa: E402
import owner_schedule  # noqa: E402
import scraper  # noqa: E402
import token_health  # noqa: E402
import user_memory  # noqa: E402
from session import (  # noqa: E402
    SESSION_FILE,
    ensure_fresh_session,
    load_storage_state,
    save_storage_state,
)
from sp_warmup_urls import SP_WARMUP_URLS  # noqa: E402

# datetime_bob is a sibling scripts/ package; scripts/ is already on sys.path above.
import datetime_bob.resolver as _dt_resolver  # noqa: E402


def _build_schedule_context() -> str | None:
    """Build the schedule_context string injected into every Bob prompt.

    Always includes a date anchor ([TODAY] ...) so Bob always knows today's date.
    Appends the owner's availability block when schedule is non-empty.
    Returns None only if both parts are empty (should not happen — anchor always present).
    """
    blocks = [
        _dt_resolver.build_today_anchor(),
        owner_schedule.build_context_block(OWNER_LABEL),
    ]
    joined = "\n\n".join(b for b in blocks if b)
    return joined or None

# Load scripts/todos/mcp_server.py by explicit path to avoid colliding with
# other MCP servers that also expose a `mcp_server` module (jira, leave, ...).
# Each of those does `sys.path.insert(0, <their dir>)`, which causes a bare
# `import mcp_server` to resolve to whichever was imported first.
_todos_spec = importlib.util.spec_from_file_location(
    "todos_store", Path(__file__).resolve().parents[1] / "todos" / "mcp_server.py",
)
todos_store = importlib.util.module_from_spec(_todos_spec)
_todos_spec.loader.exec_module(todos_store)

TEAMS_URL = "https://teams.microsoft.com/v2/"
DEFAULT_LOG_FILE = str(Path(__file__).parent / "log.jsonl")
DEFAULT_WATCH_STATE = str(Path(__file__).parent / "watch-state.json")
_CHAT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "chat-config.json"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_CHATS = 10
DEFAULT_POLL_S = 5  # 14 chats x 2 s = 7 REST req/s sustained; 5 s is enough
def _default_watch_chats() -> list[str]:
    """Return all chat names from chat-config.json, falling back to Notes."""
    try:
        data = json.loads(_CHAT_CONFIG_PATH.read_text(encoding="utf-8"))
        chats = list(data.get("chats", {}).keys())
        if chats:
            return chats
    except Exception:
        pass
    return ["Notes"]

DEFAULT_WATCH_CHATS = _default_watch_chats()
DEFAULT_RATE_LIMIT_S = 3           # minimum seconds between auto-replies
_TOKEN_REFRESH_INTERVAL_S = 40 * 60  # proactive browser nav every 40 min to keep token fresh
_TZ_NAME = org_config.TIMEZONE
_CET = ZoneInfo(_TZ_NAME)

# Per-chat in-memory conversation history (persisted to disk across restarts).
# Dict: chat_name → list of {"sender": str, "text": str}
_CHAT_HISTORY: dict[str, list[dict]] = {}
# compact when accumulated history exceeds this (~5k tokens). Was 60000: the 200-entry disk cap kept busy channels
# just under it, so every call re-sent ~53k chars of history uncached (most of the cost).
_HISTORY_COMPACT_CHARS = 20000
_HISTORY_KEEP_LAST = 8          # keep last N user+Bob exchange pairs after compaction
_HISTORY_DIR = Path(__file__).parent / "history"
_HISTORY_DISK_MAX_ENTRIES = 200  # hard cap on disk entries per chat (trim oldest on write)
# Last run of the slow periodic jobs, kept across restarts: a restart must not re-run a headless Chromium SharePoint
# warm-up and the leave poll every time.
_TIMERS_FILE = Path(__file__).parent / "timers-state.json"


def _timer_last(name: str) -> float:
    try:
        return float(json.loads(_TIMERS_FILE.read_text(encoding="utf-8")).get(name) or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def _timer_mark(name: str, ts: float) -> None:
    try:
        state = json.loads(_TIMERS_FILE.read_text(encoding="utf-8")) if _TIMERS_FILE.exists() else {}
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state[name] = ts
    try:
        tmp = _TIMERS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, _TIMERS_FILE)
    except OSError:
        pass


# Per-user memory tracking for rolling profile summaries (written by sweeper thread).
# _USER_LAST_ACTIVITY: mri -> (last_activity_ts, display_name, session_exchanges)
_USER_LAST_ACTIVITY: dict[str, tuple[float, str, int]] = {}
# _USER_SESSION_START: mri -> session_start_ts
_USER_SESSION_START: dict[str, float] = {}
# _USER_HISTORY: mri -> list of {"sender": str, "text": str} pairs (only this user + Bob)
_USER_HISTORY: dict[str, list[dict]] = {}
_USER_SESSION_TIMEOUT_S = 1800  # 30 minutes silence -> trigger summary
_USER_SWEEP_INTERVAL_S = 60     # sweeper wakes every 60 s

# A chat reply may run long tools (several lookups in one question): 25 min before the CLI is cut, and
# meanwhile one "still working" message in the chat, edited every few minutes (a 10-minute silence followed by the
# 600 s timeout used to drop the answer without any sign of life).
_CHAT_REPLY_TIMEOUT_S = 25 * 60
_STILL_WORKING_FIRST_S = 120
_STILL_WORKING_EVERY_S = 180


class _StillWorking:
    """Context manager: while a reply is being generated, post "⏳ still working" after FIRST_S seconds and edit
    it every EVERY_S with the elapsed minutes. Leaving the block only stops the timer: the caller then calls
    finish(sent) - "done" when a reply really went out, otherwise the note is deleted (or edited when Teams
    refuses). enabled=False makes the whole thing a no-op (uninvited replies get no note). Never raises."""

    def __init__(self, conv_id: str, first_s: float = _STILL_WORKING_FIRST_S,
                 every_s: float = _STILL_WORKING_EVERY_S, enabled: bool = True) -> None:
        self.conv_id, self.first_s, self.every_s = (conv_id if enabled else ""), first_s, every_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.msg_id: str | None = None
        self._t0 = 0.0

    def _say(self, text: str) -> None:
        try:
            html = api_client.bob_html(text)
            if self.msg_id:
                api_client.edit_message(self.msg_id, html, self.conv_id)
            else:
                self.msg_id = api_client.send_html_message(html, self.conv_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[watch] still-working: {exc}", file=sys.stderr, flush=True)

    def _run(self) -> None:
        wait = self.first_s
        while not self._stop.wait(wait):
            mins = max(1, round((_time_mod.time() - self._t0) / 60))
            self._say(f"⏳ Still working on the answer ({mins} min) - long lookups, I will reply here.")
            wait = self.every_s

    def __enter__(self) -> "_StillWorking":
        if self.conv_id:
            self._t0 = _time_mod.time()
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        if exc[0] is not None:                          # the reply failed: no "answer below"
            self.finish(False)

    def finish(self, sent: bool) -> None:
        """The reply is sent (True) or dropped (False): close the note accordingly."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=60)               # a note still being posted must be seen (and closed) here
        msg_id, self.msg_id = self.msg_id, None
        if not msg_id:
            return
        try:
            if sent:
                mins = max(1, round((_time_mod.time() - self._t0) / 60))
                api_client.edit_message(msg_id, api_client.bob_html(f"✅ Done after {mins} min - answer below."),
                                        self.conv_id)
            elif not api_client.delete_message(msg_id, self.conv_id):
                api_client.edit_message(msg_id, api_client.bob_html("I checked - no answer this time."),
                                        self.conv_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[watch] still-working: {exc}", file=sys.stderr, flush=True)

# Directories from which [SEND_FILE_FROM_DISK] uploads are permitted.
# Prevents prompt-injection from exfiltrating arbitrary files (e.g. ~/.ssh/id_rsa).
_ALLOWED_UPLOAD_ROOT = Path(__file__).parent.parent.parent / "user-data"
_ALLOWED_UPLOAD_ROOTS: list[Path] = [_ALLOWED_UPLOAD_ROOT]

# ---------------------------------------------------------------------------
# Persistent conversation history (disk-backed JSONL per chat)
# ---------------------------------------------------------------------------

def _history_path(chat_name: str) -> Path:
    return _HISTORY_DIR / f"{_chat_name_to_filename(chat_name)}.jsonl"


def _load_history_from_disk(chat_name: str) -> list[dict]:
    """Read the most recent history entries from disk, respecting the char budget."""
    path = _history_path(chat_name)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        print(f"[watch] ERROR loading history {chat_name!r}: {exc}", file=sys.stderr)
        return []
    entries: list[dict] = []
    for line in raw:
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    # Walk backwards keeping entries within char budget
    kept: list[dict] = []
    total = 0
    for entry in reversed(entries):
        chunk = len(entry.get("text", ""))
        if total + chunk > _HISTORY_COMPACT_CHARS and kept:
            break
        kept.insert(0, entry)
        total += chunk
    return kept


def _append_history_to_disk(chat_name: str, entries: list[dict]) -> None:
    """Append new entries to the per-chat JSONL file, trimming to the hard cap."""
    _HISTORY_DIR.mkdir(exist_ok=True)
    path = _history_path(chat_name)
    try:
        with path.open("a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # Trim to hard cap: keep last _HISTORY_DISK_MAX_ENTRIES lines
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > _HISTORY_DISK_MAX_ENTRIES:
            path.write_text("".join(lines[-_HISTORY_DISK_MAX_ENTRIES:]), encoding="utf-8")
    except Exception as exc:
        print(f"[watch] ERROR writing history {chat_name!r}: {exc}", file=sys.stderr)


def _note_bot_post(chat_name: str, inner_html: str, extra: str = "", kind: str = "", file: str = "") -> None:
    """A message a background job posted as Bob (a file link, a deploy note) -> the chat's history, so Bob
    knows what "this file" / "this change" refers to when someone answers it."""
    text = (api_client._strip_html(inner_html) + (f" {extra}" if extra else "")).strip()
    if not chat_name or not text:
        return
    entry = {"sender": "Bob", "text": text, **({"kind": kind} if kind else {}),
             **({"file": file} if file else {})}
    _CHAT_HISTORY.setdefault(chat_name, []).append(entry)
    _append_history_to_disk(chat_name, [entry])


def _rewrite_history_on_disk(chat_name: str, entries: list[dict]) -> None:
    """Overwrite the disk history file with the given entries (used after compaction)."""
    _HISTORY_DIR.mkdir(exist_ok=True)
    path = _history_path(chat_name)
    try:
        path.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[watch] ERROR rewriting history {chat_name!r}: {exc}", file=sys.stderr)


# Silent sentinel + roleplay-silence patterns. When Bob has nothing valuable to add
# we want NO message sent — not a stage-direction like *stays silent* (or the Polish *milczy* / *milczę*).
_SILENT_SENTINEL_RE = re.compile(r'<\s*silent\s*/?\s*>', re.IGNORECASE)
_SILENT_ROLEPLAY_RE = re.compile(
    r'^[\s*_~`"\'()\[\]\.]*'
    r'(milcz[ęeya]?|milczeć|nie\s+odpowiadam|brak\s+odpowiedzi|silent|stays?\s+silent|says?\s+nothing)'
    r'[\s*_~`"\'()\[\]\.!?]*$',
    re.IGNORECASE,
)

# Per-persona display label used in HTML messages sent to Teams.
# Every prefix is included in _BOT_HTML_PREFIXES for bot-message detection.
def _agent_html_prefix(persona: str | None) -> str:
    """Return the emoji+label fragment used inside <p>...</p> bot messages."""
    return api_client.agent_html_prefix(persona)


# Single source of truth lives in api_client.BOT_HTML_PREFIXES (also used by
# delete_message() as a safety check before deleting anything).
_BOT_HTML_PREFIXES = api_client.BOT_HTML_PREFIXES


# How the owner's own messages are labelled in transcripts and audit records
# (OWNER_ALIAS). One constant on purpose: _owner_label() falls back to it for every
# message the persona prefixes don't claim, so ad-hoc literals would split history labels.
OWNER_LABEL = org_config.OWNER_ALIAS

# Real people the personas may @-mention or accept teaching from live in a gitignored
# people.json next to this file (template: people.example.json; path override:
# PEOPLE_FILE). Missing file = empty tables: mention tokens are stripped, teaching is
# owner-only — never a crash.
_PEOPLE = org_config.load_json_config("PEOPLE_FILE", Path(__file__).resolve().parent / "people.json")
_OWNER_REF = "@owner"  # people.json shorthand for (OWNER_DISPLAY_NAME, owner MRI)


def _people_table(persona: str) -> dict[str, tuple[str, str]]:
    """{key: (display name, MRI)} for `persona` from people.json "mention_people"."""
    table: dict[str, tuple[str, str]] = {}
    for key, entry in ((_PEOPLE.get("mention_people") or {}).get(persona) or {}).items():
        if entry == _OWNER_REF:
            table[key.lower()] = (api_client.USER_DISPLAY, api_client.USER_MRI)
        elif isinstance(entry, list) and len(entry) == 2:
            table[key.lower()] = (str(entry[0]), str(entry[1]))
    return table


def _owner_label(m: dict) -> str:
    """Label a message for LLM transcript display.

    Every persona (Bob, the per-channel personas) and the owner's own real messages
    post via the same Teams account (USER_MRI), so Teams itself can't tell them
    apart — without this, every one of them collapses to the literal string
    OWNER_LABEL in the conversation history shown to the model, including the
    agents' own past replies and GIFs. Disambiguate via the BOT_HTML_PREFIXES
    stamped into the message HTML instead.
    """
    if m.get("sender_mri") != api_client.USER_MRI:
        return m.get("sender", "?")
    return api_client.agent_label_from_html(m.get("raw_html", "")) or OWNER_LABEL

# Collapses runs of blank lines left behind after stripping one or more inline tokens
# (e.g. several [EXAMPLE-IDEA: ...] lines in a row) — otherwise Teams renders each
# leftover newline as a <br>, producing a wall of empty vertical space in the message.
_BLANK_RUN_RE = re.compile(r'\n[ \t]*\n(?:[ \t]*\n)*')


def _collapse_blank_lines(text: str) -> str:
    return _BLANK_RUN_RE.sub("\n", text).strip()


# ---------------------------------------------------------------------------
# Example persona tokens (persona key: "example", prompts/example_persona.md).
# One idea token and one owner-only learning token, each with its own file/dir --
# the pattern a new persona copies. Handlers run on every reply (pure regex), so
# each persona needs its own token prefix to keep audit trails apart.
# ---------------------------------------------------------------------------
_EXAMPLE_DIR = Path(__file__).resolve().parent.parent.parent / "user-data" / "example"
# Canonical tags are English; the Polish forms (EXAMPLE-POMYSL, EXAMPLE-UCZY-SIE) stay accepted.
_EXAMPLE_IDEAS_FILE = _EXAMPLE_DIR / "ideas.jsonl"
_EXAMPLE_IDEAS_RE = re.compile(
    r'\[EXAMPLE-(?:IDEA|POMYSL):\s*(?P<title>[^|\]]+?)\s*\|\s*(?P<content>[^\]]+?)\s*\]',
    re.IGNORECASE | re.DOTALL,
)
_EXAMPLE_LEARN_DIR = _EXAMPLE_DIR / "learning"
_EXAMPLE_LEARN_RE = re.compile(
    r'\[EXAMPLE-(?:LEARN|UCZY-SIE):\s*(?P<title>[^|\]]+?)\s*\|\s*(?P<content>[^\]]+?)\s*\]',
    re.IGNORECASE | re.DOTALL,
)


# Bob escalation token: [BOB-CALL: key] or [BOB-CALL: key1,key2] (Polish alias [BOB-WYWOLAJ: ...])
# — pings a real person on Teams via native @mention when Bob doesn't know an answer,
# or on explicit user request ("tag X"). Own dict/regex/prefix, independent from the personas.
# Filled from people.json "mention_people.bob" ({"key": ["First Last", "8:orgid:..."]});
# empty by default. `who` still allows Polish diacritics (keys can be Polish names).
_BOB_MENTION_PEOPLE: dict[str, tuple[str, str]] = _people_table("bob")
_BOB_MENTION_RE = re.compile(r'\[BOB-(?:CALL|WYWOLAJ):\s*(?P<who>[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ,\s]+?)\s*\]', re.IGNORECASE)


def _resolve_mentions(
    reply: str, regex: re.Pattern, people: dict[str, tuple[str, str]],
    profile_fallback: bool = True,
) -> tuple[str, str, list[dict]]:
    """Extract escalation token(s) matched by `regex`, resolve to a real Teams @mention chip.

    The token's 'who' group is a comma-separated list of keys into `people` (e.g. 'person20'
    or 'person20,person51'). Unknown keys are silently ignored. Returns
    (clean_reply, mentions_html, mentions_entries) — html/entries are "" / [] when
    no valid token was found, so callers can pass them straight through unconditionally.
    """
    matches = list(regex.finditer(reply))
    if not matches:
        return reply, "", []
    clean_reply = regex.sub("", reply).strip()
    resolved: list[tuple[str, str]] = []
    for m in matches:
        for key in m.group("who").lower().split(","):
            key = key.strip()
            person = people.get(key)
            if not person and profile_fallback:
                person = user_memory.find_profile_by_name(key)
            if person and person not in resolved:
                resolved.append(person)
    if not resolved:
        return clean_reply, "", []
    mentions_html, entries = api_client.build_mentions_html(resolved)
    return clean_reply, mentions_html, entries


def _handle_bob_mention(reply: str) -> tuple[str, str, list[dict]]:
    """Extract [BOB-CALL: who] token(s), resolve to a real Teams @mention chip."""
    return _resolve_mentions(reply, _BOB_MENTION_RE, _BOB_MENTION_PEOPLE)


def _handle_idea_token(
    reply: str,
    sender: str,
    regex: re.Pattern,
    out_file: Path,
    content_key: str,
    log_tag: str,
) -> str:
    """Extract [<X>-IDEA: title | content] tokens, append to out_file, strip from reply.

    Shared by every persona's idea token — each one passes its own regex and its own
    JSONL file so the audit trails never mix. Ticket creation (when a persona does it)
    is the model's job via jira_create in the same turn; this handler only writes the
    audit trail and strips the token(s) from the visible reply.
    """
    import datetime as _dt
    import json as _json
    matches = list(regex.finditer(reply))
    if not matches:
        return reply
    out_file.parent.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    with out_file.open("a", encoding="utf-8") as fh:
        for m in matches:
            entry = {
                "date": today,
                "sender": sender,
                "title": m.group("title").strip(),
                content_key: m.group("content").strip(),
            }
            fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"[{log_tag}] saved idea from {sender!r}: {entry['title']!r}", flush=True)
    return _collapse_blank_lines(regex.sub("", reply))


def _handle_learning_token(
    reply: str, regex: re.Pattern, out_dir: Path, log_tag: str,
) -> str:
    """Extract [<X>-LEARN: title | content] tokens, save .md files, strip from reply."""
    import datetime as _dt
    matches = list(regex.finditer(reply))
    if not matches:
        return reply
    out_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    for m in matches:
        title_raw = m.group("title").strip()
        content = m.group("content").strip()
        slug = re.sub(r'[^a-z0-9]+', '-', title_raw.lower()).strip('-')[:50]
        fname = out_dir / f"{today}-{slug}.md"
        suffix = 1
        while fname.exists():
            suffix += 1
            fname = out_dir / f"{today}-{slug}-{suffix}.md"
        fname.write_text(f"# {title_raw}\n\n{content}\n", encoding="utf-8")
        print(f"[{log_tag}] saved: {fname}", flush=True)
    return regex.sub("", reply).strip()


def _adopt_legacy_example_files() -> None:
    """Older builds wrote pomysly.jsonl and nauka/: move them to the English names once, so nothing splits."""
    for old, new in ((_EXAMPLE_IDEAS_FILE.parent / "pomysly.jsonl", _EXAMPLE_IDEAS_FILE),
                     (_EXAMPLE_LEARN_DIR.parent / "nauka", _EXAMPLE_LEARN_DIR)):
        try:
            if old.exists() and not new.exists():
                os.replace(old, new)
        except OSError as exc:
            print(f"[example] could not rename {old.name} to {new.name}: {exc}", file=sys.stderr, flush=True)


def _handle_example_idea(reply: str, sender: str) -> str:
    """[EXAMPLE-IDEA: title | content] — ideas collected by the example persona."""
    _adopt_legacy_example_files()
    return _handle_idea_token(
        reply, sender, _EXAMPLE_IDEAS_RE, _EXAMPLE_IDEAS_FILE, "idea", "example-ideas",
    )


def _handle_example_learning(reply: str, sender_mri: str) -> str:
    """[EXAMPLE-LEARN: title | content] — the owner teaching the example persona.

    Enforced by MRI here, not only in the prompt: a token anyone else talks the
    model into is stripped without being saved.
    """
    if sender_mri != api_client.USER_MRI:
        if _EXAMPLE_LEARN_RE.search(reply):
            print(f"[example-learn] rejected learning from {sender_mri!r}", flush=True)
        return _EXAMPLE_LEARN_RE.sub("", reply).strip()
    _adopt_legacy_example_files()
    return _handle_learning_token(
        reply, _EXAMPLE_LEARN_RE, _EXAMPLE_LEARN_DIR, "example-learn",
    )


# 👍 on the incoming message instead of (or next to) a reply: [BOB-REACT] (Polish alias [BOB-REAKCJA]).
# A reply that is only the token sends nothing - the reaction is the whole answer.
_REACTION_RE = re.compile(r'\[BOB-(?:REACT|REAKCJA)\]', re.IGNORECASE)


def _handle_reaction(reply: str, msg_id: str, conv_id: str) -> str:
    """[BOB-REACT] - put 👍 on the message being answered, strip the token."""
    if not _REACTION_RE.search(reply):
        return reply
    if msg_id:
        ok = api_client.add_reaction(msg_id, conv_id, "like")
        print(f"[reaction] 👍 on {msg_id}: {'ok' if ok else 'ERROR'}", flush=True)
    return _collapse_blank_lines(_REACTION_RE.sub("", reply))


def _is_silent_reply(reply: str) -> bool:
    """True if reply is empty or a silence-sentinel/roleplay (e.g. '<silent/>', '*stays silent*', '*milczy*')."""
    if not reply or not reply.strip():
        return True
    stripped = _SILENT_SENTINEL_RE.sub("", reply).strip()
    if not stripped:
        return True
    return bool(_SILENT_ROLEPLAY_RE.match(stripped))


_MCP_COLD_START_RE = re.compile(
    r"serwer(?:y|ow)?\s+mcp.*?(?:nie\s+(?:polacz|zostal|sa\s+got)|jeszcze\s+(?:nie|sie))"
    r"|mcp.*?(?:nie\s+sa\s+gotowe|sie\s+nie\s+polaczyl|nie\s+(?:zainicjalizow|aktywow))"
    r"|narzedzi(?:a|ach)?\s+mcp.*?nie"
    r"|mcp\s+servers?.*?(?:not\s+(?:connected|ready|available|initiali[sz]ed)|still\s+(?:starting|initiali[sz]ing))"
    r"|mcp\s+tools?.*?(?:not\s+(?:available|ready|connected)|unavailable)",
    re.IGNORECASE | re.DOTALL,
)


def _looks_like_mcp_cold_start(reply: str) -> bool:
    """Detect Sonnet's fallback prose when claude CLI didn't finish MCP init in time.

    Triggers a one-shot retry in the watch loop. False positives are cheap
    (one extra claude call), so the regex errs on the side of catching variants.
    """
    if not reply:
        return False
    # Matches English and Polish fallback prose; Polish diacritics are folded so the regex
    # doesn't need to enumerate them.
    folded = (
        reply.lower()
        .replace("ą", "a").replace("ć", "c").replace("ę", "e").replace("ł", "l")
        .replace("ń", "n").replace("ó", "o").replace("ś", "s").replace("ź", "z").replace("ż", "z")
    )
    return bool(_MCP_COLD_START_RE.search(folded))


def _user_memory_sweeper() -> None:
    """Daemon thread: every _USER_SWEEP_INTERVAL_S seconds scan for idle users.

    When a user has been silent for >_USER_SESSION_TIMEOUT_S and has at least
    one exchange, generates a rolling Haiku summary via user_memory.save_summary
    and resets the exchange counter. Best-effort: never crashes the thread.
    """
    while True:
        _time_mod.sleep(_USER_SWEEP_INTERVAL_S)
        try:
            now = _time_mod.time()
            snapshot = list(_USER_LAST_ACTIVITY.items())
            for mri, (last_ts, display_name, exchanges) in snapshot:
                try:
                    if (now - last_ts) > _USER_SESSION_TIMEOUT_S and exchanges > 0:
                        session_start = _USER_SESSION_START.get(mri, last_ts)
                        history = _USER_HISTORY.get(mri, [])
                        user_memory.save_summary(
                            mri, display_name, history, session_start, exchanges
                        )
                        # Reset exchange counter and session start; keep last_ts so we don't re-trigger.
                        _USER_LAST_ACTIVITY[mri] = (last_ts, display_name, 0)
                        _USER_HISTORY.pop(mri, None)
                        _USER_SESSION_START.pop(mri, None)
                except Exception as exc:
                    print(
                        f"[watch] user_memory sweeper error for {mri!r}: {exc}",
                        flush=True,
                    )
        except Exception as exc:
            print(f"[watch] user_memory sweeper error: {exc}", flush=True)


# GIF tag emitted by Claude: [GIF: query] (Giphy fallback, both personas)
_GIF_TAG_RE = re.compile(r'\[GIF:\s*(.+?)\]', re.IGNORECASE)

# Bob-mascot reaction tag emitted by Claude: [BOB_GIF: mood] (Bob persona only,
# fixed mood vocabulary in bob_gifs.REACTION_MOODS, gif files from BOB_GIF_DIR).
# The [a-z_]+ slug charset cannot match a free-form Giphy query, and _GIF_TAG_RE's
# literal "[GIF:" prefix cannot match "[BOB_GIF:" -- the two regexes never collide.
_BOB_GIF_TAG_RE = re.compile(r'\[BOB_GIF:\s*([a-z_]+)\]', re.IGNORECASE)


def _plan_gifs(reply: str, persona: str | None = None) -> tuple[str, dict | None]:
    """Pure step: strip [BOB_GIF: mood] / [GIF: query] tags, decide what (if anything) to send.

    Returns (cleaned_reply, plan). plan is None (nothing to send) or a dict for
    _execute_gif_plan to act on later:
      {"kind": "bob_gif", "mood": str, "gif_name": str, "phrase": str}
      {"kind": "giphy", "queries": list[str]}

    Precedence (Bob persona only): a valid [BOB_GIF: mood] wins and any co-emitted
    [GIF: query] is dropped -- one gif per reply, Bob-mascot preferred. Personas
    (any chat-config "persona") or an unknown mood fall through to Giphy.
    All recognized tags are always stripped from the returned text.
    """
    bob_tags = _BOB_GIF_TAG_RE.findall(reply)
    cleaned = _BOB_GIF_TAG_RE.sub("", reply).strip()

    plan: dict | None = None
    if persona is None:
        for mood in bob_tags:
            pair = bob_gifs.pick_reaction_gif_and_phrase(mood)
            if pair is None:
                print(f"[watch] -> bob_gif: unknown mood or missing gif {mood!r}, skipping", flush=True)
                continue
            gif_name, phrase = pair
            plan = {"kind": "bob_gif", "mood": mood, "gif_name": gif_name, "phrase": phrase}
            break  # one gif per reply cap

    giphy_tags = _GIF_TAG_RE.findall(cleaned)
    cleaned = _GIF_TAG_RE.sub("", cleaned).strip()
    if plan is not None:
        return cleaned, plan  # BOB_GIF precedence: drop Giphy tags without sending
    if giphy_tags:
        plan = {"kind": "giphy", "queries": [q.strip() for q in giphy_tags]}
    return cleaned, plan


def _execute_gif_plan(plan: dict | None, conv_id: str, persona: str | None = None) -> None:
    """Network step: perform the gif send decided by _plan_gifs. No-op when plan is None."""
    if plan is None:
        return
    if plan["kind"] == "bob_gif":
        gif_name, phrase, mood = plan["gif_name"], plan["phrase"], plan["mood"]
        print(f"[watch] -> bob_gif: mood={mood!r} gif={gif_name!r}", flush=True)
        caption = f'<p>{_agent_html_prefix(persona)}: {html.escape(phrase)}</p>'
        if api_client.send_ams_gif_message(caption, bob_gifs.gif_path(gif_name), conv_id) is None:
            print("[watch] -> bob_gif: AMS upload failed, skipping silently", flush=True)
    elif plan["kind"] == "giphy":
        for query in plan["queries"]:
            print(f"[watch] -> gif: {query!r}", flush=True)
            api_client.send_gif(query, conv_id, persona=persona)


def _extract_and_send_gifs(reply: str, conv_id: str, persona: str | None = None) -> str:
    """Find [BOB_GIF: mood] / [GIF: query] tags in reply, send gif(s), return cleaned reply.

    Combined plan+execute wrapper for call sites with no placeholder to defer the
    send around (catch-up aggregation, grill mode).
    """
    cleaned, plan = _plan_gifs(reply, persona=persona)
    _execute_gif_plan(plan, conv_id, persona=persona)
    return cleaned


# File attachment tag emitted by Claude: [SEND_FILE: name.md]\ncontent\n[/SEND_FILE]
# (Polish alias [WYSLIJ_PLIK: ...]...[/WYSLIJ_PLIK]; open and close may be either form).
_FILE_TAG_RE = re.compile(
    r'\[(?:SEND_FILE|WYSLIJ_PLIK):\s*([^\]\n]+)\]\s*\n(.*?)\n?\[/(?:SEND_FILE|WYSLIJ_PLIK)\]',
    re.DOTALL | re.IGNORECASE,
)

# Disk-path attachment tag (for binary files already on disk, e.g. MP3):
# [SEND_FILE_FROM_DISK: /absolute/path/to/file.mp3] (Polish alias [WYSLIJ_PLIK_Z_DYSKU: ...])
_FILE_PATH_TAG_RE = re.compile(
    r'\[(?:SEND_FILE_FROM_DISK|WYSLIJ_PLIK_Z_DYSKU):\s*([^\]\n]+)\]',
    re.IGNORECASE,
)


def _safe_relative(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _remember_bob_file(name: str, url: str, chat_name: str, content: bytes) -> None:
    """Keep the file Bob sent (bob_files) and note the post in the chat history, so a later "fix that file" -
    quoted or by link - reaches its content."""
    try:
        import bob_files  # noqa: PLC0415
        bob_files.record(name, url, chat_name, content)
        # "kind": only this code path writes it - chat text and model replies never carry it
        # "file" is the stored SharePoint name (it carries the upload-time prefix); the display name is model-chosen
        _note_bot_post(chat_name, f"📎 {name} {url}", kind="upload", file=bob_files.sp_name(url) or name)
    except Exception as exc:  # noqa: BLE001 -- the file is already sent
        print(f"[watch] -> bob_files: {exc}", file=sys.stderr, flush=True)


def _extract_and_send_files(reply: str, conv_id: str, chat_name: str) -> str:
    """Find file attachment tags in reply, upload each to SharePoint, send a link.

    Handles two tag forms:
      [SEND_FILE: name.md]\\ncontent\\n[/SEND_FILE]   — inline text content
      [SEND_FILE_FROM_DISK: /path/to/file.mp3]       — existing file on disk (binary-safe)
    (the Polish forms [WYSLIJ_PLIK: ...] / [WYSLIJ_PLIK_Z_DYSKU: ...] are accepted too)

    Uploads to the SharePoint upload folder (SHAREPOINT_UPLOAD_FOLDER_URL) (sharepoint_upload.py) and
    sends the link via the regular chatsvc message path, instead of dragging the
    file into the Teams composer — see sharepoint_upload.py's docstring for why
    (the CDP-drag approach got stuck on a Teams-side drop overlay and stopped
    working).

    Returns reply with the tags stripped. `chat_name` is unused now that the
    upload no longer switches Teams chats, kept for call-site compatibility.
    """
    import os as _os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    # --- inline text files ---
    inline_matches = list(_FILE_TAG_RE.finditer(reply))
    path_matches = list(_FILE_PATH_TAG_RE.finditer(reply))

    if not inline_matches and not path_matches:
        return reply

    cleaned = _FILE_TAG_RE.sub("", reply)
    cleaned = _FILE_PATH_TAG_RE.sub("", cleaned).strip()

    import sharepoint_upload  # noqa: PLC0415

    def _dest_name(name: str) -> str:
        return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{name}"

    for m in inline_matches:
        fname = Path(m.group(1).strip()).name
        content = m.group(2)
        if not content.strip():
            print(f"[watch] -> send_file: empty content, rejecting {fname!r}", flush=True)
            continue
        suffix = f"_{fname}" if "." in fname else f"_{fname}.md"
        fd, tmp_str = tempfile.mkstemp(suffix=suffix)
        tmp_path = Path(tmp_str)
        _os.close(fd)
        try:
            tmp_path.write_text(content, encoding="utf-8")
            print(f"[watch] -> send_file: {fname!r} ({len(content)} B)", flush=True)
            url = sharepoint_upload.upload_file(tmp_path, dest_name=_dest_name(fname))
            if url is None:
                print(f"[watch] -> send_file: upload {fname!r} failed", flush=True)
                fallback_dir = _ALLOWED_UPLOAD_ROOT / "failed-uploads"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                fallback_path = fallback_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{fname}"
                fallback_path.write_text(content, encoding="utf-8")
                api_client.send_message(
                    f"⚠️ Could not send the file \"{fname}\" (the SharePoint upload "
                    f"automation failed). I saved the content locally: {fallback_path}",
                    conv_id,
                )
            else:
                api_client.send_message(f"📎 **{fname}**\n{url}", conv_id)
                print(f"[watch] -> send_file: OK {url}", flush=True)
                _remember_bob_file(fname, url, chat_name, content.encode("utf-8"))
        except Exception as exc:
            print(f"[watch] -> send_file: error {exc}", flush=True)
        finally:
            tmp_path.unlink(missing_ok=True)

    for m in path_matches:
        src = Path(m.group(1).strip())
        if not src.is_absolute():
            print(f"[watch] -> send_file_from_disk: rejecting relative path {src!r}", flush=True)
            continue
        if not any(
            _safe_relative(src, root) for root in _ALLOWED_UPLOAD_ROOTS
        ):
            print(f"[watch] -> send_file_from_disk: path outside the allowed folders, rejecting {src!r}", flush=True)
            continue
        if not src.exists():
            print(f"[watch] -> send_file_from_disk: file does not exist {src!r}", flush=True)
            continue
        try:
            print(f"[watch] -> send_file_from_disk: {src.name!r} ({src.stat().st_size} B)", flush=True)
            url = sharepoint_upload.upload_file(src, dest_name=_dest_name(src.name))
            if url is None:
                print(f"[watch] -> send_file_from_disk: upload {src.name!r} failed", flush=True)
                api_client.send_message(
                    f"⚠️ Could not send the file \"{src.name}\" (the SharePoint upload "
                    f"automation failed). The file is available locally: {src}",
                    conv_id,
                )
            else:
                api_client.send_message(f"📎 **{src.name}**\n{url}", conv_id)
                print(f"[watch] -> send_file_from_disk: OK {url}", flush=True)
                _remember_bob_file(src.name, url, chat_name,
                                   src.read_bytes() if src.stat().st_size <= 2 * 1024 * 1024 else b"")
        except Exception as exc:
            print(f"[watch] -> send_file_from_disk: error {exc}", flush=True)

    return cleaned


# 'Hey Claude' (Polish 'Hej Claude') trigger: used only in --interactive approval loop.
# Watch mode responds to ALL messages and does not require this trigger.
_TRIGGER_RE = re.compile(r'he[yj]\s+claude[\s,:.\-—]*(.+)', re.IGNORECASE | re.DOTALL)

# Trigger: message mentions "Bob" anywhere (e.g. "hey Bob, ...", "Bob what do you think", "and Bob?")
# Group 1 is unused — we pass the full message text to Claude when Bob is mentioned anywhere.
_BOB_RE = re.compile(r'\bbob\b', re.IGNORECASE)
_TRIGGER_RE_CACHE: dict[str, re.Pattern] = {}


def _trigger_re_for(chat_name: str, chat_config: dict) -> re.Pattern:
    """Return the mention-trigger regex for a channel. Defaults to _BOB_RE.

    chat-config.json "trigger" accepts a single word ("assistant") or a list of
    alternatives (["example", "sample"]) — a persona whose name people spell
    more than one way needs all of them here, because under caller="auto" this
    regex is the ONE place the owner's own messages are still gated: a form the
    persona's prompt claims to answer to but that is missing here is dropped
    before Claude is ever invoked, with no error and no log line.
    """
    if chat_name in _TRIGGER_RE_CACHE:
        return _TRIGGER_RE_CACHE[chat_name]
    raw = (chat_config.get(chat_name) or {}).get("trigger") or ""
    words = [raw] if isinstance(raw, str) else list(raw)
    words = [w.strip() for w in words if w and w.strip()]
    if words:
        alts = "|".join(re.escape(w) for w in words)
        pat = re.compile(rf'\b(?:{alts})\b', re.IGNORECASE)
    else:
        pat = _BOB_RE
    _TRIGGER_RE_CACHE[chat_name] = pat
    return pat

# Grill mode triggers. Start: "grill me" / "let's grill" (Polish aliases: "grillujemy", "grill mnie").
# Stop: explicit "stop grill" / "end grill" (Polish aliases: "zakończ grill", "koniec grillu").
_GRILL_START_RE = re.compile(r'\b(grill\s+me|let\'?s\s+grill|grillujemy|grill\s+mnie)\b', re.IGNORECASE)
_GRILL_STOP_RE = re.compile(
    r'stop\s+grill|grill\s+stop|end\s+grill|zakończ\s+grill|koniec\s+grill', re.IGNORECASE,
)

# Attachment/file read trigger: user is explicitly asking about a file or image content.
# Positive examples: "what's in the file", "read this", "summarize this", "describe the image",
#   and the Polish equivalents ("przeczytaj plik", "opisz obraz", "co tam w pliku", ...).
# Negative examples: "thanks", "ok", "yes Bob", "how are you", "make me some tasks".
# Note: prefix alternations (przeczyt, summariz, describ etc.) have no trailing \b
# so they match all inflected forms (przeczytaj, summarize, describes etc.).
# Polish "co X" alternations also have NO trailing \b — Polish inflection means
# "obraz"/"obrazie"/"obrazach", "plik"/"pliku"/"plikach", "zdj"/"zdjeciu" must
# all match. Trailing \b would block these because the next char is a word char.
# EN "what ... file/image" allows arbitrary words between (is, the, this, your)
# because "what is in the file" and "what's in this picture" are both valid asks.
_READ_FILE_TRIGGER_RE = re.compile(
    r'\b(przeczyt|opisz|podsumow|podsumu|zobacz|spojrz|obejrzy|widzisz|read|summariz|describ)'
    r'|\bco\s+(tam|na\s+(obraz|zdj|fot)|w\s+plik|sadzisz|widzisz|widac'
    r'|jest\s+(w|na)|o\s+(tym|nim))'
    r'|\bwhat[\s\'a-z]*\b.*?\b(file|image|attach|picture|photo)\b',
    re.IGNORECASE,
)
_GRILL_END_MARKER = "Grill finished"
_GRILL_END_MARKERS = (_GRILL_END_MARKER, "Grill zakończony")  # Polish marker from older sessions

# After Bob replies, treat follow-up messages from the same sender as continuation
# (no "Bob" tag required) for this many seconds.
_CONTINUATION_WINDOW_S = 120

# Burst batching: merge consecutive same-sender messages within this window into one.
_BURST_WINDOW_S = 10

# Catch-up aggregation: if bot was offline longer than this AND there are enough
# pending triggered messages, reply with one aggregated response instead of N replies.
_CATCHUP_THRESHOLD_S = 300   # 5 minutes
_CATCHUP_MIN_MSGS = 4        # minimum triggered messages to activate catch-up
_STATS_POST_INTERVAL_S = 10 * 60  # post/edit stats to the notes chat every 10 min
_NOTIF_CHECK_INTERVAL_S = 60     # check due notifications every 60 s
_PANEL_APPROVALS_INTERVAL_S = 60  # new yes/no requests from Bob's panel -> notes chat (bob_panel/approvals.py)
_PANEL_APPROVALS_LOCK = threading.Lock()
# The SharePoint poller checks TimeLastModified (one cheap REST call) before doing
# any real download/import work -- see sharepoint_meta.py -- so a short interval here
# just controls change-detection latency, not download frequency.
_SHAREPOINT_POLL_INTERVAL_S = 60 * 60  # check the SharePoint leave XLSX every 60 min
# Hard kill for a poller subprocess. MUST be larger than the poller's own budget,
# otherwise we kill it just before it saves its state and all the work repeats on every
# tick. The leave poller is an SSO session + an xlsx download, so 300 s is plenty.
_POLLER_SUBPROCESS_TIMEOUT_S = 300
_SP_SESSION_WARM_INTERVAL_S = 6 * 60 * 60   # re-warm both SP domains in session.json every 6h
_BROADCAST_CHECK_INTERVAL_S = 30  # check broadcast schedule every 30 s
_DAEMON_WATCHDOG_INTERVAL_S = 60  # re-check MCP daemons (ai._DAEMON_SERVERS) every 60 s



def _ensure_daemon_servers(restart: bool) -> None:
    """Keep ai._DAEMON_SERVERS (read-only MCP servers) running as HTTP daemons in stdio mode.

    restart=True (watch start): always restart, so the daemon serves the current code.
    restart=False (watchdog tick): start only the ones that stopped listening.
    The MCP config is regenerated afterwards in both cases, so claude CLI goes over HTTP
    only to daemons that actually listen and falls back to stdio for the others.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mcp_admin.daemon import is_listening, restart_daemon  # noqa: PLC0415

    changed = restart
    for name in sorted(ai._DAEMON_SERVERS):
        if not restart and is_listening(name):
            continue
        ok = restart_daemon(name, ai._MCP_SERVERS[name])
        changed = True
        state = "running" if ok else "did NOT start -- falling back to stdio"
        print(f"[watch] MCP daemon {name}: {state}", flush=True)
    if changed:
        ai._refresh_mcp_config()


# ---------------------------------------------------------------------------
# Owner control commands — pause / disable / resume Bob from chat
# ---------------------------------------------------------------------------

_BOB_CONTROL_FILE = str(Path(__file__).parent / "bob-control.json")
_BROADCAST_SCHEDULE_FILE = (
    Path(__file__).resolve().parents[1] / "broadcast" / "broadcast-schedule.json"
)
_BROADCAST_RUNNER_SCRIPT = (
    Path(__file__).resolve().parents[1] / "broadcast" / "broadcast_runner.py"
)
# "Bob pause 30m" / "Bob pause 2h" / "Bob pause 45 min"
_PAUSE_RE = re.compile(
    r'\bbob\s+pause\s+(\d+)\s*(m|min|h|g|godz|hour)',  # g/godz = Polish hours
    re.IGNORECASE,
)
# "Bob off" / "Bob stop" / "Bob disable" (Polish aliases: "Bob wyłącz" / "Bob wylacz")
# Note: must NOT match "Bob disable chat X" — those are handled by _CHAT_DISABLE_RE first.
_BOB_OFF_RE = re.compile(
    r'\bbob\s+(off|stop|disable|wyłącz|wylacz)\b(?!\s+chat\b)',
    re.IGNORECASE,
)
# "Bob on" / "Bob resume" / "Bob start" / "Bob enable" (Polish aliases: "Bob włącz" / "Bob wróć"); "enable" only on
# its own, so "Bob enable notifications for X" goes to Claude
_BOB_ON_RE = re.compile(
    r'\bbob\s+(on|resume|start|włącz|wlacz|wróć|wroc|enable(?=\W*$))\b(?!\s+chat\b)',
    re.IGNORECASE,
)
# "Bob disable chat X" / "Bob off chat X" (Polish alias: "Bob wyłącz chat X")
_CHAT_DISABLE_RE = re.compile(
    r'\bbob\s+(?:wyłącz|wylacz|disable|off)\s+chat\s+(.+)',
    re.IGNORECASE,
)
# "Bob enable chat X" / "Bob on chat X" (Polish alias: "Bob włącz chat X")
_CHAT_ENABLE_RE = re.compile(
    r'\bbob\s+(?:włącz|wlacz|enable|on)\s+chat\s+(.+)',
    re.IGNORECASE,
)
# "Bob, approve apr-1a2b3c4d5e: note" / "Bob, reject apr-..." — a yes/no request from Bob's panel
# (Polish aliases: "zatwierdzam" / "odrzucam" and their variants)
# (scripts/bob_panel/approvals.py), announced in the control chat; owner + control chat only
_APR_DECIDE_RE = re.compile(
    r'\bbob\W+(approve|reject|zatwierdzam|zatwierdź|zatwierdz|akceptuję|akceptuje|odrzucam|odrzuć|odrzuc)\s+'
    r'(apr-[0-9a-f]{10})\b\s*[:\-–]?\s*(.*)',
    re.IGNORECASE | re.DOTALL,
)
# the same without the id ("Bob approve", optionally ": note") - only when exactly one decision waits
_APR_DECIDE_BARE_RE = re.compile(
    r'^\W*bob\W+(approve|reject|zatwierdzam|zatwierdź|zatwierdz|akceptuję|akceptuje|odrzucam|odrzuć|odrzuc)'
    r'\W*(?::\s*(.*))?$',
    re.IGNORECASE | re.DOTALL,
)
# "Bob chats" / "Bob chat status" — show per-chat enabled/disabled status
# (Polish aliases: "Bob chaty" / "Bob status chatów")
_CHAT_STATUS_RE = re.compile(
    r'\bbob\s+(?:chats|chat\s+status|chaty|status\s+chat(?:ów|ow)?)\b',
    re.IGNORECASE,
)
# "Bob ?" / "Bob help" (Polish alias: "Bob pomoc") — show available commands
_BOB_HELP_RE = re.compile(
    r'\bbob\s+(\?|help|pomoc)(?=\s|$)',
    re.IGNORECASE,
)
# "Bob poll <topic>" (Polish alias: "Bob ankieta <topic>") — send an Adaptive Card poll for Bob configuration;
# the English form only with a known topic
_BOB_POLL_RE = re.compile(
    r'\bbob\s+(?:poll\s+(?=(?:model|pause|onoff|chat)\b)|ankieta\s+)(.+)',
    re.IGNORECASE,
)
# Fallback text commands for per-chat config (used when Adaptive Card is unavailable).
# "Bob chat <name> mcp <csv|all|none>"
_BOB_CHAT_MCP_RE = re.compile(
    r'\bbob\s+chat\s+(.+?)\s+mcp\s+(\S.*)',
    re.IGNORECASE,
)
# "Bob chat <name> caller <owner|all|auto|clear>"
_BOB_CHAT_CALLER_RE = re.compile(
    r'\bbob\s+chat\s+(.+?)\s+caller\s+(\S+)',
    re.IGNORECASE,
)
# "Bob chat <name> epithets <csv|none>"
_BOB_CHAT_EPITHETS_RE = re.compile(
    r'\bbob\s+chat\s+(.+?)\s+epithets\s+(\S.*)',
    re.IGNORECASE,
)
# "Bob chat <name> internet <all|only_me|off>"
_BOB_CHAT_INTERNET_RE = re.compile(
    r'\bbob\s+chat\s+(.+?)\s+internet\s+(\S+)',
    re.IGNORECASE,
)
# "Bob model haiku / sonnet / opus / claude-..." — global model switch
_BOB_MODEL_RE = re.compile(
    r'\bbob\s+model\s+(haiku|sonnet|opus|claude-[\w.-]+)',
    re.IGNORECASE,
)
# "Bob caller owner / all / auto" — global caller mode
_BOB_CALLER_GLOBAL_RE = re.compile(
    r'\bbob\s+caller\s+(owner|all|auto)',
    re.IGNORECASE,
)
# "Bob config <chat name>" — show full config for one chat
_BOB_CONFIG_RE = re.compile(
    r'\bbob\s+config\s+(.+)',
    re.IGNORECASE,
)
# "Bob, forward to <target>" / "Bob, send it to <target>" — forward last reply to another chat
# (control_chat + owner only). Separators: space, comma, dash, colon (e.g. "Bob - forward to X").
# Polish aliases: "Bob, wyślij na X" / "Bob prześlij to na X".
_BOB_FORWARD_RE = re.compile(
    r'\bbob[\s,\-:]+(?:(?:forward|send)(?:\s+(?:this|it))?\s+to(?!\s+(?:me|us|him|her|them)\b)'
    r'|(?:wy[ssś]lij|prze[ssś]lij)(?:\s+to)?\s+na)\s+(.+)',
    re.IGNORECASE | re.DOTALL,
)
# Replies that confirm a pending forward (English canonical, Polish alias).
_FWD_CONFIRM_WORDS = frozenset({"yes", "tak"})
# Teams chat link — capture conv_id from path component (19:...@thread.v2 or similar)
_TEAMS_LINK_RE = re.compile(
    r'teams\.microsoft\.com/l/chat/(19:[^/\s?]+)',
)


# "Bob, remember: ..." / "Bob, remember that ..." (Polish alias "Bob, zapamiętaj: ...") — private notes, owner-only
# by default, configurable per chat. The English form needs the colon or "that": "Bob, remember the ticket ...?" is a
# question, not a note.
_ZAPAMIETAJ_RE = re.compile(
    r'\bbob[,\s]+(?:remember\s*(?::|\bthat\b)|zapami[eę]taj?[,:\s]*)\s*(.+)',
    re.IGNORECASE | re.DOTALL,
)
# "Bob, show notes" (Polish alias "Bob, pokaż notatki")
_POKAZ_NOTATKI_RE = re.compile(
    r'\bbob[,\s]+(?:show[,\s]+notes|poka[zż][,\s]+notatki)',
    re.IGNORECASE,
)

# "Bob, build me a game / generate an app / make me a website ..." (and the Polish
# "wygeneruj/zbuduj/stwórz/zrób mi grę/appkę/stronę www ...") — real code
# generation, gated behind owner approval (see _handle_build_approval_gate).
# Scoped narrowly: verb and target must appear within a few words of each other
# (not just anywhere in the message), so a Jira-bug/incident report that merely
# *quotes* an unrelated "website"/"app" somewhere in a pasted error/URL doesn't
# get misrouted into the build gate. Normal asks — Jira tickets, KB search,
# summaries, TTS audio, tables — are unaffected.
_BOB_BUILD_VERB_RE = re.compile(
    # English: only a command aimed at somebody ("build me ...", "make us ..."); a bare "build" / "generate" is
    # usually a noun or an ordinary ask ("why does the build of the app fail", "generate release notes for the app").
    r'\b(?:((?:build|code|make|create|write|generate)\s+(?:me|us))\s+(?:an?\s+)?(?:\w+\s+){0,2}?'
    r'(game|app|website|prototype)\b'
    r'|(wygeneruj\w*|zbuduj\w*|zbudowa\w*|stw[oó]rz\w*|stworzy\w*|zr[oó]b\w*|zrobi[cć]|napisz mi)\b'
    r'(?:\W+\w+){0,4}?\W+'
    r'(\bgr[eę]\b|\bgra\b|\bgry\b|gierk\w*|aplikacj\w*|appk\w*|\bapp\b|'
    r'stron[eę]\s*(?:www|internetow\w*)|\bwebsite\b|\bprototyp\w*))',
    re.IGNORECASE,
)
# Owner-only: "Bob approve <id>" / "Bob reject <id>" (Polish aliases "zatwierdź" / "odrzuć") resolve a pending build.
_BOB_BUILD_APPROVE_RE = re.compile(r'\bbob\s+(?:approve|zatwierd[zź])\s+(\d+)', re.IGNORECASE)
_BOB_BUILD_REJECT_RE = re.compile(r'\bbob\s+(?:reject|odrzu[cć])\s+(\d+)', re.IGNORECASE)

# "Bob, schedule today: text" / "Bob, schedule 28.05: text" / range "28.05-30.05: text"
# (Polish alias "Bob, harmonogram dziś: treść"). The English form needs a date first: "Bob, schedule a call ..." is
# an ordinary ask.
_HARMONOGRAM_SET_RE = re.compile(
    r'\bbob[,\s]+(?:schedule\s+(?=today|tomorrow|yesterday|\d)|harmonogram\s+)([^\s:]+)[:\s]+(.+)',
    re.IGNORECASE | re.DOTALL,
)
# "Bob, show schedule" (Polish alias "Bob, pokaż harmonogram")
_POKAZ_HARMONOGRAM_RE = re.compile(
    r'\bbob[,\s]+(?:show\s+schedule|poka[zż]\s+harmonogram)',
    re.IGNORECASE,
)
# "Bob, schedule delete 28.05" / "schedule clear today" (Polish alias "Bob, harmonogram usuń 28.05")
_HARMONOGRAM_CLEAR_RE = re.compile(
    r'\bbob[,\s]+(?:schedule|harmonogram)\s+(?:delete|clear|usu[nń])\s+(\S+)',
    re.IGNORECASE,
)

# "Bob, broadcast status"
_BROADCAST_STATUS_RE = re.compile(
    r'\bbob[,\s]+broadcast\s+status',
    re.IGNORECASE,
)
# "Bob, set broadcast <team> send HH:MM"
# Also accepts: generate|dry-run instead of send for the generate/dry-run phase
# (Polish aliases: "ustaw" for set, "wyslij" for send, "generuj" for generate)
_BROADCAST_SET_RE = re.compile(
    r'\bbob[,\s]+(?:set|ustaw)\s+broadcast\s+(\S+)\s+(send|generate|wyslij|generuj|dry-run)\s+(\d{1,2}:\d{2})',
    re.IGNORECASE,
)

# "Bob, remind today|tomorrow|28.05 HH:MM in [chat name]: [text]"
# (Polish alias "Bob, przypomnij dziś|jutro|28.05 HH:MM na [nazwa chatu]: [treść]")
_NOTIF_SET_RE = re.compile(
    r'\bbob[,\s]+(?:remind|przypomnij)\s+(\S+)\s+(\d{1,2}:\d{2})\s+(?:in|on|na)\s+(.+?)(?::\s*|\s+-\s+)(.+)',
    re.IGNORECASE | re.DOTALL,
)
# "Bob, show reminders" (Polish alias "Bob, pokaż przypomnienia")
_NOTIF_SHOW_RE = re.compile(r'\bbob[,\s]+(?:show\s+reminders|poka[zż]\s+przypomnienia)', re.IGNORECASE)
# "Bob, delete reminder [id]" (Polish alias "Bob, usuń przypomnienie [id]")
_NOTIF_DEL_RE = re.compile(r'\bbob[,\s]+(?:delete\s+reminder|usu[nń]\s+przypomnienie)\s+(\S+)', re.IGNORECASE)
# "Bob, delete overdue [reminders|tasks]" (Polish alias "Bob, usuń zaległe")
_NOTIF_DEL_OVERDUE_RE = re.compile(r'\bbob[,\s]+(?:delete\s+overdue|usu[nń]\s+zaleg[łl][ey])\b', re.IGNORECASE)


def _parse_reminder_time(token: str) -> tuple[int, int] | None:
    """Parse 'HH:MM'. Returns (hour, minute) or None."""
    m = re.match(r'^(\d{1,2}):(\d{2})$', token.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return (h, mi)


def _parse_reminder_datetime(date_token: str, time_token: str) -> datetime | None:
    """Parse a date token ('today|tomorrow|DD.MM[.YYYY]', or the Polish 'dzis|jutro') + 'HH:MM'
    into a naive local datetime, or None.

    Delegates date parsing to owner_schedule.parse_date (the same vocabulary used by
    'Bob, schedule ...').
    """
    d = owner_schedule.parse_date(date_token)
    if d is None:
        return None
    t = _parse_reminder_time(time_token)
    if t is None:
        return None
    return datetime(d.year, d.month, d.day, t[0], t[1])

_BOB_PRIVATE_DIR = Path(__file__).resolve().parents[2] / "bob-private"


def _chat_name_to_filename(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or "default"


def _load_private_notes(chat_name: str) -> str | None:
    path = _BOB_PRIVATE_DIR / f"{_chat_name_to_filename(chat_name)}.md"
    try:
        content = path.read_text(encoding="utf-8").strip()
        return content if content else None
    except FileNotFoundError:
        return None


def _load_user_context(sender_mri: str) -> str | None:
    """Load rolling memory context for *sender_mri*.

    Returns None for empty mri, missing/expired profile, or missing summary.
    Never raises.
    """
    if not sender_mri:
        return None
    try:
        profile = user_memory.load_profile(sender_mri)
        return user_memory.format_context(profile)
    except Exception:
        return None


def _append_private_note(chat_name: str, note: str, sender: str) -> None:
    _BOB_PRIVATE_DIR.mkdir(exist_ok=True)
    path = _BOB_PRIVATE_DIR / f"{_chat_name_to_filename(chat_name)}.md"
    ts = datetime.now(_CET).strftime("%Y-%m-%d %H:%M %Z")
    entry = f"\n---\n{ts} [{sender}]\n{note.strip()}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)


def _load_control() -> dict:
    try:
        p = Path(_BOB_CONTROL_FILE)
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def _save_control(state: dict) -> None:
    try:
        # atomic: the panel reads this file too (chatcfg) - never a half-written state
        tmp = Path(_BOB_CONTROL_FILE).with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, _BOB_CONTROL_FILE)
    except Exception as exc:
        print(f"[watch] ERROR writing bob-control: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Build-approval gate — "Bob, build me a game/app/website ..." needs
# real code-generation work (Write/Agent) that the sandboxed reply pipeline does
# NOT have (see ai.py's _NO_TOOLS_LOCKDOWN). Rather than let such a request
# silently fail, loop, or get fulfilled without oversight, it's parked here for
# the owner to approve/reject explicitly. Jira creation, KB/info search, and every
# other normal Bob capability go through jira_create/kb_search MCP tools inside
# the sandboxed pipeline and never touch this gate.
# ---------------------------------------------------------------------------
_PENDING_BUILDS_FILE = str(Path(__file__).parent / "pending-builds.json")


def _load_pending_builds() -> list[dict]:
    try:
        p = Path(_PENDING_BUILDS_FILE)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_pending_builds(items: list[dict]) -> None:
    try:
        Path(_PENDING_BUILDS_FILE).write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception as exc:
        print(f"[watch] ERROR writing pending-builds: {exc}", file=sys.stderr)


def _add_pending_build(chat_name: str, conv_id: str, sender: str, text: str) -> str:
    items = _load_pending_builds()
    build_id = str(max((int(it.get("id", 0)) for it in items), default=0) + 1)
    items.append({
        "id": build_id,
        "ts": _ts(),
        "chat_name": chat_name,
        "conv_id": conv_id,
        "sender": sender,
        "text": text,
        "status": "pending",
    })
    _save_pending_builds(items)
    return build_id


def _find_pending_build(build_id: str) -> dict | None:
    for it in _load_pending_builds():
        if it.get("id") == build_id and it.get("status") == "pending":
            return it
    return None


def _set_pending_build_status(build_id: str, status: str) -> None:
    items = _load_pending_builds()
    for it in items:
        if it.get("id") == build_id:
            it["status"] = status
    _save_pending_builds(items)


def _handle_build_approval_gate(
    chat_name: str, conv_id: str, sender: str, text: str,
) -> str:
    """Park a heavy-generation request and ping the owner for a yes/no instead of
    letting it silently attempt (and possibly loop on) real code generation."""
    build_id = _add_pending_build(chat_name, conv_id, sender, text)
    try:
        api_client.send_message(
            f"[Approval #{build_id}] {sender} in {chat_name!r} asks:\n"
            f"\"{text.strip()[:500]}\"\n\n"
            f"This needs real code generation (Agent/Write) — I don't do that "
            f"without your approval. Reply here (notes chat):\n"
            f"'Bob approve {build_id}' or 'Bob reject {build_id}'.",
            conversation_id=api_client.NOTES_CONV_ID,
        )
    except Exception as exc:
        print(f"[watch] ERROR sending the approval request: {exc}", file=sys.stderr)
    return (
        "That's a bigger job — building real code, not my usual work. "
        "I'm asking the owner for approval and will come back as soon as they confirm 🙂"
    )


def _load_broadcast_schedule() -> dict:
    """Load broadcast-schedule.json. Returns {} on missing file or parse error."""
    try:
        p = _BROADCAST_SCHEDULE_FILE
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_broadcast_schedule(schedule: dict) -> None:
    """Persist broadcast-schedule.json (ensure_ascii=False, indent=2)."""
    try:
        _BROADCAST_SCHEDULE_FILE.write_text(
            json.dumps(schedule, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[broadcast] ERROR writing broadcast-schedule.json: {exc}", file=sys.stderr)


def _is_broadcast_planning_day(today) -> bool:
    """Return True if today is a Sprint Planning broadcast day.

    Anchor: Tuesday 2026-06-16, repeat every 14 days.
    """
    from datetime import date as _date
    anchor = _date(2026, 6, 16)
    return today.weekday() == 1 and (today - anchor).days % 14 == 0


# In-memory set: tracks which (job_id, phase, date) combos have already fired today.
# Reset automatically when date changes (key includes date).
_fired_broadcasts: dict[str, bool] = {}


def _broadcast_scheduler_tick(log_path: str) -> None:
    """Check broadcast schedule and fire due phases as subprocesses.

    Called every ~30 s from the watch loop executor. Non-blocking.
    """
    _CET = ZoneInfo(_TZ_NAME)
    now_cet = datetime.now(_CET)
    today = now_cet.date()

    is_planning_day = _is_broadcast_planning_day(today)
    is_workday = today.weekday() < 5  # Mon-Fri

    schedule = _load_broadcast_schedule()
    if not schedule:
        return

    # Load jobs to check schedule_type per job.
    _jobs_file = _BROADCAST_RUNNER_SCRIPT.parent / "jobs.json"
    try:
        _jobs_list = json.loads(_jobs_file.read_text(encoding="utf-8"))
        _jobs_by_id = {j["id"]: j for j in _jobs_list}
    except Exception:
        _jobs_by_id = {}

    phase_keys = [
        ("generate", "generate"),
        ("dry_run", "dry-run"),
        ("send", "send"),
        ("generate_send", "generate-send"),
    ]

    for job_id, times in schedule.items():
        job_def = _jobs_by_id.get(job_id, {})
        # enabled=false: do not fire at all -- the runner would exit 1 anyway,
        # which would litter the log with errors on every tick.
        if job_def.get("enabled") is False:
            continue
        schedule_type = job_def.get("schedule_type", "planning")

        if schedule_type == "always":
            pass  # fire every day of the week, no filter
        elif schedule_type == "daily":
            if not is_workday:
                continue
        elif schedule_type == "weekly":
            if today.weekday() != int(job_def.get("weekday", 0)):  # 0 = Monday
                continue
        else:
            if not is_planning_day:
                continue

        for time_key, phase_arg in phase_keys:
            hhmm = times.get(time_key)
            if not hhmm:
                continue
            parsed = _parse_reminder_time(hhmm)
            if not parsed:
                continue
            h, mi = parsed
            fire_key = f"{job_id}:{phase_arg}:{today.isoformat()}"
            if _fired_broadcasts.get(fire_key):
                continue

            # Also check idempotency via generated file (survives restarts).
            # Generator-based jobs (e.g. worklog-daily) build and send their own
            # content deterministically and never populate generated/<job_id>.json,
            # so they are exempt from this gate.
            if phase_arg in ("dry-run", "send") and not job_def.get("generator"):
                _gen_dir = _BROADCAST_RUNNER_SCRIPT.parent / "generated"
                _gen_file = _gen_dir / f"{job_id}.json"
                # If generated file does not exist and this is dry-run/send,
                # we cannot fire — skip. The generate phase must run first.
                if phase_arg == "send" and not _gen_file.exists():
                    continue
                if phase_arg == "dry-run" and not _gen_file.exists():
                    continue

            # Check if current time >= scheduled time (within a 5-minute window
            # so we don't fire if the tick lands right on the minute boundary).
            now_minutes = now_cet.hour * 60 + now_cet.minute
            sched_minutes = h * 60 + mi
            if not (sched_minutes <= now_minutes < sched_minutes + 5):
                continue

            # Fire the phase.
            _fired_broadcasts[fire_key] = True
            print(
                f"[watch] broadcast_fired: job={job_id} phase={phase_arg} "
                f"sched={hhmm} now_cet={now_cet.strftime('%H:%M')}",
                flush=True,
            )
            _append_log(log_path, {
                "ts": _ts(),
                "event": "broadcast_fired",
                "job_id": job_id,
                "phase": phase_arg,
                "sched": hhmm,
            })
            try:
                subprocess.Popen(
                    [sys.executable, str(_BROADCAST_RUNNER_SCRIPT),
                     "--job", job_id, "--phase", phase_arg],
                )
            except Exception as exc:
                print(
                    f"[watch] broadcast error: job={job_id} phase={phase_arg}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )


def _is_paused() -> tuple[bool, str]:
    """Return (is_paused, reason). Cheap — reads small JSON once per tick."""
    ctrl = _load_control()
    if ctrl.get("disabled"):
        return True, "disabled (Bob off)"
    paused_until = ctrl.get("paused_until")
    if paused_until:
        try:
            until_dt = datetime.fromisoformat(paused_until)
            now = datetime.now(UTC)
            if now < until_dt:
                remaining_m = int((until_dt - now).total_seconds() / 60) + 1
                return True, f"paused until {until_dt.strftime('%H:%M')} UTC (~{remaining_m} min)"
        except Exception:
            pass
    return False, ""


def _is_chat_disabled(chat_name: str) -> bool:
    """Return True if this specific chat has been disabled via Bob command or Python API."""
    return chat_name in _load_control().get("disabled_chats", [])


def disable_chat(name: str) -> None:
    """Disable a specific chat by name (Python API)."""
    ctrl = _load_control()
    disabled = set(ctrl.get("disabled_chats", []))
    disabled.add(name)
    ctrl["disabled_chats"] = sorted(disabled)
    _save_control(ctrl)
    print(f"[control] Chat disabled: {name!r}", flush=True)


def enable_chat(name: str) -> None:
    """Enable a specific chat by name (Python API)."""
    ctrl = _load_control()
    disabled = set(ctrl.get("disabled_chats", []))
    disabled.discard(name)
    ctrl["disabled_chats"] = sorted(disabled)
    _save_control(ctrl)
    print(f"[control] Chat enabled: {name!r}", flush=True)


def list_chat_status(chat_names: list[str]) -> dict[str, bool]:
    """Return {chat_name: is_enabled} for given chat names."""
    disabled = set(_load_control().get("disabled_chats", []))
    return {name: name not in disabled for name in chat_names}


def _norm_chat(cfg: object) -> dict:
    """One chat's chat-config entry with English keys (roles <- role, add_groups <- dodatkowo, ...).

    roles.normalize_chat owns the alias table; this wrapper only tolerates a missing (None) entry.
    """
    if not isinstance(cfg, dict):
        return {}
    import roles  # noqa: PLC0415
    return roles.normalize_chat(cfg)


def _norm_chats(chats: object) -> dict:
    """Every chat of chat-config.json "chats" through _norm_chat; non-dict entries are kept as they are."""
    if not isinstance(chats, dict):
        return {}
    return {name: (_norm_chat(cfg) if isinstance(cfg, dict) else cfg) for name, cfg in chats.items()}


def _config_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _reload_chat_config_if_changed(chat_config: dict, path: Path, seen_mtime: float) -> float:
    """Hot-reload chat-config.json into `chat_config` IN PLACE when the file changed.

    In place, because the watch loop hands the same dict to every consumer. A file that
    fails to parse (half-saved edit) keeps the old config and is retried next tick.
    Chats added to the file still need a restart (conv_ids are resolved at start).
    Returns the mtime to remember.
    """
    mtime = _config_mtime(path)
    if mtime == seen_mtime:
        return seen_mtime
    try:
        fresh = json.loads(path.read_text(encoding="utf-8")).get("chats")
    except Exception as exc:  # noqa: BLE001 -- keep the running config
        print(f"[watch] ERROR reloading chat-config {path}: {exc} -- keeping the previous one", file=sys.stderr)
        return seen_mtime
    if not isinstance(fresh, dict):
        return seen_mtime
    fresh = _norm_chats(fresh)
    if fresh != chat_config:
        chat_config.clear()
        chat_config.update(fresh)
        print("[watch] chat-config.json changed -- reloaded", flush=True)
    return mtime


def _load_chat_config(path: Path) -> dict:
    """Load chat-config.json. Returns {chat_name: {"knowledge": None|list}} mapping."""
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return _norm_chats(data.get("chats", {}))
    except Exception as exc:
        print(f"[watch] ERROR loading chat-config {path}: {exc}", file=sys.stderr)
    return {}


def _save_chat_config(chat_config: dict, path: Path = _CHAT_CONFIG_PATH) -> None:
    """Atomically write chat-config.json preserving the top-level _comment field.

    Reads the existing file to extract _comment, replaces only the 'chats' key,
    then writes to a tmp file and renames it atomically (os.replace).
    Formatting: indent=2, ensure_ascii=False, trailing newline.
    """
    existing: dict = {}
    try:
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[watch] WARNING: cannot read {path} before writing: {exc}",
              file=sys.stderr)
    result: dict = {}
    if "_comment" in existing:
        result["_comment"] = existing["_comment"]
    result["chats"] = chat_config
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception as exc:
        print(f"[watch] ERROR writing chat-config {path}: {exc}", file=sys.stderr)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


_CHAT_CONFIG_MUTABLE_FIELDS = frozenset({"mcp", "caller", "epithets", "internet"})


def _apply_chat_config_change(
    name: str,
    field: str,
    value: object,
    chat_config: dict,
    path: Path = _CHAT_CONFIG_PATH,
) -> None:
    """Mutate chat_config[name][field] in-place and persist to disk.

    Validation: field must be in _CHAT_CONFIG_MUTABLE_FIELDS.
    For 'epithets': value=None removes the key (default prompt).
    For 'caller': value=None removes the key (fallback to args.caller).
    """
    if field not in _CHAT_CONFIG_MUTABLE_FIELDS:
        print(f"[watch] _apply_chat_config_change: invalid field {field!r}", file=sys.stderr)
        return
    if name not in chat_config:
        chat_config[name] = {}
    chat_config[name] = _norm_chat(chat_config[name])
    if field == "mcp" and "roles" in chat_config[name]:
        # an explicit group list from the control chat replaces the chat's roles: it goes back to the hand-written list
        print(f"[watch] {name!r}: roles {chat_config[name].get('roles')} replaced by a hand-written mcp list",
              flush=True)
        for key in ("roles", "remove_groups", "add_groups"):
            chat_config[name].pop(key, None)
    if field == "epithets":
        if value is None or (isinstance(value, list) and len(value) == 0):
            chat_config[name].pop("epithets", None)
        else:
            chat_config[name]["epithets"] = list(value)
    elif field == "caller":
        if value is None or value == "clear":
            chat_config[name].pop("caller", None)
        else:
            chat_config[name]["caller"] = value
    elif field == "internet":
        if value in (None, "off", False):
            chat_config[name].pop("internet", None)
        elif value in ("all", "only_me"):
            chat_config[name]["internet"] = value
        else:
            print(
                f"[watch] _apply_chat_config_change: invalid internet value {value!r}"
                " (allowed: all, only_me, off)",
                file=sys.stderr,
            )
            return
    else:
        chat_config[name][field] = value
    _save_chat_config(chat_config, path)


def _conv_id_override_for(chat_name: str, chat_config: dict) -> str | None:
    """Return hardcoded conv_id for a chat if set in chat-config.json, else None."""
    return (chat_config.get(chat_name) or {}).get("conv_id")


def _ingest_only_for(chat_name: str, chat_config: dict) -> bool:
    """True when chat-config marks the chat "ingest_only": Bob listens, never replies/posts."""
    return bool((chat_config.get(chat_name) or {}).get("ingest_only"))


def _receive_only_conv_ids(chat_config: dict) -> set[str]:
    """conv_ids of every ingest_only chat that has a hardcoded conv_id in chat-config."""
    return {
        cfg["conv_id"] for cfg in chat_config.values()
        if isinstance(cfg, dict) and cfg.get("ingest_only") and cfg.get("conv_id")
    }


def _refresh_receive_only(chat_config: dict, extra_ids: set[str] | None = None) -> None:
    """Replace api_client.RECEIVE_ONLY_CONV_IDS (config + resolved ingest-only ids).

    One atomic rebind, never clear()+update(): worker threads check the set while this runs
    every tick, and an empty set in between would let a post into the listen-only chat through."""
    ids = _receive_only_conv_ids(chat_config) | (extra_ids or set())
    api_client.RECEIVE_ONLY_CONV_IDS = frozenset(ids)


def _caller_for(chat_name: str, chat_config: dict, args: argparse.Namespace) -> str:
    """Return per-chat caller mode, falling back to the global --caller arg."""
    per_chat = (chat_config.get(chat_name) or {}).get("caller")
    if per_chat in ("owner", "all", "auto"):
        return per_chat
    return args.caller


def _hold_s_for(chat_name: str, chat_config: dict | None) -> int:
    """Seconds to hold an UNINVITED reply on this channel before sending it.

    chat-config.json "hold_s". On a channel where the agent answers without being
    called (caller="auto"), a human standing by is a better answer than the bot —
    so the reply waits, and _check_skip_answered() re-polls afterwards and drops
    it if somebody spoke in the meantime. Absent/0/garbage -> no hold.
    """
    try:
        return max(0, int(((chat_config or {}).get(chat_name) or {}).get("hold_s") or 0))
    except (TypeError, ValueError):
        return 0


def _hold_wait_s(hold_s: int, msg_ts: float, now: float, invited: bool) -> float:
    """Seconds still to wait before sending an uninvited reply (0 = send now).

    The window runs from when the message was POSTED, not from when we started
    answering it: messages are answered one by one per chat, so one measured from
    our own start stacked a full hold on every message of a burst. A message that
    already sat in the queue for hold_s gets no extra wait. An invited reply (called
    by name, quoting the bot, answering its question) never waits. Unknown
    timestamp -> full hold counted from now.
    """
    if invited or hold_s <= 0:
        return 0.0
    start = msg_ts if msg_ts > 0 else now
    return max(0.0, hold_s - (now - start))


def _control_chat_for(chat_name: str, chat_config: dict) -> bool:
    """Return True if this chat is allowed to issue Bob control commands."""
    return bool((chat_config.get(chat_name) or {}).get("control_chat", False))


def _mcp_for(chat_name: str, chat_config: dict) -> list[str] | None:
    """Return list of allowed MCP groups for this chat, or None (= all groups).

    chat-config.json format: "roles": ["project_manager", ...] (roles.json, scripts/teams_agent/roles.py), or the
    older hand-written "mcp": ["jira", "todos"] or omitted/null for all.
    Valid group names: keys of ai._MCP_GROUPS.
    "mcp_extra": ["<group>"] adds opt-in groups (ai._OPT_IN_GROUPS) on top of
    "mcp": null, so the chat keeps every default group and gains the extras.
    """
    # roles (roles.json, read fresh: panel edits apply at once) or the hand-written list; a chat on a channel
    # with external users ("external_chat") never gets a team-only group
    import roles  # noqa: PLC0415
    cfg = _norm_chat(chat_config.get(chat_name))
    doc = roles.load() if ("roles" in cfg or cfg.get("external_chat")) else {}
    return roles.effective_groups(cfg, doc, list(ai._ALL_GROUPS), list(ai._MCP_GROUPS))


def _role_knowledge_for(chat_name: str, chat_config: dict | None) -> str:
    """The chat's role knowledge added in the panel ("wiring": "prompt" in roles.json), read fresh per reply."""
    cfg = _norm_chat((chat_config or {}).get(chat_name))
    if "roles" not in cfg:
        return ""
    import roles  # noqa: PLC0415
    try:
        return roles.knowledge_text(roles.knowledge_for(cfg, roles.load()))
    except Exception as exc:  # noqa: BLE001 -- knowledge must never block a reply
        print(f"[watch] role knowledge {chat_name!r}: {exc}", file=sys.stderr, flush=True)
        return ""


def _epithets_for(chat_name: str, chat_config: dict) -> list[str] | None:
    """Return per-channel personality epithets, or None if not configured.

    chat-config.json format: "epithets": ["concise", "formal", "analytical"]
    """
    return (chat_config.get(chat_name) or {}).get("epithets", None)


def _persona_for(chat_name: str, chat_config: dict) -> str | None:
    """Return per-channel persona name, or None if not configured.

    chat-config.json format: "persona": "example" (prompts/example_persona.md)
    Absent / null / unknown -> None (default Bob persona).
    Valid names live in ai._PERSONA_PROMPTS + api_client.AGENT_LABELS.
    """
    return (chat_config.get(chat_name) or {}).get("persona", None)


def _model_for(chat_name: str, chat_config: dict, args: argparse.Namespace) -> str:
    """Per-channel model: chat-config.json "model" (alias haiku/sonnet/opus or a full
    model id) overrides the global one (--model / "Bob, model X" / model poll)."""
    raw = (chat_config.get(chat_name) or {}).get("model")
    if isinstance(raw, str) and raw.strip():
        return ai.MODEL_ALIASES.get(raw.strip().lower(), raw.strip())
    return args.model


def _effort_for(chat_name: str, chat_config: dict) -> str | None:
    """Per-channel reasoning effort: chat-config.json "effort" = low|medium|high|xhigh|max.
    Absent / unknown -> None (claude CLI default)."""
    raw = (chat_config.get(chat_name) or {}).get("effort")
    return raw if raw in ai.EFFORT_LEVELS else None


def _internet_for(chat_name: str, chat_config: dict) -> str | None:
    """Return per-chat internet access setting, or None if not configured.

    chat-config.json format: "internet": "all" | "only_me"
    Absent / null / other values -> None (no access).
    """
    raw = (chat_config.get(chat_name) or {}).get("internet", None)
    if raw in ("all", "only_me"):
        return raw
    return None


def _internet_allows(setting: str | None, is_owner: bool) -> bool:
    """Return True if internet access is allowed given the setting and sender role.

    "all"     -> True for everyone
    "only_me" -> True only for the owner (is_owner == True)
    None/other -> False
    """
    if setting == "all":
        return True
    if setting == "only_me":
        return is_owner
    return False


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bob — Teams terminal agent")
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help=f"Claude model to use for drafts (default: {DEFAULT_MODEL})")
    parser.add_argument('--max-chats', type=int, default=DEFAULT_MAX_CHATS, dest="max_chats",
                        help=f"Maximum number of chats to process in interactive mode "
                             f"(default: {DEFAULT_MAX_CHATS})")
    parser.add_argument('--force-login', action="store_true", dest="force_login",
                        help="Force re-authentication even if a fresh session exists")
    parser.add_argument('--log-file', default=DEFAULT_LOG_FILE, dest="log_file",
                        help=f"Path to JSONL audit log (default: {DEFAULT_LOG_FILE})")
    parser.add_argument('--headless', action="store_true",
                        help="Run Chromium headless. Default is False — Teams bounces "
                             "headless sessions back to OAuth even with valid cookies.")
    parser.add_argument('--watch', dest='watch_chats', nargs='+', default=DEFAULT_WATCH_CHATS,
                        help=f"Daemon mode: poll one or more chats (by name substring) and "
                             f"auto-reply to triggered messages. "
                             f"Default: {DEFAULT_WATCH_CHATS}. "
                             f"Example: --watch 'AI Channel' 'Team A'")
    parser.add_argument('--interactive', action='store_true',
                        help="Run the manual approval loop instead of watch daemon mode.")
    parser.add_argument('--poll', type=int, default=DEFAULT_POLL_S, dest='poll',
                        help=f"Watch mode tick interval in seconds (default: {DEFAULT_POLL_S}). "
                             f"Min 1 — the real bottleneck is the claude CLI (2-5s), not polling.")
    parser.add_argument('--rate-limit', type=int, default=DEFAULT_RATE_LIMIT_S, dest='rate_limit',
                        help=f"Minimum seconds between auto-replies in watch mode "
                             f"(default: {DEFAULT_RATE_LIMIT_S}).")
    parser.add_argument('--watch-state', default=DEFAULT_WATCH_STATE, dest='watch_state',
                        help=f"Path to watch state file (default: {DEFAULT_WATCH_STATE})")
    parser.add_argument('--sniff-api', action='store_true', dest='sniff_api',
                        help="Intercept and log all Teams API requests to api-sniff.jsonl "
                             "for diagnostics. Runs alongside normal watch mode.")
    parser.add_argument('--sniff-ws', action='store_true', dest='sniff_ws',
                        help="Passive WebSocket listener — log all Teams WS frames to "
                             "ws-sniff.jsonl without sending any replies. Use to discover "
                             "the real-time event format before implementing push-based mode.")
    parser.add_argument('--caller', choices=['owner', 'all', 'auto'], default='all', dest='caller',
                        help="Who triggers Bob: "
                             "'owner' = only the owner via 'Bob' mention, "
                             "'all' = anyone via 'Bob' mention (default), "
                             "'auto' = every message on the chat, no trigger needed.")
    return parser.parse_args(argv)


def _append_log(log_path: str, record: dict) -> None:
    """Append one JSONL record to the audit log (append mode, ensure_ascii=False)."""
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _check_skip_answered(
    conv_id: str,
    trigger_seq: int,
    trigger_msg_id: str,
    question: str,
    reply: str,
) -> tuple[bool, str]:
    """Re-poll the channel right before sending; skip if someone already answered.

    Short-circuits (no AI call) when the re-poll finds no messages newer than the
    trigger. Fail-open: any get_messages/classifier hiccup returns (False, "") so
    Bob sends normally rather than going silent.
    """
    recent = api_client.get_messages(conv_id, n=10)
    newer = [
        m for m in recent
        if m.get("sequence_id", 0) > trigger_seq
        and m.get("id") != trigger_msg_id
        # Bob's own posts (e.g. the "still working" note) are not someone else answering: their text starts
        # with the emoji, so the [Bob]-prefixed HTML is what identifies them
        and not (
            m.get("sender_mri") == api_client.USER_MRI
            and (m.get("text", "").lstrip().startswith(api_client.AGENT_TEXT_LABELS)
                 or m.get("raw_html", "").lstrip().startswith(api_client.BOT_HTML_PREFIXES))
        )
    ]
    if not newer:
        return False, ""
    res = ai.check_already_answered(question, reply, newer)
    skip = bool(res.get("answered")) and not res.get("override")
    return skip, res.get("evidence", "")


def _parse_json_payload(stdout: str) -> dict:
    """Return the last JSON object found in a poller subprocess's stdout, or {}.

    Tolerant of a leading CET timestamp prefix (e.g. "14:02:31 {...}") added by
    scripts/logts.py — finds the object's actual opening brace instead of assuming
    the line starts with it.
    """
    text = stdout.strip()
    brace_idx = text.find("{")
    if brace_idx == -1:
        return {}
    try:
        return json.loads(text[brace_idx:])
    except (json.JSONDecodeError, ValueError):
        return {}


def _extract_trigger(text: str) -> str | None:
    """Return the prompt portion after the 'Hey Claude' trigger, or None.

    Matches anywhere in the message text because Teams' inner_text concatenates
    sender/time prefixes with the body. The capture group is everything after
    the trigger (stripped).
    """
    if not text:
        return None
    m = _TRIGGER_RE.search(text)
    if not m:
        return None
    prompt = (m.group(1) or "").strip()
    return prompt or None


def _load_watch_state(path: str) -> dict:
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        print(f"[watch] ERROR reading state {path}: {exc}", file=sys.stderr)
    return {}


_watch_state_lock = threading.Lock()


def _save_watch_state(path: str, chat_name: str, chat_state: dict) -> None:
    with _watch_state_lock:
        state = _load_watch_state(path)
        state[chat_name] = chat_state
        try:
            Path(path).write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except Exception as exc:
            print(f"[watch] ERROR writing state {path}: {exc}", file=sys.stderr)


def _parse_teams_ts(ts: str) -> float:
    """Parse a Teams ISO-8601 timestamp string to Unix float. Returns 0.0 on failure."""
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _merge_group(group: list[dict]) -> dict:
    """Merge a burst group into one representative message. Single messages pass through."""
    if len(group) == 1:
        return dict(group[0])
    last = group[-1]
    combined = "\n".join(m.get("text", "").strip() for m in group if m.get("text", "").strip())
    all_image_urls: list[str] = []
    all_attachments: list[dict] = []
    for m in group:
        all_image_urls.extend(m.get("image_urls", []))
        all_attachments.extend(m.get("attachments", []))
    return {
        **last,
        "text": combined,
        "sequence_id": max(m["sequence_id"] for m in group),
        "image_urls": all_image_urls,
        "attachments": all_attachments,
        "_merged_count": len(group),
    }


def _group_bursts(msgs: list[dict], window_s: float = _BURST_WINDOW_S) -> list[dict]:
    """Merge consecutive same-sender messages within window_s into one combined message."""
    if not msgs:
        return []
    result: list[dict] = []
    group = [msgs[0]]
    for msg in msgs[1:]:
        prev = group[-1]
        same = msg.get("sender_mri") == prev.get("sender_mri")
        gap = _parse_teams_ts(msg.get("timestamp", "")) - _parse_teams_ts(prev.get("timestamp", ""))
        # Never merge bot messages with real user messages — they share sender_mri but would
        # corrupt the merged message's raw_html, causing it to be skipped (or worse, looped).
        # Covers: blockquote replies, [Bob] placeholder/reply messages, and bare <img> GIFs.
        def _is_bot_html(h: str) -> bool:
            s = h.lstrip()
            return (
                s.startswith("<blockquote")
                or any(s.startswith(p) for p in _BOT_HTML_PREFIXES)
                or s.startswith("<p><img")
            )
        is_bot_reply = _is_bot_html(msg.get("raw_html", ""))
        prev_is_bot_reply = _is_bot_html(prev.get("raw_html", ""))
        if same and 0 <= gap < window_s and not is_bot_reply and not prev_is_bot_reply:
            group.append(msg)
        else:
            result.append(_merge_group(group))
            group = [msg]
    result.append(_merge_group(group))
    return result


_IMG_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _images_to_tmp_files(urls: list[str]) -> list[str]:
    """Download images from url list and write each to a named temp file.

    Returns list of absolute paths (caller must unlink). Skips urls where
    api_client.download_image returns None (e.g. too small / network error).
    Empty input -> [].
    """
    if not urls:
        return []
    paths: list[str] = []
    for url in urls:
        print(f"[watch] -> downloading image: {url[:80]}...", flush=True)
        result = api_client.download_image(url)
        if not result:
            print(f"[watch] -> download_image returned None for {url[:60]}", flush=True)
            continue
        img_bytes, media_type = result
        ext = _IMG_EXT_MAP.get(media_type, ".jpg")
        tf = tempfile.NamedTemporaryFile(suffix=ext, delete=False, prefix="bob_img_")
        tf.write(img_bytes)
        tf.close()
        paths.append(tf.name)
        print(f"[watch] -> image saved: {tf.name} ({len(img_bytes)} b, {media_type})", flush=True)
    return paths


def _enrich_text_with_images(text: str, image_urls: list[str], mode: str = "stub") -> str:
    """Download images from a message and prepend their descriptions to text.

    mode="stub" (default): no network calls; returns a placeholder string so that
        history and live_context never store full image descriptions.
        1 image -> "[Image attached to the message]"; N>1 -> "[N images attached ...]".
    mode="full": existing behaviour — download + describe via Gemini.
    """
    if not image_urls:
        return text
    if mode == "stub":
        n = len(image_urls)
        if n == 1:
            placeholder = "[Image attached to the message]"
        else:
            placeholder = f"[{n} images attached to the message]"
        return f"{placeholder}\n{text}" if text else placeholder
    # mode == "full" — inline image_urls now flow via image_paths to freeform_reply;
    # fall back to stub placeholder (describe_image no longer called on this path).
    n = len(image_urls)
    if n == 1:
        placeholder = "[Image attached to the message]"
    else:
        placeholder = f"[{n} images attached to the message]"
    return f"{placeholder}\n{text}" if text else placeholder


_IMAGE_EXTS_CLI = {"png", "jpg", "jpeg", "gif", "webp"}


def _enrich_text_with_attachments(
    text: str,
    attach_list: list[dict],
    *,
    mode: str = "stub",
    conv_id: str = "",
    model: str = "",
    state_path: str = "",
    chat_name: str = "",
    image_paths_out: list[str] | None = None,
) -> str:
    """Download .md attachments and describe image attachments.

    mode="stub" (default): no SharePoint downloads; returns placeholder per attachment.
        For image files: "[Attachment: name.png — image]".
        For md files: "[Attachment: name.md]" (no size — body not fetched).
    mode="full": download + inline md; image attachments described via Claude CLI.

    For .md files (full mode): pulls each file via attachments.download and inlines the
    rendered markdown so Claude sees it as plain context inside the prompt.

    image_paths_out: when given (full mode only), each successfully downloaded image
        attachment is also written to a temp file whose path gets appended here — so
        callers can pass real screenshots along as image_paths / jira_attach candidates,
        not just a text description.

    Every successful md fetch (full mode) is audit-logged with SHA-256 + size + filename.
    """
    if not attach_list:
        return text

    # Partition attachments by type.
    def _ext(a: dict) -> str:
        return a.get("name", "").rsplit(".", 1)[-1].lower()

    image_attaches = [
        a for a in attach_list
        if (a.get("type") or "").lower() in _IMAGE_EXTS_CLI or _ext(a) in _IMAGE_EXTS_CLI
    ]
    md_attaches = [a for a in attach_list if a not in image_attaches]

    if mode == "stub":
        # Stub mode: return placeholders without any network calls.
        placeholders: list[str] = []
        for a in image_attaches:
            name = a.get("name", "image")
            stub = f"[Attachment: {name} — image]"
            if len(stub) > 200:
                stub = stub[:196] + "...]"
            placeholders.append(stub)
        for a in md_attaches:
            name = a.get("name", "file.md")
            stub = f"[Attachment: {name}]"
            if len(stub) > 200:
                stub = stub[:196] + "...]"
            placeholders.append(stub)
        if not placeholders:
            return text
        block = "\n".join(placeholders)
        return f"{block}\n\n{text}" if text else block

    # mode == "full"
    # Image attachments (SharePoint PNG/JPG/GIF/WEBP): download + describe via Claude CLI.
    if image_attaches:
        import hashlib as _hashlib  # noqa: PLC0415
        raw_images: list[tuple[bytes, str]] = []
        for a in image_attaches:
            url = a.get("url", "")
            name = a.get("name", "image")
            if not url:
                continue
            result = _attachments.download(url, name, max_bytes=_attachments._MAX_IMAGE_BYTES)
            if not result:
                print(f"[watch] -> image attach {name!r}: download failed", flush=True)
                continue
            body, ct = result
            if not ct.startswith("image/"):
                ct = "image/" + _ext(a) if _ext(a) else "image/png"
            _append_log(DEFAULT_LOG_FILE, {
                "ts": _ts(), "action": "image_attach_fetch_ok",
                "name": name, "size": len(body),
                "sha256": _hashlib.sha256(body).hexdigest(), "url": url[:200],
            })
            raw_images.append((body, ct))
            if image_paths_out is not None:
                ext = _IMG_EXT_MAP.get(ct, "." + (_ext(a) or "jpg"))
                tf = tempfile.NamedTemporaryFile(suffix=ext, delete=False, prefix="bob_attach_")
                tf.write(body)
                tf.close()
                image_paths_out.append(tf.name)
        if raw_images:
            print(f"[watch] -> describing {len(raw_images)} attached image(s)...", flush=True)
            desc = ai.describe_image(raw_images, context=text)
            if desc:
                prefix = f"[Image (attachment): {desc}]"
                text = f"{prefix}\n{text}" if text else prefix

    if not md_attaches:
        return text
    attach_list = md_attaches

    import hashlib  # noqa: PLC0415 — lazy: only paid when an attachment arrives
    files: list[tuple[bytes, str, str]] = []
    for a in attach_list:
        url = a.get("url", "")
        name = a.get("name", "file")
        if not url:
            continue
        result = _attachments.download(url, name)
        if not result:
            print(f"[watch] -> attachment {name!r}: download failed", flush=True)
            _append_log(DEFAULT_LOG_FILE, {
                "ts": _ts(), "action": "attachment_fetch_fail",
                "name": name, "url": url[:200],
            })
            continue
        body, ct = result
        digest = hashlib.sha256(body).hexdigest()
        _append_log(DEFAULT_LOG_FILE, {
            "ts": _ts(), "action": "attachment_fetch_ok",
            "name": name, "size": len(body), "content_type": ct,
            "sha256": digest, "url": url[:200],
        })
        files.append((body, ct, name))
    if not files:
        return text
    print(f"[watch] -> adding {len(files)} .md file(s) to the prompt", flush=True)
    block = ai.attachments_to_text(files, mode="full")
    if not block:
        return text
    return f"{block}\n\n{text}" if text else block


def _resolve_conv_id(chat_name: str) -> str | None:
    """Return conversation ID for a chat name. Hardcoded for known chats, discovered otherwise."""
    if chat_name in ("Notes", "Notatki"):  # the owner's notes chat (Polish name kept as an alias)
        return api_client.NOTES_CONV_ID
    return api_client.find_conversation_id(chat_name)


def _process_chat_messages(
    chat_name: str,
    conv_id: str,
    args: argparse.Namespace,
    state: dict,
    tick: int,
    caller: str | None = None,
    control_chat: bool = False,
    allowed_mcp: list[str] | None = None,
    epithets: list[str] | None = None,
    chat_config: dict | None = None,
    persona: str | None = None,
) -> None:
    """Fetch and process new messages for one chat. Updates state in-place."""
    state_path = args.watch_state
    log_path = args.log_file
    rate_limit_s = max(0, args.rate_limit)

    effective_caller = caller if caller is not None else args.caller
    # Per-channel trigger (e.g. "example" on the persona's channel, "bob" everywhere else).
    # MUST be used in ALL the gates on the owner's messages below — otherwise the owner
    # cannot call the agent on a channel with its own trigger, because the message is
    # dropped by the hard-coded \bbob\b before it reaches NORMAL MODE.
    trigger_re = _trigger_re_for(chat_name, chat_config or {})
    chat_model = _model_for(chat_name, chat_config or {}, args)
    chat_effort = _effort_for(chat_name, chat_config or {})
    last_seq_id = int(state.get("last_sequence_id", 0))
    last_composetime = state.get("last_composetime", "")
    initialised = bool(last_seq_id)

    msgs = api_client.get_messages(conv_id, n=20)
    if not msgs:
        return

    if not initialised:
        last_seq_id = msgs[-1]["sequence_id"]
        state["last_sequence_id"] = last_seq_id
        all_ts = [m.get("timestamp", "") for m in msgs if m.get("timestamp")]
        if all_ts:
            state["last_composetime"] = max(all_ts)
        state["last_activity_ts"] = _time_mod.time()
        _save_watch_state(state_path, chat_name, state)
        print(f"[watch] {chat_name!r} initialised: last_seq_id={last_seq_id}", flush=True)
        return

    new_msgs_seq = [m for m in msgs if m["sequence_id"] > last_seq_id]
    # Also catch edited messages: sequenceId unchanged but composetime updated.
    new_msgs_edit = [
        m for m in msgs
        if m.get("timestamp", "") > last_composetime
        and m["sequence_id"] <= last_seq_id
    ] if last_composetime else []
    seen_ids = {m["id"] for m in new_msgs_seq}
    new_msgs = new_msgs_seq + [m for m in new_msgs_edit if m["id"] not in seen_ids]
    if not new_msgs:
        # Still update composetime so future edits are detected relative to now.
        all_ts = [m.get("timestamp", "") for m in msgs if m.get("timestamp")]
        _changed = False
        if all_ts:
            new_ct = max(all_ts)
            if new_ct > last_composetime:
                state["last_composetime"] = new_ct
                _changed = True
        state["last_activity_ts"] = _time_mod.time()
        if _changed:
            _save_watch_state(state_path, chat_name, state)
        return

    # Burst batching: merge same-sender messages within _BURST_WINDOW_S.
    if len(new_msgs) > 1:
        _before = len(new_msgs)
        new_msgs = _group_bursts(new_msgs)
        if len(new_msgs) < _before:
            print(f"[watch] {chat_name!r}: burst-merge {_before} → {len(new_msgs)} msgs", flush=True)

    # Catch-up aggregation: if offline > _CATCHUP_THRESHOLD_S and enough pending messages,
    # reply once with an aggregated summary instead of N individual replies.
    grill_active_pre = state.get("grill_active", False)
    if not grill_active_pre:
        _offline_s = _time_mod.time() - state.get("last_activity_ts", _time_mod.time())
        if _offline_s >= _CATCHUP_THRESHOLD_S:
            _triggered: list[dict] = []
            for _m in new_msgs:
                _is_own = _m.get("sender_mri") == api_client.USER_MRI
                _txt = _m.get("text", "").strip()
                if not _txt or "8:orgid:" in _txt:
                    continue
                if _is_own and _m.get("raw_html", "").lstrip().startswith("<blockquote"):
                    continue
                _bob = bool(trigger_re.search(_txt))
                _is_q = (
                    not _is_own
                    and "schema.skype.com/Reply" in _m.get("raw_html", "")
                    and api_client.USER_MRI in _m.get("raw_html", "")
                )
                if effective_caller == "auto":
                    if not (_is_own and not _bob):
                        _triggered.append(_m)
                elif effective_caller == "owner":
                    if _is_own and _bob:
                        _triggered.append(_m)
                else:  # "all"
                    if _bob or _is_q:
                        _triggered.append(_m)

            # Skip catch-up if any triggered message has a video pipeline trigger
            if len(_triggered) >= _CATCHUP_MIN_MSGS:
                _gap_min = int(_offline_s / 60)
                print(
                    f"[watch] {chat_name!r}: catch-up after {_gap_min} min — "
                    f"{len(_triggered)} msgs", flush=True,
                )
                _lines = []
                for _m in _triggered:
                    _lbl = _owner_label(_m)
                    _enriched = _enrich_text_with_images(
                        _m.get("text", "").strip(), _m.get("image_urls", []),
                        mode="stub",
                    )
                    _enriched = _enrich_text_with_attachments(
                        _enriched, _m.get("attachments", []),
                        mode="stub",
                    )
                    _lines.append(f"{_lbl}: {_enriched}")
                _catchup_text = (
                    f"[BACK AFTER {_gap_min} MIN — below are the messages I missed]\n"
                    + "\n".join(_lines)
                    + "\n\nAnswer all of the above in one short combined message."
                )
                _since_last = _time_mod.time() - state.get("last_reply_ts", 0.0)
                if _since_last < rate_limit_s:
                    _time_mod.sleep(rate_limit_s - _since_last)
                _chat_hist = _CHAT_HISTORY.setdefault(chat_name, [])
                print(f"[watch] -> catch-up claude (model={chat_model})...", flush=True)
                _t0 = _time_mod.time()
                _reply = ai.freeform_reply(
                    _catchup_text, model=chat_model,
                    history=_chat_hist or None,
                    allowed_mcp=allowed_mcp,
                    epithets=epithets,
                    role_knowledge=_role_knowledge_for(chat_name, chat_config),
                    private_notes=_load_private_notes(chat_name),
                    schedule_context=_build_schedule_context(),
                    persona=persona,
                    extra_env={
                        "BOB_CURRENT_CHAT": chat_name,
                        "BOB_CURRENT_CONV": conv_id,  # long tools post their progress here
                        "BOB_SENDER": _triggered[-1].get("sender", ""),
                        "BOB_SENDER_MRI": _triggered[-1].get("sender_mri", ""),
                        "BOB_SENDER_TEXT": str(_triggered[-1].get("text", ""))[:4000],
                    },
                    effort=chat_effort,
                )
                print(f"[watch] -> {len(_reply)}b reply in {_time_mod.time()-_t0:.1f}s", flush=True)
                _last_m = _triggered[-1]
                _last_sender = _owner_label(_last_m)
                _reply = _extract_and_send_gifs(_reply, conv_id, persona=persona)
                _reply = _extract_and_send_files(_reply, conv_id, chat_name)
                _reply = _handle_example_learning(_reply, _last_m.get("sender_mri", ""))
                _reply = _handle_example_idea(_reply, _last_sender)
                _reply = _handle_reaction(_reply, _last_m.get("id", ""), conv_id)
                _reply, _mention_html, _mention_entries = _handle_bob_mention(_reply)
                _sent = False
                _silent_skip = _is_silent_reply(_reply)
                if _silent_skip:
                    print("[watch] -> catch-up: silent reply, not sending", flush=True)
                elif _reply:
                    _sent = api_client.reply_to_message(
                        _reply, _last_m["id"], _last_sender,
                        _last_m.get("text", ""), conv_id,
                        _last_m.get("sender_mri", ""),
                        mentions=_mention_entries or None,
                        mentions_html=_mention_html,
                        persona=persona,
                    )
                    state["last_reply_ts"] = _time_mod.time()
                if not _silent_skip:
                    print(
                        f"[watch] -> catch-up: {'sent' if _sent else 'ERROR/empty reply'}",
                        flush=True,
                    )
                _catchup_send_log: dict = {
                    "ts": _ts(), "chat_name": chat_name,
                    "action": "catchup_send" if _sent else "catchup_skip",
                    "triggered_count": len(_triggered), "gap_min": _gap_min,
                    "reply": _reply, "send_ok": _sent,
                }
                if not _sent and not _silent_skip and _reply:
                    # A send was actually attempted (and failed) here — _reply falsy
                    # or silent_skip means no api_client call happened this tick, so
                    # _last_send_error would be stale from an unrelated prior call.
                    _send_err = api_client._get_last_send_error()
                    _catchup_send_log["send_error"] = _send_err
                    _catchup_send_log["retry_attempts"] = _send_err.get("attempts", 0) if _send_err else 0
                _append_log(log_path, _catchup_send_log)
                if _sent:
                    _chat_hist.append({"sender": "[catch-up]", "text": _catchup_text})
                    _chat_hist.append({"sender": "Bob", "text": _reply})
                    state["last_bob_reply_time"] = _time_mod.time()
                    state["last_bob_reply_sender_mri"] = _last_m.get("sender_mri", "")
                state["last_sequence_id"] = max(m["sequence_id"] for m in new_msgs)
                _all_ts = [m.get("timestamp", "") for m in msgs if m.get("timestamp")]
                if _all_ts:
                    state["last_composetime"] = max(_all_ts)
                state["last_activity_ts"] = _time_mod.time()
                _save_watch_state(state_path, chat_name, state)
                return

    for msg in new_msgs:
        seq = msg["sequence_id"]

        is_owner = msg.get("sender_mri") == api_client.USER_MRI

        # Skip own bot replies — quote-replies start with <blockquote, thinking
        # placeholders start with <p>🧑‍💻<strong>[Bob]</strong>.
        # The owner's real messages are plain text / <p> without the [Bob] prefix.
        # Also skip own plain-text messages that don't mention Bob — these are proactive
        # notifications sent by _fire_due_notifications, not user interactions.
        _raw_html = msg.get("raw_html", "").lstrip()
        if is_owner and _raw_html.startswith("<blockquote"):
            last_seq_id = max(last_seq_id, seq)
            continue
        if is_owner and any(_raw_html.startswith(p) for p in _BOT_HTML_PREFIXES):
            last_seq_id = max(last_seq_id, seq)
            continue
        # Skip Bob's own SharePoint file uploads — Teams renders these as owner
        # messages containing a SharePoint link (<a href=...sharepoint...) or
        # inline image (<img src=...sharepoint...). They are not user interactions.
        # Exception: don't skip if the message mentions Bob — that's the owner
        # pasting a SharePoint recording link while triggering Bob
        # (e.g. "Bob - summarise [SP link]"). Those are real user interactions.
        if is_owner and (
            "sharepoint" in _raw_html
            and ("<a href" in _raw_html or "<img" in _raw_html)
            and not trigger_re.search(msg.get("text", ""))
        ):
            last_seq_id = max(last_seq_id, seq)
            continue
        # Skip Bob's own GIF messages — any is_owner message containing <img is a GIF Bob sent.
        # Teams strips class attributes so we can't rely on sentinels; <img is reliable.
        # Exception: if the message text mentions Bob it's the owner triggering Bob
        # (e.g. "Bob - do X 🙂") — Teams renders emoji as <img> so we must let it through.
        if is_owner and "<img" in _raw_html and not trigger_re.search(msg.get("text", "")):
            last_seq_id = max(last_seq_id, seq)
            continue

        if is_owner:
            _plain_text = msg.get("text", "")
            # Exception: allow a bare "yes" (Polish "tak") through to the control block when a
            # forward is pending — the confirmation must not require "Bob" prefix.
            _is_fwd_confirm = (
                control_chat
                and state.get("pending_forward")
                and _plain_text.strip().lower() in _FWD_CONFIRM_WORDS
            )
            if not _is_fwd_confirm and not trigger_re.search(_plain_text):
                last_seq_id = max(last_seq_id, seq)
                continue

        # === CARD ACTION / VOTE RESPONSE ===
        # Process Adaptive Card submit actions before the empty-text guard.
        # card_action is only set when get_messages() detects a vote payload.
        card_action = msg.get("card_action")
        if card_action and is_owner and control_chat and chat_config is not None:
            _ctrl_sender = msg.get("sender", OWNER_LABEL)
            _ctrl_mri = msg.get("sender_mri", "")
            vote = polls.parse_vote(card_action)
            if vote:
                ack = polls.apply_vote(vote, conv_id, chat_config, args)
                api_client.reply_to_message(
                    ack, msg["id"], _ctrl_sender,
                    msg.get("text", "vote"), conv_id, _ctrl_mri,
                )
                print(f"[watch] -> vote applied: {vote.get('action')!r}", flush=True)
            else:
                print("[watch] -> card_action without poll_id or the poll expired", flush=True)
            last_seq_id = max(last_seq_id, seq)
            continue

        text = msg.get("text", "").strip()
        if not text and not card_action:
            last_seq_id = max(last_seq_id, seq)
            continue
        if not text:
            last_seq_id = max(last_seq_id, seq)
            continue
        # Skip Teams system/meta messages whose stripped text contains raw
        # MRI identifiers — these are not real chat messages.
        if "8:orgid:" in text:
            last_seq_id = max(last_seq_id, seq)
            continue

        # === PRIVATE NOTES — "Bob, remember: ..." (Polish alias "Bob, zapamiętaj: ...") ===
        # Works in every chat. Access controlled by chat config "notes": "owner"|"all".
        # Default (missing key) = "owner" → only the owner can save notes.
        _notes_mode = (chat_config or {}).get("notes", "owner")
        _can_note = is_owner or _notes_mode == "all"
        if _can_note:
            if m := _ZAPAMIETAJ_RE.search(text):
                note_content = m.group(1).strip()
                _note_sender = msg.get("sender", "?")
                _note_mri = msg.get("sender_mri", "")
                _append_private_note(chat_name, note_content, _note_sender)
                api_client.reply_to_message(
                    "Noted.",
                    msg["id"], _note_sender, text, conv_id, _note_mri,
                )
                print(f"[watch] -> note saved for {chat_name!r} from {_note_sender!r}",
                      flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if _POKAZ_NOTATKI_RE.search(text):
                _note_sender = msg.get("sender", "?")
                _note_mri = msg.get("sender_mri", "")
                notes = _load_private_notes(chat_name)
                if notes:
                    reply_text = f"Notes for this chat:\n\n{notes[:1800]}"
                    if len(notes) > 1800:
                        reply_text += "\n\n[...shortened]"
                else:
                    reply_text = "No notes for this chat."
                api_client.reply_to_message(
                    reply_text, msg["id"], _note_sender, text, conv_id, _note_mri,
                )
                print(f"[watch] -> show notes for {chat_name!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue

        # === BUILD APPROVAL — "Bob approve <id>" / "Bob reject <id>" (Polish "zatwierdź" / "odrzuć") ===
        # Owner-only, any chat (mirrors the todo-list gate below). Resolves a
        # pending heavy-generation request queued by _handle_build_approval_gate.
        if is_owner:
            _bld_sender = msg.get("sender", OWNER_LABEL)
            _bld_mri = msg.get("sender_mri", "")
            if m := _BOB_BUILD_APPROVE_RE.search(text):
                _bld = _find_pending_build(m.group(1))
                if _bld:
                    _set_pending_build_status(m.group(1), "approved")
                    api_client.reply_to_message(
                        f"Approved #{m.group(1)} — let {_bld['sender']} "
                        f"in {_bld['chat_name']!r} know what happens next.",
                        msg["id"], _bld_sender, text, conv_id, _bld_mri,
                    )
                    try:
                        api_client.send_message(
                            "The owner approved it — I'm on it 🙂",
                            conversation_id=_bld["conv_id"],
                        )
                    except Exception as exc:
                        print(f"[watch] ERROR sending the approval notice: {exc}",
                              file=sys.stderr)
                else:
                    api_client.reply_to_message(
                        f"I found no pending request #{m.group(1)}.",
                        msg["id"], _bld_sender, text, conv_id, _bld_mri,
                    )
                print(f"[watch] -> build #{m.group(1)}: approved", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_BUILD_REJECT_RE.search(text):
                _bld = _find_pending_build(m.group(1))
                if _bld:
                    _set_pending_build_status(m.group(1), "rejected")
                    api_client.reply_to_message(
                        f"Rejected #{m.group(1)}.",
                        msg["id"], _bld_sender, text, conv_id, _bld_mri,
                    )
                    try:
                        api_client.send_message(
                            "The owner did not approve this task — I won't do it 🙏",
                            conversation_id=_bld["conv_id"],
                        )
                    except Exception as exc:
                        print(f"[watch] ERROR sending the rejection notice: {exc}",
                              file=sys.stderr)
                else:
                    api_client.reply_to_message(
                        f"I found no pending request #{m.group(1)}.",
                        msg["id"], _bld_sender, text, conv_id, _bld_mri,
                    )
                print(f"[watch] -> build #{m.group(1)}: rejected", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue

        # === TODO/REMINDER LIST + DELETE — work on any chat for the owner ===
        # On the control chat (the notes chat) we show ALL todos across every chat;
        # on a specific chat we filter to todos belonging to it. The owner-gate
        # mirrors the prior behaviour — non-owners can't browse the todo list.
        if is_owner:
            _todo_sender = msg.get("sender", OWNER_LABEL)
            _todo_mri = msg.get("sender_mri", "")
            if _NOTIF_SHOW_RE.search(text):
                api_client.reply_to_message(
                    todos_store.format_todos_for_chat(
                        current_chat=chat_name,
                        show_all=bool(control_chat),
                    ),
                    msg["id"], _todo_sender, text, conv_id, _todo_mri,
                )
                print(
                    f"[watch] -> show tasks for {chat_name!r} "
                    f"(show_all={bool(control_chat)})",
                    flush=True,
                )
                last_seq_id = max(last_seq_id, seq)
                continue
            if _NOTIF_DEL_OVERDUE_RE.search(text):
                _del_count = todos_store.delete_overdue_todos(
                    current_chat=chat_name,
                    show_all=bool(control_chat),
                )
                if _del_count:
                    _ack = f"Deleted {_del_count} overdue task{'' if _del_count == 1 else 's'}."
                else:
                    _ack = "No overdue tasks to delete."
                api_client.reply_to_message(
                    _ack, msg["id"], _todo_sender, text, conv_id, _todo_mri,
                )
                print(f"[watch] -> tasks del overdue: count={_del_count} show_all={bool(control_chat)}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _NOTIF_DEL_RE.search(text):
                _del_id = m.group(1).strip()
                _del_ok = todos_store.delete_todo_by_id(_del_id)
                _ack = "Deleted." if _del_ok else f"I found no task '{_del_id}'."
                api_client.reply_to_message(
                    _ack, msg["id"], _todo_sender, text, conv_id, _todo_mri,
                )
                print(f"[watch] -> task del: id={_del_id!r} ok={_del_ok}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue

        # === OWNER CONTROL COMMANDS (only from control_chat, before mute guard) ===
        # Acks use reply_to_message (produces <blockquote HTML) so the next poll
        # filters them via the blockquote-skip guard and they don't re-trigger commands.
        if control_chat and is_owner:
            _ctrl_sender = msg.get("sender", OWNER_LABEL)
            _ctrl_mri = msg.get("sender_mri", "")
            # --- Forward: confirmation branch (must be first to consume "yes" / "tak") ---
            if state.get("pending_forward") and text.strip().lower() in _FWD_CONFIRM_WORDS:
                _pf = state["pending_forward"]
                _fwd_text = state.get("last_forward_text") or ""
                if _fwd_text:
                    _fwd_name = _pf.get("chat_name", _pf["conv_id"])
                    _fwd_html = f'<p>🧑‍💻<strong>[Bob]</strong>: {api_client.md_to_html(_fwd_text)}</p>'
                    api_client.send_html_message(_fwd_html, _pf["conv_id"])
                    api_client.reply_to_message(
                        f"Sent to '{_fwd_name}'.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    print(f"[watch] -> forward confirmed to {_fwd_name!r}", flush=True)
                else:
                    api_client.reply_to_message(
                        "I have no reply to forward yet.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                state["pending_forward"] = None
                _save_watch_state(args.watch_state, chat_name, state)
                last_seq_id = max(last_seq_id, seq)
                continue
            # --- Forward: "Bob, forward to <target>" (Polish "Bob, wyślij na <target>") branch ---
            if m := _BOB_FORWARD_RE.search(text):
                _fwd_target = m.group(1).strip()
                _fwd_text = state.get("last_forward_text") or ""
                if not _fwd_text:
                    api_client.reply_to_message(
                        "I have no reply to forward yet.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    last_seq_id = max(last_seq_id, seq)
                    continue
                # (a) Teams link -> conv_id direct
                _link_m = _TEAMS_LINK_RE.search(_fwd_target)
                if _link_m:
                    _fwd_cid = _link_m.group(1)
                    state["pending_forward"] = {"conv_id": _fwd_cid, "chat_name": "(link)"}
                    _save_watch_state(args.watch_state, chat_name, state)
                    _preview = _fwd_text[:150].rstrip() + ("…" if len(_fwd_text) > 150 else "")
                    api_client.reply_to_message(
                        f"I'll send it to the chat (link). Text: \"{_preview}\". Reply 'yes' to send.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    print(f"[watch] -> forward pending for link {_fwd_cid!r}", flush=True)
                    last_seq_id = max(last_seq_id, seq)
                    continue
                # (b) Exact name in chat-config
                _fwd_live_cfg = _load_chat_config(_CHAT_CONFIG_PATH)
                _fwd_exact_cid = _conv_id_override_for(_fwd_target, _fwd_live_cfg)
                if _fwd_exact_cid:
                    state["pending_forward"] = {"conv_id": _fwd_exact_cid, "chat_name": _fwd_target}
                    _save_watch_state(args.watch_state, chat_name, state)
                    _preview = _fwd_text[:150].rstrip() + ("…" if len(_fwd_text) > 150 else "")
                    api_client.reply_to_message(
                        f"I'll send it to '{_fwd_target}'. Text: \"{_preview}\". Reply 'yes' to send.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    print(f"[watch] -> forward pending for {_fwd_target!r}", flush=True)
                    last_seq_id = max(last_seq_id, seq)
                    continue
                # (c) Fuzzy via conversations API
                _fwd_matches = api_client._fuzzy_find_conversations(_fwd_target)
                if len(_fwd_matches) == 1:
                    _fwd_cid, _fwd_name = _fwd_matches[0]
                    state["pending_forward"] = {"conv_id": _fwd_cid, "chat_name": _fwd_name}
                    _save_watch_state(args.watch_state, chat_name, state)
                    _preview = _fwd_text[:150].rstrip() + ("…" if len(_fwd_text) > 150 else "")
                    api_client.reply_to_message(
                        f"Do you mean '{_fwd_name}'? Text: \"{_preview}\". Reply 'yes' to send.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    print(f"[watch] -> forward pending for {_fwd_name!r}", flush=True)
                elif len(_fwd_matches) > 1:
                    _candidate_lines = "\n".join(f"- {n}" for _, n in _fwd_matches)
                    api_client.reply_to_message(
                        f"I found several chats matching '{_fwd_target}':\n"
                        f"{_candidate_lines}\n"
                        "Give me the exact name.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    print(f"[watch] -> forward ambiguous: {len(_fwd_matches)} matches", flush=True)
                else:
                    api_client.reply_to_message(
                        f"I found no chat '{_fwd_target}'.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    print(f"[watch] -> forward: no match for {_fwd_target!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            # --- end forward branches ---
            if m := _PAUSE_RE.search(text):
                amount = int(m.group(1))
                unit = m.group(2).lower()
                minutes = amount * 60 if unit.startswith(('h', 'g')) else amount
                until_dt = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=minutes)
                _save_control({**_load_control(), "paused_until": until_dt.isoformat(), "disabled": False})
                ack = (
                    f"Ok, going quiet for {minutes} min "
                    f"(until {until_dt.strftime('%H:%M')} UTC)."
                )
                api_client.reply_to_message(ack, msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri)
                print(f"[watch] -> Bob pause {minutes}min (until {until_dt.isoformat()})", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            _apr = None
            if m := _APR_DECIDE_RE.search(text):
                _apr = (m.group(1), m.group(2).lower(), m.group(3) or "")
            elif mb := _APR_DECIDE_BARE_RE.search(text):
                from bob_panel import approvals as _approvals  # noqa: PLC0415
                _waiting = _approvals.listing()["pending"]
                # exactly one request waits -> "Bob approve" is unambiguous
                if len(_waiting) == 1 and str(_waiting[0].get("id", "")).startswith("apr-"):
                    _apr = (mb.group(1), _waiting[0]["id"], mb.group(2) or "")
            if _apr:
                from bob_panel import approvals as _approvals  # noqa: PLC0415
                _verb, _apr_id, _note = _apr
                _approve = _verb.lower().startswith(("approve", "zatw", "akcept"))
                try:
                    _res = _approvals.decide(_apr_id, _approve, _note.strip(), by=f"{_approvals.OWNER} (chat)")
                    _ack = _res.get("message") or ("Saved." if _res.get("ok") else "Nothing to approve.")
                    if _res.get("ok"):
                        _ack = f"{'✅ Approved' if _approve else '❌ Rejected'}: {_apr_id}. {_ack}"
                except Exception as exc:  # noqa: BLE001
                    _ack = f"Could not save the decision {_apr_id}: {exc}"
                api_client.reply_to_message(_ack, msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri)
                print(f"[watch] -> approval {_apr_id} {'approved' if _approve else 'rejected'}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _CHAT_DISABLE_RE.search(text):
                target = m.group(1).strip()
                disable_chat(target)
                api_client.reply_to_message(
                    f"Chat '{target}' disabled. I will ignore its messages.",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(f"[watch] -> chat disabled: {target!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _CHAT_ENABLE_RE.search(text):
                target = m.group(1).strip()
                enable_chat(target)
                api_client.reply_to_message(
                    f"Chat '{target}' enabled again.",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(f"[watch] -> chat enabled: {target!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if _CHAT_STATUS_RE.search(text):
                _live_cfg = _load_chat_config(_CHAT_CONFIG_PATH)
                _disabled = set(_load_control().get("disabled_chats", []))
                _lines = [f"Model: {args.model} | Global caller: {args.caller}\n"]
                for _n, _cv in _live_cfg.items():
                    _en = "✅" if _n not in _disabled else "❌"
                    _cal = _cv.get("caller") or "(global)"
                    _eff = _mcp_for(_n, _live_cfg)
                    _mcp = (f"roles {'+'.join(_cv['roles'])} -> " if _cv.get("roles") else "") + (
                        ", ".join(_eff) if _eff is not None else "all")
                    _kl = _cv.get("knowledge")
                    _know = ", ".join(_kl) if isinstance(_kl, list) and _kl else (
                        "none" if isinstance(_kl, list) else "all"
                    )
                    _ep = ", ".join(_cv["epithets"]) if _cv.get("epithets") else "-"
                    _lines.append(
                        f"{_en} {_n}\n"
                        f"   caller:{_cal} mcp:{_mcp}\n"
                        f"   know:{_know} ep:{_ep}"
                    )
                api_client.reply_to_message(
                    "\n".join(_lines),
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_CONFIG_RE.search(text):
                _tgt = m.group(1).strip()
                _live_cfg = _load_chat_config(_CHAT_CONFIG_PATH)
                _cv = _live_cfg.get(_tgt)
                if _cv is None:
                    _partial = [k for k in _live_cfg if _tgt.lower() in k.lower()]
                    if len(_partial) == 1:
                        _tgt, _cv = _partial[0], _live_cfg[_partial[0]]
                    else:
                        _known = ", ".join(_live_cfg.keys())
                        api_client.reply_to_message(
                            f"I don't know the chat '{_tgt}'. Known: {_known}.",
                            msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                        )
                        last_seq_id = max(last_seq_id, seq)
                        continue
                _dis = _tgt in _load_control().get("disabled_chats", [])
                _cal = _cv.get("caller") or f"(global: {args.caller})"
                _eff_v = _mcp_for(_tgt, _live_cfg)
                _mcp_v = (f"roles {'+'.join(_cv['roles'])} -> " if _cv.get("roles") else "") + (
                    ", ".join(_eff_v) if _eff_v is not None else "all defaults")
                _kl_v = _cv.get("knowledge")
                _know_v = ", ".join(_kl_v) if isinstance(_kl_v, list) and _kl_v else (
                    "none" if isinstance(_kl_v, list) else "all"
                )
                _ep_v = ", ".join(_cv["epithets"]) if _cv.get("epithets") else "-"
                _ctrl_chat = "yes" if _cv.get("control_chat") else "no"
                _cid = _cv.get("conv_id", "?")
                api_client.reply_to_message(
                    f"📋 {_tgt}\n"
                    f"Status: {'❌ disabled' if _dis else '✅ active'}\n"
                    f"Caller: {_cal}\n"
                    f"MCP: {_mcp_v}\n"
                    f"Knowledge: {_know_v}\n"
                    f"Epithets: {_ep_v}\n"
                    f"Control chat: {_ctrl_chat}\n"
                    f"Conv ID: {_cid}",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(f"[watch] -> Bob config {_tgt!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _HARMONOGRAM_CLEAR_RE.search(text):
                _date_key = m.group(1).strip()
                _ok, _dates = owner_schedule.clear_date(_date_key)
                if _ok:
                    _removed = ", ".join(owner_schedule._fmt_date(d) for d in _dates)
                    api_client.reply_to_message(
                        f"Deleted: {_removed}.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                else:
                    api_client.reply_to_message(
                        f"I don't recognise the date: '{_date_key}'. Use e.g.: today, tomorrow, 28.05, 28.05.2026",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                print(f"[watch] -> schedule clear: {_date_key!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if _POKAZ_HARMONOGRAM_RE.search(text):
                api_client.reply_to_message(
                    owner_schedule.format_full_schedule(),
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print("[watch] -> schedule show", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _HARMONOGRAM_SET_RE.search(text):
                _date_key = m.group(1).strip()
                _date_text = m.group(2).strip()
                _ok, _dates = owner_schedule.set_date(_date_key, _date_text)
                if _ok:
                    if len(_dates) == 1:
                        _label = owner_schedule._fmt_date(_dates[0])
                    else:
                        _d0 = owner_schedule._fmt_date(_dates[0])
                        _d1 = owner_schedule._fmt_date(_dates[-1])
                        _label = f"{_d0} – {_d1} ({len(_dates)} days)"
                    api_client.reply_to_message(
                        f"Schedule: {_label} — {_date_text}",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                else:
                    api_client.reply_to_message(
                        f"I don't recognise the date: '{_date_key}'. "
                        "Use e.g.: today, tomorrow, 28.05, 28.05.2026, 28.05-30.05",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                print(f"[watch] -> schedule set: {_date_key!r} = {_date_text!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if _BROADCAST_STATUS_RE.search(text):
                _CET = ZoneInfo(_TZ_NAME)
                _today = datetime.now(_CET).date()
                _is_planning = _is_broadcast_planning_day(_today)
                _is_workday = _today.weekday() < 5
                _bc_sched = _load_broadcast_schedule()
                _lines = [
                    f"Broadcast scheduler — today: {_today.isoformat()}",
                    f"Workday: {'YES' if _is_workday else 'no'}"
                    f" | Planning day: {'YES' if _is_planning else 'no'}",
                    "",
                ]
                if _bc_sched:
                    try:
                        import json as _json_mod
                        _jobs_file = _BROADCAST_RUNNER_SCRIPT.parent / "jobs.json"
                        _jobs_raw = (
                            _json_mod.loads(_jobs_file.read_text(encoding="utf-8"))
                            if _jobs_file.exists() else []
                        )
                        _jobs_by_id = {
                            j["id"]: j for j in _jobs_raw if "id" in j
                        }
                    except Exception:
                        _jobs_by_id = {}
                    for _jid, _times in _bc_sched.items():
                        _job_def = _jobs_by_id.get(_jid, {})
                        _display = _job_def.get("name") or _job_def.get("team") or _jid
                        _stype = _job_def.get("schedule_type", "planning")
                        _stype_label = {"daily": "daily", "weekly": "weekly",
                                        "always": "daily (weekends too)"}.get(_stype, "planning")
                        _parts = []
                        for _phase_key, _phase_arg, _phase_label in [
                            ("generate",      "generate",      "generate"),
                            ("dry_run",       "dry-run",       "dry-run"),
                            ("send",          "send",          "send"),
                            ("generate_send", "generate-send", "generate+send"),
                        ]:
                            _t = _times.get(_phase_key, "-")
                            if _t == "-":
                                continue
                            _fired = _fired_broadcasts.get(f"{_jid}:{_phase_arg}:{_today.isoformat()}")
                            _parts.append(f"{_phase_label} {_t} {'✅' if _fired else ''}")
                        _lines.append(f"{_display} ({_stype_label}):\n  " + " | ".join(_parts))
                else:
                    _lines.append("(no schedule)")
                api_client.reply_to_message(
                    "\n".join(_lines),
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print("[watch] -> broadcast status", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BROADCAST_SET_RE.search(text):
                _bc_team = m.group(1).strip()
                _bc_phase_word = m.group(2).strip().lower()
                _bc_time_str = m.group(3).strip()
                _bc_parsed_time = _parse_reminder_time(_bc_time_str)
                if _bc_parsed_time is None:
                    api_client.reply_to_message(
                        f"Invalid time: '{_bc_time_str}'. Use the HH:MM format.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    last_seq_id = max(last_seq_id, seq)
                    continue
                # Map phase word to schedule key.
                _bc_phase_map = {
                    "send": "send",
                    "generate": "generate",
                    "wyslij": "send",
                    "generuj": "generate",
                    "dry-run": "dry_run",
                }
                _bc_sched_key = _bc_phase_map.get(_bc_phase_word, "send")
                # Find job_id by team name.
                _bc_job_id = None
                try:
                    import json as _json_mod
                    _jobs_file = _BROADCAST_RUNNER_SCRIPT.parent / "jobs.json"
                    if _jobs_file.exists():
                        _jobs_list = _json_mod.loads(_jobs_file.read_text(encoding="utf-8"))
                        for _j in _jobs_list:
                            if _j.get("team", "").lower() == _bc_team.lower():
                                _bc_job_id = _j["id"]
                                break
                except Exception:
                    pass
                if _bc_job_id is None:
                    api_client.reply_to_message(
                        f"I don't know the team '{_bc_team}'. Check the names in jobs.json.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    last_seq_id = max(last_seq_id, seq)
                    continue
                _bc_sched = _load_broadcast_schedule()
                if _bc_job_id not in _bc_sched:
                    _bc_sched[_bc_job_id] = {}
                _bc_sched[_bc_job_id][_bc_sched_key] = _bc_time_str
                _save_broadcast_schedule(_bc_sched)
                api_client.reply_to_message(
                    f"Broadcast {_bc_team} ({_bc_job_id}): {_bc_sched_key} -> {_bc_time_str}",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(
                    f"[watch] -> broadcast set: job={_bc_job_id} "
                    f"key={_bc_sched_key} time={_bc_time_str}",
                    flush=True,
                )
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _NOTIF_SET_RE.search(text):
                _date_tok = m.group(1).strip()
                _time_tok = m.group(2).strip()
                _notif_chat = m.group(3).strip()
                _notif_msg = m.group(4).strip()
                _dt = _parse_reminder_datetime(_date_tok, _time_tok)
                if _dt is None:
                    api_client.reply_to_message(
                        f"I don't recognise the date/time: '{_date_tok} {_time_tok}'. "
                        "Use e.g.: today 15:00 / tomorrow 9:30 / 28.05 14:00",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    last_seq_id = max(last_seq_id, seq)
                    continue
                _notif_id = todos_store.add_reminder_todo(
                    title=_notif_msg,
                    notify_at_iso=_dt.isoformat(timespec='seconds'),
                    notify_chat=_notif_chat,
                    source_chat=chat_name,
                    sender=_ctrl_sender,
                    sender_mri=_ctrl_mri,
                )
                _preview = _notif_msg[:60] + ("..." if len(_notif_msg) > 60 else "")
                api_client.reply_to_message(
                    f"Ok, I'll remind {_dt.strftime('%d.%m %H:%M')} in '{_notif_chat}': "
                    f"{_preview} (id: {_notif_id})",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(
                    f"[watch] -> notif set: id={_notif_id} chat={_notif_chat!r} "
                    f"at={_dt.isoformat(timespec='seconds')}",
                    flush=True,
                )
                last_seq_id = max(last_seq_id, seq)
                continue
            if _BOB_HELP_RE.search(text):
                help_text = (
                    "Available commands:\n"
                    "\n"
                    "⏸ Bob pause 30m / 2h — pause\n"
                    "🔴 Bob off / 🟢 Bob on — disable/enable\n"
                    "🤖 Bob model haiku|sonnet|opus — switch model\n"
                    "👥 Bob caller owner|all|auto — switch trigger mode\n"
                    "\n"
                    "💬 Bob disable chat [name] / Bob enable chat [name]\n"
                    "📋 Bob chats — full status of every chat\n"
                    "📋 Bob config [name] — details of one chat\n"
                    "\n"
                    "⚙️ Bob chat [name] mcp jira,todos|all|none\n"
                    "⚙️ Bob chat [name] knowledge <area>|all|none\n"
                    "⚙️ Bob chat [name] caller owner|all|auto|clear\n"
                    "⚙️ Bob chat [name] epithets concise,formal|none\n"
                    "\n"
                    "📅 Bob, schedule today|tomorrow|28.05: [text]\n"
                    "📅 Bob, schedule 28.05-30.05: [text] — date range\n"
                    "📅 Bob, show schedule\n"
                    "📅 Bob, schedule delete 28.05\n"
                    "\n"
                    "🔔 Bob, remind today|tomorrow|28.05 HH:MM in [chat name]: [text]\n"
                    "🔔 Bob, show reminders — in the notes chat shows every task,\n"
                    "                        in other chats only this chat's\n"
                    "🔔 Bob, delete reminder [id] — deletes the whole task\n"
                    "🔔 Bob, delete overdue — deletes every overdue task (this chat;\n"
                    "                         in the notes chat: every chat)\n"
                    "\n"
                    "🔥 grill me / 🛑 stop grill\n"
                    "📝 Bob, remember: [text]\n"
                    "📋 Bob, show notes\n"
                    "📤 Bob, forward to [chat/link] — send the last reply to another chat\n"
                    "❓ Bob ? — this message\n"
                    "\n"
                    "The Polish forms of these commands still work."
                )
                api_client.reply_to_message(
                    help_text, msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_MODEL_RE.search(text):
                _raw_model = m.group(1).strip().lower()
                _new_model = ai.MODEL_ALIASES.get(_raw_model, _raw_model)
                args.model = _new_model
                api_client.reply_to_message(
                    f"Model changed to: {_new_model}.",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(f"[watch] -> Bob model: {_new_model}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_CALLER_GLOBAL_RE.search(text):
                _new_caller = m.group(1).strip().lower()
                args.caller = _new_caller
                _label = {
                    "owner": f"only {OWNER_LABEL} (via 'Bob')",
                    "all": "everyone (via 'Bob')",
                    "auto": "auto — every message",
                }.get(_new_caller, _new_caller)
                api_client.reply_to_message(
                    f"Global caller changed to: {_new_caller} ({_label}).",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(f"[watch] -> Bob caller global: {_new_caller}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_POLL_RE.search(text):
                topic = m.group(1).strip()
                _cfg = chat_config if chat_config is not None else {}
                try:
                    card, poll_id = polls.build_poll_card(topic, _cfg)
                    msg_id = api_client.post_card(card, conv_id)
                    if msg_id:
                        ack = f"Poll card sent (poll_id={poll_id})."
                    else:
                        _err = api_client._get_last_send_error()
                        _err_detail = f" ({_err['err_type']} status={_err['status']})" if _err else ""
                        ack = f"Could not send the poll card for topic={topic!r}{_err_detail}."
                except ValueError as exc:
                    ack = (
                        f"Unknown poll topic: {topic!r}. "
                        f"Available: model, pause, onoff, "
                        f"chat <name> onoff|mcp|knowledge|caller|epithets. "
                        f"Error: {exc}"
                    )
                api_client.reply_to_message(ack, msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri)
                print(f"[watch] -> Bob poll topic={topic!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_CHAT_MCP_RE.search(text):
                _tgt_chat = m.group(1).strip()
                _raw_val = m.group(2).strip()
                _cfg = chat_config if chat_config is not None else {}
                if _raw_val.lower() == "all":
                    _mcp_val = None
                elif _raw_val.lower() == "none":
                    _mcp_val = []
                else:
                    _mcp_val = [v.strip() for v in _raw_val.split(",") if v.strip()]
                _apply_chat_config_change(_tgt_chat, "mcp", _mcp_val, _cfg)
                _ack_val = "all" if _mcp_val is None else (", ".join(_mcp_val) or "none")
                api_client.reply_to_message(
                    f"MCP for '{_tgt_chat}' set to: {_ack_val}.",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(f"[watch] -> chat mcp fallback: {_tgt_chat!r} = {_mcp_val!r}", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_CHAT_CALLER_RE.search(text):
                _tgt_chat = m.group(1).strip()
                _raw_val = m.group(2).strip().lower()
                _cfg = chat_config if chat_config is not None else {}
                _caller_val = None if _raw_val == "clear" else _raw_val
                _apply_chat_config_change(_tgt_chat, "caller", _caller_val, _cfg)
                _ack_val = "removed (falls back to the global one)" if _caller_val is None else _caller_val
                api_client.reply_to_message(
                    f"Caller for '{_tgt_chat}' set to: {_ack_val}.",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(f"[watch] -> chat caller fallback: {_tgt_chat!r} = {_caller_val!r}",
                      flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_CHAT_EPITHETS_RE.search(text):
                _tgt_chat = m.group(1).strip()
                _raw_val = m.group(2).strip()
                _cfg = chat_config if chat_config is not None else {}
                if _raw_val.lower() == "none":
                    _ep_val = None
                else:
                    _ep_val = [v.strip() for v in _raw_val.split(",") if v.strip()] or None
                _apply_chat_config_change(_tgt_chat, "epithets", _ep_val, _cfg)
                _ack_val = "cleared (default prompt)" if _ep_val is None \
                    else ", ".join(_ep_val)
                api_client.reply_to_message(
                    f"Epithets for '{_tgt_chat}' set to: {_ack_val}.",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(f"[watch] -> chat epithets fallback: {_tgt_chat!r} = {_ep_val!r}",
                      flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if m := _BOB_CHAT_INTERNET_RE.search(text):
                _tgt_chat = m.group(1).strip()
                _raw_val = m.group(2).strip().lower()
                _cfg = chat_config if chat_config is not None else {}
                if _raw_val == "off":
                    _inet_val: str | None = None
                elif _raw_val in ("all", "only_me"):
                    _inet_val = _raw_val
                else:
                    api_client.reply_to_message(
                        f"Invalid internet value: {_raw_val!r}."
                        " Allowed: all, only_me, off.",
                        msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                    )
                    last_seq_id = max(last_seq_id, seq)
                    continue
                _apply_chat_config_change(_tgt_chat, "internet", _inet_val, _cfg)
                _ack_val = "off (no access)" if _inet_val is None else _inet_val
                api_client.reply_to_message(
                    f"Internet for '{_tgt_chat}' set to: {_ack_val}.",
                    msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print(
                    f"[watch] -> chat internet fallback: {_tgt_chat!r} = {_inet_val!r}",
                    flush=True,
                )
                last_seq_id = max(last_seq_id, seq)
                continue
            if _BOB_OFF_RE.search(text):
                _save_control({**_load_control(), "disabled": True, "paused_until": None})
                api_client.reply_to_message(
                    "Switching off.", msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print("[watch] -> Bob disabled", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue
            if _BOB_ON_RE.search(text):
                _save_control({**_load_control(), "disabled": False, "paused_until": None})
                api_client.reply_to_message(
                    "I'm back :)", msg["id"], _ctrl_sender, text, conv_id, _ctrl_mri,
                )
                print("[watch] -> Bob enabled", flush=True)
                last_seq_id = max(last_seq_id, seq)
                continue

        # === GLOBAL MUTE GUARD ===
        paused, pause_reason = _is_paused()
        if paused:
            print(f"[watch] skip seq={seq} [{chat_name}]: Bob {pause_reason}", flush=True)
            last_seq_id = max(last_seq_id, seq)
            continue

        grill_active = state.get("grill_active", False)

        # === GRILL MODE — responds to all non-owner messages, no "Bob" trigger needed ===
        if grill_active:
            if is_owner:
                last_seq_id = max(last_seq_id, seq)
                continue
            sender = msg.get("sender", "?")
            print(f"\n[watch] tick={tick} seq={seq} [{chat_name} | {sender} [grill]]: "
                  f"{text[:120]!r}{'...' if len(text) > 120 else ''}", flush=True)

            since_last = _time_mod.time() - state.get("last_reply_ts", 0.0)
            if since_last < rate_limit_s:
                _time_mod.sleep(rate_limit_s - since_last)

            _grill_env = {
                "BOB_CURRENT_CHAT": chat_name,
                "BOB_CURRENT_CONV": conv_id,  # long tools post their progress here
                "BOB_SENDER": msg.get("sender", ""),
                "BOB_SENDER_MRI": msg.get("sender_mri", ""),
                "BOB_SENDER_TEXT": text[:4000],
            }
            if _GRILL_STOP_RE.search(text):
                state["grill_active"] = False
                state["grill_start_seq"] = 0
                print("[watch] -> grill stop — deactivating grill mode", flush=True)
                reply = ai.freeform_reply(text, model=chat_model,
                                          allowed_mcp=allowed_mcp, epithets=epithets,
                                          role_knowledge=_role_knowledge_for(chat_name, chat_config),
                                          private_notes=_load_private_notes(chat_name),
                                          schedule_context=_build_schedule_context(),
                                          persona=persona,
                                          extra_env=_grill_env,
                                          effort=chat_effort)
            else:
                print(f"[watch] -> grill_reply (model={chat_model})...", flush=True)
                t0 = _time_mod.time()
                all_msgs = api_client.get_messages(conv_id, n=100)
                grill_start_seq = state.get("grill_start_seq", 0)
                grill_conv = [
                    {
                        "sender": _owner_label(m),
                        "text": m.get("text", ""),
                    }
                    for m in all_msgs
                    if m["sequence_id"] >= grill_start_seq and m.get("text", "").strip()
                ]
                reply = ai.grill_reply(
                    grill_conv, model=chat_model,
                    allowed_mcp=allowed_mcp, epithets=epithets,
                    extra_env=_grill_env,
                    effort=chat_effort or ai._GRILL_EFFORT,
                )
                elapsed = _time_mod.time() - t0
                print(f"[watch] -> {len(reply)}b reply in {elapsed:.1f}s", flush=True)
                if reply and any(_mk in reply for _mk in _GRILL_END_MARKERS):
                    state["grill_active"] = False
                    state["grill_start_seq"] = 0
                    print("[watch] -> grill finished (marker in the reply)", flush=True)

            reply = _extract_and_send_gifs(reply, conv_id, persona=persona)
            reply = _extract_and_send_files(reply, conv_id, chat_name)

            if not reply:
                print("[watch] -> empty grill reply, skipping", flush=True)
                _append_log(log_path, {
                    "ts": _ts(), "chat_name": chat_name,
                    "incoming_text": text, "reply": "", "action": "grill_skip_empty",
                })
                last_seq_id = max(last_seq_id, seq)
                continue

            sent = api_client.reply_to_message(
                reply, msg["id"], sender, text, conv_id, msg.get("sender_mri", ""),
                persona=persona,
            )
            state["last_reply_ts"] = _time_mod.time()
            print(f"[watch] -> {'sent (grill)' if sent else 'ERROR sending (grill)'}",
                  flush=True)
            _grill_send_log: dict = {
                "ts": _ts(), "chat_name": chat_name, "incoming_text": text,
                "sender": sender, "msg_id": msg["id"], "reply": reply,
                "action": "grill_send", "send_ok": sent, "grill": True,
            }
            if not sent:
                _send_err = api_client._get_last_send_error()
                _grill_send_log["send_error"] = _send_err
                _grill_send_log["retry_attempts"] = _send_err.get("attempts", 0) if _send_err else 0
            _append_log(log_path, _grill_send_log)
            last_seq_id = max(last_seq_id, seq)
            continue  # skip normal trigger processing

        # === NORMAL MODE ===
        # auto:  respond to every non-owner message (echo prevention: skip own)
        # owner: respond only when the owner mentions "Bob" (or quotes Bob)
        # all:   respond when anyone mentions "Bob" (or quotes Bob)
        raw_html = msg.get("raw_html", "")
        # For owner messages, check "Bob" only in non-quoted text — prevents the label
        # "[Bob]" inside a blockquote from echo-triggering Bob when the owner quotes a reply.
        if is_owner and "<blockquote" in raw_html:
            _check_text = re.sub(
                r'<blockquote[^>]*>.*?</blockquote>', '', raw_html,
                flags=re.DOTALL | re.IGNORECASE,
            )
        else:
            _check_text = text
        bob_match = trigger_re.search(_check_text)
        # Require the Reply schema to distinguish genuine quote-replies to Bob
        # from forwarded messages that also contain a blockquote + owner MRI.
        # Also require an agent label in the quoted content so quoting a plain owner message
        # (same MRI) doesn't falsely trigger Bob.
        is_quote_of_bob = (
            not is_owner
            and "schema.skype.com/Reply" in raw_html
            and api_client.USER_MRI in raw_html
            and any(_lbl in raw_html for _lbl in api_client.AGENT_TEXT_LABELS)
        )
        # Continuation: same sender who last triggered Bob, within the recency window.
        _cont_elapsed = _time_mod.time() - state.get("last_bob_reply_time", 0.0)
        is_continuation = (
            bool(state.get("last_bob_reply_sender_mri"))
            and state.get("last_bob_reply_sender_mri") == msg.get("sender_mri")
            and _cont_elapsed < _CONTINUATION_WINDOW_S
        )

        if effective_caller == "auto":
            if is_owner and not bob_match:  # skip own messages unless explicitly calling Bob
                last_seq_id = max(last_seq_id, seq)
                continue
        elif effective_caller == "owner":
            # Pure owner: only the owner can trigger Bob. is_continuation is safe
            # because last_bob_reply_sender_mri is always the owner's MRI in this mode.
            triggered = (is_owner and bob_match) or (is_owner and is_continuation)
            if not triggered:
                last_seq_id = max(last_seq_id, seq)
                continue
        else:  # "all"
            # Owner must always call Bob explicitly — skip continuation for owner so that
            # the owner's messages to other people don't re-trigger Bob within the window.
            if is_owner:
                triggered = bob_match
            else:
                triggered = bob_match or is_quote_of_bob or is_continuation
            if not triggered:
                last_seq_id = max(last_seq_id, seq)
                continue

        # Rate-limit guard.
        since_last = _time_mod.time() - state.get("last_reply_ts", 0.0)
        if since_last < rate_limit_s:
            wait = rate_limit_s - since_last
            print(f"[watch] tick={tick} rate-limit: waiting {wait:.1f}s", flush=True)
            _time_mod.sleep(wait)

        sender = OWNER_LABEL if is_owner else msg.get("sender", "?")
        if effective_caller == "auto":
            trigger = "Bob" if is_owner else "auto"
        elif bob_match:
            trigger = "Bob"
        elif is_quote_of_bob:
            trigger = "quote"
        else:
            trigger = f"cont({int(_cont_elapsed)}s)"
        log_label = f"{chat_name} | {sender} [{trigger}]"
        print(f"\n[watch] tick={tick} seq={seq} [{log_label}]: {text[:120]!r}"
              f"{'...' if len(text) > 120 else ''}", flush=True)

        # --- Conversation history ---
        chat_history = _CHAT_HISTORY.setdefault(chat_name, [])
        history_chars = sum(len(m.get("text", "")) for m in chat_history)
        print(f"[watch] -> history: {len(chat_history)} entries {history_chars}ch", flush=True)
        if history_chars > _HISTORY_COMPACT_CHARS and len(chat_history) >= _HISTORY_KEEP_LAST * 2 + 1:
            keep_n = _HISTORY_KEEP_LAST * 2
            old_entries, keep_entries = chat_history[:-keep_n], chat_history[-keep_n:]
            print(f"[watch] -> compacting history ({len(old_entries)} entries, {history_chars} chars)",
                  flush=True)
            summary = ai.compact_history(old_entries)
            if summary:
                _CHAT_HISTORY[chat_name] = [{"sender": "SUMMARY", "kind": "summary", "text": summary}] + keep_entries
                chat_history = _CHAT_HISTORY[chat_name]
                _rewrite_history_on_disk(chat_name, chat_history)

        # --- Attachment context guard ---
        # Determine full vs stub mode for the current message.
        # Full mode: user is explicitly asking about file/image content, OR
        #   the same sender recently attached a file (lookback in live context).
        # Stub mode (default): no SharePoint fetches, no Gemini image descriptions.
        # History and live_context are ALWAYS written in stub mode regardless.
        raw_text = text  # text is already msg.get("text","").strip() from above

        _full_mode = bool(_READ_FILE_TRIGGER_RE.search(raw_text))
        # If the current message already carries image content (inline img tags or
        # image-type SharePoint attachments), always go to full mode — no lookback
        # needed. Images in the message itself should always be described.
        if not _full_mode:
            _msg_has_images = bool(msg.get("image_urls")) or any(
                (a.get("type") or "").lower() in _IMAGE_EXTS_CLI
                or a.get("name", "").rsplit(".", 1)[-1].lower() in _IMAGE_EXTS_CLI
                for a in (msg.get("attachments") or [])
            )
            if _msg_has_images:
                _full_mode = True

        # Fetch recent messages once — reused for both lookback and live_context.
        _raw_recent = api_client.get_messages(conv_id, n=5)
        if not _full_mode:
            # Any-sender lookback: if any recent message in the window has an image
            # or file, enable full mode so the current reply can reference it.
            for _rm in _raw_recent:
                if _rm.get("attachments") or _rm.get("image_urls"):
                    _full_mode = True
                    break

        _enrich_mode = "full" if _full_mode else "stub"

        # Build prompt_text (goes to Claude — full content when triggered).
        # Inline image_urls are now passed natively via image_paths; stub placeholder
        # is sufficient here. Attachments still use _enrich_mode (out of scope).
        # Check for grill start — activate before calling Claude.
        # image_paths_out captures real screenshot attachments (SharePoint) as temp
        # files, so they flow into _image_paths below just like pasted inline images —
        # otherwise only their text description would reach Claude, with no file to
        # actually attach to a Jira ticket via jira_attach.
        _attach_image_paths: list[str] = []
        text = _enrich_text_with_images(raw_text, msg.get("image_urls", []), mode="stub")
        text = _enrich_text_with_attachments(
            text, msg.get("attachments", []),
            mode=_enrich_mode,
            conv_id=conv_id, model=chat_model,
            state_path=state_path, chat_name=chat_name,
            image_paths_out=_attach_image_paths,
        )
        if not text:
            last_seq_id = max(last_seq_id, seq)
            continue

        # Build stub_text — always stub — for history and live_context.
        stub_text = _enrich_text_with_images(raw_text, msg.get("image_urls", []), mode="stub")
        stub_text = _enrich_text_with_attachments(
            stub_text, msg.get("attachments", []),
            mode="stub",
        )

        # Fetch last 5 raw channel messages as live context.
        # Inline image_urls use stub mode — pixels flow via image_paths to the main call.
        # Attachments (SharePoint) still use full mode when _full_mode is active.
        _current_attach_urls = {a.get("url") for a in (msg.get("attachments") or [])}
        _live_ctx: list[dict] = []
        for _rm in _raw_recent:
            # The freshest entry is usually the message being answered: it already goes in as
            # the user turn, so listing it here too made the model think it was sent twice.
            # Its attachments are captured above.
            if msg.get("id") and _rm.get("id") == msg.get("id"):
                continue
            _rm_has_img = bool(_rm.get("image_urls")) or any(
                (a.get("type") or "").lower() in _IMAGE_EXTS_CLI
                or a.get("name", "").rsplit(".", 1)[-1].lower() in _IMAGE_EXTS_CLI
                for a in (_rm.get("attachments") or [])
            )
            _rm_attach_mode = "full" if (_full_mode and _rm_has_img) else "stub"
            _rm_text = _enrich_text_with_images(
                _rm.get("text", "").strip(), _rm.get("image_urls", []),
                mode="stub",
            )
            # image_paths_out here lets screenshots from an earlier message in the
            # lookback window still reach jira_attach candidates even when the
            # message that actually asks to create the ticket is text-only.
            # _current_attach_urls excludes the current message's own attachments
            # (already captured above) so they aren't downloaded/appended twice
            # when _raw_recent's freshest entry is the current message itself.
            _rm_attachments = [
                a for a in (_rm.get("attachments") or [])
                if a.get("url") not in _current_attach_urls
            ]
            _rm_text = _enrich_text_with_attachments(_rm_text, _rm_attachments,
                                                     mode=_rm_attach_mode,
                                                     image_paths_out=_attach_image_paths)
            if _rm_text:
                _live_ctx.append({"sender": _owner_label(_rm), "text": _rm_text})

        # Collect image urls from the current message and from live_ctx source messages
        # (deduplicated). Materialise to temp files for native vision in freeform_reply.
        _img_urls_seen: list[str] = []
        for _iu in msg.get("image_urls", []):
            if _iu not in _img_urls_seen:
                _img_urls_seen.append(_iu)
        if _full_mode:
            for _rm in _raw_recent:
                for _iu in _rm.get("image_urls", []):
                    if _iu not in _img_urls_seen:
                        _img_urls_seen.append(_iu)
        _image_paths = _images_to_tmp_files(_img_urls_seen) if _img_urls_seen else []
        _image_paths.extend(_attach_image_paths)
        # Bob's own earlier file the message points at (link, name or a quoted Bob message) -> its content
        _files_note = ""
        try:
            import bob_files as _bf  # noqa: PLC0415

            def _quoted_html(qids: list[str], _conv=conv_id) -> dict:
                # the quoted messages as the server has them (one page): the sender proves a message is Bob's own
                wanted = {str(q) for q in qids}
                return {str(m.get("id")): m for m in api_client.get_messages(_conv, n=200)
                        if str(m.get("id")) in wanted}

            _files_note = _bf.note_for(raw_text, msg.get("raw_html", ""), chat_name, fetch_quoted=_quoted_html)
        except Exception as exc:  # noqa: BLE001 -- the reply goes on without it
            print(f"[watch] bob_files: {exc}", file=sys.stderr, flush=True)

        _extra_env = {
            "BOB_CURRENT_CHAT": chat_name,
            "BOB_CURRENT_CONV": conv_id,  # long tools post their progress here
            "BOB_SENDER": msg.get("sender", ""),
            "BOB_SENDER_MRI": msg.get("sender_mri", ""),
            # the requester's own words: write tools check their arguments against it, so text
            # read through tool results cannot trigger a write
            "BOB_SENDER_TEXT": raw_text[:4000],
        }
        _allow_web = _internet_allows(_internet_for(chat_name, chat_config or {}), is_owner)
        _sw = None                       # the "still working" note of the plain reply path, if any
        if _GRILL_START_RE.search(raw_text):
            state["grill_active"] = True
            state["grill_start_seq"] = seq
            print("[watch] -> grill mode activated", flush=True)
            print(f"[watch] -> grill_reply (model={chat_model})...", flush=True)
            t0 = _time_mod.time()
            grill_conv = [{"sender": sender, "text": text}]
            reply = ai.grill_reply(
                grill_conv, model=chat_model,
                allowed_mcp=allowed_mcp, epithets=epithets,
                allow_web=_allow_web,
                extra_env=_extra_env,
                effort=chat_effort or ai._GRILL_EFFORT,
            )
        elif _BOB_BUILD_VERB_RE.search(raw_text):
            print(f"[watch] -> build-gate: heavy-gen request, asking {OWNER_LABEL} for approval",
                  flush=True)
            t0 = _time_mod.time()
            reply = _handle_build_approval_gate(chat_name, conv_id, sender, text)
        else:
            print(f"[watch] -> claude (model={chat_model})...", flush=True)
            t0 = _time_mod.time()
            _user_ctx = _load_user_context(msg.get("sender_mri", ""))
            _private_notes = _load_private_notes(chat_name)
            if _files_note:
                _private_notes = ((_private_notes or "") + "\n\n" + _files_note).strip()
            if persona and _image_paths:
                _attach_note = "Attachments of the message:\n" + "\n".join(
                    f"  {p}" for p in _image_paths
                )
                _private_notes = ((_private_notes or "") + "\n\n" + _attach_note).strip()
            _sw_ts = _parse_teams_ts(msg.get("timestamp", ""))
            _sw_invited = bool(
                trigger_re.search(text) or is_quote_of_bob or is_continuation
                or (bool(state.get("last_bob_reply_sender_mri"))
                    and state.get("last_bob_reply_sender_mri") == msg.get("sender_mri")
                    and 0 <= _sw_ts - state.get("last_bob_reply_time", 0.0) < _CONTINUATION_WINDOW_S)
            )
            _sw = _StillWorking(conv_id, enabled=_sw_invited)      # an uninvited reply may still be dropped
            try:
                with _sw:
                    reply = ai.freeform_reply(
                        text, model=chat_model, timeout_s=_CHAT_REPLY_TIMEOUT_S,
                        history=chat_history or None, sender=sender,
                        allow_web=_allow_web,
                        allowed_mcp=allowed_mcp,
                        epithets=epithets,
                        role_knowledge=_role_knowledge_for(chat_name, chat_config),
                        private_notes=_private_notes,
                        schedule_context=_build_schedule_context(),
                        live_context=_live_ctx or None,
                        extra_env=_extra_env,
                        metrics_ctx={"chat": chat_name},
                        image_paths=_image_paths or None,
                        user_memory_context=_user_ctx,
                        persona=persona,
                        effort=chat_effort,
                    )

                    # MCP cold-start guard: in BOB_MCP_TRANSPORT=http mode, claude CLI
                    # initialises HTTP MCP connections asynchronously and may finish
                    # generating before tool sessions are ready. Sonnet then writes
                    # something like "the MCP servers are not connected". One retry — the
                    # second call is warm and gets the tools. Cheap, deterministic.
                    if _looks_like_mcp_cold_start(reply):
                        print("[watch] -> MCP cold start detected in the reply, retrying...",
                              flush=True)
                        reply = ai.freeform_reply(
                            text, model=chat_model, timeout_s=_CHAT_REPLY_TIMEOUT_S,
                            history=chat_history or None, sender=sender,
                            allow_web=_allow_web,
                            allowed_mcp=allowed_mcp,
                            epithets=epithets,
                            role_knowledge=_role_knowledge_for(chat_name, chat_config),
                            private_notes=_private_notes,
                            schedule_context=_build_schedule_context(),
                            live_context=_live_ctx or None,
                            extra_env=_extra_env,
                            metrics_ctx={"chat": chat_name},
                            image_paths=_image_paths or None,
                            user_memory_context=_user_ctx,
                            persona=persona,
                            effort=chat_effort,
                        )
            finally:
                # Always unlink temp image files after both possible reply calls.
                for _tmp_p in _image_paths:
                    try:
                        Path(_tmp_p).unlink(missing_ok=True)
                    except Exception:
                        pass

        try:  # an error anywhere between the reply and its send must not leave the "still working" note behind
            elapsed = _time_mod.time() - t0
            print(f"[watch] -> {len(reply)}b reply in {elapsed:.1f}s", flush=True)

            reply, _gif_plan = _plan_gifs(reply, persona=persona)
            reply = _extract_and_send_files(reply, conv_id, chat_name)
            reply = _handle_example_learning(reply, msg.get("sender_mri", ""))
            reply = _handle_example_idea(reply, sender)
            reply = _handle_reaction(reply, msg.get("id", ""), conv_id)
            reply, _mention_html, _mention_entries = _handle_bob_mention(reply)

            if _is_silent_reply(reply):
                _skip_reason = "silent_sentinel" if reply.strip() else "empty"
                print(f"[watch] -> {_skip_reason}: not sending ({reply.strip()[:40]!r})",
                      flush=True)
                _append_log(log_path, {
                    "ts": _ts(),
                    "chat_name": chat_name,
                    "incoming_text": text,
                    "reply": reply,
                    "action": f"watch_skip_{_skip_reason}",
                })
                if _sw:
                    _sw.finish(False)
                last_seq_id = max(last_seq_id, seq)
                continue

            # Grace period before speaking uninvited: hold the finished reply for a
            # moment so a human gets the first word — _check_skip_answered() below
            # then sees their message and drops ours. Only for replies nobody asked
            # for; an explicit call by name is answered at full speed. Time already
            # spent in the queue and generating counts towards the window (it runs from
            # the message's own timestamp - _hold_wait_s), so a backlog does not pile up.
            # A reply to the bot itself - a quote of its message, or the same sender
            # writing within _CONTINUATION_WINDOW_S of its last answer to them - is
            # invited and goes out at once.
            # The sleep blocks this chat's worker only (one thread + one lock per
            # chat), and messages arriving during the hold are picked up by the next
            # tick — which is what we want anyway: they are part of the answer.
            # Deliberately not applied on the catch-up path: that one fires on a
            # backlog after downtime, where the conversation is already over.
            _hold_s = _hold_s_for(chat_name, chat_config)
            if _hold_s:
                _msg_ts = _parse_teams_ts(msg.get("timestamp", ""))
                _answers_bot = (
                    bool(state.get("last_bob_reply_sender_mri"))
                    and state.get("last_bob_reply_sender_mri") == msg.get("sender_mri")
                    and 0 <= _msg_ts - state.get("last_bob_reply_time", 0.0) < _CONTINUATION_WINDOW_S
                )
                _invited = bool(
                    trigger_re.search(text) or is_quote_of_bob or is_continuation or _answers_bot
                )
                _wait = _hold_wait_s(_hold_s, _msg_ts, _time_mod.time(), _invited)
                if _wait > 0:
                    print(
                        f"[watch] -> hold {_wait:.0f}s: waiting to see if someone answers "
                        "(uninvited reply)",
                        flush=True,
                    )
                    _time_mod.sleep(_wait)

            _skip, _skip_reason = _check_skip_answered(
                conv_id, seq, msg["id"], text, reply,
            )
            if _skip:
                print(
                    f"[watch] -> already answered: not sending ({_skip_reason[:60]!r})",
                    flush=True,
                )
                _append_log(log_path, {
                    "ts": _ts(),
                    "chat_name": chat_name,
                    "sender": sender,
                    "msg_id": msg["id"],
                    "action": "watch_skip_answered",
                    "reason": _skip_reason,
                    "original_question": text,
                    "skipped_reply": reply,
                })
                if _sw:
                    _sw.finish(False)
                last_seq_id = max(last_seq_id, seq)
                continue

            sent = api_client.reply_to_message(
                reply,
                msg["id"],
                sender,
                text,
                conv_id,
                msg.get("sender_mri", ""),
                mentions=_mention_entries or None,
                mentions_html=_mention_html,
                persona=persona,
            )
            state["last_reply_ts"] = _time_mod.time()
            if _sw:
                _sw.finish(bool(sent))
        except BaseException:
            if _sw:
                _sw.finish(False)
            raise
        if sent:
            _execute_gif_plan(_gif_plan, conv_id, persona=persona)
        _send_mode = "REST quote"
        print(f"[watch] -> {'sent (' + _send_mode + ')' if sent else 'ERROR sending'}",
              flush=True)
        _watch_send_log: dict = {
            "ts": _ts(),
            "chat_name": chat_name,
            "incoming_text": text,
            "sender": sender,
            "msg_id": msg["id"],
            "reply": reply,
            "action": "watch_send",
            "send_ok": sent,
        }
        if not sent:
            _send_err = api_client._get_last_send_error()
            _watch_send_log["send_error"] = _send_err
            _watch_send_log["retry_attempts"] = _send_err.get("attempts", 0) if _send_err else 0
        _append_log(log_path, _watch_send_log)
        if sent:
            chat_history = _CHAT_HISTORY.setdefault(chat_name, [])
            # Store stub_text in history — full attachment content must never reach
            # persistent history or live_context (context guard invariant).
            new_entries = [{"sender": sender, "text": stub_text}, {"sender": "Bob", "text": reply}]
            chat_history.extend(new_entries)
            _append_history_to_disk(chat_name, new_entries)
            state["last_bob_reply_time"] = _time_mod.time()
            state["last_bob_reply_sender_mri"] = msg.get("sender_mri", "")
            # Capture latest reply for forward-last-reply command; clear any stale pending.
            state["last_forward_text"] = reply
            state["pending_forward"] = None
            # Update per-user memory tracking (skip when sender_mri is empty).
            _mri = msg.get("sender_mri", "")
            if _mri:
                _now = _time_mod.time()
                _prev = _USER_LAST_ACTIVITY.get(_mri)
                _exchanges = (_prev[2] if _prev else 0) + 1
                _USER_LAST_ACTIVITY[_mri] = (_now, sender, _exchanges)
                _USER_SESSION_START.setdefault(_mri, _now)
                _USER_HISTORY.setdefault(_mri, []).extend([
                    {"sender": sender, "text": stub_text},
                    {"sender": "Bob", "text": reply},
                ])
        last_seq_id = max(last_seq_id, seq)

    state["last_sequence_id"] = last_seq_id
    all_ts = [m.get("timestamp", "") for m in msgs if m.get("timestamp")]
    if all_ts:
        state["last_composetime"] = max(all_ts)
    state["last_activity_ts"] = _time_mod.time()
    _save_watch_state(state_path, chat_name, state)


def _notes_chat_names() -> set[str]:
    """Names the owner's notes chat goes by: the default "Notes", "Notatki" from older builds, and BOB_NOTES_CHAT."""
    return {"notes", "notatki", (os.environ.get("BOB_NOTES_CHAT") or "notes").strip().lower()}


def _fire_due_notifications(
    chat_convs: list,
    args: argparse.Namespace,
    log_path: str,
    chat_config: dict | None = None,
) -> None:
    """Send all due reminders sourced from scripts/todos/todos.json.

    chat_convs: list of (chat_name, conv_id) tuples (in-memory, zero IO).
    Falls back to api_client.find_conversation_id for chats not in chat_convs.

    Concurrency: each todo is claimed via mark_notified() BEFORE the send call.
    This prevents duplicate fires across restarts at the cost of silent drops
    if the send fails after the mark is persisted — same tradeoff as the prior
    atomic-delete behaviour, but now we keep the entry (audit + future retries).
    """
    due = todos_store.due_for_fire()
    if not due:
        return
    conv_map = dict(chat_convs)
    # Fallback conv_id for reminders whose notify_chat was not stored (e.g. created via
    # HTTP MCP server which has no per-request BOB_CURRENT_CHAT). Send to control chat.
    _cfg = chat_config or {}
    control_conv_id = next(
        (conv_map[cn] for cn in conv_map if (_cfg.get(cn) or {}).get("control_chat")),
        None,
    )
    for entry in due:
        notif_id = entry.get("id", "?")
        chat = entry.get("notify_chat") or entry.get("chat") or ""
        msg = entry.get("title", "")
        at_str = entry.get("notify_at", "")
        conv_id = conv_map.get(chat)
        if not conv_id and chat.lower() in _notes_chat_names():
            # The owner's notes chat by its default name ("Notes", or "Notatki" from older builds): never look it up
            # by a substring of chat topics, where "Release notes" would match - use the control chat.
            conv_id = control_conv_id or next(
                (conv_map[cn] for cn in conv_map if cn.lower() in _notes_chat_names()), None)
            if conv_id:
                chat = "control chat"
        if not conv_id and chat.lower() not in _notes_chat_names():
            conv_id = api_client.find_conversation_id(chat)
        if not conv_id and control_conv_id:
            print(
                f"[watch] NOTIF WARN: no conv_id for chat '{chat}' "
                f"(id={notif_id}) — sending to the control chat as a fallback.",
                file=sys.stderr, flush=True,
            )
            conv_id = control_conv_id
            chat = "control chat (fallback)"
        if not conv_id:
            print(
                f"[watch] NOTIF WARN: conv_id not found for chat '{chat}' "
                f"(id={notif_id}, at={at_str}) — skipping.",
                file=sys.stderr, flush=True,
            )
            _append_log(log_path, {
                "ts": _ts(),
                "kind": "notification_fired",
                "id": notif_id,
                "chat": chat,
                "at": at_str,
                "ok": False,
                "error": "conv_id_not_found",
            })
            continue
        # Atomic claim — stamps notified_at before send to prevent duplicate fires.
        todos_store.mark_notified(notif_id)
        notes = entry.get("notes") or ""
        notes_section = f"\nTask notes: {notes}" if notes else ""
        instruction = (
            f"[PERSONAL REMINDER FOR {OWNER_LABEL.upper()} — send it now to the chat '{chat}']\n"
            f"Reminder text: {msg}{notes_section}\n\n"
            f"This is a PERSONAL reminder for {OWNER_LABEL} — NOT a message to the team. "
            "Write a short reminder in the first person (e.g. 'Hey, a reminder: ...'). "
            "Be direct: no preamble, no signature, no word 'Bob'. "
            "Do not generate questions to the team or messages like 'Hey, I have a question for the team'. "
            "Write it in the same language as the reminder text. "
            "If the instruction mentions a GIF, add the tag [GIF: query]."
        )
        generated = ai.freeform_reply(instruction, model=args.model, allowed_mcp=None)
        gif_match = _GIF_TAG_RE.search(generated) if generated else None
        clean_text = _GIF_TAG_RE.sub("", generated).strip() if generated else ""
        if gif_match:
            ok = api_client.send_message_with_gif(clean_text, gif_match.group(1).strip(), conv_id)
        elif clean_text:
            ok = api_client.send_message(clean_text, conv_id)
        else:
            # AI returned empty — surface as failure rather than silently swallowing.
            ok = False
        print(
            f"[watch] -> notif fired: id={notif_id} chat='{chat}' "
            f"at={at_str} ok={ok}",
            flush=True,
        )
        _append_log(log_path, {
            "ts": _ts(),
            "kind": "notification_fired",
            "id": notif_id,
            "chat": chat,
            "at": at_str,
            "ok": bool(ok),
            **({"error": "empty_ai_reply"} if not ok and not clean_text and not gif_match else {}),
        })


def _run_watch_loop(page, args: argparse.Namespace) -> None:
    """Daemon mode: watch one or more chats and auto-reply via REST API.

    Polling protocol:
      1. Resolve conversation IDs for all watched chats.
      2. Navigate in the browser to the first chat — keeps Playwright session alive
         and triggers the token interceptor.
      3. Every args.poll seconds: poll each chat in round-robin order.
      4. Filter messages newer than last_sequence_id and not sent by this bot.
      5. For each triggered message: call Claude CLI, then reply_to_message().
      6. Persist last_sequence_id per chat so restarts pick up correctly.

    Security: claude CLI is invoked with --allowedTools (enforced in ai.py).
    """
    watch_chats = args.watch_chats
    state_path = args.watch_state
    log_path = args.log_file

    # When BOB_MCP_TRANSPORT=http, MCP servers must be running as daemons
    # (see scripts/mcp_admin/README.md). Fail fast with a clear hint rather
    # than silently letting claude CLI try to fetch tools from dead URLs.
    if os.environ.get("BOB_MCP_TRANSPORT", "").strip().lower() == "http":
        import socket as _socket
        import subprocess as _sp
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from mcp_admin.ports import MCP_PORTS as _MCP_PORTS  # noqa: PLC0415
        print("[watch] BOB_MCP_TRANSPORT=http — checking the MCP servers...", flush=True)
        _hc = Path(__file__).resolve().parents[1] / "mcp_admin" / "healthcheck.py"
        _rc = _sp.run([sys.executable, str(_hc), "--timeout", "30", "--quiet"]).returncode
        if _rc != 0:
            print("[watch] ERROR: some MCP servers are not responding. "
                  "Run scripts/mcp_admin/install_launchd.sh or unset BOB_MCP_TRANSPORT.",
                  file=sys.stderr)
            return
        _mcp_status = []
        for _name, _port in _MCP_PORTS.items():
            try:
                with _socket.create_connection(("127.0.0.1", _port), timeout=0.5):
                    _mcp_status.append(f"✓ {_name}")
            except OSError:
                _mcp_status.append(f"✗ {_name}")
        print(f"[watch] MCP ({len(_MCP_PORTS)}): {', '.join(_mcp_status)}", flush=True)
    else:
        # stdio mode: read-only servers (ai._DAEMON_SERVERS) still run as HTTP daemons.
        _ensure_daemon_servers(restart=True)

    # Load chat config early — may contain conv_id overrides that bypass API discovery.
    _chat_config_mtime = _config_mtime(_CHAT_CONFIG_PATH)
    chat_config = _load_chat_config(_CHAT_CONFIG_PATH)

    # Resolve conversation IDs for all watched chats.
    # Priority: conv_id from chat-config.json → autodetect via Teams API.
    chat_convs: list[tuple[str, str]] = []
    ingest_convs: list[tuple[str, str]] = []  # ingest_only chats: never reach the reply loop
    for chat_name in watch_chats:
        conv_id = _conv_id_override_for(chat_name, chat_config) or _resolve_conv_id(chat_name)
        if not conv_id:
            print(f"[watch] ERROR: conversation {chat_name!r} not found. "
                  f"Add 'conv_id' in chat-config.json as a fallback.", file=sys.stderr)
        else:
            src = "config" if _conv_id_override_for(chat_name, chat_config) else "autodetect"
            if _ingest_only_for(chat_name, chat_config):
                ingest_convs.append((chat_name, conv_id))
                print(f"[watch] {chat_name!r}: ingest-only (no replies)", flush=True)
                continue
            chat_convs.append((chat_name, conv_id))
            print(f"[watch] Activating chat: {chat_name!r} ({src}, conv_id={conv_id})", flush=True)
    _refresh_receive_only(chat_config, {c for _, c in ingest_convs})

    if not chat_convs:
        print("[watch] No active conversations — exiting.", file=sys.stderr)
        return

    # Navigate to a chat to trigger the Bearer token interceptor — only if not already captured.
    # Token is usually set during initial Teams load (page.goto), so this is a fallback.
    # We navigate to the notes chat (always accessible, owner's own notes-to-self) rather than
    # the watched chat by name, because watched chats may have different display names
    # in the Teams DOM. "Notatki" is the Polish name, tried second.
    for _notes_name in ("Notes", "Notatki"):
        if api_client._auth_token:
            break
        print(f"[watch] Token not captured — navigating to {_notes_name!r} to get it...",
              flush=True)
        scraper.switch_to_chat_by_name(page, _notes_name)
        for _ in range(10):
            if api_client._auth_token:
                break
            page.wait_for_timeout(500)  # lets the token interceptor run (time.sleep would not)
    if not api_client._auth_token:
        print("[watch] WARNING: Bearer token was not captured — "
              "the API may return 401. Try --force-login.", file=sys.stderr, flush=True)
    else:
        print("[watch] Bearer token OK.", flush=True)

    caller_label = {
        "owner": f"only {OWNER_LABEL} (via 'Bob')",
        "all":   "everyone (via 'Bob')",
        "auto":  "auto — reply to every message",
    }.get(args.caller, args.caller)
    chats_label = ", ".join(f"'{n}'" for n, _ in chat_convs)
    print(f"[watch] OK. Watching: {chats_label}", flush=True)
    print(f"[watch] Polling every {args.poll}s. Callers: {caller_label}.", flush=True)
    print(f"[watch] Rate-limit: min {args.rate_limit}s between replies.", flush=True)
    print("[watch] Ctrl-C to stop.", flush=True)

    for name, _ in chat_convs:
        cl = _caller_for(name, chat_config, args)
        ep = _epithets_for(name, chat_config)
        ep_label = ", ".join(ep) if ep else "none"
        print(f"[watch] {name!r}: caller = {cl}, epithets = {ep_label}", flush=True)

    # Per-chat state loaded once; updated in-place by _process_chat_messages.
    saved = _load_watch_state(state_path)
    chat_states: dict[str, dict] = {
        name: dict(saved.get(name, {})) for name, _ in chat_convs
    }

    # On startup, advance last_composetime to now so Bob ignores messages
    # that arrived while he was offline.
    _startup_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for name in chat_states:
        if chat_states[name].get("last_composetime", "") < _startup_ts:
            chat_states[name]["last_composetime"] = _startup_ts
            print(f"[watch] {name!r}: skipping old messages (composetime → now)", flush=True)

    # Restore conversation history from disk so Bob remembers across restarts.
    for name, _ in chat_convs:
        loaded = _load_history_from_disk(name)
        if loaded:
            _CHAT_HISTORY[name] = loaded
            print(f"[watch] History {name!r}: {len(loaded)} entries from disk.", flush=True)

    # Start per-user memory sweeper daemon thread.
    threading.Thread(target=_user_memory_sweeper, daemon=True).start()
    print("[watch] user_memory sweeper started.", flush=True)

    # Per-chat locks: skip this tick if previous Claude call for a chat is still in flight.
    _chat_processing: dict[str, threading.Lock] = {}
    # Each Claude call can hold a worker for 30–300 s; use enough workers so that
    # a long-running chat does not starve short-latency chats.
    _max_workers = min(len(chat_convs), 10)
    _executor = ThreadPoolExecutor(
        max_workers=_max_workers,
        thread_name_prefix="bob-chat",
    )
    print(f"[watch] Thread pool: max {_max_workers} parallel workers.", flush=True)

    # Anchored to the initial navigation timestamp so _refresh_token_via_browser's
    # "did a new token arrive?" check works correctly on the first refresh cycle.
    _last_token_nav_ts: float = api_client._auth_token_ts
    tick = 0

    def _refresh_token_via_browser() -> None:
        """Force Teams to issue a fresh chatsvc REST request so the interceptor
        captures a new Bearer token.

        Strategy: click_any_other_chat() clicks the first non-active rail item
        (JS-native click, no name matching) — this is reliable regardless of
        which chats are configured or visible. That click alone is what makes
        Teams hit chatsvc, so it is the whole mechanism; we deliberately do NOT
        navigate anywhere afterwards (see below).

        Fallback: page.reload() when the rail is empty or click produced no
        new token (e.g. service worker cached the request).

        Success is measured on api_client._auth_token_gen — the token VALUE —
        not on _auth_token_ts, which is bumped even when Teams hands back the
        identical token. Reporting "refreshed OK" for an unchanged token is
        what once made a 401 loop invisible for an hour.
        """
        nonlocal _last_token_nav_ts
        try:
            gen_before = api_client._auth_token_gen
            # Click any non-active rail chat to trigger a chatsvc request — more
            # reliable than switch_to_chat_by_name(away_target) because it doesn't
            # depend on the away chat being visible/findable in the rail by name.
            clicked = scraper.click_any_other_chat(page)
            if clicked:
                # No switch back: the chat-to-self has its notes name only in
                # chat-config.json — Teams renders it as "<owner display name> (You)", so
                # switch_to_chat_by_name(page, "Notes") could never match and just
                # burned its full timeout on every single refresh. Nothing needs the
                # rail to be left on a particular chat; the click above already
                # produced the chatsvc request we are here for.
                print(f"[watch] Refreshing Bearer token — clicked {clicked!r}...",
                      flush=True)
            else:
                print("[watch] Refreshing Bearer token — no other chats in the rail, reloading...",
                      flush=True)

            # Fallback if click didn't fire a real navigation or rail was empty.
            if api_client._auth_token_gen == gen_before:
                page.reload(wait_until="domcontentloaded", timeout=15000)
            for _ in range(20):
                if api_client._auth_token_gen > gen_before:
                    break
                # wait_for_timeout, not time.sleep: the sync API dispatches the `request` events
                # (and so the token interceptor) only while Playwright itself is waiting.
                page.wait_for_timeout(500)
            _last_token_nav_ts = _time_mod.time()
            if api_client._auth_token_gen > gen_before:
                print(f"[watch] Bearer token refreshed OK "
                      f"(sha={api_client.token_fingerprint(api_client._auth_token)}).",
                      flush=True)
            else:
                # Teams re-issued the same token. Refreshing again cannot help, so say
                # so plainly instead of claiming success -- if 401s continue, the cause
                # is not the token.
                print(f"[watch] WARNING: token UNCHANGED after navigation "
                      f"(sha={api_client.token_fingerprint(api_client._auth_token)}) — "
                      f"if 401s keep coming, the token is not the cause.",
                      flush=True)
        except Exception as exc:
            print(f"[watch] ERROR refreshing the token: {exc}", file=sys.stderr, flush=True)

    def _relaunch_teams_page() -> bool:
        """Open Teams in a FRESH browser context from session.json (what a manual Bob restart does).

        The old page keeps handing back the same dead token once its own sign-in state is stuck.
        On success the new page replaces `page` for the whole loop and the old context is closed;
        on failure the new context is closed and the old page stays.
        """
        nonlocal page, _last_token_nav_ts
        gen_before = api_client._auth_token_gen
        old_ctx = page.context
        ctx = None
        new_page = None
        loaded = False
        try:
            ctx = old_ctx.browser.new_context(storage_state=str(SESSION_FILE))
            new_page = ctx.new_page()
            _attach_token_interceptor(new_page)
            _attach_ic3_token_interceptor(new_page)
            if args.sniff_api:
                _start_api_sniffer(new_page)
            new_page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=60000)
            loaded = True
            for _ in range(60):
                if api_client._auth_token_gen > gen_before:
                    break
                new_page.wait_for_timeout(500)
        except Exception as exc:
            print(f"[watch] ERROR reopening Teams: {exc}", file=sys.stderr, flush=True)
        # Swap only to a page that actually loaded Teams; a token that arrived meanwhile through the
        # old page's listener is kept either way.
        if loaded and new_page is not None and api_client._auth_token_gen > gen_before:
            page = new_page
            _last_token_nav_ts = _time_mod.time()
            try:
                old_ctx.close()
            except Exception:
                pass
            print("[watch] Teams reopened (session.json) — new token OK.", flush=True)
            return True
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        print("[watch] Teams reopened, but the token is still unchanged.", file=sys.stderr, flush=True)
        return False

    def _mac_alert(text: str) -> None:
        # Teams is unreachable without a token, so the alert goes to the desktop instead.
        try:
            subprocess.run(["osascript", "-e", f'display notification {json.dumps(text)} with title "Bob"'],
                           timeout=10, check=False)
        except Exception as exc:
            print(f"[watch] macOS notification failed: {exc}", file=sys.stderr, flush=True)

    def _token_event(event: str, **extra) -> None:
        _append_log(log_path, {"ts": _ts(), "event": event, **extra})

    def _handle_token_health(changed: bool) -> None:
        now = _time_mod.time()
        minutes = _token_health.dead_minutes(now)
        action = _token_health.after_refresh(changed, now)
        if action == "relaunch":
            _token_event("teams_token_relaunch", failed_refreshes=_token_health.failed_refreshes)
            if _relaunch_teams_page():
                action = _token_health.after_refresh(True, _time_mod.time())
        elif action == "alert":
            _token_event("teams_token_stuck", minutes=minutes)
            _mac_alert(f"The Teams token has not renewed for {minutes} min — Bob is not reading chats. Sign in "
                       f"in the browser window or restart Bob.")
        if action == "recovered":
            _token_event("teams_token_recovered", minutes=minutes)
            if minutes >= 2:
                api_client.send_message(f"Bob: for ~{minutes} min I had no access to Teams (the token did not "
                                        f"renew). I got it back on my own — reading the messages from that time now.",
                                        api_client.NOTES_CONV_ID)

    _token_health = token_health.TokenHealth()

    _last_stats_ts: float = 0.0
    _last_notif_check: float = 0.0
    _last_sharepoint_ts: float = _timer_last("sharepoint")
    _last_panel_approvals_ts: float = 0.0
    _last_refresh_ts: float = 0.0
    _last_sp_session_warm_ts: float = _timer_last("sp_session_warm")
    _last_broadcast_ts: float = 0.0
    _last_daemon_watchdog_ts: float = _time_mod.time()
    _MIN_REFRESH_COOLDOWN_S = 30  # ignore 401 flags for 30s after a refresh
    # Back-off for refreshes that changed nothing. A refresh that re-captures the
    # identical token cannot fix a 401, so repeating it every 30 s is pure noise --
    # that is exactly how a 401 loop starts. After a no-op refresh we wait longer and
    # longer before letting a 401 trigger another one; any refresh that does produce
    # a new token resets the back-off to zero.
    _NOOP_REFRESH_BACKOFF_S = (0, 120, 300, 900)
    _noop_refresh_streak = 0

    while True:
        try:
            tick += 1
            _chat_config_mtime = _reload_chat_config_if_changed(chat_config, _CHAT_CONFIG_PATH, _chat_config_mtime)
            _refresh_receive_only(chat_config, {c for _, c in ingest_convs})

            # Proactive token refresh — navigate browser every 40 min to keep token alive.
            # Cooldown: after a refresh, suppress 401-triggered re-refreshes for 30s.
            # Without it, in-flight requests (sent with the old token) that return 401 after
            # the refresh completes cause an infinite refresh loop.
            token_age = _time_mod.time() - _last_token_nav_ts
            since_last_refresh = _time_mod.time() - _last_refresh_ts
            _backoff_idx = min(_noop_refresh_streak, len(_NOOP_REFRESH_BACKOFF_S) - 1)
            _refresh_cooldown = max(_MIN_REFRESH_COOLDOWN_S, _NOOP_REFRESH_BACKOFF_S[_backoff_idx])
            # Expired token: retried every minute (feeds relaunch / alert); before the expiry a
            # proactive refresh instead of waiting for the first 401 (token_health.plan_refresh).
            _plan = token_health.plan_refresh(now=_time_mod.time(), token=api_client._auth_token,
                                              flagged_401=api_client._needs_token_refresh,
                                              since_last_refresh=since_last_refresh, base_cooldown=_refresh_cooldown)
            token_expired, needs_refresh_401, expiring = _plan["expired"], _plan["needs_401"], _plan["expiring"]
            if needs_refresh_401 or expiring or token_age >= _TOKEN_REFRESH_INTERVAL_S:
                reason = ("401 detected" if needs_refresh_401 else "token expires in <10 min" if expiring
                          else f"token is {token_age/60:.0f} min old")
                if needs_refresh_401 and _noop_refresh_streak:
                    reason += f", {_noop_refresh_streak}x without a token change"
                print(f"[watch] Token refresh ({reason})", flush=True)
                # A proactive refresh that hands back the same, still valid token is normal (Teams caches
                # it until its renewal window) and must not build up the 401 back-off below.
                api_client._needs_token_refresh = False  # clear before refresh, not after
                _last_refresh_ts = _time_mod.time()
                _gen_before_refresh = api_client._auth_token_gen
                _refresh_token_via_browser()
                if api_client._auth_token_gen > _gen_before_refresh:
                    _noop_refresh_streak = 0
                elif token_health.counts_for_backoff(changed=False, needs_401=needs_refresh_401, expired=token_expired):
                    _noop_refresh_streak += 1
                # Only a refresh made with an already expired token counts towards relaunch / alert:
                # a 401 with a valid token has another cause and a new browser won't fix it.
                if token_expired:
                    _handle_token_health(api_client._auth_token_gen > _gen_before_refresh)
                    if api_client._auth_token_gen > _gen_before_refresh:
                        _noop_refresh_streak = 0  # the relaunch brought a new token

            # Post/edit stats to the notes chat every 10 min — run in executor so the
            # subprocess call does not block the main poll loop for up to 30 s.
            if _time_mod.time() - _last_stats_ts >= _STATS_POST_INTERVAL_S:
                _last_stats_ts = _time_mod.time()
                _stats_script = Path(__file__).parent / "stats.py"
                def _stats_task(_script=_stats_script):
                    try:
                        subprocess.run(
                            [sys.executable, str(_script), "--post", "--since", "today"],
                            timeout=30,
                        )
                    except Exception as exc:
                        print(f"[watch] stats post error: {exc}", file=sys.stderr, flush=True)
                _executor.submit(_stats_task)

            # Fire due notifications every ~60 s — run in executor so the AI call
            # inside _fire_due_notifications does not block the main poll loop.
            if _time_mod.time() - _last_notif_check >= _NOTIF_CHECK_INTERVAL_S:
                _last_notif_check = _time_mod.time()
                _convs_snapshot = list(chat_convs)
                _log_path_snap = args.log_file
                _cfg_snapshot = chat_config
                def _notif_task(_convs=_convs_snapshot, _lp=_log_path_snap, _cfg=_cfg_snapshot):
                    try:
                        _fire_due_notifications(_convs, args, _lp, chat_config=_cfg)
                    except Exception as exc:
                        print(f"[watch] notif tick error: {exc}", file=sys.stderr, flush=True)
                _executor.submit(_notif_task)

            # Check the SharePoint leave XLSX every 60 min — subprocess isolates Playwright
            # event-loop from the synchronous watch loop (same pattern as stats task).
            # Poller only re-downloads/converts/pushes when SharePoint's
            # TimeLastModified actually advanced (see sharepoint_meta.py).
            if (not org_config.LEAVE_SHAREPOINT_URL
                    and _time_mod.time() - _last_sharepoint_ts >= _SHAREPOINT_POLL_INTERVAL_S):
                # Not configured: say so once, then never spawn the poller.
                _last_sharepoint_ts = float("inf")
                print("[watch] LEAVE_SHAREPOINT_URL not configured — leave poll disabled", flush=True)
            if _time_mod.time() - _last_sharepoint_ts >= _SHAREPOINT_POLL_INTERVAL_S:
                _last_sharepoint_ts = _time_mod.time()
                _timer_mark("sharepoint", _last_sharepoint_ts)
                _sp_script = Path(__file__).parent / "sharepoint_poller.py"
                _sp_log_path = args.log_file
                def _sharepoint_task(_script=_sp_script, _lp=_sp_log_path):
                    try:
                        result = subprocess.run(
                            [sys.executable, str(_script)],
                            capture_output=True,
                            text=True,
                            timeout=_POLLER_SUBPROCESS_TIMEOUT_S,
                        )
                        if result.returncode == 0:
                            _payload = _parse_json_payload(result.stdout)
                            status = _payload.get("status", "unknown")
                            count = _payload.get("count", -1)
                            _append_log(_lp, {
                                "ts": _ts(),
                                "event": "sharepoint_poll",
                                "dest": "knowledge/leave/leave.json",
                                "status": status,
                                "count": count,
                            })
                            _verb = "downloaded" if status == "ok" else "no changes"
                            print(
                                f"[watch] sharepoint poll OK ({_verb}) — leave.json: {count} records",
                                flush=True,
                            )
                        else:
                            print(
                                f"[watch] sharepoint poll error (exit {result.returncode}):"
                                f" {result.stderr.strip()}",
                                file=sys.stderr,
                                flush=True,
                            )
                    except Exception as exc:
                        print(
                            f"[watch] sharepoint poll error: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                _executor.submit(_sharepoint_task)

            # Yes/no requests opened in Bob's panel (Claude Code, automations): each posted to the control chat
            # once, so the owner can answer "Bob, approve apr-..." away from the computer (the panel
            # is loopback-only).
            if _time_mod.time() - _last_panel_approvals_ts >= _PANEL_APPROVALS_INTERVAL_S:
                _last_panel_approvals_ts = _time_mod.time()

                def _panel_approvals_task():
                    if not _PANEL_APPROVALS_LOCK.acquire(blocking=False):
                        return
                    try:
                        from bob_panel import approvals as _approvals  # noqa: PLC0415
                        _approvals.announce_pending(
                            lambda text: api_client.send_message(text, api_client.NOTES_CONV_ID))
                    except Exception as exc:  # noqa: BLE001
                        print(f"[watch] panel approvals error: {exc}", file=sys.stderr, flush=True)
                    finally:
                        _PANEL_APPROVALS_LOCK.release()
                _executor.submit(_panel_approvals_task)

            # Check broadcast schedule every 30 s — fire generate/dry-run/send phases
            # as subprocesses on Sprint Planning days. Non-blocking via executor.
            if _time_mod.time() - _last_broadcast_ts >= _BROADCAST_CHECK_INTERVAL_S:
                _last_broadcast_ts = _time_mod.time()
                _bc_log_path = args.log_file
                def _broadcast_tick_task(_lp=_bc_log_path):
                    try:
                        _broadcast_scheduler_tick(_lp)
                    except Exception as exc:
                        print(
                            f"[watch] broadcast tick error: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                _executor.submit(_broadcast_tick_task)

            # Watchdog for MCP daemons (stdio mode only): restart a daemon that died
            # mid-session, otherwise claude CLI keeps an HTTP entry to a dead port.
            if (
                os.environ.get("BOB_MCP_TRANSPORT", "").strip().lower() != "http"
                and _time_mod.time() - _last_daemon_watchdog_ts >= _DAEMON_WATCHDOG_INTERVAL_S
            ):
                _last_daemon_watchdog_ts = _time_mod.time()
                try:
                    _ensure_daemon_servers(restart=False)
                except Exception as exc:
                    print(f"[watch] MCP daemon watchdog error: {exc}", file=sys.stderr, flush=True)

            # Re-warm both SharePoint domains every 6h and re-save session.json.
            # Uses a separate headless Playwright instance (no --proxy-auto-detect)
            # — same setup as the pollers — because the Teams browser context routes
            # traffic through a PAC proxy that blocks direct SharePoint connections.
            if _time_mod.time() - _last_sp_session_warm_ts >= _SP_SESSION_WARM_INTERVAL_S:
                _last_sp_session_warm_ts = _time_mod.time()
                _timer_mark("sp_session_warm", _last_sp_session_warm_ts)
                def _sp_session_warm_task():
                    try:
                        from playwright.sync_api import sync_playwright as _sync_pw
                        with _sync_pw() as _p:
                            _browser = _p.chromium.launch(headless=True)
                            _ctx = _browser.new_context(storage_state=load_storage_state())
                            any_ok = False
                            for sp_url in SP_WARMUP_URLS:
                                try:
                                    sp_pg = _ctx.new_page()
                                    sp_pg.goto(sp_url, wait_until="domcontentloaded", timeout=20_000)
                                    sp_pg.wait_for_timeout(1_500)
                                    sp_pg.close()
                                    any_ok = True
                                except Exception as exc:
                                    print(
                                        f"[watch] sp-session-warm error ({sp_url}): {exc}",
                                        file=sys.stderr,
                                        flush=True,
                                    )
                            if any_ok:
                                save_storage_state(_ctx)
                                print("[watch] sp-session-warm OK — session.json refreshed", flush=True)
                            _browser.close()
                    except Exception as exc:
                        print(
                            f"[watch] sp-session-warm error: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                _executor.submit(_sp_session_warm_task)

            for chat_name, conv_id in chat_convs:
                if _is_chat_disabled(chat_name):
                    print(f"[watch] skip [{chat_name}]: chat disabled", flush=True)
                    continue
                lock = _chat_processing.setdefault(chat_name, threading.Lock())
                if not lock.acquire(blocking=False):
                    continue  # previous tick still processing this chat

                def _task(cn=chat_name, ci=conv_id, lk=lock, _tick=tick):
                    try:
                        _process_chat_messages(
                            cn, ci, args,
                            chat_states[cn],
                            _tick,
                            caller=_caller_for(cn, chat_config, args),
                            control_chat=_control_chat_for(cn, chat_config),
                            allowed_mcp=_mcp_for(cn, chat_config),
                            epithets=_epithets_for(cn, chat_config),
                            chat_config=chat_config,
                            persona=_persona_for(cn, chat_config),
                        )
                    except Exception as exc:
                        print(f"[watch] ERROR {cn!r}: {exc}", file=sys.stderr, flush=True)
                    finally:
                        lk.release()

                _executor.submit(_task)
            _time_mod.sleep(args.poll)

        except KeyboardInterrupt:
            print("\n[watch] Interrupted (Ctrl-C).", flush=True)
            _executor.shutdown(wait=False, cancel_futures=True)
            _append_log(log_path, {
                "ts": _ts(),
                "chat_name": str(watch_chats),
                "action": "watch_stop",
            })
            break


_API_SNIFF_FILE = str(Path(__file__).parent / "api-sniff.jsonl")

# URL fragments that identify Teams messaging API traffic worth capturing.
_SNIFF_PATTERNS = (
    "/messages",
    "/conversations",
    "/v1/users",
    "/api/mt/",
    "/api/chatsvc/",
    "ng.msg.teams.microsoft.com",
    "teams.microsoft.com/api",
)


def _attach_ic3_token_interceptor(page) -> None:
    """Intercept the ic3-audience Bearer token from AMS-related requests.

    The chatsvc token captured by _attach_token_interceptor has a DIFFERENT
    audience than what AMS (Bob's own gif upload) needs. The Teams client's idle
    polling already emits requests to asyncgw.teams.microsoft.com / asm.skype.com
    carrying this token, so a passive listener keeps it fresh. Without it,
    send_ams_gif_message() fails closed and the [BOB_GIF] reaction is skipped.
    """
    def _on_request(request) -> None:
        url = request.url
        if "asyncgw.teams.microsoft.com" not in url and "asm.skype.com" not in url:
            return
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            api_client.set_ic3_token(auth[len("Bearer "):])

    page.on("request", _on_request)


def _attach_token_interceptor(page) -> None:
    """Intercept the first chatsvc request from Playwright and extract the Bearer token.

    Teams web client sends Authorization: Bearer <token> on every chatsvc call.
    We capture it once and pass it to api_client so REST calls outside the browser
    are authenticated correctly.
    """
    def _on_request(request) -> None:
        if "chatsvc" not in request.url:
            return
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[len("Bearer "):]
            api_client.set_auth_token(token)

    page.on("request", _on_request)


def _start_api_sniffer(page, log_path: str = _API_SNIFF_FILE) -> None:
    """Attach a Playwright request listener that logs Teams API calls to JSONL.

    Captures: URL, method, POST body (truncated to 2 KB), response status.
    Only records URLs matching _SNIFF_PATTERNS to avoid noise.
    """
    def _on_request(request) -> None:
        url = request.url
        if not any(p in url for p in _SNIFF_PATTERNS):
            return
        record: dict = {
            "ts": _ts(),
            "method": request.method,
            "url": url,
        }
        try:
            body = request.post_data
            if body:
                record["body"] = body[:2048]
        except Exception:
            pass
        _append_log(log_path, record)

    def _on_response(response) -> None:
        url = response.url
        if not any(p in url for p in _SNIFF_PATTERNS):
            return
        try:
            body = response.text()
            _append_log(log_path, {
                "ts": _ts(),
                "type": "response",
                "status": response.status,
                "url": url,
                "body": body[:2048] if body else "",
            })
        except Exception:
            pass

    page.on("request", _on_request)
    page.on("response", _on_response)
    print(f"[sniff] Interceptor active — writing to {log_path}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# WebSocket sniffer — passive listener, zero replies
# ---------------------------------------------------------------------------

_WS_SNIFF_FILE = str(Path(__file__).parent / "ws-sniff.jsonl")


def _start_ws_sniffer(page, log_path: str = _WS_SNIFF_FILE) -> None:
    """Attach a Playwright WebSocket listener that logs all Teams WS frames.

    Records every send/receive frame with timestamp, WebSocket URL, direction,
    and payload (truncated to 4 KB).  Use alongside --sniff-ws to discover
    the real-time event format Teams uses so we can switch from polling to push.
    """
    def _on_websocket(ws) -> None:
        url = ws.url
        print(f"[ws-sniff] New WS connection: {url}", file=sys.stderr, flush=True)
        _append_log(log_path, {"ts": _ts(), "event": "ws_open", "url": url})

        def _on_frame_sent(payload) -> None:
            _append_log(log_path, {
                "ts": _ts(), "event": "sent", "url": url,
                "payload": payload[:4096] if isinstance(payload, str) else repr(payload)[:4096],
            })

        def _on_frame_received(payload) -> None:
            _append_log(log_path, {
                "ts": _ts(), "event": "received", "url": url,
                "payload": payload[:4096] if isinstance(payload, str) else repr(payload)[:4096],
            })

        def _on_close() -> None:
            _append_log(log_path, {"ts": _ts(), "event": "ws_close", "url": url})
            print(f"[ws-sniff] WS closed: {url}", file=sys.stderr, flush=True)

        ws.on("framesent", lambda f: _on_frame_sent(f))
        ws.on("framereceived", lambda f: _on_frame_received(f))
        ws.on("close", _on_close)

    page.on("websocket", _on_websocket)
    print(f"[ws-sniff] WebSocket listener active — writing to {log_path}",
          file=sys.stderr, flush=True)


def _run_ws_sniff_loop(page, args: argparse.Namespace) -> None:
    """Passive mode: navigate to the watched chat, log WS frames, send nothing."""
    watch_chat = args.watch_chats[0]
    print("[ws-sniff] Passive mode — listening only, sending nothing.", flush=True)
    print(f"[ws-sniff] Navigating to: {watch_chat!r}", flush=True)
    scraper.switch_to_chat_by_name(page, watch_chat)
    print("[ws-sniff] Listening. Write something in the channel and watch ws-sniff.jsonl.",
          flush=True)
    print("[ws-sniff] Ctrl-C to stop.", flush=True)
    try:
        while True:
            _time_mod.sleep(1)
    except KeyboardInterrupt:
        print("\n[ws-sniff] Interrupted.", flush=True)


def _run_approval_loop(page, chats: list[dict], args: argparse.Namespace) -> None:
    """Walk through chats, draft a reply, and present the approval menu."""
    log_path = args.log_file

    for chat in chats[:args.max_chats]:
        chat_id = chat["chat_id"]
        chat_name = chat["name"]
        print(f"\n--- Chat: {chat_name} ---")

        if not scraper.switch_to_chat(page, chat_id):
            print(f"Could not open the chat {chat_name!r} — skipping.")
            continue

        msgs = scraper.read_messages(page, max_messages=20)
        if not msgs:
            print("No messages — skipping.")
            continue

        # Generate initial draft.
        draft = ai.draft_reply(
            chat_context=chat_name,
            last_messages=msgs,
            model=args.model,
        )

        while True:
            if draft:
                print(f"\nSuggested reply:\n  {draft}")
            else:
                print("\n(no suggestion — try [r]egen or [n]ext)")

            raw = input("\n[s]end / [e]dit / [r]egen / [n]ext / [q]uit > ").strip().lower()
            # Treat empty input as 'n' (skip).
            choice = raw[:1] if raw else 'n'

            if choice == 's':
                if not draft:
                    print("Cannot send an empty message. Use [r]egen or [e]dit.")
                    continue
                sent = scraper.send_message(page, draft)
                _interactive_send_log: dict = {
                    "ts": _ts(),
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "suggested_reply": draft,
                    "action": "send",
                    "sent_text": draft if sent else None,
                    "send_ok": sent,
                }
                if not sent:
                    # Interactive send uses scraper.send_message (Playwright), not
                    # api_client — _last_send_error would reflect a stale/unrelated
                    # REST call, so only neutral fields are added here (no send_error).
                    _interactive_send_log["retry_attempts"] = 0
                _append_log(log_path, _interactive_send_log)
                if sent:
                    print("Message sent.")
                else:
                    print("Error sending the message.")
                break

            elif choice == 'e':
                edited = input("Edit the reply: ").strip()
                if not edited:
                    print("Empty edit — not sending.")
                    _append_log(log_path, {
                        "ts": _ts(),
                        "chat_id": chat_id,
                        "chat_name": chat_name,
                        "suggested_reply": draft,
                        "action": "edit_cancelled",
                        "sent_text": None,
                    })
                    break
                sent = scraper.send_message(page, edited)
                _interactive_edit_log: dict = {
                    "ts": _ts(),
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "suggested_reply": draft,
                    "action": "edit",
                    "sent_text": edited if sent else None,
                    "send_ok": sent,
                }
                if not sent:
                    # See note in the [s]end branch above — no send_error here,
                    # Playwright path does not go through api_client.
                    _interactive_edit_log["retry_attempts"] = 0
                _append_log(log_path, _interactive_edit_log)
                if sent:
                    print("Message sent.")
                else:
                    print("Error sending the message.")
                break

            elif choice == 'r':
                print("Generating a new suggestion...")
                draft = ai.draft_reply(
                    chat_context=chat_name,
                    last_messages=msgs,
                    model=args.model,
                )
                # Loop back to show the new draft.

            elif choice == 'n':
                _append_log(log_path, {
                    "ts": _ts(),
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "suggested_reply": draft,
                    "action": "skip",
                    "sent_text": None,
                })
                print("Skipping the chat.")
                break

            elif choice == 'q':
                _append_log(log_path, {
                    "ts": _ts(),
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "suggested_reply": draft,
                    "action": "quit",
                    "sent_text": None,
                })
                print("End of session.")
                return

            else:
                print("Unknown option. Type s / e / r / n / q.")


def main(argv=None) -> None:
    # Everything Bob writes (history, log.jsonl, user-profiles, watch-state, token
    # cache, temp images) holds chat content or credentials: owner-only by default.
    # Inherited by children (claude CLI, MCP servers).
    os.umask(0o077)
    args = _parse_args(argv)
    if args.poll < 1:
        args.poll = 1  # floor — lower makes no sense, the Teams API is rate limited

    # Session check / re-login.
    is_fresh = ensure_fresh_session()
    if (not is_fresh or args.force_login) and not sys.stdin.isatty():
        # started by Bob's panel (no terminal): login.py would wait for an Enter nobody can press. The panel
        # logs in itself ("Sign in to Teams", login.py --done-file) and only then starts Bob.
        print("ERROR: the Teams session expired - sign in from Bob's panel: "
              f"http://localhost:{os.environ.get('BOB_PANEL_PORT') or '3006'} (Sign in to Teams).",
              file=sys.stderr, flush=True)
        sys.exit(4)
    if not is_fresh or args.force_login:
        if args.force_login and SESSION_FILE.exists():
            SESSION_FILE.unlink(missing_ok=True)
        print("Starting the SSO sign-in...")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "login.py")],
        )
        if result.returncode != 0:
            print("SSO sign-in failed. Try again.", file=sys.stderr)
            sys.exit(1)
        is_fresh = True

    # Import Playwright lazily so the module can be imported in tests without Playwright.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed. Run: pip install playwright", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, args=["--proxy-auto-detect", "--ignore-certificate-errors"])
        context = browser.new_context(storage_state=str(SESSION_FILE))
        page = context.new_page()

        # Always intercept Bearer token from browser — needed for REST API calls.
        _attach_token_interceptor(page)
        # Separate ic3-audience token -- needed for AMS uploads of [BOB_GIF] gifs.
        _attach_ic3_token_interceptor(page)

        if args.sniff_api:
            _start_api_sniffer(page)

        t0 = _time_mod.time()
        print("Opening Teams...", file=sys.stderr, flush=True)
        page.goto(TEAMS_URL, wait_until="domcontentloaded")
        print(f"DOM loaded ({_time_mod.time() - t0:.1f}s). Waiting for the Teams app shell...",
              file=sys.stderr, flush=True)

        # Race several Teams-ready signals via a single JS poll. networkidle is
        # too slow because Teams keeps a busy WebSocket open; we want the FIRST
        # signal that the SPA has actually mounted, not network quiet.
        teams_ready_js = """() => {
          const sels = [
            '[data-tid="left-rail"]',
            '[data-tid="app-bar"]',
            '[data-tid="chat-list"]',
            '[data-tid*="chat-list-item"]',
            '[data-tid="chat-pane-list"]',
            'nav[aria-label*="rail" i]',
            'nav[aria-label*="chat" i]',
            'aside[aria-label*="chat" i]',
            'nav[aria-label*="czat" i]',
            'aside[aria-label*="czat" i]',
            '[role="navigation"][aria-label]',
            '[role="application"]',
          ];
          return sels.some(s => {
            const el = document.querySelector(s);
            return !!(el && el.offsetParent !== null);
          });
        }"""
        try:
            page.wait_for_function(teams_ready_js, timeout=30000)
            print(f"Teams app shell ready ({_time_mod.time() - t0:.1f}s)",
                  file=sys.stderr, flush=True)
        except Exception:
            print(f"Teams app shell not detected in 30s ({_time_mod.time() - t0:.1f}s) — "
                  f"continuing anyway", file=sys.stderr, flush=True)

        # Small safety buffer for late-rendering chat-list items.
        page.wait_for_timeout(800)

        # Validate session: if Teams bounced us back to a login domain, the
        # cookies in session.json did not survive Teams' device-trust check.
        # Most common cause: --headless was passed; Teams discards persisted
        # auth in headless Chromium.  Fail fast with a clear hint instead of
        # polling selectors that will never exist.
        login_fragments = (
            "login.microsoftonline.com",
            "login.microsoft.com",
            "account.activedirectory.windowsazure.com",
            "login.live.com",
        )

        def _on_login_page() -> bool:
            return any(frag in (page.url or "").lower() for frag in login_fragments)

        if _on_login_page():
            print(
                f"\nERROR: Teams redirected to the sign-in page (URL: {page.url}).",
                file=sys.stderr,
            )
            if args.headless:
                print(
                    "It looks like --headless is to blame. Run again "
                    "WITHOUT --headless (the default):\n"
                    "  python scripts/teams_agent/cli.py",
                    file=sys.stderr,
                )
            else:
                print(
                    "The Teams session is invalid or expired. Sign in again:\n"
                    "  python scripts/teams_agent/cli.py --force-login",
                    file=sys.stderr,
                )
            try:
                browser.close()
            except Exception:
                pass
            sys.exit(3)

        try:
            if args.sniff_ws:
                # Passive WebSocket sniff — attach listener then navigate, send nothing.
                _start_ws_sniffer(page)
                _run_ws_sniff_loop(page, args)
                return

            if not args.interactive:
                # Default: daemon mode — watch the target chat and auto-reply.
                _run_watch_loop(page, args)
                return

            # --interactive: manual approval loop (original behaviour).
            chats = scraper.list_chats(page)
            if not chats:
                print("No chats to process.")
                return  # finally block below closes the browser

            print(f"Found {len(chats)} chats. Processing up to {args.max_chats}.")
            _run_approval_loop(page, chats, args)

        except KeyboardInterrupt:
            print("\nInterrupted by the user (Ctrl-C).")
            _append_log(args.log_file, {
                "ts": _ts(),
                "chat_id": None,
                "chat_name": None,
                "suggested_reply": None,
                "action": "aborted",
                "sent_text": None,
            })
        finally:
            import contextlib
            import io
            # Keep the live session for the next start: a restart from the panel must not need a new
            # SSO login. session.json is otherwise saved only by the 6-hourly SharePoint warm-up. The
            # watch loop may have swapped the context, so take the newest one with a Teams page.
            for _c in reversed(browser.contexts):
                if any("teams" in (pg.url or "") for pg in _c.pages):
                    try:
                        save_storage_state(_c)
                        print("Session saved (session.json).", file=sys.stderr, flush=True)
                    except Exception as exc:  # noqa: BLE001 -- never block the shutdown
                        print(f"Session not saved: {exc}", file=sys.stderr, flush=True)
                    break
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    browser.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
