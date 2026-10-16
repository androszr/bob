"""Unit tests for scripts/broadcast/broadcast_runner.py.

Strategy: use sys.modules pre-injection to stub out the heavy teams_agent
imports (api_client, session, ai) before the module is loaded. This avoids
triggering _refresh_mcp_config() and Playwright dependencies.
"""

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Shared paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BROADCAST_DIR = _REPO_ROOT / "scripts" / "broadcast"
_RUNNER_PATH = _BROADCAST_DIR / "broadcast_runner.py"

# ---------------------------------------------------------------------------
# Stub modules
# ---------------------------------------------------------------------------

_FAKE_MCP_GROUPS: dict[str, str] = {
    "jira": "mcp__jira-bob__jira_sprint_stats",
    "kb": "mcp__kb-bob__kb_search",
    "meetings": "mcp__meetings-bob__meetings_get",
}


def _fake_build_mention_chip(display_name: str, mri: str, start_itemid: int = 0):
    """Lightweight stand-in for api_client.build_mention_chip — deterministic,
    inspectable markup without pulling in the real (network-adjacent) module."""
    html = f'<mention itemid="{start_itemid}" mri="{mri}">{display_name}</mention>'
    entries = [{"itemid": str(start_itemid), "mri": mri, "displayName": display_name}]
    return html, entries, start_itemid + 1


def _make_stubs() -> dict[str, ModuleType]:
    session_stub = ModuleType("session")
    session_stub.ensure_fresh_session = MagicMock(return_value=True)  # type: ignore[attr-defined]

    api_stub = ModuleType("api_client")
    api_stub.send_message = MagicMock(return_value=True)  # type: ignore[attr-defined]
    api_stub.send_html_message = MagicMock(return_value="fake_msg_id")  # type: ignore[attr-defined]
    api_stub.send_gif = MagicMock(return_value=True)  # type: ignore[attr-defined]
    api_stub.md_to_html = MagicMock(side_effect=lambda x: x)  # type: ignore[attr-defined]
    api_stub._token_debug_info = MagicMock(return_value="token=stub")  # type: ignore[attr-defined]
    api_stub.build_mention_chip = MagicMock(side_effect=_fake_build_mention_chip)  # type: ignore[attr-defined]

    ai_stub = ModuleType("ai")
    ai_stub._MCP_GROUPS = _FAKE_MCP_GROUPS  # type: ignore[attr-defined]

    # Real user_memory.py has no heavy deps, but stubbing it keeps tests isolated
    # from whatever real profiles happen to exist under scripts/teams_agent/user-profiles/.
    user_memory_stub = ModuleType("user_memory")
    user_memory_stub.find_profile_by_name = MagicMock(return_value=None)  # type: ignore[attr-defined]

    return {
        "session": session_stub,
        "api_client": api_stub,
        "ai": ai_stub,
        "user_memory": user_memory_stub,
    }


def _load_runner(stubs: dict[str, ModuleType]) -> ModuleType:
    mod_name = "broadcast_runner_under_test"
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner(tmp_path):
    stubs = _make_stubs()
    mod = _load_runner(stubs)

    mod.JOBS_FILE = tmp_path / "jobs.json"
    mod.LOG_FILE = tmp_path / "broadcast.log.jsonl"
    mod._stub_api = stubs["api_client"]
    mod._stub_session = stubs["session"]
    mod._stub_user_memory = stubs["user_memory"]
    return mod


def _write_jobs(jobs_file: Path, jobs: list[dict]) -> None:
    jobs_file.write_text(json.dumps(jobs), encoding="utf-8")


# ---------------------------------------------------------------------------
# A: load_job
# ---------------------------------------------------------------------------


def test_load_job_returns_correct_job(runner, tmp_path):
    jobs = [
        {"id": "planning-prep", "channel": "CHAN1", "enabled": True, "tools": ["jira"], "prompt": "hi"},
        {"id": "other", "channel": "CHAN2", "enabled": True, "tools": [], "prompt": "other"},
    ]
    _write_jobs(runner.JOBS_FILE, jobs)
    job = runner.load_job("planning-prep")
    assert job is not None
    assert job["id"] == "planning-prep"


def test_load_job_returns_none_for_missing_id(runner):
    _write_jobs(runner.JOBS_FILE, [{"id": "other", "enabled": True, "tools": [], "prompt": "x", "channel": "C"}])
    assert runner.load_job("nonexistent") is None


# ---------------------------------------------------------------------------
# B: disabled job
# ---------------------------------------------------------------------------


def test_disabled_job_exits_nonzero(runner):
    _write_jobs(runner.JOBS_FILE, [{"id": "d", "channel": "C", "enabled": False, "tools": [], "prompt": "x"}])
    with pytest.raises(SystemExit) as exc_info:
        runner.load_job("d")
    assert exc_info.value.code != 0


def test_disabled_job_does_not_send_message(runner, monkeypatch):
    _write_jobs(runner.JOBS_FILE, [{"id": "d", "channel": "C", "enabled": False, "tools": [], "prompt": "x"}])
    with pytest.raises(SystemExit):
        runner.run_job("d")
    runner._stub_api.send_html_message.assert_not_called()


# ---------------------------------------------------------------------------
# C: successful single-call job
# ---------------------------------------------------------------------------


def test_successful_job_calls_send_message_and_logs(runner, monkeypatch):
    channel = "19:chat28@thread.v2"
    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": ["jira"], "prompt": "Do it."}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = json.dumps({"result": "Status sprintu: OK.", "is_error": False})
    fake_proc.stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)

    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)

    assert runner.run_job("j") is True
    runner._stub_api.send_html_message.assert_called_once()
    assert "Status sprintu" in runner._stub_api.send_html_message.call_args[0][0]

    record = json.loads(runner.LOG_FILE.read_text(encoding="utf-8").strip())
    assert record["success"] is True
    assert record["chars_sent"] > 0


# ---------------------------------------------------------------------------
# D: empty generation
# ---------------------------------------------------------------------------


def test_empty_generation_no_send_logs_failure(runner, monkeypatch):
    channel = "19:chat28@thread.v2"
    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": ["jira"], "prompt": "p"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = json.dumps({"result": "", "is_error": False})
    fake_proc.stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)
    monkeypatch.setattr(runner.api_client, "send_message", runner._stub_api.send_message)

    assert runner.run_job("j") is False
    runner._stub_api.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# E: send_message failure
# ---------------------------------------------------------------------------


def test_send_failure_logs_false(runner, monkeypatch):
    channel = "19:chat28@thread.v2"
    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": ["jira"], "prompt": "p"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = json.dumps({"result": "Some text.", "is_error": False})
    fake_proc.stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)

    runner._stub_api.send_html_message.return_value = None  # failure = None
    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)
    monkeypatch.setattr(runner.api_client, "_token_debug_info", lambda: "expired")

    assert runner.run_job("j") is False
    record = json.loads(runner.LOG_FILE.read_text(encoding="utf-8").strip())
    assert record["success"] is False


# ---------------------------------------------------------------------------
# F: placeholder channel guard
# ---------------------------------------------------------------------------


def test_placeholder_channel_guard(runner, monkeypatch):
    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": "NOTATKI_CONV_ID_PLACEHOLDER", "enabled": True, "tools": [], "prompt": "p"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)
    monkeypatch.setattr(runner.api_client, "send_message", runner._stub_api.send_message)

    assert runner.run_job("j") is False
    runner._stub_api.send_message.assert_not_called()
    record = json.loads(runner.LOG_FILE.read_text(encoding="utf-8").strip())
    assert record["error"] == "placeholder_channel"


# ---------------------------------------------------------------------------
# G: _split_messages
# ---------------------------------------------------------------------------


def test_split_messages_single(runner):
    assert runner._split_messages("Hello!") == ["Hello!"]


def test_split_messages_multiple(runner):
    assert runner._split_messages("A\n---MSG---\nB\n---MSG---\nC") == ["A", "B", "C"]


def test_split_messages_strips_whitespace(runner):
    assert runner._split_messages("  A  \n---MSG---\n  B  ") == ["A", "B"]


def test_split_messages_ignores_empty_parts(runner):
    assert runner._split_messages("A\n---MSG---\n\n---MSG---\nB") == ["A", "B"]


# ---------------------------------------------------------------------------
# H: GIF helpers
# ---------------------------------------------------------------------------


def test_is_gif_message(runner):
    assert runner._is_gif_message("GIF: running fast") is True
    assert runner._is_gif_message("Hello Teams!") is False


def test_gif_query_extraction(runner):
    assert runner._gif_query("GIF: running full speed") == "running full speed"
    assert runner._gif_query("GIF:  extra  ") == "extra"


def test_gif_message_calls_send_gif_not_send_message(runner, monkeypatch):
    channel = "19:chat28@thread.v2"
    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": ["jira"], "prompt": "p"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    generated = "Hello!\n---MSG---\nGIF: sprint finish line"
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = json.dumps({"result": generated, "is_error": False})
    fake_proc.stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)

    runner._stub_api.send_html_message.return_value = "fake_id"
    runner._stub_api.send_gif.return_value = True
    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)
    monkeypatch.setattr(runner.api_client, "send_gif", runner._stub_api.send_gif)
    monkeypatch.setattr(runner, "_INTER_MSG_DELAY_S", 0)

    assert runner.run_job("j") is True
    runner._stub_api.send_html_message.assert_called_once()
    runner._stub_api.send_gif.assert_called_once_with("sprint finish line", channel)


# ---------------------------------------------------------------------------
# I: multi-message single-call
# ---------------------------------------------------------------------------


def test_multi_message_job_sends_multiple(runner, monkeypatch):
    channel = "19:chat28@thread.v2"
    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": ["jira"], "prompt": "p"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    generated = "Msg one\n---MSG---\nMsg two\n---MSG---\nMsg three"
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = json.dumps({"result": generated, "is_error": False})
    fake_proc.stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)

    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)
    monkeypatch.setattr(runner, "_INTER_MSG_DELAY_S", 0)

    assert runner.run_job("j") is True
    assert runner._stub_api.send_html_message.call_count == 3
    record = json.loads(runner.LOG_FILE.read_text(encoding="utf-8").strip())
    assert record["messages_total"] == 3
    assert record["messages_sent"] == 3


# ---------------------------------------------------------------------------
# J: _parse_brief_sections
# ---------------------------------------------------------------------------


SAMPLE_BRIEF = """\
# Brief

## Kontekst
Jakis kontekst.

## Wiadomości

### Wiadomość 1 — Powitanie
Opis sekcji 1.

### Wiadomość 2 — Anegdoty
Opis sekcji 2.

### Wiadomość 3 — GIF
GIF: sprint vibes
"""


def test_parse_brief_sections_preamble(runner):
    preamble, sections = runner._parse_brief_sections(SAMPLE_BRIEF)
    assert "## Kontekst" in preamble
    assert "### Wiadomość 1" not in preamble


def test_parse_brief_sections_count(runner):
    _, sections = runner._parse_brief_sections(SAMPLE_BRIEF)
    assert len(sections) == 3


def test_parse_brief_sections_headers(runner):
    _, sections = runner._parse_brief_sections(SAMPLE_BRIEF)
    assert sections[0].startswith("### Wiadomość 1")
    assert sections[1].startswith("### Wiadomość 2")
    assert sections[2].startswith("### Wiadomość 3")


def test_parse_brief_sections_no_sections(runner):
    preamble, sections = runner._parse_brief_sections("Just a preamble, no sections.")
    assert sections == []
    assert "preamble" in preamble


# ---------------------------------------------------------------------------
# K: per-section mode (brief_file)
# ---------------------------------------------------------------------------


def test_per_section_mode_calls_claude_per_section(runner, tmp_path, monkeypatch):
    channel = "19:chat28@thread.v2"

    brief_dir = tmp_path / "briefs"
    brief_dir.mkdir()
    brief = brief_dir / "test.md"
    brief.write_text(SAMPLE_BRIEF, encoding="utf-8")

    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": ["jira"],
         "brief_file": "briefs/test.md"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

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
    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)
    monkeypatch.setattr(runner.api_client, "send_gif", runner._stub_api.send_gif)
    monkeypatch.setattr(runner, "_INTER_MSG_DELAY_S", 0)

    assert runner.run_job("j") is True
    # One Claude call per section (3 sections in SAMPLE_BRIEF).
    assert call_count == 3
    assert runner._stub_api.send_html_message.call_count == 3


def test_per_section_failed_section_skipped(runner, tmp_path, monkeypatch):
    """A section where Claude returns empty is skipped but others succeed."""
    channel = "19:chat28@thread.v2"

    brief_dir = tmp_path / "briefs"
    brief_dir.mkdir()
    brief = brief_dir / "test.md"
    brief.write_text(SAMPLE_BRIEF, encoding="utf-8")

    runner._BROADCAST_DIR = tmp_path
    runner.JOBS_FILE = tmp_path / "jobs.json"
    runner.LOG_FILE = tmp_path / "broadcast.log.jsonl"

    _write_jobs(runner.JOBS_FILE, [
        {"id": "j", "channel": channel, "enabled": True, "tools": ["jira"],
         "brief_file": "briefs/test.md"}
    ])
    monkeypatch.setattr(runner, "ensure_fresh_session", lambda: True)

    call_count = 0

    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        m = MagicMock()
        m.returncode = 0
        # Second section returns empty.
        result = "" if call_count == 2 else f"Msg {call_count}"
        m.stdout = json.dumps({"result": result, "is_error": False})
        m.stderr = ""
        return m

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)
    monkeypatch.setattr(runner.api_client, "send_gif", runner._stub_api.send_gif)
    monkeypatch.setattr(runner, "_INTER_MSG_DELAY_S", 0)

    # Overall failure because not all sections sent.
    assert runner.run_job("j") is False
    # Only 2 successful sends (sections 1 and 3).
    assert runner._stub_api.send_html_message.call_count == 2
    record = json.loads(runner.LOG_FILE.read_text(encoding="utf-8").strip())
    assert record["messages_sent"] == 2
    assert record["messages_total"] == 3


# ---------------------------------------------------------------------------
# Name mention resolution (daily standups / planning briefs)
# ---------------------------------------------------------------------------


def test_inject_mentions_unresolved_name_stays_plain_bold(runner):
    """No saved profile (default stub) -> bold markdown unchanged, no mentions.

    (This test's `runner` fixture stubs api_client.md_to_html as identity, so
    unresolved bold spans surface here as raw "**...**" markdown, not
    "<strong>" — real markdown-to-HTML conversion happens downstream.)
    """
    text = "⚠️ Brak logu za poprzedni dzień: **Ola Nowicka**"
    html, mentions = runner._inject_name_mentions(text)
    assert "**Ola Nowicka**" in html
    assert mentions == []


def test_inject_mentions_resolved_name_becomes_chip(runner):
    runner._stub_user_memory.find_profile_by_name.side_effect = (
        lambda name: ("Ola Nowicka", "8:orgid:fake-mri") if name == "Ola Nowicka" else None
    )
    text = "⚠️ Brak logu za poprzedni dzień: **Ola Nowicka**"
    html, mentions = runner._inject_name_mentions(text)
    assert "**Ola Nowicka**" not in html
    assert "mri=\"8:orgid:fake-mri\"" in html
    assert len(mentions) == 1
    assert mentions[0]["mri"] == "8:orgid:fake-mri"


def test_inject_mentions_header_colon_preserved(runner):
    runner._stub_user_memory.find_profile_by_name.side_effect = (
        lambda name: ("Ola Nowicka", "8:orgid:fake-mri") if name == "Ola Nowicka" else None
    )
    text = "👤 **Ola Nowicka:**\n- [PRJ1-1](url) — coś"
    html, _ = runner._inject_name_mentions(text)
    first_line = html.split("\n")[0]
    assert "mri=\"8:orgid:fake-mri\"" in first_line
    assert first_line.endswith(":")


def test_inject_mentions_mixed_name_list_partial_resolution(runner):
    """{{CONTRIBUTORS}}-style comma list: one resolves, one doesn't."""
    runner._stub_user_memory.find_profile_by_name.side_effect = (
        lambda name: ("Ola Nowicka", "8:orgid:fake-mri") if name == "Ola Nowicka" else None
    )
    text = "Wielkie dzięki dla **Ola Nowicka, Ala Kowalska**!"
    html, mentions = runner._inject_name_mentions(text)
    assert "mri=\"8:orgid:fake-mri\"" in html
    assert "**Ala Kowalska**" in html
    assert len(mentions) == 1


def test_inject_mentions_non_name_bold_span_untouched(runner):
    """Bold text that isn't a 2-3 word title-case name is left completely alone."""
    text = "**Nieprzypisane:**\n**Ready for Planning — TeamB:**"
    html, mentions = runner._inject_name_mentions(text)
    assert "**Nieprzypisane:**" in html
    assert "**Ready for Planning — TeamB:**" in html
    assert mentions == []
    runner._stub_user_memory.find_profile_by_name.assert_not_called()


def test_send_one_passes_mentions_to_send_html_message(runner, monkeypatch):
    monkeypatch.setattr(runner.api_client, "send_html_message", runner._stub_api.send_html_message)
    monkeypatch.setattr(runner.api_client, "md_to_html", runner._stub_api.md_to_html)
    monkeypatch.setattr(runner.api_client, "build_mention_chip", runner._stub_api.build_mention_chip)
    runner._stub_user_memory.find_profile_by_name.side_effect = (
        lambda name: ("Ola Nowicka", "8:orgid:fake-mri") if name == "Ola Nowicka" else None
    )

    ok = runner._send_one("Brak logu: **Ola Nowicka**", "CHAN", 1, 1)
    assert ok is True
    call_args = runner._stub_api.send_html_message.call_args[0]
    html_arg, channel_arg, mentions_arg = call_args
    assert channel_arg == "CHAN"
    assert len(mentions_arg) == 1
    assert "mri=\"8:orgid:fake-mri\"" in html_arg


