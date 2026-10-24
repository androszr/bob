"""Jira REST client — read-only helper for Bob.

Reads ATLASSIAN_TOKEN from .env in project root. Base URL, board, project and teams
come from scripts/org_config.py (JIRA_URL / JIRA_BOARD_ID / JIRA_PROJECT / JIRA_TEAMS).
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import org_config  # noqa: E402 — also loads .env from the project root

_ENV_FILE = org_config.ENV_FILE

JIRA_URL = org_config.JIRA_URL
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() != "false"
BOARD_ID = org_config.JIRA_BOARD_ID
PROJECT = org_config.JIRA_PROJECT
TEAMS = list(org_config.JIRA_TEAMS)

FIELD_SP = "customfield_10008"

DEFAULT_FIELDS = f"summary,status,assignee,priority,issuetype,{FIELD_SP}"


def _read_env_fresh(key: str, default: str = "") -> str:
    """Read `key` fresh from .env on every call (falling back to the process
    environment, then `default`) instead of a value frozen at import time.

    This module is imported once into long-running MCP daemons
    (scripts/jira/mcp_server.py --http). Reading ATLASSIAN_TOKEN into a
    module-level constant at import time would freeze it: a token rotated in
    .env would have no effect until the daemon restarted, and stale credentials
    surface as 401 Unauthorized on reads and misleading field-permission
    errors ("cannot be set, not on the appropriate screen") on create.
    """
    if _ENV_FILE.exists():
        try:
            for _l in _ENV_FILE.read_text(encoding="utf-8").splitlines():
                _l = _l.strip()
                if _l and not _l.startswith("#") and "=" in _l:
                    _k, _, _v = _l.partition("=")
                    if _k.strip() == key:
                        return _v.strip()
        except OSError:
            pass
    return os.environ.get(key, default)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_read_env_fresh('ATLASSIAN_TOKEN')}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def __getattr__(name: str):
    """PEP 562 module __getattr__: computes _HEADERS fresh on every access
    instead of it being a plain module-level constant frozen at import time
    (see _read_env_fresh docstring). Only triggers when _HEADERS is not
    already set directly in this module's __dict__ -- tests that do
    `jira_client_stub._HEADERS = {...}` keep working unchanged, since a direct
    attribute assignment is found before __getattr__ is ever consulted.
    """
    if name == "_HEADERS":
        return _headers()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Core HTTP
# ---------------------------------------------------------------------------

def _get(path: str, params: dict | None = None) -> dict | list | None:
    try:
        r = requests.get(
            f"{JIRA_URL}{path}",
            headers=_headers(),
            params=params or {},
            timeout=20,
            verify=VERIFY_SSL,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"[jira] GET {path} blad: {exc}", file=sys.stderr)
        return None


def _search(jql: str, fields: str = DEFAULT_FIELDS, max_results: int = 100) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []
    start = 0
    while True:
        data = _get("/rest/api/2/search", {
            "jql": jql, "fields": fields,
            "maxResults": min(max_results - len(results), 100),
            "startAt": start,
        })
        if not data:
            break
        issues = data.get("issues", [])
        for i in issues:
            key = i.get("key", "")
            if key not in seen:
                seen.add(key)
                results.append(i)
        if len(results) >= max_results or start + len(issues) >= data.get("total", 0):
            break
        start += len(issues)
    return results


# ---------------------------------------------------------------------------
# Sprint discovery
# ---------------------------------------------------------------------------

def get_sprints(state: str = "active,closed", max_results: int = 50) -> list[dict]:
    """Return sprints from the board matching given state(s); [] when JIRA_BOARD_ID is unset."""
    if BOARD_ID is None:
        print("[jira] JIRA_BOARD_ID not configured — sprint lookup skipped", file=sys.stderr, flush=True)
        return []
    data = _get(f"/rest/agile/1.0/board/{BOARD_ID}/sprint", {
        "state": state, "maxResults": max_results,
    })
    return data.get("values", []) if data else []


def find_team_sprint(team: str, state: str = "active") -> dict | None:
    """Find the sprint for a given team name (e.g. 'TeamA')."""
    import re
    pattern = re.compile(rf"^{re.escape(team)}\s+Sprint\s+\d+$", re.IGNORECASE)
    for sprint in get_sprints(state=state, max_results=100):
        if pattern.match(sprint.get("name", "")):
            return sprint
    return None


# ---------------------------------------------------------------------------
# Sprint issues & stats
# ---------------------------------------------------------------------------

def get_sprint_issues(sprint_id: int, fields: str = DEFAULT_FIELDS) -> list[dict]:
    """All non-subtask issues in a sprint."""
    jql = (
        f"sprint = {sprint_id} AND project = {PROJECT} "
        f"AND issuetype NOT IN (Podzadanie, \"Bug sub-task\") "
        f"ORDER BY status ASC"
    )
    return _search(jql, fields=fields)


def get_sprint_stats(team: str) -> dict | None:
    """Return sprint progress summary for a team: committed/done/wip/todo SP."""
    sprint = find_team_sprint(team, state="active")
    if not sprint:
        sprint = find_team_sprint(team, state="closed")
        if not sprint:
            return None

    issues = get_sprint_issues(sprint["id"], fields=f"summary,status,assignee,{FIELD_SP},issuetype")

    committed = done = wip = todo = 0
    issue_list = []
    for raw in issues:
        f = raw.get("fields") or {}
        sp = f.get(FIELD_SP) or 0
        status_cat = (f.get("status") or {}).get("statusCategory", {}).get("key", "")
        committed += sp
        if status_cat == "done":
            done += sp
        elif status_cat == "indeterminate":
            wip += sp
        else:
            todo += sp
        issue_list.append({
            "key": raw.get("key"),
            "summary": (f.get("summary") or "")[:80],
            "sp": sp,
            "status": (f.get("status") or {}).get("name", ""),
            "assignee": ((f.get("assignee") or {}).get("displayName") or "—"),
        })

    completion_pct = round(done / committed * 100) if committed else 0
    return {
        "team": team,
        "sprint_name": sprint.get("name"),
        "sprint_state": sprint.get("state"),
        "start_date": (sprint.get("startDate") or "")[:10],
        "end_date": (sprint.get("endDate") or "")[:10],
        "committed_sp": committed,
        "done_sp": done,
        "wip_sp": wip,
        "todo_sp": todo,
        "completion_pct": completion_pct,
        "issues": issue_list,
    }


def get_all_teams_sprint_stats() -> list[dict]:
    """Sprint stats for all configured teams in parallel."""
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(get_sprint_stats, TEAMS))
    return [r for r in results if r]


# ---------------------------------------------------------------------------
# Tempo worklogs
# ---------------------------------------------------------------------------

def get_tempo_worklogs(date_from: str, date_to: str, project_key: str = PROJECT) -> list[dict]:
    """Fetch raw Tempo worklogs for a date range."""
    results: list[dict] = []
    offset = 0
    limit = 1000
    seen_ids: set = set()
    max_pages = 20  # safety cap — avoids infinite loops when API ignores offset

    for _ in range(max_pages):
        data = _get("/rest/tempo-timesheets/3/worklogs", {
            "projectKey": project_key,
            "dateFrom": date_from,
            "dateTo": date_to,
            "offset": offset,
            "limit": limit,
        })
        if not data:
            break
        entries = data if isinstance(data, list) else data.get("worklogs", [])
        if not entries:
            break

        new = [e for e in entries if e.get("id") not in seen_ids]
        if not new:
            break  # API returned the same page again — stop
        seen_ids.update(e.get("id") for e in new)
        results.extend(new)

        if len(entries) < limit:
            break
        offset += limit

    return results


def get_tempo_summary(date_from: str, date_to: str) -> dict:
    """Summarise logged hours per person for a date range."""
    logs = get_tempo_worklogs(date_from, date_to)
    by_person: dict[str, float] = {}
    for log in logs:
        name = (log.get("author") or {}).get("displayName") or log.get("username", "?")
        hours = (log.get("timeSpentSeconds") or 0) / 3600
        by_person[name] = by_person.get(name, 0) + hours
    total = sum(by_person.values())
    return {
        "date_from": date_from,
        "date_to": date_to,
        "total_hours": round(total, 1),
        "by_person": {k: round(v, 1) for k, v in sorted(by_person.items(), key=lambda x: -x[1])},
    }


# ---------------------------------------------------------------------------
# Single issue & search
# ---------------------------------------------------------------------------

def get_issue(key: str) -> dict | None:
    data = _get(f"/rest/api/2/issue/{key}", {"fields": f"{DEFAULT_FIELDS},description,comment"})
    return _simplify(data) if data else None


def get_issue_brief(key: str, max_desc: int = 3000) -> dict | None:
    """What a reply to the reporter needs: summary, a longer description, reporter, created."""
    data = _get(f"/rest/api/2/issue/{key}", {"fields": "summary,description,reporter,created"})
    if not data:
        return None
    f = data.get("fields") or {}
    return {
        "key": data.get("key", key),
        "summary": f.get("summary") or "",
        "opis": (f.get("description") or "").strip()[:max_desc],
        "zglaszajacy": (f.get("reporter") or {}).get("displayName") or "",
        "utworzono": f.get("created") or "",
    }


# Org custom fields read by jira-bob's jira_issue (scripts/jira/mcp_server.py) —
# {customfield_id: label}, from config/jira-fields.json ("extra_custom_fields").
JIRA_FIELDS = org_config.load_json_config(
    "JIRA_FIELDS_FILE", org_config.REPO_ROOT / "config" / "jira-fields.json",
)
EXTRA_CUSTOM_FIELDS: dict[str, str] = dict(JIRA_FIELDS.get("extra_custom_fields") or {})


def _extract_field_value(raw):
    """Unwrap a raw Jira field value into a plain, human-readable form."""
    if isinstance(raw, dict):
        return raw.get("value") or raw.get("name") or raw.get("id")
    if isinstance(raw, list):
        return [_extract_field_value(item) for item in raw]
    return raw


def search(jql: str, max_results: int = 20) -> list[dict]:
    return [_simplify(i) for i in _search(jql, max_results=max_results)]


def find_issue(query: str, max_results: int = 10) -> list[dict]:
    safe = query.replace('"', '\\"')
    return search(f'text ~ "{safe}" ORDER BY updated DESC', max_results=max_results)


def get_my_issues(max_results: int = 15) -> list[dict]:
    return search(
        "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
        max_results=max_results,
    )


def _simplify(raw: dict) -> dict:
    f = raw.get("fields") or {}
    desc = (f.get("description") or "").strip()[:400]
    comments_raw = (f.get("comment") or {}).get("comments", [])[-3:]
    comments = [
        {"author": (c.get("author") or {}).get("displayName", "?"),
         "body": (c.get("body") or "").strip()[:250]}
        for c in comments_raw
    ]
    return {
        "key": raw.get("key", ""),
        "summary": (f.get("summary") or ""),
        "status": ((f.get("status") or {}).get("name") or ""),
        "assignee": ((f.get("assignee") or {}).get("displayName") or ""),
        "sp": f.get(FIELD_SP),
        "type": ((f.get("issuetype") or {}).get("name") or ""),
        "description": desc,
        "comments": comments,
        "url": f"{JIRA_URL}/browse/{raw.get('key', '')}",
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    for team in TEAMS:
        stats = get_sprint_stats(team)
        if stats:
            print(f"\n{stats['sprint_name']} ({stats['sprint_state']})")
            print(f"  Done: {stats['done_sp']} SP / {stats['committed_sp']} SP ({stats['completion_pct']}%)")
            print(f"  WIP: {stats['wip_sp']} SP | Todo: {stats['todo_sp']} SP")
