"""Session-freshness helper for the Teams Playwright scraper.

Single source of truth for SESSION_FILE path and session-age policy.
Stores Playwright `storage_state` (cookies + localStorage) in session.json.
"""

import json
import os
import time as _time_mod
from pathlib import Path

SESSION_FILE = Path(__file__).parent / "session.json"

# 7 days — Microsoft SSO cookies typically survive at least this long for
# enterprise tenants with persistent-session policies enabled.
SESSION_MAX_AGE_S = 7 * 24 * 3600


def ensure_fresh_session() -> bool:
    """Return True if a fresh session exists (no relogin needed).

    Checks that SESSION_FILE exists and its mtime is within SESSION_MAX_AGE_S.
    Does NOT emit @@CURRENT — this is a standalone terminal tool, not a pipeline.
    """
    if SESSION_FILE.exists():
        age = _time_mod.time() - SESSION_FILE.stat().st_mtime
        if age < SESSION_MAX_AGE_S:
            hours = int(age // 3600)
            mins = int(age % 3600 // 60)
            print(f"Sesja OK (wiek: {hours}h {mins}min)", flush=True)
            return True
        print("Sesja wygasla — wymagane ponowne logowanie SSO.", flush=True)
    else:
        print("Brak session.json — wymagane logowanie SSO.", flush=True)
    return False


def is_partitioned(cookie: dict) -> bool:
    """True for a partitioned (CHIPS) cookie, e.g. SPOIDCRL keyed to cloud.microsoft.

    Playwright's APIRequestContext ignores partitionKey and sends such a cookie on
    top-level requests, where a real browser would not. SharePoint then picks
    SPOIDCRL over FedAuth and answers every _api call with 403.
    """
    return bool(cookie.get("partitionKey"))


def load_storage_state(path: Path = SESSION_FILE) -> dict:
    """session.json as a storage_state dict, without partitioned cookies."""
    state = json.loads(path.read_text(encoding="utf-8"))
    state["cookies"] = [c for c in state.get("cookies", []) if not is_partitioned(c)]
    return state


def save_storage_state(context, path: Path = SESSION_FILE) -> None:
    """Write the context's storage_state to path (0600), without partitioned cookies."""
    state = context.storage_state()
    state["cookies"] = [c for c in state.get("cookies", []) if not is_partitioned(c)]
    tmp = path.with_suffix(".tmp.json")
    # Created 0600 up front: the cookies are never readable by others, not even briefly.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(state))
    os.chmod(tmp, 0o600)  # O_CREAT mode does not apply to a leftover tmp from a crash
    tmp.replace(path)
