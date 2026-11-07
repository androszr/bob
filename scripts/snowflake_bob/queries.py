"""The only SQL Bob can run. There is no free-form query tool: the model picks a report, never a statement.

Every statement is a single SELECT in the subset Snowflake and SQLite share, so the demo database runs exactly the text
a real warehouse runs. `{schema}` is the table prefix (`DEMO.STAFFING.` by default, empty on the demo database). Values
are checked, then passed as parameters (`:name`). render_sql() writes them into the text, after the same checks, only
for the `.sql` file an analyst re-runs by hand in a Snowsight worksheet.
"""

from __future__ import annotations

import re
from datetime import date

DEFAULT_SCHEMA = "DEMO.STAFFING"

_SITE = re.compile(r"[A-Z0-9-]{2,12}", re.A)
_QUEUE = re.compile(r"[0-9]{1,18}", re.A)
# real names have accents, commas and apostrophes; no backslash, no percent
_GROUP = re.compile(r"[\w ,'&/.-]{1,40}")
_SCHEMA = re.compile(r"[A-Za-z0-9_]{1,64}(\.[A-Za-z0-9_]{1,64})?", re.A)
_PLACEHOLDER = re.compile(r":([a-z_]+)\b")


class BadParameter(ValueError):
    """A value that does not have the shape its parameter demands; the message never echoes the value."""


def _check_site(v: str) -> str:
    v = str(v).strip().upper()
    if not _SITE.fullmatch(v):
        raise BadParameter("site: expected letters and digits, like S1042")
    return v


def _check_queue(v: str) -> str:
    v = str(v).strip()
    if not _QUEUE.fullmatch(v):
        raise BadParameter("queue: expected digits only")
    return v


def _check_group(v: str) -> str:
    v = str(v).strip()
    if not _GROUP.fullmatch(v):
        raise BadParameter("group: expected a group name of at most 40 characters")
    return v


def _check_day(v: str) -> str:
    v = str(v).strip()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise BadParameter("day: expected YYYY-MM-DD") from None


CHECKS = {"site": _check_site, "queue": _check_queue, "group": _check_group, "day": _check_day,
          "other_day": _check_day, "start": _check_day, "end": _check_day, "today": _check_day}

QUERIES: dict[str, str] = {
    # --- shared -------------------------------------------------------------------------------------------------------
    "latest_day": """
SELECT MAX(BIZ_DAY) AS LAST_DAY
FROM {schema}PLANS
WHERE BIZ_DAY < :today""",
    "groups": """
SELECT DISTINCT QUEUE_GROUP
FROM {schema}QUEUES
ORDER BY QUEUE_GROUP""",
    "site_card": """
SELECT SITE_ID, REGION, DISTRICT, CITY, ADDRESS, TEAM_LEAD, TEAM_LEAD_EMAIL, PHONE, PLANNER, PLANNER_PHONE, DIRECTOR,
       SEGMENT, SIZE_BAND, OPENED_ON, SEATS, LAT, LON, NEW_FORECAST_SINCE
FROM {schema}SITES
WHERE SITE_ID = :site""",
    "site_pulse": """
SELECT BIZ_DAY, OUTCOME, COUNT(*) AS LINES
FROM {schema}PLAN_LINES
WHERE SITE_ID = :site AND BIZ_DAY BETWEEN :start AND :end
GROUP BY BIZ_DAY, OUTCOME
ORDER BY BIZ_DAY""",
    "queue_card": """
SELECT QUEUE_ID, NAME, QUEUE_GROUP, SUBGROUP, BLOCK_SIZE, AGENT_COST
FROM {schema}QUEUES
WHERE QUEUE_ID = :queue""",
    # --- Morning Pulse ------------------------------------------------------------------------------------------------
    "pulse_lines": """
SELECT L.BIZ_DAY, I.QUEUE_GROUP, I.SUBGROUP, L.OUTCOME, COUNT(*) AS LINES,
       SUM(L.SUGGESTED_COST) AS SUGGESTED_COST, SUM(L.APPROVED_COST) AS APPROVED_COST
FROM {schema}PLAN_LINES L
JOIN {schema}QUEUES I ON I.QUEUE_ID = L.QUEUE_ID
WHERE L.BIZ_DAY IN (:day, :other_day)
GROUP BY L.BIZ_DAY, I.QUEUE_GROUP, I.SUBGROUP, L.OUTCOME
ORDER BY L.BIZ_DAY, I.QUEUE_GROUP, I.SUBGROUP, L.OUTCOME""",
    "pulse_plans": """
SELECT BIZ_DAY, SITE_ID, MINUTES_TO_REVIEW
FROM {schema}PLANS
WHERE BIZ_DAY IN (:day, :other_day)""",
    "pulse_queues": """
SELECT L.BIZ_DAY, L.QUEUE_ID, I.NAME, I.QUEUE_GROUP, L.OUTCOME, COUNT(*) AS SITES,
       SUM(CASE WHEN D.ROSTERED_AM <= 0 THEN 1 ELSE 0 END) AS NO_AGENTS
FROM {schema}PLAN_LINES L
JOIN {schema}QUEUES I ON I.QUEUE_ID = L.QUEUE_ID
LEFT JOIN {schema}DAILY D ON D.BIZ_DAY = L.BIZ_DAY AND D.SITE_ID = L.SITE_ID AND D.QUEUE_ID = L.QUEUE_ID
WHERE L.BIZ_DAY IN (:day, :other_day) AND L.OUTCOME IN ('removed', 'added')
GROUP BY L.BIZ_DAY, L.QUEUE_ID, I.NAME, I.QUEUE_GROUP, L.OUTCOME""",
    "pulse_key": """
SELECT L.BIZ_DAY, I.QUEUE_GROUP, I.SUBGROUP, L.QUEUE_ID, I.NAME, L.OUTCOME, L.REASON, COUNT(*) AS SITES,
       SUM(CASE WHEN D.ROSTERED_AM <= 0 THEN 1 ELSE 0 END) AS NO_AGENTS
FROM {schema}PLAN_LINES L
JOIN {schema}QUEUES I ON I.QUEUE_ID = L.QUEUE_ID
LEFT JOIN {schema}DAILY D ON D.BIZ_DAY = L.BIZ_DAY AND D.SITE_ID = L.SITE_ID AND D.QUEUE_ID = L.QUEUE_ID
WHERE L.BIZ_DAY IN (:day, :other_day) AND L.KEY_QUEUE = 1
GROUP BY L.BIZ_DAY, I.QUEUE_GROUP, I.SUBGROUP, L.QUEUE_ID, I.NAME, L.OUTCOME, L.REASON""",
    # --- Network Panorama ---------------------------------------------------------------------------------------------
    "panorama_days": """
SELECT D.BIZ_DAY, SUM(D.HANDLED) AS HANDLED, ROUND(SUM(D.FORECAST_FULL_DAY), 1) AS FORECAST, COUNT(*) AS SITES_SERVING,
       SUM(D.HAS_RUN) AS SITES_RUN, SUM(CASE WHEN D.HANDLED > 0 THEN 1 ELSE 0 END) AS SITES_HANDLED,
       SUM(CASE WHEN D.HAS_RUN = 1 THEN D.ROSTERED_AM ELSE 0 END) AS ROSTERED,
       SUM(CASE WHEN D.HAS_RUN = 1 AND D.ROSTERED_AM <= 0 THEN 1 ELSE 0 END) AS NO_AGENTS
FROM {schema}DAILY D
WHERE D.QUEUE_ID = :queue AND D.BIZ_DAY BETWEEN :start AND :end
GROUP BY D.BIZ_DAY
ORDER BY D.BIZ_DAY""",
    "panorama_plans": """
SELECT L.BIZ_DAY, COUNT(*) AS SITES_PLAN, SUM(L.SUGGESTED) AS SUGGESTED, SUM(L.APPROVED) AS APPROVED,
       SUM(CASE WHEN L.SUGGESTED > 0 THEN 1 ELSE 0 END) AS SITES_SUGGESTED,
       SUM(CASE WHEN L.APPROVED > 0 THEN 1 ELSE 0 END) AS SITES_APPROVED
FROM {schema}PLAN_LINES L
WHERE L.QUEUE_ID = :queue AND L.BIZ_DAY BETWEEN :start AND :end
GROUP BY L.BIZ_DAY
ORDER BY L.BIZ_DAY""",
    # --- Forecast vs Actual -------------------------------------------------------------------------------------------
    "forecast_rows": """
SELECT D.BIZ_DAY, D.QUEUE_ID, I.NAME, I.SUBGROUP, D.FORECAST_FULL_DAY, D.HANDLED, D.ROSTERED_AM
FROM {schema}DAILY D
JOIN {schema}QUEUES I ON I.QUEUE_ID = D.QUEUE_ID
WHERE D.SITE_ID = :site AND I.QUEUE_GROUP = :group AND D.HAS_RUN = 1 AND D.BIZ_DAY BETWEEN :start AND :end
ORDER BY D.BIZ_DAY, D.QUEUE_ID""",
}


def schema_prefix(schema: str | None) -> str:
    """`DEMO.STAFFING` -> `DEMO.STAFFING.`; anything but a plain dotted name is refused (it is pasted into SQL)."""
    schema = (schema or DEFAULT_SCHEMA).strip()
    if not _SCHEMA.fullmatch(schema):
        raise BadParameter("SNOWFLAKE_SCHEMA: expected DATABASE.SCHEMA")
    return schema + "."


def statement(query_id: str, prefix: str) -> str:
    """The statement with its table prefix, placeholders still `:name`."""
    return QUERIES[query_id].strip().replace("{schema}", prefix)


def placeholders(query_id: str) -> set[str]:
    return set(_PLACEHOLDER.findall(QUERIES[query_id]))


def validate(query_id: str, params: dict) -> dict:
    """Exactly the query's parameters, each normalised by its check. BadParameter otherwise."""
    wanted = placeholders(query_id)
    if set(params) != wanted:
        raise BadParameter(f"{query_id}: parameters must be {sorted(wanted)}")
    return {k: CHECKS[k](v) for k, v in params.items()}


def columns(query_id: str) -> list[str]:
    """The result's column names, read from the SELECT list: what a report's CSV heads with when it has no rows."""
    text = QUERIES[query_id].strip()
    select_list = text[len("SELECT"):text.index("\nFROM ")]
    parts, depth, cur = [], 0, ""
    for ch in select_list:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and not depth:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return [re.search(r"(\w+)\s*$", part).group(1) for part in [*parts, cur]]


def render_sql(query_id: str, params: dict, prefix: str) -> str:
    """The statement with its validated values written in, ready to paste into a Snowsight worksheet."""
    clean = validate(query_id, params)
    text = _PLACEHOLDER.sub(lambda m: "'" + clean[m.group(1)].replace("'", "''") + "'", statement(query_id, prefix))
    return text + ";"
