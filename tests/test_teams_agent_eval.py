"""eval/run_eval.py: the sandboxed command must not leak the daemon token into argv."""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "scripts", _ROOT / "scripts" / "teams_agent", _ROOT / "scripts" / "teams_agent" / "eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ai  # noqa: E402
import run_eval  # noqa: E402
from mcp_admin import auth  # noqa: E402


def test_sandboxed_points_jira_bob_at_sandbox_via_private_file(monkeypatch, tmp_path):
    cfg = tmp_path / "mcp-config.json"
    cfg.write_text(json.dumps({"mcpServers": {
        name: {"type": "http", "url": f"http://127.0.0.1:1/{name}", "headers": auth.auth_headers()}
        for name in ai._MCP_SERVERS
    }}))
    monkeypatch.setattr(ai, "_MCP_CONFIG", cfg)

    cmd = run_eval._sandboxed(ai.build_reply_cmd("claude-sonnet-5-5", "sys", ["jira_write"]))

    path = Path(cmd[cmd.index("--mcp-config") + 1])
    assert path.exists() and path.parent == auth.rundir()
    assert path.stat().st_mode & 0o777 == 0o600
    servers = json.loads(path.read_text())["mcpServers"]
    assert servers["jira-bob"]["args"] == [str(run_eval._SANDBOX)]
    assert "Bearer" not in " ".join(cmd) and auth.daemon_token() not in " ".join(cmd)
