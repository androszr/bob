"""Unit tests for scripts/teams_agent/session.py."""

import json
import sys
from pathlib import Path

# Make the package importable without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent"))
import session as session_mod  # noqa: E402


def test_ensure_fresh_session_missing_file(tmp_path, monkeypatch):
    """When SESSION_FILE does not exist, ensure_fresh_session returns False."""
    monkeypatch.setattr(session_mod, "SESSION_FILE", tmp_path / "session.json")
    assert session_mod.ensure_fresh_session() is False


def test_ensure_fresh_session_fresh(tmp_path, monkeypatch):
    """When SESSION_FILE exists and is younger than SESSION_MAX_AGE_S, returns True."""
    session_file = tmp_path / "session.json"
    session_file.write_text("{}")
    monkeypatch.setattr(session_mod, "SESSION_FILE", session_file)
    assert session_mod.ensure_fresh_session() is True


def test_ensure_fresh_session_stale(tmp_path, monkeypatch):
    """When SESSION_FILE is older than SESSION_MAX_AGE_S, returns False."""
    session_file = tmp_path / "session.json"
    session_file.write_text("{}")
    monkeypatch.setattr(session_mod, "SESSION_FILE", session_file)

    # Simulate a stale mtime by patching time.time to return far in the future.
    stale_age = session_mod.SESSION_MAX_AGE_S + 1
    real_mtime = session_file.stat().st_mtime
    monkeypatch.setattr(session_mod._time_mod, "time", lambda: real_mtime + stale_age)

    assert session_mod.ensure_fresh_session() is False


_FEDAUTH = {"name": "FedAuth", "value": "f", "domain": "contoso-my.sharepoint.com", "path": "/"}
_SPOIDCRL = {
    "name": "SPOIDCRL", "value": "s", "domain": "contoso-my.sharepoint.com", "path": "/",
    "partitionKey": "https://cloud.microsoft",
}


def test_load_storage_state_drops_partitioned_cookies(tmp_path):
    """A partitioned SPOIDCRL makes SharePoint answer _api calls with 403 -- never load it."""
    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({"cookies": [_FEDAUTH, _SPOIDCRL], "origins": []}))

    state = session_mod.load_storage_state(session_file)

    assert [c["name"] for c in state["cookies"]] == ["FedAuth"]
    assert state["origins"] == []


def test_save_storage_state_replaces_session_without_partitioned_cookies(tmp_path):
    """Overwrites an existing session.json: partitioned cookies dropped, 0600, no tmp left."""
    class _Ctx:
        def storage_state(self):
            return {"cookies": [_FEDAUTH, _SPOIDCRL], "origins": []}

    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({"cookies": [_SPOIDCRL], "origins": []}))
    session_file.chmod(0o644)

    session_mod.save_storage_state(_Ctx(), session_file)

    saved = json.loads(session_file.read_text())
    assert [c["name"] for c in saved["cookies"]] == ["FedAuth"]
    assert session_file.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["session.json"]
