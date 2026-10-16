"""Single MCP server as a background HTTP daemon, owned by the watch loop.

Unlike start_all.sh / launchd (all servers, BOB_MCP_TRANSPORT=http), this runs
selected servers (ai._DAEMON_SERVERS) as daemons while everything else stays
stdio. cli.py calls restart_daemon() at watch start so the daemon always runs the
current code, and ai._refresh_mcp_config() points claude CLI at it only while the
port actually listens -- otherwise that server falls back to stdio.

Pid/log locations match the mcp_admin shell scripts (~/.bob/mcp, ~/Library/Logs/bob),
so status.sh / stop_all.sh see the same process.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_admin.ports import MCP_PORTS  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNDIR = Path(os.environ.get("BOB_MCP_RUNDIR", Path.home() / ".bob" / "mcp"))
_LOGDIR = Path(os.environ.get("BOB_MCP_LOGDIR", Path.home() / "Library" / "Logs" / "bob"))
_HOST = "127.0.0.1"


def is_listening(name: str, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((_HOST, MCP_PORTS[name]), timeout=timeout):
            return True
    except OSError:
        return False


def _pidfile(name: str) -> Path:
    return _RUNDIR / f"{name}.pid"


def _our_pid(name: str, script: str) -> int | None:
    """Pid from our pidfile, but only if that process is still running `script`.

    The cmdline check guards against pid reuse -- never treat a foreign process as ours.
    """
    try:
        pid = int(_pidfile(name).read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        # fixed argv (ps + numeric pid), no user input
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],  # noqa: S607
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return pid if script in cmd else None


def owned_and_listening(name: str, script: str) -> bool:
    """True only when the port listens AND our own daemon process is alive.

    A port held by some other local process is not trusted (it would receive Jira
    queries) -- callers fall back to stdio instead.
    """
    return is_listening(name) and _our_pid(name, script) is not None


def _stop(name: str, script: str) -> None:
    """Stop our previous instance (pidfile + matching cmdline only -- never a foreign process)."""
    pid = _our_pid(name, script)
    pidfile = _pidfile(name)
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(50):
            if not is_listening(name):
                break
            time.sleep(0.1)
    pidfile.unlink(missing_ok=True)


def restart_daemon(name: str, script: str, wait_s: float = 15.0) -> bool:
    """(Re)start `script` as an HTTP daemon on MCP_PORTS[name]. True once our daemon listens.

    If the port is held by a process we did not start, nothing is spawned and False is
    returned -- the MCP config then keeps that server on stdio (see owned_and_listening).
    """
    _stop(name, script)
    if is_listening(name):
        return False
    _RUNDIR.mkdir(parents=True, exist_ok=True)
    _LOGDIR.mkdir(parents=True, exist_ok=True)
    with (_LOGDIR / f"mcp-{name}.log").open("ab") as log:  # child keeps its own fd
        # fixed argv (our interpreter + repo script), no user input
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, str(_REPO_ROOT / script), "--http", "--port", str(MCP_PORTS[name])],
            cwd=_REPO_ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
        )
    tmp = _pidfile(name).with_suffix(".pid.tmp")
    tmp.write_text(str(proc.pid))
    os.replace(tmp, _pidfile(name))
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_listening(name):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.2)
    return False
