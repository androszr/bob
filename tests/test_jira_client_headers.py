"""Unit tests for scripts/jira/jira_client.py _HEADERS freshness.

Covered here: _HEADERS must not be a module-level dict built once at import
time from ATLASSIAN_TOKEN. jira_client.py is imported once into a long-running
MCP daemon (scripts/jira/mcp_server.py --http), so a token rotated in .env
would otherwise have no effect until the daemon was restarted, producing 401s
on reads and confusing field-permission errors on jira_create.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

_JIRA_DIR = Path(__file__).resolve().parent.parent / "scripts" / "jira"
_spec = importlib.util.spec_from_file_location(
    "jira_client_headers_under_test",
    str(_JIRA_DIR / "jira_client.py"),
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["jira_client_headers_under_test"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]


def test_headers_reflect_env_file_rotation_without_reimport(monkeypatch, tmp_path):
    """A token rotated in .env must take effect on the next _HEADERS access,
    in the same process, with no module re-import -- this is exactly what a
    long-running MCP daemon needs.
    """
    env_file = tmp_path / "fake.env"
    env_file.write_text("ATLASSIAN_TOKEN=token_v1\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "_ENV_FILE", env_file)

    assert _mod._HEADERS["Authorization"] == "Bearer token_v1"

    env_file.write_text("ATLASSIAN_TOKEN=token_v2_rotated\n", encoding="utf-8")

    assert _mod._HEADERS["Authorization"] == "Bearer token_v2_rotated"


def test_headers_still_a_dict_with_expected_keys():
    """External call sites use _HEADERS as a plain dict (spread with **, or
    passed directly as `headers=`) -- the PEP 562 __getattr__ shim must keep
    returning a real dict, not some lazy/proxy object.
    """
    headers = _mod._HEADERS
    assert isinstance(headers, dict)
    assert headers.keys() >= {"Authorization", "Content-Type", "Accept"}
    assert headers["Authorization"].startswith("Bearer ")


def test_get_uses_freshly_computed_headers(monkeypatch, tmp_path):
    """_get() (jira_client.py's own internal helper) must route through the
    same fresh-read path as external _HEADERS access, not a frozen global --
    a prior version of this fix left _get() reading a bare `_HEADERS` name
    that no longer existed as a module-level constant.
    """
    env_file = tmp_path / "fake.env"
    env_file.write_text("ATLASSIAN_TOKEN=token_for_get\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "_ENV_FILE", env_file)

    with patch.object(_mod.requests, "get") as mock_get:
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {"ok": True}
        result = _mod._get("/rest/api/2/issue/PRJ1-1")

    assert result == {"ok": True}
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer token_for_get"


def test_getattr_raises_for_unknown_attribute():
    """The PEP 562 __getattr__ shim must not swallow typos into silent None --
    only _HEADERS is special-cased, everything else still raises normally."""
    import pytest

    with pytest.raises(AttributeError):
        _mod.__getattr__("_TOTALLY_MADE_UP_NAME")


def test_extract_field_value_variants():
    """_extract_field_value is used by jira-bob's jira_issue for EXTRA_CUSTOM_FIELDS."""
    assert _mod._extract_field_value(None) is None
    assert _mod._extract_field_value("plain") == "plain"
    assert _mod._extract_field_value({"value": "V", "id": "1"}) == "V"
    assert _mod._extract_field_value({"id": "1"}) == "1"
    assert _mod._extract_field_value(["a", {"value": "b"}]) == ["a", "b"]


def test_get_issue_brief_returns_what_a_reply_to_the_reporter_needs():
    created = "2026-10-20T08:00:00.000+0200"
    raw = {"key": "PRJ1-7", "fields": {"summary": "Login fails", "description": "  " + "x" * 5000 + "  ",
                                       "reporter": {"displayName": "Ewa Nowak"}, "created": created}}
    with patch.object(_mod, "_get", return_value=raw) as get:
        brief = _mod.get_issue_brief("PRJ1-7", max_desc=100)
    assert get.call_args.args[1] == {"fields": "summary,description,reporter,created"}
    assert brief == {"key": "PRJ1-7", "summary": "Login fails", "opis": "x" * 100, "zglaszajacy": "Ewa Nowak",
                     "utworzono": created}
    with patch.object(_mod, "_get", return_value=None):
        assert _mod.get_issue_brief("PRJ1-8") is None
    with patch.object(_mod, "_get", return_value={"key": "PRJ1-9", "fields": {}}):
        assert _mod.get_issue_brief("PRJ1-9")["zglaszajacy"] == ""
