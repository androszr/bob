# ruff: noqa: E501  -- the page's stylesheet, one string; its lines are not Python
"""The stylesheet every report page shares: phone first, 12 px gutter, 24 px from 720 px, nothing fixed-width."""

CSS = """
:root{--bg:#f4f6f8;--card:#fff;--ink:#1a1a1a;--ink2:#5c5c5c;--ink3:#8a8a8a;--line:#e6e9ec;--soft:#f2f4f6;
--green:#1f9d55;--green2:#5cc488;--green3:#a9e2c0;--red:#b81414;--redbg:#fde8e8;--removed:#f1a9a9;--added:#a9c8f1;
--blue:#2f6fdb;--amber:#8a5a00;--amberbg:#fff3d6;--up:#0d732f;--down:#b81414}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
/* the SharePoint viewer injects absolutely placed "comment on this graphic" buttons at every graphic, also at ones
   scrolled sideways inside a .scroll box - they must not widen the page */
html,body{overflow-x:clip}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,'Segoe UI',Roboto,Inter,Helvetica,
Arial,sans-serif}
.wrap{max-width:__MAX_W__px;margin:0 auto;padding:12px 12px 120px}
@media(min-width:720px){.wrap{padding:24px 24px 120px}body{font-size:14px}}
h1{font-size:19px;line-height:1.25;margin:0 0 4px}h2{font-size:16px;margin:22px 0 8px}h3{font-size:14px;margin:0 0 6px}
.sub{color:var(--ink2);font-size:13px}.hint{color:var(--ink2);font-size:12.5px;margin:-2px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0}
@media(min-width:720px){.card{padding:16px 18px}}
.muted{color:var(--ink2)}.small{font-size:12.5px}.nowrap{white-space:nowrap}.num{font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--ink2)}
.tabs{display:flex;gap:6px;overflow-x:auto;margin:12px 0 4px;scrollbar-width:none}.tabs::-webkit-scrollbar{display:none}
.tabs button,.seg button{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--card);color:var(--ink);
border-radius:999px;padding:7px 14px;min-height:36px;white-space:nowrap;cursor:pointer}
.tabs button.on,.seg button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.seg{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px}.seg button{font-size:13px;padding:5px 12px;min-height:34px}
.kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
@media(min-width:720px){.kpis{grid-template-columns:repeat(4,minmax(0,1fr))}}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.kpi .t{font-size:12px;color:var(--ink2);line-height:1.25}.kpi .v{font-size:22px;font-weight:700;margin:2px 0}
.kpi .d{font-size:13px;font-weight:600}.kpi .c{font-size:12px;color:var(--ink2)}
.kpi.main{border-color:var(--green);box-shadow:inset 3px 0 0 var(--green)}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.stack{position:relative;display:flex;height:26px;border-radius:6px;overflow:hidden;background:var(--soft)}
.stack.thin{height:16px;border-radius:4px}.stack i{display:block;height:100%;position:relative;cursor:pointer;
font-style:normal;font-size:11.5px;line-height:26px;text-align:center;color:#2a2a2a;white-space:nowrap;overflow:hidden}
.stack.thin i{line-height:16px;font-size:10px}.stack i.dk{color:#fff;font-weight:600}
.stack u{position:absolute;top:0;bottom:0;width:2px;background:#1a1a1a;opacity:.6;pointer-events:none}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12.5px;color:var(--ink2);margin:8px 0}
.rows .row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;padding:8px 0;border-top:1px solid var(--line);
cursor:pointer}.rows .row:first-child{border-top:none}.rows .row .l{font-weight:600;min-width:0;overflow-wrap:anywhere}.rows .row .r{text-align:right;white-space:nowrap}
.rows .row .b{grid-column:1/-1}.rows .row .x{grid-column:1/-1;display:none;font-size:12.5px;color:var(--ink2)}
.rows .row.open .x{display:block}
.badge{display:inline-block;font-size:11px;line-height:16px;font-weight:700;padding:0 6px;border-radius:999px;
background:#eef1f3;color:#4a4a4a;white-space:nowrap;vertical-align:1px}.badge.top{background:#e8f5ec;color:#0d732f}
.badge.topf{background:var(--green);color:#fff}.badge.danger{background:var(--redbg);color:var(--red)}
.badge.warning{background:var(--amberbg);color:var(--amber)}
.list .it{display:grid;grid-template-columns:28px minmax(0,1fr) auto;gap:0 8px;padding:8px 0;border-top:1px solid var(--line)}
.list .it:first-child{border-top:none}.list .it .o{color:var(--ink3);text-align:right}.list .it .nm{font-weight:600}
.list .it .meta{font-size:12.5px;color:var(--ink2)}.list .it .n{text-align:right;font-weight:700;white-space:nowrap}
.list .it .n small{display:block;font-weight:600;font-size:12px}
input.search{font:inherit;width:100%;border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-height:38px;
background:var(--card)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;
background:linear-gradient(to right,var(--card) 30%,rgba(255,255,255,0)),linear-gradient(to left,var(--card) 30%,rgba(255,255,255,0)) 100% 0,
radial-gradient(farthest-side at 0 50%,rgba(16,24,40,.18),transparent),radial-gradient(farthest-side at 100% 50%,rgba(16,24,40,.18),transparent) 100% 0;
background-repeat:no-repeat;background-size:40px 100%,40px 100%,14px 100%,14px 100%;background-attachment:local,local,scroll,scroll}
table.t{border-collapse:collapse;font-size:13px;width:100%}table.t th,table.t td{padding:6px 8px;border-bottom:1px solid
var(--line);text-align:right;white-space:nowrap}table.t th:first-child,table.t td:first-child{text-align:left}
table.t th{font-size:12px;color:var(--ink2);font-weight:600}
.warn{font-size:13px;color:var(--amber);background:#fff6e0;border-radius:8px;padding:8px 10px;margin:8px 0}
.foot{font-size:12px;color:var(--ink3);margin-top:20px;line-height:1.5}
#tip{position:fixed;z-index:9;max-width:min(320px,calc(100vw - 24px));background:#1a1a1a;color:#fff;font-size:12.5px;
line-height:1.4;border-radius:8px;padding:7px 10px;pointer-events:none;display:none;box-shadow:0 4px 16px rgba(0,0,0,.2)}
#tip b{color:#fff}
.cp{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;margin:-4px 0 -4px 2px;padding:0;
border:none;background:none;color:var(--ink3);cursor:pointer;border-radius:5px;vertical-align:middle}
.cp:hover{color:var(--ink);background:var(--soft)}.cp svg{width:13px;height:13px}.cp.ok{color:var(--green)}
.hero .cp{color:#C9EFD0}.hero .cp:hover{background:rgba(255,255,255,.12);color:#fff}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#1a1a1a;color:#fff;font-size:13px;
padding:7px 14px;border-radius:999px;display:none;z-index:10}
.hero{position:relative;overflow:hidden;color:#fff;background:radial-gradient(120% 140% at 100% 0%,#1F7A5C 0%,
rgba(31,122,92,0) 55%),linear-gradient(160deg,#0B2112 0%,#123326 45%,#18463A 100%)}
.hero:before{content:"";position:absolute;inset:0;background:repeating-linear-gradient(135deg,rgba(255,255,255,.035) 0 1px,
transparent 1px 14px);pointer-events:none}
.hero:after{content:"";position:absolute;left:0;right:0;bottom:0;height:3px;
background:linear-gradient(90deg,#9BE8A8,#5EC8B0 50%,#3F8FD6)}
.hero .in{position:relative;max-width:__MAX_W__px;margin:0 auto;padding:14px 12px 20px}
@media(min-width:720px){.hero .in{padding:20px 24px 30px}}
.brand{display:flex;align-items:center;justify-content:space-between;gap:10px}
.marks{display:inline-flex;align-items:center;gap:10px;background:#fff;border-radius:12px;padding:6px 12px 6px 8px;
box-shadow:0 6px 20px rgba(0,0,0,.25)}
.marks img{display:block;width:28px;height:28px;object-fit:contain}.marks .div{width:1px;height:26px;background:#e3e6e9}
.marks .team{display:flex;flex-direction:column;color:#5c5c5c;font-size:10.5px;line-height:1.25;white-space:nowrap}
.marks .team b{color:#1a1a1a;font-size:11.5px;letter-spacing:.2px}
.brand .kind{font-size:15px;font-weight:700;letter-spacing:.2px;color:#fff;text-align:right;line-height:1.2}
.brand .kind small{display:block;font-size:10px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#9BE8A8}
.eyebrow .pk{display:none}
.hero .eyebrow{margin:18px 0 4px;font-size:12px;letter-spacing:1.4px;text-transform:uppercase;color:#9BE8A8;font-weight:600}
.hero h1{font-size:24px;line-height:1.15;margin:0;font-weight:700;letter-spacing:-.3px}
@media(min-width:720px){.hero h1{font-size:30px}.hero .eyebrow{margin-top:24px}}
.hero .lead{margin:6px 0 0;color:#D3E8DA;font-size:13.5px;max-width:760px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}.chips span{font-size:12px;color:#EDF3EE;
background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.18);border-radius:999px;padding:3px 10px;
backdrop-filter:blur(4px)}
@media(max-width:600px){.brand .kind{display:none}.eyebrow .pk{display:inline}}
.site{margin-top:14px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.18);border-radius:14px;
backdrop-filter:blur(6px);color:#EDF3EE}
.site summary{list-style:none;display:flex;align-items:center;gap:12px;padding:8px 12px;cursor:pointer}
.site summary::-webkit-details-marker{display:none}
.site .pl{color:#C9EFD0;flex:none}.site .pl.mini{width:34px;height:auto}.site[open] .pl.mini{display:none}
.site .sl{display:flex;flex-direction:column;min-width:0;flex:1;font-size:13px;line-height:1.35}
.site .sl b{font-size:14px;color:#fff}.site .sl>span{color:#D3E8DA}
.site .roles{display:flex;gap:4px;flex-wrap:wrap}.site .ri{font-size:18px;line-height:1;cursor:help}
.site .chev{transition:transform .2s;color:#C9EFD0}.site[open] .chev{transform:rotate(180deg)}
.site .sbody{display:grid;grid-template-columns:minmax(0,1fr);gap:4px 18px;padding:0 12px 12px}
@media(min-width:720px){.site .sbody{grid-template-columns:200px minmax(0,1fr)}}
.site .pl.big{width:100%;max-width:170px;height:auto;justify-self:center;align-self:start;margin-top:4px}
.site .sbody>dl:only-child{grid-column:1/-1}
dl.skv{margin:0;display:grid;grid-template-columns:minmax(0,1fr);gap:0 22px;font-size:13px}
@media(min-width:900px){dl.skv{grid-template-columns:1fr 1fr}}
dl.skv>div{display:flex;gap:6px 14px;justify-content:space-between;padding:6px 0;border-top:1px solid rgba(255,255,255,.12)}
dl.skv dt{color:#A9D8B8;flex:0 0 38%;margin:0}dl.skv dd{margin:0;text-align:right;flex:1;min-width:0;overflow-wrap:anywhere}
dl.skv a{color:#fff;text-decoration:none;border-bottom:1px dotted rgba(255,255,255,.4)}dl.skv .muted{color:#A2B2A7}
.site .cp{color:#C9EFD0}
.pulse{margin-top:10px}.pulse .phd{font-size:11.5px;letter-spacing:1px;text-transform:uppercase;color:#A9D8B8;margin:0 0 6px}
.pulse .pts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.pulse .pt{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.18);border-radius:12px;padding:8px 9px;
min-width:0;cursor:help}
.pulse .ph{font-size:11.5px;color:#D3E8DA;line-height:1.25}.pulse .pv{display:flex;align-items:center;flex-wrap:wrap;gap:2px 6px;margin-top:3px}
.pulse .pv b{font-size:20px;color:#fff;letter-spacing:-.3px;line-height:1.1}
.pulse .pa{font-size:11.5px;color:#A2B2A7;margin-top:1px}
.pulse .pd{font-size:11px;font-weight:700;border-radius:999px;padding:0 6px;background:rgba(255,255,255,.12);color:#EDF3EE;
white-space:nowrap}.pulse .pd.good{background:#9BE8A8;color:#0B2112}.pulse .pd.bad{background:#ff8f8f;color:#3a0606}
.pulse .pb span{display:block;height:4px;border-radius:3px;background:rgba(255,255,255,.28);margin-top:3px}
.pulse .pb span.now{background:#D3E8DA}.pulse .pb span.now.good{background:#9BE8A8}.pulse .pb span.now.bad{background:#ff8f8f}
.pulse .pft{font-size:11px;color:#A9D8B8;margin-top:6px}
@media(min-width:720px){.pulse .pt{padding:10px 14px}.pulse .ph{font-size:12.5px}.pulse .pv b{font-size:26px}
.pulse .pd{font-size:12px;padding:1px 8px}.pulse .pa{font-size:12.5px}.pulse .pts{gap:8px}}
[hidden]{display:none!important}
button.more{display:block;width:100%;margin:10px 0 2px;font:inherit;font-size:13.5px;font-weight:600;min-height:40px;
border:1px dashed #cfd6dc;border-radius:10px;background:#fafbfc;color:var(--ink);cursor:pointer}button.more:hover{background:var(--soft)}@media(max-width:360px){.marks .team span{display:none}}
.card,.kpi{box-shadow:0 1px 2px rgba(16,24,40,.04),0 6px 18px rgba(16,24,40,.05);border-color:#e9ecef}
.kpi .v{letter-spacing:-.4px}@media(min-width:720px){.kpi .v{font-size:26px}h2{font-size:17px;margin-top:28px}}
h2{letter-spacing:-.2px}
"""
