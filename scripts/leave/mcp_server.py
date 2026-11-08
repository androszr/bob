"""MCP server exposing the team's leave (vacation) data.

Start: python scripts/leave/mcp_server.py
Claude connects via stdio (managed automatically by claude --mcp-config).

Reads knowledge/leave/leave.json, written by sharepoint_poller.py. Older builds
wrote knowledge/urlopy/urlopy.json; that file is read when the new one is missing.
No network calls — pure local file read.
"""

import calendar
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

mcp = FastMCP("leave-bob")

_KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
# Source of truth for the data file location.
_KNOWLEDGE_DEST = _KNOWLEDGE_DIR / "leave" / "leave.json"
# Written by older builds; read only when _KNOWLEDGE_DEST is missing.
_LEGACY_DEST = _KNOWLEDGE_DIR / "urlopy" / "urlopy.json"

# Row keys: English canonical <- older Polish spellings still accepted on read.
_KEY_ALIASES = {
    "name": ("Imię i nazwisko", "Imie_i_nazwisko", "Imię_i_nazwisko"),
    "first_day": ("Pierwszy dzień urlopu", "Pierwszy_dzien_urlopu", "Pierwszy_dzień_urlopu"),
    "last_day": ("Ostatni dzień urlopu", "Ostatni_dzien_urlopu", "Ostatni_dzień_urlopu"),
    "email": ("Adres e-mail", "Adres_e-mail", "Adres_email"),
}


def _tlog(tag: str, t0: float, extra: str = "") -> None:
    ms = (time.perf_counter() - t0) * 1000
    suffix = f", {extra}" if extra else ""
    print(f"[leave-mcp] {tag} ({ms:.0f}ms{suffix})", file=sys.stderr, flush=True)


def _data_path() -> Path:
    """The new data file, or the legacy one when only that exists."""
    if not _KNOWLEDGE_DEST.exists() and _LEGACY_DEST.exists():
        return _LEGACY_DEST
    return _KNOWLEDGE_DEST


def normalize_record(rec: dict) -> dict:
    """Return a copy of `rec` with English keys; the English key wins when both exist."""
    out = dict(rec)
    for key, aliases in _KEY_ALIASES.items():
        for alias in aliases:
            if alias in out:
                value = out.pop(alias)
                out.setdefault(key, value)
    return out


def _load_payload() -> dict:
    """Load the JSON payload, with every record's keys normalised to English.

    Raises RuntimeError with a human-readable message on failure.
    """
    path = _data_path()
    if not path.exists():
        raise RuntimeError(
            f"Data file missing: {path}. Run sharepoint_poller.py to fetch the leave data."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read the leave file: {exc}") from exc
    payload["records"] = [
        normalize_record(r) if isinstance(r, dict) else r for r in payload.get("records", [])
    ]
    return payload


def _parse_date(value: str) -> date_type | None:
    """Parse an ISO date/datetime string to date. Returns None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None


@mcp.tool()
def leave_meta() -> str:
    """Check how fresh the team's leave data is.

    Takes no parameters. Returns JSON with:
      fetched_at:  str (ISO 8601) -- when the data was fetched from SharePoint
      count:       int -- number of leave records
      file_mtime:  str (ISO 8601) -- when the file on disk was last modified

    Recommendation: call it once per session before leave_check/leave_list
    to judge whether the data is fresh.

    Example MCP call:
      { "name": "leave_meta", "arguments": {} }
    """
    t0 = time.perf_counter()
    print("[leave-mcp] leave_meta start", file=sys.stderr, flush=True)
    try:
        payload = _load_payload()
        mtime = _data_path().stat().st_mtime
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
        _tlog("leave_meta done", t0)
        return result
    except RuntimeError as exc:
        _tlog(f"leave_meta error: {exc}", t0)
        return f"ERROR: {exc}"


@mcp.tool()
def leave_check(date: str) -> str:
    """Check who is on leave on a given day.

    date:  Required date in YYYY-MM-DD format (e.g. "2026-06-20").

    Returns the people for whom first_day <= date <= last_day.
    Each result holds: name, first_day, last_day.

    If nobody is on leave, returns JSON with count=0.

    Example MCP call:
      { "name": "leave_check", "arguments": { "date": "2026-06-20" } }
    """
    t0 = time.perf_counter()
    print(f"[leave-mcp] leave_check start date={date!r}", file=sys.stderr, flush=True)
    try:
        check_date = _parse_date(date)
        if check_date is None:
            return "ERROR: Invalid date format. Use YYYY-MM-DD (e.g. 2026-06-20)."

        payload = _load_payload()
        records = payload.get("records", [])

        matches = []
        for rec in records:
            start = _parse_date(rec.get("first_day"))
            end = _parse_date(rec.get("last_day"))
            if start is None or end is None:
                continue
            if start <= check_date <= end:
                matches.append(
                    {
                        "name": rec.get("name"),
                        "first_day": rec.get("first_day"),
                        "last_day": rec.get("last_day"),
                    }
                )

        result = json.dumps(
            {"date": date, "count": len(matches), "items": matches},
            ensure_ascii=False,
            indent=2,
        )
        _tlog("leave_check done", t0, f"{len(matches)} results")
        return result
    except RuntimeError as exc:
        _tlog(f"leave_check error: {exc}", t0)
        return f"ERROR: {exc}"


@mcp.tool()
def leave_list(month: str = "") -> str:
    """List all leave, optionally filtered by month.

    month: Optional YYYY-MM (e.g. "2026-06"). When given, returns only leave that
           overlaps that month (starts before the month ends AND ends after it
           begins). When omitted, returns every record.

    Returns JSON: {"fetched_at", "count", "items":[{...full record...}]}.

    Example MCP calls:
      { "name": "leave_list", "arguments": {} }
      { "name": "leave_list", "arguments": { "month": "2026-06" } }
    """
    t0 = time.perf_counter()
    print(f"[leave-mcp] leave_list start month={month!r}", file=sys.stderr, flush=True)
    try:
        payload = _load_payload()
        records = payload.get("records", [])

        if month:
            try:
                year, mon = int(month[:4]), int(month[5:7])
                last_day = calendar.monthrange(year, mon)[1]
            except (ValueError, IndexError, calendar.IllegalMonthError):
                return "ERROR: Invalid month format. Use YYYY-MM (e.g. 2026-06)."

            # Month range: first day to last day inclusive
            month_start = date_type(year, mon, 1)
            month_end = date_type(year, mon, last_day)

            filtered = []
            for rec in records:
                start = _parse_date(rec.get("first_day"))
                end = _parse_date(rec.get("last_day"))
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
        _tlog("leave_list done", t0, f"{len(records)} results")
        return result
    except RuntimeError as exc:
        _tlog(f"leave_list error: {exc}", t0)
        return f"ERROR: {exc}"


if __name__ == "__main__":
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp

    run_mcp(mcp, default_port=MCP_PORTS["leave-bob"])
