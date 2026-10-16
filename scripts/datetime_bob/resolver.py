"""Pure-compute date resolver for Bob — no network, no side effects.

All public functions accept an optional `ref: date | None = None` parameter
so tests can inject a fixed reference date (deterministic, no real clock).

When ref is None, the real today (Europe/Warsaw) is used.
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
_DAY_LABELS_PL = owner_schedule._DAY_LABELS_PL  # {0: "Poniedzialek", ..., 6: "Niedziela"}

# Reverse map: lowercase Polish weekday name -> weekday int (Monday=0 ... Sunday=6),
# including diacritics variants.
_WEEKDAY_NORM: dict[str, int] = {
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
    """Return today's date in Europe/Warsaw. If ref is given, return ref."""
    if ref is not None:
        return ref
    from datetime import datetime
    return datetime.now(_CET).date()


def weekday(iso: str, ref: date | None = None) -> str:
    """Return Polish weekday name for an ISO date string (YYYY-MM-DD).

    ref is unused but accepted for API symmetry.
    """
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return "BLAD: nieprawidlowy format daty ISO"
    return _DAY_LABELS_PL[d.weekday()]


def build_today_anchor(ref: date | None = None) -> str:
    """Return always-present date anchor string for Bob's prompt context.

    Format: "[DZIS] YYYY-MM-DD (Dzien tygodnia), tydzien ISO N, strefa Europe/Warsaw"

    Never returns None.
    """
    d = today(ref)
    iso = d.isoformat()
    day_name = _DAY_LABELS_PL[d.weekday()]
    iso_week = d.isocalendar().week
    return f"[DZIS] {iso} ({day_name}), tydzien ISO {iso_week}, strefa Europe/Warsaw"


def resolve_date(expr: str, ref: date | None = None) -> dict:
    """Resolve a Polish relative date expression to a concrete date.

    Returns dict with keys:
      iso        str  -- YYYY-MM-DD
      weekday_pl str  -- Polish weekday name
      is_past    bool
      is_future  bool

    On unrecognised input returns {"error": "BLAD: ..."}.
    """
    base = today(ref)
    expr_stripped = expr.strip()
    t = expr_stripped.lower()

    # --- Simple keywords ---
    if t in ("dzis", "dzisiaj", "today", "dziś"):
        return _make_result(base, base)
    if t in ("jutro", "tomorrow"):
        return _make_result(base + timedelta(days=1), base)
    if t in ("pojutrze",):
        return _make_result(base + timedelta(days=2), base)
    if t in ("wczoraj", "yesterday"):
        return _make_result(base - timedelta(days=1), base)
    if t in ("przedwczoraj",):
        return _make_result(base - timedelta(days=2), base)

    # --- "poprzedni dzień roboczy" / "poprzedni dzien roboczy" ---
    if re.match(r'^(?:poprzedni|ubiegly|ubiegły|zeszly|zeszły|ostatni)\s+dzie[nń]\s+roboczy$', t):
        return _make_result(_prev_workday(base), base)

    # --- "następny dzień roboczy" / "nastepny dzien roboczy" ---
    if re.match(r'^(?:nastepny|następny|przyszly|przyszły)\s+dzie[nń]\s+roboczy$', t):
        return _make_result(_next_workday(base), base)

    # --- "za N dni" ---
    m = re.match(r'^za\s+(\d+)\s+dni(?:a|u)?$', t)
    if m:
        n = int(m.group(1))
        return _make_result(base + timedelta(days=n), base)

    # --- "N dni temu" ---
    m = re.match(r'^(\d+)\s+dni(?:a|u)?\s+temu$', t)
    if m:
        n = int(m.group(1))
        return _make_result(base - timedelta(days=n), base)

    # --- "ubiegly/zeszly <dzien>" => last past occurrence of weekday ---
    m = re.match(r'^(?:ubiegly|ubiegły|zeszly|zeszły|ostatni|ostatniego)\s+(.+)$', t)
    if m:
        day_name = m.group(1).strip()
        wd = _WEEKDAY_NORM.get(day_name)
        if wd is not None:
            target = _last_weekday(base, wd)
            return _make_result(target, base)

    # --- "przyszly/nastepny <dzien>" => the one AFTER the nearest upcoming ---
    m = re.match(r'^(?:przyszly|przyszły|nastepny|następny)\s+(.+)$', t)
    if m:
        day_name = m.group(1).strip()
        wd = _WEEKDAY_NORM.get(day_name)
        if wd is not None:
            # First upcoming (from tomorrow), then the NEXT one after that
            first_upcoming = _next_weekday(base, wd)
            target = first_upcoming + timedelta(weeks=1)
            return _make_result(target, base)

    # --- Bare weekday name => nearest UPCOMING (from tomorrow) ---
    if t in _WEEKDAY_NORM:
        wd = _WEEKDAY_NORM[t]
        target = _next_weekday(base, wd)
        return _make_result(target, base)

    # --- Delegate DD.MM, DD.MM.YYYY, YYYY-MM-DD to owner_schedule.parse_date ---
    delegated = owner_schedule.parse_date(expr_stripped)
    if delegated is not None:
        return _make_result(delegated, base)

    return {"error": f"BLAD: nierozpoznane wyrazenie daty: {expr_stripped!r}"}


def resolve_range(expr: str, ref: date | None = None) -> dict:
    """Resolve a Polish week/month/day range expression.

    Returns dict with keys:
      from_iso  str  -- YYYY-MM-DD (inclusive)
      to_iso    str  -- YYYY-MM-DD (inclusive)
      month     str  -- "YYYY-MM" (only for month expressions)

    On unrecognised input returns {"error": "BLAD: ..."}.
    """
    base = today(ref)
    t = expr.strip().lower()

    # --- Week expressions ---
    # Ten tydzien
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

    # Ubiegly / zeszly tydzien
    if re.match(r'^(?:ubiegly|ubiegły|zeszly|zeszły|ostatni|poprzedni)\s+tydzie[nń]', t):
        mon, sun = _week_bounds(base, -1)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}

    # Przyszly / nastepny tydzien
    if re.match(r'^(?:przyszly|przyszły|nastepny|następny)\s+tydzie[nń]', t):
        mon, sun = _week_bounds(base, +1)
        return {"from_iso": mon.isoformat(), "to_iso": sun.isoformat()}

    # --- Month expressions ---
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

    # --- "za N dni" / "N dni temu" — point range (from==to) ---
    m = re.match(r'^za\s+(\d+)\s+dni(?:a|u)?$', t)
    if m:
        n = int(m.group(1))
        d = base + timedelta(days=n)
        return {"from_iso": d.isoformat(), "to_iso": d.isoformat()}

    m = re.match(r'^(\d+)\s+dni(?:a|u)?\s+temu$', t)
    if m:
        n = int(m.group(1))
        d = base - timedelta(days=n)
        return {"from_iso": d.isoformat(), "to_iso": d.isoformat()}

    return {"error": f"BLAD: nierozpoznane wyrazenie zakresu: {expr!r}"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
