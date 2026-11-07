# ruff: noqa: E501  -- the page's own CSS / JS is embedded as one string, its lines are not Python
"""Network Panorama: one queue across the whole network, day by day.

Tiles (the last week against the first), one chart with three views (total / per site / site counts), tap or hover a
day for all its numbers, series switched on and off in the legend, 7 / 14 days / the whole period, a table by week or by
day with sortable columns.
"""

from __future__ import annotations

from datetime import date, timedelta

from snowflake_bob import page as pg
from snowflake_bob import queries

GREY, BLUE, GREEN, ROSTERED, RED, FORECAST = "#b9c2ca", "#2f6fdb", "#1f9d55", "#7a5af0", "#b81414", "#d98a00"
# (key, title, digits, unit, up is good, what it means)
TILES = (("sug_day", "Suggested / day", 0, " agents", True, "Extra agents the engine suggested across all plans, on average per day with plans."),
         ("appr_day", "Approved / day", 0, " agents", True, "Extra agents team leads approved, on average per day with plans."),
         ("handled_day", "Contacts / day", 0, "", True, "Contacts the network handled, on average per day."),
         ("rostered_site", "Rostered per site", 1, " agents", True, "Average morning roster at a site that had an engine run."),
         ("no_agents_pct", "Sites with nobody rostered", 0, "%", False, "Share of sites with a run that started the morning with nobody on the queue."),
         ("avg_sug", "Avg suggestion at a site", 1, " agents", True, "Average extra agents suggested to a site that got any suggestion."))
DAY_KEYS = {"sug": 0, "appr": 0, "handled": 0, "fc": 0, "sug_site": 2, "appr_site": 2, "handled_site": 1, "rostered_site": 1, "avg_sug": 1,
            "avg_appr": 1, "sites_sug": 0, "sites_appr": 0, "sites_handled": 0, "no_agents": 0, "no_agents_pct": 0, "sites_serving": 0,
            "sites_run": 0, "sites_plan": 0}


def collect(run, queue: str, end: str, days: int = 30) -> dict:
    start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
    card = run("queue_card", queue=queue)
    if not card:
        raise queries.BadParameter("queue: no such queue")
    rows = run("panorama_days", queue=queue, start=start, end=end)
    plans = {r["BIZ_DAY"]: r for r in run("panorama_plans", queue=queue, start=start, end=end)}
    out = []
    for r in rows:
        o = plans.get(r["BIZ_DAY"]) or {}
        out.append({"day": r["BIZ_DAY"], "handled": r["HANDLED"] or 0, "fc": r["FORECAST"] or 0, "sites_serving": r["SITES_SERVING"] or 0,
                    "sites_run": r["SITES_RUN"] or 0, "sites_handled": r["SITES_HANDLED"] or 0, "rostered": r["ROSTERED"] or 0,
                    "no_agents": r["NO_AGENTS"] or 0, "sites_plan": o.get("SITES_PLAN") or 0, "sug": o.get("SUGGESTED") or 0,
                    "appr": o.get("APPROVED") or 0, "sites_sug": o.get("SITES_SUGGESTED") or 0, "sites_appr": o.get("SITES_APPROVED") or 0})
    return {"queue": queue, "name": card[0]["NAME"] if card else f"Queue {queue}", "start": start, "end": end, "days": out}


def _div(a, b):
    return a / b if b else None


def _per_day(d: dict) -> dict:
    return {"sug_site": _div(d["sug"], d["sites_serving"]) if d["sites_plan"] else None,
            "appr_site": _div(d["appr"], d["sites_serving"]) if d["sites_plan"] else None,
            "handled_site": _div(d["handled"], d["sites_serving"]), "rostered_site": _div(d["rostered"], d["sites_run"]),
            "avg_sug": _div(d["sug"], d["sites_sug"]), "avg_appr": _div(d["appr"], d["sites_appr"]),
            "no_agents_pct": (d["no_agents"] / d["sites_run"] * 100) if d["sites_run"] else None}


def _block(days: list[dict]) -> dict:
    """Averages over some days; plan figures over the days that had plans."""
    if not days:
        return {}
    od = [d for d in days if d["sites_plan"]]
    total = lambda k, ds=days: sum(d[k] for d in ds)  # noqa: E731
    return {"sug_day": _div(total("sug", od), len(od)), "appr_day": _div(total("appr", od), len(od)),
            "handled_day": total("handled") / len(days), "fc_day": total("fc") / len(days),
            "rostered_site": _div(total("rostered"), total("sites_run")),
            "no_agents_pct": (total("no_agents") / total("sites_run") * 100) if total("sites_run") else None,
            "avg_sug": _div(total("sug", od), total("sites_sug", od)), "avg_appr": _div(total("appr", od), total("sites_appr", od)),
            "sites_serving": total("sites_serving") / len(days), "sites_sug": _div(total("sites_sug", od), len(od))}


def _tiles(days: list[dict]) -> list[dict]:
    a, b = _block(days[:7]), _block(days[-7:])
    apart = len(days) >= 14       # shorter, the first and last week are partly the same days: nothing to compare
    out = []
    for k, title, digits, unit, up_good, plain in TILES:
        va, vb = a[k], b[k]
        ch, kind = "–", "flat"
        if apart and va and vb is not None:
            change = (vb / va - 1) * 100
            ch = "= 0%" if round(abs(change)) == 0 else f'{"▲" if change > 0 else "▼"} {abs(change):.0f}%'
            kind = ("up" if (change > 0) == up_good else "down") if abs(change) >= 5 else "flat"
        out.append({"t": title, "v": f"{pg.num(vb, digits)}{unit}", "c": f"week 1: {pg.num(va, digits)}{unit}", "d": ch, "tone": kind, "tip": plain})
    return out


def _summary(days: list[dict]) -> list[dict]:
    a, b, t = _block(days[:7]), _block(days[-7:]), _block(days)
    s = lambda k: sum(d[k] for d in days)  # noqa: E731
    arrow = lambda k, dg=0: f"{pg.num(a[k], dg)} → {pg.num(b[k], dg)}"  # noqa: E731
    return [{"t": "Total in the period", "rows": [["Engine suggestions in plans", f"{pg.num(s('sug'))} agents"], ["Approved by team leads", f"{pg.num(s('appr'))} agents"],
                                                  ["Handled", f"{pg.num(s('handled'))} contacts"], ["Serving this queue (average)", f"{pg.num(t['sites_serving'])} sites"]]},
            {"t": "First → last week, per day", "rows": [["Engine suggestions, agents (plan days)", arrow("sug_day")], ["Approved by team leads, agents", arrow("appr_day")],
                                                         ["Contacts handled", arrow("handled_day")], ["Engine forecast, contacts", arrow("fc_day")]]},
            {"t": "Per site, first → last week", "rows": [["Rostered per site", f"{arrow('rostered_site', 1)} agents"], ["Sites with nobody rostered", f"{pg.num(a['no_agents_pct'])}% → {pg.num(b['no_agents_pct'])}%"],
                                                           ["Avg suggestion at a site with one", f"{arrow('avg_sug', 1)} agents"], ["Avg approval at a site that approved", f"{arrow('avg_appr', 1)} agents"]]}]


def _weeks(days: list[dict]) -> list[list[str]]:
    weeks: dict[tuple, list[dict]] = {}
    for d in days:
        iso = date.fromisoformat(d["day"]).isocalendar()
        weeks.setdefault((iso[0], iso[1]), []).append(d)
    rows = []
    for (_y, wn), ds in weeks.items():
        bl = _block(ds)
        rows.append([f"W{wn} · {pg.short_day(ds[0]['day'])} to {pg.short_day(ds[-1]['day'])}", pg.num(sum(x["sug"] for x in ds)),
                     pg.num(sum(x["appr"] for x in ds)), pg.num(sum(x["handled"] for x in ds)), pg.num(bl["fc_day"]), pg.num(bl["sites_sug"]),
                     pg.num(bl["avg_sug"], 1), pg.num(bl["avg_appr"], 1), pg.num(bl["rostered_site"], 1), f"{pg.num(bl['no_agents_pct'])}%"])
    return rows


def _days(days: list[dict]) -> list[dict]:
    """Per day: raw values (v, for the chart and sorting) and the same values formatted (f). Plan values only on a day with plans."""
    out = []
    for d in days:
        has = d["sites_plan"] > 0
        v = {**{k: d[k] for k in ("handled", "fc", "sites_handled", "no_agents", "sites_serving", "sites_run", "sites_plan")},
             **{k: d[k] if has else None for k in ("sug", "appr", "sites_sug", "sites_appr")}, **_per_day(d)}
        out.append({"d": d["day"], "few": d["sites_plan"] <= 20, "v": v, "f": {k: pg.num(v[k], DAY_KEYS[k]) for k in DAY_KEYS}})
    return out


def payload(w: dict) -> dict:
    days = w["days"]
    return {"queue": w["queue"], "tiles": _tiles(days) if days else [], "summary": _summary(days) if days else [], "weeks": _weeks(days),
            "days": _days(days), "c": {"grey": GREY, "blue": BLUE, "green": GREEN, "rostered": ROSTERED, "red": RED, "fc": FORECAST},
            "empty": "" if days else f"No data for queue {w['queue']} in this period."}


BODY = """
<div id="notice"></div>
<h2>The last week against the first</h2>
<div class="hint">The big number is the average of the last 7 days, under it the first week of the period. Green is a change
for the better, red for the worse. Tap a tile to see what it means.</div>
<div class="kpis k6" id="tiles"></div>
<h2>Day by day</h2>
<div class="tabs" id="view"><button class="on" data-k="total">Network total</button><button data-k="site">Per site</button>
<button data-k="sites">Site counts</button></div>
<div class="hint" id="v-hint"></div>
<div class="card"><div class="ctl"><div class="seg" id="range"></div></div>
<div class="legend" id="lg"></div><div id="chart"></div><div class="readout" id="ro"></div></div>
<h2>Period summary</h2>
<div class="grid3" id="sum"></div>
<h2>Table</h2>
<div class="seg" id="tbl"><button class="on" data-k="t">Weeks</button><button data-k="d">Days</button></div>
<div class="hint" id="t-hint"></div>
<div class="card"><div class="scroll" id="tab"></div></div>
<p class="foot">Source: Snowflake, PLAN_LINES (plans approved that day; suggestion = what the planning screen showed), DAILY (contacts,
morning roster and the first run's forecast for the day), QUEUES. Per site means per site serving the queue (roster: per site with an
engine run). Few sites plan on a Saturday, so daily averages count the days that had plans.</p>
"""

CSS = pg.CHART_CSS + """
.k6{grid-template-columns:repeat(2,minmax(0,1fr))}@media(min-width:720px){.k6{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:1000px){.k6{grid-template-columns:repeat(6,minmax(0,1fr))}}
.kpi[data-tip]{cursor:help}
#view{flex-wrap:wrap;overflow:visible}
.grid3{display:grid;grid-template-columns:minmax(0,1fr);gap:0 12px}@media(min-width:900px){.grid3{grid-template-columns:repeat(3,minmax(0,1fr))}}
"""

SCRIPT = r"""
const C = D.c, DD = D.days;
if (D.empty) $('#notice').innerHTML = `<div class="warn">${esc(D.empty)}</div>`;
$('#tiles').innerHTML = D.tiles.map(tl => `<div class="kpi" data-tip="<b>${tipEsc(tl.t)}</b><br>${tipEsc(tl.tip)}"><div class="t">${esc(tl.t)}</div>
  <div class="v num">${esc(tl.v)}</div><div class="d ${tl.tone}">${esc(tl.d)}</div><div class="c">${esc(tl.c)}</div></div>`).join('');
const kv = rows => '<dl class="kv">' + rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('') + '</dl>';
$('#sum').innerHTML = D.summary.map(bl => `<div class="card"><h3>${esc(bl.t)}</h3>${kv(bl.rows)}</div>`).join('');

// the chart: three views, each a set of panels; the readout lists the view's numbers of the tapped day
const VIEWS = {
  total: {hint: 'Across the whole network. Upper chart: extra agents the engine suggested and what team leads approved (plan days only). Lower chart: contacts handled and the engine forecast for the day.',
    panels: [{title: 'Extra agents, network total', h: 170, hm: 140, ser: [
      {k: 'sug', l: 'Engine suggestion in plan', c: C.grey, t: 'bar'}, {k: 'appr', l: 'Approved by team leads', c: C.blue, t: 'bar'}]},
      {title: 'Contacts, network total', h: 170, hm: 140, ser: [
      {k: 'handled', l: 'Contacts handled', c: C.green, t: 'line'}, {k: 'fc', l: 'Engine forecast for the day', c: C.fc, t: 'line', dash: 1}]}],
    extra: [['sites_serving', 'Sites serving it'], ['sites_plan', 'Sites that filed a plan']]},
  site: {hint: 'Average per site serving the queue (roster: per site with an engine run). It compares days whatever the number of sites.',
    panels: [{title: 'Extra agents per site', h: 150, hm: 130, ser: [
      {k: 'sug_site', l: 'Suggested / site', c: C.grey, t: 'line'}, {k: 'appr_site', l: 'Approved / site', c: C.blue, t: 'line'}]},
      {title: 'Contacts per site', h: 130, hm: 110, ser: [{k: 'handled_site', l: 'Handled / site', c: C.green, t: 'line'}]},
      {title: 'Morning roster per site (agents)', h: 130, hm: 110, ser: [{k: 'rostered_site', l: 'Rostered per site', c: C.rostered, t: 'line', dash: 1}]}],
    extra: [['avg_sug', 'Avg suggestion at a site with one'], ['avg_appr', 'Avg approval at a site that approved'], ['no_agents_pct', 'Sites with nobody rostered (%)']]},
  sites: {hint: 'How many sites got a suggestion, approved extra agents, handled contacts, and started the morning with nobody rostered. Below: average agents at a site with a suggestion or an approval.',
    panels: [{title: 'Number of sites', h: 220, hm: 180, ser: [
      {k: 'sites_sug', l: 'With a suggestion', c: C.grey, t: 'line'}, {k: 'sites_appr', l: 'Approved', c: C.blue, t: 'line'},
      {k: 'sites_handled', l: 'With contacts', c: C.green, t: 'line'}, {k: 'no_agents', l: 'Nobody rostered in the morning', c: C.red, t: 'line', dash: 1}]},
      {title: 'Average extra agents', h: 160, hm: 140, ser: [
      {k: 'avg_sug', l: 'Avg suggestion at a site with one', c: C.grey, t: 'bar'}, {k: 'avg_appr', l: 'Avg approval at a site that approved', c: C.blue, t: 'bar'}]}],
    extra: [['sites_serving', 'Sites serving it'], ['sites_run', 'Sites with an engine run']]}};
let view = 'total', days = DD.length, hover = null; const off = new Set();
const RANGES = [[7, '7 days'], [14, '14 days'], [DD.length, `Whole period (${DD.length} days)`]].filter(([k], i, list) => k <= DD.length && (i === list.length - 1 || k < DD.length));
$('#range').innerHTML = RANGES.map(([k, l]) => `<button data-k="${k}" class="${k === days ? 'on' : ''}">${esc(l)}</button>`).join('');
const allSer = v => VIEWS[v].panels.flatMap(pan => pan.ser);
function draw() {
  const dd = DD.slice(-days), V = VIEWS[view];
  if (!dd.length) { $('#chart').innerHTML = '<p class="hint">No days in this period.</p>'; return; }
  dayChart($('#chart'), {days: dd.map(day => ({d: day.d, shade: day.few})), panels: V.panels, off, hover,
    val: (i, k) => dd[i].v[k], pick: i => { hover = i; draw(); readout(dd[i]); }});
}
function readout(day) {
  if (!day) { $('#ro').innerHTML = '<span class="muted">Tap the chart or hover over a day to see its numbers.</span>'; return; }
  const V = VIEWS[view];
  $('#ro').innerHTML = `<b>${esc(dmy(day.d))}</b>${day.few ? ' <span class="badge warning">few plans</span>' : ''}<div class="g">`
    + allSer(view).map(one => `<span><span><span class="sw" style="background:${one.c}"></span>${esc(one.l)}</span><b>${esc(day.f[one.k])}</b></span>`).join('')
    + V.extra.map(([k, l]) => `<span><span class="muted">${esc(l)}</span><b>${esc(day.f[k])}</b></span>`).join('') + '</div>';
}
function setView(v) {
  view = v; hover = null; $('#v-hint').textContent = VIEWS[v].hint + (DD.some(day => day.few) ? ' A grey background marks a day with few plans (usually a weekend).' : '');
  legendButtons($('#lg'), allSer(v), off, draw); readout(null); draw();
}
tabs($('#view'), setView); tabs($('#range'), k => { days = +k; hover = null; readout(null); draw(); });
setView('total'); addEventListener('resize', draw);

// the table: by week, or every day with sortable columns
const WH = [['Week'], ['Suggested', 'agents'], ['Approved', 'agents'], ['Handled', 'contacts'], ['Forecast', 'contacts / day'],
  ['Sites with suggestion', 'per day'], ['Avg suggestion', 'agents'], ['Avg approval', 'agents'], ['Rostered per site', 'agents'], ['Nobody rostered', '% of sites']];
const DK = ['sug', 'appr', 'handled', 'fc', 'sites_sug', 'avg_sug', 'avg_appr', 'rostered_site', 'no_agents_pct'];
function drawTab(k) {
  if (k === 't') {
    $('#t-hint').textContent = 'Weekly totals (suggestions, approvals, contacts) and daily averages; W + the week number.';
    sortableTable($('#tab'), WH.map(([l, sub]) => ({l, sub, k: null})), D.weeks.map(row => ({c: row.map(esc)})));
  } else {
    $('#t-hint').textContent = 'Every day of the period. Tap a column heading to sort. Greyed out is a day with few plans.';
    sortableTable($('#tab'), [{l: 'Day', k: 0}, ...WH.slice(1).map(([l, sub], i) => ({l, sub: i === 3 ? 'contacts' : i === 4 ? 'count' : sub, k: i + 1}))],
      DD.map(day => ({cls: day.few ? 'few' : '', s: [day.d, ...DK.map(k => day.v[k])],
        c: [esc(dm(day.d)), ...DK.map(k => esc(day.f[k]) + (k === 'no_agents_pct' && day.v[k] != null ? '%' : ''))]})), {sk: 0, dir: -1});
  }
}
tabs($('#tbl'), drawTab); drawTab('t');
"""


def render(w: dict, demo: bool = False) -> str:
    days = w["days"]
    period = f"{pg.short_day(w['start'])} to {pg.long_day(w['end'])}"
    block = _block(days)
    chips = ((f"queue {w['queue']}", w["queue"]), period, f"{len(days)} days")
    if block.get("sites_serving"):
        chips += (f"served by {pg.num(block['sites_serving'])} sites on average",)
    header = pg.hero(w["name"], product="network-panorama", eyebrow="whole network, day by day", demo=demo, chips=chips,
                     lead="Engine suggestions in plans, what team leads approved, contacts and rosters: one queue at every site, day by day.")
    return pg.page(f"Queue {w['queue']} across the network, {period}", BODY, payload(w), pg.CHART_JS + SCRIPT, css=CSS, header=header)


def summary(w: dict) -> str:
    days = w["days"]
    if not days:
        return f"Queue {w['queue']}: no data in this period."
    b = _block(days[-7:])
    return (f"{w['name']} ({w['queue']}): last 7 days {pg.num(b['handled_day'])} contacts handled a day, {pg.num(b['no_agents_pct'])}% of sites with nobody "
            f"rostered in the morning, {pg.num(b['sites_serving'])} sites serve it.")
