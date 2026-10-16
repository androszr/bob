"""MCP server exposing Acme Jira & Tempo as native Claude tools.

Start: python scripts/jira/mcp_server.py
Claude connects via stdio (managed automatically by claude --mcp-config).

Reads ATLASSIAN_TOKEN from .env — same as jira_client.py.
All network calls go to https://jira.example.com (Jira + Tempo REST API).
"""

import functools
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

# Ensure jira_client is importable from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 — patches sys.stderr with CET timestamps

import anyio  # noqa: E402
import jira_client as _jira  # noqa: E402
import org_config  # noqa: E402
import requests  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

# Jeden plik, dwa serwery MCP (scripts/jira/mcp_read_server.py ustawia toolset "read"):
#   jira-read-bob -- odczyt (find, jira_issue, jira_search, jira_boards); dziala jako demon HTTP
#                    (ai._DAEMON_SERVERS), wspolny dla wszystkich czatow.
#   jira-bob      -- zapisy; zawsze stdio (ai._STDIO_FORCED_SERVERS), osobny proces per wywolanie,
#                    wiec widzi per-wiadomosciowe env (BOB_SENDER_MRI) i nie dzieli stanu miedzy czatami.
_TOOLSET = os.environ.get("JIRA_MCP_TOOLSET", "write").strip().lower()
_SERVER_NAME = "jira-read-bob" if _TOOLSET == "read" else "jira-bob"
mcp = FastMCP(_SERVER_NAME)


def _read_tool(fn):
    """Register a read tool (toolset read/all). FastMCP calls sync tools inline on the event
    loop, so in the shared daemon one slow call would block every chat -- the registered
    wrapper runs the sync function in a worker thread. The module keeps the plain sync
    function (tests call it directly)."""
    if _TOOLSET in ("read", "all"):
        @functools.wraps(fn)
        async def _threaded(*args, **kwargs):
            return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
        mcp.tool()(_threaded)
    return fn


def _write_tool(fn):
    """Register a write tool (toolset write/all) -- never exposed by the read daemon."""
    if _TOOLSET in ("write", "all"):
        mcp.tool()(fn)
    return fn

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_AFTER_S = 10.0


def _request_with_retry(
    request_fn, max_retries: int = 1, backoff_s: float = 1.0,
    retry_statuses: frozenset[int] | set[int] = _RETRYABLE_STATUS,
) -> requests.Response:
    """Call request_fn() (a zero-arg callable issuing one HTTP request) and retry
    on 429/5xx responses after a short backoff — covers transient throttling when
    several Jira writes fire back-to-back (e.g. create+attach+link in one turn).

    Honors a numeric Retry-After header if present. request_fn may raise
    requests.RequestException, which propagates to the caller unchanged
    (no retry on connection-level errors — only on HTTP status).
    """
    resp = request_fn()
    attempt = 0
    while resp.status_code in retry_statuses and attempt < max_retries:
        wait_s = backoff_s
        retry_after = resp.headers.get("Retry-After", "")
        if retry_after.isdigit():
            wait_s = min(float(retry_after), _MAX_RETRY_AFTER_S)
        time.sleep(wait_s)
        attempt += 1
        resp = request_fn()
    return resp


def _tlog(tag: str, t0: float, extra: str = "") -> None:
    ms = (time.perf_counter() - t0) * 1000
    suffix = f", {extra}" if extra else ""
    print(f"[jira-mcp] {tag} ({ms:.0f}ms{suffix})", file=sys.stderr, flush=True)


def _resolve_editmeta_value_id(
    issue_key: str, field_name: str, value_name: str,
) -> tuple[str | None, list[str]]:
    """Resolve a display name to its id for a select/version-type field's allowedValues.

    Reads /rest/api/2/issue/{key}/editmeta -- the per-issue allowed values for `field_name`
    (e.g. "priority", "fixVersions"), NOT any global list endpoint. Global lists can have
    duplicate names across unrelated schemes/projects (e.g. two different priority ids both
    named "Wysoki"), so resolving per-issue is the only reliable way to get the right id.
    Matching by id (not name) in the update payload also sidesteps a real instance quirk found
    empirically for priority: setting it via {"name": ...} returns HTTP 400 "Nazwa priorytetu
    jest niepoprawna" for at least "Sredni" even with the exact server-provided name string --
    {"id": ...} works reliably. Applied to fixVersions too for the same robustness.

    Returns (id, allowed_names): id is None if value_name doesn't match any allowed value
    (case-insensitive); allowed_names lists the valid options for the error message.
    """
    try:
        r = requests.get(
            f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}/editmeta",
            headers=_jira._HEADERS, verify=_jira.VERIFY_SSL, timeout=20,
        )
    except requests.RequestException:
        return None, []
    if r.status_code != 200:
        return None, []
    allowed = r.json().get("fields", {}).get(field_name, {}).get("allowedValues", [])
    needle = value_name.strip().lower()
    for entry in allowed:
        if str(entry.get("name", "")).strip().lower() == needle:
            return entry.get("id"), [a.get("name", "") for a in allowed]
    return None, [a.get("name", "") for a in allowed]


def _resolve_priority_id(issue_key: str, priority_name: str) -> tuple[str | None, list[str]]:
    """Resolve a priority display name to its id. See _resolve_editmeta_value_id."""
    return _resolve_editmeta_value_id(issue_key, "priority", priority_name)


def _set_priority(issue_key: str, priority_name: str) -> str:
    """Resolve priority_name to an id via _resolve_priority_id and PUT it. Returns OK or BLAD: ..."""
    import json as _json
    t0 = time.perf_counter()
    priority_id, allowed_names = _resolve_priority_id(issue_key, priority_name)
    if priority_id is None:
        options = ", ".join(allowed_names) if allowed_names else "brak danych (editmeta niedostepne)"
        return f'BLAD: nieznany priorytet "{priority_name}" dla {issue_key}. Dostepne: {options}'
    payload = _json.dumps({"fields": {"priority": {"id": priority_id}}}).encode("utf-8")
    try:
        resp = requests.put(
            f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}",
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"_set_priority HTTP {resp.status_code}", t0)
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"
    _tlog("_set_priority done", t0, f"key={issue_key} priority={priority_name} id={priority_id}")
    return "OK"


_FIX_VERSION_ACTIONS = {"add", "remove", "set"}


def _update_fix_version(issue_key: str, version_name: str, action: str = "add") -> str:
    """Resolve version_name to an id via editmeta, then add/remove/set it on fixVersions.

    action="add":    {"update": {"fixVersions": [{"add": {"id": ...}}]}}    -- keeps existing.
    action="remove": {"update": {"fixVersions": [{"remove": {"id": ...}}]}} -- keeps the rest.
    action="set":    {"fields": {"fixVersions": [{"id": ...}]}}            -- REPLACES the
                      whole list with just this one version.
    Returns OK or BLAD: ...
    """
    import json as _json
    if action not in _FIX_VERSION_ACTIONS:
        return f'BLAD: action musi byc "add", "remove" lub "set" (podano: {action!r})'
    t0 = time.perf_counter()
    version_id, allowed_names = _resolve_editmeta_value_id(issue_key, "fixVersions", version_name)
    if version_id is None:
        options = ", ".join(allowed_names) if allowed_names else "brak danych (editmeta niedostepne)"
        return f'BLAD: nieznana wersja "{version_name}" dla {issue_key}. Dostepne: {options}'
    if action == "set":
        payload = _json.dumps({"fields": {"fixVersions": [{"id": version_id}]}}).encode("utf-8")
    else:
        payload = _json.dumps(
            {"update": {"fixVersions": [{action: {"id": version_id}}]}}
        ).encode("utf-8")
    try:
        resp = requests.put(
            f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}",
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"_update_fix_version HTTP {resp.status_code}", t0)
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"
    _tlog(
        "_update_fix_version done", t0,
        f"key={issue_key} version={version_name} id={version_id} action={action}",
    )
    return "OK"


def _agile_get(path: str, params: dict | None = None) -> tuple[dict | list | None, str | None]:
    """GET Jira Agile REST API.  Returns (data, None) on success or (None, error_str)."""
    try:
        resp = requests.get(
            f"{_jira.JIRA_URL}{path}",
            headers=_jira._HEADERS,
            params=params or {},
            timeout=20,
            verify=_jira.VERIFY_SSL,
        )
    except requests.RequestException as exc:
        return None, f"problem z polaczeniem do Jiry: {exc}"
    if resp.status_code not in (200, 201):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        return None, f"Jira Agile HTTP {resp.status_code} — {detail}"
    try:
        return resp.json(), None
    except ValueError:
        return None, "Jira Agile: odpowiedz nie jest JSON (sesja SSO / strona logowania?)"


# ---------------------------------------------------------------------------
# Odczyt i komentarze -- bezposrednio do Jiry. Ksztalt odpowiedzi jest staly, bo prompty
# Boba i person na nim polegaja; jira_search zwraca tez issue_links.
# ---------------------------------------------------------------------------

_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
_COMMENT_MAX_BODY_LEN = 32000
_COMMENT_RATE_WINDOW_S = 60.0
_COMMENT_RATE_MAX = 30
_MENTION_LOGIN_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
_COMMENTS_LOG_FILE = Path(__file__).resolve().parent / "jira-comments.log.jsonl"
_ISSUE_FIELDS = (
    "summary,description,status,assignee,reporter,priority,issuetype,labels,components,"
    "fixVersions,created,updated,resolutiondate,story_points,customfield_10016,"
    "customfield_10028,customfield_10001,comment,worklog,parent,subtasks,issuelinks,timetracking"
)
_SEARCH_FIELDS = (
    "summary,status,assignee,labels,components,created,updated,priority,issuetype,"
    "parent,customfield_10001,timetracking,issuelinks"
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _recent_comment_count() -> int:
    """Comments posted in the last _COMMENT_RATE_WINDOW_S, read from the audit log.

    Kept on disk, not in memory: jira-bob runs stdio (a fresh process per claude call),
    so an in-process window would reset every time.
    """
    try:
        lines = _COMMENTS_LOG_FILE.read_text(encoding="utf-8").splitlines()[-(_COMMENT_RATE_MAX * 2):]
    except OSError:
        return 0
    cutoff = datetime.now(UTC).timestamp() - _COMMENT_RATE_WINDOW_S
    count = 0
    for line in lines:
        try:
            if datetime.fromisoformat(json.loads(line)["ts"]).timestamp() >= cutoff:
                count += 1
        except (ValueError, KeyError, TypeError):
            continue
    return count


def _jira_error(resp: requests.Response) -> str:
    """Map a non-2xx Jira response to a BLAD string (token/404/JQL errors made explicit)."""
    if resp.status_code == 401:
        return "BLAD: token Jiry nieprawidlowy lub brak (ATLASSIAN_TOKEN)"
    if resp.status_code == 403:
        return "BLAD: brak uprawnien w Jirze dla tego tokena"
    if resp.status_code == 404:
        return "BLAD: not_found"
    try:
        body = resp.json()
        msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
        detail = "; ".join(msgs) if msgs else resp.text[:300]
    except Exception:  # noqa: BLE001
        detail = resp.text[:300]
    return f"BLAD: Jira HTTP {resp.status_code} — {detail}"


def _jira_get(path: str, params: dict | None = None, timeout: int = 20) -> requests.Response:
    return _request_with_retry(lambda: requests.get(
        f"{_jira.JIRA_URL}{path}",
        headers=_jira._HEADERS,
        params=params or {},
        timeout=timeout,
        verify=_jira.VERIFY_SSL,
    ))


def _person(raw: dict | None) -> dict | None:
    return {"name": raw.get("displayName"), "email": raw.get("emailAddress")} if raw else None


def _components(raw: list | None) -> list[dict]:
    return [{"id": str(c.get("id") or ""), "name": c.get("name") or ""} for c in raw or []]


def _issue_links(raw: list | None) -> list[dict]:
    out = []
    for link in raw or []:
        other = link.get("inwardIssue") or link.get("outwardIssue") or {}
        out.append({
            "type": (link.get("type") or {}).get("name") or "",
            "direction": "inward" if link.get("inwardIssue") else "outward",
            "key": other.get("key") or "",
            "summary": (other.get("fields") or {}).get("summary") or "",
        })
    return out


def _parent(raw: dict | None) -> dict | None:
    return {"key": raw.get("key"), "summary": (raw.get("fields") or {}).get("summary") or ""} if raw else None


def _fetch_test_steps(key: str, issue_id: str | None) -> dict | None:
    """Test-case steps from QMetry (needs numeric issue id), Xray Server or Zephyr Scale.

    Returns {source, steps[]} or None when no plugin
    returned steps. redirect disabled: a missing plugin answers 302 -> login.jsp.
    """
    def _get(path: str, params: dict | None = None):
        try:
            r = requests.get(f"{_jira.JIRA_URL}{path}", headers=_jira._HEADERS, params=params or {},
                             timeout=15, verify=_jira.VERIFY_SSL, allow_redirects=False)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    if issue_id:
        body = _get("/rest/qtm/latest/teststep",
                    {"testCaseIssueId": issue_id, "maxResults": 200, "offset": 0})
        units = ((body or {}).get("result") or {}).get("stepUnits") if isinstance(body, dict) else None
        if isinstance(units, list) and units:
            live = sorted((u for u in units if not u.get("deleted")), key=lambda u: u.get("seqNo") or 0)
            return {"source": "qmetry", "steps": [
                {"index": u["seqNo"] if isinstance(u.get("seqNo"), int) else i + 1,
                 "action": str(u.get("stepDetails") or ""),
                 "data": str(u.get("testData") or ""),
                 "expected": str(u.get("expectedResult") or "")}
                for i, u in enumerate(live)]}

    def _raw(v):
        return str(v.get("raw", "") if isinstance(v, dict) else (v or ""))

    body = _get(f"/rest/raven/1.0/api/test/{key}/teststeps")
    if isinstance(body, list) and body:
        return {"source": "xray-server", "steps": [
            {"index": s["index"] if isinstance(s.get("index"), int) else i + 1,
             "action": _raw(s.get("step")), "data": _raw(s.get("data")), "expected": _raw(s.get("result"))}
            for i, s in enumerate(body)]}

    tc = _get(f"/rest/atm/1.0/testcase/{key}")
    script = (tc or {}).get("testScript") if isinstance(tc, dict) else None
    if isinstance(script, dict):
        if script.get("type") == "STEP_BY_STEP" and isinstance(script.get("steps"), list):
            return {"source": "zephyr-scale", "steps": [
                {"index": i + 1, "action": str(s.get("description") or ""),
                 "data": str(s.get("testData") or ""), "expected": str(s.get("expectedResult") or "")}
                for i, s in enumerate(script["steps"])]}
        if script.get("type") == "PLAIN_TEXT" and script.get("text"):
            return {"source": "zephyr-scale",
                    "steps": [{"index": 1, "action": str(script["text"]), "data": "", "expected": ""}]}
    return None


@_read_tool
def jira_issue(key: str) -> str:
    """Pobierz szczegoly ticketu Jira (bezposrednio z Jiry).

    Zwraca pelne dane: pola ticketu, opis, komentarze, worklogi (zalogowany czas), changelog.
    fields zawiera tez typ_pracy, dotyczy, rodzaj_zmiany, numer_zamowienia, numer_projektu
    (customfieldy Acme). Relacje: fields.issue_links [{type, direction, key, summary}]
    i fields.subtasks [{key, summary, status}]; fields.components to [{id, name}].
    Dla ticketow typu "Test Case" w odpowiedzi pojawia sie tez `test_steps`
    {source, steps[{index, action, data, expected}]} -- kroki testowe
    z QMetry/Xray/Zephyr (na jira.example.com source="qmetry"). Dla pozostalych `test_steps: null`.

    Args:
        key: Klucz ticketu, np. "PRJ1-123" lub "PRJ2-456".
    """
    t0 = time.perf_counter()
    key = (key or "").strip().upper()
    if not _ISSUE_KEY_RE.match(key):
        return "BLAD: key musi miec format PROJEKT-NNN (np. PRJ1-123)"
    fields_param = _ISSUE_FIELDS + "," + ",".join(_jira.EXTRA_CUSTOM_FIELDS)
    try:
        resp = _jira_get(f"/rest/api/2/issue/{key}", {"expand": "changelog", "fields": fields_param})
        if resp.status_code != 200:
            _tlog(f"jira_issue HTTP {resp.status_code}", t0, key)
            return _jira_error(resp)
        issue = resp.json()
        wl_resp = _jira_get(f"/rest/api/2/issue/{key}/worklog", {"maxResults": 1000})
        wl_data = wl_resp.json() if wl_resp.status_code == 200 else {"worklogs": []}
    except (requests.RequestException, ValueError) as exc:
        _tlog(f"jira_issue error: {exc}", t0)
        return f"BLAD: problem z polaczeniem do Jiry: {exc}"

    f = issue.get("fields") or {}
    status = f.get("status") or {}
    timetracking = f.get("timetracking") or {}
    fields = {
        "summary": f.get("summary") or "",
        "description": f.get("description"),
        "status": status.get("name"),
        "status_category": (status.get("statusCategory") or {}).get("name"),
        "assignee": _person(f.get("assignee")),
        "reporter": _person(f.get("reporter")),
        "priority": (f.get("priority") or {}).get("name"),
        "issue_type": (f.get("issuetype") or {}).get("name"),
        "labels": f.get("labels") or [],
        "components": _components(f.get("components")),
        "fix_versions": [v.get("name") for v in f.get("fixVersions") or []],
        "parent": _parent(f.get("parent")),
        "subtasks": [{"key": s.get("key"), "summary": (s.get("fields") or {}).get("summary") or "",
                      "status": ((s.get("fields") or {}).get("status") or {}).get("name")}
                     for s in f.get("subtasks") or []],
        "issue_links": _issue_links(f.get("issuelinks")),
        "story_points": next((f[k] for k in ("story_points", "customfield_10016", "customfield_10028")
                              if f.get(k) is not None), None),
        "epic_link": f.get("customfield_10001"),
        "created": f.get("created"),
        "updated": f.get("updated"),
        "resolution_date": f.get("resolutiondate"),
        "time_estimate_s": timetracking.get("originalEstimateSeconds"),
        "time_remaining_s": timetracking.get("remainingEstimateSeconds"),
        "time_spent_s": timetracking.get("timeSpentSeconds"),
    }
    for cf_id, label in _jira.EXTRA_CUSTOM_FIELDS.items():
        fields[label] = _jira._extract_field_value(f.get(cf_id))

    comments = [{"id": c.get("id"), "author": (c.get("author") or {}).get("displayName"),
                 "body": c.get("body") or "", "created": c.get("created"), "updated": c.get("updated")}
                for c in (f.get("comment") or {}).get("comments") or []]
    worklogs = [{"id": w.get("id"), "author": (w.get("author") or {}).get("displayName"),
                 "started": w.get("started"), "time_spent_s": w.get("timeSpentSeconds") or 0,
                 "comment": w.get("comment")}
                for w in wl_data.get("worklogs") or []]
    changelog = [{"id": h.get("id"), "author": (h.get("author") or {}).get("displayName"),
                  "created": h.get("created"),
                  "items": [{"field": i.get("field"), "from_str": i.get("fromString"),
                             "to_str": i.get("toString")} for i in h.get("items") or []]}
                 for h in reversed((issue.get("changelog") or {}).get("histories") or [])]

    test_steps = None
    if re.search("test", fields["issue_type"] or "", re.IGNORECASE):
        test_steps = _fetch_test_steps(key, issue.get("id"))

    data = {
        "key": key,
        "url": f"{_jira.JIRA_URL}/browse/{key}",
        "fields": fields,
        "comments": comments,
        "worklogs": {"total_time_spent_s": sum(w["time_spent_s"] for w in worklogs), "entries": worklogs},
        "changelog": changelog,
        "test_steps": test_steps,
        "_fetched_at": _now_iso(),
    }
    result = json.dumps(data, ensure_ascii=False, indent=2)
    _tlog("jira_issue done", t0, f"{len(result)}b")
    return result


@_read_tool
def jira_search(jql: str, max_results: int = 50) -> str:
    """Szukaj ticketow Jira przez live JQL (bezposrednio z Jiry, max 100 wynikow).

    Uzyj gdy musisz filtrowac po etykiecie, komponencie, statusie lub kombinacji.
    Przykladowe JQL:
      labels in (epic-discovery, qa-blocked)
      component in (Checkout, Auth)
      labels = qa-blocked AND component = Checkout AND statusCategory != Done
      project = PRJ1 AND updated >= -7d ORDER BY updated DESC
      project = PRJ1 AND remainingEstimate > 0 AND statusCategory != Done
      sprint = "TeamB Sprint 11" AND remainingEstimate > 0

    Odpowiedz: {issues:[{key, url, summary, status, status_category, assignee,
                labels, components:[{id,name}], created, updated,
                priority, issue_type,
                parent: {key, summary} | null,
                epic_link: str | null,
                original_estimate: str | null,
                remaining_estimate: str | null,
                original_estimate_seconds: int | null,
                remaining_estimate_seconds: int | null,
                issue_links: [{type, direction, key, summary}]}],
                total, returned, jql, _fetched_at}

    issue_links — powiązania ticketu (np. Duplicate, Blocks, Related):
      type      -- nazwa typu linku, np. "Duplicate", "Blocks", "Related"
      direction -- "inward" (np. "is duplicated by") lub "outward" (np. "duplicates")
      key       -- klucz powiązanego ticketu
      summary   -- tytuł powiązanego ticketu
    Aby znaleźć duplikaty: filtruj issue_links gdzie type=="Duplicate".

    Pola estymacji:
      remaining_estimate  — czas pozostaly do konca, czytelny string np. "2h 30m"
                            (null gdy nie ustawiony). Pokazuj jako "pozostalo: X"
                            przy listowaniu niedomknionych zadan; pomijaj nawias
                            calkowicie gdy null.
      original_estimate   — poczatkowa estymacja, czytelny string (null gdy brak).
      *_seconds           — te same wartosci w sekundach (int | null).

    Bledy:
      "BLAD: ..." z trescia errorMessages gdy Jira odrzucila JQL
      (najczesciej: nieznana etykieta lub komponent -- sprawdz pisownie).

    Args:
        jql:         Zapytanie JQL (wymagane).
        max_results: Liczba wynikow, 1-100 (domyslnie 50).
    """
    t0 = time.perf_counter()
    jql = (jql or "").strip()
    if not jql:
        return "BLAD: jql jest wymagane"
    max_results = min(max(int(max_results or 50), 1), 100)
    try:
        resp = _jira_get("/rest/api/2/search",
                         {"jql": jql, "fields": _SEARCH_FIELDS, "maxResults": max_results})
        if resp.status_code != 200:
            _tlog(f"jira_search HTTP {resp.status_code}", t0)
            return _jira_error(resp)
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        _tlog(f"jira_search error: {exc}", t0)
        return f"BLAD: problem z polaczeniem do Jiry: {exc}"

    issues = []
    for i in body.get("issues") or []:
        f = i.get("fields") or {}
        status = f.get("status") or {}
        tt = f.get("timetracking") or {}
        issues.append({
            "key": i.get("key"),
            "url": f"{_jira.JIRA_URL}/browse/{i.get('key')}",
            "summary": f.get("summary") or "",
            "status": status.get("name"),
            "status_category": (status.get("statusCategory") or {}).get("name"),
            "assignee": _person(f.get("assignee")),
            "labels": f.get("labels") or [],
            "components": _components(f.get("components")),
            "created": f.get("created"),
            "updated": f.get("updated"),
            "priority": (f.get("priority") or {}).get("name"),
            "issue_type": (f.get("issuetype") or {}).get("name"),
            "parent": _parent(f.get("parent")),
            "epic_link": f.get("customfield_10001"),
            "original_estimate": tt.get("originalEstimate"),
            "remaining_estimate": tt.get("remainingEstimate"),
            "original_estimate_seconds": tt.get("originalEstimateSeconds"),
            "remaining_estimate_seconds": tt.get("remainingEstimateSeconds"),
            "issue_links": _issue_links(f.get("issuelinks")),
        })
    data = {"issues": issues, "total": body.get("total", len(issues)), "returned": len(issues),
            "jql": jql, "_fetched_at": _now_iso()}
    result = json.dumps(data, ensure_ascii=False, indent=2)
    _tlog("jira_search done", t0, f"{len(issues)} issues")
    return result


@_write_tool
def jira_comment(
    key: str,
    body: str,
    mention: str = "",
) -> str:
    """Opublikuj komentarz do ticketu Jira (bezposrednio w Jirze).

    key:     Klucz ticketu, np. "PRJ1-123" lub "PRJ2-456".
    body:    Tresc komentarza (1-32000 znakow).
    mention: Tryb wzmianki (opcjonalny):
               "assignee"  -- login assignee pobierany z Jiry automatycznie
               "reporter"  -- login reportera pobierany z Jiry automatycznie
               "<login>"   -- np. "a.nowak" -- wstawiony verbatim jako [~a.nowak]
               ""          -- komentarz bez prefiksu wzmianki (domyslnie)

    Wywoluj TYLKO po jawnym potwierdzeniu uzytkownika -- to operacja zapisu.
    Zwraca JSON: {status, id, jira_url, mention_resolved, entry}.

    Rate limit: 30 komentarzy / 60 s.
    """
    t0 = time.perf_counter()
    key = (key or "").strip().upper()
    if not _ISSUE_KEY_RE.match(key):
        return "BLAD: key musi miec format PROJEKT-NNN (np. PRJ1-123)"
    text = body or ""
    if not 1 <= len(text) <= _COMMENT_MAX_BODY_LEN:
        return f"BLAD: tresc komentarza musi miec 1-{_COMMENT_MAX_BODY_LEN} znakow"

    if _recent_comment_count() >= _COMMENT_RATE_MAX:
        return f"BLAD: rate limit — max {_COMMENT_RATE_MAX} komentarzy na {int(_COMMENT_RATE_WINDOW_S)} s"

    mention = (mention or "").strip()
    if mention and mention not in ("assignee", "reporter") and not _MENTION_LOGIN_RE.match(mention):
        return "BLAD: mention musi byc 'assignee', 'reporter' albo loginem Jiry (litery, cyfry, . _ @ -)"
    resolved_login = None
    if mention in ("assignee", "reporter"):
        try:
            mr = _jira_get(f"/rest/api/2/issue/{key}", {"fields": "assignee,reporter"}, timeout=15)
            if mr.status_code == 200:
                login = ((mr.json().get("fields") or {}).get(mention) or {}).get("name")
                if isinstance(login, str) and login:
                    resolved_login = login
        except (requests.RequestException, ValueError) as exc:
            # Degrade gracefully: the comment still goes out without a mention.
            print(f"[jira-mcp] jira_comment mention resolution failed: {exc}", file=sys.stderr, flush=True)
    elif mention:
        resolved_login = mention

    post_body = f"[~{resolved_login}]\n\n{text}" if resolved_login else text
    try:
        # Retry only on 429: a 5xx from a proxy may come after Jira already stored the comment.
        resp = _request_with_retry(lambda: requests.post(
            f"{_jira.JIRA_URL}/rest/api/2/issue/{key}/comment",
            headers=_jira._HEADERS,
            json={"body": post_body},
            verify=_jira.VERIFY_SSL,
            timeout=15,
        ), retry_statuses={429})
    except requests.RequestException as exc:
        _tlog(f"jira_comment error: {exc}", t0)
        return f"BLAD: problem z polaczeniem do Jiry: {exc}"
    _tlog(f"jira_comment HTTP {resp.status_code}", t0, key)
    if resp.status_code not in (200, 201):
        return _jira_error(resp)
    try:
        comment_id = str(resp.json().get("id") or "")
    except ValueError:
        comment_id = ""
    jira_url = (f"{_jira.JIRA_URL}/browse/{key}?focusedCommentId={comment_id}" if comment_id
                else f"{_jira.JIRA_URL}/browse/{key}")
    entry = {
        "ts": _now_iso(), "key": key, "recipient": resolved_login, "body": post_body,
        "project": key.split("-")[0], "source": "jira-bob",
        "jira_comment_id": int(comment_id) if comment_id.isdigit() else None, "jira_url": jira_url,
    }
    try:
        with _COMMENTS_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:  # audit log is best-effort -- the comment is already posted
        print(f"[jira-mcp] jira_comment log write failed: {exc}", file=sys.stderr, flush=True)
    return json.dumps({"status": "ok", "id": comment_id, "jira_url": jira_url,
                       "mention_resolved": resolved_login, "entry": entry}, ensure_ascii=False, indent=2)


@_read_tool
def jira_boards(name: str = "", max_results: int = 50, resolve_filter: bool = True) -> str:
    """Odkryj board Jira po nazwie i pobierz jego filter_jql do uzycia w jira_search.

    Dwuetapowy workflow:
      Krok 1: jira_boards(name='Tydzien Planowy') -> boards[0].filter_jql
      Krok 2: jira_search(jql=filter_jql)         -> zadania boardu

    Parametry:
      name:           Filtr substring po nazwie boardu (case-insensitive; pusty = wszystkie).
      max_results:    Max liczba boardow (1-50, domyslnie 50). Zaciskana do [1,50].
                      Uzywaj malych wartosci przy resolve_filter=True (kazdy board = 2 dodatkowe
                      wywolania Jiry: /configuration + /filter).
      resolve_filter: True (domyslnie) = rozwija filter_jql dla kazdego boardu.
                      False = szybka lista bez rozwijania JQL (filter_id/filter_jql = null).

    count: 0 to poprawny wynik (nie blad) — brak boardow o danej nazwie.
    filter_jql moze byc null gdy board nie ma filtra lub rozwiniecie nie powiodlo sie
    dla tego boardu (partial-success — pozostale boardy zwracaja sie normalnie).
    Sprawdzaj filter_jql != null przed przekazaniem do jira_search.

    Zwraca JSON:
      {boards: [{board_id, name, type, filter_id, filter_jql}],
       count, name_filter, _fetched_at}
    """
    t0 = time.perf_counter()
    name = (name or "").strip()
    max_results = min(max(int(max_results or 50), 1), 50)
    data, err = _agile_get("/rest/agile/1.0/board", {"name": name, "maxResults": max_results})
    if err:
        _tlog(f"jira_boards error: {err}", t0)
        return f"BLAD: {err}"

    def _resolve(b: dict) -> dict:
        entry = {"board_id": b.get("id"), "name": b.get("name") or "", "type": b.get("type"),
                 "filter_id": None, "filter_jql": None}
        if not resolve_filter:
            return entry
        cfg, cfg_err = _agile_get(f"/rest/agile/1.0/board/{b.get('id')}/configuration")
        filter_id = ((cfg or {}).get("filter") or {}).get("id") if not cfg_err else None
        if not filter_id:
            return entry
        entry["filter_id"] = str(filter_id)
        flt, flt_err = _agile_get(f"/rest/api/2/filter/{filter_id}")
        if not flt_err and isinstance(flt, dict) and flt.get("jql"):
            entry["filter_jql"] = flt["jql"]
        return entry

    raw_boards = (data or {}).get("values") or [] if isinstance(data, dict) else []
    with ThreadPoolExecutor(max_workers=8) as pool:
        boards = list(pool.map(_resolve, raw_boards))
    result = json.dumps({"boards": boards, "count": len(boards), "name_filter": name,
                         "_fetched_at": _now_iso()}, ensure_ascii=False, indent=2)
    _tlog("jira_boards done", t0, f"{len(boards)} boards")
    return result


@_read_tool
def find(query: str) -> str:
    """Search Jira issues by free text (searches summary and description).

    Args:
        query: Text to search for, e.g. "migracja FieldOps"
    """
    issues = _jira.find_issue(query, max_results=10)
    if not issues:
        return f"Brak wyników dla: {query}"
    lines = [f"Znalezione ({len(issues)}):"]
    for i in issues:
        lines.append(f"  {i['key']} [{i['status']}] {i['summary']}")
    return "\n".join(lines)


# Empty (OWNER_JIRA_LOGIN unset) = no reporter in the payload; Jira uses the token owner.
_DEFAULT_REPORTER = org_config.OWNER_JIRA_LOGIN


def _search_users(query: str) -> list[dict]:
    """Wyszukaj userow Jiry, probujac kilku wariantow zapytania. Zwraca surowe obiekty Jiry.

    Wspolne jadro dla jira_search_users() (tool) i _resolve_reporter() (podstawianie
    zglaszajacego przy tworzeniu ticketu). Kolejnosc: konta @example.com najpierw.
    """
    def _one(q: str) -> list:
        r = requests.get(
            f"{_jira.JIRA_URL}/rest/api/2/user/search",
            headers=_jira._HEADERS,
            params={"username": q, "maxResults": 20},
            timeout=10,
            verify=_jira.VERIFY_SSL,
        )
        return r.json() if r.status_code == 200 else []

    words = query.strip().split()
    queries = [query]
    if len(words) >= 2:
        # tez probuj w odwroconej kolejnosci i email-style
        queries.append(" ".join(reversed(words)))
        queries.append(f"{words[-1]}.{words[0]}")   # nazwisko.imie
        queries.append(f"{words[0]}.{words[-1]}")   # imie.nazwisko

    seen: dict = {}
    for q in queries:
        try:
            found = _one(q)
        except Exception:  # noqa: BLE001 — jeden nieudany wariant nie przerywa reszty
            continue
        for u in found:
            key = u.get("name", "")
            if key and key not in seen:
                seen[key] = u

    domain = org_config.ORG_EMAIL_DOMAIN

    def _rank(u: dict) -> tuple[int, str]:
        name = u.get("name", "")
        return (0 if domain and domain in name else 1, name)

    return sorted(seen.values(), key=_rank)


def _looks_like_login(raw: str) -> bool:
    """True gdy string to gotowy login Jiry (np. 'jan.kowalski@example.com', 'a.nowak').

    Loginy nie zawieraja spacji. Imie i nazwisko ('Ewa Nowak') zawiera — i wlasnie
    takie wejscie trzeba dopiero rozwiazac przez wyszukiwanie.
    """
    return bool(raw) and " " not in raw.strip()


def _resolve_reporter(raw: str) -> tuple[str, str]:
    """Zamien wejsciowe 'reporter' na login Jiry. Zwraca (login, ostrzezenie_lub_pusty_string).

    Trzy sciezki:
      - puste           -> _DEFAULT_REPORTER, bez ostrzezenia (zachowanie historyczne)
      - gotowy login    -> zwrot bez ruszania (persona kanalowa moze podac login wprost)
      - imie i nazwisko -> wyszukanie w Jirze; brak trafienia = fallback na _DEFAULT_REPORTER
                           PLUS ostrzezenie, bo cicha podmiana zglaszajacego to zle
                           przypisanie, ktorego nikt nie zauwazy.
    """
    raw = (raw or "").strip()
    if not raw:
        return _DEFAULT_REPORTER, ""
    if _looks_like_login(raw):
        return raw, ""
    matches = _search_users(raw)
    if not matches:
        return (
            _DEFAULT_REPORTER,
            f"OSTRZEZENIE: nie znalazlem w Jirze konta dla {raw!r} — "
            f"reporter ustawiony na {_DEFAULT_REPORTER}.",
        )
    login = matches[0].get("name", "")
    if not login:
        return (
            _DEFAULT_REPORTER,
            f"OSTRZEZENIE: konto dla {raw!r} bez pola name — "
            f"reporter ustawiony na {_DEFAULT_REPORTER}.",
        )
    note = ""
    if len(matches) > 1:
        others = ", ".join(m.get("name", "") for m in matches[1:4])
        note = f"UWAGA: {raw!r} pasowal do {len(matches)} kont — wybrano {login} (inne: {others})."
    return login, note


def _verify_reporter(issue_key: str, want_login: str) -> str:
    """Sprawdz czy Jira faktycznie ustawila reportera przy tworzeniu; jesli nie — popraw PUT-em.

    Jira potrafi zignorowac pole reporter w payloadzie tworzenia (zaleznie od uprawnien i
    konfiguracji ekranu) i wstawic wlasciciela tokenu. Payload-only test tego nie wyklapie,
    dlatego po utworzeniu czytamy ticket i w razie rozbieznosci wolamy ten sam PUT, co
    jira_update_reporter.
    """
    try:
        r = requests.get(
            f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}",
            headers=_jira._HEADERS,
            params={"fields": "reporter"},
            timeout=15,
            verify=_jira.VERIFY_SSL,
        )
        if r.status_code != 200:
            return f"UWAGA: nie udalo sie zweryfikowac reportera (HTTP {r.status_code})."
        got = ((r.json().get("fields") or {}).get("reporter") or {}).get("name", "")
    except Exception as exc:  # noqa: BLE001
        return f"UWAGA: nie udalo sie zweryfikowac reportera ({exc})."

    if got == want_login:
        return ""

    import json as _json
    try:
        pr = requests.put(
            f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}",
            data=_json.dumps({"fields": {"reporter": {"name": want_login}}}).encode("utf-8"),
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return (
            f"UWAGA: Jira ustawila reportera na {got!r} zamiast {want_login!r}, "
            f"a poprawka nie przeszla ({exc})."
        )
    if pr.status_code in (200, 204):
        return f"Reporter poprawiony po fakcie na {want_login} (Jira utworzyla ticket z {got!r})."
    return (
        f"UWAGA: Jira ustawila reportera na {got!r} zamiast {want_login!r}; "
        f"poprawka PUT zwrocila HTTP {pr.status_code}."
    )


_JIRA_FIELDS = org_config.load_json_config(
    "JIRA_FIELDS_FILE", org_config.REPO_ROOT / "config" / "jira-fields.json",
)
# {(project, issue_type): issuetype id}, from config/jira-fields.json ("issue_type_ids":
# {"PROJ": {"task": "3", ...}}). Missing file = no project configured; jira_create says so.
_ISSUE_TYPE_IDS: dict[tuple[str, str], str] = {
    (proj.upper(), itype.lower()): str(type_id)
    for proj, types in (_JIRA_FIELDS.get("issue_type_ids") or {}).items()
    for itype, type_id in (types or {}).items()
}
_PROJECTS = frozenset(proj for proj, _ in _ISSUE_TYPE_IDS)
# Instance-specific write fields, from config/jira-fields.json "write_fields":
# {"typ_pracy": "customfield_…", "dotyczy": …, "rodzaj_zmiany": …, "numer_projektu": …,
#  "numer_zamowienia": …, "stan_obecny": …, "stan_docelowy": …}. A field missing from the
# file is never sent. "typ_pracy_options" maps a label to its option id (the tool accepts
# either); "rodzaj_zmiany_default" is set on create in every project except
# "projects_without_work_type".
_WRITE_FIELDS: dict[str, str] = dict(_JIRA_FIELDS.get("write_fields") or {})
_TYP_PRACY_OPTIONS: dict[str, str] = {
    str(label).strip().lower(): str(opt) for label, opt in (_JIRA_FIELDS.get("typ_pracy_options") or {}).items()
}
_RODZAJ_ZMIANY_DEFAULT = str(_JIRA_FIELDS.get("rodzaj_zmiany_default") or "")
_NO_WORK_TYPE_PROJECTS = frozenset(p.upper() for p in _JIRA_FIELDS.get("projects_without_work_type") or ())


def _set_write_field(fields: dict, name: str, value) -> None:
    """fields[<configured id for name>] = value; a field without a configured id is skipped."""
    field_id = _WRITE_FIELDS.get(name)
    if field_id:
        fields[field_id] = value


def _typ_pracy_option(value: str) -> str:
    """Option id for a Typ Pracy label (typ_pracy_options), or the value itself (already an id)."""
    return _TYP_PRACY_OPTIONS.get(value.strip().lower(), value.strip())

# Epic Name (customfield_10004) -- pole wymagane na ekranie tworzenia epika. Sprint
# (customfield_10020) na tym ekranie NIE wystepuje w zadnym projekcie.
_EPIC_NAME_FIELD = "customfield_10004"


@_write_tool
def jira_search_users(query: str) -> str:
    """Wyszukaj użytkowników Jiry po imieniu, nazwisku lub adresie e-mail.

    Przy podaniu imienia i nazwiska (np. "Ewa Nowak") automatycznie próbuje
    kilku strategii wyszukiwania i zwraca deduplikowane wyniki z @example.com na górze.

    Args:
        query: Imię, nazwisko, pełne imię i nazwisko lub e-mail. Przykłady:
               "Nowak", "Ewa Nowak", "ewa.nowak@example.com"

    Zwraca: JSON lista obiektów {name, displayName, email} lub BLAD: ...
    """
    import json as _json

    result = [
        {
            "name": u.get("name", ""),
            "displayName": u.get("displayName", ""),
            "email": u.get("emailAddress", ""),
        }
        for u in _search_users(query)
    ]
    return _json.dumps(result[:15], ensure_ascii=False)


@_write_tool
def jira_create(
    project: str,
    summary: str,
    issue_type: str,
    description: str = "",
    epic_key: str = "",
    epic_name: str = "",
    labels: str = "",
    typ_pracy: str = "",
    dotyczy: str = "",
    assignee: str = "",
    reporter: str = "",
    sprint_id: str = "",
    link_to: str = "",
    link_type: str = "Related",
    priority: str = "",
    stan_obecny: str = "",
    stan_docelowy: str = "",
) -> str:
    """Utwórz nowy ticket Jira w jednym z projektów z config/jira-fields.json.

    Wywołuj TYLKO po jawnym potwierdzeniu użytkownika — to operacja zapisu.

    Args:
        project:     Klucz projektu Jira, np. PRJ1 (lista w config/jira-fields.json).
        summary:     Tytuł ticketu (krótki, do 255 znaków).
        issue_type:  Typ: "user-story" (Historyjka/Historia), "task" (Zadanie), "bug" (Błąd)
                     lub "epic" (Epik) — tylko kombinacje projekt/typ z config/jira-fields.json.
                     Projekt bez typu epic: załóż epik w innym projekcie i podepnij zadania
                     przez epic_key (epic link działa cross-project).
        description: Opis ticketu w plain text (opcjonalny).
        epic_key:    Klucz epika do podpięcia, np. "PRJ1-100" (opcjonalny). Nie używaj razem
                     z issue_type="epic" — epik nie podpina się pod inny epik.
        epic_name:   Tylko dla issue_type="epic": nazwa epika (customfield_10004, pole wymagane
                     przez Jirę). Puste = użyje wartości summary. Dla innych typów ignorowane.
        labels:      Etykiety oddzielone przecinkami, np. "TEST,serwis" (opcjonalne).
        typ_pracy:   Typ Pracy: etykieta z config/jira-fields.json (typ_pracy_options), np.
                     "Prace programistyczne", albo ID opcji. Puste = nie ustawia.
        dotyczy:     Wartość pola "Dotyczy", np. "FieldOps" (opcjonalna).
        assignee:    Nazwa użytkownika Jira (pole "name" z jira_search_users), opcjonalna.
        reporter:    Nazwa użytkownika Jira jako zgłaszający (pole "name" z jira_search_users), opcjonalna.
                     Puste = domyślnie jan.kowalski@example.com — Jira wymaga tego pola na PRJ1
                     (i innych projektach) przy tworzeniu, więc puste "reporter" wywala walidację.
        sprint_id:   Numeryczne ID sprintu (string) do przypisania przy tworzeniu (customfield_10020),
                     np. "9002". Uzyskaj przez jira_sprint_search. Puste = nie ustawia.
                     Uwaga: jesli Jira zwroci blad o nieznanym polu, uzyj jira_assign_sprint po fakcie.
                     Dla issue_type="epic" pomijane — epiki nie maja pola sprintu na ekranie
                     tworzenia (w odpowiedzi pojawi sie OSTRZEZENIE).
        link_to:     Klucz istniejącego ticketu do automatycznego powiązania po utworzeniu,
                     np. "PRJ1-1234". Puste = nie ustawia. Używaj gdy wiesz o powiązanym US/zadaniu.
        link_type:   Typ powiązania dla link_to: "Related" (domyślny), "Blocks", "Duplicate" itp.
        priority:    Nazwa priorytetu dokładnie jak w Jirze, np. "Bloker", "Krytyczny", "Wysoki",
                     "Średni", "Niski". Puste = nie ustawia (Jira użyje domyślnego priorytetu
                     projektu). Ustawiany OSOBNYM wywołaniem zaraz po utworzeniu ticketu (nie w
                     payloadzie tworzenia) — jeśli podana nazwa nie pasuje do żadnej dostępnej
                     wartości dla tego ticketu, ticket i tak powstaje, ale w odpowiedzi pojawi się
                     OSTRZEZENIE i trzeba będzie doprecyzować priorytet przez jira_update_priority.
        stan_obecny:   Wymagane dla PRJ2/bug (ekran Błędu nie ma pola description) —
                     opisuje stan obecny/problem. Puste = nie ustawia.
        stan_docelowy: Wymagane dla PRJ2/bug — opisuje stan docelowy/
                     oczekiwane zachowanie. Puste = nie ustawia.

    Zwraca: KEY — URL przy sukcesie (z dopiskiem o priorytecie/linku jeśli podano), lub BLAD: ...
    przy błędzie samego tworzenia ticketu.
    """
    t0 = time.perf_counter()
    proj = project.upper()
    itype = issue_type.lower().strip()
    print(
        f"[jira-mcp] jira_create start project={proj} issue_type={itype} summary={summary!r}",
        file=sys.stderr, flush=True,
    )

    if not _PROJECTS:
        return "BLAD: jira_create nie jest skonfigurowane — brak issue_type_ids w config/jira-fields.json."
    if proj not in _PROJECTS:
        return f"BLAD: project musi byc jednym z {', '.join(sorted(_PROJECTS))} (podano: {project!r})"
    issuetype_id = _ISSUE_TYPE_IDS.get((proj, itype))
    if issuetype_id is None:
        if itype == "epic":
            with_epic = ", ".join(sorted(p for p, t in _ISSUE_TYPE_IDS if t == "epic"))
            return (
                f"BLAD: w {proj} nie da sie utworzyc epika. Zaloz epik w projekcie, ktory go ma "
                f"({with_epic}), i podepnij pod niego zadania {proj} przez epic_key "
                "(epic link dziala cross-project)."
            )
        return (
            f"BLAD: issue_type musi byc 'user-story', 'task', 'bug' lub 'epic' "
            f"(dostepne kombinacje projekt/typ: {sorted(_ISSUE_TYPE_IDS)}; "
            f"podano: project={proj!r}, issue_type={issue_type!r})."
        )
    is_epic = itype == "epic"

    fields: dict = {
        "project":   {"key": proj},
        "issuetype": {"id": issuetype_id},
        "summary":   summary,
    }
    if description:
        fields["description"] = description
    if is_epic:
        # Epic Name jest wymagany przez Jire na ekranie tworzenia epika — brak = HTTP 400.
        fields[_EPIC_NAME_FIELD] = (epic_name or summary).strip()
    epic_key_warning = ""
    if epic_key:
        if is_epic:
            epic_key_warning = (
                " | OSTRZEZENIE: epic_key pominiety — epik nie podpina sie pod inny epik"
            )
        else:
            fields["customfield_10001"] = epic_key
    if proj not in _NO_WORK_TYPE_PROJECTS:
        if _RODZAJ_ZMIANY_DEFAULT:
            _set_write_field(fields, "rodzaj_zmiany", {"id": _RODZAJ_ZMIANY_DEFAULT})
        if typ_pracy:
            _set_write_field(fields, "typ_pracy", {"id": _typ_pracy_option(typ_pracy)})
        if dotyczy:
            _set_write_field(fields, "dotyczy", [dotyczy])
    if assignee:
        fields["assignee"] = {"name": assignee}
    reporter_login, reporter_note = _resolve_reporter(reporter)
    if reporter_login:
        fields["reporter"] = {"name": reporter_login}
    if labels:
        fields["labels"] = [lb.strip() for lb in labels.split(",") if lb.strip()]
    sprint_warning = ""
    if sprint_id:
        if is_epic:
            # customfield_10020 nie wystepuje na ekranie tworzenia epika w zadnym projekcie —
            # wyslanie go konczy sie HTTP 400 i ticket w ogole nie powstaje.
            sprint_warning = (
                f" | OSTRZEZENIE: sprint {sprint_id} pominiety — epiki nie maja pola sprintu"
            )
        else:
            fields["customfield_10020"] = int(sprint_id)
    if stan_obecny:
        _set_write_field(fields, "stan_obecny", stan_obecny)
    if stan_docelowy:
        _set_write_field(fields, "stan_docelowy", stan_docelowy)

    import json as _json
    payload = _json.dumps({"fields": fields}).encode("utf-8")
    url = f"{_jira.JIRA_URL}/rest/api/2/issue"
    try:
        resp = requests.post(
            url,
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        _tlog(f"jira_create error: {exc}", t0)
        return f"BLAD: problem z połączeniem do Jiry: {exc}"

    if resp.status_code not in (200, 201):
        _tlog(f"jira_create HTTP {resp.status_code}", t0)
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"

    key = resp.json().get("key", "?")
    ticket_url = f"{_jira.JIRA_URL}/browse/{key}"
    _tlog("jira_create done", t0, f"key={key}")

    # Jira potrafi zignorowac reportera z payloadu tworzenia — sprawdzamy tylko wtedy, gdy
    # ktos jawnie o kogos poprosil (puste reporter = domyslny wlasciciel, nie ma czego mylic).
    reporter_fix = ""
    if reporter.strip() and key != "?":
        reporter_fix = _verify_reporter(key, reporter_login)

    link_result = ""
    if link_to and key != "?":
        link_payload = {
            "type": {"name": link_type},
            "inwardIssue": {"key": key},
            "outwardIssue": {"key": link_to},
        }
        try:
            lr = requests.post(
                f"{_jira.JIRA_URL}/rest/api/2/issueLink",
                headers=_jira._HEADERS,
                json=link_payload,
                verify=_jira.VERIFY_SSL,
                timeout=20,
            )
            if lr.status_code in (200, 201):
                link_result = f" | powiązano [{link_type}] {link_to}"
            else:
                link_result = f" | OSTRZEZENIE: link do {link_to} nie udał się (HTTP {lr.status_code})"
        except requests.RequestException as exc:
            link_result = f" | OSTRZEZENIE: link do {link_to} nie udał się: {exc}"

    priority_result = ""
    if priority and key != "?":
        # Priority is not set in the create payload -- {"priority": {"name": ...}} at create
        # time hits the same instance-specific name-resolution quirk as jira_update_priority
        # (see _resolve_priority_id docstring). Set it right after creation via id instead.
        pr = _set_priority(key, priority)
        if pr == "OK":
            priority_result = f" | priorytet: {priority}"
        else:
            priority_result = f" | OSTRZEZENIE: priorytet nie ustawiony ({pr})"

    reporter_result = ""
    for _part in (reporter_note, reporter_fix):
        if _part:
            reporter_result += f" | {_part}"

    return (
        f"{key} — {ticket_url}{link_result}{priority_result}{reporter_result}"
        f"{sprint_warning}{epic_key_warning}"
    )


@_write_tool
def jira_update_assignee(issue_key: str, assignee: str) -> str:
    """Zmień osobę przypisaną (Assignee) na istniejącym tickecie Jira.

    Działa zarówno na nowo utworzonych, jak i starych ticketach.
    Nazwę użytkownika pobierz z jira_search_users (pole "name").

    Args:
        issue_key: Klucz ticketu, np. "PRJ1-4568".
        assignee:  Nazwa użytkownika Jira (pole "name" z jira_search_users).

    Zwraca: "OK" przy sukcesie lub BLAD: ... przy błędzie.
    """
    import json as _json
    t0 = time.perf_counter()
    payload = _json.dumps({"fields": {"assignee": {"name": assignee}}}).encode("utf-8")
    url = f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}"
    try:
        resp = requests.put(
            url,
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_update_assignee HTTP {resp.status_code}", t0)
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"
    _tlog("jira_update_assignee done", t0, f"key={issue_key} assignee={assignee}")
    return "OK"


@_write_tool
def jira_update_reporter(issue_key: str, reporter: str) -> str:
    """Zmień zgłaszającego (Reporter) na istniejącym tickecie Jira.

    Działa zarówno na nowo utworzonych, jak i starych ticketach.
    Nazwę użytkownika pobierz z jira_search_users (pole "name").

    Args:
        issue_key: Klucz ticketu, np. "PRJ1-4568".
        reporter:  Nazwa użytkownika Jira (pole "name" z jira_search_users).

    Zwraca: "OK" przy sukcesie lub BLAD: ... przy błędzie.
    """
    import json as _json
    t0 = time.perf_counter()
    payload = _json.dumps({"fields": {"reporter": {"name": reporter}}}).encode("utf-8")
    url = f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}"
    try:
        resp = requests.put(
            url,
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_update_reporter HTTP {resp.status_code}", t0)
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"
    _tlog("jira_update_reporter done", t0, f"key={issue_key} reporter={reporter}")
    return "OK"


@_write_tool
def jira_update_priority(issue_key: str, priority: str) -> str:
    """Zmień priorytet na istniejącym tickecie Jira.

    Działa zarówno na nowo utworzonych, jak i starych ticketach. Priorytet jest ustawiany
    po ID (rozwiązanym z nazwy przez editmeta ticketu), nie po nazwie wprost — na tej
    instancji Jiry ustawianie priorytetu samą nazwą zawodzi dla niektórych wartości
    (zweryfikowane empirycznie), mimo ze dokladnie ta sama nazwa jest poprawna.

    Args:
        issue_key: Klucz ticketu, np. "PRJ1-4568".
        priority:  Nazwa priorytetu dokładnie jak w Jirze, np. "Bloker", "Krytyczny", "Wysoki",
                   "Średni", "Niski". Nazwy priorytetów są zdefiniowane per-projekt w Jirze —
                   jeśli podana nazwa nie istnieje w schemacie priorytetów TEGO ticketu,
                   narzędzie zwraca BLAD z listą dostępnych nazw dla tego ticketu.

    Zwraca: "OK" przy sukcesie lub BLAD: ... przy błędzie.
    """
    t0 = time.perf_counter()
    result = _set_priority(issue_key, priority)
    _tlog("jira_update_priority done", t0, f"key={issue_key} priority={priority} -> {result}")
    return result


@_write_tool
def jira_update_fix_version(issue_key: str, version: str, action: str = "add") -> str:
    """Zarządzaj polem Fix Version/s na istniejącym tickecie Jira: dodaj, usuń lub zastąp.

    Działa zarówno na nowo utworzonych, jak i starych ticketach.

    Args:
        issue_key: Klucz ticketu, np. "PRJ1-3020".
        version:   Nazwa wersji dokładnie jak w Jirze, np. "v 2.16n". Wersje są zdefiniowane
                   per-projekt w Jirze — jeśli podana nazwa nie istnieje w projekcie tego
                   ticketu, narzędzie zwraca BLAD z listą dostępnych wersji dla tego ticketu.
        action:    "add" (domyślne) — DODAJE version do listy, nie rusza już ustawionych wersji.
                   "remove" — USUWA version z listy, reszta zostaje bez zmian.
                   "set" — ZASTĘPUJE całą listę Fix Version/s wyłącznie przez version
                   (jedna wersja, wszystkie inne wcześniej ustawione znikają).

    Zwraca: "OK" przy sukcesie lub BLAD: ... przy błędzie.
    """
    t0 = time.perf_counter()
    result = _update_fix_version(issue_key, version, action)
    _tlog(
        "jira_update_fix_version done", t0,
        f"key={issue_key} version={version} action={action} -> {result}",
    )
    return result


@_write_tool
def jira_update_ticket(
    issue_key: str,
    typ_pracy: str = "",
    dotyczy: str = "",
    rodzaj_zmiany: str = "",
    labels: str = "",
    epic_link: str = "",
    components: str = "",
    numer_projektu: str = "",
    numer_zamowienia: str = "",
) -> str:
    """Zaktualizuj pola na istniejącym tickecie Jira.

    Działa na dowolnym istniejącym tickecie. Przekaż tylko te pola, które chcesz zmienić.

    Args:
        issue_key:     Klucz ticketu, np. "PRJ1-4568".
        typ_pracy:     Typ Pracy: etykieta z config/jira-fields.json (typ_pracy_options)
                       albo ID opcji. Puste = nie zmienia.
        dotyczy:       Wartość pola "Dotyczy", np. "FieldOps". Puste = nie zmienia.
        rodzaj_zmiany: ID opcji pola "Rodzaj Zmiany". Puste = nie zmienia.
        labels:        Etykiety oddzielone przecinkami, np. "serwis,zgloszenie-klienta".
                       ZASTĘPUJE wszystkie istniejące etykiety. Puste = nie zmienia.
        epic_link:     Klucz epika, np. "PRJ1-4597". Ustawia Epic Link (customfield_10001).
                       Puste = nie zmienia.
        components:    Komponenty oddzielone przecinkami, np. "praca_nieplanowana,backend".
                       ZASTĘPUJE wszystkie istniejące komponenty — jeśli chcesz tylko dodać,
                       najpierw pobierz istniejące przez jira_issue(key) i dołącz do listy.
                       Puste = nie zmienia.
        numer_projektu:   Numer projektu, zwykły tekst, np. "PRJ-123". Puste = nie zmienia.
        numer_zamowienia: Numer zamówienia, zwykły tekst, np. "ZAM-456".
                          Puste = nie zmienia.

    Zwraca: "OK" przy sukcesie lub BLAD: ... przy błędzie.
    """
    import json as _json
    t0 = time.perf_counter()
    fields: dict = {}
    if typ_pracy:
        _set_write_field(fields, "typ_pracy", {"id": _typ_pracy_option(typ_pracy)})
    if dotyczy:
        _set_write_field(fields, "dotyczy", [dotyczy])
    if rodzaj_zmiany:
        _set_write_field(fields, "rodzaj_zmiany", {"id": rodzaj_zmiany})
    if labels:
        fields["labels"] = [lb.strip() for lb in labels.split(",") if lb.strip()]
    if epic_link:
        fields["customfield_10001"] = epic_link.strip()
    if components:
        fields["components"] = [{"name": c.strip()} for c in components.split(",") if c.strip()]
    if numer_projektu:
        _set_write_field(fields, "numer_projektu", numer_projektu.strip())
    if numer_zamowienia:
        _set_write_field(fields, "numer_zamowienia", numer_zamowienia.strip())
    if not fields:
        return (
            "BLAD: podaj co najmniej jedno pole do zmiany "
            "(typ_pracy, dotyczy, rodzaj_zmiany, labels, epic_link, components, "
            "numer_projektu lub numer_zamowienia)"
        )
    payload = _json.dumps({"fields": fields}).encode("utf-8")
    url = f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}"
    try:
        resp = requests.put(
            url,
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_update_ticket HTTP {resp.status_code}", t0)
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"
    _field_map = {
        "typ_pracy": typ_pracy, "dotyczy": dotyczy, "rodzaj_zmiany": rodzaj_zmiany,
        "labels": labels, "epic_link": epic_link, "components": components,
        "numer_projektu": numer_projektu, "numer_zamowienia": numer_zamowienia,
    }
    changed = ", ".join(k for k, v in _field_map.items() if v)
    _tlog("jira_update_ticket done", t0, f"key={issue_key} changed={changed}")
    return "OK"


@_write_tool
def jira_update_description(
    issue_key: str,
    description: str = "",
    summary: str = "",
) -> str:
    """Zaktualizuj opis (description) i/lub tytuł (summary) istniejącego ticketu Jira.

    Używaj wiki markup w description (h2., h3., *, #, {code}, itd.).
    Przekaż tylko te pola, które chcesz zmienić — puste = bez zmian.

    Args:
        issue_key:   Klucz ticketu, np. "PRJ1-4846".
        description: Nowa treść opisu w formacie wiki markup Jiry. Puste = nie zmienia.
        summary:     Nowy tytuł ticketu. Puste = nie zmienia.

    Zwraca: "OK" przy sukcesie lub BLAD: ... przy błędzie.
    """
    import json as _json
    t0 = time.perf_counter()
    fields: dict = {}
    if description:
        fields["description"] = description
    if summary:
        fields["summary"] = summary
    if not fields:
        return "BLAD: podaj co najmniej jedno pole do zmiany (description lub summary)"
    payload = _json.dumps({"fields": fields}).encode("utf-8")
    url = f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}"
    try:
        resp = requests.put(
            url,
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_update_description HTTP {resp.status_code}", t0)
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"
    changed = ", ".join(f for f, v in [("description", description), ("summary", summary)] if v)
    _tlog("jira_update_description done", t0, f"key={issue_key} changed={changed}")
    return "OK"


@_write_tool
def jira_transition(issue_key: str, status: str) -> str:
    """Zmień status (workflow transition) istniejącego ticketu Jira.

    Pobiera dostępne przejścia dla ticketu i wykonuje to, którego nazwa pasuje
    do podanego statusu (dopasowanie case-insensitive, częściowe).

    Args:
        issue_key: Klucz ticketu, np. "PRJ1-4848".
        status:    Nazwa docelowego statusu, np. "Anulowane", "In Progress", "Done".

    Zwraca: "OK: <nazwa statusu>" przy sukcesie lub BLAD: ... przy błędzie.
    """
    import json as _json
    t0 = time.perf_counter()
    transitions_url = f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}/transitions"
    try:
        r = requests.get(transitions_url, headers=_jira._HEADERS, timeout=15)
        r.raise_for_status()
        transitions = r.json().get("transitions", [])
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry (pobieranie tranzycji): {exc}"

    status_lower = status.strip().lower()
    match = next(
        (t for t in transitions if status_lower in t["to"]["name"].lower()),
        None,
    )
    if not match:
        available = ", ".join(t["to"]["name"] for t in transitions)
        return f"BLAD: nie znaleziono statusu '{status}'. Dostępne: {available}"

    transition_id = match["id"]
    target_name = match["to"]["name"]
    payload = _json.dumps({"transition": {"id": transition_id}}).encode("utf-8")
    try:
        resp = requests.post(
            transitions_url,
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=15,
        )
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry (wykonanie tranzycji): {exc}"

    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_transition HTTP {resp.status_code}", t0)
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"

    _tlog("jira_transition done", t0, f"key={issue_key} -> {target_name}")
    return f"OK: {issue_key} → {target_name}"


@_write_tool
def jira_sprint_search(query: str, project: str) -> str:
    """Wyszukaj sprinty Jiry dla danego projektu (tylko active + future).

    Przeszukuje wszystkie boardy projektu przez Agile REST API, filtruje sprinty
    po nazwie (case-insensitive substring), deduplikuje po ID sprintu.
    Uzywaj PRZED jira_assign_sprint lub jira_create(sprint_id=...) aby ustalic ID sprintu.

    Args:
        query:   Fragment nazwy sprintu, np. "FieldOps Sprint 13" lub "Sprint 12".
        project: Klucz projektu Jiry, np. "PRJ2" lub "PRJ1".

    Zwraca JSON: {sprints: [{id, name, state, board_id, board_name}], count, query}.
    Jesli count=0 — brak pasujacych sprintow (poprawny wynik, nie blad).
    """
    import json as _json

    t0 = time.perf_counter()
    print(
        f"[jira-mcp] jira_sprint_search start query={query!r} project={project!r}",
        file=sys.stderr, flush=True,
    )

    boards_data, err = _agile_get("/rest/agile/1.0/board", {"projectKeyOrId": project})
    if err:
        _tlog(f"jira_sprint_search board discovery error: {err}", t0)
        return f"BLAD: {err}"

    boards = (boards_data or {}).get("values", [])
    if not boards:
        _tlog("jira_sprint_search done", t0, "no boards")
        return _json.dumps({"sprints": [], "count": 0, "query": query}, ensure_ascii=False)

    q_lower = query.lower()
    seen: dict[int, dict] = {}
    for board in boards:
        board_id = board.get("id")
        board_name = board.get("name", "")
        if board_id is None:
            continue
        sprint_data, sprint_err = _agile_get(
            f"/rest/agile/1.0/board/{board_id}/sprint",
            {"state": "active,future"},
        )
        if sprint_err:
            print(
                f"[jira-mcp] jira_sprint_search board {board_id} error: {sprint_err}",
                file=sys.stderr, flush=True,
            )
            continue
        for sprint in (sprint_data or {}).get("values", []):
            sid = sprint.get("id")
            name = sprint.get("name", "")
            if q_lower in name.lower() and sid is not None and sid not in seen:
                seen[sid] = {
                    "id": sid,
                    "name": name,
                    "state": sprint.get("state", ""),
                    "board_id": board_id,
                    "board_name": board_name,
                }

    sprints = list(seen.values())
    _tlog("jira_sprint_search done", t0, f"count={len(sprints)}")
    return _json.dumps({"sprints": sprints, "count": len(sprints), "query": query}, ensure_ascii=False)


@_write_tool
def jira_assign_sprint(issue_key: str, sprint_id: int) -> str:
    """Przypisz istniejacy ticket Jira do sprintu przez Agile REST API.

    Wywoluj TYLKO po jawnym potwierdzeniu uzytkownika — to operacja zapisu.
    Sprint ID uzyskaj przez jira_sprint_search. Dziala na active i future sprintach.
    Mozna przypisac ticket do sprintu rowniez po jego utworzeniu (fallback gdy
    customfield_10020 nie zadziala przy jira_create dla danego projektu/typu).

    Args:
        issue_key: Klucz ticketu Jira, np. "PRJ2-123".
        sprint_id: Numeryczne ID sprintu (z jira_sprint_search).

    Zwraca: "OK" przy sukcesie lub "BLAD: ..." przy bledzie.
    """
    import json as _json

    t0 = time.perf_counter()
    print(
        f"[jira-mcp] jira_assign_sprint start issue_key={issue_key!r} sprint_id={sprint_id}",
        file=sys.stderr, flush=True,
    )
    url = f"{_jira.JIRA_URL}/rest/agile/1.0/sprint/{sprint_id}/issue"
    payload = _json.dumps({"issues": [issue_key]}).encode("utf-8")
    try:
        resp = requests.post(
            url,
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        _tlog(f"jira_assign_sprint error: {exc}", t0)
        return f"BLAD: problem z polaczeniem do Jiry: {exc}"

    if resp.status_code not in (200, 204):
        _tlog(f"jira_assign_sprint HTTP {resp.status_code}", t0)
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        return f"BLAD: Jira Agile HTTP {resp.status_code} — {detail}"

    _tlog("jira_assign_sprint done", t0, f"issue_key={issue_key} sprint_id={sprint_id}")
    return "OK"


# Only files the watch loop itself downloaded from Teams messages may be attached
# (cli.py: tempfile.NamedTemporaryFile(prefix="bob_img_"/"bob_attach_")). Anything else --
# .env, session.json, ~/.ssh -- would be exfiltration via prompt injection.
_ATTACH_PREFIXES = ("bob_img_", "bob_attach_")


def _attachment_allowed(p: Path) -> bool:
    try:
        resolved = p.resolve(strict=True)
        tmp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False
    return (
        resolved.is_file()
        and resolved.parent == tmp_root
        and resolved.name.startswith(_ATTACH_PREFIXES)
    )


@_write_tool
def jira_attach(issue_key: str, file_path: str) -> str:
    """Dodaj plik lokalny jako załącznik do ticketu Jira.

    Używaj po jira_create gdy wiadomość użytkownika zawierała screenshoty lub pliki
    (przekazane jako ścieżki w kontekście wiadomości).

    Args:
        issue_key: Klucz ticketu, np. "PRJ1-1234".
        file_path: Bezwzględna ścieżka do pliku na dysku, np. "/Users/.../screenshot.png".

    Zwraca: "OK: <filename> dodany" przy sukcesie lub BLAD: ... przy błędzie.
    """
    import mimetypes as _mime
    t0 = time.perf_counter()
    if not _ISSUE_KEY_RE.match((issue_key or "").strip().upper()):
        return "BLAD: issue_key musi miec format PROJEKT-NNN (np. PRJ1-123)"
    issue_key = issue_key.strip().upper()
    p = Path(file_path)
    if not p.exists():
        return f"BLAD: plik nie istnieje: {file_path}"
    if not _attachment_allowed(p):
        _tlog("jira_attach odrzucony (sciezka spoza dozwolonych)", t0, p.name)
        return "BLAD: mozna dolaczac tylko zalaczniki z wiadomosci Teams (pliki tymczasowe Bota)"
    mime_type = _mime.guess_type(str(p))[0] or "application/octet-stream"
    url = f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}/attachments"
    headers = {k: v for k, v in _jira._HEADERS.items() if k != "Content-Type"}
    headers["X-Atlassian-Token"] = "no-check"
    try:
        with p.open("rb") as fh:
            def _do_request() -> requests.Response:
                fh.seek(0)
                return requests.post(
                    url,
                    headers=headers,
                    files={"file": (p.name, fh, mime_type)},
                    verify=_jira.VERIFY_SSL,
                    timeout=30,
                )
            resp = _request_with_retry(_do_request)
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry: {exc}"
    if resp.status_code not in (200, 201):
        _tlog(f"jira_attach HTTP {resp.status_code}", t0)
        return f"BLAD: Jira HTTP {resp.status_code} — {resp.text[:200]}"
    _tlog("jira_attach done", t0, f"key={issue_key} file={p.name}")
    return f"OK: {p.name} dodany do {issue_key}"


@_write_tool
def jira_link(
    inward_key: str,
    outward_key: str,
    link_type: str = "Related",
) -> str:
    """Ustaw natywne powiązanie (link) między dwoma ticketami Jira.

    Używaj gdy użytkownik pyta o powiązanie zadań, "blocks", "relates to", "is blocked by" itp.
    Wywołuj niezależnie od komentarzy — to osobna akcja na API Jiry.

    Typy powiązań (link_type):
      "Related"       — relates to / relates to  (domyślny)
      "Blocks"        — blocks / is blocked by
      "Duplicate"     — duplicates / is duplicated by
      "Cloners"       — clones / is cloned by
      "Problem/Incident" — causes / is caused by

    Kierunek: inward_key [link_type] outward_key
      np. "PRJ1-1" Blocks "PRJ1-2"  → PRJ1-1 blocks PRJ1-2

    Args:
        inward_key:  Klucz ticketu po stronie inward, np. "PRJ1-123".
        outward_key: Klucz ticketu po stronie outward, np. "PRJ1-456".
        link_type:   Typ powiązania (patrz wyżej). Domyślnie "Related".

    Zwraca: "OK: PRJ1-123 [Relates] PRJ1-456" lub BLAD: ...
    """
    t0 = time.perf_counter()
    url = f"{_jira.JIRA_URL}/rest/api/2/issueLink"
    payload = {
        "type": {"name": link_type},
        "inwardIssue": {"key": inward_key},
        "outwardIssue": {"key": outward_key},
    }
    try:
        resp = _request_with_retry(lambda: requests.post(
            url,
            headers=_jira._HEADERS,
            json=payload,
            verify=_jira.VERIFY_SSL,
            timeout=20,
        ))
    except requests.RequestException as exc:
        return f"BLAD: problem z połączeniem do Jiry: {exc}"
    _tlog(f"jira_link HTTP {resp.status_code}", t0, f"{inward_key}->{outward_key} type={link_type}")
    if resp.status_code not in (200, 201):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        return f"BLAD: Jira HTTP {resp.status_code} — {detail}"
    return f"OK: {inward_key} [{link_type}] {outward_key}"


if __name__ == "__main__":
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp
    run_mcp(mcp, default_port=MCP_PORTS[_SERVER_NAME])
