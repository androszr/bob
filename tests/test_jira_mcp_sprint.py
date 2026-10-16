"""Unit tests for jira_sprint_search() and jira_assign_sprint().

Strategy: load scripts/jira/mcp_server.py under a unique module alias with the
same stub approach used in test_jira_mcp_server.py.  All HTTP calls are mocked
via jira_mcp.requests.get / requests.post — no live Atlassian credentials needed.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_JIRA_MCP_PATH = _REPO_ROOT / "scripts" / "jira" / "mcp_server.py"


# ---------------------------------------------------------------------------
# Stubs (replicates test_jira_mcp_server._make_jira_stubs)
# ---------------------------------------------------------------------------

def _make_stubs() -> dict[str, ModuleType]:
    logts_stub = ModuleType("logts")

    fastmcp_mod = ModuleType("mcp")
    fastmcp_server = ModuleType("mcp.server")
    fastmcp_fastmcp = ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        def __init__(self, name: str) -> None:
            self.name = name

        def tool(self):
            def _decorator(fn):
                return fn
            return _decorator

        def run(self):
            pass

    fastmcp_fastmcp.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]

    requests_stub = ModuleType("requests")
    requests_stub.RequestException = Exception  # type: ignore[attr-defined]
    requests_stub.Response = object  # type: ignore[attr-defined]  # adnotacja w _request_with_retry

    jira_client_stub = ModuleType("jira_client")
    jira_client_stub.JIRA_URL = "https://fake.jira"  # type: ignore[attr-defined]
    jira_client_stub._HEADERS = {"Authorization": "Bearer fake"}  # type: ignore[attr-defined]
    jira_client_stub.VERIFY_SSL = False  # type: ignore[attr-defined]
    jira_client_stub.find_issue = MagicMock(return_value=[])  # type: ignore[attr-defined]

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
    mod_name = "jira_mcp_sprint_under_test"
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
def jira_mcp():
    stubs = _make_stubs()
    return _load_jira_mcp(stubs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_get_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _make_boards_response(boards: list[dict]) -> MagicMock:
    return _make_get_response(200, {"values": boards})


def _make_sprints_response(sprints: list[dict]) -> MagicMock:
    return _make_get_response(200, {"values": sprints})


# ---------------------------------------------------------------------------
# A: jira_sprint_search — found matching sprints
# ---------------------------------------------------------------------------

def test_sprint_search_finds_matching_sprint(jira_mcp):
    """jira_sprint_search must return sprint matching query substring."""
    boards = [{"id": 1192, "name": "FieldOps Board"}]
    sprints = [
        {"id": 9002, "name": "FieldOps Sprint 13", "state": "future"},
        {"id": 9001, "name": "FieldOps Sprint 12", "state": "active"},
    ]

    call_count = 0

    def mock_get(url, headers=None, params=None, timeout=None, verify=None):
        nonlocal call_count
        call_count += 1
        if "board" in url and "sprint" not in url:
            assert params.get("projectKeyOrId") == "PRJ2"
            return _make_boards_response(boards)
        if "sprint" in url:
            assert params.get("state") == "active,future"
            return _make_sprints_response(sprints)
        return _make_get_response(404, {})

    jira_mcp.requests.get = mock_get

    result_str = jira_mcp.jira_sprint_search("Sprint 13", "PRJ2")
    result = json.loads(result_str)

    assert result["count"] == 1
    assert result["query"] == "Sprint 13"
    assert result["sprints"][0]["id"] == 9002
    assert result["sprints"][0]["name"] == "FieldOps Sprint 13"
    assert result["sprints"][0]["state"] == "future"
    assert result["sprints"][0]["board_id"] == 1192


# ---------------------------------------------------------------------------
# B: jira_sprint_search — no matching sprint (count=0)
# ---------------------------------------------------------------------------

def test_sprint_search_no_match_returns_empty(jira_mcp):
    """jira_sprint_search returns count=0 when no sprint name matches query."""
    boards = [{"id": 1192, "name": "FieldOps Board"}]
    sprints = [
        {"id": 9002, "name": "FieldOps Sprint 13", "state": "future"},
    ]

    def mock_get(url, headers=None, params=None, timeout=None, verify=None):
        if "sprint" in url:
            return _make_sprints_response(sprints)
        return _make_boards_response(boards)

    jira_mcp.requests.get = mock_get

    result = json.loads(jira_mcp.jira_sprint_search("Nieistniejacy Sprint 99", "PRJ2"))

    assert result["count"] == 0
    assert result["sprints"] == []


# ---------------------------------------------------------------------------
# C: jira_sprint_search — board discovery HTTP error
# ---------------------------------------------------------------------------

def test_sprint_search_board_error_returns_blad(jira_mcp):
    """jira_sprint_search returns BLAD: when board discovery fails."""
    resp = MagicMock()
    resp.status_code = 403
    resp.json.return_value = {"errorMessages": ["Forbidden"]}
    resp.text = "Forbidden"
    jira_mcp.requests.get = lambda *a, **kw: resp

    result = jira_mcp.jira_sprint_search("Sprint 13", "PRJ2")
    assert result.startswith("BLAD:")


# ---------------------------------------------------------------------------
# D: jira_sprint_search — no boards for project returns empty
# ---------------------------------------------------------------------------

def test_sprint_search_no_boards_returns_empty(jira_mcp):
    """jira_sprint_search returns count=0 when project has no boards."""
    jira_mcp.requests.get = lambda *a, **kw: _make_boards_response([])

    result = json.loads(jira_mcp.jira_sprint_search("Sprint 13", "UNKNOWNPROJ"))
    assert result["count"] == 0
    assert result["sprints"] == []


# ---------------------------------------------------------------------------
# E: jira_sprint_search — state=active,future passed to sprint endpoint
# ---------------------------------------------------------------------------

def test_sprint_search_passes_correct_state_param(jira_mcp):
    """jira_sprint_search must pass state=active,future to the sprint endpoint."""
    boards = [{"id": 42, "name": "Board"}]
    captured_params: list[dict] = []

    def mock_get(url, headers=None, params=None, timeout=None, verify=None):
        if "sprint" in url:
            captured_params.append(dict(params or {}))
            return _make_sprints_response([])
        return _make_boards_response(boards)

    jira_mcp.requests.get = mock_get
    jira_mcp.jira_sprint_search("x", "PRJ1")

    assert len(captured_params) == 1
    assert captured_params[0]["state"] == "active,future"


# ---------------------------------------------------------------------------
# F: jira_sprint_search — deduplication across boards
# ---------------------------------------------------------------------------

def test_sprint_search_deduplicates_same_sprint_id(jira_mcp):
    """Same sprint returned by multiple boards must appear only once."""
    boards = [
        {"id": 10, "name": "Board A"},
        {"id": 11, "name": "Board B"},
    ]
    same_sprint = {"id": 999, "name": "Shared Sprint 1", "state": "active"}

    def mock_get(url, headers=None, params=None, timeout=None, verify=None):
        if "sprint" in url:
            return _make_sprints_response([same_sprint])
        return _make_boards_response(boards)

    jira_mcp.requests.get = mock_get

    result = json.loads(jira_mcp.jira_sprint_search("Shared Sprint 1", "PROJ"))
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# G: jira_assign_sprint — success (204)
# ---------------------------------------------------------------------------

def test_assign_sprint_success(jira_mcp):
    """jira_assign_sprint returns 'OK' when Agile API responds 204."""
    captured: dict = {}

    def mock_post(url, data=None, headers=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["body"] = json.loads(data)
        resp = MagicMock()
        resp.status_code = 204
        return resp

    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_assign_sprint("PRJ2-123", 9002)

    assert result == "OK"
    assert "sprint/9002/issue" in captured["url"]
    assert captured["body"] == {"issues": ["PRJ2-123"]}


# ---------------------------------------------------------------------------
# H: jira_assign_sprint — HTTP error returns BLAD
# ---------------------------------------------------------------------------

def test_assign_sprint_http_error_returns_blad(jira_mcp):
    """jira_assign_sprint returns BLAD: on non-2xx response."""
    def mock_post(url, data=None, headers=None, timeout=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"errorMessages": ["Issue not found"], "errors": {}}
        resp.text = "Issue not found"
        return resp

    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_assign_sprint("PRJ2-INVALID", 9002)
    assert result.startswith("BLAD:")
    assert "Issue not found" in result


# ---------------------------------------------------------------------------
# I: jira_assign_sprint — connection error returns BLAD
# ---------------------------------------------------------------------------

def test_assign_sprint_connection_error_returns_blad(jira_mcp):
    """jira_assign_sprint returns BLAD: when requests raises RequestException."""
    def mock_post(url, data=None, headers=None, timeout=None, **kwargs):
        raise jira_mcp.requests.RequestException("connection refused")

    jira_mcp.requests.post = mock_post

    result = jira_mcp.jira_assign_sprint("PRJ2-123", 9002)
    assert result.startswith("BLAD:")
