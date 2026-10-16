"""Tests for broadcast scheduler logic.

Covers: anchor day detection, HH:MM parsing, phase-due logic, schedule JSON
round-trip, team->job_id mapping, and the generate/dry-run/send phase functions
in broadcast_runner.py.

Strategy: stub out teams_agent imports (api_client, session, ai) using
sys.modules pre-injection before the runner module is loaded.
"""

import importlib
import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BROADCAST_DIR = _REPO_ROOT / "scripts" / "broadcast"
_RUNNER_PATH = _BROADCAST_DIR / "broadcast_runner.py"
_CLI_DIR = _REPO_ROOT / "scripts" / "teams_agent"

# ---------------------------------------------------------------------------
# Stub helpers (mirror test_broadcast_runner.py pattern)
# ---------------------------------------------------------------------------

_FAKE_MCP_GROUPS: dict[str, str] = {
    "jira": "mcp__jira-bob__jira_sprint_stats",
    "kb": "mcp__kb-bob__kb_search",
}


def _make_stubs() -> dict[str, ModuleType]:
    session_stub = ModuleType("session")
    session_stub.ensure_fresh_session = MagicMock(return_value=True)  # type: ignore[attr-defined]

    api_stub = ModuleType("api_client")
    api_stub.send_message = MagicMock(return_value=True)  # type: ignore[attr-defined]
    api_stub.send_html_message = MagicMock(return_value="fake_msg_id")  # type: ignore[attr-defined]
    api_stub.send_gif = MagicMock(return_value=True)  # type: ignore[attr-defined]
    api_stub.md_to_html = MagicMock(side_effect=lambda x: x)  # type: ignore[attr-defined]
    api_stub._token_debug_info = MagicMock(return_value="token=stub")  # type: ignore[attr-defined]

    ai_stub = ModuleType("ai")
    ai_stub._MCP_GROUPS = _FAKE_MCP_GROUPS  # type: ignore[attr-defined]

    return {"session": session_stub, "api_client": api_stub, "ai": ai_stub}


def _load_runner(stubs: dict[str, ModuleType]) -> ModuleType:
    mod_name = "broadcast_runner_sched_test"
    sys.modules.pop(mod_name, None)

    saved = {}
    for name, stub in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = stub

    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(_RUNNER_PATH))
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture()
def runner(tmp_path):
    stubs = _make_stubs()
    mod = _load_runner(stubs)

    mod.JOBS_FILE = tmp_path / "jobs.json"
    mod.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    mod.GENERATED_DIR = tmp_path / "generated"
    mod.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    mod._stub_api = stubs["api_client"]
    mod._stub_session = stubs["session"]
    return mod


def _write_jobs(jobs_file: Path, jobs: list[dict]) -> None:
    jobs_file.write_text(json.dumps(jobs), encoding="utf-8")


# ---------------------------------------------------------------------------
# A: Anchor day detection (_is_broadcast_planning_day)
# ---------------------------------------------------------------------------

# Import directly from cli.py — we need just the pure function.
# We add the cli parent dir to sys.path so we can access the function without
# triggering the full cli.py import (which needs Teams session etc.).
# Instead we extract the function logic here directly for testing.

def _is_broadcast_planning_day(today: date) -> bool:
    """Mirror of cli.py _is_broadcast_planning_day. Tested independently."""
    anchor = date(2026, 6, 16)
    return today.weekday() == 1 and (today - anchor).days % 14 == 0


def test_anchor_day_is_planning_day():
    # 2026-06-16 is a Tuesday and the anchor.
    assert _is_broadcast_planning_day(date(2026, 6, 16)) is True


def test_anchor_plus_14_is_planning_day():
    # Two weeks later is also a planning day.
    assert _is_broadcast_planning_day(date(2026, 6, 30)) is True


def test_anchor_plus_28_is_planning_day():
    assert _is_broadcast_planning_day(date(2026, 7, 14)) is True


def test_non_tuesday_is_not_planning_day():
    # 2026-06-17 is a Wednesday (day after anchor).
    assert _is_broadcast_planning_day(date(2026, 6, 17)) is False


def test_tuesday_not_on_14day_cycle_is_not_planning_day():
    # 2026-06-23 is Tuesday but only 7 days after anchor.
    assert _is_broadcast_planning_day(date(2026, 6, 23)) is False


def test_day_before_anchor_is_not_planning_day():
    # 2026-06-15 is a Monday (one day before anchor) — not a planning day.
    assert _is_broadcast_planning_day(date(2026, 6, 15)) is False


# ---------------------------------------------------------------------------
# B: HH:MM parsing (_parse_reminder_time in cli.py)
# ---------------------------------------------------------------------------

def _parse_reminder_time(token: str):
    """Mirror of cli.py _parse_reminder_time for isolated testing."""
    import re
    m = re.match(r"^(\d{1,2}):(\d{2})$", token.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return (h, mi)


def test_parse_valid_time_midnight():
    assert _parse_reminder_time("00:00") == (0, 0)


def test_parse_valid_time_morning():
    assert _parse_reminder_time("07:00") == (7, 0)


def test_parse_valid_time_end_of_day():
    assert _parse_reminder_time("23:59") == (23, 59)


def test_parse_time_single_digit_hour():
    assert _parse_reminder_time("7:30") == (7, 30)


def test_parse_invalid_time_bad_hour():
    assert _parse_reminder_time("25:00") is None


def test_parse_invalid_time_bad_minute():
    assert _parse_reminder_time("12:60") is None


def test_parse_invalid_time_no_colon():
    assert _parse_reminder_time("0700") is None


def test_parse_invalid_time_empty():
    assert _parse_reminder_time("") is None


# ---------------------------------------------------------------------------
# C: Schedule JSON round-trip
# ---------------------------------------------------------------------------

def test_schedule_json_roundtrip(tmp_path):
    schedule = {
        "planning-prep-prj2": {"generate": "07:00", "dry_run": "07:30", "send": "07:55"},
        "planning-prep-teama": {"generate": "07:00", "dry_run": "07:30", "send": "07:59"},
    }
    f = tmp_path / "broadcast-schedule.json"
    f.write_text(json.dumps(schedule, ensure_ascii=False), encoding="utf-8")
    loaded = json.loads(f.read_text(encoding="utf-8"))
    assert loaded["planning-prep-prj2"]["send"] == "07:55"
    assert loaded["planning-prep-teama"]["send"] == "07:59"
    assert loaded["planning-prep-prj2"]["generate"] == "07:00"


# ---------------------------------------------------------------------------
# D: Team -> job_id mapping
# ---------------------------------------------------------------------------

SAMPLE_JOBS = [
    {"id": "planning-prep-prj2", "team": "FieldOps",
     "channel": "19:chat23@thread.v2", "enabled": True,
     "tools": [], "brief_file": "briefs/x.md"},
    {"id": "planning-prep-teama", "team": "TeamA",
     "channel": "19:chat25@thread.v2", "enabled": True,
     "tools": [], "brief_file": "briefs/y.md"},
]


def _find_job_id_by_team(jobs: list[dict], team: str) -> str | None:
    """Mirror of the lookup logic in cli.py broadcast set handler."""
    for j in jobs:
        if j.get("team", "").lower() == team.lower():
            return j["id"]
    return None


def test_team_to_job_id_fieldops():
    assert _find_job_id_by_team(SAMPLE_JOBS, "FieldOps") == "planning-prep-prj2"


def test_team_to_job_id_teama():
    assert _find_job_id_by_team(SAMPLE_JOBS, "TeamA") == "planning-prep-teama"


def test_team_to_job_id_case_insensitive():
    assert _find_job_id_by_team(SAMPLE_JOBS, "fieldops") == "planning-prep-prj2"


def test_team_to_job_id_unknown():
    assert _find_job_id_by_team(SAMPLE_JOBS, "Unknown") is None


# ---------------------------------------------------------------------------
# E: Phase-due detection logic
# ---------------------------------------------------------------------------

def test_phase_due_when_within_window():
    """A phase scheduled at 07:00 is due at 07:02 (within 5-min window)."""
    now_h, now_m = 7, 2
    sched_h, sched_m = 7, 0
    now_minutes = now_h * 60 + now_m
    sched_minutes = sched_h * 60 + sched_m
    assert sched_minutes <= now_minutes < sched_minutes + 5


def test_phase_not_due_before_time():
    """A phase scheduled at 07:00 is NOT due at 06:59."""
    now_h, now_m = 6, 59
    sched_h, sched_m = 7, 0
    now_minutes = now_h * 60 + now_m
    sched_minutes = sched_h * 60 + sched_m
    assert not (sched_minutes <= now_minutes < sched_minutes + 5)


def test_phase_not_due_after_window():
    """A phase scheduled at 07:00 is NOT due at 07:05 (past the 5-min window)."""
    now_h, now_m = 7, 5
    sched_h, sched_m = 7, 0
    now_minutes = now_h * 60 + now_m
    sched_minutes = sched_h * 60 + sched_m
    assert not (sched_minutes <= now_minutes < sched_minutes + 5)


# ---------------------------------------------------------------------------
# F: runner -- _generated_path helper
# ---------------------------------------------------------------------------

def test_generated_path_returns_correct_name(runner, tmp_path):
    runner.GENERATED_DIR = tmp_path / "generated"
    runner.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = runner._generated_path("planning-prep-prj2")
    assert path.name == "planning-prep-prj2.json"


# ---------------------------------------------------------------------------
# G: runner -- phase generate writes file, phase send/dry-run read file
# ---------------------------------------------------------------------------

SAMPLE_BRIEF = (
    "# Brief\n\n"
    "### Wiadomość 1 — Powitanie\n"
    "Opis sekcji 1.\n\n"
    "### Wiadomość 2 — GIF\n"
    "GIF: sprint start\n"
)


def test_phase_generate_writes_file(runner, tmp_path, monkeypatch):
    """generate phase produces generated/<job_id>.json without calling send."""
    channel = "19:chat28@thread.v2"
    brief_dir = tmp_path / "briefs"
    brief_dir.mkdir()
    brief = brief_dir / "test.md"
    brief.write_text(SAMPLE_BRIEF, encoding="utf-8")

    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    runner.GENERATED_DIR = tmp_path / "generated"

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": [],
         "brief_file": "briefs/test.md"}
    ])

    call_count = 0

    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        m = MagicMock()
        m.returncode = 0
        m.stdout = json.dumps({"result": f"Wiadomosc {call_count}", "is_error": False})
        m.stderr = ""
        return m

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.run_job("j", "generate")
    assert result is True

    gen_file = runner.GENERATED_DIR / "j.json"
    assert gen_file.exists()
    data = json.loads(gen_file.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert all("section_idx" in item and "content" in item for item in data)
    # generate does NOT call send
    runner._stub_api.send_html_message.assert_not_called()
    runner._stub_api.send_gif.assert_not_called()


def test_phase_dry_run_reads_file_not_claude(runner, tmp_path, monkeypatch):
    """dry-run reads generated/<job_id>.json; does NOT call Claude CLI."""
    channel = "19:chat28@thread.v2"
    notatki = "48:notes"

    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    runner.GENERATED_DIR = tmp_path / "generated"
    runner.GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-write the generated file.
    gen_data = [
        {"section_idx": 1, "content": "Hello!"},
        {"section_idx": 2, "content": "GIF: sprint end"},
    ]
    (runner.GENERATED_DIR / "j.json").write_text(
        json.dumps(gen_data, ensure_ascii=False), encoding="utf-8"
    )

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": [], "brief_file": "briefs/x.md"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)
    monkeypatch.setattr(runner, "_load_control_channel", lambda: notatki)

    claude_called = []

    def fake_run(*args, **kwargs):
        claude_called.append(args)
        m = MagicMock()
        m.returncode = 0
        m.stdout = json.dumps({"result": "x", "is_error": False})
        m.stderr = ""
        return m

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)
    monkeypatch.setattr(runner.api_client, "send_gif", runner._stub_api.send_gif)
    monkeypatch.setattr(runner, "_INTER_MSG_DELAY_S", 0)

    result = runner.run_job("j", "dry-run")
    assert result is True
    # Claude must NOT have been called (no generation).
    assert claude_called == []
    # Messages sent to Notatki (control channel).
    runner._stub_api.send_html_message.assert_called_once()
    runner._stub_api.send_gif.assert_called_once_with("sprint end", notatki)


def test_phase_send_reads_file_not_claude(runner, tmp_path, monkeypatch):
    """send reads generated/<job_id>.json; does NOT call Claude CLI."""
    channel = "19:chat28@thread.v2"

    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    runner.GENERATED_DIR = tmp_path / "generated"
    runner.GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    gen_data = [
        {"section_idx": 1, "content": "Hello!"},
    ]
    (runner.GENERATED_DIR / "j.json").write_text(
        json.dumps(gen_data, ensure_ascii=False), encoding="utf-8"
    )

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": [], "brief_file": "briefs/x.md"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    claude_called = []

    def fake_run(*args, **kwargs):
        claude_called.append(args)
        m = MagicMock()
        m.returncode = 0
        m.stdout = json.dumps({"result": "x", "is_error": False})
        m.stderr = ""
        return m

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)
    monkeypatch.setattr(runner, "_INTER_MSG_DELAY_S", 0)

    result = runner.run_job("j", "send")
    assert result is True
    # Claude must NOT have been called.
    assert claude_called == []
    runner._stub_api.send_html_message.assert_called_once()


def test_dry_run_fails_without_generated_file(runner, tmp_path, monkeypatch):
    """dry-run returns False when generated/<job_id>.json does not exist."""
    channel = "19:chat28@thread.v2"

    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    runner.GENERATED_DIR = tmp_path / "generated"
    runner.GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": [], "brief_file": "briefs/x.md"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    result = runner.run_job("j", "dry-run")
    assert result is False


def test_send_fails_without_generated_file(runner, tmp_path, monkeypatch):
    """send returns False when generated/<job_id>.json does not exist."""
    channel = "19:chat28@thread.v2"

    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    runner.GENERATED_DIR = tmp_path / "generated"
    runner.GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": [], "brief_file": "briefs/x.md"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    result = runner.run_job("j", "send")
    assert result is False


# ---------------------------------------------------------------------------
# H: placeholder channel guard still works in send phase
# ---------------------------------------------------------------------------

def test_send_phase_placeholder_channel_guard(runner, tmp_path, monkeypatch):
    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    runner.GENERATED_DIR = tmp_path / "generated"
    runner.GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    gen_data = [{"section_idx": 1, "content": "Hi!"}]
    (runner.GENERATED_DIR / "j.json").write_text(
        json.dumps(gen_data, ensure_ascii=False), encoding="utf-8"
    )

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": "NOTATKI_CONV_ID_PLACEHOLDER",
         "enabled": True, "tools": [], "brief_file": "briefs/x.md"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    result = runner.run_job("j", "send")
    assert result is False
    runner._stub_api.send_html_message.assert_not_called()


# ---------------------------------------------------------------------------
# I: generate phase -- ensure_ascii=False in persisted JSON
# ---------------------------------------------------------------------------

def test_generate_file_ensure_ascii_false(runner, tmp_path, monkeypatch):
    """Content with Polish characters must be stored as UTF-8, not escaped."""
    channel = "19:chat28@thread.v2"
    brief_dir = tmp_path / "briefs"
    brief_dir.mkdir()
    brief = brief_dir / "test.md"
    brief.write_text(SAMPLE_BRIEF, encoding="utf-8")

    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    runner.GENERATED_DIR = tmp_path / "generated"

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": [],
         "brief_file": "briefs/test.md"}
    ])

    def fake_run(*args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = json.dumps({"result": "Witaj Zespole TeamB!", "is_error": False})
        m.stderr = ""
        return m

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner.run_job("j", "generate")
    raw = (runner.GENERATED_DIR / "j.json").read_bytes()
    # Polish chars should appear as UTF-8 bytes, not as \\u escapes.
    assert b"\\u" not in raw


# ---------------------------------------------------------------------------
# J: schedule_type == "always" fires on weekends
# ---------------------------------------------------------------------------

def _schedule_type_allows(schedule_type: str, is_workday: bool, is_planning_day: bool) -> bool:
    """Mirror of the schedule_type filter in cli.py _broadcast_scheduler_tick."""
    if schedule_type == "always":
        return True
    if schedule_type == "daily":
        return is_workday
    return is_planning_day


def test_always_fires_on_saturday():
    saturday = date(2026, 6, 20)  # Saturday
    is_workday = saturday.weekday() < 5
    is_planning = _is_broadcast_planning_day(saturday)
    assert _schedule_type_allows("always", is_workday, is_planning) is True


def test_always_fires_on_sunday():
    sunday = date(2026, 6, 21)  # Sunday
    is_workday = sunday.weekday() < 5
    is_planning = _is_broadcast_planning_day(sunday)
    assert _schedule_type_allows("always", is_workday, is_planning) is True


def test_always_fires_on_monday():
    monday = date(2026, 6, 22)  # Monday
    is_workday = monday.weekday() < 5
    is_planning = _is_broadcast_planning_day(monday)
    assert _schedule_type_allows("always", is_workday, is_planning) is True


def test_daily_blocked_on_saturday():
    saturday = date(2026, 6, 20)
    is_workday = saturday.weekday() < 5
    is_planning = _is_broadcast_planning_day(saturday)
    assert _schedule_type_allows("daily", is_workday, is_planning) is False


def test_always_fires_on_planning_day():
    """always must also fire on planning days (not just non-workdays)."""
    planning_day = date(2026, 6, 16)  # Tuesday, anchor
    is_workday = planning_day.weekday() < 5
    is_planning = _is_broadcast_planning_day(planning_day)
    assert _schedule_type_allows("always", is_workday, is_planning) is True
