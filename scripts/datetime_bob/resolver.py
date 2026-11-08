"""Pure-compute date resolver for Bob — no network, no side effects.

All public functions accept an optional `ref: date | None = None` parameter
so tests can inject a fixed reference date (deterministic, no real clock).

When ref is None, the real today in org_config.TIMEZONE is used.

Expressions are understood in English and in Polish (older chats, Polish users).
"""

import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Make teams_agent importable so we can reuse its constants.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "teams_agent"))
import owner_schedule  # noqa: E402

# Single source of truth for timezone and weekday labels — reuse from owner_schedule.
_CET = owner_schedule._CET
_DAY_LABELS = owner_schedule._DAY_LABELS  # {0: "Monday", ..., 6: "Sunday"}
_DAY_LABELS_PL = owner_schedule._DAY_LABELS_PL  # {0: "Poniedziałek", ..., 6: "Niedziela"}

# Reverse map: lowercase weekday name -> weekday int (Monday=0 ... Sunday=6).
# English names and short forms, plus Polish names with and without diacritics.
_WEEKDAY_NORM: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
    "poniedzialek": 0,
    "poniedziałek": 0,
    "wtorek": 1,
    "sroda": 2,
    "środa": 2,
    "czwartek": 3,
    "piatek": 4,
    "piątek": 4,
    "sobota": 5,
    "niedziela": 6,
}


def today(ref: date | None = None) -> date:
    """Return today's date in the configured time zone. If ref is given, return ref."""
    if ref is not None:
        return ref
    from datetime import datetime
    return datetime.now(_CET).date()


def weekday(iso: str, ref: date | None = None) -> str:
    """Return the English weekday name for an ISO date string (YYYY-MM-DD).

    ref is unused but accepted for API symmetry.
    """
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return "ERROR: invalid ISO date format"
    return _DAY_LABELS[d.weekday()]


def weekday_pl(iso: str) -> str:
    """Return the Polish weekday name for an ISO date string (YYYY-MM-DD)."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return "ERROR: invalid ISO date format"
    return _DAY_LABELS_PL[d.weekday()]


def build_today_anchor(ref: date | None = None) -> str:
    """Return always-present date anchor string for Bob's prompt context.

    Format: "[TODAY] YYYY-MM-DD (Weekday), ISO week N, time zone <TZ>"

    Never returns None.
    """
    d = today(ref)
    iso = d.isoformat()
    day_name = _DAY_LABELS[d.weekday()]
    iso_week = d.isocalendar().week
    return f"[TODAY] {iso} ({day_name}), ISO week {iso_week}, time zone {_CET.key}"


def resolve_date(expr: str, ref: date | None = None) -> dict:
    """Resolve a relative date expression (English or Polish) to a concrete date.

    Returns dict with keys:
      iso        str  -- YYYY-MM-DD
      weekday    str  -- English weekday name
      weekday_pl str  -- Polish weekday name
      is_past    bool
      is_future  bool

    A bare weekday name, "this <weekday>" and English "next <weekday>" are the nearest
    upcoming one (from tomorrow); the Polish "przyszły <dzień>" is the one AFTER that.

    On unrecognised input returns {"error": "ERROR: ..."}.
    """
    base = today(ref)
    expr_stripped = expr.strip()
    t = expr_stripped.lower()

    # --- Simple keywords ---
    if t in ("dzis", "dzisiaj", "today", "dziś"):
        return _make_result(base, base)
    if t in ("jutro", "tomorrow"):
        return _make_result(base + timedelta(days=1), base)
    if t in ("pojutrze", "day after tomorrow", "the day after tomorrow"):
        return _make_result(base + timedelta(days=2), base)
    if t in ("wczoraj", "yesterday"):
        return _make_result(base - timedelta(days=1), base)
    if t in ("przedwczoraj", "day before yesterday", "the day before yesterday"):
        return _make_result(base - timedelta(days=2), base)

    # --- "previous business day" / "poprzedni dzień roboczy" ---
    if re.match(r'^(?:poprzedni|ubiegly|ubiegły|zeszly|zeszły|ostatni)\s+dzie[nń]\s+roboczy$', t) \
            or re.match(_EN_PREV + r'\s+(?:business|working|work)\s?day$', t):
        return _make_result(_prev_workday(base), base)

    # --- "next business day" / "następny dzień roboczy" ---
    if re.match(r'^(?:nastepny|następny|przyszly|przyszły)\s+dzie[nń]\s+roboczy$', t) \
            or re.match(_EN_NEXT + r'\s+(?:business|working|work)\s?day$', t):
        return _make_result(_next_workday(base), base)

    # --- "in N days" / "za N dni" ---
    n = _days_ahead(t)
    if n is not None:
        return _make_result(base + timedelta(days=n), base)

    # --- "N days ago" / "N dni temu" ---
    n = _days_ago(t)
    if n is not None:
        return _make_result(base - timedelta(days=n), base)

    # --- "last <weekday>" / "ubiegły <dzień>" => last past occurrence of weekday ---
    m = re.match(r'^(?:ubiegly|ubiegły|ubiegla|ubiegła|zeszly|zeszły|zeszla|zeszła|ostatni|ostatnia'
                 r'|ostatniego|last|previous|past)\s+(.+)$', t)
    if m:
        day_name = m.group(1).strip()
        wd = _WEEKDAY_NORM.get(day_name)
        if wd is not None:
            target = _last_weekday(base, wd)
            return _make_result(target, base)

    # --- English "next <weekday>" => the nearest upcoming one, as most English speakers mean it ---
    m = re.match(r'^next\s+(.+)$', t)
    if m and m.group(1).strip() in _WEEKDAY_NORM:
        return _make_result(_next_weekday(base, _WEEKDAY_NORM[m.group(1).strip()]), base)

    # --- Polish "przyszły <dzień>" => the one AFTER the nearest upcoming ---
    m = re.match(r'^(?:przyszly|przyszły|przyszla|przyszła|nastepny|następny|nastepna|następna)\s+(.+)$', t)
    if m:
        day_name = m.group(1).strip()
        wd = _WEEKDAY_NORM.get(day_name)
        if wd is not None:
            # First upcoming (from tomorrow), then the NEXT one after that
            first_upcoming = _next_weekday(base, wd)
            target = first_upcoming + timedelta(weeks=1)
            return _make_result(target, base)

    # --- Bare weekday name (or "this <weekday>") => nearest UPCOMING (from tomorrow) ---
    bare = re.sub(r'^(?:this|coming|on)\s+', '', t)
    if bare in _WEEKDAY_NORM:
        wd = _WEEKDAY_NORM[bare]
        target = _next_weekday(base, wd)
        return _make_result(target, base)

    # --- Delegate DD.MM, DD.MM.YYYY, YYYY-MM-DD to owner_schedule.parse_date ---
    delegated = owner_schedule.parse_date(expr_stripped)
    if delegated is not None:
        return _make_result(delegated, base)

    return {"error": f"ERROR: date expression not recognised: {expr_stripped!r}"}


def resolve_range(expr: str, ref: date | None = None) -> dict:
    """Resolve a week/month/day range expression (English or Polish).

    Returns dict with keys:
      from_iso  str  -- YYYY-MM-DD (inclusive)
      to_iso    str  -- YYYY-MM-DD (inclusive)
      month     str  -- "YYYY-MM" (only for month expressions)

    On unrecognised input returns {"error": "ERROR: ..."}.
    """
    base = today(ref)
    t = expr.strip().lower()

    # --- Week expressions ---
    # This week
    if re.match(_EN_THIS + r'\s+week$', t):
        mon, sun = _week_bounds(base, 0)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}
    if re.match(_EN_PREV + r'\s+week$', t):
        mon, sun = _week_bounds(base, -1)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}
    if re.match(_EN_NEXT + r'\s+week$', t):
        mon, sun = _week_bounds(base, +1)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}

    if re.match(r'^(?:ten|tego|biezacy|bieżący)\s+tydzien(?:ia|owi)?$', t) or t == "ten tydzien" or t == "ten tydzień":
        mon, sun = _week_bounds(base, 0)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}

    if t in ("ten tydzien", "ten tydzień", "biezacy tydzien", "bieżący tydzień",
             "tego tygodnia", "w tym tygodniu"):
        mon, sun = _week_bounds(base, 0)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}

    if re.match(r'^(?:ten|tego|biezacy|bieżący|obecny)\s+tydzie[nń]', t):
        mon, sun = _week_bounds(base, 0)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}

    # Last week (Polish)
    if re.match(r'^(?:ubiegly|ubiegły|zeszly|zeszły|ostatni|poprzedni)\s+tydzie[nń]', t):
        mon, sun = _week_bounds(base, -1)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}

    # Next week (Polish)
    if re.match(r'^(?:przyszly|przyszły|nastepny|następny)\s+tydzie[nń]', t):
        mon, sun = _week_bounds(base, +1)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}

    # --- Month expressions ---
    if re.match(_EN_THIS + r'\s+month$', t):
        return _month_bounds(base.year, base.month)
    if re.match(_EN_PREV + r'\s+month$', t):
        return _month_bounds(*_prev_month(base.year, base.month))
    if re.match(_EN_NEXT + r'\s+month$', t):
        return _month_bounds(*_next_month(base.year, base.month))

    if re.match(r'^(?:ten|tego|biezacy|bieżący|obecny)\s+miesiac(?:a|owi)?', t) or t in (
        "ten miesiac", "ten miesiąc", "tego miesiaca", "tego miesiąca",
        "w tym miesiacu", "w tym miesiącu", "biezacy miesiac", "bieżący miesiąc"
    ):
        return _month_bounds(base.year, base.month)

    if re.match(r'^(?:ubiegly|ubiegły|zeszly|zeszły|ostatni|poprzedni)\s+miesiac(?:a)?', t):
        year, month = _prev_month(base.year, base.month)
        return _month_bounds(year, month)

    if re.match(r'^(?:przyszly|przyszły|nastepny|następny)\s+miesiac(?:a)?', t):
        year, month = _next_month(base.year, base.month)
        return _month_bounds(year, month)

    # --- "in N days" / "N days ago" (and the Polish forms) — point range (from==to) ---
    n = _days_ahead(t)
    if n is not None:
        d = base + timedelta(days=n)
        return {"from_iso": d.isoformat(), "to_iso": d.isoformat()}

    n = _days_ago(t)
    if n is not None:
        d = base - timedelta(days=n)
        return {"from_iso": d.isoformat(), "to_iso": d.isoformat()}

    return {"error": f"ERROR: range expression not recognised: {expr!r}"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# English "this / last / next" prefixes (anchored at the start of the expression).
_EN_THIS = r'^(?:this|current|the\s+current)'
_EN_PREV = r'^(?:last|previous|past|the\s+last|the\s+previous)'
_EN_NEXT = r'^(?:next|coming|the\s+next|the\s+coming)'


def _days_ahead(t: str) -> int | None:
    """N for "in N days" / "za N dni", else None."""
    m = re.match(r'^(?:za\s+(\d+)\s+dni(?:a|u)?|in\s+(\d+)\s+days?)$', t)
    return int(m.group(1) or m.group(2)) if m else None


def _days_ago(t: str) -> int | None:
    """N for "N days ago" / "N dni temu", else None."""
    m = re.match(r'^(\d+)\s+(?:dni(?:a|u)?\s+temu|days?\s+ago)$', t)
    return int(m.group(1)) if m else None


def _prev_workday(ref: date) -> date:
    """Return the most recent business day strictly before ref."""
    dow = ref.weekday()
    if dow == 0:    # Monday -> Friday
        return ref - timedelta(days=3)
    elif dow == 6:  # Sunday -> Friday
        return ref - timedelta(days=2)
    elif dow == 5:  # Saturday -> Friday
        return ref - timedelta(days=1)
    else:
        return ref - timedelta(days=1)


def _next_workday(ref: date) -> date:
    """Return the next business day strictly after ref."""
    dow = ref.weekday()
    if dow == 4:    # Friday -> Monday
        return ref + timedelta(days=3)
    elif dow == 5:  # Saturday -> Monday
        return ref + timedelta(days=2)
    elif dow == 6:  # Sunday -> Monday
        return ref + timedelta(days=1)
    else:
        return ref + timedelta(days=1)


def _make_result(d: date, base: date) -> dict:
    return {
        "iso": d.isoformat(),
        "weekday": _DAY_LABELS[d.weekday()],
        "weekday_pl": _DAY_LABELS_PL[d.weekday()],
        "is_past": d < base,
        "is_future": d > base,
    }


def _next_weekday(ref: date, weekday_int: int) -> date:
    """Return the next occurrence of weekday_int strictly after ref (tomorrow or later)."""
    days_ahead = weekday_int - ref.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return ref + timedelta(days=days_ahead)


def _last_weekday(ref: date, weekday_int: int) -> date:
    """Return the last occurrence of weekday_int strictly before ref."""
    days_behind = ref.weekday() - weekday_int
    if days_behind <= 0:
        days_behind += 7
    return ref - timedelta(days=days_behind)


def _week_bounds(ref: date, offset: int) -> tuple[date, date]:
    """Return (monday, sunday) of the week that is `offset` weeks from ref's week.

    offset=0 => this week, offset=-1 => last week, offset=+1 => next week.
    """
    # Monday of ref's week
    monday_this = ref - timedelta(days=ref.weekday())
    monday_target = monday_this + timedelta(weeks=offset)
    sunday_target = monday_target + timedelta(days=6)
    return monday_this + timedelta(weeks=offset), sunday_target


def _month_bounds(year: int, month: int) -> dict:
    import calendar as _calendar
    last_day = _calendar.monthrange(year, month)[1]
    return {
        "from_iso": date(year, month, 1).isoformat(),
        "to_iso": date(year, month, last_day).isoformat(),
        "month": f"{year:04d}-{month:02d}",
    }


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1
