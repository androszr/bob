# ruff: noqa: E501  -- the page's own CSS / JS is embedded as one string, its lines are not Python
"""Morning Pulse: how team leads treated the day's suggested staffing plans, as one page with two tabs.

The whole network (accepted as is, changed, removed, added; the plan; every group opening into its subgroups; the most
removed and added queues) and the sites' key queues (inside and outside the suggestion, by group, opening down to the
queue). Tap or hover any bar segment for its numbers; groups sortable; queue lists searchable.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from snowflake_bob import backends
from snowflake_bob import page as pg

# outcome key -> (label, colour, white label on the bar)
SEGS = {"accepted": ("as suggested", "#1f9d55", True), "reduced": ("fewer agents", "#5cc488", False),
        "increased": ("more agents", "#a9e2c0", False), "removed": ("removed", "#f1a9a9", False),
        "added": ("added by the team lead", "#a9c8f1", False)}
OUT_SEGS = {"added": ("added by the team lead", "#a9c8f1", False), "no agents": ("not planned, nobody rostered", "#e0746f", True),
            "below pace": ("not planned, roster below pace", "#f2c078", False), "not needed": ("not planned, enough agents", "#d9dee3", False)}
COUNTED = tuple(SEGS)


def collect(run, day: str) -> dict:
    other = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
    return {"day": day, "other": other, "lines": _labelled(run("pulse_lines", day=day, other_day=other)),
            "plans": run("pulse_plans", day=day, other_day=other), "queues": _labelled(run("pulse_queues", day=day, other_day=other)),
            "key_rows": _labelled(run("pulse_key", day=day, other_day=other))}


def _labelled(rows: list[dict]) -> list[dict]:
    """A missing group or subgroup gets a name: the page groups and sorts by them, and a warehouse has gaps."""
    fill = {"QUEUE_GROUP": "(no group)", "SUBGROUP": "(no subgroup)"}
    return [{**r, **{k: name for k, name in fill.items() if k in r and not r[k]}} for r in rows]


def _seg(k: str, label: str, colour: str, v: float | None, tip_html: str, dark: bool = False) -> dict | None:
    if v is None or v <= 0:
        return None
    return {"k": k, "c": colour, "v": round(v, 2), "t": f"{int(v + 0.5)}%", "tip": tip_html, "dk": dark}


def _shares(rows: list[dict]) -> tuple[dict, int]:
    count: dict[str, int] = {}
    for r in rows:
        if r["OUTCOME"] in COUNTED:
            count[r["OUTCOME"]] = count.get(r["OUTCOME"], 0) + r["LINES"]
    total = sum(count.values())
    return ({k: count.get(k, 0) / total * 100 for k in COUNTED} if total else {k: None for k in COUNTED}), total


def _with_changes(s: dict) -> float | None:
    vals = [s.get("accepted"), s.get("reduced"), s.get("increased")]
    return None if vals[0] is None else sum(x or 0 for x in vals)


def _delta(a, b):
    return None if a is None or b is None else round(a - b, 1)


def _cat_row(label: str, cur: list[dict], prev: list[dict], weight: int, cmp_: str, plans: int) -> dict:
    s, n = _shares(cur)
    p, _ = _shares(prev)
    segs = [x for k, (lb, col, dk) in SEGS.items() if (x := _seg(k, lb, col, s.get(k), pg.tip(f"{label}: {lb}", pg.percent(s.get(k))), dk))]
    d = _delta(s.get("accepted"), p.get("accepted"))
    return {"l": label, "w": weight, "acc": s.get("accepted"), "delta": d, "segs": segs, "tick": p.get("accepted"), "v": pg.percent(s.get("accepted")),
            "d": pg.arrow(d), "tone": pg.tone(d, True),
            "x": [f"with changes {pg.percent(_with_changes(s))}", f"as suggested on {cmp_}: {pg.percent(p.get('accepted'))}", f"removed {pg.percent(s.get('removed'))}",
                  f"added {pg.percent(s.get('added'))}", f"{n / plans:.1f} lines / plan" if plans else "", f"{pg.num(n)} lines"]}


def _money(v, sign: str = "€") -> str:
    return "–" if v is None else f"{sign}{v:,.0f}"


def _main(w: dict) -> dict:
    day, other = w["day"], w["other"]
    cmp_ = pg.short_day(other)
    cur = [r for r in w["lines"] if r["BIZ_DAY"] == day]
    prev = [r for r in w["lines"] if r["BIZ_DAY"] == other]
    s, n = _shares(cur)
    p, np_ = _shares(prev)
    plan_cur = [r for r in w["plans"] if r["BIZ_DAY"] == day]
    plan_prev = [r for r in w["plans"] if r["BIZ_DAY"] == other]
    per = n / len(plan_cur) if plan_cur else None
    per_prev = np_ / len(plan_prev) if plan_prev else None
    count = lambda v, nn: None if v is None or not nn else int(v * nn / 100 + 0.5)  # noqa: E731

    bar = [x for k, (lb, c, dk) in SEGS.items() if (x := _seg(k, lb, c, s[k], pg.tip(lb, f"{pg.percent(s[k])} of lines ({cmp_}: {pg.percent(p[k])})",
                                                                                     f"about {count(s[k], per)} of the average plan's queues" if per else ""), dk))]
    ticks = [v for v in (p["accepted"], _with_changes(p)) if v is not None]

    def cell(title, c, q, up_good, judged=True, main=False, colour=""):
        d = _delta(c, q)
        cnt, pc = count(c, per), count(q, per_prev)
        dc = None if cnt is None or pc is None else cnt - pc
        extra = ""
        if cnt is not None:
            extra = f"about {cnt} {'queue' if cnt == 1 else 'queues'} / plan" + (f' ({"▲" if dc > 0 else "▼" if dc < 0 else "="} {abs(dc)})' if dc is not None else "")
        return {"t": title, "v": pg.percent(c), "d": pg.arrow(d), "tone": pg.tone(d, up_good) if judged else "flat", "c": f"{cmp_}: {pg.percent(q)}", "main": main, "sw": colour, "x": extra}
    cells = [cell("Accepted as is", s["accepted"], p["accepted"], True, colour=SEGS["accepted"][1]),
             cell("= with changes", _with_changes(s), _with_changes(p), True, main=True),
             cell("+ fewer agents", s["reduced"], p["reduced"], True, judged=False, colour=SEGS["reduced"][1]),
             cell("+ more agents", s["increased"], p["increased"], True, judged=False, colour=SEGS["increased"][1]),
             cell("Removed", s["removed"], p["removed"], False, colour=SEGS["removed"][1]),
             cell("Added by the team lead", s["added"], p["added"], False, colour=SEGS["added"][1])]
    minutes = lambda rows: [r["MINUTES_TO_REVIEW"] for r in rows if r["MINUTES_TO_REVIEW"] is not None]  # noqa: E731
    med = statistics.median(minutes(plan_cur)) if minutes(plan_cur) else None
    med_p = statistics.median(minutes(plan_prev)) if minutes(plan_prev) else None
    money = lambda v: _money(v, w.get("sign", "€"))  # noqa: E731
    dmed = _delta(med, med_p)
    value = lambda rows, k, nn: sum((r[k] or 0) for r in rows) / nn if nn else None  # noqa: E731
    sv, sv_p = value(cur, "SUGGESTED_COST", len(plan_cur)), value(prev, "SUGGESTED_COST", len(plan_prev))
    ov, ov_p = value(cur, "APPROVED_COST", len(plan_cur)), value(prev, "APPROVED_COST", len(plan_prev))
    change = lambda a, b: "–" if not a or not b else f'{"▲" if a > b else "▼"} {abs(a / b - 1) * 100:.0f}%'  # noqa: E731
    sug = lambda rows: sum(r["LINES"] for r in rows if r["OUTCOME"] in ("accepted", "reduced", "increased", "removed"))  # noqa: E731
    pc_ = sug(cur) / len(plan_cur) if plan_cur else None
    pp_ = sug(prev) / len(plan_prev) if plan_prev else None
    plan = [{"t": "Time to review (median)", "v": "–" if med is None else f"{med:.1f} min", "d": pg.arrow(dmed, " min"), "tone": pg.tone(dmed, False), "c": f"{cmp_}: {'–' if med_p is None else f'{med_p:.1f} min'}"},
             {"t": "Suggested queues / plan", "v": pg.num(pc_), "d": "–" if pc_ is None or pp_ is None else f'{"▲" if round(pc_) > round(pp_) else "▼" if round(pc_) < round(pp_) else "="} {abs(round(pc_) - round(pp_))}', "tone": "flat", "c": f"{cmp_}: {pg.num(pp_)}"},
             {"t": "Suggested cost / plan", "v": money(sv), "d": change(sv, sv_p), "tone": "flat", "c": f"{cmp_}: {money(sv_p)}"},
             {"t": "Approved cost / plan", "v": money(ov), "d": change(ov, ov_p), "tone": "flat", "c": f"{cmp_}: {money(ov_p)}"}]
    cats = []
    names = sorted({r["QUEUE_GROUP"] for r in cur}, key=lambda c: (-sum(r["LINES"] for r in cur if r["QUEUE_GROUP"] == c and r["OUTCOME"] in COUNTED), c))
    for i, c in enumerate(names):
        row = _cat_row(c, [r for r in cur if r["QUEUE_GROUP"] == c], [r for r in prev if r["QUEUE_GROUP"] == c], i, cmp_, len(plan_cur))
        subs = []
        for j, g in enumerate(sorted({r["SUBGROUP"] for r in cur if r["QUEUE_GROUP"] == c})):
            sr = _cat_row(g, [r for r in cur if r["QUEUE_GROUP"] == c and r["SUBGROUP"] == g],
                          [r for r in prev if r["QUEUE_GROUP"] == c and r["SUBGROUP"] == g], j, cmp_, len(plan_cur))
            sr["m"], sr["x"] = sr["x"][4] + " · " + sr["x"][0], [x for x in sr["x"][1:] if not x.endswith("/ plan")]
            subs.append(sr)
        row["p"], row["ph"] = subs, f"Subgroups: {len(subs)}; tap one for details"
        cats.append(row)
    return {"cmp": cmp_, "bar": bar, "ticks": ticks, "cells": cells, "plan": plan, "groups": cats,
            "avg": f"{pg.num(per)} queues" + (f" ({cmp_}: {pg.num(per_prev)})" if per_prev else ""), "notes": [],
            "removed": _queues(w["queues"], day, other, "removed"), "added": _queues(w["queues"], day, other, "added")}


def _queues(rows: list[dict], day: str, other: str, outcome: str, top: int = 25) -> list[dict]:
    cur = sorted((r for r in rows if r["BIZ_DAY"] == day and r["OUTCOME"] == outcome), key=lambda r: (-r["SITES"], r["QUEUE_ID"]))
    prev = {r["QUEUE_ID"]: r["SITES"] for r in rows if r["BIZ_DAY"] == other and r["OUTCOME"] == outcome}
    out = []
    for r in cur[:top]:
        change = r["SITES"] - prev.get(r["QUEUE_ID"], 0)
        share = (r["NO_AGENTS"] or 0) / r["SITES"] if r["SITES"] else 0
        badges = [{"t": "often nobody rostered", "c": "danger"}] if share >= 0.3 else [{"t": "thin roster", "c": "warning"}] if share >= 0.1 else []
        out.append({"i": r["QUEUE_ID"], "nm": r["NAME"], "grp": r["QUEUE_GROUP"], "n": pg.num(r["SITES"]), "ch": f'{"▲" if change > 0 else "▼" if change < 0 else "="} {abs(change)}',
                    "tone": "flat" if not change else "down" if change > 0 else "up", "b": badges})
    return out


def _top_key(r: dict) -> str:
    return r["OUTCOME"] if r["OUTCOME"] != "skipped" else (r["REASON"] or "not needed")


def _top(w: dict) -> dict | None:
    day, other = w["day"], w["other"]
    cmp_ = pg.short_day(other)
    cur = [r for r in w["key_rows"] if r["BIZ_DAY"] == day]
    prev = [r for r in w["key_rows"] if r["BIZ_DAY"] == other]
    if not cur:
        return None
    n_sites = len([r for r in w["plans"] if r["BIZ_DAY"] == day]) or 1
    n_sites_p = len([r for r in w["plans"] if r["BIZ_DAY"] == other]) or 1
    in_keys, out_keys = ("accepted", "reduced", "increased", "removed"), tuple(OUT_SEGS)

    def shares(rows, keys):
        count = {k: 0 for k in keys}
        for r in rows:
            if (k := _top_key(r)) in count:
                count[k] += r["SITES"]
        total = sum(count.values())
        return {k: count[k] / total * 100 for k in keys} if total else {k: None for k in keys}, total
    meta = {**{k: SEGS[k] for k in in_keys}, **OUT_SEGS}

    def part(which: int) -> dict:
        keys = in_keys if which == 1 else out_keys
        s, n = shares(cur, keys)
        p, pn = shares(prev, keys)
        per, pper = n / n_sites, pn / n_sites_p
        segs = [x for k in keys if (x := _seg(k, meta[k][0], meta[k][1], s[k], pg.tip(meta[k][0], f"{pg.percent(s[k])} ({cmp_}: {pg.percent(p[k])})", f"about {(s[k] or 0) * per / 100:.1f} key queues of the average site"), meta[k][2]))]
        cells = []
        for k in keys:
            d = _delta(s[k], p[k])
            c = (s[k] or 0) * per / 100
            cells.append({"t": meta[k][0].capitalize(), "v": pg.percent(s[k]), "d": pg.arrow(d), "tone": pg.tone(d, k in ("accepted", "added")) if k != "not needed" else "flat", "c": f"{cmp_}: {pg.percent(p[k])}", "x": f"about {c:.1f} key queues / site", "main": False, "sw": meta[k][1]})
        total_all = sum(r["SITES"] for r in cur)
        return {"title": "Inside the suggestion: the engine suggested the key queue" if which == 1 else "Outside the suggestion: the engine did not suggest it",
                "meta": f"average {per:.1f} key queues per site ({cmp_}: {pper:.1f}) · {n / total_all * 100 if total_all else 0:.0f}% of key queues · {pg.num(n)} pairs",
                "segs": segs, "tick": p.get("accepted") if which == 1 else None, "cells": cells}

    def cats(which: int) -> list[dict]:
        keys = in_keys if which == 1 else out_keys
        vk = "accepted" if which == 1 else "added"

        def row(rows_c, rows_p, label, leaf=False):
            s, n = shares(rows_c, keys)
            p, _ = shares(rows_p, keys)
            d = _delta(s[vk], p[vk])
            if leaf:
                segs = [[k, round(s[k], 1), round(s[k] * n / 100)] for k in keys if s[k]]
            else:
                segs = [x for k in keys if (x := _seg(k, meta[k][0], meta[k][1], s[k], pg.tip(f"{label}: {meta[k][0]}", pg.percent(s[k]), f"about {(s[k] or 0) * n / n_sites / 100:.1f} key queues of the average site"), meta[k][2]))]
            x = ([f"{pg.num(n)} sites {'with a suggestion' if which == 1 else 'without one'}"] if leaf else [f"{n / n_sites:.1f} key queues of the average site"])
            if which == 1:
                x.insert(0, f"as suggested on {cmp_}: {pg.percent(p[vk])}")
            return {"l": label, ("sg" if leaf else "segs"): segs, "tick": None if which != 1 or p[vk] is None else round(p[vk], 2), "v": pg.percent(s[vk]), "d": pg.arrow(d),
                    "tone": pg.tone(d, True) if which == 1 else "flat", "x": x}, n
        out = []
        for c in sorted({r["QUEUE_GROUP"] for r in cur}):
            rc, rp = [r for r in cur if r["QUEUE_GROUP"] == c], [r for r in prev if r["QUEUE_GROUP"] == c]
            top, n = row(rc, rp, c)
            if not n:
                continue
            subs = []
            for g in sorted({r["SUBGROUP"] for r in rc}):
                gc, gp = [r for r in rc if r["SUBGROUP"] == g], [r for r in rp if r["SUBGROUP"] == g]
                gr, gn = row(gc, gp, g)
                if not gn:
                    continue
                prods = []
                for queue in sorted({r["QUEUE_ID"] for r in gc}):
                    ic = [r for r in gc if r["QUEUE_ID"] == queue]
                    pr, pn = row(ic, [r for r in gp if r["QUEUE_ID"] == queue], ic[0]["NAME"], True)
                    if pn:
                        prods.append({**pr, "i": queue, "_n": pn})
                prods.sort(key=lambda x: -x.pop("_n"))
                gr["p"], gr["ph"] = prods, f"Queues in this subgroup: {len(prods)}; tap one for details"
                gr["m"] = f"{pg.num(gn)} pairs · {len(prods)} {'queue' if len(prods) == 1 else 'queues'}"
                subs.append((gn, gr))
            subs.sort(key=lambda t: -t[0])
            top["p"], top["ph"] = [g for _, g in subs], f"Subgroups: {len(subs)}; tap one for its queues"
            out.append((n, top))
        out.sort(key=lambda t: -t[0])
        return [r for _, r in out]

    unavail: dict[str, dict] = {}
    for r in cur:
        if _top_key(r) in ("no rostered", "below pace"):
            x = unavail.setdefault(r["QUEUE_ID"], {"i": r["QUEUE_ID"], "nm": r["NAME"], "grp": r["QUEUE_GROUP"], "bz": 0, "u": 0})
            x["bz" if _top_key(r) == "no rostered" else "u"] += r["SITES"]
    nd = [{"i": x["i"], "nm": x["nm"], "grp": x["grp"], "n": pg.num(x["bz"]), "u": pg.num(x["u"]), "nd": pg.num(x["bz"] + x["u"]), "ch": "", "tone": "flat", "b": []}
          for x in sorted(unavail.values(), key=lambda x: (-x["bz"], -x["u"], x["i"]))[:25]]
    pairs = sum(r["SITES"] for r in cur)
    return {"cmp": cmp_, "parts": [part(1), part(2)], "cats1": cats(1), "cats2": cats(2), "lg": {k: [v[0], v[1], v[2]] for k, v in meta.items()},
            "lead": f"average site: {pairs / n_sites:.1f} key queues · {pg.num(pairs)} site and key-queue pairs · {len({r['QUEUE_ID'] for r in cur})} queues are key somewhere", "nd": nd}


CSS = """
.rows .row .l .chev,.prods .pr .l .chev{display:inline-block;color:var(--ink3);font-weight:400;margin-right:4px;transition:transform .15s}
.rows .row.open>.l .chev,.rows .prods .pr.open>.l .chev{transform:rotate(90deg)}
.rows .row .prods{grid-column:1/-1;display:none;margin:8px 0 2px;padding:6px 0 0 10px;border-left:3px solid #D6EFDC;min-width:0}
.rows .row.open>.prods,.rows .prods .pr.open>.prods{display:block}.prods .ph{font-size:12px;color:var(--ink2);margin-bottom:2px}
.rows .prods .pr>.prods{border-left-color:#e3e8ee}
.prods .pr{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;padding:7px 0;border-top:1px solid var(--line);
cursor:pointer}.prods .pr .l{font-weight:600;font-size:13px;min-width:0;overflow-wrap:anywhere}.prods .pr .r{text-align:right;white-space:nowrap}
.prods .pr .m{grid-column:1/-1;font-size:12px;color:var(--ink2);min-width:0;overflow-wrap:anywhere}.prods .pr .b{grid-column:1/-1}
.rows .row .prods .pr>.x{grid-column:1/-1;display:none;font-size:12px;color:var(--ink2)}
.rows .row .prods .pr.open>.x{display:flex;flex-wrap:wrap;gap:2px 12px}
#tk-q{margin:2px 0 4px}
"""

BODY = """
<div class="tabs" id="tabs"><button class="on" data-k="net">Whole network</button><button data-k="top">Key queues</button>
</div>
<section id="v-net"></section><section id="v-top" hidden></section>
<p class="foot" id="foot"></p>
"""

SCRIPT = r"""
const M = D.main, T = D.top;
function stack(segs, ticks, thin) {
  const marks = (ticks || []).filter(x => x != null).map(x => `<u style="left:${Math.min(Math.max(x,0),100)}%"></u>`);
  return `<div class="stack${thin ? ' thin' : ''}">` + segs.map(sg =>
    `<i class="${sg.dk ? 'dk' : ''}" style="width:${sg.v}%;background:${sg.c}" data-tip="${esc(sg.tip)}">${sg.v >= (thin ? 7 : 5) ? sg.t : ''}</i>`).join('')
    + marks.join('') + '</div>';
}
function kpi(cl) {
  return `<div class="kpi${cl.main ? ' main' : ''}"><div class="t">${cl.sw ? `<span class="sw" style="background:${cl.sw}"></span>` : ''}${esc(cl.t)}</div>
    <div class="v num">${esc(cl.v)}</div><div class="d ${cl.tone}">${esc(cl.d)}</div><div class="c">${esc(cl.c)}</div>
    ${cl.x ? `<div class="c">${esc(cl.x)}</div>` : ''}</div>`;
}
function legend(segs) {
  return '<div class="legend">' + segs.map(sg => `<span><span class="sw" style="background:${sg.c}"></span>${esc(sg.tip.replace(/<br>.*/,'').replace(/<\/?b>/g,'').replace(/^.*: /,''))}</span>`).join('') + '</div>';
}
// a group opens into its subgroups, a subgroup into its queues
const chev = cl => cl.p && cl.p.length ? '<span class="chev">›</span>' : '';
function kids(cl) {
  return cl.p && cl.p.length ? `<div class="prods"><div class="ph">${esc(cl.ph)}</div>${cl.p.map(prow).join('')}</div>` : '';
}
// what a search looks in: the row, and every row under it
const sOf = cl => cl._s ??= `${cl.i || ''} ${cl.l} ${(cl.p || []).map(sOf).join(' ')}`.toLowerCase();
// a queue's segments come compact ([key, share, sites]) and are drawn from the legend
const segsOf = pr => pr.segs || pr.sg.map(([k, v, n]) => ({k, v, c: T.lg[k][1], dk: T.lg[k][2], t: `${Math.floor(v + 0.5)}%`,
  tip: `<b>${esc(pr.l + ': ' + T.lg[k][0])}</b><br>${esc(pct(v))}<br>about ${esc(int(n))} sites`}));
function prow(pr) {
  return `<div class="pr" data-pr data-s="${esc(sOf(pr))}">
    <span class="l">${chev(pr)}${esc(pr.l)}</span><span class="r"><b class="num">${esc(pr.v)}</b> <span class="small ${pr.tone}">${esc(pr.d)}</span></span>
    ${pr.i || pr.m ? `<span class="m">${pr.i ? `queue ${esc(pr.i)}${cp(pr.i)}${pr.m ? ' · ' : ''}` : ''}${esc(pr.m || '')}</span>` : ''}
    <span class="b">${stack(segsOf(pr), [pr.tick], true)}</span>
    <span class="x">${pr.x.map(x => `<span>${esc(x)}</span>`).join('')}</span>${kids(pr)}</div>`;
}
function catRows(rows, q) {
  rows = q ? rows.filter(cl => sOf(cl).includes(q)) : rows;
  if (!rows.length) return '<p class="hint">Nothing matches the search.</p>';
  return '<div class="rows">' + rows.map(cl => `<div class="row" data-row>
    <span class="l">${chev(cl)}${esc(cl.l)}</span><span class="r"><b class="num">${esc(cl.v)}</b> <span class="small ${cl.tone}">${esc(cl.d)}</span></span>
    <span class="b">${stack(cl.segs, [cl.tick], true)}</span>
    <span class="x">${cl.x.filter(Boolean).map(esc).join(' · ')}</span>${kids(cl)}</div>`).join('') + '</div>';
}
// a search hit on a queue or subgroup: open its parents, hide the siblings that do not match
function findIn(root, q) {
  if (!q) return;
  $$('[data-row]', root).forEach(rw => { if (!rw.querySelector('.l').textContent.toLowerCase().includes(q)) rw.classList.add('open'); });
  const own = el => el.querySelector('.l').textContent.toLowerCase().includes(q);
  $$('[data-pr]', root).forEach(pr => {
    let up = pr.parentElement.closest('[data-pr],[data-row]'), byParent = false;
    for (; up && !byParent; up = up.parentElement.closest('[data-pr],[data-row]')) byParent = own(up);
    pr.hidden = !pr.dataset.s.includes(q) && !byParent;
    if (!pr.hidden && !own(pr) && !byParent && pr.querySelector('.prods')) pr.classList.add('open');
  });
}
function plist(id, rows, cols) {
  if (!rows) return '<p class="hint">No queue data for this day.</p>';
  return `<input class="search" placeholder="Search: queue number, name, group" data-q="${id}">
    <div class="list" id="${id}">` + rows.map((pr, n) => `<div class="it" data-s="${esc((pr.i+' '+pr.nm+' '+pr.grp).toLowerCase())}">
    <span class="o">${n + 1}.</span><span><span class="nm">${esc(pr.nm)}</span>
    ${pr.b.map(bd => `<span class="badge ${bd.c}">${esc(bd.t)}</span>`).join(' ')}
    <div class="meta">${esc(pr.i)}${cp(pr.i)} · ${esc(pr.grp)}${cols === 'nd' ? ` · roster below pace: ${esc(pr.u)} · uncovered: ${esc(pr.nd)}` : ''}</div></span>
    <span class="n">${esc(pr.n)}<small class="${pr.tone}">${esc(pr.ch)}</small></span></div>`).join('') + '</div>';
}
function wire(root) {
  // nested rows: a tap belongs to the innermost row; taps on a bar segment, a copy icon or inside an open child list do not fold the parent
  $$('[data-row],[data-pr]', root).forEach(rw => rw.addEventListener('click', ev => {
    if (ev.target.closest('[data-tip],[data-copy]') || ev.target.closest('[data-row],[data-pr]') !== rw) return;
    const box = ev.target.closest('.prods'); if (box && rw.contains(box)) return;
    rw.classList.toggle('open');
  }));
  $$('input[data-q]', root).forEach(inp => inp.addEventListener('input', () => {
    const q = inp.value.trim().toLowerCase();
    $$('.it', $('#' + inp.dataset.q)).forEach(it => it.hidden = q && !it.dataset.s.includes(q));
  }));
}
function seg(id, opts, on) {
  return `<div class="seg" id="${id}">` + opts.map(([k, l]) => `<button data-k="${k}" class="${k === on ? 'on' : ''}">${esc(l)}</button>`).join('') + '</div>';
}

// --- whole network
(function () {
  const box = $('#v-net');
  const sorts = {w: (aa, bb) => aa.w - bb.w, acc: (aa, bb) => (bb.acc ?? -1) - (aa.acc ?? -1), d: (aa, bb) => (bb.delta ?? -99) - (aa.delta ?? -99)};
  box.innerHTML = M.notes.map(u => `<div class="warn">⚠ ${esc(u)}</div>`).join('') + `
  <div class="card"><h3>What team leads did with the suggestion: as is, or with a changed number of agents</h3>
    <div class="hint">average plan: ${esc(M.avg)} · marks = ${esc(M.cmp)} · tap the bar to see the numbers</div>
    ${stack(M.bar, M.ticks)}${legend(M.bar)}
    <div class="kpis">${M.cells.map(kpi).join('')}</div></div>
  <h2>The plan</h2><div class="kpis">${M.plan.map(kpi).join('')}</div>
  <h2>Acceptance by group</h2>
  <div class="hint">The bar is every line of the group (100%); the mark is "as suggested" on ${esc(M.cmp)}. Tap a row for details and subgroups.</div>
  ${seg('sort', [['w','by weight in the plan'],['acc','by as suggested'],['d','by change']], 'w')}
  <div class="card" id="groups"></div>
  <h2>Most often removed and added: top 25</h2>
  <div class="hint">Number of sites and the change against ${esc(M.cmp)} (red = more sites)</div>
  ${seg('pl', [['removed','Removed'],['added','Added by the team lead']], 'removed')}
  <div class="card" id="pl-box"></div>`;
  const drawGroups = k => { $('#groups').innerHTML = catRows([...M.groups].sort(sorts[k])); wire($('#groups')); };
  const drawLists = k => { $('#pl-box').innerHTML = plist('pl-' + k, M[k]); wire($('#pl-box')); };
  drawGroups('w'); drawLists('removed'); tabs($('#sort'), drawGroups); tabs($('#pl'), drawLists);
})();

// --- key queues (none when the day has no key-queue lines)
if (T) (function () {
  const box = $('#v-top');
  box.innerHTML = `<div class="card"><h3>Key queues: what happened to them in the plan</h3>
    <div class="hint">${esc(T.lead)}</div>` + T.parts.map((pt, i) => `
    <div style="margin-top:${i ? 18 : 6}px${i ? ';padding-top:14px;border-top:1px dashed var(--line)' : ''}">
      <h3>${esc(pt.title)}</h3><div class="hint">${esc(pt.meta)}${i === 0 ? ` · mark = as suggested on ${esc(T.cmp)}` : ''}</div>
      ${stack(pt.segs, [pt.tick])}${legend(pt.segs)}<div class="kpis">${pt.cells.map(kpi).join('')}</div></div>`).join('') + `</div>
  <h2>Key queues by group</h2>
  ${seg('tk', [['1','Inside the suggestion'],['2','Outside the suggestion']], '1')}
  <div class="hint" id="tk-hint"></div>
  <input class="search" id="tk-q" placeholder="Search: group, subgroup, queue number or name">
  <div class="card" id="tk-box"></div>
  ${T.nd.length ? `<h2>Key queues that were not suggested and not approved</h2>
  <div class="hint">Number of sites with nobody rostered; in the description, roster below pace and uncovered in total</div>
  <div class="card" id="nd-box">${plist('t-nd', T.nd, 'nd')}</div>` : ''}`;
  const hints = {'1': `The bar is the key queues the engine suggested (100%): as suggested, changed number, removed; the mark is as suggested on ${T.cmp}.`,
                 '2': 'The bar is the key queues the engine did not suggest (100%): added by the team lead, the rest by roster.'};
  let tk = '1';
  const drawTk = k => { tk = k; const q = $('#tk-q').value.trim().toLowerCase();
    $('#tk-hint').textContent = hints[k] + ' Tap a group for subgroups, a subgroup for its queues.';
    $('#tk-box').innerHTML = catRows(T['cats' + k], q); wire($('#tk-box')); findIn($('#tk-box'), q); };
  $('#tk-q').addEventListener('input', () => drawTk(tk));
  drawTk('1'); tabs($('#tk'), drawTk); if ($('#nd-box')) wire($('#nd-box'));
})();

tabs($('#tabs'), k => { $('#v-net').hidden = k !== 'net'; $('#v-top').hidden = k !== 'top'; hideTip(); });
$('#foot').textContent = D.foot;
"""


def render(w: dict, demo: bool = False) -> str:
    day = w["day"]
    title = f"Suggestion acceptance, {pg.long_day(day)}"
    plans = [r for r in w["plans"] if r["BIZ_DAY"] == day]
    chips = (f"{pg.num(len(plans))} plans", f"{pg.num(len({r['SITE_ID'] for r in plans}))} sites", f"compared with {pg.short_day(w['other'])}")
    header = pg.hero(title, product="morning-pulse", eyebrow="morning report", demo=demo, chips=chips,
                     lead=f"Against the same weekday a week earlier ({pg.long_day(w['other'])}).")
    top = _top(w)
    sign = "€" if demo else backends.settings().get("BOB_REPORT_CURRENCY", "")
    data = {"main": _main({**w, "sign": sign}), "top": top, "foot": "Source: Snowflake, PLAN_LINES, PLANS, QUEUES and DAILY. Acceptance = lines accepted / (lines suggested + lines added); "
                                                  "removed and added use the same denominator."}
    body = BODY if top else BODY.replace('<button data-k="top">Key queues</button>', "")
    return pg.page(title, body, data, SCRIPT, css=CSS, header=header)


def summary(w: dict) -> str:
    day, other = w["day"], w["other"]
    s, n = _shares([r for r in w["lines"] if r["BIZ_DAY"] == day])
    p, _ = _shares([r for r in w["lines"] if r["BIZ_DAY"] == other])
    if not n:
        return f"{pg.long_day(day)}: no plan lines."
    d = _delta(s["accepted"], p["accepted"])
    return (f"{pg.long_day(day)}: {pg.percent(s['accepted'])} accepted as is ({pg.arrow(d)} against {pg.short_day(other)}), "
            f"{pg.percent(s['removed'])} removed, {pg.percent(s['added'])} added, {pg.num(n)} lines.")
