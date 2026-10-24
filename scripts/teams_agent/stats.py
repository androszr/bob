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
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 -- side-effect: CET timestamps na stdout/stderr

_LOG_FILE = Path(__file__).parent / "log.jsonl"
_PERF_LOG = Path(__file__).parent / "perf-metrics.jsonl"
_OTEL_LOG = Path(__file__).parent.parent / "otel" / "otel-spans.jsonl"
_CHAT_CONFIG = Path(__file__).resolve().parents[2] / "chat-config.json"
_STATS_STATE = Path(__file__).parent / "stats-state.json"
_CET = ZoneInfo("Europe/Warsaw")   # was a fixed +2: wrong "today" and times from the winter time change on
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


# The daily Notatki message: read on a phone - one line of headline numbers, what needs attention,
# one channel table, a 7-day trend. Tokens / latency percentiles / OTEL tables moved to the panel (Pulpit).
_KIND = {"watch_send": "odpowiedz", "catchup_send": "odpowiedz", "watch_skip_silent_sentinel": "milczenie",
         "watch_skip_answered": "juz_odpowiedziane"}
_PANEL_URL = f"http://localhost:{os.environ.get('BOB_PANEL_PORT') or '3006'}"
_WD = ("pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz.")


def channel_activity(log_path: Path, since: datetime) -> dict[str, dict]:
    """Per chat since `since`: messages Bob had to react to and how (replied / silent / someone already answered)."""
    out: dict[str, dict] = {}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = _KIND.get(r.get("action") or "")
        chat = r.get("chat_name")
        ts = _parse_ts(r.get("ts", ""))
        if not kind or not isinstance(chat, str) or ts is None or ts < since:
            continue
        if kind == "odpowiedz" and not r.get("send_ok", True):
            continue
        c = out.setdefault(chat, {"wiadomosci": 0, "odpowiedz": 0, "milczenie": 0, "juz_odpowiedziane": 0,
                                  "last_ts": None})
        c["wiadomosci"] += 1
        c[kind] += 1
        if c["last_ts"] is None or ts > c["last_ts"]:
            c["last_ts"] = ts
    return out


def daily_costs(perf_path: Path, days: int = 7, now: datetime | None = None) -> dict:
    """Cost per local day for the last `days` days (today last) + yesterday's cost up to this time of day."""
    now = (now or _cet_now()).astimezone(_CET)
    first = (now - timedelta(days=days - 1)).date()
    per_day = {first + timedelta(days=i): Decimal(0) for i in range(days)}
    yday_so_far = Decimal(0)
    yday_cut = now - timedelta(days=1)
    try:
        fh = perf_path.open(encoding="utf-8")
    except OSError:
        return {"days": per_day, "yday_so_far": None}
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(r.get("ts", ""))
            if ts is None:
                continue
            local = ts.astimezone(_CET)
            try:
                cost = Decimal(str(r.get("total_cost_usd") or 0))      # money never goes through float math
            except ArithmeticError:
                continue
            if local.date() in per_day:
                per_day[local.date()] += cost
            if local.date() == yday_cut.date() and local <= yday_cut:
                yday_so_far += cost
    return {"days": per_day, "yday_so_far": yday_so_far}


def _usd(v: Decimal | float) -> str:
    return f"{v:,.2f} $".replace(",", " ").replace(".", ",")


def render_html(  # noqa: PLR0912, PLR0915
    stats: dict,
    configured_chats: list[str],
    since_label: str = "",
    perf: dict | None = None,
    otel: dict | None = None,
    activity: dict | None = None,
    costs: dict | None = None,
) -> str:
    """The daily Notatki message: headline numbers, notes, channels, trend (Teams HTML, phone-first)."""
    now = _cet_now()
    head = (f"<p><strong>&#128202; Bob - dziś</strong> &nbsp;<span style='color:#888'>"
            f"{_WD[now.weekday()]} {now:%d.%m}, stan na {now:%H:%M}</span></p>")
    activity = activity or {}
    perf_ok = bool(perf and perf.get("available"))
    t = (perf or {}).get("total", {}) if perf_ok else {}
    per_chat_p = (perf or {}).get("per_chat", {}) if perf_ok else {}

    n_msg = sum(c["wiadomosci"] for c in activity.values())
    n_rep = sum(c["odpowiedz"] for c in activity.values())
    n_sil = sum(c["milczenie"] for c in activity.values())
    n_ans = sum(c["juz_odpowiedziane"] for c in activity.values())
    lines = [f"&#128172; <strong>{n_msg}</strong> wiadomości do Boba &middot; &#9989; <strong>{n_rep}</strong> "
             f"odpowiedzi &middot; &#129323; <strong>{n_sil}</strong> bez odpowiedzi"
             + (f" &middot; &#128101; {n_ans} odpowiedział ktoś inny" if n_ans else "")]
    if perf_ok:
        cost = Decimal(str(t.get("total_cost_usd", 0.0)))
        yday = (costs or {}).get("yday_so_far")
        cmp = ""
        if yday:
            diff = (cost - yday) / yday * 100
            arrow = "&#9650;" if diff > 0 else "&#9660;"
            cmp = f" <span style='color:#888'>({arrow} {abs(diff):.0f}% wobec wczoraj o tej porze: {_usd(yday)})</span>"
        p50, p95 = t.get("p50_duration_ms") or 0, t.get("p95_duration_ms") or 0
        lines.append(f"&#128176; <strong>{_usd(cost)}</strong> koszt Claude{cmp}")
        if p50:
            sec = lambda ms: f"{ms / 1000:.1f}".replace(".", ",")  # noqa: E731
            lines.append(f"&#9201; odpowiedź zwykle w <strong>{sec(p50)} s</strong>"
                         f" <span style='color:#888'>(najdłuższe {sec(p95)} s)</span>")
    else:
        lines.append("<span style='color:#888'>brak danych o koszcie i czasie (czekam na perf-metrics.jsonl)</span>")
    body = f"<p>{'<br>'.join(lines)}</p>"

    # what needs attention - at most three plain-language notes
    notes = []
    if perf_ok and t.get("total_cost_usd", 0) > 0:
        top_chat, top = max(per_chat_p.items(), key=lambda kv: kv[1].get("total_cost_usd", 0), default=("", {}))
        share = top.get("total_cost_usd", 0) / t["total_cost_usd"]
        a = activity.get(top_chat, {})
        if share >= 0.5 and top_chat:
            silent = f", {a['milczenie']} z {a['wiadomosci']} wiadomości bez odpowiedzi" if a.get("milczenie") else ""
            notes.append(f"<strong>{_e(top_chat)}</strong> to {share:.0%} dzisiejszego kosztu{silent}.")
        hit = t.get("cache_hit_rate", 0.0)
        if t.get("count", 0) >= 5 and hit < 0.6:
            notes.append(f"Tylko {hit:.0%} treści szło z pamięci podręcznej (cache) - reszta była wysyłana od nowa "
                         "i liczona drożej.")
        if p95 and p95 > 60_000:
            notes.append(f"Najdłuższe odpowiedzi trwały ponad {p95 / 60000:.0f} min - analizy albo przeciążenie.")
    if notes:
        body += "<p><strong>Warto wiedzieć</strong><br>" + "<br>".join(f"&#8226; {n}" for n in notes[:3]) + "</p>"

    # channels: one table, most expensive first, then most active
    names = set(activity) | {c for c in per_chat_p if c}
    rows = sorted(names, key=lambda c: (-(per_chat_p.get(c, {}).get("total_cost_usd", 0)),
                                        -(activity.get(c, {}).get("wiadomosci", 0))))
    if rows:
        td = "<td style='padding:3px 8px;border-bottom:1px solid #ddd;'>"
        tdr = "<td style='padding:3px 8px;border-bottom:1px solid #ddd;text-align:right;white-space:nowrap;'>"
        th = "<th style='padding:3px 8px;border-bottom:2px solid #999;text-align:{a};'>{t}</th>"
        trs = "".join(
            f"<tr>{td}{_e(c)}</td>{tdr}{activity.get(c, {}).get('wiadomosci', 0)}</td>"
            f"{tdr}{activity.get(c, {}).get('odpowiedz', 0)}</td>"
            f"{tdr}{_usd(per_chat_p.get(c, {}).get('total_cost_usd', 0.0)) if perf_ok else '-'}</td></tr>"
            for c in rows)
        body += ("<p><strong>Kanały</strong></p><table style='border-collapse:collapse;font-size:0.95em;'><tr>"
                 + th.format(a="left", t="Kanał") + th.format(a="right", t="Wiad.")
                 + th.format(a="right", t="Odp.") + th.format(a="right", t="Koszt") + f"</tr>{trs}</table>")

    # trend: one line, readable on a phone
    if costs and costs.get("days"):
        days = list(costs["days"].items())
        vals = [v for _, v in days]
        avg = sum(vals[:-1]) / max(1, len(vals) - 1)
        top = max(vals) if vals else 0
        cells = [(("dziś" if i == len(days) - 1 else _WD[d.weekday()].rstrip(".")), v) for i, (d, v) in enumerate(days)]
        line = " &middot; ".join(f"<strong>{lab} {v:.0f} $</strong>" if v == top and top else f"{lab} {v:.0f} $"
                                 for lab, v in cells)
        body += (f"<p><strong>Koszt - ostatnie 7 dni</strong><br>{line}<br>"
                 f"<span style='color:#888'>średnio {_usd(avg)} dziennie (bez dzisiaj)</span></p>")
    models = (perf or {}).get("per_model", {}) if perf_ok else {}
    if len(models) > 1:
        body += ("<p style='color:#888;font-size:0.9em'>Modele: " + ", ".join(
            f"{_e(m.replace('claude-', ''))} {b.get('count', 0)}× {_usd(b.get('total_cost_usd', 0.0))}"
            for m, b in sorted(models.items(), key=lambda kv: -kv[1].get("total_cost_usd", 0))) + "</p>")
    foot = (f"<p style='color:#888;font-size:0.85em'>Szczegóły na żywo (mapa ciepła, logi): panel Boba "
            f"{_PANEL_URL} na Macu &middot; odświeżane co 10 min</p>")
    return head + body + foot


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

    body = html.split("</p>", 1)[-1]                   # everything but the "stan na HH:MM" header
    if existing_id and existing_id != "unknown" and same_day and state.get("body") == body:
        print("[stats] bez zmian - nie edytuje", flush=True)
        return
    if existing_id and existing_id != "unknown" and same_day:
        ok = api_client.edit_message(existing_id, html, conv_id)
        if ok:
            print(f"[stats] wiadomosc edytowana (id={existing_id})", flush=True)
            state["updated_at"] = _cet_now().isoformat()
            state["body"] = body
            _save_state(state)
            return
        print("[stats] edit nie powiodl sie -- probuje nowa wiadomosc", flush=True)

    msg_id = api_client.send_html_message(html, conv_id)
    if msg_id:
        reason = "nowy dzien -- nowa" if (existing_id and not same_day) else "nowa"
        print(f"[stats] {reason} wiadomosc wyslana (id={msg_id})", flush=True)
        _save_state({"message_id": msg_id, "conv_id": conv_id,
                     "posted_at": _cet_now().isoformat(), "body": body})
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
        html = render_html(stats, configured, since_label=since_label, perf=perf,
                           activity=channel_activity(log_path, perf_since), costs=daily_costs(_PERF_LOG))
        post_or_edit(html)
    else:
        print(render(stats, configured))


if __name__ == "__main__":
    main()
