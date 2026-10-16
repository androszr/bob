"""Transport selection helper for Bob's MCP servers.

Each scripts/<dir>/mcp_server.py ends with:

    if __name__ == "__main__":
        run_mcp(mcp, default_port=MCP_PORTS["<name>-bob"])

Mode resolution:
  1. CLI flag --stdio forces stdio regardless of env var (highest priority --
     needed because BOB_MCP_TRANSPORT=http is exported repo-wide in ~/.zshrc,
     which every spawned subprocess inherits; a server that MUST run stdio
     per-call -- e.g. one gating on BOB_SENDER_MRI, which a persistent HTTP
     daemon never sees -- needs a way to opt out of that ambient default).
  2. CLI flag --http (with optional --port, --host) wins over the env var.
  3. Env var BOB_MCP_TRANSPORT=http triggers streamable-http on default_port.
  4. Otherwise: stdio (default — backwards compatible).
"""

import argparse
import os
import sys
from typing import Any


def run_mcp(mcp: Any, default_port: int, default_host: str = "127.0.0.1") -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--http", action="store_true",
                        help="Run on streamable-http instead of stdio.")
    parser.add_argument("--stdio", action="store_true",
                        help="Force stdio, overriding BOB_MCP_TRANSPORT=http.")
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--host", default=default_host)
    # parse_known_args so future flags from the framework don't crash us.
    args, _unknown = parser.parse_known_args()

    env_http = os.environ.get("BOB_MCP_TRANSPORT", "").strip().lower() == "http"
    use_http = not args.stdio and (args.http or env_http)

    if use_http:
        from mcp_admin.auth import BearerAuth, daemon_token  # noqa: PLC0415

        # FastMCP.run() does not take host/port — they live on .settings.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # run(transport="streamable-http") builds its app via self.streamable_http_app();
        # shadow it on the instance so every request must carry the daemon token (auth.py).
        build_app = mcp.streamable_http_app
        token = daemon_token()
        mcp.streamable_http_app = lambda: BearerAuth(build_app(), token)
        print(f"[mcp-admin] starting on streamable-http http://{args.host}:{args.port}/mcp",
              file=sys.stderr, flush=True)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
