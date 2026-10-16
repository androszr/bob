"""MCP server exposing urlopy (vacation) data for the team.

Start: python scripts/urlopy/mcp_server.py
Claude connects via stdio (managed automatically by claude --mcp-config).

Reads knowledge/urlopy/urlopy.json populated by sharepoint_poller.py.
No network calls — pure local file read.
"""

import json
import sys
import time
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

# Ensure logts and mcp_admin are importable from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 -- patches sys.stderr with CET timestamps

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("urlopy-bob")

# Source of truth for the data file location.
_KNOWLEDGE_DEST = (
    Path(__file__).resolve().parents[2] / "knowledge" / "urlopy" / "urlopy.json"
)


def _tlog(tag: str, t0: float, extra: str = "") -> None:
    ms = (time.perf_counter() - t0) * 1000
    suffix = f", {extra}" if extra else ""
    print(f"[urlopy-mcp] {tag} ({ms:.0f}ms{suffix})", file=sys.stderr, flush=True)


def _load_payload() -> dict:
    """Load JSON payload from _KNOWLEDGE_DEST.

    Returns the parsed dict on success.
    Raises RuntimeError with a human-readable message on failure.
    """
    if not _KNOWLEDGE_DEST.exists():
        raise RuntimeError(
            f"Brak pliku danych: {_KNOWLEDGE_DEST}. "
            "Uruchom sharepoint_poller.py aby pobrać dane urlopów."
        )
    try:
        text = _KNOWLEDGE_DEST.read_text(encoding="utf-8")
        return json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Nie można odczytać pliku urlopów: {exc}") from exc


def _parse_date(value: str) -> date_type | None:
    """Parse an ISO date/datetime string to date. Returns None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None


@mcp.tool()
def urlopy_meta() -> str:
    """Sprawdz swiezosc danych urlopowych zespolu.

    Nie wymaga zadnych parametrow. Zwraca JSON z:
      fetched_at:  str (ISO 8601) -- kiedy dane zostaly pobrane z SharePoint
      count:       int -- liczba rekordow urlopowych
      file_mtime:  str (ISO 8601) -- czas ostatniej modyfikacji pliku na dysku

    Rekomendacja: wywolaj raz na sesje przed urlopy_check/urlopy_list
    aby ocenic czy dane sa swieze.

    Przykladowe wywolanie MCP:
      { "name": "urlopy_meta", "arguments": {} }
    """
    t0 = time.perf_counter()
    print("[urlopy-mcp] urlopy_meta start", file=sys.stderr, flush=True)
    try:
        payload = _load_payload()
        mtime = _KNOWLEDGE_DEST.stat().st_mtime
        mtime_iso = datetime.fromtimestamp(mtime).isoformat()
        result = json.dumps(
            {
                "fetched_at": payload.get("fetched_at"),
                "count": payload.get("count"),
                "file_mtime": mtime_iso,
            },
            ensure_ascii=False,
            indent=2,
        )
        _tlog("urlopy_meta done", t0)
        return result
    except RuntimeError as exc:
        _tlog(f"urlopy_meta error: {exc}", t0)
        return f"BLAD: {exc}"


@mcp.tool()
def urlopy_check(date: str) -> str:
    """Sprawdz kto jest na urlopie w podanym dniu.

    date:  Wymagana data w formacie YYYY-MM-DD (np. "2026-06-20").

    Zwraca liste osob, dla ktorych Pierwszy_dzien_urlopu <= date <= Ostatni_dzien_urlopu.
    Kazdy wynik zawiera: Imie_i_nazwisko, Pierwszy_dzien_urlopu, Ostatni_dzien_urlopu.

    Jesli nikt nie jest na urlopie — zwraca pusty JSON z count=0.

    Przykladowe wywolanie MCP:
      { "name": "urlopy_check", "arguments": { "date": "2026-06-20" } }
    """
    t0 = time.perf_counter()
    print(f"[urlopy-mcp] urlopy_check start date={date!r}", file=sys.stderr, flush=True)
    try:
        check_date = _parse_date(date)
        if check_date is None:
            return "BLAD: Nieprawidlowy format daty. Uzyj YYYY-MM-DD (np. 2026-06-20)."

        payload = _load_payload()
        records = payload.get("records", [])

        matches = []
        for rec in records:
            start = _parse_date(rec.get("Pierwszy dzień urlopu"))
            end = _parse_date(rec.get("Ostatni dzień urlopu"))
            if start is None or end is None:
                continue
            if start <= check_date <= end:
                matches.append(
                    {
                        "Imię i nazwisko": rec.get("Imię i nazwisko"),
                        "Pierwszy dzień urlopu": rec.get("Pierwszy dzień urlopu"),
                        "Ostatni dzień urlopu": rec.get("Ostatni dzień urlopu"),
                    }
                )

        result = json.dumps(
            {"date": date, "count": len(matches), "items": matches},
            ensure_ascii=False,
            indent=2,
        )
        _tlog("urlopy_check done", t0, f"{len(matches)} wynikow")
        return result
    except RuntimeError as exc:
        _tlog(f"urlopy_check error: {exc}", t0)
        return f"BLAD: {exc}"


@mcp.tool()
def urlopy_list(month: str = "") -> str:
    """Lista wszystkich urlopow, opcjonalnie filtrowana po miesiacu.

    month: Opcjonalny YYYY-MM (np. "2026-06"). Jesli podany, zwraca tylko urlopy
           ktore nakladaja sie na ten miesiac (urlop zaczyna sie przed koncem miesiaca
           I konczy po jego poczatku). Jesli pominiety, zwraca wszystkie rekordy.

    Zwraca JSON: {"fetched_at", "count", "items":[{...pelny rekord...}]}.

    Przykladowe wywolania MCP:
      { "name": "urlopy_list", "arguments": {} }
      { "name": "urlopy_list", "arguments": { "month": "2026-06" } }
    """
    t0 = time.perf_counter()
    print(f"[urlopy-mcp] urlopy_list start month={month!r}", file=sys.stderr, flush=True)
    try:
        payload = _load_payload()
        records = payload.get("records", [])

        if month:
            try:
                year, mon = int(month[:4]), int(month[5:7])
            except (ValueError, IndexError):
                return "BLAD: Nieprawidlowy format miesiaca. Uzyj YYYY-MM (np. 2026-06)."

            # Month range: first day to last day inclusive
            import calendar

            last_day = calendar.monthrange(year, mon)[1]
            month_start = date_type(year, mon, 1)
            month_end = date_type(year, mon, last_day)

            filtered = []
            for rec in records:
                start = _parse_date(rec.get("Pierwszy dzień urlopu"))
                end = _parse_date(rec.get("Ostatni dzień urlopu"))
                if start is None or end is None:
                    continue
                # Overlap: start <= month_end AND end >= month_start
                if start <= month_end and end >= month_start:
                    filtered.append(rec)
            records = filtered

        result = json.dumps(
            {
                "fetched_at": payload.get("fetched_at"),
                "count": len(records),
                "items": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        _tlog("urlopy_list done", t0, f"{len(records)} wynikow")
        return result
    except RuntimeError as exc:
        _tlog(f"urlopy_list error: {exc}", t0)
        return f"BLAD: {exc}"


if __name__ == "__main__":
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp

    run_mcp(mcp, default_port=MCP_PORTS["urlopy-bob"])
