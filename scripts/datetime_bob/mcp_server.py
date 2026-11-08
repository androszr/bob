"""MCP server exposing deterministic date/time resolution for Bob.

Start: python scripts/datetime_bob/mcp_server.py
Claude connects via stdio (managed automatically by claude --mcp-config).

Pure-compute — no network calls, no external dependencies beyond stdlib.
"""

import json
import sys
import time
from pathlib import Path

# Ensure logts and mcp_admin are importable from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 -- patches sys.stderr with CET timestamps

from mcp.server.fastmcp import FastMCP  # noqa: E402

import resolver  # noqa: E402

mcp = FastMCP("datetime-bob")


def _tlog(tag: str, t0: float, extra: str = "") -> None:
    ms = (time.perf_counter() - t0) * 1000
    suffix = f", {extra}" if extra else ""
    print(f"[datetime-mcp] {tag} ({ms:.0f}ms{suffix})", file=sys.stderr, flush=True)


@mcp.tool()
def now() -> str:
    """Return the current date and time in Bob's configured time zone.

    Takes no parameters. Returns JSON with:
      iso        str  -- date as YYYY-MM-DD
      weekday    str  -- weekday name in English (e.g. "Saturday")
      weekday_pl str  -- weekday name in Polish (e.g. "Sobota")
      iso_week   int  -- ISO week number (1-53)
      time_cet   str  -- current local time as HH:MM (configured time zone)

    Example MCP call:
      { "name": "now", "arguments": {} }
    """
    t0 = time.perf_counter()
    print("[datetime-mcp] now start", file=sys.stderr, flush=True)
    try:
        from datetime import datetime

        now_local = datetime.now(resolver._CET)
        d = now_local.date()
        result = json.dumps(
            {
                "iso": d.isoformat(),
                "weekday": resolver._DAY_LABELS[d.weekday()],
                "weekday_pl": resolver._DAY_LABELS_PL[d.weekday()],
                "iso_week": d.isocalendar().week,
                "time_cet": now_local.strftime("%H:%M"),
            },
            ensure_ascii=False,
        )
        _tlog("now done", t0)
        return result
    except Exception as exc:  # noqa: BLE001
        _tlog(f"now error: {exc}", t0)
        return f"ERROR: {exc}"


@mcp.tool()
def resolve_date(expr: str) -> str:
    """Turn a relative date expression into a concrete ISO date.

    expr: A date expression in English or Polish, e.g.:
      "today", "tomorrow", "yesterday", "day after tomorrow",
      "monday", "last tuesday", "next wednesday",
      "in 5 days", "3 days ago", "next business day",
      "jutro", "ubiegly wtorek", "za 5 dni",
      "15.06", "15.06.2026", "2026-06-15"

    A bare weekday, "this <weekday>" and "next <weekday>" are the nearest upcoming
    one; the Polish "przyszły <dzień>" is the one after that.

    Returns JSON with:
      iso        str  -- YYYY-MM-DD
      weekday    str  -- English weekday name
      weekday_pl str  -- Polish weekday name
      is_past    bool -- whether the date is before today
      is_future  bool -- whether the date is after today

    When the expression is not recognised, returns {"error": "ERROR: ..."}.

    RULE: for relative expressions (tomorrow, monday, last week, in 3 days) call
    this tool FIRST, and only then leave_check / list_oncall / sf_aggregates
    with the resulting YYYY-MM-DD.

    Example MCP call:
      { "name": "resolve_date", "arguments": { "expr": "next monday" } }
    """
    t0 = time.perf_counter()
    print(f"[datetime-mcp] resolve_date start expr={expr!r}", file=sys.stderr, flush=True)
    try:
        result_dict = resolver.resolve_date(expr)
        result = json.dumps(result_dict, ensure_ascii=False)
        _tlog("resolve_date done", t0)
        return result
    except Exception as exc:  # noqa: BLE001
        _tlog(f"resolve_date error: {exc}", t0)
        return json.dumps({"error": f"ERROR: {exc}"}, ensure_ascii=False)


@mcp.tool()
def weekday(iso_date: str) -> str:
    """Return the weekday name for an ISO date.

    iso_date: Date as YYYY-MM-DD.

    Returns JSON with:
      weekday    str  -- English weekday name (e.g. "Monday")
      weekday_pl str  -- Polish weekday name (e.g. "Poniedziałek")

    Example MCP call:
      { "name": "weekday", "arguments": { "iso_date": "2026-06-15" } }
    """
    t0 = time.perf_counter()
    print(f"[datetime-mcp] weekday start iso_date={iso_date!r}", file=sys.stderr, flush=True)
    try:
        name = resolver.weekday(iso_date)
        if name.startswith("ERROR:"):
            result = json.dumps({"error": name}, ensure_ascii=False)
        else:
            result = json.dumps(
                {"weekday": name, "weekday_pl": resolver.weekday_pl(iso_date)}, ensure_ascii=False
            )
        _tlog("weekday done", t0)
        return result
    except Exception as exc:  # noqa: BLE001
        _tlog(f"weekday error: {exc}", t0)
        return json.dumps({"error": f"ERROR: {exc}"}, ensure_ascii=False)


@mcp.tool()
def date_range(expr: str) -> str:
    """Turn a date range expression into concrete ISO dates.

    expr: A range expression in English or Polish, e.g.:
      "this week", "last week", "next week",
      "this month", "last month", "next month",
      "in 5 days", "3 days ago",
      "ten tydzien", "ubiegly miesiac"

    Returns JSON with:
      from_iso   str  -- YYYY-MM-DD (start of the range, inclusive)
      to_iso     str  -- YYYY-MM-DD (end of the range, inclusive)
      month      str  -- "YYYY-MM" (month expressions only; useful for
                         list_oncall(month=...))

    When the expression is not recognised, returns {"error": "ERROR: ..."}.

    NOTE: "last week" for sf_aggregates = Monday to Sunday.
    Pass from_iso as date_from and to_iso as date_to.

    Example MCP call:
      { "name": "date_range", "arguments": { "expr": "last week" } }
    """
    t0 = time.perf_counter()
    print(f"[datetime-mcp] date_range start expr={expr!r}", file=sys.stderr, flush=True)
    try:
        result_dict = resolver.resolve_range(expr)
        result = json.dumps(result_dict, ensure_ascii=False)
        _tlog("date_range done", t0)
        return result
    except Exception as exc:  # noqa: BLE001
        _tlog(f"date_range error: {exc}", t0)
        return json.dumps({"error": f"ERROR: {exc}"}, ensure_ascii=False)


if __name__ == "__main__":
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp

    run_mcp(mcp, default_port=MCP_PORTS["datetime-bob"])
