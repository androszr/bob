# ruff: noqa: E501  -- the page's own JS is embedded as one string, its lines are not Python
"""Bob's reports as one self-contained HTML file: the data as JSON inside it, the layout and the charts drawn by inline
JS, no CDN and no external file. It opens from a SharePoint link (`?web=1` opens the file viewer, which runs the page in
a sandboxed iframe), from GitHub Pages or from a double click, on a desktop and on a phone.

Layout rules for every page: 12 px side gutter on a phone, 24 px from 720 px, content at most MAX_W wide; grids collapse
to one column, wide tables scroll inside their own box and never the page; tap targets of at least 36 px; tooltips work
on hover and on tap.
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import date

from snowflake_bob import backends, uk_map
from snowflake_bob.page_css import CSS

MAX_W = 1080
MARK = "Bob Insights"
PRODUCTS = {"morning-pulse": "Morning Pulse", "network-panorama": "Network Panorama", "forecast-vs-actual": "Forecast vs Actual"}
DEMO_CHIP = "Made-up demo data"
# Bob's own icon, drawn inline so a page has no image file and no embedded blob
_ICON = ('<svg viewBox="0 0 1024 1024" width="28" height="28" role="img" aria-label="Bob">'
         '<rect width="1024" height="1024" rx="224" fill="#0B100F"/>'
         '<path d="M232 232H792A72 72 0 0 1 864 304V600A72 72 0 0 1 792 672H520L372 808V672H232A72 72 0 0 1 160 600V304A72 72 0 0 1 232 232Z" fill="#9BE8A8"/>'
         '<path d="M418 330H552A70 70 0 0 1 586 460A78 78 0 0 1 560 574H418ZM494 392V440H540A24 24 0 0 0 540 392ZM494 498V532H548A17 17 0 0 0 548 498Z" fill="#0B2112" fill-rule="evenodd"/></svg>')
_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Shared helpers for the page scripts: number formats, the tooltip (hover + tap), the copy icon, tabs, the boundary mark.
JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const nf = (v, d=1) => v == null || isNaN(v) ? '–' : Number(v).toLocaleString('en-GB',
  {minimumFractionDigits: d, maximumFractionDigits: d});
const pct = (v, d=1) => v == null ? '–' : nf(v, d) + '%';
const int = v => v == null ? '–' : Math.round(v).toLocaleString('en-GB');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $ = (s, r=document) => r.querySelector(s), $$ = (s, r=document) => [...r.querySelectorAll(s)];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const dm = iso => `${+iso.slice(8, 10)} ${MONTHS[+iso.slice(5, 7) - 1]}`;
const dmy = iso => `${dm(iso)} ${iso.slice(0, 4)}`;
const tip = document.getElementById('tip');
let tipFor = null;
function showTip(el, x, y) {
  tip.innerHTML = el.dataset.tip; tip.style.display = 'block'; tipFor = el;
  const w = tip.offsetWidth, h = tip.offsetHeight, vw = innerWidth;
  tip.style.left = Math.max(8, Math.min(vw - w - 8, x - w / 2)) + 'px';
  tip.style.top = (y - h - 14 < 8 ? y + 18 : y - h - 14) + 'px';
}
function hideTip() { tip.style.display = 'none'; tipFor = null; }
document.addEventListener('pointermove', ev => {
  if (ev.pointerType !== 'mouse') return;
  const el = ev.target.closest('[data-tip]');
  el ? showTip(el, ev.clientX, ev.clientY) : hideTip();
});
document.addEventListener('click', ev => {
  const el = ev.target.closest('[data-tip]');
  if (el && (ev.pointerType !== 'mouse' || !ev.pointerType)) {
    if (tipFor === el) hideTip(); else showTip(el, ev.clientX, ev.clientY);
  } else if (!el) hideTip();
});
addEventListener('scroll', hideTip, {passive: true});
// a small copy icon next to every site and queue number; a sandboxed viewer may refuse navigator.clipboard, then the old
// execCommand path runs from the same tap
const CP_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5" y="5" width="9" height="9" rx="2"/><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5"/></svg>';
// data-tip goes through the attribute's decoding and then into innerHTML, so a value there is escaped twice
const tipEsc = s => esc(esc(s));
const cp = v => `<button class="cp" type="button" data-copy="${esc(v)}" title="Copy ${esc(v)}" aria-label="Copy ${esc(v)}">${CP_ICON}</button>`;
function copyText(txt) {
  const old = () => { const area = document.createElement('textarea'); area.value = txt; area.style.position = 'fixed'; area.style.opacity = '0';
    document.body.appendChild(area); area.select(); let ok = false; try { ok = document.execCommand('copy'); } catch (err) {} area.remove(); return ok; };
  try { if (navigator.clipboard) return navigator.clipboard.writeText(txt).then(() => true, () => old()); } catch (err) {}
  return Promise.resolve(old());
}
document.addEventListener('click', ev => {
  const btn = ev.target.closest('[data-copy]'); if (!btn) return;
  ev.stopPropagation(); ev.preventDefault();
  copyText(btn.dataset.copy).then(ok => {
    const box = document.getElementById('toast'); box.textContent = ok ? `Copied ${btn.dataset.copy}` : 'Could not copy';
    box.style.display = 'block'; btn.classList.add('ok'); clearTimeout(box._h);
    box._h = setTimeout(() => { box.style.display = 'none'; btn.classList.remove('ok'); }, 1400);
  });
}, true);
document.querySelectorAll('[data-cp]').forEach(el => el.insertAdjacentHTML('beforeend', cp(el.dataset.cp)));
// The new-forecasts boundary on every time chart; it stays visible when the whole visible range is before or after it.
// opt.since = 'YYYY-MM-DD HH:MM' or ''; first / last = ISO days of the range; x = its position or null; left / right / top / bot = the plot box.
function forecastMark(opt) {
  if (!opt.since) return '';
  const day = opt.since.slice(0, 10), lab = esc(dmy(day)), fs = opt.mob ? 10.5 : 11.5;
  const badge = (txt, fill, ink) => { const w = txt.length * fs * 0.56 + 16;
    return `<rect x="${opt.left + 4}" y="${opt.top + 4}" width="${w}" height="${fs + 9}" rx="${(fs + 9) / 2}" fill="${fill}" opacity=".92"/>`
      + `<text x="${opt.left + 12}" y="${opt.top + fs + 7}" font-size="${fs}" font-weight="700" fill="${ink}">${esc(txt)}</text>`; };
  if (day <= opt.first) return `<rect x="${opt.left}" y="${opt.top}" width="${opt.right - opt.left}" height="${opt.bot - opt.top}" fill="#0d732f" opacity=".05"/>`
    + badge(`whole range on the new forecasts (since ${lab})`, '#e8f5ec', '#0d732f');
  if (day > opt.last || opt.x == null) return badge(`whole range on the old forecasts · new since ${lab}`, '#eef1f3', '#4a4a4a');
  const right = opt.x > opt.right - (opt.mob ? 120 : 170);
  return `<rect x="${opt.x}" y="${opt.top}" width="${Math.max(0, opt.right - opt.x)}" height="${opt.bot - opt.top}" fill="#0d732f" opacity=".05"/>`
    + `<line x1="${opt.x}" x2="${opt.x}" y1="${opt.top - 4}" y2="${opt.bot}" stroke="#0d732f" stroke-width="2" stroke-dasharray="6 4"/>`
    + `<text x="${opt.x + (right ? -6 : 6)}" y="${opt.top + fs + 2}" font-size="${fs}" font-weight="700" fill="#0d732f" text-anchor="${right ? 'end' : 'start'}">new forecasts · ${lab}</text>`;
}
const forecastBand = opt => forecastMark(opt).replace(/<text[\s\S]*?<\/text>/g, '').replace(/<rect[^>]*\brx=[^>]*\/>/g, '');
function tabs(root, onChange) {
  $$('button', root).forEach(btn => btn.addEventListener('click', () => {
    $$('button', root).forEach(other => other.classList.toggle('on', other === btn)); onChange(btn.dataset.k);
  }));
}
"""

# A responsive multi-panel day chart drawn to the container width: one date axis, bars side by side, lines with a gap
# where a day has no value. Used by Network Panorama and Forecast vs Actual.
CHART_CSS = """
.ctl{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px}.ctl .seg{margin:0 0 4px}
.readout{font-size:13px;min-height:44px;border-top:1px solid var(--line);margin-top:6px;padding-top:8px}
.readout b{font-size:14px}.readout .g{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,210px),1fr));gap:2px 16px;margin-top:4px}
.readout .g>span{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:3px 0;border-bottom:1px dashed #edf0f2}
.readout .g>span>b{font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.legend button{font:inherit;font-size:12.5px;border:none;background:none;padding:4px 2px;color:var(--ink);cursor:pointer;
min-height:30px}.legend button.off{opacity:.35}
.sw.ln{height:3px;width:14px;border-radius:2px;vertical-align:3px}.sw.dash{background-image:linear-gradient(90deg,currentColor 60%,transparent 0)!important;background-size:6px 3px}
#chart svg{display:block;width:100%;touch-action:pan-y}
table.t th.s{cursor:pointer;user-select:none}table.t th .ar{font-style:normal}table.t th .ar:after{content:" ↕";color:var(--ink3);font-size:10px}
table.t th.asc .ar:after{content:" ▲";color:var(--ink)}table.t th.desc .ar:after{content:" ▼";color:var(--ink)}
table.t td:first-child,table.t th:first-child{position:sticky;left:0;background:var(--card);z-index:1}
table.t tr.few td{color:var(--ink3)}table.t th small{display:block;font-weight:400;color:var(--ink3)}
dl.kv{margin:0;font-size:13.5px}dl.kv>div{display:flex;justify-content:space-between;align-items:baseline;gap:6px 16px;
padding:7px 0;border-top:1px solid var(--line)}dl.kv>div:first-child{border-top:none}
dl.kv dt{flex:1 1 55%;min-width:0;color:var(--ink2);margin:0}
dl.kv dd{flex:0 1 auto;min-width:0;margin:0;text-align:right;font-weight:600;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
"""

CHART_JS = r"""
const fx = (v, d=0) => v == null || isNaN(v) ? '–' : Number(v).toLocaleString('en-GB', {minimumFractionDigits: d, maximumFractionDigits: d});
// a top whose quarters are round numbers (the grid has five lines)
const niceMax = raw => { raw = Math.max(raw, 1e-9); const mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 1.2, 1.6, 2, 2.4, 3.2, 4, 4.8, 6, 8, 10].find(m => m * mag >= raw) * mag; };
function dayChart(box, opt) {
  // opt: {days: [{d, shade}], panels: [{title, h, hm, ser: [{k, c, t: 'bar'|'line', dash}]}], val(i, k), off: Set, hover, pick(i), mark(...)}
  const W = Math.max(260, box.clientWidth), mob = W < 560, n = opt.days.length;
  const panels = opt.panels.map(pan => {
    const ser = pan.ser.filter(one => !opt.off.has(one.k));
    const vals = []; opt.days.forEach((_, i) => ser.forEach(one => { const v = opt.val(i, one.k); if (v != null) vals.push(v); }));
    const max = niceMax(Math.max(0, ...vals)), q = max / 4, whole = v => Math.abs(v - Math.round(v)) < 1e-6;
    const dg = whole(q) ? 0 : whole(q * 10) ? 1 : 2;
    return {...pan, ser, max, dg};
  });
  const lab = Math.max(...panels.map(pan => fx(pan.max, pan.dg).length));
  const L = 10 + lab * (mob ? 5.6 : 6.2), R = 8, step = (W - L - R) / n, x = i => L + step * (i + .5);
  let y0 = 0, svg = '';
  opt.days.forEach((day, i) => { if (day.shade) svg += `<rect x="${L + step * i}" y="0" width="${step}" height="__H__" fill="#f3f5f7"/>`; });
  panels.forEach((pan, pi) => {
    const lift = opt.mark && !pi ? 18 : 0, h = mob ? pan.hm : pan.h, top = y0 + 24 + lift, bot = y0 + h - 8 + (lift ? 12 : 0), y = v => bot - v / pan.max * (bot - top);
    svg += `<text x="${L}" y="${y0 + 13}" font-size="11.5" font-weight="600" fill="#5c5c5c">${esc(pan.title)}</text>`;
    [0, .25, .5, .75, 1].forEach(f => { const v = pan.max * f, yy = y(v);
      svg += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="${f ? '#eef0f2' : '#d5d9de'}"/>`;
      if (!mob || f % .5 === 0) svg += `<text x="${L - 5}" y="${yy + 3.5}" font-size="10" fill="#8a8a8a" text-anchor="end">${fx(v, pan.dg)}</text>`; });
    if (opt.mark) svg += opt.mark({pi, left: L, right: W - R, top, bot, mob, step});
    const bars = pan.ser.filter(one => one.t === 'bar'), bw = Math.max(1, step * .78 / Math.max(1, bars.length));
    bars.forEach((one, j) => opt.days.forEach((_, i) => { const v = opt.val(i, one.k); if (!v) return;
      svg += `<rect x="${(x(i) - step * .39 + j * bw).toFixed(1)}" y="${y(Math.max(v, 0)).toFixed(1)}" width="${Math.max(.8, bw - (bw > 4 ? 1 : 0)).toFixed(1)}" height="${Math.abs(y(v) - y(0)).toFixed(1)}" rx="${bw > 6 ? 1.5 : 0}" fill="${one.c}"/>`; }));
    pan.ser.filter(one => one.t === 'line').forEach(one => {
      let path = '', pen = false, dots = '';
      opt.days.forEach((_, i) => { const v = opt.val(i, one.k); if (v == null) { pen = false; return; }
        path += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
        if (n <= 45 || opt.hover === i) dots += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="${opt.hover === i ? 4 : 2.2}" fill="${opt.hover === i ? one.c : '#fff'}" stroke="${one.c}" stroke-width="1.6"/>`; });
      svg += `<path d="${path}" fill="none" stroke="${one.c}" stroke-width="2.2" stroke-linejoin="round" ${one.dash ? 'stroke-dasharray="6 4"' : ''}/>` + dots;
    });
    y0 += h + (lift ? 12 : 0);
  });
  const every = Math.ceil(n / (mob ? 6 : 12));
  opt.days.forEach((day, i) => { if ((n - 1 - i) % every === 0) svg += `<text x="${x(i)}" y="${y0 + 13}" font-size="10" fill="#8a8a8a" text-anchor="${x(i) > W - R - 16 ? 'end' : x(i) < L + 16 ? 'start' : 'middle'}">${dm(day.d)}</text>`; });
  if (opt.hover != null && opt.hover < n) svg += `<rect x="${L + step * opt.hover}" y="0" width="${step}" height="${y0}" fill="#123326" fill-opacity=".04" stroke="#123326" stroke-opacity=".45" stroke-width="1" rx="3"/>`;
  svg = svg.replaceAll('__H__', y0);
  box.innerHTML = `<svg viewBox="0 0 ${W} ${y0 + 18}" height="${y0 + 18}" role="img">${svg}<rect class="hit" x="${L}" y="0" width="${W - L - R}" height="${y0}" fill="transparent"/></svg>`;
  const hit = $('.hit', box), pick = ev => { const rc = hit.getBoundingClientRect();
    opt.pick(Math.max(0, Math.min(n - 1, Math.floor((ev.clientX - rc.left) / (rc.width / n))))); };
  hit.addEventListener('pointermove', ev => { if (ev.pointerType === 'mouse') pick(ev); });
  hit.addEventListener('pointerdown', pick);
}
function legendButtons(root, ser, off, redraw) {
  root.innerHTML = ser.map(one => `<button data-s="${esc(one.k)}" class="${off.has(one.k) ? 'off' : ''}"><span class="sw${one.t === 'line' ? ' ln' : ''}${one.dash ? ' dash' : ''}" style="background:${one.c};color:${one.c}"></span>${esc(one.l)}</button>`).join('');
  $$('button', root).forEach(btn => btn.addEventListener('click', () => {
    off.has(btn.dataset.s) ? off.delete(btn.dataset.s) : off.add(btn.dataset.s); btn.classList.toggle('off'); redraw(); }));
}
function sortableTable(root, head, rows, opts={}) {
  // head: [{l, sub, k}] (k = sort key index in rows[i].s or null), rows: [{c: [cells html], s: [sort values], cls}]
  let sk = opts.sk ?? null, dir = opts.dir ?? 1;
  const draw = () => {
    const rr = sk == null ? rows : [...rows].sort((a, b) => { const x = a.s[sk], y = b.s[sk];
      return (x == null) - (y == null) || (x < y ? -1 : x > y ? 1 : 0) * dir; });
    root.innerHTML = '<table class="t"><thead><tr>' + head.map((hd, i) => `<th class="${hd.k != null ? 's' : ''}${hd.k != null && hd.k === sk ? (dir > 0 ? ' asc' : ' desc') : ''}" data-i="${i}">${esc(hd.l)}${hd.k != null ? '<i class="ar"></i>' : ''}${hd.sub ? `<small>${esc(hd.sub)}</small>` : ''}</th>`).join('')
      + '</tr></thead><tbody>' + rr.map(row => `<tr class="${row.cls || ''}">${row.c.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('') + '</tbody></table>';
    $$('th.s', root).forEach(th => th.addEventListener('click', () => { const k = head[+th.dataset.i].k;
      if (sk === k) dir = -dir; else { sk = k; dir = k === 0 ? 1 : -1; } draw(); }));
  };
  draw();
}
"""


def esc(s) -> str:
    return _html.escape(str(s))


def tip_attr(tip_html: str) -> str:
    """A data-tip value: the page's JS puts the decoded attribute into innerHTML, so its html is escaped once more."""
    return esc(tip_html)


def tip(title: str, *lines: str) -> str:
    return f"<b>{esc(title)}</b>" + "".join(f"<br>{esc(x)}" for x in lines if x)


def num(v, digits: int = 0) -> str:
    return "–" if v is None else f"{v:,.{digits}f}"


def percent(v, digits: int = 1) -> str:
    return "–" if v is None else f"{v:.{digits}f}%"


def arrow(d, unit: str = " pp") -> str:
    if d is None:
        return "–"
    return "= 0" + unit if abs(d) < 0.05 else f'{"▲" if d > 0 else "▼"} {abs(d):.1f}{unit}'


def tone(d, up_good: bool) -> str:
    return "flat" if d is None or abs(d) < 0.05 else ("up" if (d > 0) == up_good else "down")


def short_day(iso: str | None) -> str:
    if not iso:
        return "–"
    d = date.fromisoformat(str(iso)[:10])
    return f"{d.day} {_MONTHS[d.month - 1]}"


def long_day(iso: str | None) -> str:
    return f"{short_day(iso)} {str(iso)[:4]}" if iso else "–"


def _json_for_script(data) -> str:
    """JSON safe inside <script type="application/json">: no '<' at all, so neither '</script' nor '<!--' can end or
    bend the tag (JSON.parse reads the \\u003c escape back as '<')."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).replace("<", "\\u003c")


def site_card(s: dict | None) -> str:
    """The collapsed site card in the page header: team lead, contacts, planner, address, region, segment, time open."""
    if not s:
        return ""
    # the named people are empty on a real report unless BOB_REPORT_CONTACTS=1 (reports.CONTACT_FIELDS)
    line = " · ".join(x for x in (f"team lead {s['TEAM_LEAD']}" if s["TEAM_LEAD"] else "", s["CITY"], f"region {s['REGION']}") if x)
    director = f'<br><span class="muted">regional director: {esc(s["DIRECTOR"])}</span>' if s["DIRECTOR"] else ""
    rows = [("Team lead", f'<b>{esc(s["TEAM_LEAD"])}</b><br><a href="mailto:{esc(s["TEAM_LEAD_EMAIL"])}">{esc(s["TEAM_LEAD_EMAIL"])}</a>') if s["TEAM_LEAD"] else None,
            ("Site phone", f'{esc(s["PHONE"])}<span data-cp="{esc(s["PHONE"])}"></span>'),
            ("Workforce planner", f'<b>{esc(s["PLANNER"])}</b><br>{esc(s["PLANNER_PHONE"])}') if s["PLANNER"] else None,
            ("Address", f'{esc(s["ADDRESS"])}<br><span class="muted">{esc(s["CITY"])}</span>'),
            ("Region", f'region {esc(s["REGION"])} · district {esc(s["DISTRICT"])}{director}'),
            ("Segment", esc(s["SEGMENT"])), ("Size", esc(s["SIZE_BAND"])),
            ("Seats", num(s["SEATS"])), ("Open since", long_day(s["OPENED_ON"]))]
    kv = "".join(f"<div><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in filter(None, rows))
    return (f'<details class="site" data-site><summary>{uk_map.svg(s["LAT"], s["LON"], s["CITY"], "mini")}<span class="sl"><b>🏢 Site {esc(s["SITE_ID"])}'
            f'<span data-cp="{esc(s["SITE_ID"])}"></span></b><span>{esc(line)}</span></span><span class="chev">▾</span></summary>'
            f'<div class="sbody">{uk_map.svg(s["LAT"], s["LON"], s["CITY"], "big")}<dl class="skv">{kv}</dl></div></details>')


PULSE = (("acc", "Accepted as is", "✅", True), ("removed", "Removed", "🧹", False), ("added", "Added", "➕", False))


def pulse_html(p: dict | None) -> str:
    """Before and since the new forecasts as three wide tiles (value before -> value now, the change coloured by whether
    it is good, two thin bars to compare at a glance) and one line naming the windows."""
    if not p:
        return ""
    a, b = p["before"], p["after"]
    tiles = []
    for k, label, icon, up_good in PULSE:
        x, y = a.get(k), b.get(k)
        if x is None and y is None:
            continue
        d = None if x is None or y is None else round(y - x, 1)
        kind = "flat" if d is None or abs(d) < 0.05 else ("good" if (d > 0) == up_good else "bad")
        sign = "–" if d is None else ("=" if kind == "flat" else ("▲" if d > 0 else "▼"))
        dtxt = "" if d is None else f"{sign} {abs(d):.1f} pp"
        mx = max(x or 0, y or 0, 1)
        tip_html = tip(label, f"{p['before_label']}: {percent(x)}", f"{p['after_label']}: {percent(y)}")
        tiles.append(f'<div class="pt" data-tip="{tip_attr(tip_html)}">'
                     f'<div class="ph">{icon} {esc(label)}</div><div class="pv"><b>{percent(y)}</b><span class="pd {kind}">{dtxt}</span></div>'
                     f'<div class="pa">was {percent(x)}</div><div class="pb"><span style="width:{(x or 0) / mx * 100:.0f}%"></span>'
                     f'<span class="now {kind}" style="width:{(y or 0) / mx * 100:.0f}%"></span></div></div>')
    if not tiles:
        return ""
    head = (f'Before and since the new forecasts (since {long_day(p["since"])})' if p.get("since")
            else "This site is not on the new forecasts yet: the latest 30 days against the 30 before")
    foot = (f'{esc(p["before_label"])}: {short_day(a["from"])} to {short_day(a["to"])} ({a["n"]} lines) · '
            f'{esc(p["after_label"])}: {short_day(b["from"])} to {short_day(b["to"])} ({b["n"]} lines)')
    return f'<section class="pulse"><div class="phd">📈 {esc(head)}</div><div class="pts">{"".join(tiles)}</div><div class="pft">{foot}</div></section>'


def hero(title: str, *, product: str, eyebrow: str = "", lead: str = "", chips: tuple = (), demo: bool = False,
         site: dict | None = None, pulse: dict | None = None) -> str:
    """The page header: the brand tile, the report's name top right, the title, one lead line, chips with the key facts,
    and for a site report its collapsed card and its pulse. A chip given as (text, value) gets a copy icon for the value."""
    env = {} if demo else backends.settings()   # a demo page always says Acme: made-up numbers never carry a real name
    org, team = env.get("BOB_REPORT_ORG") or "Acme", env.get("BOB_REPORT_TEAM") or "Workforce analytics"
    name = PRODUCTS.get(product, product)
    chips = ((DEMO_CHIP,) + tuple(chips)) if demo else tuple(chips)
    chip_html = "".join(f'<span data-cp="{esc(c[1])}">{esc(c[0])}</span>' if isinstance(c, tuple) else f"<span>{esc(c)}</span>"
                        for c in chips)
    return (f'<header class="hero"><div class="in"><div class="brand"><span class="marks">{_ICON}<span class="div"></span>'
            f'<span class="team"><b>{esc(org)}</b><span>{esc(team)}</span></span></span>'
            f'<span class="kind"><small>{esc(MARK)}</small>{esc(name)}</span></div>'
            f'<div class="eyebrow"><span class=pk>{esc(name)} · </span>{esc(eyebrow)}</div><h1>{esc(title)}</h1>'
            + (f'<p class="lead">{esc(lead)}</p>' if lead else "")
            + (f'<div class="chips">{chip_html}</div>' if chip_html else "")
            + site_card(site) + pulse_html(pulse) + "</div></header>")


def page(title: str, body: str, data, script: str, css: str = "", header: str = "") -> str:
    """The whole page: shared CSS + JS, the header, the data as JSON, the page's own body and script."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{esc(title)}</title><style>{CSS.replace("__MAX_W__", str(MAX_W))}{css}</style></head>
<body>{header}<div class="wrap">{body}</div><div id="tip"></div><div id="toast"></div>
<script type="application/json" id="data">{_json_for_script(data)}</script>
<script>{JS}
{script}</script></body></html>
"""


def file_stem(kind: str, parts: list[str]) -> str:
    """A safe file name stem: <kind>_<parts>; the caller adds the timestamp."""
    return "_".join(_UNSAFE.sub("-", str(x)).strip("-.") for x in [kind, *parts] if str(x).strip())


def view_url(file_url: str) -> str:
    """The SharePoint file viewer's address: the plain URL downloads, `?web=1` opens and runs the page."""
    return file_url + ("&" if "?" in file_url else "?") + "web=1"


# Shared by the reports below: the before / after numbers of a site, from site_pulse rows.
def pulse_from_rows(rows: list[dict], since: str | None, last: str) -> dict | None:
    """{before, after, since, labels}. Measured over 30 days each side of the boundary (or of the last 30 days)."""
    if not rows:
        return None
    from datetime import timedelta  # noqa: PLC0415

    end = date.fromisoformat(last)
    pivot = date.fromisoformat(since[:10]) if since else end - timedelta(days=29)
    win = lambda a, b: [r for r in rows if a <= r["BIZ_DAY"] <= b]  # noqa: E731
    before = win((pivot - timedelta(days=30)).isoformat(), (pivot - timedelta(days=1)).isoformat())
    after = win(pivot.isoformat(), min(end, pivot + timedelta(days=29)).isoformat())

    def block(part: list[dict]) -> dict:
        days = sorted(r["BIZ_DAY"] for r in part)
        count = {}
        for r in part:
            if r["OUTCOME"] != "skipped":
                count[r["OUTCOME"]] = count.get(r["OUTCOME"], 0) + r["LINES"]
        total = sum(count.values())
        share = lambda k: round(count.get(k, 0) / total * 100, 1) if total else None  # noqa: E731
        return {"acc": share("accepted"), "removed": share("removed"), "added": share("added"), "n": total,
                "from": days[0] if days else None, "to": days[-1] if days else None}
    a, b = block(before), block(after)
    if not a["n"] or not b["n"]:
        return None
    return {"before": a, "after": b, "since": since, "before_label": "before", "after_label": "since" if since else "latest"}
