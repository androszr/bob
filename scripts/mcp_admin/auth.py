"""Shared bearer token for Bob's HTTP MCP daemons.

The daemons listen on 127.0.0.1 only, and FastMCP's DNS-rebinding protection
(Host/Origin allowlist, on by default for a localhost bind) already keeps
browsers out. Without a token, though, any other local process could still query
Jira and the other team data through them. Every HTTP request must
now carry `Authorization: Bearer <token>`:

  * the token lives in <rundir>/daemon-token (0600, rundir 0700), created on
    first use by whichever side -- daemon or ai.py -- gets there first,
  * transport.run_mcp wraps the daemon's ASGI app in BearerAuth,
  * ai._refresh_mcp_config puts auth_headers() into every HTTP entry; per-call
    configs go to 0600 files (write_private_config), not argv, so `ps` never
    shows the token.

Rotating: delete daemon-token and every mcp-config-*.json (they embed the old
token) in the rundir, then restart the watch loop (daemons + config).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import tempfile
from pathlib import Path


def rundir() -> Path:
    """~/.bob/mcp (BOB_MCP_RUNDIR overrides) -- same dir as daemon.py pidfiles."""
    d = Path(os.environ.get("BOB_MCP_RUNDIR", Path.home() / ".bob" / "mcp"))
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _write_new_private(path: Path, content: str) -> None:
    """Create `path` (0600) with full content atomically; no-op if it already exists."""
    # mkstemp: unique name per call (threads share a pid) and already 0600
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            os.link(tmp, path)  # atomic and fails if path exists -- readers never see a partial file
        except FileExistsError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def daemon_token() -> str:
    path = rundir() / "daemon-token"
    if not path.exists():
        _write_new_private(path, secrets.token_urlsafe(32))
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:  # truncated/empty file must never become the accepted credential
        raise RuntimeError(f"{path} holds a truncated token; delete it and retry")
    return token


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {daemon_token()}"}


def write_private_config(content: str) -> Path:
    """Content-addressed 0600 file under rundir -- for --mcp-config values that hold the token."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    path = rundir() / f"mcp-config-{digest}.json"
    if not path.exists():
        _write_new_private(path, content)
    return path


class BearerAuth:
    """ASGI middleware: 401 (websocket: close 1008) without the daemon token; lifespan passes."""

    def __init__(self, app, token: str) -> None:
        if len(token) < 32:
            raise ValueError("BearerAuth needs a token of at least 32 chars")
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(got, self._expected):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)
