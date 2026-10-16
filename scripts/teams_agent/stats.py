"""Bob Stats -- aggregate per-chat usage from log.jsonl and perf-metrics.jsonl.

Usage:
    python scripts/teams_agent/stats.py [--log LOG] [--since YYYY-MM-DD]
    python scripts/teams_agent/stats.py --post [--since today]

--post:  post to Teams Notatki chat (or edit the existing pinned message).
         Requires cli.py already running (Bearer token must be set in api_client).
         State (message ID) is saved to stats-state.json.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 -- side-effect: CET timestamps na stdout/stderr

_LOG_FILE = Path(__file__).parent / "log.jsonl"
_PERF_LOG = Path(__file__).parent / "perf-metrics.jsonl"
_OTEL_LOG = Path(__file__).parent.parent / "otel" / "otel-spans.jsonl"
_CHAT_CONFIG = Path(__file__).resolve().parents[2] / "chat-config.json"
_STATS_STATE = Path(__file__).parent / "stats-state.json"
_CET = timezone(timedelta(hours=2))  # CEST (summer); use +1 in winter
_NOTATKI_CONV_ID = "48:notes"


def _cet_now() -> datetime:
    return datetime.now(_CET)


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _load_chat_names() -> list[str]:
    try:
        data = json.loads(_CHAT_CONFIG.read_text(encoding="utf-8"))
        return list(data.get("chats", {}).keys())
    except Exception:
        return []


def compute_stats(log_path: Path, since: datetime | None = None) -> dict:
    """Parse log.jsonl, return per-chat stats dict."""
    chats: dict[str, dict] = defaultdict(lambda: {
        "sent": 0,
        "skipped": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "first_ts": None,
        "last_ts": None,
    })

    for raw in log_path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            continue

        ts = _parse_ts(r.get("ts", ""))
        if since and ts and ts < since:
            continue

        chat = r.get("chat_name") or ""
        action = r.get("action", "")

        if action == "watch_send":
            s = chats[chat]
            if r.get("send_ok"):
                s["sent"] += 1
            else:
                s["skipped"] += 1
            s["input_tokens"] += _estimate_tokens(r.get("incoming_text", ""))
            s["output_tokens"] += _estimate_tokens(r.get("reply", ""))
            if ts:
                if s["first_ts"] is None or ts < s["first_ts"]:
                    s["first_ts"] = ts
                if s["last_ts"] is None or ts > s["last_ts"]:
                    s["last_ts"] = ts

        elif action == "watch_skip_empty":
            chats[chat]["skipped"] += 1

    return dict(chats)


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def _fmt_ts(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    local = dt.astimezone(_CET)
    return local.strftime("%d.%m %H:%M")


def _percentile(values: list[float], p: float) -> float:
    """Return p-th percentile of values using nearest-rank method. Empty -> 0.0."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    idx = max(0, min(n - 1, int(p / 100.0 * n + 0.5) - 1))
    return s[idx]


def _fmt_cost(usd: float) -> str:
    """Format cost in USD. Small values use 4 decimal places, large use 2."""
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


# ---------------------------------------------------------------------------
# Perf aggregation
# ---------------------------------------------------------------------------


def aggregate_perf(perf_path: Path, since: datetime) -> dict:
    """Read perf-metrics.jsonl, aggregate perf data since `since` (midnight CET).

    Returns dict with keys:
        available: bool
        total: dict of aggregated sums + percentiles + cache_hit_rate
        per_chat: dict[str, dict]
        per_model: dict[str, dict]
        count: int

    Defensive: missing file -> {"available": False}. Bad lines skipped silently.
    """
    if not perf_path.exists():
        return {"available": False}

    def _empty_bucket() -> dict:
        return {
            "count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.0,
            "duration_ms_list": [],
            "ttft_ms_list": [],
        }

    total = _empty_bucket()
    per_chat: dict[str, dict] = defaultdict(_empty_bucket)
    per_model: dict[str, dict] = defaultdict(_empty_bucket)

    def _add_to(bucket: dict, r: dict) -> None:
        bucket["count"] += 1
        bucket["input_tokens"] += r.get("input_tokens") or 0
        bucket["output_tokens"] += r.get("output_tokens") or 0
        bucket["cache_read_input_tokens"] += r.get("cache_read_input_tokens") or 0
        bucket["cache_creation_input_tokens"] += r.get("cache_creation_input_tokens") or 0
        bucket["total_cost_usd"] += r.get("total_cost_usd") or 0.0
        dm = r.get("duration_ms")
        if isinstance(dm, (int, float)):
            bucket["duration_ms_list"].append(float(dm))
        tm = r.get("ttft_ms")
        if isinstance(tm, (int, float)):
            bucket["ttft_ms_list"].append(float(tm))

    try:
        with perf_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(r.get("ts", ""))
                if ts is None or ts < since:
                    continue
                _add_to(total, r)
                ctx = r.get("ctx") or {}
                chat_name = ctx.get("chat", "") if isinstance(ctx, dict) else ""
                _add_to(per_chat[chat_name], r)
                model_name = r.get("model") or ""
                _add_to(per_model[model_name], r)
    except Exception:
        return {"available": False}

    if total["count"] == 0:
        return {
            "available": True,
            "total": total,
            "per_chat": dict(per_chat),
            "per_model": dict(per_model),
            "count": 0,
        }

    # Compute percentiles globally
    dm_list = total["duration_ms_list"]
    tm_list = total["ttft_ms_list"]
    total["p50_duration_ms"] = _percentile(dm_list, 50)
    total["p95_duration_ms"] = _percentile(dm_list, 95)
    total["p50_ttft_ms"] = _percentile(tm_list, 50)
    total["p95_ttft_ms"] = _percentile(tm_list, 95)

    # Cache hit-rate = cache_read / (input + cache_read + cache_creation)
    inp = total["input_tokens"]
    cr = total["cache_read_input_tokens"]
    cc = total["cache_creation_input_tokens"]
    total["cache_hit_rate"] = cr / max(1, inp + cr + cc)

    return {
        "available": True,
        "total": total,
        "per_chat": dict(per_chat),
        "per_model": dict(per_model),
        "count": total["count"],
    }


# ---------------------------------------------------------------------------
# OTEL aggregation
# ---------------------------------------------------------------------------


def aggregate_otel(otel_path: Path, since: datetime) -> dict:
    """Read otel-spans.jsonl, aggregate OTEL data since `since` (midnight CET).

    Returns dict with keys:
        available: bool
        per_tool: dict[str, dict]  -- tool_name -> {count, duration_ms_list, error_count}
        per_source: dict[str, int] -- source -> span count
        trend: dict[str, dict]     -- date_str (YYYY-MM-DD CET) -> {count, cost_usd, duration_ms}
        sessions: list[dict]       -- per session_id aggregates

    Defensive: missing file -> {"available": False}. Bad lines skipped silently.
    """
    if not otel_path.exists():
        return {"available": False}

    per_tool: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "duration_ms_list": [],
        "error_count": 0,
    })
    per_source: dict[str, int] = defaultdict(int)
    trend: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "cost_usd": 0.0,
        "duration_ms": 0.0,
    })
    # per_session: session_id -> aggregate
    per_session: dict[str, dict] = defaultdict(lambda: {
        "tokens": 0,
        "cost_usd": 0.0,
        "span_count": 0,
        "day": "",
    })

    try:
        with otel_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(r.get("ts", ""))
                if ts is None or ts < since:
                    continue

                duration_ms = r.get("duration_ms") or 0.0
                success = r.get("success", True)
                source = r.get("source", "dev")
                session_id = r.get("session_id", "")
                tool_name = r.get("tool_name")
                tokens = r.get("tokens") or 0
                cost_usd = r.get("cost_usd") or 0.0

                # per_tool (only for tool_use spans with a tool_name)
                if tool_name:
                    bucket = per_tool[tool_name]
                    bucket["count"] += 1
                    if isinstance(duration_ms, (int, float)):
                        bucket["duration_ms_list"].append(float(duration_ms))
                    if not success:
                        bucket["error_count"] += 1

                # per_source
                per_source[source] += 1

                # trend (key = CET date)
                day_key = ts.astimezone(_CET).strftime("%Y-%m-%d")
                day_bucket = trend[day_key]
                day_bucket["count"] += 1
                day_bucket["cost_usd"] += cost_usd
                day_bucket["duration_ms"] += float(duration_ms) if isinstance(
                    duration_ms, (int, float)
                ) else 0.0

                # per_session
                if session_id:
                    sb = per_session[session_id]
                    sb["tokens"] += tokens
                    sb["cost_usd"] += cost_usd
                    sb["span_count"] += 1
                    if not sb["day"]:
                        sb["day"] = day_key

    except Exception:
        return {"available": False}

    # Compute per-tool percentiles + error_rate
    per_tool_out: dict[str, dict] = {}
    for tname, tb in per_tool.items():
        dl = tb["duration_ms_list"]
        count = tb["count"]
        error_count = tb["error_count"]
        per_tool_out[tname] = {
            "count": count,
            "p50_duration_ms": _percentile(dl, 50),
            "p95_duration_ms": _percentile(dl, 95),
            "error_rate": (error_count / count) if count > 0 else 0.0,
        }

    sessions_out = [
        {"session_id": sid, **vals}
        for sid, vals in sorted(per_session.items(), key=lambda x: x[1]["day"])
    ]

    return {
        "available": True,
        "per_tool": per_tool_out,
        "per_source": dict(per_source),
        "trend": dict(trend),
        "sessions": sessions_out,
    }


# ---------------------------------------------------------------------------
# Plain-text render (terminal)
# ---------------------------------------------------------------------------

def render(stats: dict, configured_chats: list[str]) -> str:
    now = _cet_now().strftime("%Y-%m-%d %H:%M CET")
    lines = [
        f"BOB STATS -- {now}",
        "=" * 68,
        f"{'Chat':<28} {'Wysl':>5} {'Pom':>4} {'~Inp':>6} {'~Out':>6} {'Ostatnia'}",
        "-" * 68,
    ]

    all_chats = list(dict.fromkeys(configured_chats + list(stats.keys())))

    total_sent = total_skip = total_inp = total_out = 0

    for chat in all_chats:
        if not chat:
            continue
        s = stats.get(chat, {})
        sent = s.get("sent", 0)
        skip = s.get("skipped", 0)
        inp = s.get("input_tokens", 0)
        out = s.get("output_tokens", 0)
        last = _fmt_ts(s.get("last_ts"))
        total_sent += sent
        total_skip += skip
        total_inp += inp
        total_out += out
        lines.append(
            f"{chat:<28} {sent:>5} {skip:>4} {_fmt_tokens(inp):>6} {_fmt_tokens(out):>6}  {last}"
        )

    lines.append("-" * 68)
    lines.append(
        f"{'SUMA':<28} {total_sent:>5} {total_skip:>4} "
        f"{_fmt_tokens(total_inp):>6} {_fmt_tokens(total_out):>6}"
    )
    lines.append("")
    lines.append("* tokeny szacowane: 1 token ~ 4 znaki. Nie uwzglednia sys-prompt/knowledge.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML render (Teams)
# ---------------------------------------------------------------------------

def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(  # noqa: PLR0912, PLR0915
    stats: dict,
    configured_chats: list[str],
    since_label: str = "",
    perf: dict | None = None,
    otel: dict | None = None,
) -> str:
    """Render stats as Teams-compatible HTML (rich table + perf + otel sections)."""
    now = _cet_now().strftime("%Y-%m-%d %H:%M CET")

    # Only rows with activity for the HTML view (skip dead chats), sorted by last activity.
    all_chats = list(dict.fromkeys(configured_chats + list(stats.keys())))
    _min_dt = datetime.min.replace(tzinfo=UTC)
    active = [(c, stats[c]) for c in all_chats if c and stats.get(c, {}).get("sent", 0) > 0]
    active.sort(key=lambda x: x[1].get("last_ts") or _min_dt, reverse=True)

    total_sent = sum(s["sent"] for _, s in active)
    total_skipped = sum(s.get("skipped", 0) for _, s in active)

    since_note = f" &nbsp;|&nbsp; od {_e(since_label)}" if since_label else ""

    td = '<td style="padding:2px 10px;border:1px solid #ddd;">'
    td_r = '<td style="padding:2px 10px;border:1px solid #ddd;text-align:right;">'
    th = '<th style="padding:4px 10px;border:1px solid #bbb;background:#f4f4f4;text-align:left;">'
    th_r = '<th style="padding:4px 10px;border:1px solid #bbb;background:#f4f4f4;text-align:right;">'
    tfoot_td = 'style="padding:4px 10px;border:1px solid #bbb;background:#f9f9f9;"'
    tfoot_td_r = (
        'style="padding:4px 10px;border:1px solid #bbb;background:#f9f9f9;text-align:right;"'
    )

    # --- Section 1: Aktywnosc (from log.jsonl) ---
    act_rows = []
    for chat, s in active:
        last = _fmt_ts(s.get("last_ts"))
        act_rows.append(
            f"<tr>"
            f"{td}{_e(chat)}</td>"
            f"{td_r}{s['sent']}</td>"
            f"{td_r}{s.get('skipped', 0)}</td>"
            f"{td}{_e(last)}</td>"
            f"</tr>"
        )

    act_section = (
        f"<p><strong>Aktywność</strong></p>"
        f"<table style='border-collapse:collapse;font-size:0.95em;'>"
        f"<thead><tr>"
        f"{th}Chat</th>{th_r}Wysłane</th>{th_r}Pominięte</th>{th}Ostatnia aktyw.</th>"
        f"</tr></thead>"
        f"<tbody>{''.join(act_rows)}</tbody>"
        f"<tfoot><tr>"
        f"<td {tfoot_td}><strong>SUMA</strong></td>"
        f"<td {tfoot_td_r}><strong>{total_sent}</strong></td>"
        f"<td {tfoot_td_r}><strong>{total_skipped}</strong></td>"
        f"<td {tfoot_td}></td>"
        f"</tr></tfoot>"
        f"</table>"
    )

    # --- Perf sections (2-6) ---
    perf_available = perf is not None and perf.get("available", False)
    no_perf_note = (
        "<p style='font-size:0.9em;'>"
        "brak danych perf (czekam na perf-metrics.jsonl)</p>"
    )

    if not perf_available:
        perf_section = no_perf_note
    else:
        t = perf.get("total", {})
        per_chat_p = perf.get("per_chat", {})
        per_model_p = perf.get("per_model", {})

        # --- Section 2: Latencja ---
        def _ms(v: object) -> str:
            if isinstance(v, (int, float)) and v > 0:
                return f"{v:.0f} ms"
            return "-"

        lat_section = (
            f"<p><strong>Latencja</strong></p>"
            f"<table style='border-collapse:collapse;font-size:0.95em;'>"
            f"<thead><tr>{th}Metryka</th>{th_r}p50</th>{th_r}p95</th></tr></thead>"
            f"<tbody>"
            f"<tr>{td}duration_ms</td>"
            f"{td_r}{_ms(t.get('p50_duration_ms'))}</td>"
            f"{td_r}{_ms(t.get('p95_duration_ms'))}</td></tr>"
            f"<tr>{td}ttft_ms</td>"
            f"{td_r}{_ms(t.get('p50_ttft_ms'))}</td>"
            f"{td_r}{_ms(t.get('p95_ttft_ms'))}</td></tr>"
            f"</tbody></table>"
        )

        # --- Section 3: Tokeny (per chat) ---
        tok_rows = []
        for cname, cb in sorted(per_chat_p.items()):
            tok_rows.append(
                f"<tr>{td}{_e(cname or '(bez chatu)')}</td>"
                f"{td_r}{_fmt_tokens(cb.get('input_tokens', 0))}</td>"
                f"{td_r}{_fmt_tokens(cb.get('output_tokens', 0))}</td>"
                f"{td_r}{_fmt_tokens(cb.get('cache_read_input_tokens', 0))}</td>"
                f"{td_r}{_fmt_tokens(cb.get('cache_creation_input_tokens', 0))}</td>"
                f"</tr>"
            )
        tok_section = (
            f"<p><strong>Tokeny (realne)</strong></p>"
            f"<table style='border-collapse:collapse;font-size:0.95em;'>"
            f"<thead><tr>"
            f"{th}Chat</th>{th_r}Input</th>{th_r}Output</th>"
            f"{th_r}Cache read</th>{th_r}Cache create</th>"
            f"</tr></thead>"
            f"<tbody>{''.join(tok_rows)}</tbody>"
            f"<tfoot><tr>"
            f"<td {tfoot_td}><strong>SUMA</strong></td>"
            f"<td {tfoot_td_r}><strong>{_fmt_tokens(t.get('input_tokens', 0))}</strong></td>"
            f"<td {tfoot_td_r}><strong>{_fmt_tokens(t.get('output_tokens', 0))}</strong></td>"
            f"<td {tfoot_td_r}>"
            f"<strong>{_fmt_tokens(t.get('cache_read_input_tokens', 0))}</strong></td>"
            f"<td {tfoot_td_r}>"
            f"<strong>{_fmt_tokens(t.get('cache_creation_input_tokens', 0))}</strong></td>"
            f"</tr></tfoot>"
            f"</table>"
        )

        # --- Section 4: Koszt ---
        cost_rows = []
        for cname, cb in sorted(per_chat_p.items()):
            cost_rows.append(
                f"<tr>{td}{_e(cname or '(bez chatu)')}</td>"
                f"{td_r}{_fmt_cost(cb.get('total_cost_usd', 0.0))}</td>"
                f"</tr>"
            )
        cost_model_rows = []
        for mname, mb in sorted(per_model_p.items()):
            cost_model_rows.append(
                f"<tr>{td}{_e(mname or '(nieznany)')}</td>"
                f"{td_r}{mb.get('count', 0)}</td>"
                f"{td_r}{_fmt_cost(mb.get('total_cost_usd', 0.0))}</td>"
                f"</tr>"
            )
        cost_section = (
            f"<p><strong>Koszt</strong></p>"
            f"<table style='border-collapse:collapse;font-size:0.95em;'>"
            f"<thead><tr>{th}Chat</th>{th_r}Koszt</th></tr></thead>"
            f"<tbody>{''.join(cost_rows)}</tbody>"
            f"<tfoot><tr>"
            f"<td {tfoot_td}><strong>SUMA dzienna</strong></td>"
            f"<td {tfoot_td_r}>"
            f"<strong>{_fmt_cost(t.get('total_cost_usd', 0.0))}</strong></td>"
            f"</tr></tfoot>"
            f"</table>"
        )

        # --- Section 5: Cache hit-rate ---
        hit_rate = t.get("cache_hit_rate", 0.0)
        cache_section = (
            f"<p><strong>Cache hit-rate: {hit_rate:.1%}</strong>"
            f" &nbsp;<span style='font-size:0.9em;'>"
            f"(cache_read / input+cache_read+cache_creation)</span></p>"
        )

        # --- Section 6: Breakdown per model ---
        model_section = (
            f"<p><strong>Breakdown per model</strong></p>"
            f"<table style='border-collapse:collapse;font-size:0.95em;'>"
            f"<thead><tr>"
            f"{th}Model</th>{th_r}Odpowiedzi</th>"
            f"{th_r}Input</th>{th_r}Output</th>{th_r}Koszt</th>"
            f"</tr></thead>"
            f"<tbody>{''.join(cost_model_rows)}</tbody>"
            f"</table>"
        )

        perf_section = lat_section + tok_section + cost_section + cache_section + model_section

    # --- OTEL section ---
    otel_available = otel is not None and otel.get("available", False)
    no_otel_note = (
        "<p style='font-size:0.9em;'>"
        "brak danych OTEL (czekam na otel-spans.jsonl)</p>"
    )

    if not otel_available:
        otel_section = no_otel_note
    else:
        per_tool_o = otel.get("per_tool", {})
        trend_o = otel.get("trend", {})
        per_source_o = otel.get("per_source", {})

        # --- OTEL sub-section: Narzedzia ---
        tool_rows = []
        for tname in sorted(per_tool_o.keys()):
            tb = per_tool_o[tname]
            tool_rows.append(
                f"<tr>{td}{_e(tname)}</td>"
                f"{td_r}{tb.get('count', 0)}</td>"
                f"{td_r}{tb.get('p50_duration_ms', 0.0):.0f}</td>"
                f"{td_r}{tb.get('p95_duration_ms', 0.0):.0f}</td>"
                f"{td_r}{tb.get('error_rate', 0.0):.1%}</td>"
                f"</tr>"
            )

        narzedzia_section = (
            f"<p><strong>OTEL: Narzedzia</strong></p>"
            f"<table style='border-collapse:collapse;font-size:0.95em;'>"
            f"<thead><tr>"
            f"{th}Narzedzie</th>{th_r}Wywolania</th>"
            f"{th_r}p50 ms</th>{th_r}p95 ms</th>{th_r}Error %</th>"
            f"</tr></thead>"
            f"<tbody>{''.join(tool_rows) or '<tr><td colspan=5>(brak danych)</td></tr>'}</tbody>"
            f"</table>"
        )

        # --- OTEL sub-section: Trend (day-over-day) ---
        trend_rows = []
        for day in sorted(trend_o.keys()):
            db = trend_o[day]
            trend_rows.append(
                f"<tr>{td}{_e(day)}</td>"
                f"{td_r}{db.get('count', 0)}</td>"
                f"{td_r}{_fmt_cost(db.get('cost_usd', 0.0))}</td>"
                f"{td_r}{db.get('duration_ms', 0.0):.0f}</td>"
                f"</tr>"
            )

        trend_section = (
            f"<p><strong>OTEL: Trend</strong></p>"
            f"<table style='border-collapse:collapse;font-size:0.95em;'>"
            f"<thead><tr>"
            f"{th}Dzien</th>{th_r}Spany</th>{th_r}Koszt</th>{th_r}Total ms</th>"
            f"</tr></thead>"
            f"<tbody>{''.join(trend_rows) or '<tr><td colspan=4>(brak danych)</td></tr>'}</tbody>"
            f"</table>"
        )

        # --- OTEL sub-section: Zrodlo (source breakdown) ---
        source_rows = []
        for src in sorted(per_source_o.keys()):
            source_rows.append(
                f"<tr>{td}{_e(src)}</td>{td_r}{per_source_o[src]}</td></tr>"
            )

        source_section = (
            f"<p><strong>OTEL: Zrodlo</strong></p>"
            f"<table style='border-collapse:collapse;font-size:0.95em;'>"
            f"<thead><tr>{th}Zrodlo</th>{th_r}Spany</th></tr></thead>"
            f"<tbody>{''.join(source_rows) or '<tr><td colspan=2>(brak danych)</td></tr>'}</tbody>"
            f"</table>"
        )

        otel_section = narzedzia_section + trend_section + source_section

    # Build footer note
    if perf_available:
        footer = (
            "<p style='font-size:0.85em;margin-top:4px;'>"
            "&#x2139; tokeny i koszt z perf-metrics.jsonl (realne dane API)</p>"
        )
    else:
        footer = (
            "<p style='font-size:0.85em;margin-top:4px;'>"
            "&#x2139; tokeny szacowane &middot; 1 token &asymp; 4 znaki "
            "&middot; nie uwzgl. sys-prompt &amp; knowledge</p>"
        )

    html = (
        f"<p><strong>&#128202; BOB STATS</strong>"
        f"&nbsp;&nbsp;<span style='font-size:0.9em'>"
        f"aktualizacja: {_e(now)}{since_note}</span></p>"
        f"{act_section}"
        f"{perf_section}"
        f"{otel_section}"
        f"{footer}"
    )
    return html


# ---------------------------------------------------------------------------
# Post / edit to Teams
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        if _STATS_STATE.exists():
            return json.loads(_STATS_STATE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    _STATS_STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def post_or_edit(html: str, conv_id: str = _NOTATKI_CONV_ID) -> None:
    """Post a new daily message or edit today's existing one.

    New CET day -> always send a NEW message so each day keeps its own snapshot
    on the channel (daily history). Within the same day -> edit the message
    posted today in place. The message's day is taken from state["posted_at"].
    """
    # Import here to avoid loading Playwright/session deps when just printing.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import api_client  # noqa: PLC0415

    state = _load_state()
    existing_id = state.get("message_id")
    posted_at = _parse_ts(state.get("posted_at", ""))
    same_day = (
        posted_at is not None
        and posted_at.astimezone(_CET).date() == _cet_now().date()
    )

    if existing_id and existing_id != "unknown" and same_day:
        ok = api_client.edit_message(existing_id, html, conv_id)
        if ok:
            print(f"[stats] wiadomosc edytowana (id={existing_id})", flush=True)
            state["updated_at"] = _cet_now().isoformat()
            _save_state(state)
            return
        print("[stats] edit nie powiodl sie -- probuje nowa wiadomosc", flush=True)

    msg_id = api_client.send_html_message(html, conv_id)
    if msg_id:
        reason = "nowy dzien -- nowa" if (existing_id and not same_day) else "nowa"
        print(f"[stats] {reason} wiadomosc wyslana (id={msg_id})", flush=True)
        _save_state({"message_id": msg_id, "conv_id": conv_id,
                     "posted_at": _cet_now().isoformat()})
    else:
        print("[stats] BLAD: nie udalo sie wyslac wiadomosci", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Bob stats per chat")
    parser.add_argument("--log", default=str(_LOG_FILE))
    parser.add_argument("--since", default=None,
                        help="Filtruj od daty YYYY-MM-DD lub 'today'. "
                             "Np. --since 2026-05-26 albo --since today")
    parser.add_argument("--post", action="store_true",
                        help="Wyslij/edytuj wiadomosc HTML na Teams (Notatki).")
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"Brak pliku logu: {log_path}", file=sys.stderr)
        sys.exit(1)

    since: datetime | None = None
    since_label = ""
    if args.since:
        raw = args.since.strip().lower()
        if raw == "today":
            since = _cet_now().replace(hour=0, minute=0, second=0, microsecond=0)
            since_label = _cet_now().strftime("%d.%m.%Y")
        else:
            try:
                since = datetime.fromisoformat(args.since).replace(tzinfo=_CET)
                since_label = args.since
            except ValueError:
                print(f"Nieprawidlowy format daty: {args.since!r}", file=sys.stderr)
                sys.exit(1)

    # For perf aggregation, use midnight CET even if --since is not specified
    perf_since = since if since is not None else _cet_now().replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    stats = compute_stats(log_path, since=since)
    configured = _load_chat_names()

    if args.post:
        perf = aggregate_perf(_PERF_LOG, perf_since)
        otel = aggregate_otel(_OTEL_LOG, perf_since)
        html = render_html(stats, configured, since_label=since_label, perf=perf, otel=otel)
        post_or_edit(html)
    else:
        print(render(stats, configured))


if __name__ == "__main__":
    main()
