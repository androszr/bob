"""One report from request to files: run its predefined queries, build the page, write the page, the SQL and the CSV.

The `.sql` file holds every statement the report ran, with its values written in, so an analyst can paste it into a
Snowsight worksheet and get the same rows. The `.csv` file holds the report's main result.
"""

from __future__ import annotations

import csv
import io
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from snowflake_bob import backends, page, queries, report_forecast, report_panorama, report_pulse

REPORT_DIR = Path(__file__).resolve().parents[2] / "user-data" / "reports"
KINDS = {"morning-pulse": (report_pulse, "pulse_lines"),
         "network-panorama": (report_panorama, "panorama_days"),
         "forecast-vs-actual": (report_forecast, "forecast_rows")}
# Named people on the site card: off real reports unless BOB_REPORT_CONTACTS=1, as a page gets uploaded and forwarded.
CONTACT_FIELDS = ("TEAM_LEAD", "TEAM_LEAD_EMAIL", "PLANNER", "PLANNER_PHONE", "DIRECTOR")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """A spreadsheet runs a cell that starts like a formula, and warehouse text can start any way it likes."""
    return "'" + value if isinstance(value, str) and value.startswith(_FORMULA_START) else value


@dataclass
class Report:
    kind: str
    stem: str
    html: str
    sql: str
    csv: str
    summary: str
    demo: bool
    paths: dict = field(default_factory=dict)
    links: dict = field(default_factory=dict)


class Recorder:
    """Runs queries through the source and remembers each one for the `.sql` and `.csv` files."""

    def __init__(self, backend, contacts: bool = True):
        self.backend = backend
        self.contacts = contacts
        self.ran: list[tuple[str, dict, list[dict]]] = []

    def __call__(self, query_id: str, **params) -> list[dict]:
        rows = self.backend.run(query_id, params)
        if query_id == "site_card" and not self.contacts:
            rows = [{k: ("" if k in CONTACT_FIELDS else v) for k, v in row.items()} for row in rows]
        self.ran.append((query_id, params, rows))
        return rows

    def sql_text(self, title: str, demo: bool) -> str:
        prefix = self.backend.prefix or queries.schema_prefix(None)
        note = "-- Made-up demo data: on the demo database the table names have no prefix.\n" if demo else ""
        parts = [f"-- {title}\n-- Paste into a Snowsight worksheet: each statement runs on its own.\n{note}"]
        for query_id, params, _rows in self.ran:
            parts.append(f"-- {query_id}\n{queries.render_sql(query_id, params, prefix)}\n")
        return "\n".join(parts)

    def csv_text(self, query_id: str) -> str:
        rows = next((rows for qid, _p, rows in self.ran if qid == query_id), [])
        buf = io.StringIO()   # always a header: SharePoint refuses an empty file, and no rows is an answer too
        names = list(rows[0]) if rows else queries.columns(query_id)
        writer = csv.DictWriter(buf, fieldnames=names, lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: _csv_safe(v) for k, v in row.items()} for row in rows)
        return buf.getvalue()


def latest_day(backend) -> str:
    """The newest day with plans before today, which is not always yesterday: no plans on a Sunday, a late load, a
    trial account's fixed data. Yesterday when that cannot be read; the report itself then says what went wrong."""
    if getattr(backend, "is_demo", False):
        from snowflake_bob import demo_data  # noqa: PLC0415
        return demo_data.LAST_DAY.isoformat()
    try:
        rows = backend.run("latest_day", {"today": date.today().isoformat()})
    except backends.BackendError:
        rows = []
    return (rows and rows[0].get("LAST_DAY")) or (date.today() - timedelta(days=1)).isoformat()


def build(kind: str, backend, **params) -> Report:
    """The report. Raises queries.BadParameter on a bad value and backends.BackendError when the source fails."""
    module, main_query = KINDS[kind]
    # every value is checked before a report does anything with it, dates included
    params = {k: queries.CHECKS[k](v) if k in queries.CHECKS else v for k, v in params.items()}
    demo = bool(getattr(backend, "is_demo", False))
    rec = Recorder(backend, contacts=demo or backends.settings().get("BOB_REPORT_CONTACTS", "").strip() == "1")
    data = module.collect(rec, **params)
    stem = page.file_stem(kind, [str(v) for v in params.values()])
    return Report(kind=kind, stem=stem, html=module.render(data, demo=demo), summary=module.summary(data), demo=demo,
                  sql=rec.sql_text(page.PRODUCTS[kind], demo), csv=rec.csv_text(main_query))


def save(report: Report, folder: Path | None = None, now: datetime | None = None) -> dict:
    """Page, SQL and CSV side by side, owner-only: a real report holds real numbers. The timestamp comes first."""
    chosen = folder or backends.settings().get("BOB_REPORT_DIR")
    folder = Path(chosen or REPORT_DIR)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not chosen:    # mkdir's mode skips a folder that already exists; a chosen folder is the owner's call
        folder.chmod(0o700)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    paths = {}
    for ext, text in (("html", report.html), ("sql", report.sql), ("csv", report.csv)):
        path = folder / f"{stamp}_{report.stem}.{ext}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        paths[ext] = path
    report.paths = paths
    return paths


def _upload(path: Path) -> str | None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "teams_agent"))
    import sharepoint_upload  # noqa: PLC0415 -- it pulls in the Teams session code; only an upload needs it
    return sharepoint_upload.upload_file(path)


def deliver(report: Report, upload=None, env=None) -> dict:
    """Links for the saved files on SharePoint, the page's through the file viewer. Without a configured folder, or
    when an upload fails, that file simply has no link and stays on this machine; a failure never loses the report."""
    env = backends.settings() if env is None else env
    links: dict = {}
    if not env.get("SHAREPOINT_UPLOAD_FOLDER_URL", "").strip():
        report.links = links
        return links
    upload = upload or _upload
    for ext, path in report.paths.items():
        try:
            url = upload(path)
        except Exception as exc:  # noqa: BLE001 -- the local file is the fallback
            print(f"[snowflake-bob] upload failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            url = None
        if url:
            links[ext] = page.view_url(url) if ext == "html" else url
    report.links = links
    return links


def answer(report: Report) -> str:
    """What the tool returns to the model: the facts, then where the report is."""
    lines = []
    if report.demo:
        lines.append("DEMO: made-up data, not a real warehouse. Say so in the reply.")
    lines.append(report.summary)
    name = page.PRODUCTS[report.kind]
    if report.links.get("html"):
        lines.append(f"Report: [{name}]({report.links['html']})")
    elif report.paths.get("html"):
        lines.append(f"Report ({name}) saved on the owner's machine, no link: {report.paths['html'].name}")
    for ext, label in (("sql", "SQL, to re-run in Snowsight"), ("csv", "Result as CSV")):
        if report.links.get(ext):
            lines.append(f"{label}: {report.links[ext]}")
    return "\n".join(lines)
