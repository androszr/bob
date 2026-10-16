"""Shared fixtures.

Keep tests away from the live bot's runtime files. cli/ai/user_memory write to
paths next to their source (history/, log.jsonl, perf-metrics.jsonl,
user-profiles/) -- without this fixture, a pytest run would append "Test reply"
turns to real chat histories that Bob later feeds back to the model as context.
"""

import os
import sys
from pathlib import Path

import pytest

# Org/owner config (scripts/org_config.py) is read at import time, so it is pinned
# here, before any test module imports app code: a developer's .env or shell must
# not change what the suite sees. The gitignored JSON configs come from fixtures.
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
os.environ["PEOPLE_FILE"] = str(_FIXTURES / "people.json")
os.environ["JIRA_FIELDS_FILE"] = str(_FIXTURES / "jira-fields.json")
# The values the existing fixtures were written against. Neutral defaults are
# covered by tests/test_org_config.py, which never reads os.environ.
os.environ.update({
    "OWNER_TEAMS_USER_ID": "00000000-0000-0000-0000-000000000000",
    "OWNER_DISPLAY_NAME": "Kowalski Jan",
    "OWNER_ALIAS": "Szef",
    "OWNER_JIRA_LOGIN": "jan.kowalski@example.com",
    "ORG_EMAIL_DOMAIN": "example.com",
    "JIRA_URL": "https://jira.example.com",
    "SHAREPOINT_TENANT": "contoso",
    "NAME_NOISE_PREFIXES": "EXT",
})
for _unset in ("JIRA_BOARD_ID", "JIRA_PROJECT", "JIRA_TEAMS", "URLOPY_SHAREPOINT_URL",
               "SHAREPOINT_UPLOAD_FOLDER_URL"):
    os.environ[_unset] = ""


@pytest.fixture(autouse=True)
def _isolate_teams_runtime_files(tmp_path, monkeypatch):
    # mcp_admin/auth.py: daemon token + per-call MCP config files.
    monkeypatch.setenv("BOB_MCP_RUNDIR", str(tmp_path / "bob-mcp"))
    targets = {
        "cli": {"_HISTORY_DIR": tmp_path / "history", "DEFAULT_LOG_FILE": str(tmp_path / "log.jsonl")},
        "ai": {"_PERF_LOG": tmp_path / "perf-metrics.jsonl"},
        "user_memory": {"PROFILE_DIR": tmp_path / "user-profiles"},
    }
    for module_name, attrs in targets.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr, value in attrs.items():
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, value)
    yield
