"""Date-based availability schedule for the owner — injected into Bob's context for every chat.

Storage: owner-schedule.json — dict keyed by ISO date "YYYY-MM-DD".
History is preserved indefinitely; context block shows only today + upcoming entries.

Commands (from the owner's control_chat; the command parser lives in cli.py):
  Bob, schedule today: remote work, available from 10
  Bob, schedule tomorrow: leave
  Bob, schedule 28.05: office
  Bob, schedule 28.05-30.05: conference
  Bob, show schedule
  Bob, schedule clear 28.05

Date tokens accept English and Polish words: today/dziś, tomorrow/jutro,
day after tomorrow/pojutrze, yesterday/wczoraj, plus DD.MM, DD.MM.YYYY, YYYY-MM-DD.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import org_config as _org_config
except Exception:  # noqa: BLE001 — scripts/ not on sys.path in some entry points
    _org_config = None

_SCHEDULE_FILE = Path(__file__).parent / "owner-schedule.json"
_CET = ZoneInfo(getattr(_org_config, "TIMEZONE", "Europe/Warsaw"))

# Polish month names (full and short), kept for parsing Polish input.
_POLISH_MONTHS = {
    "sty": 1, "styczeń": 1, "styczen": 1,
    "lut": 2, "luty": 2,
    "mar": 3, "marzec": 3,
    "kwi": 4, "kwiecień": 4, "kwiecien": 4,
    "maj": 5, "maja": 5,
    "cze": 6, "czerwiec": 6,
    "lip": 7, "lipiec": 7,
    "sie": 8, "sierpień": 8, "sierpien": 8,
    "wrz": 9, "wrzesień": 9, "wrzesien": 9,
    "paź": 10, "paz": 10, "październik": 10, "pazdziernik": 10,
    "lis": 11, "listopad": 11,
    "gru": 12, "grudzień": 12, "grudzien": 12,
}

# Polish weekday labels: still read by datetime_bob (its `weekday_pl` field).
_DAY_LABELS_PL = {
    0: "Poniedziałek", 1: "Wtorek", 2: "Środa",
    3: "Czwartek", 4: "Piątek", 5: "Sobota", 6: "Niedziela",
}

_DAY_LABELS = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday",
    3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday",
}


def _today() -> date:
    from datetime import datetime
    return datetime.now(_CET).date()


def parse_date(token: str) -> date | None:
    """Parse a single date token. Returns None if unrecognised."""
    t = token.strip().lower()
    today = _today()

    if t in ("dziś", "dzis", "today"):
        return today
    if t in ("jutro", "tomorrow"):
        return today + timedelta(days=1)
    if t in ("pojutrze", "day after tomorrow"):
        return today + timedelta(days=2)
    if t in ("wczoraj", "yesterday"):
        return today - timedelta(days=1)

    # DD.MM or DD.MM.YYYY
    m = re.match(r'^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$', token.strip())
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3) or today.year)
        try:
            return date(year, month, day)
        except ValueError:
            return None

    # YYYY-MM-DD
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', token.strip())
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    return None


def parse_date_range(token: str) -> list[date] | None:
    """Parse 'DD.MM-DD.MM' or 'DD.MM.YYYY-DD.MM.YYYY' range. Returns list of dates."""
    # Try splitting on '-' — but be careful with YYYY-MM-DD format
    # Heuristic: if there are 2+ dots, split on the dash that's between two date-like parts
    parts = re.split(r'\s*[-–]\s*', token.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    d1 = parse_date(parts[0])
    d2 = parse_date(parts[1])
    if d1 is None or d2 is None or d2 < d1:
        return None
    result = []
    cur = d1
    while cur <= d2:
        result.append(cur)
        cur += timedelta(days=1)
    return result


def _load() -> dict:
    try:
        if _SCHEDULE_FILE.exists():
            return json.loads(_SCHEDULE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save(data: dict) -> None:
    _SCHEDULE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def set_date(date_token: str, text: str) -> tuple[bool, list[date]]:
    """Set entry for a single date or range. Returns (success, list_of_dates_set)."""
    # Try range first
    if re.search(r'[-–]', date_token) and '.' in date_token:
        dates = parse_date_range(date_token)
        if dates:
            data = _load()
            for d in dates:
                data[d.isoformat()] = text.strip()
            _save(data)
            return True, dates

    d = parse_date(date_token)
    if d is None:
        return False, []
    data = _load()
    data[d.isoformat()] = text.strip()
    _save(data)
    return True, [d]


def clear_date(date_token: str) -> tuple[bool, list[date]]:
    """Remove entry for a date or range. Returns (success, list_of_dates_removed)."""
    if re.search(r'[-–]', date_token) and '.' in date_token:
        dates = parse_date_range(date_token)
        if dates:
            data = _load()
            for d in dates:
                data.pop(d.isoformat(), None)
            _save(data)
            return True, dates

    d = parse_date(date_token)
    if d is None:
        return False, []
    data = _load()
    data.pop(d.isoformat(), None)
    _save(data)
    return True, [d]


def _fmt_date(d: date) -> str:
    """Format date as 'DD.MM.YYYY (Weekday)'."""
    return f"{d.day:02d}.{d.month:02d}.{d.year} ({_DAY_LABELS[d.weekday()]})"


def format_full_schedule(past_days: int = 7, future_days: int = 30) -> str:
    """Return human-readable schedule: recent past + upcoming entries."""
    data = _load()
    if not data:
        return "No schedule entries."

    today = _today()
    cutoff_past = (today - timedelta(days=past_days)).isoformat()
    cutoff_future = (today + timedelta(days=future_days)).isoformat()

    relevant = {k: v for k, v in data.items() if cutoff_past <= k <= cutoff_future}
    if not relevant:
        all_dates = sorted(data.keys())
        return (
            f"No entries in the ±{past_days}/{future_days} day window.\n"
            f"All entries: {all_dates[0]} … {all_dates[-1]} ({len(data)} total)"
        )

    lines = [f"Owner's schedule (last {past_days}d + next {future_days}d):"]
    for iso_date in sorted(relevant.keys()):
        d = date.fromisoformat(iso_date)
        marker = " ◀ TODAY" if d == today else ""
        lines.append(f"  {_fmt_date(d)}: {relevant[iso_date]}{marker}")
    return "\n".join(lines)


def build_context_block(owner_label: str = "OWNER") -> str | None:
    """Return context block for injection into Bob's prompt, headed with `owner_label`.

    Shows today's entry + upcoming 14 days. Returns None when schedule is empty.
    """
    data = _load()
    if not data:
        return None

    today = _today()
    today_iso = today.isoformat()
    cutoff = (today + timedelta(days=14)).isoformat()

    upcoming = {k: v for k, v in data.items() if today_iso <= k <= cutoff}
    if not upcoming:
        return None

    today_entry = upcoming.get(today_iso)
    lines = [
        f"[{owner_label.upper()} AVAILABILITY]",
        f"Today: {_fmt_date(today)}.",
    ]
    if today_entry:
        lines.append(f"Today: {today_entry}")
    else:
        lines.append("Today: no entry.")

    future = {k: v for k, v in upcoming.items() if k != today_iso}
    if future:
        lines.append("Coming days:")
        for iso_date in sorted(future.keys()):
            d = date.fromisoformat(iso_date)
            lines.append(f"  {_fmt_date(d)}: {future[iso_date]}")

    lines.append(
        f"If someone asks where the {owner_label.lower()} is or what they are doing, answer from the above. "
        "Keep a natural tone; do not read the list out verbatim."
    )
    return "\n".join(lines)
