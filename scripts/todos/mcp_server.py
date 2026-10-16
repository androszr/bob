"""MCP server — Bob's built-in to-do list with active reminders.

Storage: scripts/todos/todos.json (list of task dicts).
Each task:
  id              — 8-char hex from uuid4
  title           — task description
  assigned_to     — person responsible (free text)
  status          — "todo" | "in_progress" | "done"
  created_at      — ISO 8601 timestamp (CET, naive)
  completed_at    — ISO 8601 timestamp or null
  chat            — Teams chat name where the task was created (from BOB_CURRENT_CHAT)
  notes           — optional extra info or null
  created_by      — display name of the person who created the task
  created_by_mri  — Teams MRI of the creator (used for auth checks)
  notify_at       — OPTIONAL ISO 8601 (CET, naive) — when to fire an active reminder
  notify_chat     — OPTIONAL chat name where the reminder will be posted
  notified_at     — set by the daemon after the reminder fires (audit trail)

Authorization:
  - Only the creator (matched by MRI) can update or delete their task.
  - The owner (OWNER_TEAMS_USER_ID in .env) can always modify any task.
  - MRI comes from BOB_SENDER_MRI env var set by cli.py from Teams API — not from message text.

Active-reminder helpers (imported by scripts/teams_agent/cli.py — NOT MCP tools):
  due_for_fire(now)           — todos with notify_at <= now AND notified_at IS NULL
  mark_notified(id, ts)       — set notified_at (called before sending — atomic claim)
  list_pending_reminders()    — todos with notify_at set AND notified_at NULL, sorted
  clear_notify(id)            — remove notify_at/notify_chat (cancel without deleting todo)
  add_reminder_todo(...)      — create a todo seeded with an active reminder

Start: python scripts/todos/mcp_server.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 — patches sys.stderr with CET timestamps
import org_config  # noqa: E402

from mcp.server.fastmcp import FastMCP

_TZ = ZoneInfo("Europe/Warsaw")

mcp = FastMCP("todos-bob")

_TODOS_FILE = Path(__file__).resolve().parent / "todos.json"

# The owner's Teams MRI (OWNER_TEAMS_USER_ID) — immutable identity, used as the superuser
# check. Unset = a sentinel no sender can carry, so nobody gets owner rights.
_OWNER_MRI = org_config.OWNER_MRI

# Default reminder destination for owner-created todos — the owner's control chat.
# Non-owner reminders fall back to the chat where the todo was created.
_OWNER_NOTIFY_CHAT = "Notatki"


def _now() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S")


def _load() -> list[dict]:
    try:
        if _TODOS_FILE.exists():
            return json.loads(_TODOS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[todos] BLAD odczytu: {exc}", file=sys.stderr)
    return []


def _save(todos: list[dict]) -> None:
    try:
        _TODOS_FILE.write_text(
            json.dumps(todos, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[todos] BLAD zapisu: {exc}", file=sys.stderr)


def _short_id() -> str:
    return uuid4().hex[:8]


def _sender_mri() -> str:
    return os.environ.get("BOB_SENDER_MRI", "")


def _sender_name() -> str:
    return os.environ.get("BOB_SENDER", "")


def _is_owner() -> bool:
    return _sender_mri() == _OWNER_MRI


def _can_modify(task: dict) -> bool:
    """Return True if the current sender is allowed to modify this task."""
    if _is_owner():
        return True
    mri = _sender_mri()
    if not mri:
        return False
    task_mri = task.get("created_by_mri", "")
    # Tasks created before MRI tracking: fall back to display name match.
    if not task_mri:
        task_name = task.get("created_by", "")
        return bool(task_name and task_name.lower() == _sender_name().lower())
    return mri == task_mri


def _auth_error(task: dict) -> str:
    creator = task.get("created_by") or "nieznany"
    return (
        f"Brak uprawnień. Zadanie #{task['id']} może edytować tylko "
        f"{creator} (lub {org_config.OWNER_DISPLAY_NAME} jako admin)."
    )


def _parse_due_date(raw: str) -> str | None:
    """Normalize due_date input to ISO 8601 date string (YYYY-MM-DD) or None."""
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Unrecognized format — store as-is; label rendering will fall back to plain display.
    print(f"[todos] due_date format nierozpoznany, przechowuję as-is: {raw!r}", file=sys.stderr)
    return raw


def _parse_notify_at(raw: str) -> str | None:
    """Normalize notify_at input to canonical naive-CET ISO 8601 'YYYY-MM-DDTHH:MM:SS'.

    Accepts the same forms as _parse_due_date plus bare 'YYYY-MM-DDTHH:MM'.
    Returns None for empty input; returns the raw string when format is unrecognised
    (the daemon's fromisoformat call will surface the issue with a clear traceback).
    """
    raw = raw.strip()
    if not raw:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    print(f"[todos] notify_at format nierozpoznany, przechowuję as-is: {raw!r}", file=sys.stderr)
    return raw


def _due_label(due_date: str | None) -> str:
    """Return a short human label for the due date with overdue/upcoming flags."""
    if not due_date:
        return ""
    try:
        due = datetime.strptime(due_date[:10], "%Y-%m-%d").date()
        today = datetime.now(_TZ).date()
        delta = (due - today).days
        if delta < 0:
            return f"  ⚠ ZALEGŁA ({due_date})"
        if delta == 0:
            return f"  ⏰ DZIŚ ({due_date})"
        if delta <= 3:
            return f"  🔜 za {delta}d ({due_date})"
        return f"  📅 {due_date}"
    except ValueError:
        return f"  📅 {due_date}"


@mcp.tool()
def add_todo(
    title: str,
    assigned_to: str,
    due_date: str = "",
    notes: str = "",
    notify_at: str = "",
    notify_chat: str = "",
) -> str:
    """Add a new task to the to-do list.

    The task is owned by the person who requested it (from BOB_SENDER env).
    Only the creator (or Jan) can later update or delete it.

    Set notify_at to make Bob actively send a reminder on the target chat at that time.
    When notify_at is set and notify_chat is empty, the reminder fires on the chat where
    the task was created (BOB_CURRENT_CHAT).

    Args:
        title:       Short task description.
        assigned_to: Person responsible (free text, e.g. "Jan", "Ewa Nowak").
        due_date:    Optional deadline in YYYY-MM-DD format (or YYYY-MM-DD HH:MM:SS).
        notes:       Optional additional context or details.
        notify_at:   Optional ISO 8601 datetime when to fire an active reminder
                     (e.g. "2026-05-31T09:00:00", naive CET).
        notify_chat: Optional Teams chat name where to post the reminder.
                     Defaults to the current chat when notify_at is set.
    """
    current_chat = os.environ.get("BOB_CURRENT_CHAT", "")
    notify_at_norm = _parse_notify_at(notify_at)
    explicit_notify_chat = notify_chat.strip()
    # Reminder routing rule: owner-created todos default to the "Notatki" control chat;
    # everyone else's todos default to the chat where the todo was created. Explicit
    # notify_chat overrides both — used when the request literally says "na <chat>".
    if notify_at_norm:
        if explicit_notify_chat:
            resolved_notify_chat = explicit_notify_chat
        elif _is_owner():
            resolved_notify_chat = _OWNER_NOTIFY_CHAT
        else:
            resolved_notify_chat = current_chat
    else:
        resolved_notify_chat = None
    task = {
        "id": _short_id(),
        "title": title.strip(),
        "assigned_to": assigned_to.strip(),
        "status": "todo",
        "created_at": _now(),
        "due_date": _parse_due_date(due_date),
        "completed_at": None,
        "chat": current_chat,
        "notes": notes.strip() or None,
        "created_by": _sender_name(),
        "created_by_mri": _sender_mri(),
        "notify_at": notify_at_norm,
        "notify_chat": resolved_notify_chat,
        "notified_at": None,
    }
    todos = _load()
    todos.append(task)
    _save(todos)
    creator_label = f" (zlecone przez {task['created_by']})" if task["created_by"] else ""
    due_label = f", termin: {task['due_date']}" if task["due_date"] else ""
    notify_label = (
        f", 🔔 {task['notify_at']} → {task['notify_chat']}" if task["notify_at"] else ""
    )
    return (
        f"Dodano #{task['id']}: {task['title']} → {task['assigned_to']}"
        f"{creator_label}{due_label}{notify_label}"
    )


@mcp.tool()
def list_todos(assigned_to: str = "", status: str = "", all_chats: bool = False) -> str:
    """List tasks from the to-do list.

    By default returns non-completed tasks for the current chat only.
    Set all_chats=True to see tasks from every chat.

    Args:
        assigned_to: Filter by person name (case-insensitive). Leave empty for all.
        status:      Filter by status: "todo", "in_progress", "done", or empty for all non-done.
        all_chats:   If True, include tasks from all chats (default: current chat only).
    """
    todos = _load()
    current_chat = os.environ.get("BOB_CURRENT_CHAT", "")

    filtered = todos
    if not all_chats and current_chat:
        filtered = [t for t in filtered if t.get("chat", "") == current_chat]
    if assigned_to:
        filtered = [t for t in filtered if assigned_to.lower() in t.get("assigned_to", "").lower()]
    if status:
        filtered = [t for t in filtered if t.get("status") == status]
    else:
        filtered = [t for t in filtered if t.get("status") != "done"]

    if not filtered:
        if assigned_to or status:
            return "Brak pasujących zadań."
        return "Lista zadań jest pusta."

    lines = []
    for t in filtered:
        status_label = {
            "todo": "do zrobienia",
            "in_progress": "w trakcie",
            "done": "done",
        }.get(t.get("status", "todo"), t.get("status", "?"))
        line = (
            f"#{t['id']}  {t.get('assigned_to', '?')}"
            f"  |  {t.get('title', '?')}"
            f"  |  {status_label}"
            f"  |  {t.get('created_at', '?')[:10]}"
            f"{_due_label(t.get('due_date'))}"
        )
        if t.get("notify_at"):
            fired = " (wysłane)" if t.get("notified_at") else " (zaplanowane)"
            line += f"  |  🔔 {t['notify_at']} → {t.get('notify_chat') or '?'}{fired}"
        if t.get("created_by"):
            line += f"  |  zlecone przez {t['created_by']}"
        if all_chats and t.get("chat"):
            line += f"  |  {t['chat']}"
        if t.get("notes"):
            line += f"\n       Notatka: {t['notes']}"
        lines.append(line)

    return f"Zadania ({len(filtered)}):\n" + "\n".join(lines)


@mcp.tool()
def update_todo(
    id: str,
    title: str = "",
    assigned_to: str = "",
    status: str = "",
    due_date: str = "",
    notes: str = "",
    notify_at: str = "",
    notify_chat: str = "",
) -> str:
    """Update an existing task (partial update — only provided fields are changed).

    Only the task creator or Jan (admin) can update a task.
    Allowed statuses: "todo", "in_progress", "done".

    Pass "-" for notify_at to cancel an active reminder (clears notify_at + notify_chat).

    Args:
        id:          Task ID (8-char hex, from list_todos output).
        title:       New task description (optional).
        assigned_to: New assignee (optional).
        status:      New status: "todo", "in_progress", or "done" (optional).
        due_date:    New deadline in YYYY-MM-DD format, or "-" to clear it (optional).
        notes:       New notes (optional).
        notify_at:   New reminder time (ISO 8601), or "-" to cancel.
        notify_chat: Chat for the reminder (only relevant when notify_at is set).
    """
    todos = _load()
    for task in todos:
        if task.get("id") == id:
            if not _can_modify(task):
                return _auth_error(task)
            if title:
                task["title"] = title.strip()
            if assigned_to:
                task["assigned_to"] = assigned_to.strip()
            if status:
                if status not in ("todo", "in_progress", "done"):
                    return f"Nieprawidłowy status: {status!r}. Użyj: todo, in_progress, done."
                task["status"] = status
                if status == "done" and not task.get("completed_at"):
                    task["completed_at"] = _now()
                elif status != "done":
                    task["completed_at"] = None
            if due_date:
                task["due_date"] = None if due_date.strip() == "-" else _parse_due_date(due_date)
            if notes:
                task["notes"] = notes.strip()
            if notify_at:
                if notify_at.strip() == "-":
                    task["notify_at"] = None
                    task["notify_chat"] = None
                    task["notified_at"] = None
                else:
                    task["notify_at"] = _parse_notify_at(notify_at)
                    if notify_chat:
                        task["notify_chat"] = notify_chat.strip()
                    elif not task.get("notify_chat"):
                        # Routing fallback: pick by ORIGINAL creator, not the current caller.
                        # Owner-created todos go to Jan's control chat; otherwise, the
                        # chat where the todo was opened (i.e. the conversation it belongs to).
                        if task.get("created_by_mri") == _OWNER_MRI:
                            task["notify_chat"] = _OWNER_NOTIFY_CHAT
                        else:
                            task["notify_chat"] = task.get("chat") or ""
                    # Re-arm: a fresh notify_at means we want this reminder to fire again.
                    task["notified_at"] = None
            elif notify_chat:
                task["notify_chat"] = notify_chat.strip()
            _save(todos)
            due_suffix = f", termin: {task['due_date']}" if task.get("due_date") else ""
            notify_suffix = (
                f", 🔔 {task['notify_at']} → {task.get('notify_chat') or '?'}"
                if task.get("notify_at") else ""
            )
            return (
                f"Zaktualizowano #{id}: {task['title']}"
                f" ({task['status']}) → {task['assigned_to']}{due_suffix}{notify_suffix}"
            )
    return f"Nie znaleziono zadania #{id}."


@mcp.tool()
def list_upcoming_todos(days: int = 3) -> str:
    """List non-done tasks that are overdue or due within the next N days.

    Useful for scheduled reminders. Returns tasks sorted by due_date ascending,
    followed by tasks with no due_date.

    Args:
        days: How many days ahead to look (default: 3). Use 0 for only overdue tasks.
    """
    todos = _load()
    # Scope to the caller's chat unless owner (mirrors list_todos behaviour).
    if not _is_owner():
        current_chat = os.environ.get("BOB_CURRENT_CHAT", "")
        todos = [t for t in todos if t.get("chat") == current_chat]
    today = datetime.now(_TZ).date()
    overdue, upcoming, no_due = [], [], []

    for t in todos:
        if t.get("status") == "done":
            continue
        dd = t.get("due_date")
        if not dd:
            no_due.append(t)
            continue
        try:
            due = datetime.strptime(dd[:10], "%Y-%m-%d").date()
            delta = (due - today).days
            if delta < 0:
                overdue.append((delta, t))
            elif delta <= days:
                upcoming.append((delta, t))
        except ValueError:
            no_due.append(t)

    if not overdue and not upcoming:
        return f"Brak zadań zaległych ani zbliżających się w ciągu {days} dni."

    lines = []

    if overdue:
        lines.append("⚠ ZALEGŁE:")
        for _, t in sorted(overdue, key=lambda x: x[0]):
            lines.append(f"  #{t['id']}  {t.get('assigned_to','?')}  |  {t['title']}  |  termin: {t['due_date']}")
            if t.get("notes"):
                lines.append(f"       {t['notes']}")

    if upcoming:
        lines.append(f"\n🔜 ZBLIŻAJĄCE SIĘ (do {days} dni):")
        for delta, t in sorted(upcoming, key=lambda x: x[0]):
            days_label = "dziś" if delta == 0 else f"za {delta}d"
            lines.append(
                f"  #{t['id']}  {t.get('assigned_to','?')}  |  {t['title']}  |  {days_label} ({t['due_date']})"
            )
            if t.get("notes"):
                lines.append(f"       {t['notes']}")

    return "\n".join(lines)


@mcp.tool()
def delete_todo(id: str) -> str:
    """Permanently delete a task from the to-do list.

    Only the task creator or Jan (admin) can delete a task.

    Args:
        id: Task ID (8-char hex, from list_todos output).
    """
    todos = _load()
    for task in todos:
        if task.get("id") == id:
            if not _can_modify(task):
                return _auth_error(task)
            todos = [t for t in todos if t.get("id") != id]
            _save(todos)
            return f"Usunięto #{id}."
    return f"Nie znaleziono zadania #{id}."


# ---------------------------------------------------------------------------
# Active-reminder helpers — imported by scripts/teams_agent/cli.py daemon.
# These are NOT MCP tools; they operate on the same todos.json storage.
# ---------------------------------------------------------------------------


def due_for_fire(now: datetime | None = None) -> list[dict]:
    """Return todos whose reminder is due to fire and hasn't been sent yet.

    A todo is due when:
      - notify_at is set,
      - notify_at <= now (naive CET comparison),
      - notified_at is None,
      - status != "done" (completed tasks don't ring anymore).

    Storage is NOT mutated here — the caller must call mark_notified() before
    sending to atomically claim each entry and avoid double-fires.
    """
    if now is None:
        now = datetime.now(_TZ).replace(tzinfo=None)
    todos = _load()
    due = []
    for t in todos:
        if not t.get("notify_at") or t.get("notified_at") or t.get("status") == "done":
            continue
        try:
            at_dt = datetime.fromisoformat(t["notify_at"])
        except (ValueError, TypeError):
            print(f"[todos] notify_at nie do parsowania: {t.get('id')} = {t.get('notify_at')!r}",
                  file=sys.stderr)
            continue
        if at_dt <= now:
            due.append(t)
    return due


def mark_notified(task_id: str, ts: str | None = None) -> bool:
    """Stamp notified_at on a todo. Called by the daemon BEFORE sending the message.

    Marking before send mirrors the prior atomic-delete semantics: it prevents
    duplicate fires across restarts at the cost of silent drops if the send fails
    after the mark is persisted. Returns True if the task was found.
    """
    todos = _load()
    for task in todos:
        if task.get("id") == task_id:
            task["notified_at"] = ts or _now()
            _save(todos)
            return True
    return False


def list_pending_reminders() -> list[dict]:
    """Return all todos with an armed reminder, sorted by notify_at ascending.

    Armed = notify_at set AND notified_at is None AND status != "done".
    """
    todos = _load()
    pending = [
        t for t in todos
        if t.get("notify_at") and not t.get("notified_at") and t.get("status") != "done"
    ]
    pending.sort(key=lambda t: t.get("notify_at", ""))
    return pending


def clear_notify(task_id: str) -> bool:
    """Cancel an active reminder while keeping the todo. Returns True on hit."""
    todos = _load()
    for task in todos:
        if task.get("id") == task_id:
            task["notify_at"] = None
            task["notify_chat"] = None
            task["notified_at"] = None
            _save(todos)
            return True
    return False


def add_reminder_todo(
    *,
    title: str,
    notify_at_iso: str,
    notify_chat: str,
    source_chat: str,
    sender: str,
    sender_mri: str,
) -> str:
    """Create a todo seeded as an active reminder. Used by cli.py for 'Bob, przypomnij...'.

    The caller passes notify_chat explicitly (parsed from the user's command), so this
    function does NOT apply the owner-vs-others routing fallback — it trusts what the
    user typed. The owner-routing fallback only kicks in when AI uses add_todo() /
    update_todo() without specifying notify_chat.

    Args:
        title:         The reminder message text.
        notify_at_iso: ISO datetime 'YYYY-MM-DDTHH:MM:SS' (naive CET).
        notify_chat:   Target chat for the reminder (explicit, never empty).
        source_chat:   Chat where the command was issued — recorded for context only.
        sender:        Display name of the requester.
        sender_mri:    Teams MRI of the requester.

    Returns: the new todo id.
    """
    todos = _load()
    task = {
        "id": _short_id(),
        "title": title.strip(),
        "assigned_to": sender or "Jan",
        "status": "todo",
        "created_at": _now(),
        # Soft deadline = date portion of the reminder, so the todo also shows
        # up in due-date views (list_upcoming_todos, remind.py).
        "due_date": notify_at_iso[:10] if notify_at_iso else None,
        "completed_at": None,
        "chat": source_chat,
        "notes": None,
        "created_by": sender,
        "created_by_mri": sender_mri,
        "notify_at": notify_at_iso,
        "notify_chat": notify_chat.strip(),
        "notified_at": None,
    }
    todos.append(task)
    _save(todos)
    return task["id"]


def format_pending_reminders() -> str:
    """Legacy: list only armed reminders. Kept for callers that explicitly want this view."""
    pending = list_pending_reminders()
    if not pending:
        return "Brak zaplanowanych przypomnień."
    lines = ["Zaplanowane przypomnienia:"]
    for t in pending:
        chat = t.get("notify_chat") or "?"
        title = (t.get("title") or "")[:80]
        lines.append(f"  [{t['id']}] {t['notify_at']} → '{chat}': {title}")
    return "\n".join(lines)


def _categorise(todos: list[dict], today_iso: str) -> dict[str, list[dict]]:
    """Bucket non-done todos by due_date relative to today_iso ('YYYY-MM-DD')."""
    overdue, today_list, upcoming, no_date = [], [], [], []
    for t in todos:
        if t.get("status") == "done":
            continue
        dd = t.get("due_date")
        if not dd:
            no_date.append(t)
            continue
        # Compare ISO strings — both YYYY-MM-DD, lexicographic == chronological.
        if dd < today_iso:
            overdue.append(t)
        elif dd == today_iso:
            today_list.append(t)
        else:
            upcoming.append(t)
    return {
        "overdue": sorted(overdue, key=lambda t: t.get("due_date", "")),
        "today": today_list,
        "upcoming": sorted(upcoming, key=lambda t: t.get("due_date", "")),
        "no_date": no_date,
    }


def _format_todo_line(t: dict, show_chat: bool = False) -> str:
    """Render one todo as a single line with title, due/notify markers, and id.

    When show_chat is True (used in the all-chats view), the source chat name is
    used as the leading bracketed label and the short id is moved to the tail
    (still visible for `usuń przypomnienie [id]`). Otherwise the id stays at the
    front like before — in a per-chat view the chat name would be redundant.
    """
    if show_chat:
        chat_label = (t.get("chat") or "?")[:40]
        parts = [f"[{chat_label}]"]
    else:
        parts = [f"[{t['id']}]"]
    title = (t.get("title") or "")[:120]
    parts.append(title)
    extras = []
    if t.get("notify_at"):
        fired = "wysłane" if t.get("notified_at") else "fire"
        chat = t.get("notify_chat") or "?"
        extras.append(f"🔔 {t['notify_at']} → '{chat}' ({fired})")
    if t.get("due_date") and not t.get("notify_at"):
        extras.append(f"termin {t['due_date']}")
    if t.get("assigned_to"):
        extras.append(f"→ {t['assigned_to']}")
    if show_chat:
        extras.append(f"id:{t['id']}")
    if extras:
        parts.append("  |  " + "  ·  ".join(extras))
    return "  " + " ".join(parts)


def format_todos_for_chat(current_chat: str, show_all: bool = False) -> str:
    """Render todos as a single unified view.

    show_all=True (used on the owner's control chat) lists todos across every chat.
    Otherwise only todos whose `chat` field matches current_chat are shown.

    Output groups: ⚠ zaległe → ⏰ dziś → 🔜 nadchodzące → 📋 bez terminu.
    Active reminders (with notify_at) are marked inline alongside passive ones — UX
    treats them as one kind of thing.
    """
    todos = _load()
    if not show_all and current_chat:
        todos = [t for t in todos if t.get("chat") == current_chat]
    todos = [t for t in todos if t.get("status") != "done"]
    if not todos:
        scope = "we wszystkich chatach" if show_all else f"w '{current_chat}'"
        return f"Brak zadań {scope}."

    today_iso = datetime.now(_TZ).date().isoformat()
    buckets = _categorise(todos, today_iso)
    header = (
        f"Zadania (wszystkie chaty, {len(todos)}):"
        if show_all else
        f"Zadania w '{current_chat}' ({len(todos)}):"
    )
    lines = [header]
    if buckets["overdue"]:
        lines.append(f"\n⚠ ZALEGŁE ({len(buckets['overdue'])}):")
        for t in buckets["overdue"]:
            lines.append(_format_todo_line(t, show_chat=show_all))
    if buckets["today"]:
        lines.append(f"\n⏰ DZIŚ ({len(buckets['today'])}):")
        for t in buckets["today"]:
            lines.append(_format_todo_line(t, show_chat=show_all))
    if buckets["upcoming"]:
        lines.append(f"\n🔜 NADCHODZĄCE ({len(buckets['upcoming'])}):")
        for t in buckets["upcoming"]:
            lines.append(_format_todo_line(t, show_chat=show_all))
    if buckets["no_date"]:
        lines.append(f"\n📋 BEZ TERMINU ({len(buckets['no_date'])}):")
        for t in buckets["no_date"]:
            lines.append(_format_todo_line(t, show_chat=show_all))
    return "\n".join(lines)


def delete_todo_by_id(task_id: str) -> bool:
    """Permanently delete a todo. No auth check — caller (cli.py) is already owner-gated."""
    todos = _load()
    new_todos = [t for t in todos if t.get("id") != task_id]
    if len(new_todos) == len(todos):
        return False
    _save(new_todos)
    return True


def delete_overdue_todos(current_chat: str, show_all: bool = False) -> int:
    """Delete all non-done todos whose due_date is in the past.

    Mirrors the scope logic of format_todos_for_chat: show_all=True removes
    overdue tasks across every chat; otherwise only tasks for current_chat.
    Returns the number of deleted tasks.
    """
    todos = _load()
    today_iso = datetime.now(_TZ).date().isoformat()

    def _is_overdue(t: dict) -> bool:
        if t.get("status") == "done":
            return False
        dd = t.get("due_date")
        return bool(dd and dd < today_iso)

    if show_all:
        to_delete = {t["id"] for t in todos if _is_overdue(t)}
    else:
        to_delete = {
            t["id"] for t in todos
            if _is_overdue(t) and t.get("chat") == current_chat
        }

    if not to_delete:
        return 0
    _save([t for t in todos if t.get("id") not in to_delete])
    return len(to_delete)


if __name__ == "__main__":
    print(f"[todos] Serwer startuje, plik: {_TODOS_FILE}", file=sys.stderr)
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp
    run_mcp(mcp, default_port=MCP_PORTS["todos-bob"])
