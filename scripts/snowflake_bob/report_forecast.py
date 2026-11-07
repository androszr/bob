# ruff: noqa: E501  -- the page's own CSS / JS is embedded as one string, its lines are not Python
"""Forecast vs Actual: the engine's daily contact forecast against contacts handled, for one queue group at one site.

Totals, deviation and daily error (WAPE); the weakest weekday and the weekday most often with nobody rostered; tap a
weekday for its numbers; the day-by-day chart with a readout per day, 30 days or the whole period, series switched in
the legend; every queue of the group, by subgroup or as a searchable list below / above / all.
"""

from __future__ import annotations

from datetime import date

from snowflake_bob import page as pg
from snowflake_bob import queries
from snowflake_bob.report_panorama import BLUE, GREEN, GREY, RED, ROSTERED

ORANGE = "#d98a00"
TOP_MIN_CONTACTS = 200      # the "below / above contacts" lists: a queue with at least this many contacts handled in the period
NO_SUB = "(no subgroup)"
WD = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WD_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def collect(run, site: str, group: str, start: str, end: str) -> dict:
    if start > end:
        raise queries.BadParameter("start, end: the start is after the end")
    cards = run("site_card", site=site)
    if not cards:
        raise queries.BadParameter("site: no such site")
    known = [r["QUEUE_GROUP"] for r in run("groups") if r["QUEUE_GROUP"]]
    group = next((c for c in known if c.casefold() == group.casefold()), None)
    if group is None:
        raise queries.BadParameter("group: no such group; the groups are: " + ", ".join(known))
    return {"site": site, "group": group, "start": start, "end": end, "card": cards[0] if cards else None,
            "pulse_rows": run("site_pulse", site=site, start="2000-01-01", end=end) if cards else [],
            "rows": run("forecast_rows", site=site, group=group, start=start, end=end)}


def _pct(p: float, s: float) -> str:
    if not s:
        return "–"
    v = round((p - s) / s * 100)
    return f"{v:+d}%" if v else "0%"


def analyse(w: dict) -> dict:
    fc, handled, zero = {}, {}, {}
    queues: dict[str, dict] = {}
    wd: dict[int, dict] = {}
    for r in w["rows"]:
        d, f, s, nobody = r["BIZ_DAY"], r["FORECAST_FULL_DAY"] or 0, r["HANDLED"] or 0, (r["ROSTERED_AM"] or 0) <= 0
        fc[d], handled[d], zero[d] = fc.get(d, 0) + f, handled.get(d, 0) + s, zero.get(d, 0) + nobody
        it = queues.setdefault(r["QUEUE_ID"], {"nm": r["NAME"], "g": r["SUBGROUP"] or "", "p": 0.0, "s": 0.0, "z": 0, "days": 0})
        it["p"], it["s"], it["z"], it["days"] = it["p"] + f, it["s"] + s, it["z"] + nobody, it["days"] + 1
        k = date.fromisoformat(d).weekday()
        x = wd.setdefault(k, {"p": 0.0, "s": 0.0, "z": 0, "cnt": 0, "days": set()})
        x["p"], x["s"], x["z"], x["cnt"] = x["p"] + f, x["s"] + s, x["z"] + nobody, x["cnt"] + 1
        x["days"].add(d)
    return {"days": sorted(fc), "P": fc, "S": handled, "Z": zero, "queues": queues, "wd": wd}


def _groups(idx: list[dict]) -> list[dict]:
    """Subgroups, most contacts first: sums, the deviation as in the headline, and the error over its queues."""
    g: dict[str, dict] = {}
    for x in idx:
        a = g.setdefault(x["g"] or NO_SUB, {"g": x["g"] or NO_SUB, "p": 0.0, "s": 0.0, "err": 0.0, "n": 0, "z": 0, "below": 0, "above": 0})
        a["p"] += x["p"]
        a["s"] += x["s"]
        a["err"] += abs(x["p"] - x["s"])
        a["n"] += 1
        a["z"] += x["z"]
        if x["side"]:
            a[x["side"]] += 1
    out = []
    for a in sorted(g.values(), key=lambda a: (-a["s"], a["g"])):
        err = a.pop("err")
        out.append({**a, "p": round(a["p"]), "s": round(a["s"]), "pct": _pct(a["p"], a["s"]), "dev": round((a["p"] - a["s"]) / a["s"], 4) if a["s"] else None,
                    "wape": f"{err / a['s'] * 100:.0f}%" if a["s"] else "–"})
    return out


def payload(w: dict, since: str | None) -> dict:
    a = analyse(w)
    days, P, S, Z, wd = a["days"], a["P"], a["S"], a["Z"], a["wd"]
    if not days:
        return {"empty": "No engine runs for this group and period.", "kpis": [], "cards": [], "wd": [], "days": [], "idx": [], "subgroups": [], "groups": [],
                "min_s": TOP_MIN_CONTACTS, "c": {}, "since": since, "no_g": NO_SUB}
    tp, ts = sum(P.values()), sum(S.values())
    wape = sum(abs(P[d] - S[d]) for d in days) / ts * 100 if ts else 0
    z_avg = sum(x["z"] for x in wd.values()) / max(sum(x["cnt"] for x in wd.values()), 1) * 100
    per_day = {k: (x["p"] / len(x["days"]), x["s"] / len(x["days"])) for k, x in wd.items()}
    under = min(per_day, key=lambda k: (per_day[k][0] - per_day[k][1]) / max(per_day[k][1], 1))
    zero = max(wd, key=lambda k: wd[k]["z"] / max(wd[k]["cnt"], 1))
    week_s = sum(s for _, s in per_day.values())
    dev = (tp - ts) / ts if ts else 0
    kpis = [{"t": "Engine forecast (total)", "v": pg.num(tp), "c": "contacts, whole-day forecast, period total", "tip": "All the engine's whole-day forecasts added up: every queue, every day."},
            {"t": "Handled (total)", "v": pg.num(ts), "c": "contacts, on days with an engine run", "tip": "Contacts handled on the same queues on the same days."},
            {"t": "Forecast deviation", "v": _pct(tp, ts), "main": True, "tone": "down" if dev < -0.05 else "flat",
             "c": "forecast below contacts handled" if tp < ts else "forecast above contacts handled" if tp > ts else "level", "tip": "How far the total forecast is from the contacts handled. Minus means the engine forecast fewer than were handled."},
            {"t": "Daily error (WAPE)", "v": f"{wape:.0f}%", "c": "average miss day by day", "tip": "Sum of |forecast − handled| day by day, divided by contacts handled. It shows how far single days miss, even when the total agrees."},
            {"t": "Queues with nobody rostered", "v": f"{z_avg:.0f}% of days", "c": "on the first engine run of the day", "tip": "Share of queue-days that started with nobody rostered. Contacts handled are capped then, so demand may have been higher."}]
    pu, su = per_day[under]
    cards = [{"k": "red", "t": f"Weakest day: {WD[under]}", "v": _pct(pu, su), "p": f"forecast {pu:.0f} contacts against {su:.0f} handled a day.", "x": f"{WD[under]} is {su / max(week_s, 1) * 100:.0f}% of the group's weekly contacts."},
             {"k": "orange", "t": f"Nobody rostered in the morning: {WD[zero]}", "v": f"{wd[zero]['z'] / max(wd[zero]['cnt'], 1) * 100:.0f}%",
              "p": f"of queues start the morning run with nobody rostered (weekly average {z_avg:.0f}%). The forecast for this day is {_pct(*per_day[zero])} against contacts handled.", "x": ""}]
    wdays = []
    for k in range(7):
        if k not in per_day:
            continue
        p, s = per_day[k]
        d = (p - s) / s if s else 0
        wdays.append({"l": WD[k], "sh": WD_SHORT[k], "p": round(p), "s": round(s), "pct": _pct(p, s), "col": RED if d < -0.2 else BLUE if d > 0.2 else "#5c5c5c",
                      "n": len(wd[k]["days"]), "z": round(wd[k]["z"] / max(wd[k]["cnt"], 1) * 100), "share": round(s / max(week_s, 1) * 100), "under": k == under, "zero": k == zero})
    day_rows = [{"d": d, "p": round(P[d], 1), "s": S[d], "z": Z[d], "pct": _pct(P[d], S[d])} for d in days]
    idx = []
    for key, v in sorted(a["queues"].items(), key=lambda kv: -kv[1]["s"]):
        side = ("below" if v["p"] < v["s"] else "above" if v["p"] > v["s"] else "") if v["s"] >= TOP_MIN_CONTACTS else ""
        idx.append({"i": key, "nm": v["nm"], "g": v["g"], "p": round(v["p"]), "s": round(v["s"]), "z": v["z"], "days": v["days"], "pct": _pct(v["p"], v["s"]),
                    "dev": round((v["p"] - v["s"]) / v["s"], 4) if v["s"] else None, "side": side})
    return {"empty": "", "kpis": kpis, "cards": cards, "wd": wdays, "days": day_rows, "idx": idx, "since": since, "subgroups": sorted({x["g"] for x in idx if x["g"]}), "groups": _groups(idx),
            "min_s": TOP_MIN_CONTACTS, "no_g": NO_SUB, "c": {"grey": GREY, "green": GREEN, "rostered": ROSTERED, "red": RED, "blue": BLUE, "orange": ORANGE}}


BODY = """
<div id="notice"></div>
<div class="kpis k5" id="kpis"></div>
<div class="grid2" id="cards"></div>
<h2>Average by day of the week</h2>
<div class="hint">Grey bar: engine forecast for the day. Green: contacts handled (average). Under the day: how far the forecast is from contacts handled,
red when more than 20% too low, blue when more than 20% too high. Tap a day for details.</div>
<div class="card"><div class="wdc" id="wd"></div><div class="det" id="wd-det"></div></div>
<h2>Day by day</h2>
<div class="hint" id="d-hint"></div>
<div class="card"><div class="seg" id="range"></div><div class="legend" id="lg"></div><div id="chart"></div>
<div class="readout" id="ro"></div></div>
<h2>Queues in this group</h2>
<div class="seg" id="mode"><button class="on" data-k="grp">By subgroup</button><button data-k="list">Queue list</button></div>
<div class="hint" id="i-hint"></div>
<div id="grp-box"><div class="card"><input class="search" id="gq" placeholder="Search: subgroup, queue number or name" type="search">
<div class="rows" id="grp"></div></div></div>
<div id="list-box" hidden>
<div class="seg" id="side"><button class="on" data-k="below">Forecast below contacts</button><button data-k="above">Forecast above contacts</button>
<button data-k="all">All</button></div>
<div class="card"><div class="filters"><input class="search" id="q" placeholder="Search: queue number or name" type="search">
<select id="g"></select></div><div class="list" id="idx"></div><div id="more"></div></div></div>
<p class="foot">Source: Snowflake, DAILY (the first run's forecast for the whole day, morning roster, contacts handled) and QUEUES. Only
days with an engine run for the queue. Contacts with nobody rostered are capped, so real demand may be higher.</p>
"""

CSS = pg.CHART_CSS + """
.k5{grid-template-columns:repeat(2,minmax(0,1fr))}@media(min-width:720px){.k5{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:1000px){.k5{grid-template-columns:repeat(5,minmax(0,1fr))}}
.k5 .kpi:last-child:nth-child(odd){grid-column:1/-1}@media(min-width:720px){.k5 .kpi:last-child:nth-child(odd){grid-column:auto}}
.kpi[data-tip]{cursor:help}
.grid2{display:grid;grid-template-columns:minmax(0,1fr);gap:0 12px}@media(min-width:820px){.grid2{grid-template-columns:1fr 1fr}}
.hl{display:flex;gap:14px;align-items:flex-start;border-left:4px solid var(--c)}.hl .big{font-size:30px;font-weight:700;
color:var(--c);letter-spacing:-.5px;line-height:1.1;white-space:nowrap}.hl h3{margin:0 0 2px}.hl p{margin:0;font-size:13px;color:var(--ink2)}
.hl p i{color:var(--ink3)}
.wdc{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:4px;align-items:end}
.wdc .col{display:flex;flex-direction:column;align-items:stretch;cursor:pointer;border-radius:10px;padding:4px 2px 6px;min-width:0}
.wdc .col:hover,.wdc .col.on{background:#f3f6f8}.wdc .col.on{box-shadow:inset 0 0 0 1.5px #123326}
.wdc .bars{display:flex;justify-content:center;align-items:flex-end;gap:3px;height:170px}
.wdc .bars span{display:flex;flex-direction:column;justify-content:flex-end;align-items:center;width:min(30px,42%);height:100%}
.wdc .bars span b{font-size:10.5px;font-weight:600;color:#333;margin-bottom:2px;font-variant-numeric:tabular-nums}
.wdc .bars span i{display:block;width:100%;border-radius:4px 4px 0 0;min-height:2px}
.wdc .lb{text-align:center;font-size:12.5px;margin-top:6px;border-top:1px solid #d5d9de;padding-top:5px;white-space:nowrap}
.wdc .lb .s{display:none}.wdc .pc{text-align:center;font-size:11.5px;font-weight:700;white-space:nowrap}
.wdc .z{text-align:center;font-size:11px;color:var(--ink3);white-space:nowrap}
@media(max-width:640px){.wdc .lb .l{display:none}.wdc .lb .s{display:inline}.wdc .bars{height:140px}.wdc .bars span b{font-size:9.5px}
.wdc .z{font-size:10px;line-height:1.25}.wdc .z span{display:block}.wdc{gap:2px}}
.det{margin-top:10px;background:#f7f9fa;border-radius:10px;padding:10px 12px;font-size:13.5px;min-height:44px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px}.filters .search{flex:1 1 220px;width:auto}
.filters select{font:inherit;flex:1 1 180px;min-width:0;border:1px solid var(--line);border-radius:10px;padding:8px 10px;
min-height:38px;background:var(--card);color:var(--ink)}
.list .it{cursor:pointer}.list .it .pb{display:flex;flex-direction:column;gap:2px;margin-top:5px;max-width:260px}
.list .it .pb i{display:block;height:6px;border-radius:3px;min-width:2px}
.list .it .x{display:none;font-size:12.5px;color:var(--ink2);margin-top:4px}.list .it.open .x{display:block}
.rows .row .l .chev{display:inline-block;color:var(--ink3);font-weight:400;margin-right:4px;transition:transform .15s}
.rows .row.open .l .chev{transform:rotate(90deg)}.rows .row .m{grid-column:1/-1;font-size:12.5px;color:var(--ink2);min-width:0;overflow-wrap:anywhere}
.rows .row .pb,.prods .pr .pb{display:flex;flex-direction:column;gap:2px;margin-top:3px}.rows .row .pb i{display:block;height:6px;border-radius:3px;min-width:2px}
.rows .row .x span{display:inline-block;margin-right:12px}
.rows .row .prods{grid-column:1/-1;display:none;margin:8px 0 2px;padding:6px 0 0 10px;border-left:3px solid #D6EFDC;min-width:0}
.rows .row.open .prods{display:block}.prods .ph{font-size:12px;color:var(--ink2);margin-bottom:2px}
.prods .pr{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;padding:7px 0;border-top:1px solid var(--line);
cursor:pointer}.prods .pr .l{font-weight:600;font-size:13px;min-width:0;overflow-wrap:anywhere}.prods .pr .r{text-align:right;white-space:nowrap}
.prods .pr .m{grid-column:1/-1;font-size:12px;color:var(--ink2)}.prods .pr .b{grid-column:1/-1}
.rows .row.open .prods .pr .x{grid-column:1/-1;display:none;font-size:12px;color:var(--ink2)}
.rows .row.open .prods .pr.open .x{display:flex;flex-wrap:wrap;gap:2px 12px}
#gq{margin-bottom:4px}
.list .it .n .pc{font-size:15px}.more{font:inherit;width:100%;margin-top:8px;border:1px solid var(--line);background:var(--soft);
border-radius:10px;min-height:38px;cursor:pointer;color:var(--ink)}
"""

SCRIPT = r"""
const C = D.c;
if (D.empty) $('#notice').innerHTML = `<div class="warn">${esc(D.empty)}</div>`;
$('#kpis').innerHTML = D.kpis.map(kp => `<div class="kpi${kp.main ? ' main' : ''}" data-tip="<b>${tipEsc(kp.t)}</b><br>${tipEsc(kp.tip)}"><div class="t">${esc(kp.t)}</div>
  <div class="v num ${kp.tone || ''}">${esc(kp.v)}</div><div class="c">${esc(kp.c)}</div></div>`).join('');
$('#cards').innerHTML = D.cards.map(cd => `<div class="card hl" style="--c:${C[cd.k]}"><div class="big">${esc(cd.v)}</div>
  <div><h3>${esc(cd.t)}</h3><p>${esc(cd.p)}</p>${cd.x ? `<p><i>${esc(cd.x)}</i></p>` : ''}</div></div>`).join('');

// weekdays: two bars per day, tap for the numbers
if (D.wd.length) {
  const vmax = niceMax(Math.max(...D.wd.flatMap(it => [it.p, it.s])) * 1.12);
  $('#wd').style.setProperty('--n', D.wd.length);
  $('#wd').innerHTML = D.wd.map((it, i) => `<div class="col" data-i="${i}">
    <div class="bars"><span><b>${it.p}</b><i style="height:${it.p / vmax * 100}%;background:${C.grey}"></i></span>
    <span><b>${it.s}</b><i style="height:${it.s / vmax * 100}%;background:${C.green}"></i></span></div>
    <div class="lb"><span class="l">${esc(it.l)}</span><span class="s">${esc(it.sh)}</span></div>
    <div class="pc" style="color:${it.col}">${esc(it.pct)}</div><div class="z"><span>empty:</span> <span>${it.z}%</span></div></div>`).join('');
  const det = i => { const it = D.wd[i];
    $$('#wd .col').forEach(col => col.classList.toggle('on', +col.dataset.i === i));
    $('#wd-det').innerHTML = `<b>${esc(it.l)}</b> · average of ${it.n} days<br>engine forecast <b>${fx(it.p)}</b> contacts, handled <b>${fx(it.s)}</b>: forecast <b style="color:${it.col}">${esc(it.pct)}</b> against contacts handled`
      + `<br><span class="muted">${it.share}% of the group's weekly contacts · ${it.z}% of queues with nobody rostered in the morning${it.under ? ' · the weakest forecast day' : ''}${it.zero ? ' · the day most often with nobody rostered' : ''}</span>`; };
  $$('#wd .col').forEach(col => col.addEventListener('click', () => det(+col.dataset.i)));
  det(Math.max(0, D.wd.findIndex(it => it.under)));
}

// day by day
const DD = D.days;
if (DD.length) {
  $('#d-hint').textContent = `${dmy(DD[0].d)} to ${dmy(DD[DD.length - 1].d)} · upper chart: contacts handled and the engine forecast for the day (all queues of the group), lower chart: how many queues started the morning with nobody rostered. Tap or hover over a day.`;
  const PANELS = [{title: 'contacts / day', h: 260, hm: 210, ser: [{k: 's', l: 'Contacts handled', c: C.green, t: 'line'},
    {k: 'p', l: 'Engine forecast for the day', c: C.rostered, t: 'line', dash: 1}]},
    {title: 'queues with nobody rostered', h: 130, hm: 110, ser: [{k: 'z', l: 'Queues with nobody rostered on the first run', c: C.red, t: 'bar'}]}];
  const off = new Set(); let days = DD.length, hover = null;
  const RANGES = [[30, '30 days'], [DD.length, `Whole period (${DD.length} days)`]].filter(([k], i, list) => i === list.length - 1 || k < DD.length);
  $('#range').innerHTML = RANGES.map(([k, l]) => `<button data-k="${k}" class="${k === days ? 'on' : ''}">${esc(l)}</button>`).join('');
  // the new-forecasts boundary on both panels, its label only on the top one
  const NW = D.since || '';
  const mark = dd => mk => { const sd = NW.slice(0, 10), k = dd.findIndex(day => day.d >= sd);
    const pos = k < 0 ? null : mk.left + mk.step * k;
    const box = {since: NW, first: dd[0].d, last: dd[dd.length - 1].d, x: pos, left: mk.left, right: mk.right, top: mk.pi ? mk.top : mk.top - 22, bot: mk.bot, mob: mk.mob};
    return mk.pi ? forecastBand(box) : forecastMark(box); };
  const draw = () => { const dd = DD.slice(-days);
    dayChart($('#chart'), {days: dd.map(day => ({d: day.d, shade: false})), panels: PANELS, off, hover,
      mark: NW ? mark(dd) : null, val: (i, k) => dd[i][k], pick: i => { hover = i; draw(); readout(dd[i]); }}); };
  const readout = day => {
    if (!day) { $('#ro').innerHTML = '<span class="muted">Tap the chart or hover over a day to see its numbers.</span>'; return; }
    const off2 = day.s ? (day.p - day.s) / day.s : 0;
    $('#ro').innerHTML = `<b>${esc(dmy(day.d))}</b><div class="g">
      <span><span><span class="sw" style="background:${C.green}"></span>Handled</span><b>${fx(day.s)} contacts</b></span>
      <span><span><span class="sw" style="background:${C.rostered}"></span>Engine forecast for the day</span><b>${fx(day.p)} contacts</b></span>
      <span><span class="muted">Forecast against contacts handled</span><b style="color:${off2 < -.2 ? C.red : off2 > .2 ? C.blue : 'inherit'}">${esc(day.pct)}</b></span>
      <span><span><span class="sw" style="background:${C.red}"></span>Queues with nobody rostered</span><b>${fx(day.z)}</b></span></div>`;
  };
  legendButtons($('#lg'), PANELS.flatMap(pan => pan.ser), off, draw);
  tabs($('#range'), k => { days = +k; hover = null; readout(null); draw(); });
  readout(null); draw(); addEventListener('resize', draw);
}

// queues: below / above contacts or all, search, subgroup
const HINTS = {below: `Queues whose total forecast was below contacts handled (at least ${D.min_s} handled), biggest gap first.`,
  above: `Queues whose total forecast was above contacts handled (at least ${D.min_s} handled), biggest gap first.`,
  all: 'Every queue of the group with an engine run, most contacts first.'};
let side = 'below', limit = 15;
$('#g').innerHTML = '<option value="">All subgroups</option>' + D.subgroups.map(gr => `<option>${esc(gr)}</option>`).join('');
const devCol = dv => dv == null ? 'inherit' : dv < -.2 ? C.red : dv > .2 ? C.blue : 'inherit';
const pbar = (it, nm) => `<div class="pb" data-tip="${esc(`<b>${esc(nm)}</b><br>forecast ${fx(it.p)} · handled ${fx(it.s)} contacts`)}"><i style="width:${it.p / Math.max(it.p, it.s, 1) * 100}%;background:${C.grey}"></i><i style="width:${it.s / Math.max(it.p, it.s, 1) * 100}%;background:${C.green}"></i></div>`;
function drawIdx() {
  const q = $('#q').value.trim().toLowerCase(), gr = $('#g').value;
  let rows = D.idx.filter(it => (side === 'all' || it.side === side) && (!gr || it.g === gr) && (!q || (it.i + ' ' + it.nm).toLowerCase().includes(q)));
  if (side === 'below') rows.sort((aa, bb) => aa.dev - bb.dev); else if (side === 'above') rows.sort((aa, bb) => bb.dev - aa.dev); else rows.sort((a, b) => b.s - a.s);
  $('#i-hint').textContent = HINTS[side] + ' Bars: grey = forecast, green = handled; on the right forecast / handled in contacts. Tap a row for details.';
  $('#idx').innerHTML = rows.length ? rows.slice(0, limit).map((it, n) => `<div class="it"><span class="o">${n + 1}.</span><span><span class="nm">${esc(it.nm)}</span>
    <div class="meta">${esc(it.i)}${cp(it.i)}${it.g ? ' · ' + esc(it.g) : ''}</div>${pbar(it, it.nm)}
    <div class="x">Forecast ${fx(it.p)}, handled ${fx(it.s)} contacts over ${it.days} days with a run · average ${fx(it.p / Math.max(it.days, 1), 1)} against ${fx(it.s / Math.max(it.days, 1), 1)} a day · nobody rostered on ${it.z} days</div></span>
    <span class="n"><span class="pc" style="color:${devCol(it.dev)}">${esc(it.pct)}</span><small class="muted">${fx(it.p)} / ${fx(it.s)}</small><small class="muted">empty: ${it.z} days</small></span></div>`).join('')
    : '<p class="hint">No queues for this choice.</p>';
  $('#more').innerHTML = rows.length > limit ? `<button class="more" type="button">Show all (${rows.length})</button>` : '';
  if (rows.length > limit) $('#more button').addEventListener('click', () => { limit = 1e9; drawIdx(); });
  $$('#idx .it').forEach(el => el.addEventListener('click', ev => { if (!ev.target.closest('[data-tip],[data-copy]')) el.classList.toggle('open'); }));
}
tabs($('#side'), k => { side = k; limit = 15; drawIdx(); });
function drawGrp() {
  const q = $('#gq').value.trim().toLowerCase();
  const rows = D.groups.map(gr => { const all = D.idx.filter(it => (it.g || D.no_g) === gr.g).sort((a, b) => b.s - a.s);
    const own = gr.g.toLowerCase().includes(q);
    return {gr, own, queues: !q || own ? all : all.filter(it => (it.i + ' ' + it.nm).toLowerCase().includes(q))}; })
    .filter(row => !q || row.own || row.queues.length);
  $('#i-hint').textContent = 'Subgroups, most contacts first: total forecast and contacts handled, deviation as in the header; error = sum of |forecast − handled| of its queues over its contacts. Bar: grey = forecast, green = handled. Tap a subgroup for its queues, a queue for details.';
  $('#grp').innerHTML = rows.length ? rows.map(({gr, own, queues}) => `<div class="row${q && !own ? ' open' : ''}" data-row>
    <span class="l"><span class="chev">›</span>${esc(gr.g)}</span><span class="r"><b class="num" style="color:${devCol(gr.dev)}">${esc(gr.pct)}</b></span>
    <span class="m">${gr.n} ${gr.n === 1 ? 'queue' : 'queues'} · forecast ${fx(gr.p)} / handled ${fx(gr.s)} contacts · queue error (WAPE) ${esc(gr.wape)}</span>
    <span class="b">${pbar(gr, gr.g)}</span>
    <span class="x"><span>below contacts: ${gr.below} queues</span><span>above: ${gr.above} queues</span><span>nobody rostered: ${fx(gr.z)} queue-days</span></span>
    <div class="prods"><div class="ph">Queues: ${queues.length}${q && !own ? ' matching' : ''}, most contacts first; tap a queue for details</div>${queues.map(it => `<div class="pr" data-pr>
      <span class="l">${esc(it.nm)}</span><span class="r"><b class="num" style="color:${devCol(it.dev)}">${esc(it.pct)}</b></span>
      <span class="m">queue ${esc(it.i)}${cp(it.i)} · ${fx(it.p)} / ${fx(it.s)} contacts · empty: ${it.z} days</span>
      <span class="b">${pbar(it, it.nm)}</span>
      <span class="x"><span>Forecast ${fx(it.p)}, handled ${fx(it.s)} contacts over ${it.days} days with a run</span><span>average ${fx(it.p / Math.max(it.days, 1), 1)} against ${fx(it.s / Math.max(it.days, 1), 1)} a day</span></span></div>`).join('')}</div></div>`).join('')
    : '<p class="hint">Nothing matches the search.</p>';
  $$('#grp [data-row]').forEach(el => el.addEventListener('click', ev => { if (!ev.target.closest('[data-tip],[data-copy],.prods')) el.classList.toggle('open'); }));
  $$('#grp [data-pr]').forEach(el => el.addEventListener('click', ev => { if (!ev.target.closest('[data-tip],[data-copy]')) el.classList.toggle('open'); }));
}
const setMode = k => { $('#grp-box').hidden = k !== 'grp'; $('#list-box').hidden = k === 'grp'; k === 'grp' ? drawGrp() : drawIdx(); };
tabs($('#mode'), setMode); $('#gq').addEventListener('input', drawGrp);
if (!D.groups.length) $('#mode').hidden = true;
$('#q').addEventListener('input', () => drawIdx()); $('#g').addEventListener('change', () => drawIdx());
setMode(D.groups.length ? 'grp' : 'list');
"""


def render(w: dict, demo: bool = False) -> str:
    site, cat, card = w["site"], w["group"], w["card"]
    since = (card or {}).get("NEW_FORECAST_SINCE") or ""
    p = payload(w, since)
    dd = p["days"]
    period = f"{pg.long_day(dd[0]['d'])} to {pg.long_day(dd[-1]['d'])}" if dd else f"{w['start']} to {w['end']}"
    chips = ((f"site {site}", site), period, f"{len(dd)} days", f"{len(p['idx'])} queues with an engine run")
    header = pg.hero(f"{cat}: engine forecast against contacts handled", product="forecast-vs-actual", eyebrow=f"site {site} · daily forecast", demo=demo, chips=chips,
                     site=card, pulse=pg.pulse_from_rows(w["pulse_rows"], since or None, w["end"]),
                     lead="Does the engine's forecast for the whole day match the contacts this group handled at this site: in total, by day of the week, day by day and by queue.")
    return pg.page(f"Forecast vs actual, {site} {cat}", BODY, p, pg.CHART_JS + SCRIPT, css=CSS, header=header)


def summary(w: dict) -> str:
    a = analyse(w)
    if not a["days"]:
        return f"Site {w['site']}, {w['group']}: no engine runs in this period."
    tp, ts = sum(a["P"].values()), sum(a["S"].values())
    wape = sum(abs(a["P"][d] - a["S"][d]) for d in a["days"]) / ts * 100 if ts else 0
    return (f"Site {w['site']}, {w['group']}: forecast {pg.num(tp)} against {pg.num(ts)} contacts handled ({_pct(tp, ts)}), daily error {wape:.0f}%, "
            f"over {len(a['days'])} days.")
