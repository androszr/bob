"""Start / stop / restart the Bob process (scripts/teams_agent/cli.py).

Bob is started the way the owner starts it in the terminal - `python3 scripts/teams_agent/cli.py` from the
repo root inside a login zsh, so it gets the same environment (PATH, tokens from .zshrc) - but detached
(own session), with stdout/stderr in LOG_FILE. It outlives a panel restart; the panel finds it again by
its command line. A Bob started by hand in a terminal is found the same way and can be restarted from
the panel (it then comes back as the panel's own process).

Stop = SIGINT (= Ctrl+C, Bob's own cleanup), then SIGTERM, then SIGKILL.

Teams login from the panel (the terminal only starts the panel): login() opens the SSO
browser (login.py --done-file), the panel's "Jestem zalogowany" calls login_confirm(), and after a good login Bob
is started when asked ("Zaloguj i uruchom"). start/restart refuse while session.json needs a login, so a restart
never leaves Bob down. Bob is started again with the panel (autostart, e.g. after a reboot) when he was meant to
run (state "ma_dzialac": set by start, cleared by a manual stop) and the session is still valid.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CLI_REL = "scripts/teams_agent/cli.py"
STATE_DIR = REPO / "user-data" / "bob-panel"
LOG_FILE = STATE_DIR / "bob.log"
STATE_FILE = STATE_DIR / "supervisor.json"
SHELL = os.environ.get("BOB_PANEL_SHELL", "/bin/zsh")
STOP_GRACE_S = 25
SESSION_FILE = REPO / "scripts" / "teams_agent" / "session.json"
SESSION_MAX_AGE_S = 7 * 24 * 3600          # teams_agent/session.py: older -> SSO login needed
LOGIN_LOG = STATE_DIR / "login.log"
LOGIN_DONE = STATE_DIR / "login-done"
LOGIN_REL = "scripts/teams_agent/login.py"
_lock = threading.Lock()
_login: dict = {}                          # the running login: proc, started, then_start, by


def _ps() -> list[tuple[int, str, str]]:
    """[(pid, start time, command)] of every process."""
    out = subprocess.run(["ps", "-axo", "pid=,lstart=,command="], capture_output=True, text=True, check=False).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 7 or not parts[0].isdigit():
            continue
        rows.append((int(parts[0]), " ".join(parts[1:6]), parts[6]))
    return rows


def find_bob() -> dict | None:
    """The running Bob: {pid, started, command} (the python process, not a wrapping shell)."""
    for pid, started, cmd in _ps():
        if CLI_REL in cmd and "python" in cmd.lower() and "-c " not in cmd and "<defunct>" not in cmd:
            return {"pid": pid, "started": started, "command": cmd}
    return None


_state_lock = threading.Lock()


def _write_state(**fields) -> None:
    """Read-modify-write under a lock, atomic on disk: a reader never sees {} (that would drop ma_dzialac)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    import json  # noqa: PLC0415
    with _state_lock:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
        except (OSError, ValueError):
            state = {}
        state.update(fields)
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass


def read_state() -> dict:
    try:
        import json  # noqa: PLC0415
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _alive(pid: int) -> bool:
    # A Bob started by the panel is its child: once it exits it stays a zombie until reaped, and a zombie
    # still answers kill(pid, 0) - the restart of 23.10.2026 01:02 waited for it and never started Bob again.
    try:
        done, _status = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass                                   # not our child (started in a terminal)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop(by: str = "panel", keep_wanted: bool = False) -> dict:
    with _lock:
        bob = find_bob()
        if not bob:
            return {"ok": True, "message": "Bob nie działał."}
        pid = bob["pid"]
        for sig, wait in ((signal.SIGINT, STOP_GRACE_S), (signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            deadline = time.time() + wait
            while time.time() < deadline and _alive(pid):
                time.sleep(0.5)
            if not _alive(pid):
                break
        ok = not _alive(pid)
        _write_state(last_stop=datetime.now(UTC).isoformat(), last_stop_by=by,
                     **({} if keep_wanted else {"ma_dzialac": False}))
        return {"ok": ok, "message": f"Zatrzymany (pid {pid})." if ok else f"Nie udało się zatrzymać pid {pid}."}


def session_ok() -> bool:
    try:
        return time.time() - SESSION_FILE.stat().st_mtime < SESSION_MAX_AGE_S
    except OSError:
        return False


_NEEDS_LOGIN = {"ok": False, "needs_login": True,
                "message": "Sesja Teams wygasła - najpierw „Zaloguj do Teams” (albo „Zaloguj i uruchom”)."}


def start(by: str = "panel") -> dict:
    with _lock:
        bob = find_bob()
        if bob:
            return {"ok": True, "message": f"Bob już działa (pid {bob['pid']}).", "pid": bob["pid"]}
        if not session_ok():
            return dict(_NEEDS_LOGIN)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)
        log = open(LOG_FILE, "ab", buffering=0)  # noqa: SIM115 -- handed to the child
        os.chmod(LOG_FILE, 0o600)
        log.write(f"\n===== start {datetime.now().isoformat(timespec='seconds')} ({by}) =====\n".encode())
        cmd = f"cd {shlex.quote(str(REPO))} && exec python3 {CLI_REL}"
        proc = subprocess.Popen([SHELL, "-il", "-c", cmd], stdin=subprocess.DEVNULL, stdout=log, stderr=log,  # noqa: S603
                                start_new_session=True, cwd=str(REPO))
        log.close()
        _write_state(last_start=datetime.now(UTC).isoformat(), last_start_by=by, pid=proc.pid, ma_dzialac=True)
        for _ in range(20):                     # the login shell execs into python within a second or two
            time.sleep(0.5)
            if proc.poll() is not None:
                return {"ok": False, "message": f"Bob zakończył się od razu (kod {proc.returncode}) - zobacz log."}
            if find_bob():
                break
        found = find_bob()
        return {"ok": bool(found), "pid": (found or {}).get("pid"),
                "message": f"Uruchomiony (pid {(found or {}).get('pid')})." if found else "Uruchamia się..."}


def restart(by: str = "panel") -> dict:
    if not session_ok():
        return dict(_NEEDS_LOGIN, message="Restart wstrzymany: sesja Teams wygasła, a po zatrzymaniu Bob by nie "
                                          "wstał. Najpierw „Zaloguj do Teams”.")
    s = stop(by, keep_wanted=True)
    if not s["ok"]:
        return s
    r = start(by)
    _write_state(last_restart=datetime.now(UTC).isoformat(), last_restart_by=by)
    return {**r, "message": f"{s['message']} {r['message']}"}


def login(by: str = "panel", then_start: bool = False) -> dict:
    """Open the Teams SSO browser (login.py --done-file) on this Mac; the owner logs in and clicks "Jestem zalogowany".
    then_start: start Bob once the session is saved."""
    with _lock:
        cur = _login.get("proc")
        if cur is not None and cur.poll() is None:
            _login["then_start"] = _login.get("then_start") or then_start
            return {"ok": True, "message": "Logowanie już trwa - dokończ je w otwartej przeglądarce."}
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)
        LOGIN_DONE.unlink(missing_ok=True)
        log = os.fdopen(os.open(LOGIN_LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb", buffering=0)
        cmd = f"cd {shlex.quote(str(REPO))} && exec python3 {LOGIN_REL} --done-file {shlex.quote(str(LOGIN_DONE))}"
        proc = subprocess.Popen([SHELL, "-il", "-c", cmd], stdin=subprocess.DEVNULL, stdout=log, stderr=log,  # noqa: S603
                                start_new_session=True, cwd=str(REPO))
        log.close()
        _login.clear()
        _login.update(proc=proc, started=datetime.now(UTC).isoformat(), then_start=then_start, by=by)
        _write_state(last_login_start=_login["started"], last_login_by=by)
    threading.Thread(target=_after_login, args=(proc,), daemon=True).start()
    return {"ok": True, "message": "Otwieram przeglądarkę na tym Macu: zaloguj się (SSO + MFA, „Pozostań "
                                   "zalogowany” = Tak), a gdy zobaczysz Teams, kliknij „Jestem zalogowany”."}


def _after_login(proc) -> None:
    rc = proc.wait()
    with _lock:
        if _login.get("proc") is not proc:         # a newer login replaced this one - its record is not ours
            return
        _login["exit_code"] = rc
        _login["finished"] = datetime.now(UTC).isoformat()
        then_start, by = _login.get("then_start"), _login.get("by", "panel")
    _write_state(last_login_end=_login["finished"], last_login_code=rc)
    if rc == 0 and then_start:
        _login["start_result"] = start(f"{by} (po logowaniu)")


def login_confirm() -> dict:
    proc = _login.get("proc")
    if proc is None or proc.poll() is not None:
        return {"ok": False, "message": "Logowanie nie trwa - kliknij najpierw „Zaloguj do Teams”."}
    LOGIN_DONE.touch()
    return {"ok": True, "message": "Sprawdzam stronę i zapisuję sesję (plus SharePoint) - kilka sekund."}


def login_status() -> dict:
    proc = _login.get("proc")
    running = proc is not None and proc.poll() is None
    try:
        tail = LOGIN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
    except OSError:
        tail = []
    return {"running": running, "started": _login.get("started"), "finished": _login.get("finished"),
            "exit_code": _login.get("exit_code"), "then_start": _login.get("then_start"),
            "start_result": _login.get("start_result"), "log": tail, "session_ok": session_ok()}


def autostart() -> dict | None:
    """At panel start (login, reboot): bring Bob back when he was meant to run and needs no login."""
    st = read_state()
    if find_bob() or not st.get("ma_dzialac") or not session_ok():
        return None
    return start("panel (autostart)")


def log_tail(lines: int = 200) -> str:
    try:
        with open(LOG_FILE, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 200_000))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])
