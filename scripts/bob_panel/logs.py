"""Bob's log (user-data/bob-panel/bob.log) made readable for the panel's "Logs" tab.

The log is Bob's stdout/stderr - about 5 000 lines a day, most of it routine (polling, health checks, token
refresh, per-chat start-up lines). Instead of the raw tail this parses every line into
{ts, source, level, chat, text}: multi-line blocks (Playwright "Call log:", indented lines, tracebacks) are
glued to their line, consecutive repeats of the same message (numbers / ids normalised) collapse into one entry
"×N, from-to", and lines are grouped by run (a "===== start ... =====" marker per Bob start). Levels come from
the text: error / warn / info / debug (debug = routine noise, hidden by default). Nothing in Bob changes.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from bob_panel import supervisor

MAX_BYTES = 3_000_000
_START_RE = re.compile(r"^===== start (\S+) \((.*)\) =====$")
_TS_RE = re.compile(r"^(\d\d):(\d\d):(\d\d) ?(.*)$")
_SRC_RE = re.compile(r"^\[([\w./-]+)\]\s*")
_CHAT_RE = re.compile(r"\[([^|\]]+?) \|")

ERROR_RE = re.compile(r"\bblad\b|\bBLAD\b|błąd|Traceback|Exception|timed? ?out|Timeout \d+ms|connection error|"
                      r"\bstatus=5\d\d|\(5\d\d\)|refused|nie powiod|nieudan|odrzucon", re.I)
WARN_RE = re.compile(r"OSTRZEZENIE|ostrzeżenie|\bretry\b|ponawiam|ponowie|wygasl|stuck|relaunch", re.I)
# routine lines: polling, health checks, start-up listing, token refresh - hidden unless asked for
DEBUG_RE = re.compile(
    r"pomijam stare wiadomosci|: caller = |Historia '.*' wpis|\+0 wiad, \+0 obrazow|"
    r"edit_message \(200\)|wiadomosc edytowana|wyslano \(20\d\)|"
    r"Token refresh|Odswiezam Bearer|click_any_other_chat|token BEZ ZMIANY|Aktywuje chat|demon MCP .*: dziala|"
    r"-> historia:|prompt \d+ch -> claude|ingest-only|Rate-limit|Polling co|Ctrl-C aby|silent_sentinel|"
    r"sharepoint_poll|token zaktualizowany|Bearer token OK|^\s*$")
_ROUTINE_WARN_RE = re.compile(r"token BEZ ZMIANY po nawigacji")
_STOP_RE = re.compile(r"Przerwano \(Ctrl-C\)|KeyboardInterrupt")
_TRANSIENT_RE = re.compile(r"get_messages blad \[(ReadTimeout|ConnectionError|ChunkedEncodingError)|"
                           r"_post blad \[ConnectionError status=None\] — retry")
_NORM_RE = re.compile(r"\d+([.,]\d+)?|[0-9a-f]{8,}", re.I)
SOURCES = {"watch": "Czaty", "api": "Teams API", "teams/ai": "Claude", "stats": "Statystyki", "teams": "Teams",
           "control": "Sterowanie", "broadcast": "Harmonogram", "start": "Start Boba", "bob": "Bob"}


def _source(text: str) -> tuple[str, str]:
    m = _SRC_RE.match(text)
    if not m:
        return "bob", text
    return m.group(1), text[m.end():]


def level_of(text: str) -> str:
    if _STOP_RE.search(text):                 # Bob stopped by a restart: Ctrl-C and its traceback are expected
        return "info"
    if _TRANSIENT_RE.search(text):            # Teams did not answer once - Bob retries on its own
        return "warn"
    if ERROR_RE.search(text):
        return "error"
    if _ROUTINE_WARN_RE.search(text):        # "warnings" printed on every refresh - not actionable alone
        return "debug"
    if WARN_RE.search(text):
        return "warn"
    if DEBUG_RE.search(text):
        return "debug"
    return "info"


def parse(raw: str, today: date | None = None) -> list[dict]:
    """Lines -> entries in order. Times get dates from the run marker (and roll over at midnight)."""
    entries: list[dict] = []
    day = today or date.today()
    run = None
    last_t = None
    for line in raw.splitlines():
        m = _START_RE.match(line.strip())
        if m:
            try:
                started = datetime.fromisoformat(m.group(1))
                day, last_t = started.date(), started.time()
            except ValueError:
                started = None
            run = {"start": m.group(1), "by": m.group(2)}
            entries.append({"ts": m.group(1), "source": "start", "level": "info", "chat": None,
                            "text": f"Start Boba ({m.group(2)})", "run": run["start"], "extra": []})
            continue
        m = _TS_RE.match(line)
        if not m:                                   # continuation: Call log, traceback, indented details
            if entries and line.strip():
                if not entries[-1]["text"].strip():
                    entries[-1]["text"] = line.strip()[:600]
                    continue
                entries[-1]["extra"].append(line.rstrip()[:400])
                if line.lstrip().startswith(("Traceback", "Error", "Exception")) and \
                        not _STOP_RE.search(entries[-1]["text"]):
                    entries[-1]["level"] = "error"
                if _STOP_RE.search(line):
                    entries[-1]["level"] = "info"
            continue
        try:
            t = datetime.strptime(":".join(m.group(1, 2, 3)), "%H:%M:%S").time()
        except ValueError:                          # "25:61:00 ..." - not a timestamp, a continuation line
            if entries:
                entries[-1]["extra"].append(line.rstrip()[:400])
            continue
        if last_t is not None and t < last_t and (datetime.combine(day, last_t) - datetime.combine(day, t)) > \
                timedelta(hours=1):
            day += timedelta(days=1)
        last_t = t
        src, text = _source(m.group(4))
        cm = _CHAT_RE.search(text)
        entries.append({"ts": datetime.combine(day, t).isoformat(), "source": src, "level": level_of(m.group(4)),
                        "chat": cm.group(1).strip() if cm else None, "text": text[:600],
                        "run": run["start"] if run else None, "extra": []})
    return entries


def collapse(entries: list[dict]) -> list[dict]:
    """Consecutive entries with the same source and normalised text -> one entry with count and last time."""
    out: list[dict] = []
    for e in entries:
        key = (e["source"], e["level"], _NORM_RE.sub("#", e["text"]))
        if out and out[-1]["_key"] == key and not e["extra"]:
            out[-1]["count"] += 1
            out[-1]["last"] = e["ts"]
            continue
        out.append({**e, "_key": key, "count": 1, "last": e["ts"]})
    for e in out:
        e.pop("_key", None)
    return out


def view(hours: float = 6, level: str = "info", source: str = "", q: str = "") -> dict:
    """The log tab: entries of the last `hours` at `level` or above, filtered, collapsed, newest last; problem
    summary of the last 24 h; counters for the filters."""
    try:
        with open(supervisor.LOG_FILE, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - MAX_BYTES))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return {"entries": [], "problems": [], "counts": {}, "sources": {}, "runs": []}
    allE = parse(raw)
    if size > MAX_BYTES:
        # the tail starts mid-run: dates of lines before the first "===== start" are guesses - drop them
        first = next((i for i, e in enumerate(allE) if e["source"] == "start" and e["text"].startswith("Start")), None)
        allE = allE[first:] if first is not None else allE
    now = datetime.now()
    since = (now - timedelta(hours=hours)).isoformat() if hours else ""
    day_ago = (now - timedelta(hours=24)).isoformat()
    order = ["debug", "info", "warn", "error"]
    min_i = order.index(level) if level in order else 1
    win = [e for e in allE if e["ts"] >= since]
    counts = {lv: sum(1 for e in win if e["level"] == lv) for lv in order}
    sources: dict[str, int] = {}
    for e in win:
        sources[e["source"]] = sources.get(e["source"], 0) + 1
    ql = q.strip().lower()
    shown = [e for e in win if order.index(e["level"]) >= min_i and (not source or e["source"] == source)
             and (not ql or ql in e["text"].lower() or ql in " ".join(e["extra"]).lower())]
    problems = collapse_by_text([e for e in allE if e["ts"] >= day_ago and e["level"] in ("error", "warn")])
    runs = [{"start": e["ts"], "by": e["text"]} for e in allE
            if e["source"] == "start" and e["text"].startswith("Start")]
    return {"entries": collapse(shown)[-800:], "problems": problems[:30], "counts": counts,
            "sources": dict(sorted(sources.items(), key=lambda kv: -kv[1])), "runs": runs[-10:],
            "labels": SOURCES, "total": len(win)}


def collapse_by_text(entries: list[dict]) -> list[dict]:
    """Problems of the day grouped by message (not only consecutive): count, first, last - errors first."""
    groups: dict[tuple, dict] = {}
    for e in entries:
        key = (e["level"], e["source"], _NORM_RE.sub("#", e["text"].split(" | ")[0].split(":")[0])[:90])
        g = groups.get(key)
        if g:
            g["count"] += 1
            g["last"] = e["ts"]
        else:
            groups[key] = {**e, "count": 1, "first": e["ts"], "last": e["ts"]}
    newest = sorted(groups.values(), key=lambda g: g["last"], reverse=True)
    return sorted(newest, key=lambda g: g["level"] != "error")        # stable: errors first, newest first
