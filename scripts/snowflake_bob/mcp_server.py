"""MCP server `snowflake-bob`: three reports from the warehouse, as interactive pages. There is no free-form SQL tool.

Start: python scripts/snowflake_bob/mcp_server.py [--stdio|--http]

Opt-in group "snowflake": a chat gets it only through a role or its own list, and it never reaches a chat flagged
external_chat (roles.ALWAYS_TEAM_ONLY). Runs on the demo database until Snowflake is configured
(docs/snowflake-reports.md).
"""

from __future__ import annotations

import functools
import sys
from datetime import date, timedelta
from pathlib import Path

import anyio.to_thread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 -- patches sys.stderr with CET timestamps

from mcp.server.fastmcp import FastMCP  # noqa: E402

from snowflake_bob import backends, queries, reports  # noqa: E402

mcp = FastMCP("snowflake-bob")
MAX_FORECAST_DAYS = 366


def _backend():
    """Factory (patched in tests)."""
    return backends.from_env()


def _tool(fn):
    """Registers a tool that runs in a worker thread and always answers: a report blocks for as long as its statements
    or a sign-in take, and the upload's cookie refresh cannot start a browser on the event loop's thread. Returns the
    plain function, which is what the tests call."""
    @functools.wraps(fn)
    def answering(*args, **kwargs) -> str:
        try:
            return fn(*args, **kwargs)
        except queries.BadParameter as exc:
            return f"ERROR: {exc}"
        except backends.BackendError as exc:
            return f"ERROR: Snowflake: {exc}"
        except Exception as exc:  # noqa: BLE001 -- the model and a scheduled job both act on the ERROR: prefix
            print(f"[snowflake-bob] {fn.__name__} failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            return f"ERROR: the report could not be built ({type(exc).__name__})"

    @functools.wraps(fn)
    async def threaded(*args, **kwargs) -> str:
        return await anyio.to_thread.run_sync(lambda: answering(*args, **kwargs))

    mcp.tool()(threaded)
    return answering


def _run(kind: str, backend, **params) -> str:
    try:
        report = reports.build(kind, backend, **params)
    finally:
        backend.close()       # a daemon lives on between reports; its warehouse session must not
    reports.save(report)
    reports.deliver(report)
    return reports.answer(report)


@_tool
def report_status() -> str:
    """Which data source the reports use, the demo database or Snowflake. For "where do these numbers come from?"."""
    backend = _backend()
    if backend.is_demo:
        return "DEMO: the reports use made-up data; Snowflake is not configured."
    return f"Snowflake: schema {backend.config.schema}."


@_tool
def report_morning_pulse(day: str = "") -> str:
    """Morning Pulse: how team leads treated the suggested staffing plans of one day across the whole network.

    day: YYYY-MM-DD; empty means the latest day."""
    backend = _backend()
    return _run("morning-pulse", backend, day=day or reports.latest_day(backend))


@_tool
def report_network_panorama(queue: str, days: int = 30, end: str = "") -> str:
    """Network Panorama: one queue across all sites, day by day.

    queue: queue number, digits only. days: length of the period, 7 to 60.
    end: YYYY-MM-DD; empty means the latest day."""
    backend = _backend()
    return _run("network-panorama", backend, queue=queue, end=end or reports.latest_day(backend),
                days=max(7, min(int(days), 60)))


@_tool
def report_forecast_vs_actual(site: str, group: str, start: str = "", end: str = "") -> str:
    """Forecast vs Actual: the engine's daily forecast against contacts handled, for one queue group at one site.

    site: site number, like S1042. group: queue group name; a wrong one gets the list of names.
    start, end: YYYY-MM-DD; empty means the latest 60 days."""
    backend = _backend()
    end = end or reports.latest_day(backend)
    try:
        start = start or (date.fromisoformat(end) - timedelta(days=59)).isoformat()
        too_long = (date.fromisoformat(end) - date.fromisoformat(start)).days > MAX_FORECAST_DAYS
    except ValueError:
        return "ERROR: start, end: expected YYYY-MM-DD"
    if too_long:
        return f"ERROR: start, end: at most {MAX_FORECAST_DAYS} days"
    return _run("forecast-vs-actual", backend, site=site, group=group, start=start, end=end)


if __name__ == "__main__":
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp

    run_mcp(mcp, default_port=MCP_PORTS["snowflake-bob"])
