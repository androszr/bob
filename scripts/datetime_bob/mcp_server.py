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
    """Zwroc aktualna date i czas w strefie Europe/Warsaw.

    Nie wymaga zadnych parametrow. Zwraca JSON z:
      iso        str  -- data w formacie YYYY-MM-DD
      weekday_pl str  -- nazwa dnia tygodnia po polsku (np. "Sobota")
      iso_week   int  -- numer tygodnia ISO (1-53)
      time_cet   str  -- aktualny czas w formacie HH:MM (CET/CEST)

    Przykladowe wywolanie MCP:
      { "name": "now", "arguments": {} }
    """
    t0 = time.perf_counter()
    print("[datetime-mcp] now start", file=sys.stderr, flush=True)
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        _CET = ZoneInfo("Europe/Warsaw")
        now_cet = datetime.now(_CET)
        d = now_cet.date()
        result = json.dumps(
            {
                "iso": d.isoformat(),
                "weekday_pl": resolver._DAY_LABELS_PL[d.weekday()],
                "iso_week": d.isocalendar().week,
                "time_cet": now_cet.strftime("%H:%M"),
            },
            ensure_ascii=False,
        )
        _tlog("now done", t0)
        return result
    except Exception as exc:  # noqa: BLE001
        _tlog(f"now error: {exc}", t0)
        return f"BLAD: {exc}"


@mcp.tool()
def resolve_date(expr: str) -> str:
    """Zamien polskie wyrazenie wzglednej daty na konkretna date ISO.

    expr: Wyrazenie daty po polsku, np.:
      "jutro", "wczoraj", "przedwczoraj", "pojutrze",
      "poniedzialek", "ubiegly wtorek", "przyszla sroda",
      "za 5 dni", "3 dni temu",
      "15.06", "15.06.2026", "2026-06-15"

    Zwraca JSON z:
      iso        str  -- YYYY-MM-DD
      weekday_pl str  -- polska nazwa dnia tygodnia
      is_past    bool -- czy data jest w przeszlosci wzgledem dzis
      is_future  bool -- czy data jest w przyszlosci wzgledem dzis

    Gdy wyrazenie jest nierozpoznane zwraca {"error": "BLAD: ..."}.

    ZASADA: dla wyrazen wzglednych (jutro, poniedzialek, ubiegly tydzien,
    za 3 dni) wywolaj NAJPIERW ten tool, a dopiero potem urlopy_check /
    list_oncall / sf_aggregates z uzyskanym YYYY-MM-DD.

    Przykladowe wywolanie MCP:
      { "name": "resolve_date", "arguments": { "expr": "przyszly poniedzialek" } }
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
        return json.dumps({"error": f"BLAD: {exc}"}, ensure_ascii=False)


@mcp.tool()
def weekday(iso_date: str) -> str:
    """Zwroc polska nazwe dnia tygodnia dla daty ISO.

    iso_date: Data w formacie YYYY-MM-DD.

    Zwraca JSON z:
      weekday_pl str  -- polska nazwa dnia tygodnia (np. "Poniedzialek")

    Przykladowe wywolanie MCP:
      { "name": "weekday", "arguments": { "iso_date": "2026-06-15" } }
    """
    t0 = time.perf_counter()
    print(f"[datetime-mcp] weekday start iso_date={iso_date!r}", file=sys.stderr, flush=True)
    try:
        name = resolver.weekday(iso_date)
        result = json.dumps({"weekday_pl": name}, ensure_ascii=False)
        _tlog("weekday done", t0)
        return result
    except Exception as exc:  # noqa: BLE001
        _tlog(f"weekday error: {exc}", t0)
        return json.dumps({"error": f"BLAD: {exc}"}, ensure_ascii=False)


@mcp.tool()
def date_range(expr: str) -> str:
    """Zamien wyrazenie zakresu dat na konkretne daty ISO.

    expr: Wyrazenie zakresu po polsku, np.:
      "ten tydzien", "ubiegly tydzien", "przyszly tydzien",
      "ten miesiac", "ubiegly miesiac", "przyszly miesiac",
      "za 5 dni", "3 dni temu"

    Zwraca JSON z:
      from_iso   str  -- YYYY-MM-DD (poczatek zakresu, wlacznie)
      to_iso     str  -- YYYY-MM-DD (koniec zakresu, wlacznie)
      month      str  -- "YYYY-MM" (tylko dla wyrazen miesiaca; uzyteczne dla
                         list_oncall(month=...))

    Gdy wyrazenie jest nierozpoznane zwraca {"error": "BLAD: ..."}.

    UWAGA: "ubiegly tydzien" dla sf_aggregates = od poniedzialku do niedzieli.
    Przekaz from_iso jako date_from i to_iso jako date_to.

    Przykladowe wywolanie MCP:
      { "name": "date_range", "arguments": { "expr": "ubiegly tydzien" } }
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
        return json.dumps({"error": f"BLAD: {exc}"}, ensure_ascii=False)


if __name__ == "__main__":
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp

    run_mcp(mcp, default_port=MCP_PORTS["datetime-bob"])
