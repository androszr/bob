"""MCP server exposing Jira & Tempo as native Claude tools.

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
import logts  # noqa: E402, F401, I001 — patches sys.stderr with local timestamps

import anyio  # noqa: E402
import jira_client as _jira  # noqa: E402
import jira_fields  # noqa: E402
import org_config  # noqa: E402
import requests  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

# One file, two MCP servers (scripts/jira/mcp_read_server.py sets the "read" toolset):
#   jira-read-bob -- reads (find, jira_issue, jira_search, jira_boards); runs as an HTTP daemon
#                    (ai._DAEMON_SERVERS), shared by every chat.
#   jira-bob      -- writes; always stdio (ai._STDIO_FORCED_SERVERS), a separate process per call,
#                    so it sees the per-message env (BOB_SENDER_MRI) and shares no state between chats.
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
    named "High"), so resolving per-issue is the only reliable way to get the right id.
    Matching by id (not name) in the update payload also sidesteps a real instance quirk found
    empirically for priority: setting it via {"name": ...} returns HTTP 400 "Priority name is
    invalid" for at least one value even with the exact server-provided name string --
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
    """Resolve priority_name to an id via _resolve_priority_id and PUT it. Returns OK or ERROR: ..."""
    import json as _json
    t0 = time.perf_counter()
    priority_id, allowed_names = _resolve_priority_id(issue_key, priority_name)
    if priority_id is None:
        options = ", ".join(allowed_names) if allowed_names else "no data (editmeta unavailable)"
        return f'ERROR: unknown priority "{priority_name}" for {issue_key}. Available: {options}'
    payload = _json.dumps({"fields": {"priority": {"id": priority_id}}}).encode("utf-8")
    try:
        resp = requests.put(
            f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}",
            data=payload,
            headers={**_jira._HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"ERROR: cannot connect to Jira: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"_set_priority HTTP {resp.status_code}", t0)
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"
    _tlog("_set_priority done", t0, f"key={issue_key} priority={priority_name} id={priority_id}")
    return "OK"


_FIX_VERSION_ACTIONS = {"add", "remove", "set"}


def _update_fix_version(issue_key: str, version_name: str, action: str = "add") -> str:
    """Resolve version_name to an id via editmeta, then add/remove/set it on fixVersions.

    action="add":    {"update": {"fixVersions": [{"add": {"id": ...}}]}}    -- keeps existing.
    action="remove": {"update": {"fixVersions": [{"remove": {"id": ...}}]}} -- keeps the rest.
    action="set":    {"fields": {"fixVersions": [{"id": ...}]}}            -- REPLACES the
                      whole list with just this one version.
    Returns OK or ERROR: ...
    """
    import json as _json
    if action not in _FIX_VERSION_ACTIONS:
        return f'ERROR: action must be "add", "remove" or "set" (got: {action!r})'
    t0 = time.perf_counter()
    version_id, allowed_names = _resolve_editmeta_value_id(issue_key, "fixVersions", version_name)
    if version_id is None:
        options = ", ".join(allowed_names) if allowed_names else "no data (editmeta unavailable)"
        return f'ERROR: unknown version "{version_name}" for {issue_key}. Available: {options}'
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
        return f"ERROR: cannot connect to Jira: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"_update_fix_version HTTP {resp.status_code}", t0)
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"
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
        return None, f"cannot connect to Jira: {exc}"
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
        return None, "Jira Agile: the response is not JSON (SSO session / login page?)"


# ---------------------------------------------------------------------------
# Reads and comments -- straight to Jira. The response shape is fixed because Bob's and the
# personas' prompts rely on it; jira_search also returns issue_links.
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
    """Map a non-2xx Jira response to an ERROR string (token/404/JQL errors made explicit)."""
    if resp.status_code == 401:
        return "ERROR: Jira token invalid or missing (ATLASSIAN_TOKEN)"
    if resp.status_code == 403:
        return "ERROR: this token has no permission for that in Jira"
    if resp.status_code == 404:
        return "ERROR: not_found"
    try:
        body = resp.json()
        msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
        detail = "; ".join(msgs) if msgs else resp.text[:300]
    except Exception:  # noqa: BLE001
        detail = resp.text[:300]
    return f"ERROR: Jira HTTP {resp.status_code} — {detail}"


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
    """Get the details of a Jira ticket (straight from Jira).

    Returns the full data: ticket fields, description, comments, worklogs (logged time), changelog.
    fields also holds the org's custom fields configured in config/jira-fields.json (e.g.
    work_type, affects, change_type, order_number, project_number).
    Relations: fields.issue_links [{type, direction, key, summary}]
    and fields.subtasks [{key, summary, status}]; fields.components is [{id, name}].
    For "Test Case" tickets the response also carries `test_steps`
    {source, steps[{index, action, data, expected}]} -- the test steps
    from QMetry/Xray/Zephyr (source says which). For every other ticket `test_steps: null`.

    Args:
        key: Ticket key, e.g. "PRJ1-123" or "PRJ2-456".
    """
    t0 = time.perf_counter()
    key = (key or "").strip().upper()
    if not _ISSUE_KEY_RE.match(key):
        return "ERROR: key must look like PROJECT-NNN (e.g. PRJ1-123)"
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
        return f"ERROR: cannot connect to Jira: {exc}"

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
    """Search Jira tickets with live JQL (straight from Jira, at most 100 results).

    Use it when you need to filter by label, component, status or a combination.
    Example JQL:
      labels in (epic-discovery, qa-blocked)
      component in (Checkout, Auth)
      labels = qa-blocked AND component = Checkout AND statusCategory != Done
      project = PRJ1 AND updated >= -7d ORDER BY updated DESC
      project = PRJ1 AND remainingEstimate > 0 AND statusCategory != Done
      sprint = "TeamB Sprint 11" AND remainingEstimate > 0

    Response: {issues:[{key, url, summary, status, status_category, assignee,
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

    issue_links — the ticket's links (e.g. Duplicate, Blocks, Related):
      type      -- link type name, e.g. "Duplicate", "Blocks", "Related"
      direction -- "inward" (e.g. "is duplicated by") or "outward" (e.g. "duplicates")
      key       -- key of the linked ticket
      summary   -- title of the linked ticket
    To find duplicates: filter issue_links where type=="Duplicate".

    Estimate fields:
      remaining_estimate  — time left, a readable string e.g. "2h 30m"
                            (null when not set). Show it as "remaining: X"
                            when listing open tasks; leave the brackets out
                            entirely when null.
      original_estimate   — the original estimate, a readable string (null when missing).
      *_seconds           — the same values in seconds (int | null).

    Errors:
      "ERROR: ..." with the errorMessages text when Jira rejected the JQL
      (most often: an unknown label or component -- check the spelling).

    Args:
        jql:         JQL query (required).
        max_results: Number of results, 1-100 (default 50).
    """
    t0 = time.perf_counter()
    jql = (jql or "").strip()
    if not jql:
        return "ERROR: jql is required"
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
        return f"ERROR: cannot connect to Jira: {exc}"

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
    """Post a comment on a Jira ticket (straight to Jira).

    key:     Ticket key, e.g. "PRJ1-123" or "PRJ2-456".
    body:    Comment text (1-32000 characters).
    mention: Mention mode (optional):
               "assignee"  -- the assignee's login, fetched from Jira automatically
               "reporter"  -- the reporter's login, fetched from Jira automatically
               "<login>"   -- e.g. "a.nowak" -- inserted verbatim as [~a.nowak]
               ""          -- comment without a mention prefix (default)

    Call ONLY after the user has explicitly confirmed -- this is a write.
    Returns JSON: {status, id, jira_url, mention_resolved, entry}.

    Rate limit: 30 comments / 60 s.
    """
    t0 = time.perf_counter()
    key = (key or "").strip().upper()
    if not _ISSUE_KEY_RE.match(key):
        return "ERROR: key must look like PROJECT-NNN (e.g. PRJ1-123)"
    text = body or ""
    if not 1 <= len(text) <= _COMMENT_MAX_BODY_LEN:
        return f"ERROR: the comment text must be 1-{_COMMENT_MAX_BODY_LEN} characters"

    if _recent_comment_count() >= _COMMENT_RATE_MAX:
        return f"ERROR: rate limit — at most {_COMMENT_RATE_MAX} comments per {int(_COMMENT_RATE_WINDOW_S)} s"

    mention = (mention or "").strip()
    if mention and mention not in ("assignee", "reporter") and not _MENTION_LOGIN_RE.match(mention):
        return "ERROR: mention must be 'assignee', 'reporter' or a Jira login (letters, digits, . _ @ -)"
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
        return f"ERROR: cannot connect to Jira: {exc}"
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
    """Find a Jira board by name and get its filter_jql to use in jira_search.

    Two-step workflow:
      Step 1: jira_boards(name='Weekly Planning') -> boards[0].filter_jql
      Step 2: jira_search(jql=filter_jql)         -> the board's tasks

    Parameters:
      name:           Substring filter on the board name (case-insensitive; empty = all).
      max_results:    Max number of boards (1-50, default 50). Clamped to [1,50].
                      Use small values with resolve_filter=True (each board = 2 extra
                      Jira calls: /configuration + /filter).
      resolve_filter: True (default) = expands filter_jql for every board.
                      False = a quick list without expanding the JQL (filter_id/filter_jql = null).

    count: 0 is a valid result (not an error) — no board with that name.
    filter_jql can be null when the board has no filter or expanding it failed
    for that board (partial success — the other boards come back normally).
    Check filter_jql != null before passing it to jira_search.

    Returns JSON:
      {boards: [{board_id, name, type, filter_id, filter_jql}],
       count, name_filter, _fetched_at}
    """
    t0 = time.perf_counter()
    name = (name or "").strip()
    max_results = min(max(int(max_results or 50), 1), 50)
    data, err = _agile_get("/rest/agile/1.0/board", {"name": name, "maxResults": max_results})
    if err:
        _tlog(f"jira_boards error: {err}", t0)
        return f"ERROR: {err}"

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
        query: Text to search for, e.g. "FieldOps migration"
    """
    issues = _jira.find_issue(query, max_results=10)
    if not issues:
        return f"No results for: {query}"
    lines = [f"Found ({len(issues)}):"]
    for i in issues:
        lines.append(f"  {i['key']} [{i['status']}] {i['summary']}")
    return "\n".join(lines)


# Empty (OWNER_JIRA_LOGIN unset) = no reporter in the payload; Jira uses the token owner.
_DEFAULT_REPORTER = org_config.OWNER_JIRA_LOGIN


def _search_users(query: str) -> list[dict]:
    """Search Jira users, trying several variants of the query. Returns raw Jira objects.

    Shared core of jira_search_users() (the tool) and _resolve_reporter() (filling in the
    reporter when a ticket is created). Order: accounts in the org's e-mail domain first.
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
        # also try the reversed order and e-mail style
        queries.append(" ".join(reversed(words)))
        queries.append(f"{words[-1]}.{words[0]}")   # surname.firstname
        queries.append(f"{words[0]}.{words[-1]}")   # firstname.surname

    seen: dict = {}
    for q in queries:
        try:
            found = _one(q)
        except Exception:  # noqa: BLE001 — one failed variant does not stop the rest
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
    """True when the string is already a Jira login (e.g. 'jan.kowalski@example.com', 'a.nowak').

    Logins contain no spaces. A full name ('Ewa Nowak') does — and that is exactly
    the input that still has to be resolved by a search.
    """
    return bool(raw) and " " not in raw.strip()


def _resolve_reporter(raw: str) -> tuple[str, str]:
    """Turn the 'reporter' input into a Jira login. Returns (login, warning_or_empty_string).

    Three paths:
      - empty      -> _DEFAULT_REPORTER, no warning (historical behaviour)
      - a login    -> returned untouched (a channel persona may pass the login directly)
      - a full name -> a Jira search; no hit = fall back to _DEFAULT_REPORTER
                       PLUS a warning, because silently swapping the reporter is a wrong
                       assignment nobody would notice.
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
            f"WARNING: no Jira account found for {raw!r} — "
            f"reporter set to {_DEFAULT_REPORTER}.",
        )
    login = matches[0].get("name", "")
    if not login:
        return (
            _DEFAULT_REPORTER,
            f"WARNING: the account for {raw!r} has no name field — "
            f"reporter set to {_DEFAULT_REPORTER}.",
        )
    note = ""
    if len(matches) > 1:
        others = ", ".join(m.get("name", "") for m in matches[1:4])
        note = f"NOTE: {raw!r} matched {len(matches)} accounts — picked {login} (others: {others})."
    return login, note


def _verify_reporter(issue_key: str, want_login: str) -> str:
    """Check that Jira really set the reporter on create; if not, fix it with a PUT.

    Jira can ignore the reporter field in the create payload (depending on permissions and
    screen configuration) and put in the token owner. A payload-only test would not catch
    that, so after creating we read the ticket back and, on a mismatch, send the same PUT as
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
            return f"NOTE: could not verify the reporter (HTTP {r.status_code})."
        got = ((r.json().get("fields") or {}).get("reporter") or {}).get("name", "")
    except Exception as exc:  # noqa: BLE001
        return f"NOTE: could not verify the reporter ({exc})."

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
            f"NOTE: Jira set the reporter to {got!r} instead of {want_login!r}, "
            f"and the fix failed ({exc})."
        )
    if pr.status_code in (200, 204):
        return f"Reporter corrected afterwards to {want_login} (Jira created the ticket with {got!r})."
    return (
        f"NOTE: Jira set the reporter to {got!r} instead of {want_login!r}; "
        f"the fixing PUT returned HTTP {pr.status_code}."
    )


_JIRA_FIELDS = jira_fields.load()
# {(project, issue_type): issuetype id}, from config/jira-fields.json ("issue_type_ids":
# {"PROJ": {"task": "3", ...}}). Missing file = no project configured; jira_create says so.
_ISSUE_TYPE_IDS: dict[tuple[str, str], str] = {
    (proj.upper(), itype.lower()): str(type_id)
    for proj, types in (_JIRA_FIELDS.get("issue_type_ids") or {}).items()
    for itype, type_id in (types or {}).items()
}
_PROJECTS = frozenset(proj for proj, _ in _ISSUE_TYPE_IDS)
# Instance-specific write fields, from config/jira-fields.json "write_fields":
# {"work_type": "customfield_…", "affects": …, "change_type": …, "project_number": …,
#  "order_number": …, "current_state": …, "target_state": …}. A field missing from the
# file is never sent. "work_type_options" maps a label to its option id (the tool accepts
# either); "change_type_default" is set on create in every project except
# "projects_without_work_type". Older Polish keys are read as aliases (jira_fields.py).
_WRITE_FIELDS: dict[str, str] = dict(_JIRA_FIELDS.get("write_fields") or {})
_WORK_TYPE_OPTIONS: dict[str, str] = {
    str(label).strip().lower(): str(opt) for label, opt in (_JIRA_FIELDS.get("work_type_options") or {}).items()
}
_CHANGE_TYPE_DEFAULT = str(_JIRA_FIELDS.get("change_type_default") or "")
_NO_WORK_TYPE_PROJECTS = frozenset(p.upper() for p in _JIRA_FIELDS.get("projects_without_work_type") or ())


def _set_write_field(fields: dict, name: str, value) -> None:
    """fields[<configured id for name>] = value; a field without a configured id is skipped."""
    field_id = _WRITE_FIELDS.get(name)
    if field_id:
        fields[field_id] = value


def _work_type_option(value: str) -> str:
    """Option id for a Work type label (work_type_options), or the value itself (already an id)."""
    return _WORK_TYPE_OPTIONS.get(value.strip().lower(), value.strip())

# Epic Name (customfield_10004) -- a required field on the epic create screen. Sprint
# (customfield_10020) is NOT on that screen in any project.
_EPIC_NAME_FIELD = "customfield_10004"


@_write_tool
def jira_search_users(query: str) -> str:
    """Search Jira users by first name, surname or e-mail address.

    Given a full name (e.g. "Ewa Nowak") it automatically tries several search
    strategies and returns de-duplicated results with the org's e-mail domain first.

    Args:
        query: First name, surname, full name or e-mail. Examples:
               "Nowak", "Ewa Nowak", "ewa.nowak@example.com"

    Returns: a JSON list of {name, displayName, email} objects, or ERROR: ...
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
    work_type: str = "",
    affects: str = "",
    assignee: str = "",
    reporter: str = "",
    sprint_id: str = "",
    link_to: str = "",
    link_type: str = "Related",
    priority: str = "",
    current_state: str = "",
    target_state: str = "",
) -> str:
    """Create a new Jira ticket in one of the projects from config/jira-fields.json.

    Call ONLY after the user has explicitly confirmed — this is a write.

    Args:
        project:     Jira project key, e.g. PRJ1 (list in config/jira-fields.json).
        summary:     Ticket title (short, up to 255 characters).
        issue_type:  Type: "user-story" (Story), "task" (Task), "bug" (Bug)
                     or "epic" (Epic) — only project/type combinations from config/jira-fields.json.
                     A project without the epic type: create the epic in another project and link
                     the tasks through epic_key (the epic link works across projects).
        description: Ticket description in plain text (optional).
        epic_key:    Key of the epic to link to, e.g. "PRJ1-100" (optional). Do not use it together
                     with issue_type="epic" — an epic does not link under another epic.
        epic_name:   Only for issue_type="epic": the epic name (customfield_10004, a field Jira
                     requires). Empty = uses the summary. Ignored for other types.
        labels:      Comma-separated labels, e.g. "TEST,service" (optional).
        work_type:   Work type: a label from config/jira-fields.json (work_type_options), e.g.
                     "Development", or an option ID. Empty = not set.
        affects:     Value of the "Affects" field, e.g. "FieldOps" (optional).
        assignee:    Jira user name (the "name" field from jira_search_users), optional.
        reporter:    Jira user name as the reporter (the "name" field from jira_search_users), optional.
                     Empty = the default reporter (the owner's login, OWNER_JIRA_LOGIN) — Jira
                     requires this field on PRJ1 (and other projects) on create, so an empty
                     "reporter" would fail validation.
        sprint_id:   Numeric sprint ID (string) to assign on create (customfield_10020),
                     e.g. "9002". Get it from jira_sprint_search. Empty = not set.
                     Note: if Jira returns an unknown-field error, use jira_assign_sprint afterwards.
                     Skipped for issue_type="epic" — epics have no sprint field on the create
                     screen (the response carries a WARNING).
        link_to:     Key of an existing ticket to link automatically after creation,
                     e.g. "PRJ1-1234". Empty = not set. Use it when you know of a related story/task.
        link_type:   Link type for link_to: "Related" (default), "Blocks", "Duplicate" etc.
        priority:    Priority name exactly as in Jira, e.g. "Blocker", "Critical", "High",
                     "Medium", "Low". Empty = not set (Jira uses the project's default
                     priority). Set in a SEPARATE call right after the ticket is created (not in
                     the create payload) — if the name matches no available value for this
                     ticket, the ticket is still created, but the response carries a WARNING
                     and the priority has to be fixed with jira_update_priority.
        current_state: Required for PRJ2/bug (the Bug screen has no description field) —
                     describes the current state / the problem. Empty = not set.
        target_state:  Required for PRJ2/bug — describes the target state /
                     expected behaviour. Empty = not set.

    Returns: KEY — URL on success (with a note about the priority/link if given), or ERROR: ...
    when creating the ticket itself failed.
    """
    t0 = time.perf_counter()
    proj = project.upper()
    itype = issue_type.lower().strip()
    print(
        f"[jira-mcp] jira_create start project={proj} issue_type={itype} summary={summary!r}",
        file=sys.stderr, flush=True,
    )

    if not _PROJECTS:
        return "ERROR: jira_create is not configured — no issue_type_ids in config/jira-fields.json."
    if proj not in _PROJECTS:
        return f"ERROR: project must be one of {', '.join(sorted(_PROJECTS))} (got: {project!r})"
    issuetype_id = _ISSUE_TYPE_IDS.get((proj, itype))
    if issuetype_id is None:
        if itype == "epic":
            with_epic = ", ".join(sorted(p for p, t in _ISSUE_TYPE_IDS if t == "epic"))
            return (
                f"ERROR: an epic cannot be created in {proj}. Create the epic in a project that has one "
                f"({with_epic}) and link the {proj} tasks to it through epic_key "
                "(the epic link works across projects)."
            )
        return (
            f"ERROR: issue_type must be 'user-story', 'task', 'bug' or 'epic' "
            f"(available project/type combinations: {sorted(_ISSUE_TYPE_IDS)}; "
            f"got: project={proj!r}, issue_type={issue_type!r})."
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
        # Jira requires Epic Name on the epic create screen — missing = HTTP 400.
        fields[_EPIC_NAME_FIELD] = (epic_name or summary).strip()
    epic_key_warning = ""
    if epic_key:
        if is_epic:
            epic_key_warning = (
                " | WARNING: epic_key skipped — an epic does not link under another epic"
            )
        else:
            fields["customfield_10001"] = epic_key
    if proj not in _NO_WORK_TYPE_PROJECTS:
        if _CHANGE_TYPE_DEFAULT:
            _set_write_field(fields, "change_type", {"id": _CHANGE_TYPE_DEFAULT})
        if work_type:
            _set_write_field(fields, "work_type", {"id": _work_type_option(work_type)})
        if affects:
            _set_write_field(fields, "affects", [affects])
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
            # customfield_10020 is not on the epic create screen in any project —
            # sending it ends in HTTP 400 and the ticket is not created at all.
            sprint_warning = (
                f" | WARNING: sprint {sprint_id} skipped — epics have no sprint field"
            )
        else:
            fields["customfield_10020"] = int(sprint_id)
    if current_state:
        _set_write_field(fields, "current_state", current_state)
    if target_state:
        _set_write_field(fields, "target_state", target_state)

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
        return f"ERROR: cannot connect to Jira: {exc}"

    if resp.status_code not in (200, 201):
        _tlog(f"jira_create HTTP {resp.status_code}", t0)
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"

    key = resp.json().get("key", "?")
    ticket_url = f"{_jira.JIRA_URL}/browse/{key}"
    _tlog("jira_create done", t0, f"key={key}")

    # Jira can ignore the reporter in the create payload — we check only when somebody
    # explicitly asked for someone (empty reporter = the default owner, nothing to mix up).
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
                link_result = f" | linked [{link_type}] {link_to}"
            else:
                link_result = f" | WARNING: link to {link_to} failed (HTTP {lr.status_code})"
        except requests.RequestException as exc:
            link_result = f" | WARNING: link to {link_to} failed: {exc}"

    priority_result = ""
    if priority and key != "?":
        # Priority is not set in the create payload -- {"priority": {"name": ...}} at create
        # time hits the same instance-specific name-resolution quirk as jira_update_priority
        # (see _resolve_priority_id docstring). Set it right after creation via id instead.
        pr = _set_priority(key, priority)
        if pr == "OK":
            priority_result = f" | priority: {priority}"
        else:
            priority_result = f" | WARNING: priority not set ({pr})"

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
    """Change the Assignee on an existing Jira ticket.

    Works on newly created and old tickets alike.
    Get the user name from jira_search_users (the "name" field).

    Args:
        issue_key: Ticket key, e.g. "PRJ1-4568".
        assignee:  Jira user name (the "name" field from jira_search_users).

    Returns: "OK" on success or ERROR: ... on failure.
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
        return f"ERROR: cannot connect to Jira: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_update_assignee HTTP {resp.status_code}", t0)
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"
    _tlog("jira_update_assignee done", t0, f"key={issue_key} assignee={assignee}")
    return "OK"


@_write_tool
def jira_update_reporter(issue_key: str, reporter: str) -> str:
    """Change the Reporter on an existing Jira ticket.

    Works on newly created and old tickets alike.
    Get the user name from jira_search_users (the "name" field).

    Args:
        issue_key: Ticket key, e.g. "PRJ1-4568".
        reporter:  Jira user name (the "name" field from jira_search_users).

    Returns: "OK" on success or ERROR: ... on failure.
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
        return f"ERROR: cannot connect to Jira: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_update_reporter HTTP {resp.status_code}", t0)
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"
    _tlog("jira_update_reporter done", t0, f"key={issue_key} reporter={reporter}")
    return "OK"


@_write_tool
def jira_update_priority(issue_key: str, priority: str) -> str:
    """Change the priority on an existing Jira ticket.

    Works on newly created and old tickets alike. The priority is set by ID (resolved
    from the name through the ticket's editmeta), not by the name directly — on some Jira
    instances setting the priority by name alone fails for some values (verified
    empirically), even though exactly the same name is valid.

    Args:
        issue_key: Ticket key, e.g. "PRJ1-4568".
        priority:  Priority name exactly as in Jira, e.g. "Blocker", "Critical", "High",
                   "Medium", "Low". Priority names are defined per project in Jira —
                   if the name does not exist in THIS ticket's priority scheme, the tool
                   returns ERROR with the list of names available for this ticket.

    Returns: "OK" on success or ERROR: ... on failure.
    """
    t0 = time.perf_counter()
    result = _set_priority(issue_key, priority)
    _tlog("jira_update_priority done", t0, f"key={issue_key} priority={priority} -> {result}")
    return result


@_write_tool
def jira_update_fix_version(issue_key: str, version: str, action: str = "add") -> str:
    """Manage the Fix Version/s field on an existing Jira ticket: add, remove or replace.

    Works on newly created and old tickets alike.

    Args:
        issue_key: Ticket key, e.g. "PRJ1-3020".
        version:   Version name exactly as in Jira, e.g. "v 2.16n". Versions are defined
                   per project in Jira — if the name does not exist in this ticket's
                   project, the tool returns ERROR with the list of versions available for this ticket.
        action:    "add" (default) — ADDS version to the list, leaves versions already set alone.
                   "remove" — REMOVES version from the list, the rest stays unchanged.
                   "set" — REPLACES the whole Fix Version/s list with version alone
                   (one version; every other version set before disappears).

    Returns: "OK" on success or ERROR: ... on failure.
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
    work_type: str = "",
    affects: str = "",
    change_type: str = "",
    labels: str = "",
    epic_link: str = "",
    components: str = "",
    project_number: str = "",
    order_number: str = "",
) -> str:
    """Update fields on an existing Jira ticket.

    Works on any existing ticket. Pass only the fields you want to change.

    Args:
        issue_key:     Ticket key, e.g. "PRJ1-4568".
        work_type:     Work type: a label from config/jira-fields.json (work_type_options)
                       or an option ID. Empty = unchanged.
        affects:       Value of the "Affects" field, e.g. "FieldOps". Empty = unchanged.
        change_type:   Option ID of the "Change type" field. Empty = unchanged.
        labels:        Comma-separated labels, e.g. "service,customer-report".
                       REPLACES all existing labels. Empty = unchanged.
        epic_link:     Epic key, e.g. "PRJ1-4597". Sets the Epic Link (customfield_10001).
                       Empty = unchanged.
        components:    Comma-separated components, e.g. "unplanned_work,backend".
                       REPLACES all existing components — if you only want to add one,
                       first get the existing ones with jira_issue(key) and append to the list.
                       Empty = unchanged.
        project_number: Project number, plain text, e.g. "PRJ-123". Empty = unchanged.
        order_number:   Order number, plain text, e.g. "PO-456".
                        Empty = unchanged.

    Returns: "OK" on success or ERROR: ... on failure.
    """
    import json as _json
    t0 = time.perf_counter()
    fields: dict = {}
    if work_type:
        _set_write_field(fields, "work_type", {"id": _work_type_option(work_type)})
    if affects:
        _set_write_field(fields, "affects", [affects])
    if change_type:
        _set_write_field(fields, "change_type", {"id": change_type})
    if labels:
        fields["labels"] = [lb.strip() for lb in labels.split(",") if lb.strip()]
    if epic_link:
        fields["customfield_10001"] = epic_link.strip()
    if components:
        fields["components"] = [{"name": c.strip()} for c in components.split(",") if c.strip()]
    if project_number:
        _set_write_field(fields, "project_number", project_number.strip())
    if order_number:
        _set_write_field(fields, "order_number", order_number.strip())
    if not fields:
        return (
            "ERROR: give at least one field to change "
            "(work_type, affects, change_type, labels, epic_link, components, "
            "project_number or order_number)"
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
        return f"ERROR: cannot connect to Jira: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_update_ticket HTTP {resp.status_code}", t0)
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"
    _field_map = {
        "work_type": work_type, "affects": affects, "change_type": change_type,
        "labels": labels, "epic_link": epic_link, "components": components,
        "project_number": project_number, "order_number": order_number,
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
    """Update the description and/or the title (summary) of an existing Jira ticket.

    Use wiki markup in description (h2., h3., *, #, {code}, etc.).
    Pass only the fields you want to change — empty = unchanged.

    Args:
        issue_key:   Ticket key, e.g. "PRJ1-4846".
        description: New description text in Jira wiki markup. Empty = unchanged.
        summary:     New ticket title. Empty = unchanged.

    Returns: "OK" on success or ERROR: ... on failure.
    """
    import json as _json
    t0 = time.perf_counter()
    fields: dict = {}
    if description:
        fields["description"] = description
    if summary:
        fields["summary"] = summary
    if not fields:
        return "ERROR: give at least one field to change (description or summary)"
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
        return f"ERROR: cannot connect to Jira: {exc}"
    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_update_description HTTP {resp.status_code}", t0)
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"
    changed = ", ".join(f for f, v in [("description", description), ("summary", summary)] if v)
    _tlog("jira_update_description done", t0, f"key={issue_key} changed={changed}")
    return "OK"


@_write_tool
def jira_transition(issue_key: str, status: str) -> str:
    """Change the status (workflow transition) of an existing Jira ticket.

    Gets the ticket's available transitions and runs the one whose name matches
    the given status (case-insensitive, partial match).

    Args:
        issue_key: Ticket key, e.g. "PRJ1-4848".
        status:    Name of the target status, e.g. "Cancelled", "In Progress", "Done".

    Returns: "OK: <status name>" on success or ERROR: ... on failure.
    """
    import json as _json
    t0 = time.perf_counter()
    transitions_url = f"{_jira.JIRA_URL}/rest/api/2/issue/{issue_key}/transitions"
    try:
        r = requests.get(transitions_url, headers=_jira._HEADERS, timeout=15)
        r.raise_for_status()
        transitions = r.json().get("transitions", [])
    except requests.RequestException as exc:
        return f"ERROR: cannot connect to Jira (fetching transitions): {exc}"

    status_lower = status.strip().lower()
    match = next(
        (t for t in transitions if status_lower in t["to"]["name"].lower()),
        None,
    )
    if not match:
        available = ", ".join(t["to"]["name"] for t in transitions)
        return f"ERROR: status '{status}' not found. Available: {available}"

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
        return f"ERROR: cannot connect to Jira (running the transition): {exc}"

    if resp.status_code not in (200, 204):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        _tlog(f"jira_transition HTTP {resp.status_code}", t0)
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"

    _tlog("jira_transition done", t0, f"key={issue_key} -> {target_name}")
    return f"OK: {issue_key} → {target_name}"


@_write_tool
def jira_sprint_search(query: str, project: str) -> str:
    """Search a project's Jira sprints (active + future only).

    Searches every board of the project through the Agile REST API, filters sprints
    by name (case-insensitive substring), de-duplicates by sprint ID.
    Use it BEFORE jira_assign_sprint or jira_create(sprint_id=...) to find the sprint ID.

    Args:
        query:   Part of the sprint name, e.g. "FieldOps Sprint 13" or "Sprint 12".
        project: Jira project key, e.g. "PRJ2" or "PRJ1".

    Returns JSON: {sprints: [{id, name, state, board_id, board_name}], count, query}.
    If count=0 — no matching sprint (a valid result, not an error).
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
        return f"ERROR: {err}"

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
    """Assign an existing Jira ticket to a sprint through the Agile REST API.

    Call ONLY after the user has explicitly confirmed — this is a write.
    Get the sprint ID from jira_sprint_search. Works on active and future sprints.
    A ticket can also be assigned to a sprint after it was created (the fallback when
    customfield_10020 does not work in jira_create for a given project/type).

    Args:
        issue_key: Jira ticket key, e.g. "PRJ2-123".
        sprint_id: Numeric sprint ID (from jira_sprint_search).

    Returns: "OK" on success or "ERROR: ..." on failure.
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
        return f"ERROR: cannot connect to Jira: {exc}"

    if resp.status_code not in (200, 204):
        _tlog(f"jira_assign_sprint HTTP {resp.status_code}", t0)
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        return f"ERROR: Jira Agile HTTP {resp.status_code} — {detail}"

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
    """Add a local file as an attachment to a Jira ticket.

    Use it after jira_create when the user's message carried screenshots or files
    (passed as paths in the message context).

    Args:
        issue_key: Ticket key, e.g. "PRJ1-1234".
        file_path: Absolute path to the file on disk, e.g. "/Users/.../screenshot.png".

    Returns: "OK: <filename> added" on success or ERROR: ... on failure.
    """
    import mimetypes as _mime
    t0 = time.perf_counter()
    if not _ISSUE_KEY_RE.match((issue_key or "").strip().upper()):
        return "ERROR: issue_key must look like PROJECT-NNN (e.g. PRJ1-123)"
    issue_key = issue_key.strip().upper()
    p = Path(file_path)
    if not p.exists():
        return f"ERROR: file does not exist: {file_path}"
    if not _attachment_allowed(p):
        _tlog("jira_attach rejected (path not allowed)", t0, p.name)
        return "ERROR: only attachments from Teams messages can be attached (Bob's temporary files)"
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
        return f"ERROR: cannot connect to Jira: {exc}"
    if resp.status_code not in (200, 201):
        _tlog(f"jira_attach HTTP {resp.status_code}", t0)
        return f"ERROR: Jira HTTP {resp.status_code} — {resp.text[:200]}"
    _tlog("jira_attach done", t0, f"key={issue_key} file={p.name}")
    return f"OK: {p.name} added to {issue_key}"


@_write_tool
def jira_link(
    inward_key: str,
    outward_key: str,
    link_type: str = "Related",
) -> str:
    """Set a native link between two Jira tickets.

    Use it when the user asks to link tasks, "blocks", "relates to", "is blocked by" etc.
    Call it independently of comments — it is a separate action on the Jira API.

    Link types (link_type):
      "Related"       — relates to / relates to  (default)
      "Blocks"        — blocks / is blocked by
      "Duplicate"     — duplicates / is duplicated by
      "Cloners"       — clones / is cloned by
      "Problem/Incident" — causes / is caused by

    Direction: inward_key [link_type] outward_key
      e.g. "PRJ1-1" Blocks "PRJ1-2"  → PRJ1-1 blocks PRJ1-2

    Args:
        inward_key:  Key of the ticket on the inward side, e.g. "PRJ1-123".
        outward_key: Key of the ticket on the outward side, e.g. "PRJ1-456".
        link_type:   Link type (see above). Default "Related".

    Returns: "OK: PRJ1-123 [Relates] PRJ1-456" or ERROR: ...
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
        return f"ERROR: cannot connect to Jira: {exc}"
    _tlog(f"jira_link HTTP {resp.status_code}", t0, f"{inward_key}->{outward_key} type={link_type}")
    if resp.status_code not in (200, 201):
        try:
            body = resp.json()
            msgs = body.get("errorMessages", []) + list(body.get("errors", {}).values())
            detail = "; ".join(msgs) if msgs else resp.text[:300]
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        return f"ERROR: Jira HTTP {resp.status_code} — {detail}"
    return f"OK: {inward_key} [{link_type}] {outward_key}"


if __name__ == "__main__":
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp
    run_mcp(mcp, default_port=MCP_PORTS[_SERVER_NAME])
