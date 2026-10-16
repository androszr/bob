"""Jira/Tempo context enrichment for Bob's prompt.

Detects what the user is asking about and fetches the right data:
- Ticket key (PRJ1-123) → issue details
- Sprint / team question → sprint stats
- Tempo / worklogi → tempo summary for current week/month
- Free-text search → top matching issues
"""

import re
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_client as _jira

# Polish/English month names → month number
_MONTHS = {
    "styczeń": 1, "stycznia": 1, "january": 1, "jan": 1,
    "luty": 2, "lutego": 2, "february": 2, "feb": 2,
    "marzec": 3, "marca": 3, "march": 3, "mar": 3,
    "kwiecień": 4, "kwietnia": 4, "april": 4, "apr": 4,
    "maj": 5, "maja": 5, "maju": 5, "may": 5,
    "czerwiec": 6, "czerwca": 6, "june": 6, "jun": 6,
    "lipiec": 7, "lipca": 7, "july": 7, "jul": 7,
    "sierpień": 8, "sierpnia": 8, "august": 8, "aug": 8,
    "wrzesień": 9, "września": 9, "september": 9, "sep": 9,
    "październik": 10, "października": 10, "october": 10, "oct": 10,
    "listopad": 11, "listopada": 11, "november": 11, "nov": 11,
    "grudzień": 12, "grudnia": 12, "december": 12, "dec": 12,
}

_MONTH_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in _MONTHS) + r')\b',
    re.IGNORECASE,
)


def _tempo_date_range(message: str) -> tuple[str, str]:
    """Infer date range from message. Returns (date_from, date_to) strings."""
    today = date.today()

    # Explicit month name
    m = _MONTH_RE.search(message)
    if m:
        month_n = _MONTHS[m.group(1).lower()]
        year = today.year if month_n <= today.month else today.year - 1
        last_day = monthrange(year, month_n)[1]
        end = date(year, month_n, last_day)
        # Don't go past today
        if end > today:
            end = today
        return str(date(year, month_n, 1)), str(end)

    # "zeszły miesiąc" / "poprzedni miesiąc"
    if re.search(r'\b(zeszł|poprzedni|last\s+month)\b', message, re.IGNORECASE):
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return str(last_prev.replace(day=1)), str(last_prev)

    # Default: current month so far
    return str(today.replace(day=1)), str(today)

# Jira ticket key pattern.
_KEY_RE = re.compile(r'\b([A-Z]{2,10}-\d+)\b')

# Team names.
_TEAM_RE = re.compile(r'\b(TeamA|TeamD|TeamB)\b', re.IGNORECASE)

# Sprint-related keywords.
_SPRINT_RE = re.compile(
    r'\b(sprint|sprincie|sprintu|sprintem|story\s*point[sy]?|SP\b|velocity|burndown|'
    r'zrealizowali|ile\s+done|ile\s+sko[nń]czyli|postęp|progress|aktywny\s+sprint)\b',
    re.IGNORECASE,
)

# Tempo / time-logging keywords.
_TEMPO_RE = re.compile(
    r'\b(tempo|worklog|zalogowali|czas\s+pracy|logowali|godzin[ay]?|ile\s+zalogowali|'
    r'ile\s+godzin|przepracowali|logowanie)\b',
    re.IGNORECASE,
)

# Generic Jira question keywords (no specific ticket).
_JIRA_RE = re.compile(
    r'\b(jira|task[iy]?|ticket[sy]?|issue[sy]?|backlog|story|bug[iy]?|epic[iy]?|'
    r'co\s+masz\s+w\s+sprincie|co\s+robimy|co\s+jest\s+do\s+zrobienia)\b',
    re.IGNORECASE,
)


def build_jira_context(message: str) -> str:
    """Return a Jira/Tempo context block to inject into Bob's prompt, or '' if not relevant."""
    parts: list[str] = []

    # 1. Explicit ticket keys — always fetch.
    keys = list(dict.fromkeys(_KEY_RE.findall(message)))
    for key in keys[:3]:
        issue = _jira.get_issue(key)
        if issue:
            parts.append(_format_issue(issue))

    # 2. Sprint stats — if sprint keywords or team names present.
    teams_mentioned = [m.group(1).capitalize() for m in _TEAM_RE.finditer(message)]
    if not teams_mentioned:
        teams_mentioned = _jira.TEAMS  # default: all teams

    if _SPRINT_RE.search(message) or (teams_mentioned != _jira.TEAMS and not keys):
        for team in teams_mentioned[:3]:
            # Normalize team name to match known teams.
            canonical = next((t for t in _jira.TEAMS if t.lower() == team.lower()), None)
            if not canonical:
                continue
            stats = _jira.get_sprint_stats(canonical)
            if stats:
                parts.append(_format_sprint(stats))

    # 3. Tempo summary — if tempo keywords present.
    if _TEMPO_RE.search(message) and not keys:
        date_from, date_to = _tempo_date_range(message)
        summary = _jira.get_tempo_summary(date_from, date_to)
        if summary.get("total_hours"):
            parts.append(_format_tempo(summary))

    # 4. Generic Jira question without keys or sprint/tempo — my open issues.
    if not parts and _JIRA_RE.search(message):
        issues = _jira.get_my_issues(max_results=10)
        if issues:
            lines = [f"  {i['key']} [{i['status']}] {i['summary']}" for i in issues]
            parts.append("Moje otwarte taski w Jirze:\n" + "\n".join(lines))

    if not parts:
        return ""

    return "[JIRA/TEMPO CONTEXT — dane pobrane na żywo]\n" + "\n\n".join(parts) + "\n[/JIRA/TEMPO CONTEXT]"


def _format_sprint(s: dict) -> str:
    lines = [
        f"Sprint: {s['sprint_name']} ({s['sprint_state']}) {s['start_date']} → {s['end_date']}",
        f"  Committed: {s['committed_sp']} SP | Done: {s['done_sp']} SP ({s['completion_pct']}%)"
        f" | WIP: {s['wip_sp']} SP | Todo: {s['todo_sp']} SP",
    ]
    # Show top in-progress and done issues.
    wip = [i for i in s["issues"] if "progress" in i["status"].lower() or "realizacj" in i["status"].lower()]
    done = [i for i in s["issues"] if i["status"].lower() in ("done", "gotowe", "closed", "resolved")]
    if wip:
        lines.append("  W trakcie:")
        for i in wip[:5]:
            lines.append(f"    {i['key']} ({i['sp']} SP) [{i['assignee']}] {i['summary']}")
    if done:
        lines.append(f"  Ukończone ({len(done)} tasków):")
        for i in done[:5]:
            lines.append(f"    {i['key']} ({i['sp']} SP) {i['summary']}")
    return "\n".join(lines)


def _format_issue(i: dict) -> str:
    lines = [
        f"{i['key']}: {i['summary']}",
        f"  Status: {i['status']} | SP: {i['sp']} | Assignee: {i['assignee'] or '—'}",
        f"  URL: {i['url']}",
    ]
    if i.get("description"):
        lines.append(f"  Opis: {i['description'][:300]}")
    if i.get("comments"):
        lines.append("  Ostatnie komentarze:")
        for c in i["comments"]:
            lines.append(f"    [{c['author']}]: {c['body'][:200]}")
    return "\n".join(lines)


def _format_tempo(t: dict) -> str:
    lines = [f"Tempo — zalogowany czas {t['date_from']} → {t['date_to']} (łącznie: {t['total_hours']}h):"]
    for name, hours in list(t["by_person"].items())[:10]:
        lines.append(f"  {name}: {hours}h")
    return "\n".join(lines)
