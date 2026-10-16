"""Tests for the active-reminder helpers in scripts/todos/mcp_server.py.

These helpers replace the old standalone notifications script
module after reminders and todos were unified into a single store.
"""

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Six scripts/*/mcp_server.py files share the same module name. Load this one by
# file path under a unique key to avoid sys.modules collisions across test files.
_TODOS_MCP = Path(__file__).resolve().parent.parent / "scripts" / "todos" / "mcp_server.py"
_spec = importlib.util.spec_from_file_location("_todos_mcp_server", _TODOS_MCP)
todos_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(todos_store)


_OWNER_MRI = todos_store._OWNER_MRI
_OWNER_CHAT = todos_store._OWNER_NOTIFY_CHAT
_NON_OWNER_MRI = "8:orgid:00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """Redirect todos.json to a per-test tmp path so tests can't see each other."""
    monkeypatch.setattr(todos_store, "_TODOS_FILE", tmp_path / "todos.json")


# ---------------------------------------------------------------------------
# add_reminder_todo (regex flow — explicit chat from user command)
# ---------------------------------------------------------------------------


def test_add_reminder_todo_uses_explicit_chat():
    """Regex path always honours the chat the user typed — no owner-routing fallback."""
    tid = todos_store.add_reminder_todo(
        title="ping Magdę o roadmapie",
        notify_at_iso="2026-05-31T09:00:00",
        notify_chat="Person 44",
        source_chat="Notatki",
        sender="Kowalski Jan",
        sender_mri=_OWNER_MRI,
    )
    [task] = todos_store._load()
    assert task["id"] == tid
    assert task["notify_chat"] == "Person 44"
    assert task["notify_at"] == "2026-05-31T09:00:00"
    assert task["notified_at"] is None
    assert task["title"] == "ping Magdę o roadmapie"
    # Date portion of notify_at is mirrored to due_date so the todo shows up
    # in due-date views alongside passive tasks.
    assert task["due_date"] == "2026-05-31"


# ---------------------------------------------------------------------------
# add_todo MCP — default-chat routing depends on creator
# ---------------------------------------------------------------------------


def test_add_todo_owner_default_routes_to_notatki(monkeypatch):
    """Owner-created todo without explicit notify_chat → Jan's control chat."""
    monkeypatch.setenv("BOB_SENDER_MRI", _OWNER_MRI)
    monkeypatch.setenv("BOB_SENDER", "Kowalski Jan")
    monkeypatch.setenv("BOB_CURRENT_CHAT", "Person 11")

    todos_store.add_todo(
        title="zadzwoń do Ani",
        assigned_to="Jan",
        notify_at="2026-05-31T09:00:00",
    )
    [task] = todos_store._load()
    # Source chat is preserved as `chat`, but the reminder fires on Notatki.
    assert task["chat"] == "Person 11"
    assert task["notify_chat"] == _OWNER_CHAT
    assert task["notify_at"] == "2026-05-31T09:00:00"


def test_add_todo_non_owner_default_routes_to_source_chat(monkeypatch):
    """Non-owner todo without explicit notify_chat → the chat where it was created."""
    monkeypatch.setenv("BOB_SENDER_MRI", _NON_OWNER_MRI)
    monkeypatch.setenv("BOB_SENDER", "Person 16")
    monkeypatch.setenv("BOB_CURRENT_CHAT", "Person 16")

    todos_store.add_todo(
        title="kalkulator KAWA — manual",
        assigned_to="Jan",
        notify_at="2026-06-27T09:00:00",
    )
    [task] = todos_store._load()
    assert task["notify_chat"] == "Person 16"
    assert task["chat"] == "Person 16"


def test_add_todo_explicit_notify_chat_wins(monkeypatch):
    """Explicit notify_chat overrides both owner and non-owner default routing."""
    monkeypatch.setenv("BOB_SENDER_MRI", _OWNER_MRI)
    monkeypatch.setenv("BOB_SENDER", "Kowalski Jan")
    monkeypatch.setenv("BOB_CURRENT_CHAT", "Notatki")

    todos_store.add_todo(
        title="pogadać z Anią",
        assigned_to="Jan",
        notify_at="2026-05-30T10:00:00",
        notify_chat="Person 11",
    )
    [task] = todos_store._load()
    assert task["notify_chat"] == "Person 11"


def test_add_todo_without_notify_at_has_no_reminder(monkeypatch):
    """Passive todo (no notify_at) does not get a notify_chat assigned."""
    monkeypatch.setenv("BOB_SENDER_MRI", _OWNER_MRI)
    monkeypatch.setenv("BOB_SENDER", "Kowalski Jan")
    monkeypatch.setenv("BOB_CURRENT_CHAT", "Person 11")

    todos_store.add_todo(title="kupić Bobowi colę", assigned_to="Marek")
    [task] = todos_store._load()
    assert task["notify_at"] is None
    assert task["notify_chat"] is None
    assert task["notified_at"] is None


# ---------------------------------------------------------------------------
# due_for_fire / mark_notified (daemon flow)
# ---------------------------------------------------------------------------


def test_due_for_fire_picks_past_only():
    """Only todos with notify_at <= now AND notified_at is None come back."""
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    past_id = todos_store.add_reminder_todo(
        title="past", notify_at_iso=past, notify_chat="X",
        source_chat="Notatki", sender="R", sender_mri=_OWNER_MRI,
    )
    todos_store.add_reminder_todo(
        title="future", notify_at_iso=future, notify_chat="X",
        source_chat="Notatki", sender="R", sender_mri=_OWNER_MRI,
    )
    due = todos_store.due_for_fire()
    assert [t["id"] for t in due] == [past_id]


def test_due_for_fire_skips_already_notified():
    """Once notified_at is stamped, the same todo doesn't re-fire."""
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    tid = todos_store.add_reminder_todo(
        title="x", notify_at_iso=past, notify_chat="Y",
        source_chat="Notatki", sender="R", sender_mri=_OWNER_MRI,
    )
    assert todos_store.due_for_fire()  # first call: 1 due
    todos_store.mark_notified(tid)
    assert todos_store.due_for_fire() == []  # second call: nothing
    # The todo still exists for audit.
    assert any(t["id"] == tid and t["notified_at"] for t in todos_store._load())


def test_due_for_fire_skips_done_tasks():
    """Completed todos don't ring even if their notify_at is in the past."""
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    tid = todos_store.add_reminder_todo(
        title="x", notify_at_iso=past, notify_chat="Y",
        source_chat="Notatki", sender="R", sender_mri=_OWNER_MRI,
    )
    todos = todos_store._load()
    for t in todos:
        if t["id"] == tid:
            t["status"] = "done"
    todos_store._save(todos)
    assert todos_store.due_for_fire() == []


# ---------------------------------------------------------------------------
# list_pending_reminders / clear_notify / format_pending_reminders
# ---------------------------------------------------------------------------


def test_list_and_format_pending_sorted_by_notify_at():
    todos_store.add_reminder_todo(
        title="later", notify_at_iso="2026-06-10T09:00:00", notify_chat="A",
        source_chat="N", sender="R", sender_mri=_OWNER_MRI,
    )
    todos_store.add_reminder_todo(
        title="earlier", notify_at_iso="2026-06-01T09:00:00", notify_chat="B",
        source_chat="N", sender="R", sender_mri=_OWNER_MRI,
    )
    pending = todos_store.list_pending_reminders()
    assert [t["title"] for t in pending] == ["earlier", "later"]

    formatted = todos_store.format_pending_reminders()
    assert "earlier" in formatted and "later" in formatted
    assert formatted.index("earlier") < formatted.index("later")


def test_format_pending_empty():
    assert todos_store.format_pending_reminders() == "Brak zaplanowanych przypomnień."


def test_clear_notify_keeps_todo_but_disarms_reminder():
    tid = todos_store.add_reminder_todo(
        title="cancel me", notify_at_iso="2026-06-01T09:00:00", notify_chat="X",
        source_chat="N", sender="R", sender_mri=_OWNER_MRI,
    )
    assert todos_store.clear_notify(tid) is True
    [task] = todos_store._load()
    assert task["id"] == tid
    assert task["notify_at"] is None
    assert task["notify_chat"] is None
    # Task itself remains — clear_notify cancels the reminder, doesn't delete the todo.
    assert task["title"] == "cancel me"


def test_clear_notify_returns_false_for_unknown_id():
    assert todos_store.clear_notify("nope") is False


# ---------------------------------------------------------------------------
# format_todos_for_chat — Notatki shows everything, specific chat filters
# ---------------------------------------------------------------------------


def _seed_todo(chat: str, title: str, due_date: str | None = None) -> str:
    """Helper: insert a passive todo (no notify_at) belonging to `chat`."""
    todos = todos_store._load()
    tid = todos_store._short_id()
    todos.append({
        "id": tid, "title": title, "assigned_to": "Jan",
        "status": "todo", "created_at": "2026-05-28T10:00:00",
        "due_date": due_date, "completed_at": None,
        "chat": chat, "notes": None,
        "created_by": "Jan", "created_by_mri": _OWNER_MRI,
        "notify_at": None, "notify_chat": None, "notified_at": None,
    })
    todos_store._save(todos)
    return tid


def test_format_todos_for_chat_show_all_lists_every_chat():
    """show_all=True (used on the control chat) returns todos from every chat."""
    a_id = _seed_todo("Person 11", "ping Anię")
    b_id = _seed_todo("Person 16", "kalkulator KAWA")
    c_id = _seed_todo("Notatki", "własna notka")
    out = todos_store.format_todos_for_chat(current_chat="Notatki", show_all=True)
    assert "wszystkie chaty" in out
    for tid in (a_id, b_id, c_id):
        assert tid in out


def test_format_todos_for_chat_specific_chat_filters_to_chat():
    """show_all=False filters by `chat` field — other chats' todos must not leak."""
    a_id = _seed_todo("Person 11", "ping Anię")
    b_id = _seed_todo("Person 16", "kalkulator KAWA")
    out = todos_store.format_todos_for_chat(current_chat="Person 11", show_all=False)
    assert a_id in out
    assert b_id not in out
    assert "Person 11" in out


def test_format_todos_for_chat_empty_specific_chat_returns_brak():
    """No todos for a specific chat → friendly empty message."""
    out = todos_store.format_todos_for_chat(current_chat="Marek Sowa", show_all=False)
    assert "Brak zadań" in out and "Marek Sowa" in out


def test_format_todos_for_chat_groups_by_due_date():
    """Output groups todos into overdue / today / upcoming / no-date buckets."""
    today = datetime.now().date().isoformat()
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    _seed_todo("Notatki", "stare zaległe", due_date=yesterday)
    _seed_todo("Notatki", "dzisiejsze", due_date=today)
    _seed_todo("Notatki", "jutrzejsze", due_date=tomorrow)
    _seed_todo("Notatki", "bez daty")
    out = todos_store.format_todos_for_chat(current_chat="Notatki", show_all=True)
    assert "ZALEGŁE" in out and "stare zaległe" in out
    assert "DZIŚ" in out and "dzisiejsze" in out
    assert "NADCHODZĄCE" in out and "jutrzejsze" in out
    assert "BEZ TERMINU" in out and "bez daty" in out


def test_format_todos_for_chat_skips_done():
    """Completed todos must not appear in the list."""
    tid = _seed_todo("Person 11", "skończone")
    todos = todos_store._load()
    for t in todos:
        if t["id"] == tid:
            t["status"] = "done"
    todos_store._save(todos)
    out = todos_store.format_todos_for_chat(current_chat="Person 11", show_all=False)
    assert tid not in out


# ---------------------------------------------------------------------------
# delete_todo_by_id (no-auth helper used by cli.py for 'usuń przypomnienie')
# ---------------------------------------------------------------------------


def test_delete_todo_by_id_removes_entry():
    tid = _seed_todo("Notatki", "do usunięcia")
    assert todos_store.delete_todo_by_id(tid) is True
    assert all(t["id"] != tid for t in todos_store._load())


def test_delete_todo_by_id_returns_false_for_unknown():
    assert todos_store.delete_todo_by_id("nope12345") is False


# ---------------------------------------------------------------------------
# update_todo MCP — re-arming uses ORIGINAL creator for routing, not caller
# ---------------------------------------------------------------------------


def test_update_todo_rearm_uses_original_creator_routing(monkeypatch):
    """When a passive todo is re-armed without explicit chat, routing follows ORIGINAL creator."""
    # Non-owner creates a passive todo in their own chat.
    monkeypatch.setenv("BOB_SENDER_MRI", _NON_OWNER_MRI)
    monkeypatch.setenv("BOB_SENDER", "Person 11")
    monkeypatch.setenv("BOB_CURRENT_CHAT", "Person 11")
    todos_store.add_todo(title="todo A", assigned_to="Jan")
    [task_before] = todos_store._load()
    tid = task_before["id"]
    assert task_before["notify_at"] is None

    # Owner (admin) now re-arms it without specifying notify_chat. The fallback
    # must look at the original creator (non-owner → source chat), NOT the caller (owner).
    monkeypatch.setenv("BOB_SENDER_MRI", _OWNER_MRI)
    monkeypatch.setenv("BOB_SENDER", "Kowalski Jan")
    todos_store.update_todo(id=tid, notify_at="2026-05-30T09:00:00")
    [task_after] = todos_store._load()
    assert task_after["notify_at"] == "2026-05-30T09:00:00"
    # Routing follows original creator (non-owner) → source chat "Person 11",
    # even though the caller doing the update is the owner.
    assert task_after["notify_chat"] == "Person 11"


def test_update_todo_cancel_reminder_with_dash():
    tid = todos_store.add_reminder_todo(
        title="x", notify_at_iso="2026-06-01T09:00:00", notify_chat="X",
        source_chat="N", sender="R", sender_mri=_OWNER_MRI,
    )
    # Caller must be owner (admin) to update someone else's todo, or original creator.
    # add_reminder_todo recorded created_by_mri=_OWNER_MRI, so owner can update it.
    import os
    os.environ["BOB_SENDER_MRI"] = _OWNER_MRI
    os.environ["BOB_SENDER"] = "Jan"
    todos_store.update_todo(id=tid, notify_at="-")
    [task] = todos_store._load()
    assert task["notify_at"] is None
    assert task["notify_chat"] is None
