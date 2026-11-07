# ruff: noqa: E501  -- data tables and page text, one row to a line
"""A made-up contact-centre group, "Acme": 100 sites, 43 queues, 60 days, generated from a fixed seed.

An engine suggests each day how many extra agents a queue needs at a site, and a team lead accepts or changes it.
Nothing here is derived from real data: apart from the UK city names and where those cities are, every value comes out of
the random generator below, in the same shape a real warehouse would hold. A fixed seed means the same rows every time, so the sample reports never change by accident.

    python3 scripts/snowflake_bob/demo_data.py --export DIR   # CSV files plus a DDL script, to load into a Snowflake trial
"""

from __future__ import annotations

import csv
import math
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 20261106
LAST_DAY = date(2026, 11, 6)
DAYS = 60
N_SITES = 100
STRETCH = 1.5           # a busy team takes this much more than the planned contacts per agent before contacts go unanswered

# (city, region, latitude, longitude): real UK cities, so the site card's map means something. Nothing about the sites is real.
CITIES = (("Glasgow", "R1", 55.86, -4.25), ("Manchester", "R2", 53.48, -2.24), ("Birmingham", "R3", 52.48, -1.90),
          ("Leeds", "R2", 53.80, -1.55), ("Bristol", "R4", 51.45, -2.59), ("Edinburgh", "R1", 55.95, -3.19),
          ("Cardiff", "R3", 51.48, -3.18), ("Newcastle", "R2", 54.98, -1.61), ("Belfast", "R1", 54.60, -5.93),
          ("Nottingham", "R3", 52.95, -1.15), ("Liverpool", "R2", 53.41, -2.98), ("London", "R4", 51.51, -0.13),
          ("Aberdeen", "R1", 57.15, -2.10), ("Plymouth", "R4", 50.38, -4.14), ("Norwich", "R3", 52.63, 1.30))
SEGMENTS = ("Customer care", "Technical", "Commercial", "Multilingual", "Outsourced")
STREETS = ("Market Street", "Station Road", "Park Lane", "Mill Road", "Church Street", "Bridge Street", "High Street")
PLANNERS = (("Priya Shah", "555 0101"), ("Tom Becker", "555 0102"))
WEEKDAY_LOAD = {0: 1.25, 1: 1.1, 5: 0.6, 6: 0.45}   # Monday is the busy day, the weekend the quiet one

# (group, subgroup, [(queue name, popularity, daily cost of one agent, contacts per agent a day, overtime-capped)])
CATALOG = (
    ("Billing", "Invoices", (("Invoice questions", 9, 120, 34, 0), ("Wrong amount", 6, 120, 30, 0), ("Duplicate charge", 5, 120, 32, 0))),
    ("Billing", "Payments", (("Failed payment", 9, 115, 38, 0), ("Refund status", 4, 115, 36, 0), ("Payment plan", 4, 130, 22, 0),
                             ("Direct debit setup", 5, 115, 34, 0))),
    ("Billing", "Credit", (("Credit check", 3, 145, 20, 0), ("Debt advice", 4, 150, 16, 0))),
    ("Commercial", "New customers", (("New contract", 6, 135, 22, 0), ("Price quote", 5, 125, 30, 0), ("Business enquiry", 3, 155, 18, 0))),
    ("Commercial", "Upgrades", (("Plan upgrade", 6, 130, 26, 0), ("Add-on offer", 3, 125, 28, 0), ("Device upgrade", 6, 135, 24, 0))),
    ("Commercial", "Renewals", (("Renewal offer", 3, 130, 24, 0), ("Win-back call", 2, 140, 18, 0))),
    ("Technical support", "Connectivity", (("No connection", 8, 160, 18, 1), ("Slow connection", 6, 155, 20, 1), ("Outage updates", 3, 140, 40, 1))),
    ("Technical support", "Devices", (("Router setup", 5, 150, 20, 1), ("Device swap", 6, 145, 24, 1), ("Phone setup", 3, 150, 22, 1))),
    ("Technical support", "Software", (("App sign-in", 6, 150, 28, 1), ("App errors", 7, 165, 16, 1))),
    ("Accounts", "Sign-in", (("Password reset", 8, 105, 48, 0), ("Locked account", 7, 105, 40, 0))),
    ("Accounts", "Profile", (("Address change", 6, 105, 42, 0), ("Name change", 4, 105, 38, 0), ("Close account", 2, 120, 26, 0))),
    ("Claims", "Damage", (("New damage claim", 8, 170, 14, 1), ("Claim status", 7, 145, 30, 1), ("Claim dispute", 4, 180, 10, 1))),
    ("Claims", "Escalations", (("Written complaint", 4, 175, 10, 1), ("Regulator reply", 3, 190, 6, 1))),
    ("Claims", "Payouts", (("Payout status", 8, 140, 32, 1), ("Payout error", 7, 165, 16, 1))),
    ("Back office", "Documents", (("Scanned forms", 1, 95, 60, 0), ("Identity documents", 1, 110, 44, 0))),
    ("Back office", "Data fixes", (("Duplicate record", 2, 110, 40, 0), ("Wrong contact details", 1, 100, 50, 0))),
    ("Live chat", "General", (("General chat", 8, 100, 56, 0), ("Chat handover", 7, 110, 44, 0))),
)

TABLES = {
    "SITES": ("SITE_ID", "REGION", "DISTRICT", "CITY", "ADDRESS", "TEAM_LEAD", "TEAM_LEAD_EMAIL", "PHONE", "PLANNER",
              "PLANNER_PHONE", "DIRECTOR", "SEGMENT", "SIZE_BAND", "OPENED_ON", "SEATS", "LAT", "LON",
              "NEW_FORECAST_SINCE"),
    "QUEUES": ("QUEUE_ID", "NAME", "QUEUE_GROUP", "SUBGROUP", "BLOCK_SIZE", "AGENT_COST"),
    "DAILY": ("BIZ_DAY", "SITE_ID", "QUEUE_ID", "FORECAST_FULL_DAY", "HANDLED", "ROSTERED_AM", "HAS_RUN"),
    "PLANS": ("BIZ_DAY", "SITE_ID", "MINUTES_TO_REVIEW"),
    "PLAN_LINES": ("BIZ_DAY", "SITE_ID", "QUEUE_ID", "SUGGESTED", "APPROVED", "OUTCOME", "REASON", "SUGGESTED_COST",
                   "APPROVED_COST", "KEY_QUEUE"),
}
_TEXT_COLUMNS = {"BIZ_DAY", "SITE_ID", "QUEUE_ID", "REGION", "DISTRICT", "CITY", "ADDRESS", "TEAM_LEAD", "TEAM_LEAD_EMAIL", "PHONE",
                 "PLANNER", "PLANNER_PHONE", "DIRECTOR", "SEGMENT", "SIZE_BAND", "OPENED_ON", "NEW_FORECAST_SINCE", "NAME",
                 "QUEUE_GROUP", "SUBGROUP", "OUTCOME", "REASON"}


def _size_band(seats: int) -> str:
    return "Under 80 seats" if seats < 80 else "80 to 120 seats" if seats <= 120 else "Over 120 seats"


def _sites(rng: random.Random) -> list[dict]:
    out = []
    for n in range(N_SITES):
        city, region, lat, lon = CITIES[n % len(CITIES)]
        size = rng.uniform(0.55, 1.6)
        seats = round(size * 100)
        since = (LAST_DAY - timedelta(days=rng.randint(8, 42))).isoformat() + " 05:15" if rng.random() < 0.7 else None
        out.append({"SITE_ID": f"S{1001 + n * 7}", "REGION": region, "DISTRICT": f"{region}.{1 + n % 3}",
                    "CITY": city, "ADDRESS": f"{rng.randint(1, 180)} {rng.choice(STREETS)}",
                    "TEAM_LEAD": f"Person {10 + n % 80:02d}", "TEAM_LEAD_EMAIL": f"site{1001 + n * 7}@example.com",
                    "PHONE": f"555 {1000 + n:04d}", "PLANNER": PLANNERS[n % 2][0], "PLANNER_PHONE": PLANNERS[n % 2][1],
                    "DIRECTOR": "Alex Morgan", "SEGMENT": SEGMENTS[n % len(SEGMENTS)],
                    "SIZE_BAND": _size_band(seats), "OPENED_ON": (date(2018, 1, 15) + timedelta(days=rng.randint(0, 2800))).isoformat(),
                    "SEATS": seats, "LAT": round(lat + (rng.uniform(0.12, 0.88) - .5) * .1, 3), "LON": round(lon + (rng.uniform(0.1, 0.9) - .5) * .1, 3),
                    "NEW_FORECAST_SINCE": since, "_size": size, "_keep": rng.uniform(0.55, 0.85)})
    return out


def _queues() -> list[dict]:
    out, k = [], 0
    for group, sub, rows in CATALOG:
        for name, pop, cost, per_agent, capped in rows:
            out.append({"QUEUE_ID": str(4011 + k * 37), "NAME": name, "QUEUE_GROUP": group, "SUBGROUP": sub,
                        "BLOCK_SIZE": 1 if pop <= 3 else 2 if pop <= 7 else 3, "AGENT_COST": cost, "_pop": pop,
                        "_per_agent": per_agent, "_capped": capped})
            k += 1
    return out


def _suggested_blocks(rng, start: int, *, quiet: bool, capped: bool) -> int:
    """What the engine asks for, in shift blocks: most days what is needed, sometimes cut or raised."""
    v = start
    if v > 0 and rng.random() < 0.06:
        v = 0
    if quiet and rng.random() < 0.45:
        v = 0
    if capped and v > 1 and rng.random() < 0.25:
        v -= 1
    if quiet and v > 0 and rng.random() < 0.2:
        v = 0
    if v == 0 and start > 0 and rng.random() < 0.08:
        v = 1
    return v


def generate() -> dict[str, list[dict]]:
    """All tables as row dicts. Deterministic: the same seed gives the same rows."""
    rng = random.Random(SEED)  # noqa: S311 -- made-up sample data, not security
    sites, queues = _sites(rng), _queues()
    first = LAST_DAY - timedelta(days=DAYS - 1)
    group_bias = {c[0]: rng.uniform(-0.12, 0.12) for c in CATALOG}
    daily, plans, lines = [], [], []
    for st in sites:
        since = (st["NEW_FORECAST_SINCE"] or "9999")[:10]
        keys = {q["QUEUE_ID"] for q in sorted(queues, key=lambda q: -q["_pop"] * rng.uniform(0.6, 1.3))[:14]}
        joining = {}      # yesterday's approved extra agents start today
        for offset in range(DAYS):
            day = first + timedelta(days=offset)
            iso = day.isoformat()
            wd = day.weekday()
            plans_today = wd < 5 or (wd == 5 and rng.random() < 0.08)
            if plans_today:
                plans.append({"BIZ_DAY": iso, "SITE_ID": st["SITE_ID"],
                              "MINUTES_TO_REVIEW": round(rng.lognormvariate(math.log(14), 0.45), 1)})
            for q in queues:
                if hash_keep(st["SITE_ID"], q["QUEUE_ID"]) > st["_keep"] + q["_pop"] * 0.03:
                    continue
                key, per_agent = q["QUEUE_ID"], q["_per_agent"]
                usual = q["_pop"] * 0.42 * st["_size"]
                load = WEEKDAY_LOAD.get(wd, 1.0)
                contacts = max(0.0, rng.gauss(usual * load, 0.9)) * per_agent
                new = iso >= since
                err = group_bias[q["QUEUE_GROUP"]] + rng.gauss(0, 0.16 if new else 0.3) + (0 if new else -0.1)
                fc = round(max(0.0, contacts * (1 + err)), 1)
                need = round(fc / per_agent, 2)
                rostered = max(0, round(rng.gauss(usual * load * 0.85, 0.7)))
                joined = joining.get(key, 0)
                absent = min(rostered, rng.randint(1, 2)) if rostered > 0 and rng.random() < 0.05 else 0
                capacity = (rostered + joined - absent) * per_agent * STRETCH
                handled = round(min(contacts, capacity))
                has_run = wd != 6
                daily.append({"BIZ_DAY": iso, "SITE_ID": st["SITE_ID"], "QUEUE_ID": key, "FORECAST_FULL_DAY": fc,
                              "HANDLED": handled, "ROSTERED_AM": rostered, "JOINING": joined, "ABSENT": absent,
                              "HAS_RUN": int(has_run)})
                incoming = 0
                block = q["BLOCK_SIZE"]
                if plans_today:
                    quiet = need < 0.7
                    start = math.ceil(max(0, need - rostered) / block)
                    suggested = _suggested_blocks(rng, start, quiet=quiet, capped=bool(q["_capped"])) * block
                    key_queue = int(key in keys)
                    outcome, approved, reason = _outcome(rng, suggested, block, key_queue, rostered, st["_keep"])
                    if outcome:
                        cost = q["AGENT_COST"]
                        lines.append({"BIZ_DAY": iso, "SITE_ID": st["SITE_ID"], "QUEUE_ID": key, "SUGGESTED": suggested,
                                      "APPROVED": approved, "OUTCOME": outcome, "REASON": reason,
                                      "SUGGESTED_COST": round(suggested * cost, 2), "APPROVED_COST": round(approved * cost, 2),
                                      "KEY_QUEUE": key_queue})
                    incoming = approved
                joining[key] = incoming
    return {"SITES": _public(sites), "QUEUES": _public(queues), "DAILY": daily, "PLANS": plans, "PLAN_LINES": lines}


def hash_keep(site: str, queue: str) -> float:
    """A stable 0..1 per site and queue: who serves what (Python's own hash() changes per process)."""
    return (int(site[1:]) * 2654435761 + int(queue) * 40_503) % 1000 / 1000


def _outcome(rng, suggested: int, block: int, key_queue: int, rostered: int, keep: float):
    """(outcome, approved agents, reason) of one line; (None, 0, None) when nothing happened to it."""
    if suggested > 0:
        r = rng.random()
        if r < keep:
            return "accepted", suggested, None
        if r < keep + 0.1:
            return "reduced", max(block, suggested - block), None
        if r < keep + 0.16:
            return "increased", suggested + block, None
        return "removed", 0, None
    if rng.random() < 0.05:
        return "added", block, None
    if key_queue and rng.random() < 0.6:
        return "skipped", 0, "no agents" if rostered <= 0 else "below pace" if rng.random() < 0.5 else "not needed"
    return None, 0, None


def _public(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


_cached: sqlite3.Connection | None = None


def database() -> sqlite3.Connection:
    """The data set in SQLite, built once per process."""
    global _cached  # noqa: PLW0603
    if _cached is None:
        con = sqlite3.connect(":memory:", check_same_thread=False)
        data = generate()
        for table, columns in TABLES.items():
            con.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
            marks = ",".join("?" * len(columns))
            con.executemany(f"INSERT INTO {table} VALUES ({marks})",  # noqa: S608 -- names from the constant above
                            [tuple(r[c] for c in columns) for r in data[table]])
        con.execute("CREATE INDEX i_daily ON DAILY (BIZ_DAY, SITE_ID, QUEUE_ID)")
        con.execute("CREATE INDEX i_daily_queue ON DAILY (QUEUE_ID, BIZ_DAY)")
        con.execute("CREATE INDEX i_lines ON PLAN_LINES (BIZ_DAY, SITE_ID, QUEUE_ID)")
        con.execute("CREATE INDEX i_lines_queue ON PLAN_LINES (QUEUE_ID, BIZ_DAY)")
        _cached = con
    return _cached


def ddl(schema: str = "DEMO.STAFFING") -> str:
    """CREATE TABLE statements for loading the exported CSV files into a Snowflake trial."""
    parts = [f"CREATE DATABASE IF NOT EXISTS {schema.split('.')[0]};", f"CREATE SCHEMA IF NOT EXISTS {schema};"]
    for table, columns in TABLES.items():
        cols = ", ".join(f"{c} {'VARCHAR' if c in _TEXT_COLUMNS else 'FLOAT'}" for c in columns)
        parts.append(f"CREATE OR REPLACE TABLE {schema}.{table} ({cols});")
    return "\n".join(parts) + "\n"


def export(folder: Path) -> list[Path]:
    """One CSV per table plus create-tables.sql."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    data = generate()
    for table, columns in TABLES.items():
        path = folder / f"{table}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(data[table])
        written.append(path)
    path = folder / "create-tables.sql"
    path.write_text(ddl(), encoding="utf-8")
    written.append(path)
    return written


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--export":
        sys.exit(__doc__)
    for written_path in export(Path(sys.argv[2])):
        print(written_path)
