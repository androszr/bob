"""Unit tests for jira_freshness() and jira_refresh().

Strategy: load scripts/jira/mcp_server.py under a unique module alias,
pre-stub heavy imports (jira_client, logts, requests,
mcp.server.fastmcp) so no live network or Atlassian credentials are needed.
"""

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_JIRA_DIR = _REPO_ROOT / "scripts" / "jira"
_JIRA_MCP_PATH = _JIRA_DIR / "mcp_server.py"


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

def _make_jira_stubs() -> dict[str, ModuleType]:
    """Create minimal stubs for all heavy imports in jira/mcp_server.py."""
    # logts — just a no-op patcher
    logts_stub = ModuleType("logts")

    # mcp.server.fastmcp
    fastmcp_mod = ModuleType("mcp")
    fastmcp_server = ModuleType("mcp.server")
    fastmcp_fastmcp = ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        def __init__(self, name: str) -> None:
            self.name = name

        def tool(self):
            """Decorator that just returns the function unchanged."""
            def _decorator(fn):
                return fn
            return _decorator

        def run(self):
            pass

    fastmcp_fastmcp.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]

    # requests
    requests_stub = ModuleType("requests")
    requests_stub.RequestException = Exception  # type: ignore[attr-defined]
    requests_stub.Response = object  # type: ignore[attr-defined]  # adnotacja w _request_with_retry

    # jira_client stub
    jira_client_stub = ModuleType("jira_client")
    jira_client_stub.JIRA_URL = "https://fake.jira"  # type: ignore[attr-defined]
    jira_client_stub._HEADERS = {}  # type: ignore[attr-defined]
    jira_client_stub.VERIFY_SSL = False  # type: ignore[attr-defined]
    jira_client_stub.find_issue = MagicMock(return_value=[])  # type: ignore[attr-defined]
    jira_client_stub.get_extra_fields = MagicMock(return_value={})  # type: ignore[attr-defined]
    jira_client_stub.EXTRA_CUSTOM_FIELDS = {"customfield_10100": "typ_pracy"}  # type: ignore[attr-defined]
    jira_client_stub._extract_field_value = (  # type: ignore[attr-defined]
        lambda raw: raw.get("value") if isinstance(raw, dict) else raw
    )

    # mcp_admin stubs (used only in __main__ guard)
    mcp_admin_stub = ModuleType("mcp_admin")
    mcp_admin_ports = ModuleType("mcp_admin.ports")
    mcp_admin_ports.MCP_PORTS = {}  # type: ignore[attr-defined]
    mcp_admin_transport = ModuleType("mcp_admin.transport")
    mcp_admin_transport.run_mcp = MagicMock()  # type: ignore[attr-defined]

    return {
        "logts": logts_stub,
        "mcp": fastmcp_mod,
        "mcp.server": fastmcp_server,
        "mcp.server.fastmcp": fastmcp_fastmcp,
        "requests": requests_stub,
        "jira_client": jira_client_stub,
        "mcp_admin": mcp_admin_stub,
        "mcp_admin.ports": mcp_admin_ports,
        "mcp_admin.transport": mcp_admin_transport,
    }


def _load_jira_mcp(stubs: dict[str, ModuleType]) -> ModuleType:
    mod_name = "jira_mcp_server_under_test"
    sys.modules.pop(mod_name, None)

    saved: dict = {}
    for name, stub in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = stub

    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(_JIRA_MCP_PATH))
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def jira_mcp(tmp_path):
    stubs = _make_jira_stubs()
    return _load_jira_mcp(stubs)


def _mtime_iso(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------------
# F: jira_create — sprint_id sets customfield_10020
# ---------------------------------------------------------------------------

def _make_post_mock(status_code: int = 201, response_json: dict | None = None):
    """Return a (mock_post, captured) tuple for intercepting requests.post calls."""
    from unittest.mock import MagicMock

    captured: dict = {}
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = response_json or {"key": "PRJ2-999"}

    def _post(url, data=None, headers=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return resp

    return _post, captured


def test_jira_create_with_sprint_id_sets_customfield(jira_mcp):
    """jira_create(sprint_id='9002') must include customfield_10020 = 9002 in POST payload."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ2-999"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ2",
        summary="Test sprint field",
        issue_type="task",
        sprint_id="9002",
    )

    assert "BLAD" not in result
    assert "PRJ2-999" in result
    assert "data" in captured
    payload = json.loads(captured["data"])
    assert payload["fields"]["customfield_10020"] == 9002


def test_jira_create_without_sprint_id_no_customfield(jira_mcp):
    """jira_create without sprint_id must NOT include customfield_10020 in POST payload."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ2-998"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ2",
        summary="Test no sprint",
        issue_type="task",
    )

    assert "BLAD" not in result
    assert "data" in captured
    payload = json.loads(captured["data"])
    assert "customfield_10020" not in payload["fields"]


def test_jira_create_without_reporter_falls_back_to_default(jira_mcp):
    """jira_create without reporter must still set fields.reporter — PRJ1 (and others)
    reject creation with a required-field validation error if reporter is omitted."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-999"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Test no reporter",
        issue_type="task",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert payload["fields"]["reporter"] == {"name": jira_mcp._DEFAULT_REPORTER}


def test_jira_create_without_owner_login_omits_reporter(jira_mcp, monkeypatch):
    """OWNER_JIRA_LOGIN unset: no reporter in the payload (Jira uses the token owner)
    instead of sending an empty name."""
    monkeypatch.setattr(jira_mcp, "_DEFAULT_REPORTER", "")
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-999"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(project="PRJ1", summary="Bez reportera", issue_type="task")

    assert "BLAD" not in result
    assert "reporter" not in json.loads(captured["data"])["fields"]


def test_jira_create_without_issue_type_config_reports_not_configured(jira_mcp, monkeypatch):
    """No config/jira-fields.json: jira_create says it is not configured, never POSTs."""
    monkeypatch.setattr(jira_mcp, "_PROJECTS", frozenset())
    mock_post, captured = _make_post_mock(201)
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(project="PRJ1", summary="x", issue_type="task")

    assert result.startswith("BLAD") and "nie jest skonfigurowane" in result
    assert captured == {}


def test_jira_create_project_list_comes_from_issue_type_config(jira_mcp):
    result = jira_mcp.jira_create(project="NOPE", summary="x", issue_type="task")
    assert result.startswith("BLAD: project musi byc jednym z PRJ1, PRJ2, PRJ3, PRJ4, PRJ5")


def test_jira_create_with_reporter_uses_given_value(jira_mcp):
    """jira_create(reporter=...) must use the explicit value, not the default fallback."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1000"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Test explicit reporter",
        issue_type="task",
        reporter="user44@example.com",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert payload["fields"]["reporter"] == {"name": "user44@example.com"}


# ---------------------------------------------------------------------------
# G: jira_create / jira_update_priority — priority resolved via editmeta, set by id
#
# _resolve_priority_id/_set_priority read /issue/{key}/editmeta for allowedValues, then PUT
# {"priority": {"id": ...}} -- NOT {"name": ...} directly. This sidesteps a real instance
# quirk: setting priority by name returns HTTP 400 "Nazwa priorytetu
# jest niepoprawna" for at least "Sredni" even with the exact server-provided name string;
# by id works reliably.
# ---------------------------------------------------------------------------

def _make_get_mock(status_code: int = 200, response_json: dict | None = None):
    """Return a (mock_get, captured) tuple for intercepting requests.get calls."""
    captured: dict = {}
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = response_json or {}

    def _get(url, headers=None, verify=None, timeout=None, **kwargs):
        captured["url"] = url
        return resp

    return _get, captured


_EDITMETA_ALLOWED = {
    "fields": {
        "priority": {
            "allowedValues": [
                {"id": "10601", "name": "Wysoki"},
                {"id": "10301", "name": "Sredni"},
                {"id": "10603", "name": "Niski"},
                {"id": "2", "name": "Krytyczny"},
            ]
        }
    }
}


def test_jira_create_with_priority_sets_it_via_followup_put(jira_mcp):
    """jira_create(priority='Wysoki') must NOT put priority in the create payload -- it
    resolves the id via editmeta and PUTs it as a separate follow-up call."""
    mock_post, post_captured = _make_post_mock(201, {"key": "PRJ1-1"})
    mock_get, _ = _make_get_mock(200, _EDITMETA_ALLOWED)
    mock_put, put_captured = _make_put_mock(204)
    jira_mcp.requests.post = mock_post
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Test priority field",
        issue_type="task",
        priority="Wysoki",
    )

    assert "BLAD" not in result
    assert "priorytet: Wysoki" in result
    create_payload = json.loads(post_captured["data"])
    assert "priority" not in create_payload["fields"]
    put_payload = json.loads(put_captured["data"])
    assert put_payload["fields"]["priority"] == {"id": "10601"}


def test_jira_create_without_priority_no_followup_call(jira_mcp):
    """jira_create without priority must not touch editmeta/PUT at all."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-2"})
    mock_get, get_captured = _make_get_mock(200, _EDITMETA_ALLOWED)
    jira_mcp.requests.post = mock_post
    jira_mcp.requests.get = mock_get

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Test no priority",
        issue_type="task",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert "priority" not in payload["fields"]
    assert "url" not in get_captured  # editmeta never called


def test_jira_create_with_unknown_priority_still_creates_ticket_with_warning(jira_mcp):
    """An unresolvable priority name must not fail ticket creation -- it surfaces a warning."""
    mock_post, _ = _make_post_mock(201, {"key": "PRJ1-3"})
    mock_get, _ = _make_get_mock(200, _EDITMETA_ALLOWED)
    jira_mcp.requests.post = mock_post
    jira_mcp.requests.get = mock_get

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Test bad priority",
        issue_type="task",
        priority="Nieistniejacy",
    )

    assert result.startswith("PRJ1-3")
    assert "OSTRZEZENIE" in result
    assert "priorytet nie ustawiony" in result


def test_jira_update_priority_success(jira_mcp):
    """jira_update_priority resolves the name via editmeta and PUTs {"priority": {"id": ...}}."""
    mock_get, _ = _make_get_mock(200, _EDITMETA_ALLOWED)
    mock_put, put_captured = _make_put_mock(204)
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_priority("PRJ1-5394", "Wysoki")

    assert result == "OK"
    assert put_captured["url"].endswith("/issue/PRJ1-5394")
    payload = json.loads(put_captured["data"])
    assert payload["fields"]["priority"] == {"id": "10601"}


def test_jira_update_priority_unknown_name_returns_blad_with_options(jira_mcp):
    """An unresolvable priority name must return BLAD listing the allowed names, no PUT sent."""
    mock_get, _ = _make_get_mock(200, _EDITMETA_ALLOWED)
    mock_put = MagicMock(side_effect=AssertionError("PUT must not be called"))
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_priority("PRJ1-5394", "Nieistniejacy")

    assert result.startswith("BLAD: ")
    assert "Nieistniejacy" in result
    assert "Wysoki" in result  # lists available options


def test_jira_update_priority_put_http_error_returns_blad(jira_mcp):
    """A resolved priority id whose PUT fails must return a BLAD: message."""
    mock_get, _ = _make_get_mock(200, _EDITMETA_ALLOWED)
    mock_put, _ = _make_put_mock(400, {"errorMessages": ["some other validation error"]})
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_priority("PRJ1-5394", "Wysoki")

    assert result.startswith("BLAD: ")
    assert "some other validation error" in result


# ---------------------------------------------------------------------------
# H: jira_update_fix_version — add / remove / set, resolved via editmeta like priority
# ---------------------------------------------------------------------------

_EDITMETA_FIXVERSIONS = {
    "fields": {
        "fixVersions": {
            "allowedValues": [
                {"id": "380500", "name": "v 2.16n"},
                {"id": "336500", "name": "v 2.5.1n"},
            ]
        }
    }
}


def test_jira_update_fix_version_add_sends_update_add_payload(jira_mcp):
    mock_get, _ = _make_get_mock(200, _EDITMETA_FIXVERSIONS)
    mock_put, put_captured = _make_put_mock(204)
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_fix_version("PRJ1-3020", "v 2.16n", action="add")

    assert result == "OK"
    payload = json.loads(put_captured["data"])
    assert payload == {"update": {"fixVersions": [{"add": {"id": "380500"}}]}}


def test_jira_update_fix_version_remove_sends_update_remove_payload(jira_mcp):
    mock_get, _ = _make_get_mock(200, _EDITMETA_FIXVERSIONS)
    mock_put, put_captured = _make_put_mock(204)
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_fix_version("PRJ1-3020", "v 2.16n", action="remove")

    assert result == "OK"
    payload = json.loads(put_captured["data"])
    assert payload == {"update": {"fixVersions": [{"remove": {"id": "380500"}}]}}


def test_jira_update_fix_version_set_sends_fields_replace_payload(jira_mcp):
    mock_get, _ = _make_get_mock(200, _EDITMETA_FIXVERSIONS)
    mock_put, put_captured = _make_put_mock(204)
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_fix_version("PRJ1-3020", "v 2.5.1n", action="set")

    assert result == "OK"
    payload = json.loads(put_captured["data"])
    assert payload == {"fields": {"fixVersions": [{"id": "336500"}]}}


def test_jira_update_fix_version_default_action_is_add(jira_mcp):
    mock_get, _ = _make_get_mock(200, _EDITMETA_FIXVERSIONS)
    mock_put, put_captured = _make_put_mock(204)
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    jira_mcp.jira_update_fix_version("PRJ1-3020", "v 2.16n")

    payload = json.loads(put_captured["data"])
    assert "add" in payload["update"]["fixVersions"][0]


def test_jira_update_fix_version_invalid_action_returns_blad_no_http_calls(jira_mcp):
    mock_get = MagicMock(side_effect=AssertionError("editmeta must not be called"))
    mock_put = MagicMock(side_effect=AssertionError("PUT must not be called"))
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_fix_version("PRJ1-3020", "v 2.16n", action="delete_everything")

    assert result.startswith("BLAD: ")
    assert "action" in result


def test_jira_update_fix_version_unknown_name_returns_blad_with_options(jira_mcp):
    mock_get, _ = _make_get_mock(200, _EDITMETA_FIXVERSIONS)
    mock_put = MagicMock(side_effect=AssertionError("PUT must not be called"))
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_fix_version("PRJ1-3020", "v 9.9.9nieistniejaca", action="add")

    assert result.startswith("BLAD: ")
    assert "v 2.16n" in result  # lists available options


def test_jira_update_fix_version_http_error_returns_blad(jira_mcp):
    mock_get, _ = _make_get_mock(200, _EDITMETA_FIXVERSIONS)
    mock_put, _ = _make_put_mock(400, {"errorMessages": ["fixVersions: invalid transition"]})
    jira_mcp.requests.get = mock_get
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_fix_version("PRJ1-3020", "v 2.16n", action="add")

    assert result.startswith("BLAD: ")
    assert "invalid transition" in result


# ---------------------------------------------------------------------------
# jira_issue — merges Typ Pracy / Dotyczy / Rodzaj Zmiany missing from the base issue
# ---------------------------------------------------------------------------

class _FakeCockpitClient:
    """Stand-in client returning a minimal issue."""

    def get_issue(self, key):
        return {"key": key, "fields": {"summary": "Test ticket"}}


# ---------------------------------------------------------------------------
# jira_update_ticket — numer_projektu / numer_zamowienia (plain string fields)
# ---------------------------------------------------------------------------

def _make_put_mock(status_code: int = 204, response_json: dict | None = None):
    """Return a (mock_put, captured) tuple for intercepting requests.put calls."""
    captured: dict = {}
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = response_json or {}

    def _put(url, data=None, headers=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return resp

    return _put, captured


def test_jira_update_ticket_numer_projektu_plain_string(jira_mcp):
    """numer_projektu must be written as a plain string, not {'id': ...} or a list."""
    mock_put, captured = _make_put_mock()
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_ticket(issue_key="PRJ1-4828", numer_projektu="PRJ-123")

    assert result == "OK"
    payload = json.loads(captured["data"])
    value = payload["fields"]["customfield_10104"]
    assert value == "PRJ-123"
    assert isinstance(value, str)


def test_jira_update_ticket_numer_zamowienia_plain_string(jira_mcp):
    """numer_zamowienia must be written as a plain string, not {'id': ...} or a list."""
    mock_put, captured = _make_put_mock()
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_ticket(issue_key="PRJ1-4828", numer_zamowienia="ZAM-456")

    assert result == "OK"
    payload = json.loads(captured["data"])
    value = payload["fields"]["customfield_10103"]
    assert value == "ZAM-456"
    assert isinstance(value, str)


def test_jira_update_ticket_both_numer_fields_at_once(jira_mcp):
    """Both numer_projektu and numer_zamowienia can be set in a single call."""
    mock_put, captured = _make_put_mock()
    jira_mcp.requests.put = mock_put

    result = jira_mcp.jira_update_ticket(
        issue_key="PRJ1-4828",
        numer_projektu="PRJ-123",
        numer_zamowienia="ZAM-456",
    )

    assert result == "OK"
    payload = json.loads(captured["data"])
    assert payload["fields"]["customfield_10104"] == "PRJ-123"
    assert payload["fields"]["customfield_10103"] == "ZAM-456"


def test_jira_update_ticket_no_fields_returns_error(jira_mcp):
    """Calling jira_update_ticket without any field must return the BLAD message."""
    result = jira_mcp.jira_update_ticket(issue_key="PRJ1-4828")

    assert result.startswith("BLAD: podaj co najmniej jedno pole")


# ---------------------------------------------------------------------------
# _request_with_retry — shared retry-with-backoff helper
# ---------------------------------------------------------------------------

def test_request_with_retry_success_first_try_no_sleep(jira_mcp, monkeypatch):
    slept = []
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    def _req():
        calls["n"] += 1
        return MagicMock(status_code=200, headers={})

    resp = jira_mcp._request_with_retry(_req)

    assert resp.status_code == 200
    assert calls["n"] == 1
    assert slept == []


def test_request_with_retry_retries_once_on_429_then_succeeds(jira_mcp, monkeypatch):
    slept = []
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: slept.append(s))
    responses = [
        MagicMock(status_code=429, headers={}),
        MagicMock(status_code=200, headers={}),
    ]
    calls = {"n": 0}

    def _req():
        resp = responses[calls["n"]]
        calls["n"] += 1
        return resp

    resp = jira_mcp._request_with_retry(_req)

    assert resp.status_code == 200
    assert calls["n"] == 2
    assert slept == [1.0]


def test_request_with_retry_gives_up_after_max_retries(jira_mcp, monkeypatch):
    slept = []
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    def _req():
        calls["n"] += 1
        return MagicMock(status_code=503, headers={})

    resp = jira_mcp._request_with_retry(_req, max_retries=1)

    assert resp.status_code == 503
    assert calls["n"] == 2  # initial attempt + 1 retry, then gives up
    assert slept == [1.0]


def test_request_with_retry_respects_retry_after_header(jira_mcp, monkeypatch):
    slept = []
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: slept.append(s))
    responses = [
        MagicMock(status_code=429, headers={"Retry-After": "3"}),
        MagicMock(status_code=200, headers={}),
    ]
    calls = {"n": 0}

    def _req():
        resp = responses[calls["n"]]
        calls["n"] += 1
        return resp

    jira_mcp._request_with_retry(_req)

    assert slept == [3.0]


def test_request_with_retry_non_retryable_status_returns_immediately(jira_mcp, monkeypatch):
    slept = []
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    def _req():
        calls["n"] += 1
        return MagicMock(status_code=400, headers={})

    resp = jira_mcp._request_with_retry(_req)

    assert resp.status_code == 400
    assert calls["n"] == 1
    assert slept == []


# ---------------------------------------------------------------------------
# jira_link — retry on transient errors
# ---------------------------------------------------------------------------

def test_jira_link_success_no_retry(jira_mcp, monkeypatch):
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: None)
    mock_post = MagicMock(return_value=MagicMock(status_code=201, headers={}))
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_link("PRJ1-1", "PRJ1-2")

    assert result == "OK: PRJ1-1 [Related] PRJ1-2"
    assert mock_post.call_count == 1


def test_jira_link_retries_on_429_then_succeeds(jira_mcp, monkeypatch):
    slept = []
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: slept.append(s))
    mock_post = MagicMock(side_effect=[
        MagicMock(status_code=429, headers={}),
        MagicMock(status_code=201, headers={}),
    ])
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_link("PRJ1-1", "PRJ1-2", link_type="Blocks")

    assert result == "OK: PRJ1-1 [Blocks] PRJ1-2"
    assert mock_post.call_count == 2
    assert slept == [1.0]


def test_jira_link_gives_up_after_retry_exhausted(jira_mcp, monkeypatch):
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: None)
    resp_503 = MagicMock(status_code=503, headers={})
    resp_503.json.side_effect = ValueError("not json")
    resp_503.text = "Service Unavailable"
    mock_post = MagicMock(return_value=resp_503)
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_link("PRJ1-1", "PRJ1-2")

    assert result.startswith("BLAD: Jira HTTP 503")
    assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# jira_attach — retry on transient errors + file-pointer reset across attempts
# ---------------------------------------------------------------------------

def test_jira_attach_success_no_retry(jira_mcp, monkeypatch, tmp_path):
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: None)
    monkeypatch.setattr(jira_mcp.tempfile, "gettempdir", lambda: str(tmp_path))
    f = tmp_path / "bob_attach_screenshot.png"
    f.write_bytes(b"fake-png-bytes")
    mock_post = MagicMock(return_value=MagicMock(status_code=201, headers={}))
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_attach("PRJ1-1", str(f))

    assert result == "OK: bob_attach_screenshot.png dodany do PRJ1-1"
    assert mock_post.call_count == 1


def test_jira_attach_retries_on_500_and_resends_full_file(jira_mcp, monkeypatch, tmp_path):
    """A failed first attempt must not leave the stream exhausted for the retry."""
    slept = []
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(jira_mcp.tempfile, "gettempdir", lambda: str(tmp_path))
    f = tmp_path / "bob_attach_screenshot.png"
    f.write_bytes(b"fake-png-bytes")
    reads: list[bytes] = []

    def _post(url, headers=None, files=None, verify=None, timeout=None):
        fh = files["file"][1]
        reads.append(fh.read())  # simulate requests actually consuming the stream
        if len(reads) == 1:
            return MagicMock(status_code=500, headers={})
        return MagicMock(status_code=201, headers={})

    jira_mcp.requests.post = _post

    result = jira_mcp.jira_attach("PRJ1-1", str(f))

    assert result == "OK: bob_attach_screenshot.png dodany do PRJ1-1"
    assert reads == [b"fake-png-bytes", b"fake-png-bytes"]
    assert slept == [1.0]


# ---------------------------------------------------------------------------
# jira_refresh_schedule — real schedule config + log tail + daemon status
# ---------------------------------------------------------------------------

def _write_schedule(tmp_path, data: dict):
    f = tmp_path / "refresh-schedule.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# H: jira_create — reporter podstawiany z osoby proszacej na czacie
# ---------------------------------------------------------------------------

def _make_user_search_mock(users_by_query: dict | None = None, default: list | None = None):
    """Mock requests.get dla /user/search. users_by_query: {fragment_zapytania: [userzy]}."""
    from unittest.mock import MagicMock

    calls: list = []

    def _get(url, headers=None, params=None, timeout=None, **kwargs):
        calls.append({"url": url, "params": params})
        resp = MagicMock()
        if "/user/search" in url:
            q = (params or {}).get("username", "")
            hits = default if default is not None else []
            for frag, users in (users_by_query or {}).items():
                if frag.lower() in q.lower():
                    hits = users
                    break
            resp.status_code = 200
            resp.json.return_value = hits
        else:
            resp.status_code = 404
            resp.json.return_value = {}
        return resp

    return _get, calls


def _make_issue_get_mock(reporter_name: str):
    """Mock requests.get dla GET /issue/<key>?fields=reporter — zwraca danego reportera."""
    from unittest.mock import MagicMock

    def _get(url, headers=None, params=None, timeout=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"fields": {"reporter": {"name": reporter_name}}}
        return resp

    return _get


def test_jira_create_resolves_display_name_to_jira_login(jira_mcp):
    """reporter='Tomasz Wiatr' (imie i nazwisko z czatu) musi trafic do payloadu jako login."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1001"})
    jira_mcp.requests.post = mock_post
    search_get, _calls = _make_user_search_mock(
        {"wiatr": [{"name": "user60@example.com",
                        "displayName": "Tomasz Wiatr",
                        "emailAddress": "user60@example.com"}]},
    )
    # _resolve_reporter idzie przez /user/search, _verify_reporter przez /issue/<key>
    def _routed_get(url, **kwargs):
        if "/user/search" in url:
            return search_get(url, **kwargs)
        return _make_issue_get_mock("user60@example.com")(url, **kwargs)
    jira_mcp.requests.get = _routed_get

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Ticket zalozony na prosbe Tomasza",
        issue_type="task",
        reporter="Tomasz Wiatr",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert payload["fields"]["reporter"] == {"name": "user60@example.com"}
    assert "OSTRZEZENIE" not in result


def test_jira_create_login_reporter_skips_user_search(jira_mcp):
    """Gotowy login (np. user15@example.com) nie moze isc przez wyszukiwanie."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1002"})
    jira_mcp.requests.post = mock_post
    search_get, calls = _make_user_search_mock(default=[])

    def _routed_get(url, **kwargs):
        if "/user/search" in url:
            return search_get(url, **kwargs)
        return _make_issue_get_mock("user15@example.com")(url, **kwargs)
    jira_mcp.requests.get = _routed_get

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Pomysl technika",
        issue_type="task",
        reporter="user15@example.com",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert payload["fields"]["reporter"] == {"name": "user15@example.com"}
    assert calls == [], "login nie powinien uruchamiac /user/search"


def test_jira_create_unknown_person_falls_back_and_warns(jira_mcp):
    """Brak konta w Jirze: fallback na domyslnego reportera + JAWNE ostrzezenie w zwrotce."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1003"})
    jira_mcp.requests.post = mock_post
    search_get, _calls = _make_user_search_mock(default=[])

    def _routed_get(url, **kwargs):
        if "/user/search" in url:
            return search_get(url, **kwargs)
        return _make_issue_get_mock(jira_mcp._DEFAULT_REPORTER)(url, **kwargs)
    jira_mcp.requests.get = _routed_get

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Zgloszenie od osoby bez konta",
        issue_type="task",
        reporter="Jan Bez Konta",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert payload["fields"]["reporter"] == {"name": jira_mcp._DEFAULT_REPORTER}
    assert "OSTRZEZENIE" in result
    assert "Jan Bez Konta" in result


def test_jira_create_fixes_reporter_when_jira_ignores_it(jira_mcp):
    """Gdy Jira utworzy ticket z innym reporterem, kod musi poprawic go PUT-em i to zaraportowac."""
    from unittest.mock import MagicMock

    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1004"})
    jira_mcp.requests.post = mock_post

    search_get, _calls = _make_user_search_mock(
        {"wiatr": [{"name": "user60@example.com",
                        "displayName": "Tomasz Wiatr"}]},
    )

    def _routed_get(url, **kwargs):
        if "/user/search" in url:
            return search_get(url, **kwargs)
        # Jira zignorowala reportera z payloadu — zwraca wlasciciela tokenu
        return _make_issue_get_mock("jan.kowalski@example.com")(url, **kwargs)
    jira_mcp.requests.get = _routed_get

    put_calls: list = []

    def _put(url, data=None, headers=None, timeout=None, **kwargs):
        put_calls.append({"url": url, "data": data})
        resp = MagicMock()
        resp.status_code = 204
        return resp
    jira_mcp.requests.put = _put

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Reporter ignorowany przy tworzeniu",
        issue_type="task",
        reporter="Tomasz Wiatr",
    )

    assert "BLAD" not in result
    assert len(put_calls) == 1, "brak poprawki PUT gdy Jira zignorowala reportera"
    put_payload = json.loads(put_calls[0]["data"])
    assert put_payload["fields"]["reporter"] == {"name": "user60@example.com"}
    assert "poprawiony" in result


def test_jira_create_empty_reporter_does_not_verify(jira_mcp):
    """Puste reporter = domyslny wlasciciel: zadnego /user/search ani weryfikacji po fakcie."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1005"})
    jira_mcp.requests.post = mock_post
    get_calls: list = []

    def _routed_get(url, **kwargs):
        get_calls.append(url)
        return _make_issue_get_mock(jira_mcp._DEFAULT_REPORTER)(url, **kwargs)
    jira_mcp.requests.get = _routed_get

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Bez reportera",
        issue_type="task",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert payload["fields"]["reporter"] == {"name": jira_mcp._DEFAULT_REPORTER}
    assert get_calls == [], "pusty reporter nie powinien wolac Jiry po nic"
    assert "OSTRZEZENIE" not in result


# ---------------------------------------------------------------------------
# I: jira_create — epiki (id 10000 w fixture)
#
# Wartosci sprawdzone empirycznie przez /rest/api/2/issue/createmeta/{proj}/issuetypes[/10000]:
# Epik jest na ekranie tworzenia w PRJ1/PRJ2/PRJ5/PRJ4 (nie w PRJ3 -- fixture), wymaga
# customfield_10004 (Epic Name) i nie ma pola sprintu (customfield_10020).
# ---------------------------------------------------------------------------

def test_jira_create_epic_uses_zlecenie_type_and_epic_name(jira_mcp):
    """issue_type='epic' -> issuetype 10000 + Epic Name = summary."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ2-1000"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ2",
        summary="Stabilizacja FieldOps",
        issue_type="epic",
    )

    assert "BLAD" not in result
    assert "PRJ2-1000" in result
    payload = json.loads(captured["data"])
    assert payload["fields"]["issuetype"] == {"id": "10000"}
    assert payload["fields"]["customfield_10004"] == "Stabilizacja FieldOps"


def test_jira_create_epic_name_overrides_summary(jira_mcp):
    """epic_name= ma pierwszenstwo nad summary w customfield_10004."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1001"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="[Q3] Bardzo dlugi tytul epika",
        issue_type="epic",
        epic_name="Q3 Serwis",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert payload["fields"]["customfield_10004"] == "Q3 Serwis"


def test_jira_create_non_epic_has_no_epic_name(jira_mcp):
    """Zwykly task nie dostaje customfield_10004 — nie ma tego pola na swoim ekranie."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1002"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Zwykle zadanie",
        issue_type="task",
        epic_name="ignorowane",
    )

    assert "BLAD" not in result
    payload = json.loads(captured["data"])
    assert "customfield_10004" not in payload["fields"]


def test_jira_create_epic_drops_sprint_id_with_warning(jira_mcp):
    """Epik + sprint_id: customfield_10020 pominiete (400 z Jiry), ticket powstaje z ostrzezeniem."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ2-1003"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ2",
        summary="Epik ze sprintem",
        issue_type="epic",
        sprint_id="9002",
    )

    assert "BLAD" not in result
    assert "PRJ2-1003" in result
    assert "OSTRZEZENIE" in result and "9002" in result
    payload = json.loads(captured["data"])
    assert "customfield_10020" not in payload["fields"]


def test_jira_create_epic_drops_epic_key_with_warning(jira_mcp):
    """Epik nie podpina sie pod inny epik — customfield_10001 pominiete, ostrzezenie w zwrocie."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1004"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Epik pod epikiem",
        issue_type="epic",
        epic_key="PRJ1-4823",
    )

    assert "BLAD" not in result
    assert "OSTRZEZENIE" in result
    payload = json.loads(captured["data"])
    assert "customfield_10001" not in payload["fields"]


def test_jira_create_epic_in_project_without_epic_is_rejected_without_post(jira_mcp):
    """PRJ3 nie ma epika w konfiguracji — BLAD z podpowiedzia, bez wolania Jiry."""
    posted: list = []

    def _post(url, **kwargs):
        posted.append(url)
        raise AssertionError("nie powinno dojsc do POST-a")
    jira_mcp.requests.post = _post

    result = jira_mcp.jira_create(
        project="PRJ3",
        summary="Epik w PRJ3",
        issue_type="epic",
    )

    assert result.startswith("BLAD")
    assert "PRJ1" in result and "PRJ2" in result
    assert posted == []


def test_jira_create_epic_keeps_project_custom_fields(jira_mcp):
    """Epik w PRJ1 nadal dostaje Rodzaj Zmiany / Typ Pracy / Dotyczy — sa na ekranie epika."""
    mock_post, captured = _make_post_mock(201, {"key": "PRJ1-1006"})
    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_create(
        project="PRJ1",
        summary="Epik z polami projektowymi",
        issue_type="epic",
        typ_pracy="Prace programistyczne",
        dotyczy="FieldOps",
    )

    assert "BLAD" not in result
    fields = json.loads(captured["data"])["fields"]
    assert fields["customfield_10102"] == {"id": "90010"}
    assert fields["customfield_10100"] == {"id": "90003"}
    assert fields["customfield_10101"] == ["FieldOps"]


# ---------------------------------------------------------------------------
# Odczyt i komentarze bezposrednio do Jiry
# ---------------------------------------------------------------------------

def _json_resp(status: int, body) -> MagicMock:
    r = MagicMock(status_code=status, headers={})
    r.json.return_value = body
    r.text = str(body)
    return r


_RAW_ISSUE = {
    "id": "10001",
    "key": "PRJ1-1",
    "fields": {
        "summary": "Tytul",
        "status": {"name": "W toku", "statusCategory": {"name": "In Progress"}},
        "assignee": {"displayName": "Ewa Nowak", "emailAddress": "user04@example.com", "name": "a.nowak"},
        "issuetype": {"name": "Story"},
        "components": [{"id": 7, "name": "Checkout"}],
        "issuelinks": [{"type": {"name": "Blocks"}, "outwardIssue": {"key": "PRJ1-2",
                                                                       "fields": {"summary": "Inny"}}}],
        "subtasks": [{"key": "PRJ1-3", "fields": {"summary": "Pod", "status": {"name": "Do zrobienia"}}}],
        "customfield_10016": 5,
        "customfield_10100": {"value": "Rozwoj"},
        "comment": {"comments": [{"id": "1", "author": {"displayName": "X"}, "body": "hej"}]},
    },
    "changelog": {"histories": [{"id": "a", "items": []}, {"id": "b", "items": []}]},
}


def test_jira_issue_shapes_response_in_the_stable_format(jira_mcp):
    jira_mcp.requests.get = MagicMock(side_effect=[
        _json_resp(200, _RAW_ISSUE),
        _json_resp(200, {"worklogs": [{"id": "w", "timeSpentSeconds": 3600}]}),
    ])
    data = json.loads(jira_mcp.jira_issue("prj1-1"))
    f = data["fields"]
    assert data["key"] == "PRJ1-1"
    assert f["assignee"] == {"name": "Ewa Nowak", "email": "user04@example.com"}
    assert f["components"] == [{"id": "7", "name": "Checkout"}]
    assert f["issue_links"] == [{"type": "Blocks", "direction": "outward", "key": "PRJ1-2", "summary": "Inny"}]
    assert f["subtasks"][0]["status"] == "Do zrobienia"
    assert f["story_points"] == 5 and f["typ_pracy"] == "Rozwoj"
    assert data["worklogs"]["total_time_spent_s"] == 3600
    assert [h["id"] for h in data["changelog"]] == ["b", "a"]  # newest first
    assert data["test_steps"] is None  # not a Test issue -> no plugin probes


def test_jira_issue_rejects_bad_key_without_http(jira_mcp):
    jira_mcp.requests.get = MagicMock()
    assert jira_mcp.jira_issue("nie-klucz").startswith("BLAD:")
    jira_mcp.requests.get.assert_not_called()


def test_jira_issue_404_maps_to_not_found(jira_mcp):
    jira_mcp.requests.get = MagicMock(return_value=_json_resp(404, {"errorMessages": ["x"]}))
    assert jira_mcp.jira_issue("PRJ1-9") == "BLAD: not_found"


def test_jira_search_includes_issue_links_and_estimates(jira_mcp):
    raw = {"total": 1, "issues": [{"key": "PRJ1-1", "fields": {
        "summary": "S", "timetracking": {"remainingEstimate": "1d", "remainingEstimateSeconds": 28800},
        "issuelinks": [{"type": {"name": "Duplicate"}, "inwardIssue": {"key": "PRJ1-5", "fields": {}}}],
    }}]}
    jira_mcp.requests.get = MagicMock(return_value=_json_resp(200, raw))
    data = json.loads(jira_mcp.jira_search("project = PRJ1", max_results=500))
    issue = data["issues"][0]
    assert issue["remaining_estimate"] == "1d" and issue["remaining_estimate_seconds"] == 28800
    assert issue["issue_links"][0]["direction"] == "inward"
    assert jira_mcp.requests.get.call_args.kwargs["params"]["maxResults"] == 100  # clamped


def test_jira_search_invalid_jql_returns_jira_message(jira_mcp):
    jira_mcp.requests.get = MagicMock(return_value=_json_resp(400, {"errorMessages": ["Pole 'xx' nie istnieje"]}))
    assert "Pole 'xx' nie istnieje" in jira_mcp.jira_search("xx = 1")


def test_jira_comment_resolves_assignee_mention(jira_mcp, tmp_path, monkeypatch):
    monkeypatch.setattr(jira_mcp, "_COMMENTS_LOG_FILE", tmp_path / "log.jsonl")
    jira_mcp.requests.get = MagicMock(return_value=_json_resp(200, {"fields": {"assignee": {"name": "a.nowak"}}}))
    jira_mcp.requests.post = MagicMock(return_value=_json_resp(201, {"id": "77"}))
    data = json.loads(jira_mcp.jira_comment("PRJ1-1", "tresc", mention="assignee"))
    assert data["mention_resolved"] == "a.nowak"
    assert jira_mcp.requests.post.call_args.kwargs["json"] == {"body": "[~a.nowak]\n\ntresc"}
    assert data["jira_url"].endswith("focusedCommentId=77")
    assert (tmp_path / "log.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_jira_comment_rate_limited_from_disk_log(jira_mcp, tmp_path, monkeypatch):
    """The window is read from the audit log, so it holds across stdio processes."""
    log = tmp_path / "log.jsonl"
    now = jira_mcp._now_iso()
    log.write_text("".join(json.dumps({"ts": now}) + "\n" for _ in range(jira_mcp._COMMENT_RATE_MAX)))
    monkeypatch.setattr(jira_mcp, "_COMMENTS_LOG_FILE", log)
    jira_mcp.requests.post = MagicMock()
    assert jira_mcp.jira_comment("PRJ1-1", "x").startswith("BLAD: rate limit")
    jira_mcp.requests.post.assert_not_called()


def test_jira_comment_old_log_entries_do_not_count(jira_mcp, tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    log.write_text("".join(json.dumps({"ts": "2020-01-01T00:00:00+00:00"}) + "\n" for _ in range(50)))
    monkeypatch.setattr(jira_mcp, "_COMMENTS_LOG_FILE", log)
    jira_mcp.requests.post = MagicMock(return_value=_json_resp(201, {"id": "1"}))
    assert json.loads(jira_mcp.jira_comment("PRJ1-1", "x"))["status"] == "ok"


def test_jira_comment_rejects_markup_in_mention(jira_mcp, tmp_path, monkeypatch):
    monkeypatch.setattr(jira_mcp, "_COMMENTS_LOG_FILE", tmp_path / "log.jsonl")
    jira_mcp.requests.post = MagicMock()
    assert jira_mcp.jira_comment("PRJ1-1", "x", mention="a] [~b").startswith("BLAD: mention")
    jira_mcp.requests.post.assert_not_called()


def test_jira_comment_does_not_retry_post_on_5xx(jira_mcp, tmp_path, monkeypatch):
    """A 502 may come after Jira stored the comment -- re-posting would duplicate it."""
    monkeypatch.setattr(jira_mcp, "_COMMENTS_LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr(jira_mcp.time, "sleep", lambda s: None)
    jira_mcp.requests.post = MagicMock(return_value=_json_resp(502, {"errorMessages": ["proxy"]}))
    assert jira_mcp.jira_comment("PRJ1-1", "x").startswith("BLAD:")
    assert jira_mcp.requests.post.call_count == 1


def test_jira_attach_rejects_files_outside_bob_temp_attachments(jira_mcp, monkeypatch, tmp_path):
    """Prompt injection must not exfiltrate .env / session.json via jira_attach."""
    monkeypatch.setattr(jira_mcp.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    secret = tmp_path / ".env"
    secret.write_text("ATLASSIAN_TOKEN=x")
    disguised = tmp_path / "tmp" / "session.json"  # right dir, wrong prefix
    disguised.write_text("{}")
    jira_mcp.requests.post = MagicMock()
    assert jira_mcp.jira_attach("PRJ1-1", str(secret)).startswith("BLAD: mozna dolaczac tylko")
    assert jira_mcp.jira_attach("PRJ1-1", str(disguised)).startswith("BLAD: mozna dolaczac tylko")
    assert jira_mcp.jira_attach("not a key", str(secret)).startswith("BLAD: issue_key")
    jira_mcp.requests.post.assert_not_called()


def test_default_toolset_is_write_server(jira_mcp):
    """Imported without JIRA_MCP_TOOLSET the module is jira-bob (writes, stdio)."""
    assert jira_mcp._SERVER_NAME == "jira-bob"


def test_jira_boards_resolves_filter_with_partial_success(jira_mcp):
    def fake_get(url, **kwargs):
        if url.endswith("/rest/agile/1.0/board"):
            return _json_resp(200, {"values": [{"id": 1, "name": "A", "type": "scrum"},
                                               {"id": 2, "name": "B", "type": "kanban"}]})
        if url.endswith("/board/1/configuration"):
            return _json_resp(200, {"filter": {"id": 55}})
        if url.endswith("/filter/55"):
            return _json_resp(200, {"jql": "project = PRJ1"})
        return _json_resp(500, {})
    jira_mcp.requests.get = MagicMock(side_effect=fake_get)
    data = json.loads(jira_mcp.jira_boards(name="x"))
    by_id = {b["board_id"]: b for b in data["boards"]}
    assert by_id[1]["filter_jql"] == "project = PRJ1" and by_id[1]["filter_id"] == "55"
    assert by_id[2]["filter_jql"] is None
    assert data["count"] == 2



# ---------------------------------------------------------------------------
# Instance-specific write fields come from config/jira-fields.json
# ---------------------------------------------------------------------------

def test_update_ticket_skips_a_write_field_without_configured_id(jira_mcp, monkeypatch):
    mock_put, captured = _make_put_mock()
    jira_mcp.requests.put = mock_put
    monkeypatch.delitem(jira_mcp._WRITE_FIELDS, "numer_projektu")

    result = jira_mcp.jira_update_ticket(issue_key="PRJ1-1", numer_projektu="PRJ-1", dotyczy="FieldOps")

    assert result == "OK"
    assert json.loads(captured["data"])["fields"] == {"customfield_10101": ["FieldOps"]}


def test_update_ticket_typ_pracy_accepts_label_or_option_id(jira_mcp):
    mock_put, captured = _make_put_mock()
    jira_mcp.requests.put = mock_put

    jira_mcp.jira_update_ticket(issue_key="PRJ1-1", typ_pracy="prace PROGRAMISTYCZNE")
    assert json.loads(captured["data"])["fields"]["customfield_10100"] == {"id": "90003"}
    jira_mcp.jira_update_ticket(issue_key="PRJ1-1", typ_pracy="90004")
    assert json.loads(captured["data"])["fields"]["customfield_10100"] == {"id": "90004"}


def test_create_in_project_without_work_type_sends_no_work_type_fields(jira_mcp):
    mock_post, captured = _make_post_mock(201, {"key": "PRJ5-1"})
    jira_mcp.requests.post = mock_post

    jira_mcp.jira_create(project="PRJ5", summary="x", issue_type="task", typ_pracy="Testy", dotyczy="FieldOps")

    fields = json.loads(captured["data"])["fields"]
    assert not {"customfield_10100", "customfield_10101", "customfield_10102"} & set(fields)
