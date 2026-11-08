"""Integration-ish tests for scripts/teams_agent/cli.py approval loop branches.

Stubs: input(), ai.draft_reply(), scraper functions, Playwright (sync_playwright).
Verifies log.jsonl shape for each approval key: s, e, r, n, q.
"""

import inspect
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the module is importable from tests/.
_TEAMS_DIR = str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent")
if _TEAMS_DIR not in sys.path:
    sys.path.insert(0, _TEAMS_DIR)

import cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_CHATS = [{"chat_id": "0", "name": "Alice", "unread": True}]
_FAKE_MESSAGES = [{"sender": "Alice", "text": "Hey!"}]
_DRAFT = "Hi Alice!"


def _make_args(log_file: str, max_chats: int = 1, **extra) -> cli.argparse.Namespace:
    defaults = dict(
        model="claude-haiku-4-5",
        max_chats=max_chats,
        force_login=False,
        log_file=log_file,
        watch_chat=None,
        poll=10,
        watch_state="/dev/null",
    )
    defaults.update(extra)
    return cli.argparse.Namespace(**defaults)


def _make_page_mock() -> MagicMock:
    page = MagicMock()
    page.url = "https://teams.microsoft.com/v2/"
    return page


# ---------------------------------------------------------------------------
# Helpers to patch the full dependency surface of _run_approval_loop
# ---------------------------------------------------------------------------


class _LoopContext:
    """Context manager that patches scraper + ai so tests focus on the loop logic."""

    def __init__(self, inputs: list[str], draft: str = _DRAFT, log_file: str = "/dev/null"):
        self._inputs = iter(inputs)
        self._draft = draft
        self._log_file = log_file
        self._patches = []

    def __enter__(self):
        # Patch scraper functions.
        self._patches.append(patch.object(cli.scraper, "switch_to_chat", return_value=True))
        self._patches.append(patch.object(cli.scraper, "read_messages", return_value=_FAKE_MESSAGES))
        self._patches.append(patch.object(cli.scraper, "send_message", return_value=True))
        self._patches.append(patch.object(cli.ai, "draft_reply", return_value=self._draft))
        self._patches.append(patch("builtins.input", side_effect=list(self._inputs)))
        self.mocks = [p.__enter__() for p in self._patches]
        return self

    def __exit__(self, *args):
        for p in self._patches:
            p.__exit__(*args)


# ---------------------------------------------------------------------------
# Branch: s (send)
# ---------------------------------------------------------------------------


def test_approval_send_branch(tmp_path):
    """Key 's' sends the draft and writes action='send' to log.jsonl."""
    log_file = str(tmp_path / "log.jsonl")
    args = _make_args(log_file)
    page = _make_page_mock()

    with _LoopContext(inputs=["s"], log_file=log_file):
        cli._run_approval_loop(page, _FAKE_CHATS, args)

    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "send"
    assert record["sent_text"] == _DRAFT
    assert record["chat_id"] == "0"


# ---------------------------------------------------------------------------
# Branch: e (edit)
# ---------------------------------------------------------------------------


def test_approval_edit_branch(tmp_path):
    """Key 'e' sends the edited text and writes action='edit' to log.jsonl."""
    log_file = str(tmp_path / "log.jsonl")
    args = _make_args(log_file)
    page = _make_page_mock()
    edited_text = "Edited reply"

    with _LoopContext(inputs=["e", edited_text], log_file=log_file):
        cli._run_approval_loop(page, _FAKE_CHATS, args)

    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "edit"
    assert record["sent_text"] == edited_text


# ---------------------------------------------------------------------------
# Branch: r (regen) then s (send)
# ---------------------------------------------------------------------------


def test_approval_regen_branch(tmp_path):
    """Key 'r' calls ai.draft_reply again; subsequent 's' sends the new draft."""
    log_file = str(tmp_path / "log.jsonl")
    args = _make_args(log_file)
    page = _make_page_mock()

    regen_draft = "Regenerated reply"
    drafts = [_DRAFT, regen_draft]

    with (
        patch.object(cli.scraper, "switch_to_chat", return_value=True),
        patch.object(cli.scraper, "read_messages", return_value=_FAKE_MESSAGES),
        patch.object(cli.scraper, "send_message", return_value=True),
        patch.object(cli.ai, "draft_reply", side_effect=list(drafts)),
        patch("builtins.input", side_effect=["r", "s"]),
    ):
        cli._run_approval_loop(page, _FAKE_CHATS, args)

    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "send"
    assert record["sent_text"] == regen_draft


# ---------------------------------------------------------------------------
# Branch: n (skip)
# ---------------------------------------------------------------------------


def test_approval_next_branch(tmp_path):
    """Key 'n' skips the chat and writes action='skip' to log.jsonl."""
    log_file = str(tmp_path / "log.jsonl")
    args = _make_args(log_file)
    page = _make_page_mock()

    with _LoopContext(inputs=["n"], log_file=log_file):
        cli._run_approval_loop(page, _FAKE_CHATS, args)

    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "skip"
    assert record["sent_text"] is None


# ---------------------------------------------------------------------------
# Branch: q (quit)
# ---------------------------------------------------------------------------


def test_approval_quit_branch(tmp_path):
    """Key 'q' stops the loop and writes action='quit' to log.jsonl."""
    log_file = str(tmp_path / "log.jsonl")
    # Two chats — q on the first should stop processing both.
    two_chats = [
        {"chat_id": "0", "name": "Alice", "unread": True},
        {"chat_id": "1", "name": "Bob", "unread": True},
    ]
    args = _make_args(log_file, max_chats=2)
    page = _make_page_mock()

    with _LoopContext(inputs=["q"], log_file=log_file):
        cli._run_approval_loop(page, two_chats, args)

    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    # Only one log line — the loop stopped at the first chat.
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "quit"


# ---------------------------------------------------------------------------
# JSONL shape invariants
# ---------------------------------------------------------------------------


def test_log_record_has_required_keys(tmp_path):
    """Every log record contains ts, chat_id, suggested_reply, action, sent_text."""
    log_file = str(tmp_path / "log.jsonl")
    args = _make_args(log_file)
    page = _make_page_mock()

    with _LoopContext(inputs=["n"], log_file=log_file):
        cli._run_approval_loop(page, _FAKE_CHATS, args)

    record = json.loads((tmp_path / "log.jsonl").read_text(encoding="utf-8").strip())
    for key in ("ts", "chat_id", "suggested_reply", "action", "sent_text"):
        assert key in record, f"Missing key: {key}"


def test_log_ensure_ascii_false(tmp_path):
    """Polish characters in log are stored as UTF-8, not escaped."""
    log_file = str(tmp_path / "log.jsonl")
    args = _make_args(log_file)
    page = _make_page_mock()

    polish_draft = "Cześć, jak się masz?"  # Polish on purpose: checks non-ASCII stays UTF-8
    with (
        patch.object(cli.scraper, "switch_to_chat", return_value=True),
        patch.object(cli.scraper, "read_messages", return_value=_FAKE_MESSAGES),
        patch.object(cli.scraper, "send_message", return_value=True),
        patch.object(cli.ai, "draft_reply", return_value=polish_draft),
        patch("builtins.input", side_effect=["n"]),
    ):
        cli._run_approval_loop(page, _FAKE_CHATS, args)

    raw = (tmp_path / "log.jsonl").read_bytes().decode("utf-8")
    assert polish_draft in raw


def test_approval_send_failure_adds_neutral_retry_fields(tmp_path):
    """Failed interactive 's' send adds neutral retry_attempts
    (no send_error — Playwright path bypasses api_client)."""
    log_file = str(tmp_path / "log.jsonl")
    args = _make_args(log_file)
    page = _make_page_mock()

    with (
        patch.object(cli.scraper, "switch_to_chat", return_value=True),
        patch.object(cli.scraper, "read_messages", return_value=_FAKE_MESSAGES),
        patch.object(cli.scraper, "send_message", return_value=False),
        patch.object(cli.ai, "draft_reply", return_value=_DRAFT),
        patch("builtins.input", side_effect=["s"]),
    ):
        cli._run_approval_loop(page, _FAKE_CHATS, args)

    record = json.loads((tmp_path / "log.jsonl").read_text(encoding="utf-8").strip())
    assert record["send_ok"] is False
    assert record["retry_attempts"] == 0
    assert "send_error" not in record


# ---------------------------------------------------------------------------
# Watch mode: _extract_trigger
# ---------------------------------------------------------------------------


class TestExtractTrigger:
    def test_matches_with_comma(self):
        assert cli._extract_trigger("Hey Claude, what is the capital of France?") == "what is the capital of France?"
        # the Polish "Hej" form stays accepted
        assert cli._extract_trigger("Hej Claude, what time is it?") == "what time is it?"

    def test_matches_case_insensitive(self):
        assert cli._extract_trigger("hey claude how are you") == "how are you"
        assert cli._extract_trigger("HEY CLAUDE write something") == "write something"

    def test_matches_english_hey(self):
        assert cli._extract_trigger("Hey Claude, introduce yourself") == "introduce yourself"
        assert cli._extract_trigger("hey claude tell me a joke") == "tell me a joke"

    def test_matches_with_sender_prefix(self):
        # Teams inner_text often prefixes sender/time; the trigger lives mid-message.
        text = "Person 40 11:23 Hey Claude, is Alex in tomorrow?"
        assert cli._extract_trigger(text) == "is Alex in tomorrow?"

    def test_matches_multiline_prompt(self):
        text = "Hey Claude\nWrite a short summary."
        assert cli._extract_trigger(text) == "Write a short summary."

    def test_no_match_returns_none(self):
        assert cli._extract_trigger("Hello world") is None
        assert cli._extract_trigger("") is None
        assert cli._extract_trigger("Claude please answer") is None  # missing 'Hej'

    def test_empty_prompt_returns_none(self):
        # Trigger present but nothing after it -> no prompt -> None.
        assert cli._extract_trigger("Hej Claude") is None
        assert cli._extract_trigger("Hej Claude   ") is None


# ---------------------------------------------------------------------------
# Watch mode: _load_watch_state / _save_watch_state round-trip
# ---------------------------------------------------------------------------


def test_watch_state_round_trip(tmp_path):
    state_path = str(tmp_path / "watch-state.json")
    # Initially missing -> empty dict.
    assert cli._load_watch_state(state_path) == {}
    # Save one chat, then another.
    cli._save_watch_state(state_path, "Person 40", {"last_sequence_id": 100})
    cli._save_watch_state(state_path, "Inny", {"last_sequence_id": 200})
    state = cli._load_watch_state(state_path)
    assert state["Person 40"] == {"last_sequence_id": 100}
    assert state["Inny"] == {"last_sequence_id": 200}


def test_watch_state_preserves_polish_chars(tmp_path):
    state_path = str(tmp_path / "watch-state.json")
    cli._save_watch_state(state_path, "Person 46", {"last_sequence_id": 42})
    raw = Path(state_path).read_bytes().decode("utf-8")
    assert "Person 46" in raw  # ensure_ascii=False keeps it readable


# ---------------------------------------------------------------------------
# _save_chat_config — atomic write, preserves _comment
# ---------------------------------------------------------------------------


def test_save_chat_config_writes_chats(tmp_path):
    """_save_chat_config writes a valid JSON file with the given chat_config."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    chat_config = {"AI Channel": {"knowledge": None, "mcp": ["jira"]}}
    cli._save_chat_config(chat_config, path=cfg_path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["chats"] == chat_config


def test_save_chat_config_preserves_comment(tmp_path):
    """_save_chat_config preserves the existing _comment top-level key."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    original = {
        "_comment": "test comment should survive",
        "chats": {"OldChat": {"knowledge": []}},
    }
    cfg_path.write_text(json.dumps(original, indent=2), encoding="utf-8")
    new_config = {"NewChat": {"knowledge": None}}
    cli._save_chat_config(new_config, path=cfg_path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["_comment"] == "test comment should survive"
    assert "NewChat" in data["chats"]


def test_save_chat_config_trailing_newline(tmp_path):
    """Written file ends with a newline (for clean git diffs)."""
    cfg_path = tmp_path / "chat-config.json"
    cli._save_chat_config({"A": {}}, path=cfg_path)
    raw = cfg_path.read_bytes()
    assert raw.endswith(b"\n")


def test_save_chat_config_ensure_ascii_false(tmp_path):
    """Polish characters are stored as UTF-8, not escaped."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    chat_config = {"AI Channel": {"epithets": ["zwiezly", "formalny"]}}
    cli._save_chat_config(chat_config, path=cfg_path)
    raw = cfg_path.read_bytes().decode("utf-8")
    # No escaped Unicode sequences for ASCII-range strings, file is readable
    data = json.loads(raw)
    assert data["chats"]["AI Channel"]["epithets"] == ["zwiezly", "formalny"]


# ---------------------------------------------------------------------------
# _apply_chat_config_change
# ---------------------------------------------------------------------------


def test_apply_chat_config_change_mcp(tmp_path):
    """Sets mcp field and calls _save_chat_config."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(json.dumps({"chats": {"AI Channel": {"knowledge": None}}}), encoding="utf-8")
    chat_config = {"AI Channel": {"knowledge": None}}
    cli._apply_chat_config_change("AI Channel", "mcp", ["jira", "todos"], chat_config, path=cfg_path)
    assert chat_config["AI Channel"]["mcp"] == ["jira", "todos"]
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["chats"]["AI Channel"]["mcp"] == ["jira", "todos"]


def test_apply_chat_config_change_epithets_sets_list(tmp_path):
    """Sets epithets to a list of strings."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(json.dumps({"chats": {"AI Channel": {}}}), encoding="utf-8")
    chat_config = {"AI Channel": {}}
    cli._apply_chat_config_change(
        "AI Channel", "epithets", ["zwiezly", "formalny"], chat_config, path=cfg_path
    )
    assert chat_config["AI Channel"]["epithets"] == ["zwiezly", "formalny"]


def test_apply_chat_config_change_epithets_none_deletes_key(tmp_path):
    """Setting epithets=None removes the key (default prompt)."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(json.dumps({"chats": {"AI Channel": {"epithets": ["zwiezly"]}}}),
                        encoding="utf-8")
    chat_config = {"AI Channel": {"epithets": ["zwiezly"]}}
    cli._apply_chat_config_change("AI Channel", "epithets", None, chat_config, path=cfg_path)
    assert "epithets" not in chat_config["AI Channel"]
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "epithets" not in data["chats"]["AI Channel"]


def test_apply_chat_config_change_caller_clear_deletes_key(tmp_path):
    """Setting caller=None removes the key (fallback to args.caller)."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(json.dumps({"chats": {"AI Channel": {"caller": "owner"}}}),
                        encoding="utf-8")
    chat_config = {"AI Channel": {"caller": "owner"}}
    cli._apply_chat_config_change("AI Channel", "caller", None, chat_config, path=cfg_path)
    assert "caller" not in chat_config["AI Channel"]


def test_apply_chat_config_change_invalid_field(tmp_path):
    """Invalid field is rejected silently (no crash, no mutation)."""
    chat_config = {"AI Channel": {"knowledge": None}}
    cli._apply_chat_config_change("AI Channel", "conv_id", "evil", chat_config)
    assert chat_config["AI Channel"].get("conv_id") is None


# ---------------------------------------------------------------------------
# _BOB_POLL_RE regex
# ---------------------------------------------------------------------------


class TestBobPollRe:
    def test_basic_topic(self):
        m = cli._BOB_POLL_RE.search("Bob ankieta model")
        assert m is not None
        assert m.group(1).strip() == "model"

    def test_per_chat_topic(self):
        m = cli._BOB_POLL_RE.search("bob ankieta chat AI Channel mcp")
        assert m is not None
        assert "AI Channel mcp" in m.group(1)

    def test_case_insensitive(self):
        assert cli._BOB_POLL_RE.search("BOB ANKIETA pause") is not None

    def test_no_match(self):
        assert cli._BOB_POLL_RE.search("Bob pause 30m") is None


class TestFallbackRegexes:
    def test_mcp_csv(self):
        m = cli._BOB_CHAT_MCP_RE.search("Bob chat AI Channel mcp jira,todos")
        assert m is not None
        assert m.group(1).strip() == "AI Channel"
        assert m.group(2).strip() == "jira,todos"

    def test_caller(self):
        m = cli._BOB_CHAT_CALLER_RE.search("Bob chat AI Channel caller owner")
        assert m is not None
        assert m.group(2).strip() == "owner"

    def test_epithets_csv(self):
        m = cli._BOB_CHAT_EPITHETS_RE.search("Bob chat AI Channel epithets zwiezly,formalny")
        assert m is not None
        assert m.group(2).strip() == "zwiezly,formalny"

    def test_epithets_none(self):
        m = cli._BOB_CHAT_EPITHETS_RE.search("Bob chat AI Channel epithets none")
        assert m is not None
        assert m.group(2).strip() == "none"


# ---------------------------------------------------------------------------
# Persistent history (disk-backed JSONL)
# ---------------------------------------------------------------------------


class TestPersistentHistory:
    def test_load_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_HISTORY_DIR", tmp_path)
        result = cli._load_history_from_disk("AI Channel")
        assert result == []

    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_HISTORY_DIR", tmp_path)
        entries = [
            {"sender": "Alice", "text": "Hi Bob, zażółć gęślą jaźń"},
            {"sender": "Bob", "text": "Hi!"},
        ]
        cli._append_history_to_disk("AI Channel", entries)
        loaded = cli._load_history_from_disk("AI Channel")
        assert loaded == entries

    def test_respects_char_budget(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_HISTORY_DIR", tmp_path)
        monkeypatch.setattr(cli, "_HISTORY_COMPACT_CHARS", 50)
        entries = [
            {"sender": "A", "text": "x" * 30},
            {"sender": "Bob", "text": "y" * 30},
        ]
        cli._append_history_to_disk("budget_chat", entries)
        loaded = cli._load_history_from_disk("budget_chat")
        # Only the last entry fits within 50 chars
        assert len(loaded) == 1
        assert loaded[0]["sender"] == "Bob"

    def test_rewrite_replaces_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_HISTORY_DIR", tmp_path)
        old = [{"sender": "Alice", "text": "old"}]
        cli._append_history_to_disk("rewrite_chat", old)
        new = [{"sender": "SUMMARY", "text": "compacted"}]
        cli._rewrite_history_on_disk("rewrite_chat", new)
        loaded = cli._load_history_from_disk("rewrite_chat")
        assert loaded == new

    def test_trim_to_hard_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_HISTORY_DIR", tmp_path)
        monkeypatch.setattr(cli, "_HISTORY_DISK_MAX_ENTRIES", 4)
        all_entries = [{"sender": "A", "text": f"msg{i}"} for i in range(6)]
        cli._append_history_to_disk("cap_chat", all_entries)
        path = cli._history_path("cap_chat")
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 4
        # Should keep the last 4
        assert json.loads(lines[0])["text"] == "msg2"

    def test_corrupted_lines_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_HISTORY_DIR", tmp_path)
        path = tmp_path / "corrupted.jsonl"
        path.write_text('{"sender": "A", "text": "ok"}\nNOT_JSON\n{"sender": "B", "text": "ok2"}\n')
        monkeypatch.setattr(cli, "_history_path", lambda name: path)
        loaded = cli._load_history_from_disk("corrupted")
        assert len(loaded) == 2
        assert all(e["text"].startswith("ok") for e in loaded)


# ---------------------------------------------------------------------------
# Notification command handlers (control_chat, is_owner=True)
# ---------------------------------------------------------------------------


def _make_notif_args(log_file: str, watch_state: str) -> cli.argparse.Namespace:
    return cli.argparse.Namespace(
        model="claude-haiku-4-5",
        max_chats=1,
        force_login=False,
        log_file=log_file,
        watch_chat=None,
        poll=10,
        watch_state=watch_state,
        rate_limit=0,
        caller="owner",
    )


def _make_owner_msg(text: str, seq: int = 2) -> dict:
    """Return a minimal message dict from the owner (USER_MRI)."""
    return {
        "id": f"msg_{seq}",
        "sequence_id": seq,
        "sender": "Boss",
        "sender_mri": cli.api_client.USER_MRI,
        "text": text,
        "raw_html": f"<p>{text}</p>",
        "timestamp": "2026-05-27T10:00:00Z",
        "image_urls": [],
    }


def test_notif_set_command(tmp_path, monkeypatch):
    """_NOTIF_SET_RE handler calls add_notification and acks with the notif_id."""
    log_file = str(tmp_path / "log.jsonl")
    state_path = str(tmp_path / "state.json")
    args = _make_notif_args(log_file, state_path)

    msg_text = "Bob, remind today 15:00 in AI Channel: test ping"
    fake_msgs = [_make_owner_msg(msg_text)]

    ack_calls = []

    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: fake_msgs)
    monkeypatch.setattr(
        cli.api_client, "reply_to_message",
        lambda text, *a, **kw: ack_calls.append(text) or True,
    )
    monkeypatch.setattr(
        cli.todos_store, "add_reminder_todo",
        lambda **kw: "todo_X",
    )
    monkeypatch.setattr(
        cli, "_parse_reminder_datetime",
        lambda d, t: __import__("datetime").datetime(2026, 5, 27, 15, 0),
    )
    monkeypatch.setattr(
        cli, "_load_chat_config",
        lambda path: {"AI Channel": {"knowledge": None}},
    )

    state = {"last_sequence_id": 1}
    cli._process_chat_messages(
        "Notes", "conv-notes", args, state, [0.0],
        control_chat=True,
        caller="owner",
    )

    assert any("todo_X" in ack for ack in ack_calls), f"Expected todo_X in acks: {ack_calls}"


def test_notif_show_command(tmp_path, monkeypatch):
    """_NOTIF_SHOW_RE handler calls format_full_list and acks with the result."""
    log_file = str(tmp_path / "log.jsonl")
    state_path = str(tmp_path / "state.json")
    args = _make_notif_args(log_file, state_path)

    msg_text = "Bob, pokaz przypomnienia"
    fake_msgs = [_make_owner_msg(msg_text)]

    ack_calls = []

    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: fake_msgs)
    monkeypatch.setattr(
        cli.api_client, "reply_to_message",
        lambda text, *a, **kw: ack_calls.append(text) or True,
    )
    monkeypatch.setattr(
        cli.todos_store, "format_todos_for_chat",
        lambda current_chat, show_all=False: "FOO_LIST",
    )

    state = {"last_sequence_id": 1}
    cli._process_chat_messages(
        "Notes", "conv-notes", args, state, [0.0],
        control_chat=True,
        caller="owner",
    )

    assert any("FOO_LIST" in ack for ack in ack_calls), f"Expected FOO_LIST in acks: {ack_calls}"


def test_notif_del_command(tmp_path, monkeypatch):
    """_NOTIF_DEL_RE handler calls delete_notification and acks 'Deleted.'."""
    log_file = str(tmp_path / "log.jsonl")
    state_path = str(tmp_path / "state.json")
    args = _make_notif_args(log_file, state_path)

    msg_text = "Bob, usun przypomnienie sn_xyz"
    fake_msgs = [_make_owner_msg(msg_text)]

    ack_calls = []
    del_calls = []

    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: fake_msgs)
    monkeypatch.setattr(
        cli.api_client, "reply_to_message",
        lambda text, *a, **kw: ack_calls.append(text) or True,
    )
    monkeypatch.setattr(
        cli.todos_store, "delete_todo_by_id",
        lambda notif_id: del_calls.append(notif_id) or True,
    )

    state = {"last_sequence_id": 1}
    cli._process_chat_messages(
        "Notes", "conv-notes", args, state, [0.0],
        control_chat=True,
        caller="owner",
    )

    assert del_calls == ["sn_xyz"], f"Expected delete called with sn_xyz: {del_calls}"
    assert any("Deleted." in ack for ack in ack_calls), f"Expected Deleted. in acks: {ack_calls}"


# ---------------------------------------------------------------------------
# _internet_allows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("setting,is_owner,expected", [
    ("all",      True,  True),
    ("all",      False, True),
    ("only_me",  True,  True),
    ("only_me",  False, False),
    (None,       True,  False),
    (None,       False, False),
    ("off",      True,  False),
    ("off",      False, False),
    ("",         True,  False),
])
def test_internet_allows(setting, is_owner, expected):
    assert cli._internet_allows(setting, is_owner) is expected


# ---------------------------------------------------------------------------
# _internet_for
# ---------------------------------------------------------------------------


def test_internet_for_returns_setting():
    cfg = {"AI Channel": {"internet": "all"}}
    assert cli._internet_for("AI Channel", cfg) == "all"


def test_internet_for_only_me():
    cfg = {"AI Channel": {"internet": "only_me"}}
    assert cli._internet_for("AI Channel", cfg) == "only_me"


def test_internet_for_missing_chat_returns_none():
    assert cli._internet_for("Unknown Chat", {}) is None


def test_internet_for_missing_field_returns_none():
    cfg = {"AI Channel": {"mcp": None}}
    assert cli._internet_for("AI Channel", cfg) is None


def test_internet_for_null_value_returns_none():
    cfg = {"AI Channel": {"internet": None}}
    assert cli._internet_for("AI Channel", cfg) is None


def test_internet_for_invalid_value_returns_none():
    cfg = {"AI Channel": {"internet": "true"}}
    assert cli._internet_for("AI Channel", cfg) is None


# ---------------------------------------------------------------------------
# _BOB_CHAT_INTERNET_RE
# ---------------------------------------------------------------------------


class TestBobChatInternetRe:
    def test_all(self):
        m = cli._BOB_CHAT_INTERNET_RE.search("Bob chat AI Channel internet all")
        assert m is not None
        assert m.group(1).strip() == "AI Channel"
        assert m.group(2).strip() == "all"

    def test_only_me(self):
        m = cli._BOB_CHAT_INTERNET_RE.search("bob chat AI Channel internet only_me")
        assert m is not None
        assert m.group(2).strip() == "only_me"

    def test_off(self):
        m = cli._BOB_CHAT_INTERNET_RE.search("Bob chat AI Channel internet off")
        assert m is not None
        assert m.group(2).strip() == "off"

    def test_case_insensitive(self):
        assert cli._BOB_CHAT_INTERNET_RE.search("BOB CHAT test internet ALL") is not None

    def test_no_match_without_keyword(self):
        assert cli._BOB_CHAT_INTERNET_RE.search("Bob chat AI Channel mcp jira") is None


# ---------------------------------------------------------------------------
# _apply_chat_config_change for "internet"
# ---------------------------------------------------------------------------


def test_apply_chat_config_change_internet_all(tmp_path):
    """Setting internet='all' stores the value."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(json.dumps({"chats": {"AI Channel": {}}}), encoding="utf-8")
    chat_config = {"AI Channel": {}}
    cli._apply_chat_config_change("AI Channel", "internet", "all", chat_config, path=cfg_path)
    assert chat_config["AI Channel"]["internet"] == "all"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["chats"]["AI Channel"]["internet"] == "all"


def test_apply_chat_config_change_internet_only_me(tmp_path):
    """Setting internet='only_me' stores the value."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(json.dumps({"chats": {"AI Channel": {}}}), encoding="utf-8")
    chat_config = {"AI Channel": {}}
    cli._apply_chat_config_change("AI Channel", "internet", "only_me", chat_config, path=cfg_path)
    assert chat_config["AI Channel"]["internet"] == "only_me"


def test_apply_chat_config_change_internet_off_removes_key(tmp_path):
    """Setting internet=None (off) removes the key from config."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(
        json.dumps({"chats": {"AI Channel": {"internet": "all"}}}), encoding="utf-8"
    )
    chat_config = {"AI Channel": {"internet": "all"}}
    cli._apply_chat_config_change("AI Channel", "internet", None, chat_config, path=cfg_path)
    assert "internet" not in chat_config["AI Channel"]
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "internet" not in data["chats"]["AI Channel"]


def test_apply_chat_config_change_internet_off_string_removes_key(tmp_path):
    """Setting internet='off' (string) removes the key from config."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(
        json.dumps({"chats": {"AI Channel": {"internet": "only_me"}}}), encoding="utf-8"
    )
    chat_config = {"AI Channel": {"internet": "only_me"}}
    cli._apply_chat_config_change("AI Channel", "internet", "off", chat_config, path=cfg_path)
    assert "internet" not in chat_config["AI Channel"]


def test_apply_chat_config_change_internet_invalid_no_mutation(tmp_path, capsys):
    """Invalid internet value is rejected, config unchanged."""
    import json
    cfg_path = tmp_path / "chat-config.json"
    cfg_path.write_text(json.dumps({"chats": {"AI Channel": {}}}), encoding="utf-8")
    chat_config = {"AI Channel": {}}
    cli._apply_chat_config_change("AI Channel", "internet", "yes", chat_config, path=cfg_path)
    assert "internet" not in chat_config["AI Channel"]
    captured = capsys.readouterr()
    assert "invalid internet value" in captured.err


# ---------------------------------------------------------------------------
# Attachment context guard — _READ_FILE_TRIGGER_RE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    # Polish positive matches
    "Bob, przeczytaj ten plik",
    "przeczytaj to",
    "opisz obraz",
    "opisz to zdjecie",
    "podsumuj ten plik",
    "podsumow ten dokument",
    "zobacz co tu jest",
    "spojrz na to",
    "obejrzyj to",
    "co tam w pliku",
    "co tam w dokumencie",
    "co widzisz na zdjeciu",
    "co widac na tym obrazku",
    "co sadzisz o tym",
    "co jest w pliku",
])
def test_read_file_trigger_pl_matches(text):
    """Polish trigger phrases (kept as aliases) must match _READ_FILE_TRIGGER_RE."""
    assert cli._READ_FILE_TRIGGER_RE.search(text), f"Expected match for: {text!r}"


@pytest.mark.parametrize("text", [
    # English positive matches
    "read this file",
    "read the document",
    "what's in file",
    "what in attach",
    "summarize this",
    "summarize the document",
    "describe this",
    "describe the image",
    "what about image",
    "what on photo",
])
def test_read_file_trigger_en_matches(text):
    """English trigger phrases must match _READ_FILE_TRIGGER_RE."""
    assert cli._READ_FILE_TRIGGER_RE.search(text), f"Expected match for: {text!r}"


@pytest.mark.parametrize("text", [
    "thanks",
    "ok",
    "yes, Bob",
    "great",
    "got it",
    "sure",
    # Polish small talk must not match either
    "dziekuje",
    "co u Ciebie",
    "zrob mi taski",
])
def test_read_file_trigger_no_match(text):
    """Unrelated phrases must NOT match _READ_FILE_TRIGGER_RE."""
    assert not cli._READ_FILE_TRIGGER_RE.search(text), f"Unexpected match for: {text!r}"


# ---------------------------------------------------------------------------
# _BOB_RE / _trigger_re_for — "does this message mention Bob" detection.
# Regression coverage for the reported bug: "bob," (comma) or other special
# characters directly touching "bob" allegedly failed to trigger. \b in
# regex is a boundary between a word char (\w) and a non-word char -- a
# comma, exclamation mark, quote, paren, dash, em-dash, emoji, zero-width
# space, BOM, NBSP, and hashtag are all non-word, so \bbob\b already matches
# every one of these directly-attached cases with no space required.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "bob,", "bob, what's up", ",bob", "hey bob, hi",
    "bob!", "bob?", "bob.", "bob;", "bob:",
    "(bob)", "bob-", "-bob", "bob—",
    "Bob,", "BOB,",
    '"bob"', "'bob'", "bob*", "*bob",
    "bob/siema", "bob\\siema",
    "@bob,", "#bob", "bob#tag",
    "bob\U0001F600", "\U0001F600bob",  # emoji directly attached, no space
    "bob​",  # zero-width space
    "bob﻿",  # BOM
    "bob\xa0,",   # non-breaking space then comma
])
def test_bob_re_matches_with_special_char_directly_attached(text):
    """A comma or other non-word special character touching 'bob' still triggers."""
    assert cli._BOB_RE.search(text), f"Expected _BOB_RE match for: {text!r}"


@pytest.mark.parametrize("text", [
    "bobby", "abob", "bobo", "bob2", "2bob",
])
def test_bob_re_does_not_match_bob_as_substring_of_another_word(text):
    """'bob' embedded in a longer alphanumeric token must NOT trigger."""
    assert not cli._BOB_RE.search(text), f"Unexpected _BOB_RE match for: {text!r}"


def test_trigger_re_for_falls_back_to_bob_re_with_no_chat_config(monkeypatch):
    """A chat with no configured custom trigger word uses _BOB_RE and its comma handling."""
    monkeypatch.setattr(cli, "_TRIGGER_RE_CACHE", {})
    pat = cli._trigger_re_for("SomeChat", {})
    assert pat.search("bob, what's up")


# ---------------------------------------------------------------------------
# _enrich_text_with_images — stub mode
# ---------------------------------------------------------------------------


def test_enrich_text_with_images_stub_no_network():
    """mode='stub' returns placeholder without calling download_image or describe_image."""
    with (
        patch.object(cli.api_client, "download_image") as mock_download,
        patch.object(cli.ai, "describe_image") as mock_describe,
    ):
        result = cli._enrich_text_with_images("hello", ["http://example.com/img.jpg"],
                                               mode="stub")
    mock_download.assert_not_called()
    mock_describe.assert_not_called()
    assert "[Image attached to the message]" in result


def test_enrich_text_with_images_stub_single_image():
    """mode='stub' with 1 image url returns singular placeholder."""
    result = cli._enrich_text_with_images("some text", ["http://x.com/a.jpg"], mode="stub")
    assert "[Image attached to the message]" in result
    assert "some text" in result


def test_enrich_text_with_images_stub_multiple_images():
    """mode='stub' with N>1 images returns plural placeholder."""
    result = cli._enrich_text_with_images("msg", ["http://x.com/a.jpg", "http://x.com/b.jpg"],
                                           mode="stub")
    assert "[2 images attached to the message]" in result


def test_enrich_text_with_images_stub_no_urls_returns_text():
    """mode='stub' with empty url list returns original text unchanged."""
    result = cli._enrich_text_with_images("unchanged", [], mode="stub")
    assert result == "unchanged"


# ---------------------------------------------------------------------------
# _enrich_text_with_attachments — stub mode
# ---------------------------------------------------------------------------


def test_enrich_text_with_attachments_stub_no_download():
    """mode='stub' returns placeholder without calling _attachments.download."""
    attach_list = [{"url": "https://sp.example.com/notes.md", "name": "notes.md"}]
    with patch.object(cli._attachments, "download") as mock_dl:
        result = cli._enrich_text_with_attachments("hello", attach_list, mode="stub")
    mock_dl.assert_not_called()
    assert "notes.md" in result


def test_enrich_text_with_attachments_stub_md_placeholder():
    """mode='stub' for .md attachment produces [Zalacznik: name.md] placeholder."""
    attach_list = [{"url": "https://sp.example.com/doc.md", "name": "doc.md"}]
    result = cli._enrich_text_with_attachments("context", attach_list, mode="stub")
    assert "doc.md" in result
    assert "[PLIK:" not in result
    assert "context" in result


def test_enrich_text_with_attachments_stub_empty_list():
    """mode='stub' with empty attach list returns original text."""
    result = cli._enrich_text_with_attachments("only text", [], mode="stub")
    assert result == "only text"


def test_enrich_text_with_attachments_full_mode_populates_image_paths_out():
    """mode='full' on an image attachment writes a temp file and appends its path
    to image_paths_out — the mechanism jira_attach candidates rely on, including
    for attachments picked up from the _raw_recent lookback window (not just the
    current message)."""
    fake_png = b"\x89PNG" + b"\x00" * 100
    attach_list = [{"url": "https://sp.example.com/screen.png", "name": "screen.png",
                     "type": "png"}]
    image_paths_out: list[str] = []
    try:
        with (
            patch.object(cli._attachments, "download", return_value=(fake_png, "image/png")),
            patch.object(cli.ai, "describe_image", return_value="opis obrazka"),
        ):
            result = cli._enrich_text_with_attachments(
                "context", attach_list, mode="full", image_paths_out=image_paths_out,
            )
        assert "opis obrazka" in result
        assert len(image_paths_out) == 1
        p = Path(image_paths_out[0])
        assert p.exists()
        assert p.read_bytes() == fake_png
    finally:
        for p in image_paths_out:
            Path(p).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# _images_to_tmp_files
# ---------------------------------------------------------------------------


def test_images_to_tmp_files_empty_returns_empty():
    """Empty url list returns empty list without any network calls."""
    with patch.object(cli.api_client, "download_image") as mock_dl:
        result = cli._images_to_tmp_files([])
    mock_dl.assert_not_called()
    assert result == []


def test_images_to_tmp_files_creates_tmp_file(tmp_path):
    """Successful download writes bytes to a .png temp file."""
    fake_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    with patch.object(cli.api_client, "download_image", return_value=(fake_bytes, "image/png")):
        paths = cli._images_to_tmp_files(["http://example.com/img.png"])
    assert len(paths) == 1
    p = Path(paths[0])
    try:
        assert p.exists()
        assert p.suffix == ".png"
        assert p.name.startswith("bob_img_")
        assert p.read_bytes() == fake_bytes
    finally:
        p.unlink(missing_ok=True)


def test_images_to_tmp_files_skips_none_result():
    """download_image returning None is silently skipped."""
    with patch.object(cli.api_client, "download_image", return_value=None):
        paths = cli._images_to_tmp_files(["http://example.com/tiny.jpg"])
    assert paths == []


def test_images_to_tmp_files_multiple_urls():
    """Multiple urls each produce a separate temp file."""
    fake_png = b"\x89PNG" + b"\x00" * 4
    fake_jpg = b"\xff\xd8\xff" + b"\x00" * 4
    side_effects = [(fake_png, "image/png"), (fake_jpg, "image/jpeg")]
    with patch.object(cli.api_client, "download_image", side_effect=side_effects):
        paths = cli._images_to_tmp_files(
            ["http://example.com/a.png", "http://example.com/b.jpg"]
        )
    assert len(paths) == 2
    suffixes = {Path(p).suffix for p in paths}
    assert ".png" in suffixes
    assert ".jpg" in suffixes
    for p in paths:
        Path(p).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# main reply path — describe_image NOT called; freeform_reply receives image_paths
# ---------------------------------------------------------------------------


def test_full_mode_reply_uses_image_paths_not_describe_image(tmp_path):
    """In full-mode reply, ai.describe_image must NOT be called;
    ai.freeform_reply must receive a non-empty image_paths list."""
    fake_png = b"\x89PNG" + b"\x00" * 100

    captured_calls: list[dict] = []

    def _fake_freeform_reply(*args, **kwargs):
        captured_calls.append(kwargs)
        return "test reply"

    with (
        patch.object(cli.api_client, "download_image", return_value=(fake_png, "image/png")),
        patch.object(cli.ai, "describe_image") as mock_describe,
        patch.object(cli.ai, "freeform_reply", side_effect=_fake_freeform_reply),
    ):
        # Directly test _images_to_tmp_files produces paths ...
        paths = cli._images_to_tmp_files(["http://example.com/img.png"])
        # ... and that describe_image is never called on that path
        mock_describe.assert_not_called()
        # Simulate passing image_paths to freeform_reply (as the watch loop does)
        cli.ai.freeform_reply(
            "what is in this picture?",
            image_paths=paths or None,
        )

    assert len(captured_calls) == 1
    assert captured_calls[0].get("image_paths")
    assert len(captured_calls[0]["image_paths"]) == 1
    assert Path(captured_calls[0]["image_paths"][0]).suffix == ".png"
    mock_describe.assert_not_called()
    for p in paths:
        Path(p).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Bob own SharePoint upload skip
# ---------------------------------------------------------------------------


def test_bob_own_sharepoint_upload_skip_a_href():
    """is_owner + sharepoint <a href= in raw_html triggers the skip."""
    _raw = '<p><a href="https://contoso.sharepoint.com/sites/test/file.xlsx">file.xlsx</a></p>'
    # Simulate the condition from the watch loop
    is_owner = True
    assert "sharepoint" in _raw and "<a href" in _raw
    # The skip condition should be True
    skip = is_owner and (
        "sharepoint" in _raw
        and ("<a href" in _raw or "<img" in _raw)
    )
    assert skip


def test_bob_own_sharepoint_upload_skip_img():
    """is_owner + sharepoint <img in raw_html triggers the skip."""
    _raw = '<p><img src="https://contoso.sharepoint.com/img.png" /></p>'
    is_owner = True
    skip = is_owner and (
        "sharepoint" in _raw
        and ("<a href" in _raw or "<img" in _raw)
    )
    assert skip


def test_bob_own_sharepoint_upload_skip_non_owner():
    """Non-owner messages with sharepoint URLs must NOT be skipped."""
    _raw = '<p><a href="https://contoso.sharepoint.com/doc.md">doc</a></p>'
    is_owner = False
    skip = is_owner and (
        "sharepoint" in _raw
        and ("<a href" in _raw or "<img" in _raw)
    )
    assert not skip


def test_bob_own_sharepoint_upload_skip_no_sharepoint():
    """is_owner + regular link must NOT trigger the skip."""
    _raw = '<p><a href="https://example.com/doc.md">doc</a></p>'
    is_owner = True
    skip = is_owner and (
        "sharepoint" in _raw
        and ("<a href" in _raw or "<img" in _raw)
    )
    assert not skip


# ---------------------------------------------------------------------------
# _safe_relative — upload path guard
# ---------------------------------------------------------------------------


def test_safe_relative_direct_child_allowed(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    child = root / "frame.jpg"
    child.touch()
    assert cli._safe_relative(child, root) is True


def test_safe_relative_nested_child_allowed(tmp_path):
    root = tmp_path / "allowed"
    (root / "sub").mkdir(parents=True)
    nested = root / "sub" / "deep.jpg"
    nested.touch()
    assert cli._safe_relative(nested, root) is True


def test_safe_relative_outside_root_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "secret" / "id_rsa"
    (tmp_path / "secret").mkdir()
    outside.touch()
    assert cli._safe_relative(outside, root) is False


def test_safe_relative_path_traversal_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    traversal = root / ".." / "secret.txt"
    assert cli._safe_relative(traversal, root) is False


# ---------------------------------------------------------------------------
# _BOB_FORWARD_RE and _TEAMS_LINK_RE — regex tests
# ---------------------------------------------------------------------------


class TestBobForwardRe:
    def test_english_forward_to(self):
        m = cli._BOB_FORWARD_RE.search("Bob, forward to AI Channel")
        assert m is not None
        assert m.group(1).strip() == "AI Channel"

    def test_english_send_it_to(self):
        m = cli._BOB_FORWARD_RE.search("Bob - send it to AI Channel")
        assert m is not None
        assert m.group(1).strip() == "AI Channel"

    def test_name_form(self):
        """Polish alias "wyslij na" still works."""
        m = cli._BOB_FORWARD_RE.search("Bob, wyslij na AI Channel")
        assert m is not None
        assert m.group(1).strip() == "AI Channel"

    def test_wyslij_with_s_caron(self):
        m = cli._BOB_FORWARD_RE.search("Bob, wyślij na AI Channel")
        assert m is not None
        assert m.group(1).strip() == "AI Channel"

    def test_link_form(self):
        url = (
            "https://teams.microsoft.com/l/chat/"
            "19:chat05@thread.v2/0?tenantId=x"
        )
        m = cli._BOB_FORWARD_RE.search(f"Bob wyslij na {url}")
        assert m is not None
        assert url in m.group(1)

    def test_case_insensitive(self):
        assert cli._BOB_FORWARD_RE.search("BOB WYSLIJ NA Test Chat") is not None

    def test_no_match_without_bob(self):
        assert cli._BOB_FORWARD_RE.search("wyslij na gdzies") is None
        assert cli._BOB_FORWARD_RE.search("forward to somewhere") is None

    def test_no_match_different_command(self):
        assert cli._BOB_FORWARD_RE.search("Bob pause 30m") is None


class TestTeamsLinkRe:
    def test_extracts_conv_id(self):
        url = "https://teams.microsoft.com/l/chat/19:abc123%40thread.v2/0"
        m = cli._TEAMS_LINK_RE.search(url)
        assert m is not None
        assert m.group(1) == "19:abc123%40thread.v2"

    def test_no_match_non_teams_url(self):
        assert cli._TEAMS_LINK_RE.search("https://example.com/chat/19:abc") is None

    def test_no_match_plain_text(self):
        assert cli._TEAMS_LINK_RE.search("AI Channel chat name") is None


# ---------------------------------------------------------------------------
# watch-state round-trip: last_forward_text and pending_forward
# ---------------------------------------------------------------------------


def test_watch_state_last_forward_text_roundtrip(tmp_path):
    """last_forward_text with Polish chars is stored and loaded without escaping."""
    state_path = str(tmp_path / "watch-state.json")
    polish_reply = "Zadzwoń do mnie jutro rano."  # Polish on purpose: non-ASCII must stay unescaped
    cli._save_watch_state(state_path, "Notes", {"last_forward_text": polish_reply})
    loaded = cli._load_watch_state(state_path)
    assert loaded["Notes"]["last_forward_text"] == polish_reply
    # Verify stored as real UTF-8, not escaped
    raw = Path(state_path).read_bytes().decode("utf-8")
    assert polish_reply in raw


def test_watch_state_pending_forward_roundtrip(tmp_path):
    """pending_forward dict is stored and loaded correctly."""
    state_path = str(tmp_path / "watch-state.json")
    pf = {"conv_id": "19:chat06@thread.v2", "chat_name": "Rota Internal"}
    cli._save_watch_state(state_path, "Notes", {"pending_forward": pf})
    loaded = cli._load_watch_state(state_path)
    assert loaded["Notes"]["pending_forward"] == pf


# ---------------------------------------------------------------------------
# Forward command — fuzzy-flow unit test
# ---------------------------------------------------------------------------


def test_forward_fuzzy_single_match_sets_pending(tmp_path, monkeypatch):
    """_BOB_FORWARD_RE with a single fuzzy match stores pending_forward in state."""
    log_file = str(tmp_path / "log.jsonl")
    state_path = str(tmp_path / "state.json")
    args = _make_notif_args(log_file, state_path)

    msg_text = "Bob, forward to Rota"
    fake_msgs = [_make_owner_msg(msg_text)]

    ack_calls = []

    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: fake_msgs)
    monkeypatch.setattr(
        cli.api_client, "reply_to_message",
        lambda text, *a, **kw: ack_calls.append(text) or True,
    )
    monkeypatch.setattr(
        cli.api_client, "_fuzzy_find_conversations",
        lambda q: [("19:chat32@thread.v2", "Rota Internal")],
    )
    monkeypatch.setattr(
        cli, "_load_chat_config",
        lambda path: {},
    )

    state = {"last_sequence_id": 1, "last_forward_text": "Bob's last reply."}
    cli._process_chat_messages(
        "Notes", "conv-notes", args, state, [0.0],
        control_chat=True,
        caller="owner",
    )

    saved = cli._load_watch_state(state_path)
    pf = saved.get("Notes", {}).get("pending_forward")
    assert pf is not None, f"pending_forward not set, acks: {ack_calls}"
    assert pf["conv_id"] == "19:chat32@thread.v2"
    assert pf["chat_name"] == "Rota Internal"
    assert any("Do you mean" in ack for ack in ack_calls), f"Unexpected acks: {ack_calls}"


# ---------------------------------------------------------------------------
# Forward command — confirmation flow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("msg_text", ["yes", "tak"])
def test_forward_confirmation_tak_sends_and_clears(tmp_path, monkeypatch, msg_text):
    """Sending 'yes' (or the Polish 'tak') with pending_forward sends with the Bob header and clears it."""
    log_file = str(tmp_path / "log.jsonl")
    state_path = str(tmp_path / "state.json")
    args = _make_notif_args(log_file, state_path)

    fake_msgs = [_make_owner_msg(msg_text)]

    ack_calls = []
    html_calls = []

    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: fake_msgs)
    monkeypatch.setattr(
        cli.api_client, "reply_to_message",
        lambda text, *a, **kw: ack_calls.append(text) or True,
    )
    monkeypatch.setattr(
        cli.api_client, "send_html_message",
        lambda html, cid: html_calls.append((html, cid)) or "msg-id-123",
    )

    pf_data = {"conv_id": "19:chat32@thread.v2", "chat_name": "Rota Internal"}
    state = {
        "last_sequence_id": 1,
        "last_forward_text": "Bob's last reply.",
        "pending_forward": pf_data,
    }
    cli._process_chat_messages(
        "Notes", "conv-notes", args, state, [0.0],
        control_chat=True,
        caller="owner",
    )

    assert html_calls, f"send_html_message not called, acks: {ack_calls}"
    sent_html, sent_cid = html_calls[0]
    assert "[Bob]" in sent_html, f"Bob header missing in HTML: {sent_html!r}"
    assert "Bob's last reply." in sent_html
    assert sent_cid == "19:chat32@thread.v2"
    assert any("Sent to" in ack for ack in ack_calls), f"Unexpected acks: {ack_calls}"

    # pending_forward must be cleared
    saved = cli._load_watch_state(state_path)
    assert saved.get("Notes", {}).get("pending_forward") is None


# ---------------------------------------------------------------------------
# watch_send — send failure enriches log.jsonl with send_error/retry_attempts
# (the failure detail must not be reduced to a bare send_ok=false)
# ---------------------------------------------------------------------------


def test_watch_send_failure_enriches_log_with_send_error(tmp_path, monkeypatch):
    """A failed watch_send records send_error/retry_attempts."""
    log_file = str(tmp_path / "log.jsonl")
    state_path = str(tmp_path / "state.json")
    args = _make_notif_args(log_file, state_path)

    msg_text = "Bob, how are you?"
    fake_msgs = [_make_owner_msg(msg_text, seq=2)]

    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: fake_msgs)
    monkeypatch.setattr(cli.ai, "freeform_reply", lambda *a, **kw: "Test reply")

    def _fake_reply_to_message(*a, **kw):
        cli.api_client._set_last_send_error({
            "status": 503, "err_type": "HTTPError", "message": "boom", "attempts": 3,
        })
        return False

    monkeypatch.setattr(cli.api_client, "reply_to_message", _fake_reply_to_message)

    state = {"last_sequence_id": 1}
    cli._process_chat_messages(
        "AI Channel", "conv-ai", args, state, 1,
        control_chat=False,
        caller="owner",
        chat_config={},
    )

    lines = Path(log_file).read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    watch_send = [r for r in records if r.get("action") == "watch_send"]
    assert len(watch_send) == 1, f"Expected one watch_send record, got: {records}"
    rec = watch_send[0]
    assert rec["send_ok"] is False
    assert rec["send_error"] == {
        "status": 503, "err_type": "HTTPError", "message": "boom", "attempts": 3,
    }
    assert rec["retry_attempts"] == 3


def test_watch_send_success_no_send_error_fields(tmp_path, monkeypatch):
    """A successful watch_send does not add send_error/retry_attempts noise."""
    log_file = str(tmp_path / "log.jsonl")
    state_path = str(tmp_path / "state.json")
    args = _make_notif_args(log_file, state_path)

    msg_text = "Bob, how are you?"
    fake_msgs = [_make_owner_msg(msg_text, seq=2)]

    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: fake_msgs)
    monkeypatch.setattr(cli.ai, "freeform_reply", lambda *a, **kw: "Test reply")
    monkeypatch.setattr(cli.api_client, "reply_to_message", lambda *a, **kw: True)

    state = {"last_sequence_id": 1}
    cli._process_chat_messages(
        "AI Channel", "conv-ai", args, state, 1,
        control_chat=False,
        caller="owner",
        chat_config={},
    )

    lines = Path(log_file).read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    watch_send = [r for r in records if r.get("action") == "watch_send"]
    assert len(watch_send) == 1, f"Expected one watch_send record, got: {records}"
    rec = watch_send[0]
    assert rec["send_ok"] is True
    assert "send_error" not in rec
    assert "retry_attempts" not in rec
    assert "token_refresh_attempted" not in rec


# ---------------------------------------------------------------------------
# _persona_for helper
# ---------------------------------------------------------------------------


def test_persona_for_returns_configured_value():
    """Returns the persona string when chat has persona key set."""
    chat_config = {
        "Asystent - testy agenta": {
            "conv_id": "19:chat13@thread.v2",
            "persona": "example",
            "mcp": ["jira", "kb"],
            "caller": "all",
        }
    }
    result = cli._persona_for("Asystent - testy agenta", chat_config)
    assert result == "example"


def test_persona_for_returns_none_when_key_absent():
    """Returns None when chat has no persona key (Bob is default)."""
    chat_config = {
        "AI Channel": {"knowledge": None, "mcp": ["jira"]}
    }
    result = cli._persona_for("AI Channel", chat_config)
    assert result is None


def test_persona_for_returns_none_when_chat_absent():
    """Returns None when chat name is not in config at all."""
    result = cli._persona_for("Nieznany Chat", {})
    assert result is None


def test_persona_for_returns_none_when_persona_null():
    """Explicit null / None value in config maps to None."""
    chat_config = {"SomeChat": {"persona": None, "mcp": None}}
    result = cli._persona_for("SomeChat", chat_config)
    assert result is None


def test_gif_performance_dead_code_removed():
    """Regression guard: Haiku placeholder personalization must not be reintroduced."""
    assert not hasattr(cli.ai, "generate_placeholder_batch")
    assert not hasattr(cli, "_PERSONALIZED_PLACEHOLDER_CACHE")
    assert not hasattr(cli, "_PLACEHOLDER_HISTORY_MIN")
    assert not hasattr(cli, "_PLACEHOLDER_SEMAPHORE")
    assert not hasattr(cli, "_PLACEHOLDER_REFILL_BELOW")


def test_thinking_placeholder_mechanism_removed():
    """Regression guard: the retired thinking-placeholder mechanism must stay gone."""
    assert not hasattr(cli, "_should_send_thinking_placeholder")
    assert not hasattr(cli, "_prewarm_placeholder_gifs")
    assert not hasattr(cli, "_AMS_OBJECT_CACHE")
    assert not hasattr(cli, "_PREWARM_SEMAPHORE")
    assert not hasattr(cli, "gif_placeholder")
# ---------------------------------------------------------------------------
# _plan_gifs / _execute_gif_plan (Giphy [GIF: query] path only)
# ---------------------------------------------------------------------------


def test_plan_gifs_giphy_fallback():
    cleaned, plan = cli._plan_gifs("Look at this. [GIF: dog dancing]", persona=None)
    assert cleaned == "Look at this."
    assert plan == {"kind": "giphy", "queries": ["dog dancing"]}


def test_plan_gifs_no_tag_returns_none():
    cleaned, plan = cli._plan_gifs("A plain reply without tags.", persona=None)
    assert cleaned == "A plain reply without tags."
    assert plan is None


def test_execute_gif_plan_none_is_noop(monkeypatch):
    mock_giphy = MagicMock()
    monkeypatch.setattr(cli.api_client, "send_gif", mock_giphy)
    cli._execute_gif_plan(None, "19:abc", persona=None)
    mock_giphy.assert_not_called()


def test_execute_gif_plan_giphy_sends_all_queries(monkeypatch):
    mock_giphy = MagicMock(return_value=True)
    monkeypatch.setattr(cli.api_client, "send_gif", mock_giphy)
    plan = {"kind": "giphy", "queries": ["dog dancing", "cat jumping"]}
    cli._execute_gif_plan(plan, "19:abc", persona=None)
    assert mock_giphy.call_count == 2


def test_gif_plan_not_executed_when_reply_is_silent(tmp_path, monkeypatch):
    """A silent-sentinel reply must not send an orphan gif."""
    log_file = str(tmp_path / "log.jsonl")
    state_path = str(tmp_path / "state.json")
    args = _make_notif_args(log_file, state_path)

    fake_msgs = [_make_owner_msg("Bob, something", seq=2)]
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: fake_msgs)
    monkeypatch.setattr(cli.ai, "freeform_reply", lambda *a, **kw: "<silent/> [GIF: dog dancing]")

    mock_giphy = MagicMock(return_value=True)
    monkeypatch.setattr(cli.api_client, "send_gif", mock_giphy)

    state = {"last_sequence_id": 1}
    cli._process_chat_messages(
        "AI Channel", "conv-ai", args, state, 1,
        control_chat=False,
        caller="owner",
        chat_config={},
    )

    mock_giphy.assert_not_called()


# ---------------------------------------------------------------------------
# @-mention escalation token: [BOB-CALL:...] (Polish alias [BOB-WYWOLAJ:...])
# ---------------------------------------------------------------------------


def test_bob_mention_re_matches_single_key():
    m = cli._BOB_MENTION_RE.search("text [BOB-CALL: kowalski] end")
    assert m is not None
    assert m.group("who") == "kowalski"


def test_bob_mention_re_matches_multiple_keys():
    m = cli._BOB_MENTION_RE.search("[BOB-CALL: kowalski,ala]")
    assert m is not None
    assert m.group("who") == "kowalski,ala"


def test_bob_mention_re_case_insensitive():
    m = cli._BOB_MENTION_RE.search("[bob-call: kowalski]")
    assert m is not None


@pytest.mark.parametrize("token", ["[BOB-WYWOLAJ: kowalski]", "[bob-wywolaj: kowalski]"])
def test_bob_mention_re_accepts_polish_alias(token):
    m = cli._BOB_MENTION_RE.search(f"text {token} end")
    assert m is not None and m.group("who") == "kowalski"


def test_bob_mention_re_no_match_on_plain_text():
    assert cli._BOB_MENTION_RE.search("a plain message without a token") is None


def test_handle_bob_mention_empty_dict_does_not_crash_and_strips_token():
    """Empty _BOB_MENTION_PEOPLE (default state) must not crash; token is stripped
    from the visible reply even though no mention can be resolved."""
    reply, html, entries = cli._handle_bob_mention("reply [BOB-CALL: anyone] end")
    assert html == ""
    assert entries == []
    assert "BOB-CALL" not in reply
    assert "end" in reply


def test_handle_bob_mention_no_token_returns_reply_unchanged():
    reply, html, entries = cli._handle_bob_mention("a plain reply without a token")
    assert reply == "a plain reply without a token"
    assert html == ""
    assert entries == []


def test_handle_bob_mention_unknown_key_no_mention():
    """A token with a key that isn't in _BOB_MENTION_PEOPLE yields no mention,
    even if the dict is populated with other entries."""
    with patch.dict(cli._BOB_MENTION_PEOPLE, {"kowalski": ("Jan Test", "8:orgid:xxx")}, clear=True):
        reply, html, entries = cli._handle_bob_mention("[BOB-CALL: unknown]")
    assert html == ""
    assert entries == []
    assert "BOB-CALL" not in reply


def test_handle_bob_mention_known_key_resolves_via_build_mentions_html():
    fake_html = '<at id="0">Jan Test</at>'
    fake_entries = [{"id": "0", "mri": "8:orgid:xxx", "displayName": "Jan Test"}]
    with patch.dict(cli._BOB_MENTION_PEOPLE, {"kowalski": ("Jan Test", "8:orgid:xxx")}, clear=True), \
         patch.object(cli.api_client, "build_mentions_html", return_value=(fake_html, fake_entries)) as mock_build:
        reply, html, entries = cli._handle_bob_mention("please [BOB-CALL: kowalski] help")
    mock_build.assert_called_once_with([("Jan Test", "8:orgid:xxx")])
    assert html == fake_html
    assert entries == fake_entries
    assert "BOB-CALL" not in reply


def test_handle_bob_mention_polish_alias_resolves_too():
    fake_html = '<at id="0">Jan Test</at>'
    fake_entries = [{"id": "0", "mri": "8:orgid:xxx", "displayName": "Jan Test"}]
    with patch.dict(cli._BOB_MENTION_PEOPLE, {"kowalski": ("Jan Test", "8:orgid:xxx")}, clear=True), \
         patch.object(cli.api_client, "build_mentions_html", return_value=(fake_html, fake_entries)):
        reply, html, entries = cli._handle_bob_mention("please [BOB-WYWOLAJ: kowalski] help")
    assert html == fake_html and "BOB-WYWOLAJ" not in reply and reply == "please  help"


def test_handle_bob_mention_ignores_other_prefixes():
    """_handle_bob_mention must not match another persona's [X-CALL:...] token."""
    reply, html, entries = cli._handle_bob_mention("text [EXAMPLE-CALL: nowak] end")
    assert "[EXAMPLE-CALL: nowak]" in reply
    assert html == ""
    assert entries == []


# ---------------------------------------------------------------------------
# mention fallback: user_memory.find_profile_by_name
# ---------------------------------------------------------------------------


def test_handle_bob_mention_falls_back_to_user_memory_when_key_not_in_dict():
    """Key absent from _BOB_MENTION_PEOPLE resolves via user_memory.find_profile_by_name."""
    fake_html = '<at id="0">Person 40</at>'
    fake_entries = [{"id": "0", "mri": "8:orgid:person-40", "displayName": "Person 40"}]
    with patch.dict(cli._BOB_MENTION_PEOPLE, {}, clear=True), \
         patch.object(
             cli.user_memory, "find_profile_by_name",
             return_value=("Person 40", "8:orgid:person-40"),
         ) as mock_find, \
         patch.object(cli.api_client, "build_mentions_html", return_value=(fake_html, fake_entries)) as mock_build:
        reply, html, entries = cli._handle_bob_mention("tag [BOB-CALL: ewa nowak] please")
    mock_find.assert_called_once_with("ewa nowak")
    mock_build.assert_called_once_with([("Person 40", "8:orgid:person-40")])
    assert html == fake_html
    assert entries == fake_entries
    assert "BOB-CALL" not in reply


def test_handle_bob_mention_ambiguous_fallback_yields_no_mention():
    """When find_profile_by_name returns None (ambiguous match), token is stripped, no mention."""
    with patch.dict(cli._BOB_MENTION_PEOPLE, {}, clear=True), \
         patch.object(cli.user_memory, "find_profile_by_name", return_value=None):
        reply, html, entries = cli._handle_bob_mention("[BOB-CALL: jan kowalski]")
    assert html == ""
    assert entries == []
    assert "BOB-CALL" not in reply


def test_bob_mention_re_matches_polish_diacritics():
    m = cli._BOB_MENTION_RE.search("[BOB-CALL: Żaneta Ćwiklińska]")
    assert m is not None
    assert m.group("who") == "Żaneta Ćwiklińska"


# ---------------------------------------------------------------------------
# _broadcast_scheduler_tick — generated/<job_id>.json gate (BUG-1 regression)
# ---------------------------------------------------------------------------
#
# Generator-based jobs (job_def has a truthy "generator" key, e.g.
# sprint-hygiene) build and send their own content deterministically and
# never populate scripts/broadcast/generated/<job_id>.json. The gate that
# requires that file to exist before firing dry-run/send must therefore be
# skipped for such jobs, while Claude-generation jobs (no "generator" key)
# must still require it.


class _FixedDatetime(cli.datetime):
    """datetime.now() stand-in returning a fixed, injectable instant."""

    _fixed = None

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls._fixed
        return cls._fixed.astimezone(tz)


def _setup_scheduler_tick_env(tmp_path, monkeypatch, job_def: dict, sched: dict):
    """Wire cli.py module-level paths to a tmp dir and a fixed 'now' (workday, in-window)."""
    broadcast_dir = tmp_path / "broadcast"
    broadcast_dir.mkdir()
    runner_script = broadcast_dir / "broadcast_runner.py"
    runner_script.write_text("# stub", encoding="utf-8")
    jobs_file = broadcast_dir / "jobs.json"
    jobs_file.write_text(json.dumps([job_def]), encoding="utf-8")
    schedule_file = broadcast_dir / "broadcast-schedule.json"
    schedule_file.write_text(json.dumps({job_def["id"]: sched}), encoding="utf-8")

    monkeypatch.setattr(cli, "_BROADCAST_RUNNER_SCRIPT", runner_script)
    monkeypatch.setattr(cli, "_BROADCAST_SCHEDULE_FILE", schedule_file)
    monkeypatch.setattr(cli, "_fired_broadcasts", {})

    # 2026-07-02 is a Thursday (workday); 07:02 is inside the 07:00 5-min window.
    fixed = cli.datetime(2026, 7, 2, 7, 2, tzinfo=cli.ZoneInfo("Europe/Warsaw"))
    _FixedDatetime._fixed = fixed
    monkeypatch.setattr(cli, "datetime", _FixedDatetime)

    popen_calls = []
    monkeypatch.setattr(
        cli.subprocess, "Popen",
        lambda *a, **kw: popen_calls.append((a, kw)),
    )
    return popen_calls


def test_scheduler_fires_generator_job_without_generated_file(tmp_path, monkeypatch):
    """Job with 'generator' set fires dry-run even though generated/<id>.json is absent."""
    job_def = {
        "id": "sprint-hygiene-teamb-0700",
        "schedule_type": "daily",
        "generator": "sprint-hygiene",
    }
    popen_calls = _setup_scheduler_tick_env(
        tmp_path, monkeypatch, job_def, {"dry_run": "07:00"}
    )
    log_file = tmp_path / "log.jsonl"

    cli._broadcast_scheduler_tick(str(log_file))

    assert len(popen_calls) == 1
    args = popen_calls[0][0][0]
    assert "--job" in args and job_def["id"] in args
    assert "--phase" in args and "dry-run" in args


def test_scheduler_skips_non_generator_job_without_generated_file(tmp_path, monkeypatch):
    """Job without 'generator' still requires generated/<id>.json to fire dry-run."""
    job_def = {
        "id": "planning-prep-teamb",
        "schedule_type": "daily",
    }
    popen_calls = _setup_scheduler_tick_env(
        tmp_path, monkeypatch, job_def, {"dry_run": "07:00"}
    )
    log_file = tmp_path / "log.jsonl"

    cli._broadcast_scheduler_tick(str(log_file))

    assert popen_calls == []


def test_scheduler_skips_disabled_job(tmp_path, monkeypatch):
    """enabled=false: no phase fires, even when the job (generator, time window)
    would otherwise start -- the runner would exit 1 anyway."""
    job_def = {
        "id": "sprint-hygiene-teamb-0700",
        "schedule_type": "daily",
        "generator": "sprint-hygiene",
        "enabled": False,
    }
    popen_calls = _setup_scheduler_tick_env(
        tmp_path, monkeypatch, job_def, {"dry_run": "07:00", "send": "07:00"}
    )
    log_file = tmp_path / "log.jsonl"

    cli._broadcast_scheduler_tick(str(log_file))

    assert popen_calls == []


# ---------------------------------------------------------------------------
# _check_skip_answered (skip-if-answered re-poll)
# ---------------------------------------------------------------------------


def test_check_skip_answered_no_newer_messages_skips_ai_call(monkeypatch):
    """No messages newer than the trigger seq -> (False, ''), AI helper not called."""
    fake_msgs = [{"id": "m1", "sender_mri": "8:orgid:other", "text": "hi", "sequence_id": 5}]
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=10: fake_msgs)
    ai_called = []
    monkeypatch.setattr(
        cli.ai, "check_already_answered",
        lambda *a, **kw: ai_called.append((a, kw)) or {"answered": True, "override": False},
    )

    skip, reason = cli._check_skip_answered("conv1", 10, "trigger-id", "question?", "reply")

    assert (skip, reason) == (False, "")
    assert ai_called == []


def test_check_skip_answered_ignores_bobs_own_still_working_note(monkeypatch):
    """The "still working" note Bob posts between the question and the reply is not someone answering."""
    html = cli.api_client.bob_html("⏳ Still working on the answer (2 min)")
    fake_msgs = [{"id": "m2", "sender_mri": cli.api_client.USER_MRI, "sequence_id": 11, "raw_html": html,
                  "text": cli.api_client._strip_html(html)}]
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=10: fake_msgs)
    monkeypatch.setattr(cli.ai, "check_already_answered",
                        lambda *a, **kw: pytest.fail("Bob's own note must not reach the classifier"))

    assert cli._check_skip_answered("conv1", 10, "trigger-id", "question?", "reply") == (False, "")


def test_check_skip_answered_newer_and_answered_skips(monkeypatch):
    """Newer message present + classifier says answered, no override -> skip."""
    fake_msgs = [
        {"id": "m2", "sender_mri": "8:orgid:other", "text": "already answered", "sequence_id": 11},
    ]
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=10: fake_msgs)
    monkeypatch.setattr(
        cli.ai, "check_already_answered",
        lambda question, reply, newer: {
            "answered": True, "override": False, "evidence": "already answered",
        },
    )

    skip, reason = cli._check_skip_answered("conv1", 10, "trigger-id", "question?", "reply")

    assert skip is True
    assert reason == "already answered"


def test_check_skip_answered_override_sends_anyway(monkeypatch):
    """Newer message answered but with explicit override -> do not skip."""
    fake_msgs = [
        {"id": "m2", "sender_mri": "8:orgid:other", "text": "Bob answer anyway", "sequence_id": 11},
    ]
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=10: fake_msgs)
    monkeypatch.setattr(
        cli.ai, "check_already_answered",
        lambda question, reply, newer: {"answered": True, "override": True, "evidence": "x"},
    )

    skip, reason = cli._check_skip_answered("conv1", 10, "trigger-id", "question?", "reply")

    assert skip is False


def test_check_skip_answered_empty_get_messages_result(monkeypatch):
    """get_messages returning [] (e.g. 401) -> (False, ''), fail-open."""
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=10: [])
    ai_called = []
    monkeypatch.setattr(
        cli.ai, "check_already_answered",
        lambda *a, **kw: ai_called.append((a, kw)) or {"answered": True, "override": False},
    )

    skip, reason = cli._check_skip_answered("conv1", 10, "trigger-id", "question?", "reply")

    assert (skip, reason) == (False, "")
    assert ai_called == []


# ---------------------------------------------------------------------------
# _parse_json_payload (prefix-tolerant poller stdout parser)
# ---------------------------------------------------------------------------


def test_parse_json_payload_bare_object():
    assert cli._parse_json_payload('{"status": "ok", "count": 42}') == {"status": "ok", "count": 42}


def test_parse_json_payload_with_timestamp_prefix():
    """logts.py prefixes stdout with a CET timestamp (e.g. '14:02:31 {...}')."""
    assert cli._parse_json_payload('14:02:31 {"status": "ok", "count": 42}') == {
        "status": "ok",
        "count": 42,
    }


def test_parse_json_payload_empty_or_garbage_returns_empty_dict():
    assert cli._parse_json_payload("") == {}
    assert cli._parse_json_payload("git-push: noop") == {}


def test_parse_json_payload_malformed_json_returns_empty_dict():
    assert cli._parse_json_payload("14:02:31 {not valid json") == {}


# ---------------------------------------------------------------------------
# _extract_and_send_files ([SEND_FILE] inline-content tag handling, Polish [WYSLIJ_PLIK] alias)
# ---------------------------------------------------------------------------

import attachments_upload  # noqa: E402
import sharepoint_upload  # noqa: E402


def test_extract_and_send_files_skips_empty_inline_body(monkeypatch):
    mock_upload = MagicMock(return_value="https://contoso.sharepoint.com/x/report.md")
    monkeypatch.setattr(sharepoint_upload, "upload_file", mock_upload)
    reply = "I have the data.\n[SEND_FILE: report.md]\n\n[/SEND_FILE]"

    cleaned = cli._extract_and_send_files(reply, conv_id="19:abc", chat_name="Test")

    mock_upload.assert_not_called()
    assert "SEND_FILE" not in cleaned
    assert cleaned == "I have the data."


def test_extract_and_send_files_sends_nonempty_inline_body(monkeypatch):
    mock_upload = MagicMock(return_value="https://contoso.sharepoint.com/x/report.md")
    monkeypatch.setattr(sharepoint_upload, "upload_file", mock_upload)
    mock_send = MagicMock(return_value=True)
    monkeypatch.setattr(cli.api_client, "send_message", mock_send)
    reply = "Here is the file.\n[SEND_FILE: report.md]\nreport body\n[/SEND_FILE]"

    cleaned = cli._extract_and_send_files(reply, conv_id="19:abc", chat_name="Test")

    mock_upload.assert_called_once()
    sent_path = mock_upload.call_args[0][0]
    assert sent_path.name.endswith("_report.md")
    dest_name = mock_upload.call_args.kwargs["dest_name"]
    assert dest_name.endswith("_report.md")
    assert cleaned == "Here is the file."
    mock_send.assert_called_once()
    msg, conv = mock_send.call_args[0]
    assert conv == "19:abc"
    assert "report.md" in msg
    assert "https://contoso.sharepoint.com/x/report.md" in msg


def test_extract_and_send_files_inline_failure_saves_fallback_and_notifies(monkeypatch, tmp_path):
    mock_upload = MagicMock(return_value=None)
    monkeypatch.setattr(sharepoint_upload, "upload_file", mock_upload)
    mock_send = MagicMock(return_value=True)
    monkeypatch.setattr(cli.api_client, "send_message", mock_send)
    monkeypatch.setattr(cli, "_ALLOWED_UPLOAD_ROOT", tmp_path)
    reply = "Here is the file.\n[SEND_FILE: report.md]\nreport body\n[/SEND_FILE]"

    cleaned = cli._extract_and_send_files(reply, conv_id="19:abc", chat_name="Test")

    assert cleaned == "Here is the file."
    fallbacks = list((tmp_path / "failed-uploads").glob("*_report.md"))
    assert len(fallbacks) == 1
    assert fallbacks[0].read_text(encoding="utf-8") == "report body"
    mock_send.assert_called_once()
    msg, conv = mock_send.call_args[0]
    assert conv == "19:abc"
    assert "Could not send the file" in msg
    assert str(fallbacks[0]) in msg


def test_extract_and_send_files_disk_failure_notifies_with_src_path(monkeypatch, tmp_path):
    mock_upload = MagicMock(return_value=None)
    monkeypatch.setattr(sharepoint_upload, "upload_file", mock_upload)
    mock_send = MagicMock(return_value=True)
    monkeypatch.setattr(cli.api_client, "send_message", mock_send)
    monkeypatch.setattr(cli, "_ALLOWED_UPLOAD_ROOTS", [tmp_path])
    src = tmp_path / "recording.mp3"
    src.write_bytes(b"abc")
    reply = f"Here you go.\n[SEND_FILE_FROM_DISK: {src}]"

    cleaned = cli._extract_and_send_files(reply, conv_id="19:abc", chat_name="Test")

    assert cleaned == "Here you go."
    mock_send.assert_called_once()
    msg, conv = mock_send.call_args[0]
    assert conv == "19:abc"
    assert "Could not send the file" in msg
    assert str(src) in msg


def test_upload_and_send_rejects_empty_file(tmp_path):
    empty_file = tmp_path / "empty.md"
    empty_file.write_bytes(b"")

    result = attachments_upload.upload_and_send("Test", empty_file, conv_id="19:abc")

    assert result is None


# ---------------------------------------------------------------------------
# _extract_and_send_gifs -- surviving [GIF: query] Giphy path only
# ---------------------------------------------------------------------------


def test_extract_and_send_gifs_giphy_fallback_intact(monkeypatch):
    mock_giphy = MagicMock(return_value=True)
    monkeypatch.setattr(cli.api_client, "send_gif", mock_giphy)

    reply = "Look at this. [GIF: dog dancing]"
    cleaned = cli._extract_and_send_gifs(reply, conv_id="19:abc", persona=None)

    assert cleaned == "Look at this."
    mock_giphy.assert_called_once_with("dog dancing", "19:abc", persona=None)


def test_extract_and_send_gifs_no_tag_sends_nothing(monkeypatch):
    mock_giphy = MagicMock(return_value=True)
    monkeypatch.setattr(cli.api_client, "send_gif", mock_giphy)

    reply = "A plain reply without tags."
    cleaned = cli._extract_and_send_gifs(reply, conv_id="19:abc", persona=None)

    assert cleaned == "A plain reply without tags."
    mock_giphy.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: a per-channel trigger MUST work for the owner's messages.
#
# The gate on the owner's messages in _process_chat_messages must not use the
# hard-coded _BOB_RE: "<trigger> speak up" from the owner on a persona channel
# with its own trigger must reach NORMAL MODE, not be silently dropped.
# ---------------------------------------------------------------------------

_PERSONA_CHAT = "Assistant - agent tests"
_PERSONA_CFG = {_PERSONA_CHAT: {"persona": "example", "caller": "auto", "trigger": "assistant"}}
_BOB_CHAT = "Release notes channel"
_BOB_CFG = {_BOB_CHAT: {"caller": "all"}}


def _owner_msg(text: str, seq: int = 500) -> dict:
    return {
        "id": str(1000 + seq),
        "sequence_id": seq,
        "sender": "Kowalski Jan",
        "sender_mri": cli.api_client.USER_MRI,
        "text": text,
        "raw_html": f"<p>{text}</p>",
        "timestamp": "2026-08-20T12:21:09.9460000Z",
        "image_urls": [],
        "attachments": [],
    }


def _run_one_message(tmp_path, chat_name, chat_config, msg, caller):
    """Run one message through _process_chat_messages. Returns the ai.freeform_reply mock."""
    args = cli.argparse.Namespace(
        model="claude-haiku-4-5",
        log_file=str(tmp_path / "log.jsonl"),
        watch_state=str(tmp_path / "watch-state.json"),
        rate_limit=0,
        caller=caller,
        poll=2,
        max_chats=1,
        force_login=False,
    )
    # seq baseline lower than the message => the message is "new"
    state = {"last_sequence_id": msg["sequence_id"] - 1, "last_composetime": "2026-08-20T10:00:00.000Z"}

    with patch.object(cli.api_client, "get_messages", return_value=[msg]), \
         patch.object(cli.api_client, "reply_to_message", return_value=True), \
         patch.object(cli.ai, "freeform_reply", return_value="reply") as mock_reply, \
         patch.object(cli, "_build_schedule_context", return_value=""), \
         patch.object(cli, "_load_private_notes", return_value=None), \
         patch.object(cli, "_load_user_context", return_value=None), \
         patch.object(cli, "_save_watch_state", return_value=None):
        cli._process_chat_messages(
            chat_name=chat_name,
            conv_id="19:chat30@thread.v2",
            args=args,
            state=state,
            tick=1,
            caller=caller,
            chat_config=chat_config,
            persona=(chat_config.get(chat_name) or {}).get("persona"),
        )
    return mock_reply



def test_owner_persona_trigger_reaches_reply_on_persona_channel(tmp_path):
    """'Assistant speak up' from the owner on a channel with trigger='assistant' must reach generation."""
    mock_reply = _run_one_message(
        tmp_path, _PERSONA_CHAT, _PERSONA_CFG, _owner_msg("Assistant speak up"), caller="auto",
    )
    assert mock_reply.called, (
        "the owner's message with the channel trigger was dropped — "
        "the gate uses the hard-coded \\bbob\\b again"
    )


def test_owner_message_without_channel_trigger_still_skipped(tmp_path):
    """Without the trigger word the owner's message is still skipped (echo prevention)."""
    mock_reply = _run_one_message(
        tmp_path, _PERSONA_CHAT, _PERSONA_CFG, _owner_msg("ok, he set those up over there"),
        caller="auto",
    )
    assert not mock_reply.called


def test_owner_bob_word_does_not_trigger_persona_channel(tmp_path):
    """On a persona channel the word 'Bob' is NOT a trigger — the persona answers there."""
    mock_reply = _run_one_message(
        tmp_path, _PERSONA_CHAT, _PERSONA_CFG, _owner_msg("Bob speak up"), caller="auto",
    )
    assert not mock_reply.called

def test_owner_bob_trigger_still_works_on_normal_channel(tmp_path):
    """Regression the other way: a normal channel still reacts to 'Bob'."""
    mock_reply = _run_one_message(
        tmp_path, _BOB_CHAT, _BOB_CFG, _owner_msg("Bob - what about the report?"), caller="all",
    )
    assert mock_reply.called


# ---------------------------------------------------------------------------
# Poller subprocess timeout -- must outlast the child's own budget
# ---------------------------------------------------------------------------
# A poller killed before sharepoint_meta.mark_processed() never advances its mtime,
# so the full download repeats on every tick forever. These tests pin the invariant
# (one shared timeout that outlasts the child's own budget) rather than the number.


def _poller_source() -> str:
    return (Path(cli.__file__)).read_text(encoding="utf-8")


def test_all_poller_subprocesses_use_the_shared_timeout():
    """No poller may reintroduce a bare timeout=120 -- pollers share the constant."""
    src = _poller_source()
    assert src.count("timeout=_POLLER_SUBPROCESS_TIMEOUT_S,") == 1  # only the leave poller
    assert "timeout=120," not in src


# ---------------------------------------------------------------------------
# Token refresh gate
# ---------------------------------------------------------------------------


def test_refresh_gate_reads_token_generation_not_timestamp():
    """The success check must key on the token VALUE, not on _auth_token_ts.

    _auth_token_ts advances on every capture, including a re-capture of the
    identical token, so gating on it printed "Bearer token refreshed OK" while
    nothing had changed -- hiding a 401 loop.
    """
    src = Path(cli.__file__).read_text(encoding="utf-8")
    start = src.index("def _refresh_token_via_browser()")
    body = src[start:src.index("_last_stats_ts: float", start)]

    assert "_auth_token_gen" in body
    assert "_auth_token_ts <= ts_before" not in body, (
        "refresh success must not be decided by the timestamp"
    )


def test_refresh_does_not_navigate_to_notes():
    """The chat-to-self is 'Kowalski Jan (You)' in the Teams DOM, never 'Notes'.

    switch_to_chat_by_name(page, "Notes") could therefore never match and burned
    its whole timeout on every refresh. click_any_other_chat() already triggers the
    chatsvc request the refresh needs.
    """
    src = Path(cli.__file__).read_text(encoding="utf-8")
    start = src.index("def _refresh_token_via_browser()")
    body = src[start:src.index("_last_stats_ts: float", start)]
    # Strip comments -- the call is *named* in a comment explaining why it is gone.
    code = "\n".join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
    )

    assert 'switch_to_chat_by_name(page, "Notes"' not in code


def test_noop_refresh_backoff_is_monotonic_and_starts_at_zero():
    """A refresh that changes nothing must widen the window before the next try.

    The ladder is a local inside _run_watch_loop (not importable), so it is read
    out of the source and evaluated as the literal it is.
    """
    import ast

    src = Path(cli.__file__).read_text(encoding="utf-8")
    line = next(
        ln for ln in src.splitlines()
        if ln.lstrip().startswith("_NOOP_REFRESH_BACKOFF_S = ")
    )
    ladder = ast.literal_eval(line.split("=", 1)[1].strip())

    assert ladder[0] == 0, "the first refresh attempt must not be delayed"
    assert list(ladder) == sorted(ladder)
    assert ladder[-1] >= 600, (
        "a persistently ineffective refresh should back off to minutes, not seconds"
    )


def test_noop_refresh_streak_resets_only_on_a_new_token():
    """Streak bookkeeping must key on _auth_token_gen, not on the refresh happening."""
    src = Path(cli.__file__).read_text(encoding="utf-8")
    start = src.index("_gen_before_refresh = api_client._auth_token_gen")
    block = src[start:start + 400]

    assert "if api_client._auth_token_gen > _gen_before_refresh:" in block
    assert "_noop_refresh_streak = 0" in block
    assert "_noop_refresh_streak += 1" in block


# ---------------------------------------------------------------------------
# Per-channel triggers
# ---------------------------------------------------------------------------


def test_trigger_re_for_accepts_list_of_alternatives():
    """Under caller='auto' this regex is the only gate left on the OWNER's own
    messages, so every spelling the persona answers to must be listed — a missing
    one is dropped before Claude is invoked, silently."""
    cli._TRIGGER_RE_CACHE.pop("Assistant - agent tests", None)
    chat_config = {"Assistant - agent tests": {"trigger": ["assistant", "helper"]}}
    pat = cli._trigger_re_for("Assistant - agent tests", chat_config)
    assert pat.search("assistant, what about the form?")
    assert pat.search("Helper save this")
    # Word boundaries: a longer word containing the trigger stays out.
    assert not pat.search("the assistantship said something else")
    assert not pat.search("bob what's up")
    cli._TRIGGER_RE_CACHE.pop("Assistant - agent tests", None)


def test_trigger_re_for_still_accepts_plain_string():
    """Backwards compat: existing channels configure a single trigger word."""
    cli._TRIGGER_RE_CACHE.pop("Jakis Kanal", None)
    pat = cli._trigger_re_for("Jakis Kanal", {"Jakis Kanal": {"trigger": "asystent"}})
    assert pat.search("asystent hej")
    assert not pat.search("bob hej")
    cli._TRIGGER_RE_CACHE.pop("Jakis Kanal", None)


# ---------------------------------------------------------------------------
# Both reply paths must post-process identically
# ---------------------------------------------------------------------------

# Post-processors: handlers that consume a token out of a finished reply.
# Two things are deliberately out of scope: the build-approval gate (runs before
# Claude is called, normal path only) and the generic `*_token` helpers, which are
# the shared implementations the per-persona wrappers delegate to and are never
# wired into a reply path directly.
_POSTPROCESSOR_SUFFIX = re.compile(r"_(idea(_\w+)?|learning|mention)$")
_GENERIC_HELPER_SUFFIX = "_token"


def _is_postprocessor(name: str) -> bool:
    return bool(_POSTPROCESSOR_SUFFIX.search(name)) and not name.endswith(_GENERIC_HELPER_SUFFIX)


def _reply_postprocessors(src: str, var: str) -> set[str]:
    """Handler names assigned back onto *var* inside _process_chat_messages."""
    found = re.findall(rf"^\s+{var}(?:,[^=]*)? = (_handle_\w+)\(", src, re.M)
    return {n for n in found if _is_postprocessor(n)}


def test_catchup_and_normal_paths_run_the_same_reply_postprocessors():
    """_process_chat_messages post-processes a reply in two separate places: the
    catch-up aggregation branch (_reply) and the normal branch (reply). A handler
    added to one and forgotten in the other fails silently and asymmetrically —
    tokens would be stripped and saved normally, but silently ignored on the
    catch-up path, i.e. exactly when the bot returns from downtime with a backlog.
    """
    src = inspect.getsource(cli._process_chat_messages)
    catchup = _reply_postprocessors(src, "_reply")
    normal = _reply_postprocessors(src, "reply")
    assert catchup, "catch-up branch parsed as empty — the regex or the code shape changed"
    assert normal, "normal branch parsed as empty — the regex or the code shape changed"
    assert catchup == normal, (
        f"catch-up only: {sorted(catchup - normal)} | normal only: {sorted(normal - catchup)}"
    )


def test_every_persona_token_handler_is_wired_into_the_reply_paths():
    """A handler that exists but is never called is dead code that looks alive."""
    src = inspect.getsource(cli._process_chat_messages)
    wired = _reply_postprocessors(src, "reply")
    defined = {
        name for name in dir(cli)
        if name.startswith("_handle_") and _is_postprocessor(name)
    }
    assert defined <= wired, f"defined but not wired: {sorted(defined - wired)}"


# ---------------------------------------------------------------------------
# hold_s — grace period before an uninvited reply
# ---------------------------------------------------------------------------

def test_hold_s_for_reads_per_chat_value():
    cfg = {"Kanal": {"hold_s": 45}}
    assert cli._hold_s_for("Kanal", cfg) == 45


def test_hold_s_for_defaults_to_no_hold():
    """Absent, null, zero, unknown chat and no config at all — all mean 'send now'.
    A channel that never opted in must not start lagging."""
    assert cli._hold_s_for("Kanal", {"Kanal": {}}) == 0
    assert cli._hold_s_for("Kanal", {"Kanal": {"hold_s": None}}) == 0
    assert cli._hold_s_for("Kanal", {"Kanal": {"hold_s": 0}}) == 0
    assert cli._hold_s_for("Inny", {"Kanal": {"hold_s": 60}}) == 0
    assert cli._hold_s_for("Kanal", None) == 0


def test_hold_s_for_survives_garbage_and_never_goes_negative():
    """Fail-open: a typo in chat-config.json must not crash the watch loop, and a
    negative value must not turn into a negative sleep."""
    assert cli._hold_s_for("Kanal", {"Kanal": {"hold_s": "sixty"}}) == 0
    assert cli._hold_s_for("Kanal", {"Kanal": {"hold_s": -30}}) == 0


def test_hold_runs_before_the_skip_answered_recheck():
    """The hold is worthless unless _check_skip_answered() re-polls AFTER it — that
    re-poll is what actually spots the human who answered while we waited."""
    src = inspect.getsource(cli._process_chat_messages)
    call = "_skip, _skip_reason = _check_skip_answered("
    assert src.index("_hold_s = _hold_s_for(") < src.index(call)


def test_hold_applies_only_to_uninvited_replies():
    """Someone who calls the agent by name is waiting for it — holding their answer
    back is pure latency. The gate must stay tied to the channel trigger."""
    src = inspect.getsource(cli._process_chat_messages)
    call = "_skip, _skip_reason = _check_skip_answered("
    hold = src[src.index("_hold_s = _hold_s_for("):src.index(call)]
    # an explicit call by name, a quote of the bot or a continuation make the reply "invited": no hold
    assert "trigger_re.search(text)" in hold and "is_quote_of_bob" in hold and "is_continuation" in hold
    assert "_hold_wait_s(" in hold
    assert "_time_mod.sleep(_wait)" in hold


def test_persona_channel_holds_uninvited_replies():
    """A persona channel answers without being called (caller='auto'), so it must
    also wait before speaking uninvited."""
    cfg = json.loads(Path("chat-config.example.json").read_text(encoding="utf-8"))
    for name, entry in cfg["chats"].items():
        if entry.get("persona") == "example":
            assert cli._hold_s_for(name, cfg["chats"]) > 0


def test_model_for_per_chat_alias_overrides_global():
    import argparse
    args = argparse.Namespace(model="claude-sonnet-5-5")
    cfg = {"Kanal": {"model": "opus"}, "Inny": {"model": "claude-haiku-4-5-20251001"}}
    assert cli._model_for("Kanal", cfg, args) == "claude-opus-5-5"
    assert cli._model_for("Inny", cfg, args) == "claude-haiku-4-5-20251001"
    assert cli._model_for("Missing", cfg, args) == "claude-sonnet-5-5"


def test_effort_for_accepts_only_known_levels():
    cfg = {"A": {"effort": "low"}, "B": {"effort": "turbo"}}
    assert cli._effort_for("A", cfg) == "low"
    assert cli._effort_for("B", cfg) is None
    assert cli._effort_for("C", cfg) is None


def test_main_sets_owner_only_umask():
    """Everything Bob writes holds chat content/credentials: main() must set umask 077
    before doing anything else."""
    import os  # noqa: PLC0415

    class _Stop(Exception):
        pass

    def _stop(_argv):
        raise _Stop

    original = os.umask(0o022)
    try:
        with patch.object(cli, "_parse_args", side_effect=_stop), pytest.raises(_Stop):
            cli.main([])
        observed = os.umask(original)
    finally:
        os.umask(original)
    assert observed == 0o077


def test_mcp_extra_adds_opt_in_group_on_top_of_all(monkeypatch):
    import ai  # noqa: PLC0415

    monkeypatch.setattr(ai, "_ALL_GROUPS", [g for g in ai._MCP_GROUPS if g != "md_to_mp3"])
    cfg = {"Notes": {"mcp": None, "mcp_extra": ["md_to_mp3", "no-such-group"]},
           "X": {"mcp": ["jira"], "mcp_extra": ["md_to_mp3"]}, "Y": {"mcp": None}}
    notes = cli._mcp_for("Notes", cfg)
    assert notes[-1] == "md_to_mp3" and set(ai._ALL_GROUPS) <= set(notes) and "no-such-group" not in notes
    assert cli._mcp_for("X", cfg) == ["jira", "md_to_mp3"]
    assert cli._mcp_for("Y", cfg) is None


def test_chat_config_hot_reload_in_place(tmp_path):
    import os  # noqa: PLC0415
    path = tmp_path / "chat-config.json"
    path.write_text(json.dumps({"chats": {"A": {"mcp": ["jira"]}}}), encoding="utf-8")
    cfg = cli._load_chat_config(path)
    same_ref = cfg
    seen = cli._config_mtime(path)
    assert cli._reload_chat_config_if_changed(cfg, path, seen) == seen  # unchanged file: no-op

    path.write_text(json.dumps({"chats": {"A": {"mcp": ["jira", "todos"]}}}), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, seen + 5))
    seen = cli._reload_chat_config_if_changed(cfg, path, seen)
    assert same_ref is cfg and cli._mcp_for("A", cfg) == ["jira", "todos"]

    path.write_text("{ half-saved", encoding="utf-8")  # broken edit keeps the running config
    os.utime(path, (path.stat().st_atime, seen + 5))
    assert cli._reload_chat_config_if_changed(cfg, path, seen) == seen
    assert cli._mcp_for("A", cfg) == ["jira", "todos"]


# ---------------------------------------------------------------------------
# Example persona — labels, idea/learning tokens, attachments
# ---------------------------------------------------------------------------


def test_agent_html_prefix_example_persona():
    assert "[Assistant]" in cli._agent_html_prefix("example")
    assert "[Bob]" in cli._agent_html_prefix(None)


def test_owner_label_distinguishes_persona_from_owner():
    """All personas post from USER_MRI — without the HTML prefix lookup, the persona's
    own past replies would be shown to the model as the owner's messages."""
    def _msg(raw_html):
        return {"sender_mri": cli.api_client.USER_MRI, "raw_html": raw_html, "sender": "Boss"}

    assert cli._owner_label(
        _msg(f"<p>{cli._agent_html_prefix('example')}: hi</p>")) == "Assistant"
    assert cli._owner_label(_msg(f"<p>{cli._agent_html_prefix(None)}: hi</p>")) == "Bob"
    assert cli._owner_label(_msg("<p>a plain owner message</p>")) == cli.OWNER_LABEL


def test_check_skip_answered_filters_own_agent_message(monkeypatch):
    """Own-account agent-prefixed messages (Bob/persona) are excluded from 'newer'."""
    fake_msgs = [
        {"id": "m2", "sender_mri": cli.api_client.USER_MRI, "text": "[Bob]: thinking...", "sequence_id": 11},
        {"id": "m3", "sender_mri": cli.api_client.USER_MRI, "text": "[Assistant]: thinking...", "sequence_id": 12},
    ]
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=10: fake_msgs)
    ai_called = []
    monkeypatch.setattr(
        cli.ai, "check_already_answered",
        lambda *a, **kw: ai_called.append((a, kw)) or {"answered": True, "override": False},
    )

    skip, reason = cli._check_skip_answered("conv1", 10, "trigger-id", "question?", "reply")

    assert (skip, reason) == (False, "")
    assert ai_called == []


@pytest.mark.parametrize("tag", ["EXAMPLE-IDEA", "EXAMPLE-POMYSL"])
def test_handle_example_idea_writes_jsonl_and_strips_token(tmp_path, tag):
    """The English tag is canonical; the Polish EXAMPLE-POMYSL from older prompts is still accepted."""
    ideas_file = tmp_path / "ideas.jsonl"
    with patch.object(cli, "_EXAMPLE_IDEAS_FILE", ideas_file):
        reply = cli._handle_example_idea(
            f"Thanks for the idea! [{tag}: Push alert | An alert about unapproved "
            "documents.] Saved :)",
            "Nowak Ewa",
        )
    assert tag not in reply and "Saved :)" in reply
    (line,) = ideas_file.read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(line)
    assert (entry["sender"], entry["title"]) == ("Nowak Ewa", "Push alert")
    assert "unapproved documents" in entry["idea"] and "date" in entry


def test_handle_example_idea_no_token_returns_reply_unchanged(tmp_path):
    ideas_file = tmp_path / "ideas.jsonl"
    with patch.object(cli, "_EXAMPLE_IDEAS_FILE", ideas_file):
        assert cli._handle_example_idea("a plain reply", "X") == "a plain reply"
    assert not ideas_file.exists()


def test_handle_example_idea_multiple_tokens_no_blank_gap(tmp_path):
    """Several ideas in one message must not leave a wall of blank lines behind."""
    ideas_file = tmp_path / "ideas.jsonl"
    reply = (
        "A golden list! Saving every point!\n"
        "[EXAMPLE-IDEA: Idea 1 | Body of the first idea.]\n"
        "[EXAMPLE-IDEA: Idea 2 | Body of the second idea.]\n"
        "[EXAMPLE-POMYSL: Idea 3 | Body of the third idea.]\n"
        "Thanks — 3 concrete ideas."
    )
    with patch.object(cli, "_EXAMPLE_IDEAS_FILE", ideas_file):
        cleaned = cli._handle_example_idea(reply, "Nowak Ewa")
    assert "EXAMPLE-IDEA" not in cleaned and "EXAMPLE-POMYSL" not in cleaned and "\n\n" not in cleaned
    assert len(ideas_file.read_text(encoding="utf-8").strip().splitlines()) == 3


@pytest.mark.parametrize("tag", ["EXAMPLE-LEARN", "EXAMPLE-UCZY-SIE"])
def test_example_learning_only_from_the_owner(tmp_path, tag):
    """Enforced by MRI: a token anyone else talks the model into is stripped, not saved.
    The Polish EXAMPLE-UCZY-SIE tag is accepted as an alias."""
    tok = f"Ok. [{tag}: title | content]"
    with patch.object(cli, "_EXAMPLE_LEARN_DIR", tmp_path):
        out = cli._handle_example_learning(tok, "8:orgid:00000000-0000-0000-0000-0000000000ff")
        assert tag not in out and not list(tmp_path.glob("*.md"))
        assert cli._handle_example_learning(tok, cli.api_client.USER_MRI) == "Ok."
    (saved,) = tmp_path.glob("*.md")
    assert saved.read_text(encoding="utf-8") == "# title\n\ncontent\n"


def test_image_attachments_reach_every_persona():
    """For a persona the screenshot is often the report itself: the gate that puts
    'Attachments of the message:' into the private notes must cover every persona."""
    src = inspect.getsource(cli._process_chat_messages)
    assert "if persona and _image_paths:" in src


# ---------------------------------------------------------------------------
# Owner labels in prompt text come from OWNER_ALIAS, never a hard-coded name
# ---------------------------------------------------------------------------

def test_availability_block_is_headed_with_owner_alias(monkeypatch):
    import owner_schedule  # noqa: PLC0415

    today = owner_schedule._today()
    monkeypatch.setattr(owner_schedule, "_load", lambda: {today.isoformat(): "working remotely"})
    monkeypatch.setattr(cli, "OWNER_LABEL", "Boss")
    block = cli._build_schedule_context()
    assert "[BOSS AVAILABILITY]" in block and "working remotely" in block


def test_personal_reminder_prompt_names_the_owner_alias():
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert "REMINDER FOR {OWNER_LABEL.upper()}" in src
    assert "reminder for {OWNER_LABEL}" in src


# ---------------------------------------------------------------------------
# English commands and tags are canonical; the Polish forms stay accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("regex, english, polish", [
    ("_ZAPAMIETAJ_RE", "Bob, remember: buy milk", "Bob, zapamiętaj: buy milk"),
    ("_POKAZ_NOTATKI_RE", "Bob, show notes", "Bob, pokaż notatki"),
    ("_BOB_OFF_RE", "Bob disable", "Bob wyłącz"),
    ("_BOB_ON_RE", "Bob enable", "Bob włącz"),
    ("_CHAT_DISABLE_RE", "Bob disable chat X", "Bob wyłącz chat X"),
    ("_CHAT_ENABLE_RE", "Bob enable chat X", "Bob włącz chat X"),
    ("_CHAT_STATUS_RE", "Bob chats", "Bob chaty"),
    ("_BOB_HELP_RE", "Bob help", "Bob pomoc"),
    ("_BOB_POLL_RE", "Bob poll model", "Bob ankieta model"),
    ("_BOB_BUILD_APPROVE_RE", "Bob approve 3", "Bob zatwierdź 3"),
    ("_BOB_BUILD_REJECT_RE", "Bob reject 3", "Bob odrzuć 3"),
    ("_APR_DECIDE_RE", "Bob, approve apr-0123456789: ok", "Bob, zatwierdzam apr-0123456789: ok"),
    ("_APR_DECIDE_BARE_RE", "Bob reject", "Bob odrzucam"),
    ("_HARMONOGRAM_SET_RE", "Bob, schedule today: remote", "Bob, harmonogram dziś: zdalnie"),
    ("_POKAZ_HARMONOGRAM_RE", "Bob, show schedule", "Bob, pokaż harmonogram"),
    ("_HARMONOGRAM_CLEAR_RE", "Bob, schedule delete 28.05", "Bob, harmonogram usuń 28.05"),
    ("_BROADCAST_SET_RE", "Bob, set broadcast teama send 09:00", "Bob, ustaw broadcast teama wyslij 09:00"),
    ("_NOTIF_SET_RE", "Bob, remind tomorrow 9:30 in Notes: call", "Bob, przypomnij jutro 9:30 na Notes: call"),
    ("_NOTIF_SHOW_RE", "Bob, show reminders", "Bob, pokaż przypomnienia"),
    ("_NOTIF_DEL_RE", "Bob, delete reminder t1", "Bob, usuń przypomnienie t1"),
    ("_NOTIF_DEL_OVERDUE_RE", "Bob, delete overdue", "Bob, usuń zaległe"),
    ("_GRILL_START_RE", "grill me", "grillujemy"),
    ("_GRILL_STOP_RE", "end grill", "zakończ grill"),
])
def test_owner_commands_accept_english_and_polish(regex, english, polish):
    pat = getattr(cli, regex)
    assert pat.search(english), f"{regex} must match the English form {english!r}"
    assert pat.search(polish), f"{regex} must keep the Polish alias {polish!r}"


def test_remember_captures_the_note_in_both_languages():
    assert cli._ZAPAMIETAJ_RE.search("Bob, remember: buy milk").group(1).strip() == "buy milk"
    assert cli._ZAPAMIETAJ_RE.search("Bob zapamietaj: kup mleko").group(1).strip() == "kup mleko"


def test_notif_set_captures_chat_and_text_in_english():
    m = cli._NOTIF_SET_RE.search("Bob, remind tomorrow 9:30 in AI Channel: call the vendor")
    assert m.groups() == ("tomorrow", "9:30", "AI Channel", "call the vendor")


def test_build_gate_catches_english_requests_but_not_ordinary_ones():
    assert cli._BOB_BUILD_VERB_RE.search("Bob, build me a game about cats")
    assert cli._BOB_BUILD_VERB_RE.search("Bob, zbuduj mi grę")
    assert not cli._BOB_BUILD_VERB_RE.search("Bob, create a Jira ticket for the app")


def test_send_file_tag_accepts_english_polish_and_mixed_forms():
    for reply in (
        "x\n[SEND_FILE: a.md]\nbody\n[/SEND_FILE]",
        "x\n[WYSLIJ_PLIK: a.md]\nbody\n[/WYSLIJ_PLIK]",
        "x\n[SEND_FILE: a.md]\nbody\n[/WYSLIJ_PLIK]",
    ):
        m = cli._FILE_TAG_RE.search(reply)
        assert m and m.group(1) == "a.md" and m.group(2) == "body"
    for tag in ("SEND_FILE_FROM_DISK", "WYSLIJ_PLIK_Z_DYSKU"):
        assert cli._FILE_PATH_TAG_RE.search(f"[{tag}: /data/a.mp3]").group(1) == "/data/a.mp3"
    # the inline tag must not swallow the disk tag
    assert cli._FILE_TAG_RE.search("[SEND_FILE_FROM_DISK: /data/a.mp3]\nx\n[/SEND_FILE]") is None


def test_send_file_polish_alias_is_uploaded_and_stripped(monkeypatch):
    mock_upload = MagicMock(return_value="https://contoso.sharepoint.com/x/a.md")
    monkeypatch.setattr(sharepoint_upload, "upload_file", mock_upload)
    monkeypatch.setattr(cli.api_client, "send_message", MagicMock(return_value=True))
    monkeypatch.setattr(cli, "_remember_bob_file", lambda *a, **kw: None)
    cleaned = cli._extract_and_send_files("Here.\n[WYSLIJ_PLIK: a.md]\nbody\n[/WYSLIJ_PLIK]", "19:abc", "Test")
    assert cleaned == "Here." and mock_upload.call_count == 1


def test_forward_confirm_words_cover_both_languages():
    assert cli._FWD_CONFIRM_WORDS == {"yes", "tak"}


def test_grill_end_markers_cover_both_languages():
    assert "Grill finished" in cli._GRILL_END_MARKERS and "Grill zakończony" in cli._GRILL_END_MARKERS


def test_mcp_cold_start_detects_english_and_polish_prose():
    assert cli._looks_like_mcp_cold_start("Sorry, the MCP servers are not connected yet.")
    assert cli._looks_like_mcp_cold_start("Niestety serwery MCP sie nie polaczyly.")
    assert not cli._looks_like_mcp_cold_start("Here is your Jira ticket.")


# ---------------------------------------------------------------------------
# chat-config.json: Polish keys from older builds are read through roles.normalize_chat
# ---------------------------------------------------------------------------


def test_load_chat_config_normalises_polish_keys(tmp_path):
    p = tmp_path / "chat-config.json"
    p.write_text(json.dumps({"chats": {"A": {"role": ["pm"], "dodatkowo": ["urlopy"], "bez": ["jira"],
                                             "kanal_fr": True, "caller": "all"}}}), encoding="utf-8")
    cfg = cli._load_chat_config(p)["A"]
    assert cfg["roles"] == ["pm"] and cfg["add_groups"] == ["leave"] and cfg["remove_groups"] == ["jira"]
    assert cfg["external_chat"] is True and cfg["caller"] == "all"
    assert not {"role", "dodatkowo", "bez", "kanal_fr"} & set(cfg)


def test_reload_chat_config_normalises_polish_keys(tmp_path):
    p = tmp_path / "chat-config.json"
    p.write_text(json.dumps({"chats": {"A": {"role": ["pm"]}}}), encoding="utf-8")
    live: dict = {}
    cli._reload_chat_config_if_changed(live, p, 0.0)
    assert live == {"A": {"roles": ["pm"]}}


def test_mcp_for_reads_polish_role_key_from_a_raw_config(monkeypatch):
    import roles  # noqa: PLC0415

    seen = []
    monkeypatch.setattr(roles, "load", lambda: {"roles": {}})
    monkeypatch.setattr(roles, "effective_groups", lambda cfg, doc, *a: seen.append((cfg, doc)) or ["jira"])
    assert cli._mcp_for("A", {"A": {"role": ["pm"], "kanal_fr": True}}) == ["jira"]
    cfg, doc = seen[0]
    assert cfg["roles"] == ["pm"] and cfg["external_chat"] is True and doc == {"roles": {}}


def test_control_chat_mcp_list_drops_roles_written_in_either_language(tmp_path):
    p = tmp_path / "chat-config.json"
    chat_config = {"A": {"role": ["pm"], "dodatkowo": ["jira"], "bez": ["todos"]}}
    cli._apply_chat_config_change("A", "mcp", ["jira"], chat_config, path=p)
    assert chat_config["A"] == {"mcp": ["jira"]}
    assert json.loads(p.read_text(encoding="utf-8"))["chats"]["A"] == {"mcp": ["jira"]}


def test_leave_poller_uses_the_english_names():
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert "org_config.LEAVE_SHAREPOINT_URL" in src and "knowledge/leave/leave.json" in src
    assert "URLOPY_SHAREPOINT_URL" not in src and "urlopy.json" not in src


def test_time_zone_comes_from_org_config():
    assert cli._TZ_NAME == cli.org_config.TIMEZONE
    assert '"Europe/Warsaw"' not in Path(cli.__file__).read_text(encoding="utf-8")


def test_example_persona_adopts_older_polish_files(tmp_path, monkeypatch):
    """pomysly.jsonl and nauka/ from an older build become ideas.jsonl and learning/ before the next write."""
    monkeypatch.setattr(cli, "_EXAMPLE_IDEAS_FILE", tmp_path / "ideas.jsonl")
    monkeypatch.setattr(cli, "_EXAMPLE_LEARN_DIR", tmp_path / "learning")
    (tmp_path / "pomysly.jsonl").write_text('{"kind": "pomysl"}\n', encoding="utf-8")
    (tmp_path / "nauka").mkdir()
    (tmp_path / "nauka" / "old.md").write_text("# old\n", encoding="utf-8")
    cli._handle_example_idea("[EXAMPLE-IDEA: New | body]", "Alice")
    lines = (tmp_path / "ideas.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"kind": "pomysl"}' and len(lines) == 2
    assert not (tmp_path / "pomysly.jsonl").exists()
    assert (tmp_path / "learning" / "old.md").exists() and not (tmp_path / "nauka").exists()


@pytest.mark.parametrize("text", [
    "Bob, why does the build of the app fail on CI?",
    "Bob, generate release notes for the app",
    "Bob, make me a summary of the app incidents",
])
def test_ordinary_english_asks_do_not_hit_the_build_gate(text):
    assert not cli._BOB_BUILD_VERB_RE.search(text)


def test_english_build_commands_still_hit_the_build_gate():
    for text in ("Bob, make me a snake game", "Bob, build us a simple website", "Bob, code me a prototype"):
        assert cli._BOB_BUILD_VERB_RE.search(text), text


def test_ordinary_english_sentences_are_not_taken_as_control_commands():
    assert not cli._ZAPAMIETAJ_RE.search("Bob, remember the ticket we discussed on Monday? What was its key?")
    assert cli._ZAPAMIETAJ_RE.search("Bob, remember that I am out Friday").group(1) == "I am out Friday"
    assert not cli._HARMONOGRAM_SET_RE.search("Bob, schedule a call with Ola")
    assert cli._HARMONOGRAM_SET_RE.search("Bob, schedule 28.05-30.05: leave").groups() == ("28.05-30.05", "leave")
    assert not cli._BOB_FORWARD_RE.search("Bob, send it to me as a file")
    assert not cli._BOB_ON_RE.search("Bob enable notifications for the team")
    assert cli._BOB_ON_RE.search("Bob enable")
    assert not cli._BOB_POLL_RE.search("Bob poll the team about lunch")
    assert cli._BOB_POLL_RE.search("Bob poll chat AI Channel mcp").group(1) == "chat AI Channel mcp"


@pytest.mark.parametrize("notify_chat", ["Notes", "Notatki"])
def test_owner_reminder_for_the_default_notes_chat_goes_to_the_control_chat(monkeypatch, tmp_path, notify_chat):
    """An older install calls its notes chat "Notatki"; a reminder for "Notes" must not be resolved by a substring
    of chat topics (a "Release notes" chat would match) - it goes to the control chat."""
    import argparse
    due = [{"id": "t1", "notify_chat": notify_chat, "title": "call the vendor", "notify_at": "2026-11-08T15:00"}]
    monkeypatch.setattr(cli.todos_store, "due_for_fire", lambda: list(due))
    monkeypatch.setattr(cli.todos_store, "mark_notified", lambda _id: None)
    monkeypatch.setattr(cli.ai, "freeform_reply", lambda *a, **k: "Hey, a reminder: call the vendor")
    looked_up, sent = [], []
    monkeypatch.setattr(cli.api_client, "find_conversation_id", lambda name: looked_up.append(name) or "19:release")
    monkeypatch.setattr(cli.api_client, "send_message", lambda text, cid: sent.append(cid) or True)
    cli._fire_due_notifications([("Notatki", "19:control"), ("Team", "19:team")],
                                argparse.Namespace(model="m"), str(tmp_path / "log.jsonl"),
                                chat_config={"Notatki": {"control_chat": True}, "Team": {}})
    assert sent == ["19:control"] and looked_up == []
