"""Block until all MCP servers respond on their HTTP ports, or timeout.

Usage:
    python3 scripts/mcp_admin/healthcheck.py [--timeout 30] [--quiet]

Exit codes:
    0 — all 9 healthy within timeout
    1 — at least one unreachable after timeout

Intended to be called from cli.py at watch-loop startup when
BOB_MCP_TRANSPORT=http, so the watch fails fast with a clear error rather
than silently spawning subprocess fallbacks per message.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_admin.ports import MCP_PORTS  # noqa: E402


def _probe(host: str, port: int, timeout: float = 0.5) -> bool:
    """True if TCP connect succeeds. Fast: doesn't speak MCP, just checks listen socket."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Max seconds to wait for all servers to come up (default 30).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    remaining = dict(MCP_PORTS)
    last_print = 0.0

    while remaining and time.monotonic() < deadline:
        for name, port in list(remaining.items()):
            if _probe(args.host, port):
                if not args.quiet:
                    print(f"OK   {name} port={port}", flush=True)
                remaining.pop(name)
        if remaining:
            now = time.monotonic()
            if not args.quiet and now - last_print > 2.0:
                names = ", ".join(remaining)
                print(f"waiting: {names}", flush=True)
                last_print = now
            time.sleep(0.5)

    if remaining:
        print("\nHEALTHCHECK FAILED — these servers did not come up:", file=sys.stderr)
        for name, port in remaining.items():
            print(f"  {name} (expected on {args.host}:{port})", file=sys.stderr)
        print("\nFix:", file=sys.stderr)
        print("  scripts/mcp_admin/install_launchd.sh   (persistent)", file=sys.stderr)
        print("  scripts/mcp_admin/start_all.sh         (ad-hoc)", file=sys.stderr)
        print("Or unset BOB_MCP_TRANSPORT to fall back to stdio.", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"\nAll {len(MCP_PORTS)} MCP servers healthy.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
