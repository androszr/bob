"""Deterministic unit tests for scripts/datetime_bob/resolver.py.

All tests inject ref=date(2026, 6, 13) — a Saturday — so they never depend
on the real system clock.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "datetime_bob"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent"))

import resolver  # noqa: E402

# Fixed reference date: 2026-06-13 = Saturday (weekday 5)
REF = date(2026, 6, 13)


# ---------------------------------------------------------------------------
# build_today_anchor
# ---------------------------------------------------------------------------

def test_build_today_anchor_never_none():
    result = resolver.build_today_anchor(ref=REF)
    assert result is not None
    assert len(result) > 0


def test_build_today_anchor_contains_iso():
    result = resolver.build_today_anchor(ref=REF)
    assert "2026-06-13" in result


def test_build_today_anchor_contains_weekday():
    result = resolver.build_today_anchor(ref=REF)
    assert "Saturday" in result


def test_build_today_anchor_contains_iso_week():
    # 2026-06-13 is in ISO week 24
    assert REF.isocalendar().week == 24
    result = resolver.build_today_anchor(ref=REF)
    assert "24" in result


def test_build_today_anchor_contains_timezone():
    result = resolver.build_today_anchor(ref=REF)
    assert "Europe/Warsaw" in result


def test_build_today_anchor_format_prefix():
    result = resolver.build_today_anchor(ref=REF)
    assert result.startswith("[TODAY]")


# ---------------------------------------------------------------------------
# weekday
# ---------------------------------------------------------------------------

def test_weekday_saturday():
    assert resolver.weekday("2026-06-13") == "Saturday"


def test_weekday_monday():
    assert resolver.weekday("2026-06-15") == "Monday"


def test_weekday_invalid():
    result = resolver.weekday("not-a-date")
    assert result.startswith("ERROR:")


# ---------------------------------------------------------------------------
# resolve_date — simple keywords
# ---------------------------------------------------------------------------

def test_resolve_date_dzis():
    r = resolver.resolve_date("dzis", ref=REF)
    assert r["iso"] == "2026-06-13"
    assert r["is_past"] is False
    assert r["is_future"] is False


def test_resolve_date_dzisiaj():
    r = resolver.resolve_date("dzisiaj", ref=REF)
    assert r["iso"] == "2026-06-13"


def test_resolve_date_jutro():
    r = resolver.resolve_date("jutro", ref=REF)
    assert r["iso"] == "2026-06-14"
    assert r["is_future"] is True
    assert r["is_past"] is False


def test_resolve_date_wczoraj():
    r = resolver.resolve_date("wczoraj", ref=REF)
    assert r["iso"] == "2026-06-12"
    assert r["is_past"] is True
    assert r["is_future"] is False


def test_resolve_date_pojutrze():
    r = resolver.resolve_date("pojutrze", ref=REF)
    assert r["iso"] == "2026-06-15"
    assert r["is_future"] is True


def test_resolve_date_przedwczoraj():
    r = resolver.resolve_date("przedwczoraj", ref=REF)
    assert r["iso"] == "2026-06-11"
    assert r["is_past"] is True


# ---------------------------------------------------------------------------
# resolve_date — "poprzedni/ostatni dzień roboczy" and "następny dzień roboczy"
# REF = Saturday 2026-06-13.
# Prev workday (Saturday → Friday): 2026-06-12
# Next workday (Saturday → Monday): 2026-06-15
# ---------------------------------------------------------------------------

def test_resolve_date_poprzedni_dzien_roboczy():
    r = resolver.resolve_date("poprzedni dzień roboczy", ref=REF)
    assert r["iso"] == "2026-06-12"
    assert r["is_past"] is True


def test_resolve_date_poprzedni_dzien_roboczy_ascii():
    r = resolver.resolve_date("poprzedni dzien roboczy", ref=REF)
    assert r["iso"] == "2026-06-12"


def test_resolve_date_ostatni_dzien_roboczy():
    r = resolver.resolve_date("ostatni dzien roboczy", ref=REF)
    assert r["iso"] == "2026-06-12"


def test_resolve_date_nastepny_dzien_roboczy():
    r = resolver.resolve_date("następny dzień roboczy", ref=REF)
    assert r["iso"] == "2026-06-15"
    assert r["is_future"] is True


def test_resolve_date_nastepny_dzien_roboczy_ascii():
    r = resolver.resolve_date("nastepny dzien roboczy", ref=REF)
    assert r["iso"] == "2026-06-15"


def test_resolve_date_przyszly_dzien_roboczy():
    r = resolver.resolve_date("przyszly dzien roboczy", ref=REF)
    assert r["iso"] == "2026-06-15"


def test_resolve_date_prev_workday_from_monday():
    # Monday → prev workday = Friday (3 days back)
    from datetime import date as _date
    r = resolver.resolve_date("poprzedni dzien roboczy", ref=_date(2026, 6, 15))
    assert r["iso"] == "2026-06-12"


def test_resolve_date_next_workday_from_friday():
    # Friday → next workday = Monday (3 days forward)
    from datetime import date as _date
    r = resolver.resolve_date("nastepny dzien roboczy", ref=_date(2026, 6, 19))
    assert r["iso"] == "2026-06-22"


# ---------------------------------------------------------------------------
# resolve_date — "za N dni" / "N dni temu"
# ---------------------------------------------------------------------------

def test_resolve_date_za_5_dni():
    r = resolver.resolve_date("za 5 dni", ref=REF)
    assert r["iso"] == "2026-06-18"
    assert r["is_future"] is True


def test_resolve_date_3_dni_temu():
    r = resolver.resolve_date("3 dni temu", ref=REF)
    assert r["iso"] == "2026-06-10"
    assert r["is_past"] is True


def test_resolve_date_za_1_dnia():
    r = resolver.resolve_date("za 1 dnia", ref=REF)
    assert r["iso"] == "2026-06-14"


# ---------------------------------------------------------------------------
# resolve_date — bare weekday name (nearest upcoming from tomorrow)
# REF = Saturday. Next Monday = +2 days = 2026-06-15.
# ---------------------------------------------------------------------------

def test_resolve_date_poniedzialek_upcoming():
    r = resolver.resolve_date("poniedzialek", ref=REF)
    # From Saturday, next Monday = 2026-06-15
    assert r["iso"] == "2026-06-15"
    assert r["is_future"] is True


def test_resolve_date_niedziela_upcoming():
    # From Saturday, next Sunday = 2026-06-14 (tomorrow)
    r = resolver.resolve_date("niedziela", ref=REF)
    assert r["iso"] == "2026-06-14"
    assert r["is_future"] is True


def test_resolve_date_sobota_upcoming():
    # From Saturday, next Saturday = 2026-06-20 (same weekday, so +7)
    r = resolver.resolve_date("sobota", ref=REF)
    assert r["iso"] == "2026-06-20"
    assert r["is_future"] is True


# ---------------------------------------------------------------------------
# resolve_date — "ubiegly/zeszly <dzien>" (last past occurrence)
# REF = Saturday 2026-06-13. Last Monday = 2026-06-08.
# ---------------------------------------------------------------------------

def test_resolve_date_ubiegly_poniedzialek():
    r = resolver.resolve_date("ubiegly poniedzialek", ref=REF)
    assert r["iso"] == "2026-06-08"
    assert r["is_past"] is True


def test_resolve_date_zeszly_wtorek():
    r = resolver.resolve_date("zeszly wtorek", ref=REF)
    assert r["iso"] == "2026-06-09"
    assert r["is_past"] is True


def test_resolve_date_ubiegla_sroda():
    r = resolver.resolve_date("ubiegly sroda", ref=REF)
    assert r["iso"] == "2026-06-10"
    assert r["is_past"] is True


def test_resolve_date_ubiegla_sobota():
    # From Saturday, last Saturday (strictly before) = 2026-06-06
    r = resolver.resolve_date("ubiegly sobota", ref=REF)
    assert r["iso"] == "2026-06-06"
    assert r["is_past"] is True


# ---------------------------------------------------------------------------
# resolve_date — "przyszly/nastepny <dzien>" (next AFTER nearest upcoming)
# REF = Saturday 2026-06-13. Nearest Monday = 2026-06-15. Next = 2026-06-22.
# ---------------------------------------------------------------------------

def test_resolve_date_przyszly_poniedzialek():
    r = resolver.resolve_date("przyszly poniedzialek", ref=REF)
    assert r["iso"] == "2026-06-22"
    assert r["is_future"] is True


def test_resolve_date_nastepny_wtorek():
    r = resolver.resolve_date("nastepny wtorek", ref=REF)
    # Nearest Tuesday = 2026-06-16; next after = 2026-06-23
    assert r["iso"] == "2026-06-23"
    assert r["is_future"] is True


# ---------------------------------------------------------------------------
# resolve_date — explicit date formats (delegated to owner_schedule.parse_date)
# ---------------------------------------------------------------------------

def test_resolve_date_ddmm():
    r = resolver.resolve_date("20.06", ref=REF)
    assert r["iso"] == "2026-06-20"


def test_resolve_date_ddmmyyyy():
    r = resolver.resolve_date("20.06.2026", ref=REF)
    assert r["iso"] == "2026-06-20"


def test_resolve_date_iso():
    r = resolver.resolve_date("2026-07-01", ref=REF)
    assert r["iso"] == "2026-07-01"


def test_resolve_date_unknown_returns_error():
    r = resolver.resolve_date("kiedys tam moze", ref=REF)
    assert "error" in r
    assert r["error"].startswith("ERROR:")


# ---------------------------------------------------------------------------
# resolve_range — weeks
# REF = Saturday 2026-06-13.
# This week (Mon-Sun): 2026-06-08 .. 2026-06-14
# Last week: 2026-06-01 .. 2026-06-07
# Next week: 2026-06-15 .. 2026-06-21
# ---------------------------------------------------------------------------

def test_resolve_range_ten_tydzien():
    r = resolver.resolve_range("ten tydzien", ref=REF)
    assert r["from_iso"] == "2026-06-08"
    assert r["to_iso"] == "2026-06-14"


def test_resolve_range_ubiegly_tydzien():
    r = resolver.resolve_range("ubiegly tydzien", ref=REF)
    assert r["from_iso"] == "2026-06-01"
    assert r["to_iso"] == "2026-06-07"


def test_resolve_range_przyszly_tydzien():
    r = resolver.resolve_range("przyszly tydzien", ref=REF)
    assert r["from_iso"] == "2026-06-15"
    assert r["to_iso"] == "2026-06-21"


def test_resolve_range_zeszly_tydzien():
    r = resolver.resolve_range("zeszly tydzien", ref=REF)
    assert r["from_iso"] == "2026-06-01"
    assert r["to_iso"] == "2026-06-07"


def test_resolve_range_nastepny_tydzien():
    r = resolver.resolve_range("nastepny tydzien", ref=REF)
    assert r["from_iso"] == "2026-06-15"
    assert r["to_iso"] == "2026-06-21"


# ---------------------------------------------------------------------------
# resolve_range — months
# REF = Saturday 2026-06-13.
# This month: 2026-06-01 .. 2026-06-30
# Last month: 2026-05-01 .. 2026-05-31
# Next month: 2026-07-01 .. 2026-07-31
# ---------------------------------------------------------------------------

def test_resolve_range_ten_miesiac():
    r = resolver.resolve_range("ten miesiac", ref=REF)
    assert r["from_iso"] == "2026-06-01"
    assert r["to_iso"] == "2026-06-30"
    assert r.get("month") == "2026-06"


def test_resolve_range_ubiegly_miesiac():
    r = resolver.resolve_range("ubiegly miesiac", ref=REF)
    assert r["from_iso"] == "2026-05-01"
    assert r["to_iso"] == "2026-05-31"
    assert r.get("month") == "2026-05"


def test_resolve_range_przyszly_miesiac():
    r = resolver.resolve_range("przyszly miesiac", ref=REF)
    assert r["from_iso"] == "2026-07-01"
    assert r["to_iso"] == "2026-07-31"
    assert r.get("month") == "2026-07"


def test_resolve_range_ostatni_miesiac():
    r = resolver.resolve_range("ostatni miesiac", ref=REF)
    assert r["from_iso"] == "2026-05-01"
    assert r["to_iso"] == "2026-05-31"


# ---------------------------------------------------------------------------
# resolve_range — "za N dni" / "N dni temu" (point range, from==to)
# ---------------------------------------------------------------------------

def test_resolve_range_za_5_dni():
    r = resolver.resolve_range("za 5 dni", ref=REF)
    assert r["from_iso"] == "2026-06-18"
    assert r["to_iso"] == "2026-06-18"


def test_resolve_range_3_dni_temu():
    r = resolver.resolve_range("3 dni temu", ref=REF)
    assert r["from_iso"] == "2026-06-10"
    assert r["to_iso"] == "2026-06-10"


def test_resolve_range_unknown_returns_error():
    r = resolver.resolve_range("nie wiem kiedy", ref=REF)
    assert "error" in r
    assert r["error"].startswith("ERROR:")


# ---------------------------------------------------------------------------
# Year boundary (December/January)
# ---------------------------------------------------------------------------

def test_resolve_range_przyszly_miesiac_year_boundary():
    # ref = 2026-12-20; next month = 2027-01
    ref_dec = date(2026, 12, 20)
    r = resolver.resolve_range("przyszly miesiac", ref=ref_dec)
    assert r["from_iso"] == "2027-01-01"
    assert r["to_iso"] == "2027-01-31"
    assert r.get("month") == "2027-01"


def test_resolve_range_ubiegly_miesiac_year_boundary():
    # ref = 2027-01-05; last month = 2026-12
    ref_jan = date(2027, 1, 5)
    r = resolver.resolve_range("ubiegly miesiac", ref=ref_jan)
    assert r["from_iso"] == "2026-12-01"
    assert r["to_iso"] == "2026-12-31"
    assert r.get("month") == "2026-12"


# ---------------------------------------------------------------------------
# Month boundary: resolve_range week crossing month boundary
# REF = 2026-06-13 (Saturday), last week Mon=2026-06-01, Sun=2026-06-07
# (stays within June — use January boundary instead)
# ---------------------------------------------------------------------------

def test_resolve_range_week_crossing_month_boundary():
    # ref = 2026-07-03 (Friday); last week Mon=2026-06-22, Sun=2026-06-28
    ref_july = date(2026, 7, 3)
    r = resolver.resolve_range("ubiegly tydzien", ref=ref_july)
    assert r["from_iso"] == "2026-06-22"
    assert r["to_iso"] == "2026-06-28"


# ---------------------------------------------------------------------------
# is_past / is_future flags
# ---------------------------------------------------------------------------

def test_is_past_for_past_date():
    r = resolver.resolve_date("wczoraj", ref=REF)
    assert r["is_past"] is True
    assert r["is_future"] is False


def test_is_future_for_future_date():
    r = resolver.resolve_date("jutro", ref=REF)
    assert r["is_future"] is True
    assert r["is_past"] is False


def test_neither_past_nor_future_for_today():
    r = resolver.resolve_date("dzis", ref=REF)
    assert r["is_past"] is False
    assert r["is_future"] is False


# ---------------------------------------------------------------------------
# English expressions (REF = Saturday 2026-06-13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expr,iso", [
    ("today", "2026-06-13"),
    ("Tomorrow", "2026-06-14"),
    ("yesterday", "2026-06-12"),
    ("day after tomorrow", "2026-06-15"),
    ("the day before yesterday", "2026-06-11"),
    ("monday", "2026-06-15"),
    ("Sunday", "2026-06-14"),
    ("saturday", "2026-06-20"),
    ("this friday", "2026-06-19"),
    ("last monday", "2026-06-08"),
    ("last saturday", "2026-06-06"),
    ("previous wednesday", "2026-06-10"),
    ("next monday", "2026-06-15"),
    ("next tuesday", "2026-06-16"),
    ("in 5 days", "2026-06-18"),
    ("in 1 day", "2026-06-14"),
    ("3 days ago", "2026-06-10"),
    ("1 day ago", "2026-06-12"),
    ("previous business day", "2026-06-12"),
    ("last working day", "2026-06-12"),
    ("previous working day", "2026-06-12"),
    ("Previous Working Day", "2026-06-12"),
    ("next business day", "2026-06-15"),
])
def test_resolve_date_english(expr, iso):
    assert resolver.resolve_date(expr, ref=REF)["iso"] == iso


def test_resolve_date_english_and_polish_agree():
    for en, pl in [("last tuesday", "zeszly wtorek"), ("in 5 days", "za 5 dni"), ("3 days ago", "3 dni temu"),
                   ("friday", "piątek")]:
        assert resolver.resolve_date(en, ref=REF) == resolver.resolve_date(pl, ref=REF), en


def test_english_next_weekday_is_the_coming_one_and_polish_przyszly_the_one_after():
    assert resolver.resolve_date("next monday", ref=REF)["iso"] == resolver.resolve_date("monday", ref=REF)["iso"]
    assert resolver.resolve_date("przyszly poniedzialek", ref=REF)["iso"] == "2026-06-22"


def test_resolve_date_result_has_both_weekday_names():
    r = resolver.resolve_date("monday", ref=REF)
    assert r["weekday"] == "Monday"
    assert r["weekday_pl"] == "Poniedziałek"


def test_weekday_pl_kept_for_polish_name():
    assert resolver.weekday_pl("2026-06-13") == "Sobota"
    assert resolver.weekday_pl("nope").startswith("ERROR:")


@pytest.mark.parametrize("expr,frm,to", [
    ("this week", "2026-06-08", "2026-06-14"),
    ("current week", "2026-06-08", "2026-06-14"),
    ("last week", "2026-06-01", "2026-06-07"),
    ("previous week", "2026-06-01", "2026-06-07"),
    ("next week", "2026-06-15", "2026-06-21"),
    ("this month", "2026-06-01", "2026-06-30"),
    ("last month", "2026-05-01", "2026-05-31"),
    ("next month", "2026-07-01", "2026-07-31"),
    ("in 5 days", "2026-06-18", "2026-06-18"),
    ("3 days ago", "2026-06-10", "2026-06-10"),
])
def test_resolve_range_english(expr, frm, to):
    r = resolver.resolve_range(expr, ref=REF)
    assert (r["from_iso"], r["to_iso"]) == (frm, to)


def test_resolve_range_english_month_has_month_key():
    assert resolver.resolve_range("next month", ref=REF)["month"] == "2026-07"


def test_english_error_texts():
    assert resolver.resolve_date("whenever", ref=REF)["error"].startswith("ERROR: date expression")
    assert resolver.resolve_range("whenever", ref=REF)["error"].startswith("ERROR: range expression")
