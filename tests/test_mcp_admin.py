"""Tests for scripts/mcp_admin/ — ports, transport selection, http config gen."""

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "teams_agent"))

from mcp_admin import daemon, ports, transport  # noqa: E402

# ---------------------------------------------------------------------------
# ports.py
# ---------------------------------------------------------------------------


def test_ports_cover_all_mcp_servers():
    """ports.MCP_PORTS must contain every server in ai._MCP_SERVERS."""
    import ai  # noqa: PLC0415
    for name in ai._MCP_SERVERS:
        assert name in ports.MCP_PORTS, (
            f"Server '{name}' missing from ports.MCP_PORTS. "
            "Add it with a free port in 7101-7120."
        )


def test_ports_are_unique_and_in_range():
    vals = list(ports.MCP_PORTS.values())
    assert len(vals) == len(set(vals)), f"Duplicate ports: {vals}"
    for name, p in ports.MCP_PORTS.items():
        assert 7100 < p < 7200, f"{name} port {p} outside reserved 7101-7199 range"


def test_http_url_format():
    assert ports.http_url("jira-bob") == "http://127.0.0.1:7101/mcp"
    assert ports.http_url("urlopy-bob") == "http://127.0.0.1:7112/mcp"


# ---------------------------------------------------------------------------
# transport.py — run_mcp dispatcher
# ---------------------------------------------------------------------------


def _make_fake_mcp() -> MagicMock:
    m = MagicMock()
    m.settings = MagicMock()
    return m


def test_run_mcp_defaults_to_stdio(monkeypatch):
    monkeypatch.delenv("BOB_MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(sys, "argv", ["server.py"])
    fake = _make_fake_mcp()
    transport.run_mcp(fake, default_port=7101)
    fake.run.assert_called_once_with(transport="stdio")


def test_run_mcp_env_triggers_http(monkeypatch):
    monkeypatch.setenv("BOB_MCP_TRANSPORT", "http")
    monkeypatch.setattr(sys, "argv", ["server.py"])
    fake = _make_fake_mcp()
    transport.run_mcp(fake, default_port=7101)
    fake.run.assert_called_once_with(transport="streamable-http")
    assert fake.settings.port == 7101
    assert fake.settings.host == "127.0.0.1"


def test_run_mcp_flag_overrides_env(monkeypatch):
    monkeypatch.delenv("BOB_MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(sys, "argv", ["server.py", "--http", "--port", "7199"])
    fake = _make_fake_mcp()
    transport.run_mcp(fake, default_port=7101)
    fake.run.assert_called_once_with(transport="streamable-http")
    assert fake.settings.port == 7199


def test_run_mcp_stdio_flag_overrides_env(monkeypatch):
    # ~/.zshrc exports BOB_MCP_TRANSPORT=http repo-wide -- a server that MUST
    # run stdio per-call (e.g. one gating on BOB_SENDER_MRI) needs --stdio to
    # win over that ambient env var.
    monkeypatch.setenv("BOB_MCP_TRANSPORT", "http")
    monkeypatch.setattr(sys, "argv", ["server.py", "--stdio"])
    fake = _make_fake_mcp()
    transport.run_mcp(fake, default_port=7101)
    fake.run.assert_called_once_with(transport="stdio")


def test_run_mcp_case_insensitive_env(monkeypatch):
    monkeypatch.setenv("BOB_MCP_TRANSPORT", "HTTP")
    monkeypatch.setattr(sys, "argv", ["server.py"])
    fake = _make_fake_mcp()
    transport.run_mcp(fake, default_port=7102)
    fake.run.assert_called_once_with(transport="streamable-http")


def test_run_mcp_ignores_unknown_env_value(monkeypatch):
    monkeypatch.setenv("BOB_MCP_TRANSPORT", "websocket")
    monkeypatch.setattr(sys, "argv", ["server.py"])
    fake = _make_fake_mcp()
    transport.run_mcp(fake, default_port=7101)
    fake.run.assert_called_once_with(transport="stdio")


# ---------------------------------------------------------------------------
# ai._refresh_mcp_config — dual mode
# ---------------------------------------------------------------------------


def test_refresh_mcp_config_stdio_default(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "owned_and_listening", lambda name, script: False)
    monkeypatch.delenv("BOB_MCP_TRANSPORT", raising=False)
    import ai  # noqa: PLC0415

    fake_cfg = tmp_path / "mcp-config.json"
    with patch.object(ai, "_MCP_CONFIG", fake_cfg):
        ai._refresh_mcp_config()

    payload = json.loads(fake_cfg.read_text())
    assert set(payload["mcpServers"]) == set(ai._MCP_SERVERS)
    for entry in payload["mcpServers"].values():
        assert "command" in entry and "args" in entry
        assert "type" not in entry


def test_refresh_mcp_config_http_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("BOB_MCP_TRANSPORT", "http")
    import ai  # noqa: PLC0415
    from mcp_admin import auth  # noqa: PLC0415

    fake_cfg = tmp_path / "mcp-config.json"
    with patch.object(ai, "_MCP_CONFIG", fake_cfg):
        ai._refresh_mcp_config()

    assert fake_cfg.stat().st_mode & 0o777 == 0o600
    payload = json.loads(fake_cfg.read_text())
    assert set(payload["mcpServers"]) == set(ai._MCP_SERVERS)
    for name, entry in payload["mcpServers"].items():
        if name in ai._STDIO_FORCED_SERVERS:
            # Gates on BOB_SENDER_MRI, which never reaches a persistent HTTP
            # daemon -- forced stdio (with an explicit --stdio override, see
            # scripts/mcp_admin/transport.py) even when BOB_MCP_TRANSPORT=http.
            assert entry["command"]
            assert entry["args"][-1] == "--stdio"
            assert "type" not in entry
            continue
        assert entry["type"] == "http"
        assert entry["url"] == ports.http_url(name)
        assert entry["headers"] == auth.auth_headers()
        assert "command" not in entry


def test_refresh_mcp_config_treats_blank_as_stdio(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "owned_and_listening", lambda name, script: False)
    monkeypatch.setenv("BOB_MCP_TRANSPORT", "")
    import ai  # noqa: PLC0415

    fake_cfg = tmp_path / "mcp-config.json"
    with patch.object(ai, "_MCP_CONFIG", fake_cfg):
        ai._refresh_mcp_config()

    payload = json.loads(fake_cfg.read_text())
    for entry in payload["mcpServers"].values():
        assert "command" in entry


def test_refresh_mcp_config_uses_listening_daemon_in_stdio_mode(monkeypatch, tmp_path):
    """jira-read-bob (ai._DAEMON_SERVERS) goes over HTTP while our daemon listens; others stay stdio."""
    monkeypatch.delenv("BOB_MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(daemon, "owned_and_listening", lambda name, script: name == "jira-read-bob")
    import ai  # noqa: PLC0415

    fake_cfg = tmp_path / "mcp-config.json"
    with patch.object(ai, "_MCP_CONFIG", fake_cfg):
        ai._refresh_mcp_config()

    servers = json.loads(fake_cfg.read_text())["mcpServers"]
    from mcp_admin import auth  # noqa: PLC0415

    assert servers["jira-read-bob"] == {"type": "http", "url": "http://127.0.0.1:7126/mcp",
                                        "headers": auth.auth_headers()}
    assert fake_cfg.stat().st_mode & 0o777 == 0o600  # holds the daemon token
    assert all("command" in e for n, e in servers.items() if n != "jira-read-bob")


def test_daemon_servers_never_gate_on_sender_env():
    """A daemon's env is frozen at start -- servers reading BOB_SENDER_* must stay stdio."""
    import ai  # noqa: PLC0415

    assert ai._DAEMON_SERVERS.isdisjoint(ai._STDIO_FORCED_SERVERS)
    for name in ai._DAEMON_SERVERS:
        script = _REPO_ROOT / ai._MCP_SERVERS[name]
        sources = [script.read_text(encoding="utf-8")]
        if name == "jira-read-bob":  # thin wrapper around the shared jira module
            sources.append((script.parent / "mcp_server.py").read_text(encoding="utf-8"))
        # code reads only (os.environ[...] / .get / getenv) -- comments may mention the var
        reads = re.compile(r"(environ(\.get)?\(?\[?|getenv\()\s*[\"']BOB_SENDER")
        assert not any(reads.search(src) for src in sources), name


def test_jira_write_tools_only_in_stdio_server():
    """Writes go through jira-bob (stdio); the daemon group exposes read tools only."""
    import ai  # noqa: PLC0415

    assert "jira-bob" in ai._STDIO_FORCED_SERVERS
    assert all(t.startswith("mcp__jira-read-bob__") for t in ai._MCP_GROUPS["jira"].split(","))
    assert all(t.startswith("mcp__jira-bob__") for t in ai._MCP_GROUPS["jira_write"].split(","))


def _fake_daemon_env(monkeypatch, tmp_path, ps_output: str):
    monkeypatch.setattr(daemon, "_RUNDIR", tmp_path)
    (tmp_path / "jira-read-bob.pid").write_text("4242")
    monkeypatch.setattr(daemon.subprocess, "run", lambda *a, **k: MagicMock(stdout=ps_output))


def test_stop_never_kills_foreign_process_with_reused_pid(monkeypatch, tmp_path):
    _fake_daemon_env(monkeypatch, tmp_path, "/usr/bin/some-other-tool")
    kills = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: kills.append(pid))
    daemon._stop("jira-read-bob", "scripts/jira/mcp_read_server.py")
    assert kills == []
    assert not (tmp_path / "jira-read-bob.pid").exists()


def test_stop_kills_our_daemon(monkeypatch, tmp_path):
    _fake_daemon_env(monkeypatch, tmp_path, "python3 /repo/scripts/jira/mcp_read_server.py --http")
    kills = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: kills.append(pid))
    monkeypatch.setattr(daemon, "is_listening", lambda name, timeout=0.3: False)
    daemon._stop("jira-read-bob", "scripts/jira/mcp_read_server.py")
    assert kills == [4242]


def test_owned_and_listening_rejects_foreign_listener(monkeypatch, tmp_path):
    _fake_daemon_env(monkeypatch, tmp_path, "/usr/bin/some-other-tool")
    monkeypatch.setattr(daemon, "is_listening", lambda name, timeout=0.3: True)
    assert daemon.owned_and_listening("jira-read-bob", "scripts/jira/mcp_read_server.py") is False


def test_restart_daemon_does_not_spawn_when_port_taken_by_foreign_process(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "_RUNDIR", tmp_path)
    monkeypatch.setattr(daemon, "is_listening", lambda name, timeout=0.3: True)
    popen = MagicMock()
    monkeypatch.setattr(daemon.subprocess, "Popen", popen)
    assert daemon.restart_daemon("jira-read-bob", "scripts/jira/mcp_read_server.py") is False
    popen.assert_not_called()


def test_restart_daemon_reports_early_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "_RUNDIR", tmp_path)
    monkeypatch.setattr(daemon, "_LOGDIR", tmp_path)
    monkeypatch.setattr(daemon, "is_listening", lambda name, timeout=0.3: False)
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    proc = MagicMock(pid=777)
    proc.poll.return_value = 1
    monkeypatch.setattr(daemon.subprocess, "Popen", MagicMock(return_value=proc))
    assert daemon.restart_daemon("jira-read-bob", "scripts/jira/mcp_read_server.py") is False
    assert (tmp_path / "jira-read-bob.pid").read_text() == "777"



# ---------------------------------------------------------------------------
# auth.py — bearer token for HTTP daemons
# ---------------------------------------------------------------------------


def test_daemon_token_is_private_and_stable():
    from mcp_admin import auth  # noqa: PLC0415

    token = auth.daemon_token()
    assert len(token) >= 32 and auth.daemon_token() == token
    path = auth.rundir() / "daemon-token"
    assert path.stat().st_mode & 0o777 == 0o600
    assert auth.rundir().stat().st_mode & 0o777 == 0o700


def test_write_private_config_is_0600_and_content_addressed():
    from mcp_admin import auth  # noqa: PLC0415

    a = auth.write_private_config('{"x": 1}')
    assert a == auth.write_private_config('{"x": 1}')
    assert a != auth.write_private_config('{"x": 2}')
    assert a.read_text() == '{"x": 1}' and a.stat().st_mode & 0o777 == 0o600


def _asgi_call(app, headers):
    import asyncio  # noqa: PLC0415

    sent = []

    async def _send(msg):
        sent.append(msg)

    async def _receive():
        return {"type": "http.request", "body": b""}

    asyncio.run(app({"type": "http", "headers": headers}, _receive, _send))
    return sent


_TOKEN = "s3cret" * 8


def test_bearer_auth_rejects_missing_or_wrong_token():
    from mcp_admin import auth  # noqa: PLC0415

    inner = MagicMock()

    async def _inner(scope, receive, send):
        inner(scope)

    app = auth.BearerAuth(_inner, _TOKEN)
    for headers in ([], [(b"authorization", b"Bearer nope")]):
        sent = _asgi_call(app, headers)
        assert sent[0]["status"] == 401
    inner.assert_not_called()
    assert _asgi_call(app, [(b"authorization", f"Bearer {_TOKEN}".encode())]) == []
    inner.assert_called_once()


@pytest.mark.parametrize("token", ["", "short", "x" * 31])
def test_bearer_auth_rejects_empty_or_short_token(token):
    from mcp_admin import auth  # noqa: PLC0415

    with pytest.raises(ValueError):
        auth.BearerAuth(MagicMock(), token)


def test_bearer_auth_websocket_needs_token_and_lifespan_passes():
    import asyncio  # noqa: PLC0415

    from mcp_admin import auth  # noqa: PLC0415

    seen = []

    async def _inner(scope, receive, send):
        seen.append(scope["type"])

    app = auth.BearerAuth(_inner, _TOKEN)
    sent = []

    async def _send(msg):
        sent.append(msg)

    asyncio.run(app({"type": "websocket", "headers": []}, None, _send))
    assert sent == [{"type": "websocket.close", "code": 1008}] and seen == []
    good = [(b"authorization", f"Bearer {_TOKEN}".encode())]
    asyncio.run(app({"type": "websocket", "headers": good}, None, _send))
    asyncio.run(app({"type": "lifespan"}, None, _send))  # no auth: startup must not 401
    assert seen == ["websocket", "lifespan"]


def test_daemon_token_rejects_truncated_file():
    from mcp_admin import auth  # noqa: PLC0415

    (auth.rundir() / "daemon-token").write_text("abc")
    with pytest.raises(RuntimeError):
        auth.daemon_token()


def test_private_files_survive_concurrent_writers():
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    from mcp_admin import auth  # noqa: PLC0415

    content = json.dumps({"mcpServers": {"x": {"url": "u"}}})

    def _work(_):
        path = auth.write_private_config(content)
        return path.read_text(), auth.daemon_token()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_work, range(240)))
    assert {text for text, _ in results} == {content}
    tokens = {tok for _, tok in results}
    assert len(tokens) == 1 and len(next(iter(tokens))) >= 32
    for f in (auth.rundir() / "daemon-token", auth.write_private_config(content)):
        assert f.stat().st_mode & 0o777 == 0o600
    assert not list(auth.rundir().glob(".*.tmp"))  # temp files always cleaned up


def test_run_mcp_http_serves_behind_bearer_auth(monkeypatch):
    """Real FastMCP app through run_mcp: lifespan starts, 401 without token, not 401 with."""
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415
    from mcp_admin import auth  # noqa: PLC0415
    from starlette.testclient import TestClient  # noqa: PLC0415

    monkeypatch.setattr(sys, "argv", ["server.py", "--http"])
    mcp = FastMCP("t")
    captured = {}
    # run() would bind a port; capture the app run_mcp would have served instead.
    monkeypatch.setattr(mcp, "run", lambda transport: captured.update(app=mcp.streamable_http_app()))
    transport.run_mcp(mcp, default_port=7126)

    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"}}}
    accept = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(captured["app"], base_url="http://127.0.0.1:7126") as client:  # runs lifespan
        assert client.post("/mcp", json=body, headers=accept).status_code == 401
        ok = client.post("/mcp", json=body, headers={**accept, **auth.auth_headers()})
        assert ok.status_code != 401
