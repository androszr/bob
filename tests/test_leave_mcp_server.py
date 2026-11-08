"""Tests for scripts/leave/mcp_server.py — no live services, no network."""
import json
from pathlib import Path

import pytest

import scripts.leave.mcp_server as srv


def _write_payload(path: Path, records: list[dict]) -> None:
    """Write a minimal leave.json payload to path."""
    payload = {
        "fetched_at": "2026-06-13T08:00:00+00:00",
        "count": len(records),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _patch_dest(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(srv, "_KNOWLEDGE_DEST", path)
    monkeypatch.setattr(srv, "_LEGACY_DEST", path.parent / "absent-legacy.json")


# ---------------------------------------------------------------------------
# leave_meta
# ---------------------------------------------------------------------------


def test_leave_meta_returns_fetched_at_and_count(tmp_path, monkeypatch):
    """leave_meta returns fetched_at and count from the file."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, [{"Id": 1}, {"Id": 2}])
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_meta())
    assert result["fetched_at"] == "2026-06-13T08:00:00+00:00"
    assert result["count"] == 2
    assert "file_mtime" in result


def test_leave_meta_missing_file(tmp_path, monkeypatch):
    """leave_meta returns ERROR when file is missing."""
    dest = tmp_path / "nonexistent.json"
    _patch_dest(monkeypatch, dest)

    result = srv.leave_meta()
    assert result.startswith("ERROR:")


# ---------------------------------------------------------------------------
# leave_check
# ---------------------------------------------------------------------------

_SAMPLE_RECORDS = [
    {
        "Id": 1,
        "name": "Ewa Kowalska",
        "first_day": "2026-06-10T00:00:00",
        "last_day": "2026-06-20T00:00:00",
    },
    {
        "Id": 2,
        "name": "Marek Nowak",
        "first_day": "2026-06-25T00:00:00",
        "last_day": "2026-06-30T00:00:00",
    },
    {
        "Id": 3,
        "name": "Zofia Wisniewski",
        "first_day": "2026-07-01T00:00:00",
        "last_day": "2026-07-14T00:00:00",
    },
]


def test_leave_check_date_in_range(tmp_path, monkeypatch):
    """leave_check returns persons whose leave covers the given date."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_check("2026-06-15"))
    assert result["count"] == 1
    assert result["items"][0]["name"] == "Ewa Kowalska"


def test_leave_check_date_on_boundary_start(tmp_path, monkeypatch):
    """leave_check matches on the first day of leave (inclusive)."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_check("2026-06-10"))
    assert result["count"] == 1
    assert result["items"][0]["name"] == "Ewa Kowalska"


def test_leave_check_date_on_boundary_end(tmp_path, monkeypatch):
    """leave_check matches on the last day of leave (inclusive)."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_check("2026-06-20"))
    assert result["count"] == 1
    assert result["items"][0]["name"] == "Ewa Kowalska"


def test_leave_check_nobody_on_leave(tmp_path, monkeypatch):
    """leave_check returns count=0 when nobody is on leave."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_check("2026-06-22"))
    assert result["count"] == 0
    assert result["items"] == []


def test_leave_check_multiple_on_same_day(tmp_path, monkeypatch):
    """leave_check returns all persons on leave the same day."""
    records = [
        {
            "Id": 1,
            "name": "Ewa Kowalska",
            "first_day": "2026-06-01T00:00:00",
            "last_day": "2026-06-30T00:00:00",
        },
        {
            "Id": 2,
            "name": "Marek Nowak",
            "first_day": "2026-06-15T00:00:00",
            "last_day": "2026-06-20T00:00:00",
        },
    ]
    dest = tmp_path / "leave.json"
    _write_payload(dest, records)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_check("2026-06-16"))
    assert result["count"] == 2


def test_leave_check_invalid_date_format(tmp_path, monkeypatch):
    """leave_check returns ERROR for invalid date format."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = srv.leave_check("not-a-date")
    assert result.startswith("ERROR:")


def test_leave_check_missing_file(tmp_path, monkeypatch):
    """leave_check returns ERROR when file is missing."""
    dest = tmp_path / "nonexistent.json"
    _patch_dest(monkeypatch, dest)

    result = srv.leave_check("2026-06-15")
    assert result.startswith("ERROR:")


def test_leave_check_result_fields(tmp_path, monkeypatch):
    """leave_check result items contain expected fields."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_check("2026-06-15"))
    assert result["count"] == 1
    item = result["items"][0]
    assert "name" in item
    assert "first_day" in item
    assert "last_day" in item


# ---------------------------------------------------------------------------
# leave_list
# ---------------------------------------------------------------------------


def test_leave_list_no_filter_returns_all(tmp_path, monkeypatch):
    """leave_list without month returns all records."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_list())
    assert result["count"] == len(_SAMPLE_RECORDS)


def test_leave_list_month_filter_overlapping(tmp_path, monkeypatch):
    """leave_list with month returns only leaves overlapping that month."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_list(month="2026-06"))
    # Ewa (Jun 10-20) and Marek (Jun 25-30) overlap June; Zofia (Jul) does not
    assert result["count"] == 2
    names = {item["name"] for item in result["items"]}
    assert "Ewa Kowalska" in names
    assert "Marek Nowak" in names
    assert "Zofia Wisniewski" not in names


def test_leave_list_month_filter_no_overlap(tmp_path, monkeypatch):
    """leave_list returns count=0 when no leave overlaps the month."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_list(month="2026-08"))
    assert result["count"] == 0
    assert result["items"] == []


def test_leave_list_month_spanning_boundary(tmp_path, monkeypatch):
    """leave_list includes leave that starts before month and ends inside it."""
    records = [
        {
            "Id": 1,
            "name": "Ewa Kowalska",
            "first_day": "2026-05-28T00:00:00",
            "last_day": "2026-06-05T00:00:00",
        },
    ]
    dest = tmp_path / "leave.json"
    _write_payload(dest, records)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_list(month="2026-06"))
    assert result["count"] == 1


def test_leave_list_invalid_month_format(tmp_path, monkeypatch):
    """leave_list returns ERROR for invalid month format."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = srv.leave_list(month="not-a-month")
    assert result.startswith("ERROR:")


def test_leave_list_missing_file(tmp_path, monkeypatch):
    """leave_list returns ERROR when file is missing."""
    dest = tmp_path / "nonexistent.json"
    _patch_dest(monkeypatch, dest)

    result = srv.leave_list()
    assert result.startswith("ERROR:")


def test_leave_list_returns_fetched_at(tmp_path, monkeypatch):
    """leave_list result includes fetched_at from the payload."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_list())
    assert result["fetched_at"] == "2026-06-13T08:00:00+00:00"


@pytest.mark.parametrize(
    "date,expected_count",
    [
        ("2026-06-10", 1),  # first day of Ewa's leave
        ("2026-06-20", 1),  # last day of Ewa's leave
        ("2026-06-21", 0),  # day after Ewa's leave
        ("2026-06-25", 1),  # first day of Marek's leave
        ("2026-06-30", 1),  # last day of Marek's leave
        ("2026-07-01", 1),  # first day of Zofia's leave
        ("2026-09-01", 0),  # no one on leave
    ],
)
def test_leave_check_parametrized_boundary(
    tmp_path, monkeypatch, date: str, expected_count: int
):
    """leave_check boundary conditions across multiple dates."""
    dest = tmp_path / "leave.json"
    _write_payload(dest, _SAMPLE_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_check(date))
    assert result["count"] == expected_count, (
        f"date={date}: expected {expected_count} result(s), got {result['count']}"
    )


# ---------------------------------------------------------------------------
# Older data written by previous builds stays readable
# ---------------------------------------------------------------------------

_POLISH_RECORDS = [
    {
        "Id": 1,
        "Imię i nazwisko": "Ewa Kowalska",
        "Pierwszy dzień urlopu": "2026-06-10T00:00:00",
        "Ostatni dzień urlopu": "2026-06-20T00:00:00",
    },
    {
        "Id": 2,
        "Imie_i_nazwisko": "Marek Nowak",
        "Pierwszy_dzien_urlopu": "2026-06-12T00:00:00",
        "Ostatni_dzien_urlopu": "2026-06-18T00:00:00",
    },
]


def test_old_row_keys_are_read_as_english(tmp_path, monkeypatch):
    dest = tmp_path / "leave.json"
    _write_payload(dest, _POLISH_RECORDS)
    _patch_dest(monkeypatch, dest)

    result = json.loads(srv.leave_check("2026-06-15"))
    assert result["count"] == 2
    assert {i["name"] for i in result["items"]} == {"Ewa Kowalska", "Marek Nowak"}
    assert result["items"][0]["first_day"] == "2026-06-10T00:00:00"
    listed = json.loads(srv.leave_list(month="2026-06"))["items"]
    assert all("Pierwszy dzień urlopu" not in r and "last_day" in r for r in listed)


def test_old_email_key_is_read_as_email():
    assert srv.normalize_record({"Adres e-mail": "a@example.com"}) == {"email": "a@example.com"}


def test_english_key_wins_over_polish_alias():
    rec = srv.normalize_record({"first_day": "2026-01-02", "Pierwszy dzień urlopu": "1999-01-01"})
    assert rec == {"first_day": "2026-01-02"}


def test_falls_back_to_legacy_data_file(tmp_path, monkeypatch):
    legacy = tmp_path / "urlopy" / "urlopy.json"
    _write_payload(legacy, _POLISH_RECORDS)
    monkeypatch.setattr(srv, "_KNOWLEDGE_DEST", tmp_path / "leave" / "leave.json")
    monkeypatch.setattr(srv, "_LEGACY_DEST", legacy)

    assert json.loads(srv.leave_meta())["count"] == 2
    assert json.loads(srv.leave_check("2026-06-15"))["count"] == 2


def test_new_data_file_wins_over_legacy(tmp_path, monkeypatch):
    new = tmp_path / "leave" / "leave.json"
    legacy = tmp_path / "urlopy" / "urlopy.json"
    _write_payload(new, _SAMPLE_RECORDS[:1])
    _write_payload(legacy, _POLISH_RECORDS)
    monkeypatch.setattr(srv, "_KNOWLEDGE_DEST", new)
    monkeypatch.setattr(srv, "_LEGACY_DEST", legacy)

    assert json.loads(srv.leave_meta())["count"] == 1
