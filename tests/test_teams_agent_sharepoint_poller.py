"""Tests for sharepoint_poller: xlsx_to_records and write_json (no live Playwright)."""
import json
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from scripts.teams_agent.sharepoint_poller import write_json, xlsx_to_records

# ---------------------------------------------------------------------------
# xlsx_to_records
# ---------------------------------------------------------------------------


def _make_xlsx(path: Path, rows: list[list]) -> Path:
    """Write rows to an XLSX at path (first row = headers) and return path."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for row in rows:
        ws.append(row)
    wb.save(str(path))
    wb.close()
    return path


def test_xlsx_to_records_basic(tmp_path):
    """Two data rows with string and integer values are mapped correctly."""
    xlsx = _make_xlsx(
        tmp_path / "test.xlsx",
        [
            ["Id", "Name", "Score"],
            [1, "Alice", 99],
            [2, "Bob", 88],
        ],
    )
    records = xlsx_to_records(xlsx)
    assert len(records) == 2
    assert records[0] == {"Id": 1, "Name": "Alice", "Score": 99}
    assert records[1] == {"Id": 2, "Name": "Bob", "Score": 88}


def test_xlsx_to_records_datetime_isoformat(tmp_path):
    """Datetime values are serialised as ISO-8601 strings."""
    dt = datetime(2024, 6, 15, 9, 30, 0)
    xlsx = _make_xlsx(
        tmp_path / "dates.xlsx",
        [
            ["Id", "Start", "Name"],
            [1, dt, "Urlop A"],
        ],
    )
    records = xlsx_to_records(xlsx)
    assert len(records) == 1
    assert records[0]["Start"] == dt.isoformat()
    assert records[0]["Id"] == 1
    assert records[0]["Name"] == "Urlop A"


def test_xlsx_to_records_empty_cell_is_none(tmp_path):
    """Empty cells are mapped to None."""
    xlsx = _make_xlsx(
        tmp_path / "empty.xlsx",
        [
            ["Id", "Note"],
            [1, None],
        ],
    )
    records = xlsx_to_records(xlsx)
    assert records[0]["Note"] is None


def test_xlsx_to_records_no_data_rows(tmp_path):
    """Header-only sheet returns empty list."""
    xlsx = _make_xlsx(
        tmp_path / "header_only.xlsx",
        [["Id", "Name"]],
    )
    records = xlsx_to_records(xlsx)
    assert records == []


def test_xlsx_to_records_empty_workbook(tmp_path):
    """Completely empty sheet returns empty list."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    p = tmp_path / "empty_wb.xlsx"
    wb.save(str(p))
    wb.close()
    records = xlsx_to_records(p)
    assert records == []


def test_xlsx_to_records_polish_column_names(tmp_path):
    """Polish column names are preserved as-is (ensure_ascii=False irrelevant here)."""
    xlsx = _make_xlsx(
        tmp_path / "polish.xlsx",
        [
            ["Imie i nazwisko", "Adres e-mail"],
            ["Jan Kowalski", "jan@example.com"],
        ],
    )
    records = xlsx_to_records(xlsx)
    assert "Imie i nazwisko" in records[0]
    assert records[0]["Imie i nazwisko"] == "Jan Kowalski"


# ---------------------------------------------------------------------------
# write_json
# ---------------------------------------------------------------------------


def _set_dest(monkeypatch, dest: Path) -> None:
    monkeypatch.setattr("scripts.teams_agent.sharepoint_poller.DEST", dest)


def test_write_json_structure(tmp_path, monkeypatch):
    """write_json writes valid JSON with fetched_at, count, records."""
    dest = tmp_path / "urlopy.json"
    _set_dest(monkeypatch, dest)

    records = [{"Id": 1, "Name": "Alice"}, {"Id": 2, "Name": "Bob"}]
    count = write_json(records)

    assert count == 2
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert len(payload["records"]) == 2
    assert "fetched_at" in payload
    # fetched_at must be parseable as ISO datetime
    datetime.fromisoformat(payload["fetched_at"])


def test_write_json_ensure_ascii_false(tmp_path, monkeypatch):
    """Polish characters in records are written as UTF-8, not escaped."""
    dest = tmp_path / "urlopy.json"
    _set_dest(monkeypatch, dest)

    write_json([{"Imie i nazwisko": "Jan Kowąlski"}])

    raw = dest.read_bytes().decode("utf-8")
    # Polish characters must appear literally, not as \uXXXX escapes
    assert "Kowąlski" in raw


def test_write_json_creates_parent_dirs(tmp_path, monkeypatch):
    """write_json creates missing parent directories."""
    dest = tmp_path / "nested" / "deep" / "urlopy.json"
    _set_dest(monkeypatch, dest)

    write_json([{"Id": 1}])
    assert dest.exists()


def test_write_json_count_matches_records(tmp_path, monkeypatch):
    """count field always matches len(records)."""
    dest = tmp_path / "urlopy.json"
    _set_dest(monkeypatch, dest)

    for n in (0, 1, 5):
        write_json([{"Id": i} for i in range(n)])
        payload = json.loads(dest.read_text(encoding="utf-8"))
        assert payload["count"] == n
        assert len(payload["records"]) == n


def test_write_json_writes_exactly_one_file(tmp_path, monkeypatch):
    """Jeden plik wynikowy, bez kopii."""
    dest = tmp_path / "urlopy" / "urlopy.json"
    _set_dest(monkeypatch, dest)

    assert write_json([{"Id": 1, "Imie_i_nazwisko": "Ewa Nowak"}]) == 1

    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == [dest]
    assert "Ewa Nowak" in dest.read_text(encoding="utf-8")


def test_dest_points_at_knowledge_urlopy():
    """DEST to knowledge/urlopy/urlopy.json (zrodlo urlopy-bob)."""
    from scripts.teams_agent import sharepoint_poller

    assert sharepoint_poller.DEST.parts[-3:] == ("knowledge", "urlopy", "urlopy.json")
    assert not hasattr(sharepoint_poller, "KNOWLEDGE_DEST")


def test_write_json_is_atomic_and_leaves_no_tmp(tmp_path, monkeypatch):
    """Nadpisanie istniejacego pliku idzie przez os.replace i nie zostawia *.tmp."""
    from scripts.teams_agent import sharepoint_poller

    dest = tmp_path / "urlopy.json"
    dest.write_text('{"count": 0, "records": []}', encoding="utf-8")
    _set_dest(monkeypatch, dest)

    replaced: list[tuple[str, str]] = []
    real_replace = sharepoint_poller.os.replace

    def spy_replace(src, dst):
        replaced.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(sharepoint_poller.os, "replace", spy_replace)

    write_json([{"Id": 1}, {"Id": 2}])

    assert len(replaced) == 1
    src, dst = replaced[0]
    assert dst == str(dest)
    assert Path(src).parent == dest.parent
    assert json.loads(dest.read_text(encoding="utf-8"))["count"] == 2
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_json_failure_keeps_old_file_and_cleans_tmp(tmp_path, monkeypatch):
    """Blad w trakcie zapisu: stary plik nietkniety, plik tymczasowy usuniety."""
    from scripts.teams_agent import sharepoint_poller

    dest = tmp_path / "urlopy.json"
    old = '{"count": 7, "records": []}'
    dest.write_text(old, encoding="utf-8")
    _set_dest(monkeypatch, dest)

    def boom(src, dst):
        raise OSError("dysk pelny")

    monkeypatch.setattr(sharepoint_poller.os, "replace", boom)

    with pytest.raises(OSError):
        write_json([{"Id": 1}])

    assert dest.read_text(encoding="utf-8") == old
    assert [p.name for p in tmp_path.iterdir()] == ["urlopy.json"]
