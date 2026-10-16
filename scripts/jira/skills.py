"""Bob's skill registry — callable tools Claude can invoke to fetch live data.

Each skill is a function that takes typed params and returns a plain-text result
block ready to be injected back into Claude's context.

Claude signals skill calls via:
    <skills>[{"name": "...", "params": {...}}, ...]</skills>

Python parses this, executes each skill, and feeds results back.
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_client as _jira  # noqa: E402,I001


# ---------------------------------------------------------------------------
# Skill descriptor shown to Claude in every prompt
# ---------------------------------------------------------------------------

SKILLS_PROMPT = """
Masz dostęp do następujących skilli. Wywołaj je gdy potrzebujesz danych na żywo.

Aby wywołać skille, odpowiedz TYLKO tym blokiem (bez żadnego innego tekstu):
<skills>[{"name": "nazwa", "params": {...}}, ...]</skills>

Możesz wywołać wiele skilli naraz w jednej tablicy.
Jeśli nie potrzebujesz danych — odpowiedz normalnie bez bloku <skills>.

DOSTĘPNE SKILLE:

sprint(team: str)
  Aktywny sprint dla teamu. team: "TeamA" | "TeamD" | "TeamB" | "all"
  Zwraca: committed/done/wip SP, listę tasków z assignee i statusem.
  Przykład: {"name": "sprint", "params": {"team": "TeamA"}}

tempo(date_from: str, date_to: str, person?: str)
  Worklogi z Tempo za podany okres. Daty w formacie YYYY-MM-DD.
  person: opcjonalnie filtruj po fragmencie nazwiska (np. "Nowak").
  Przykład: {"name": "tempo", "params": {"date_from": "2026-05-01", "date_to": "2026-05-31", "person": "Nowak"}}

issue(key: str)
  Szczegóły konkretnego ticketu: status, assignee, SP, opis, ostatnie komentarze.
  Przykład: {"name": "issue", "params": {"key": "PRJ1-123"}}

search(jql: str, max_results?: int)
  Wyszukiwanie JQL. max_results domyślnie 15.
  Przykład: {"name": "search", "params": {"jql": "assignee = currentUser() AND status != Done"}}

find(query: str)
  Szukaj po tekście w summary/description.
  Przykład: {"name": "find", "params": {"query": "migracja FieldOps"}}

my_issues()
  Moje otwarte taski (przypisane do currentUser, nie Done).
  Przykład: {"name": "my_issues", "params": {}}
""".strip()


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

def _skill_sprint(team: str = "all") -> str:
    if team.lower() == "all":
        results = _jira.get_all_teams_sprint_stats()
    else:
        canonical = next((t for t in _jira.TEAMS if t.lower() == team.lower()), team)
        r = _jira.get_sprint_stats(canonical)
        results = [r] if r else []

    if not results:
        return f"Nie znaleziono aktywnego sprintu dla teamu: {team}"

    parts = []
    for s in results:
        lines = [
            f"Sprint: {s['sprint_name']} ({s['sprint_state']}) {s['start_date']} → {s['end_date']}",
            f"  Committed: {s['committed_sp']} SP | Done: {s['done_sp']} SP ({s['completion_pct']}%) "
            f"| WIP: {s['wip_sp']} SP | Todo: {s['todo_sp']} SP",
        ]
        wip = [i for i in s["issues"] if "progress" in i["status"].lower() or
               "realizacj" in i["status"].lower() or "trakcie" in i["status"].lower()]
        done = [i for i in s["issues"] if i["status"].lower() in
                ("done", "gotowe", "closed", "resolved", "ukończone")]
        if wip:
            lines.append(f"  W trakcie ({len(wip)}):")
            for i in wip[:8]:
                lines.append(f"    {i['key']} ({i['sp']} SP) [{i['assignee']}] {i['summary']}")
        if done:
            lines.append(f"  Ukończone ({len(done)}):")
            for i in done[:8]:
                lines.append(f"    {i['key']} ({i['sp']} SP) [{i['assignee']}] {i['summary']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _skill_tempo(date_from: str, date_to: str, person: str = "") -> str:
    summary = _jira.get_tempo_summary(date_from, date_to)
    if not summary.get("total_hours"):
        return f"Brak worklogów w Tempo za okres {date_from} → {date_to}."

    lines = [f"Tempo {date_from} → {date_to} | łącznie: {summary['total_hours']}h"]
    for name, hours in summary["by_person"].items():
        if person and person.lower() not in name.lower():
            continue
        lines.append(f"  {name}: {hours}h")

    if person and len(lines) == 1:
        return f"Nie znaleziono worklogów dla '{person}' w okresie {date_from} → {date_to}."
    return "\n".join(lines)


def _skill_issue(key: str) -> str:
    issue = _jira.get_issue(key)
    if not issue:
        return f"Nie znaleziono ticketu: {key}"
    lines = [
        f"{issue['key']}: {issue['summary']}",
        f"  Status: {issue['status']} | SP: {issue['sp']} | Typ: {issue['type']}",
        f"  Assignee: {issue['assignee'] or '—'}",
        f"  URL: {issue['url']}",
    ]
    if issue.get("description"):
        lines.append(f"  Opis: {issue['description'][:400]}")
    if issue.get("comments"):
        lines.append("  Ostatnie komentarze:")
        for c in issue["comments"]:
            lines.append(f"    [{c['author']}]: {c['body'][:300]}")
    return "\n".join(lines)


def _skill_search(jql: str, max_results: int = 15) -> str:
    issues = _jira.search(jql, max_results=max_results)
    if not issues:
        return f"Brak wyników dla JQL: {jql}"
    lines = [f"Wyniki ({len(issues)}):"]
    for i in issues:
        lines.append(f"  {i['key']} [{i['status']}] {i['sp'] or '?'} SP "
                     f"[{i['assignee'] or '—'}] {i['summary']}")
    return "\n".join(lines)


def _skill_find(query: str) -> str:
    issues = _jira.find_issue(query, max_results=10)
    if not issues:
        return f"Brak wyników dla: {query}"
    lines = [f"Znalezione ({len(issues)}):"]
    for i in issues:
        lines.append(f"  {i['key']} [{i['status']}] {i['summary']}")
    return "\n".join(lines)


def _skill_my_issues() -> str:
    issues = _jira.get_my_issues(max_results=15)
    if not issues:
        return "Brak otwartych tasków przypisanych do Ciebie."
    lines = [f"Twoje otwarte taski ({len(issues)}):"]
    for i in issues:
        lines.append(f"  {i['key']} [{i['status']}] {i['sp'] or '?'} SP {i['summary']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Any] = {
    "sprint": _skill_sprint,
    "tempo": _skill_tempo,
    "issue": _skill_issue,
    "search": _skill_search,
    "find": _skill_find,
    "my_issues": _skill_my_issues,
}


def execute(name: str, params: dict) -> str:
    """Run a skill by name with given params. Returns result string."""
    fn = _REGISTRY.get(name)
    if not fn:
        return f"[skill '{name}' nie istnieje]"
    try:
        return fn(**params)
    except Exception as exc:
        return f"[skill '{name}' błąd: {exc}]"
