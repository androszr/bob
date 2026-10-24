"""Read-only views of what Bob is doing, from the files Bob already keeps (nothing new to maintain)."""

from __future__ import annotations

import ast
import json
import operator
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import org_config

from bob_panel import supervisor

REPO = supervisor.REPO
TEAMS = REPO / "scripts" / "teams_agent"
LOG_JSONL = TEAMS / "log.jsonl"
WATCH_STATE = TEAMS / "watch-state.json"
CHAT_CONFIG = REPO / "chat-config.json"
AI_PY = TEAMS / "ai.py"
BROADCAST = REPO / "scripts" / "broadcast"

def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _tail_lines(path: Path, n: int, max_bytes: int = 2_000_000) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


def _iso_from_epoch(v) -> str | None:
    try:
        return datetime.fromtimestamp(float(v), UTC).isoformat()
    except (TypeError, ValueError):
        return None


def status() -> dict:
    bob = supervisor.find_bob()
    ws = _json(WATCH_STATE, {})
    last = max((float(v.get("last_activity_ts") or 0) for v in ws.values() if isinstance(v, dict)), default=0)
    lines = _tail_lines(LOG_JSONL, 1)
    last_log = None
    if lines:
        try:
            last_log = json.loads(lines[-1]).get("ts")
        except ValueError:
            pass
    return {
        "bob": {"running": bool(bob), **(bob or {}),
                "managed": bool(bob) and bob["pid"] == supervisor.read_state().get("pid")},
        "supervisor": supervisor.read_state(),
        "last_activity": _iso_from_epoch(last) if last else None,
        "last_log": last_log,
        "owner": org_config.OWNER_DISPLAY_NAME,
        "teams_session": teams_session(),
        "login": supervisor.login_status(),
        "now": datetime.now(UTC).isoformat(),
    }


TEAMS_SESSION = TEAMS / "session.json"
SESSION_MAX_AGE_H = 7 * 24      # teams_agent/session.py SESSION_MAX_AGE_S: older -> SSO login on start


def teams_session() -> dict:
    """Age of session.json: Bob starts without a new SSO login only while it is younger than 7 days."""
    try:
        age_h = (datetime.now(UTC).timestamp() - TEAMS_SESSION.stat().st_mtime) / 3600
    except OSError:
        return {"exists": False, "login_needed": True}
    return {"exists": True, "age_h": round(age_h, 1), "max_age_h": SESSION_MAX_AGE_H,
            "login_needed": age_h >= SESSION_MAX_AGE_H, "warn": age_h >= SESSION_MAX_AGE_H - 24}


def tasks(limit: int = 60) -> list[dict]:
    """Bob's last handled messages and events (log.jsonl), newest first."""
    out = []
    for line in reversed(_tail_lines(LOG_JSONL, limit * 3)):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("action") in ("watch_skip_empty", "watch_stop"):
            continue                      # watch_stop logs the whole chat list as chat_name - not a conversation
        if not isinstance(e.get("chat_name"), (str, type(None))) or str(e.get("chat_name") or "").startswith("["):
            continue
        out.append({
            "ts": e.get("ts"), "chat": e.get("chat_name"), "sender": e.get("sender"),
            "event": e.get("event") or e.get("action") or ("odpowiedź" if e.get("reply") else None),
            "text": (e.get("incoming_text") or "")[:240],
            "reply": (e.get("reply") or "")[:240],
            "status": e.get("status"),
        })
        if len(out) >= limit:
            break
    return out


def schedule() -> dict:
    """Timed jobs: the broadcast schedule (enabled jobs only) and Bob's fixed loop intervals."""
    jobs = {j.get("id"): j for j in _json(BROADCAST / "jobs.json", []) if isinstance(j, dict)}
    sched = _json(BROADCAST / "broadcast-schedule.json", {})
    rows = []
    for jid, times in sched.items():
        j = jobs.get(jid, {})
        rows.append({"id": jid, "name": j.get("name") or jid, "enabled": bool(j.get("enabled", True)) if j else None,
                     "times": times, "description": (j.get("description") or "")[:200],
                     "disabled_reason": j.get("_disabled_reason")})
    rows.sort(key=lambda r: (not r["enabled"], min(str(v) for v in r["times"].values()) if r["times"] else ""))
    last = {}
    for line in _tail_lines(LOG_JSONL, 3000):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "broadcast_fired" and e.get("job_id"):
            last[e["job_id"]] = e.get("ts")
    for r in rows:
        r["last_fired"] = last.get(r["id"])
    return {"broadcasts": rows, "loops": loops()}


def loops() -> list[dict]:
    """Bob's periodic loops (cli.py *_INTERVAL_S constants) - what runs on its own and how often."""
    try:
        src = (TEAMS / "cli.py").read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for m in re.finditer(r"^_([A-Z0-9_]+)_INTERVAL_S\s*=\s*([^#\n]+?)\s*(?:#\s*(.*))?$", src, re.M):
        try:
            sec = int(_arith(m.group(2)))
        except (ValueError, SyntaxError, ZeroDivisionError):
            continue
        out.append({"name": m.group(1).lower(), "every_s": sec, "what": (m.group(3) or "").strip()})
    return out


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv}


def _arith(expr: str) -> float:
    """Value of a literal arithmetic expression (numbers with + - * //), nothing else - no eval()."""
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        raise ValueError("not a plain number expression")
    return walk(ast.parse(expr.strip(), mode="eval"))


def chats() -> list[dict]:
    cfg = _json(CHAT_CONFIG, {}).get("chats") or {}
    ws = _json(WATCH_STATE, {})
    from bob_panel import rolecfg  # noqa: PLC0415
    _known, default = rolecfg.groups()
    labels = rolecfg.agent_labels()
    out = []
    for name, c in cfg.items():
        c = c if isinstance(c, dict) else {}
        eff = rolecfg.effective(c)              # exactly cli._mcp_for: roles or the hand-written list
        st = ws.get(name) or {}
        out.append({
            "name": name, "caller": c.get("caller"), "persona": c.get("persona"),
            "label": labels.get(c.get("persona") or "", "Bob"), "roles": c.get("role"),
            "groups": default if eff is None else eff, "all_groups": eff is None or set(default) <= set(eff),
            "flags": [k for k in ("control_chat", "ingest_only") if c.get(k)]
                     + (["kanał zewnętrzny"] if c.get("kanal_fr") else [])
                     + ([f"internet: {c['internet']}"] if c.get("internet") else []),
            "last_activity": _iso_from_epoch(st.get("last_activity_ts")) if isinstance(st, dict) else None,
            "last_reply": _iso_from_epoch(st.get("last_bob_reply_time")) if isinstance(st, dict) else None,
        })
    out.sort(key=lambda r: r["last_activity"] or "", reverse=True)
    return out


def _mcp_groups() -> list[str]:
    """Keys of ai._MCP_GROUPS, read from the source (importing ai pulls in the whole bot)."""
    try:
        src = AI_PY.read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"^_MCP_GROUPS: dict\[str, str\] = \{(.*?)^\}", src, re.M | re.S)
    return re.findall(r'^\s*"([a-z0-9_]+)"\s*:', m.group(1), re.M) if m else []


def _personas() -> list[str]:
    """Keys of ai._PERSONA_PROMPTS (channel personas), read from the source like _mcp_groups."""
    try:
        src = AI_PY.read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"^_PERSONA_PROMPTS: dict\[str, str\] = \{(.*?)^\}", src, re.M | re.S)
    return re.findall(r'^\s*"([a-z0-9_]+)"\s*:', m.group(1), re.M) if m else []


# -- live dashboard (Dashboard) ------------------------------------------------------------------------
PERF_JSONL = TEAMS / "perf-metrics.jsonl"
_KIND = {"watch_send": "odpowiedz", "catchup_send": "odpowiedz", "watch_skip_silent_sentinel": "milczenie",
         "watch_skip_answered": "juz_odpowiedziane"}


def _local_hour(ts: str):
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(ZoneInfo("Europe/Warsaw")).replace(minute=0, second=0, microsecond=0)


def dashboard(hours: int = 24) -> dict:
    """What Bob did, where and when over the last `hours` (a live dashboard with drill-down).

    Per channel x local hour: Bob's decisions on incoming messages (log.jsonl: replied / stayed silent / skipped
    as already answered) - the heatmap; per hour: Claude calls, cost and reply time (perf-metrics.jsonl) - the
    load charts; plus headline numbers and the latest actions. Reads only the tail of both files."""
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    now = datetime.now(ZoneInfo("Europe/Warsaw")).replace(minute=0, second=0, microsecond=0)
    slots = [now - timedelta_hours(h) for h in range(hours - 1, -1, -1)]
    index = {s: i for i, s in enumerate(slots)}
    chats: dict[str, dict] = {}
    feed = []
    for line in _tail_lines(LOG_JSONL, 6000):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind = _KIND.get(e.get("action") or "")
        chat = e.get("chat_name")
        if not kind or not isinstance(chat, str):
            continue
        slot = _local_hour(e.get("ts"))
        if slot not in index:
            continue
        c = chats.setdefault(chat, {"name": chat, "cells": [[0, 0, 0] for _ in slots], "total": 0,
                                    "odpowiedzi": 0, "milczenie": 0, "juz_odpowiedziane": 0})
        k = ("odpowiedz", "milczenie", "juz_odpowiedziane").index(kind)
        c["cells"][index[slot]][k] += 1
        c["total"] += 1
        c[{"odpowiedz": "odpowiedzi"}.get(kind, kind)] += 1
        feed.append({"ts": e.get("ts"), "chat": chat, "kind": kind, "sender": e.get("sender"),
                     "text": (e.get("incoming_text") or "")[:160], "reply": (e.get("reply") or "")[:160]})
    calls = [0] * len(slots)
    cost = [Decimal(0)] * len(slots)
    dur: list[list[float]] = [[] for _ in slots]
    per_chat_cost: dict[str, Decimal] = {}
    for line in _tail_lines(PERF_JSONL, 6000):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        slot = _local_hour(e.get("ts"))
        if slot not in index:
            continue
        i = index[slot]
        calls[i] += 1
        try:
            c_usd = Decimal(str(e.get("total_cost_usd") or 0))      # money: never float math
        except InvalidOperation:
            c_usd = Decimal(0)
        cost[i] += c_usd
        if e.get("duration_ms"):
            dur[i].append(float(e["duration_ms"]) / 1000)
        chat = (e.get("ctx") or {}).get("chat") or "(bez czatu)"
        per_chat_cost[chat] = per_chat_cost.get(chat, Decimal(0)) + c_usd
    all_dur = sorted(d for ds in dur for d in ds)
    rows = sorted(chats.values(), key=lambda c: -c["total"])
    for r in rows:
        r["koszt"] = float(round(per_chat_cost.get(r["name"], Decimal(0)), 2))
    return {
        "hours": [s.isoformat() for s in slots],
        "chats": rows,
        "load": {"calls": calls, "cost": [float(round(x, 3)) for x in cost],
                 "dur_avg": [round(sum(d) / len(d), 1) if d else None for d in dur]},
        "kpi": {"obsluzone": sum(r["total"] for r in rows), "odpowiedzi": sum(r["odpowiedzi"] for r in rows),
                "milczenie": sum(r["milczenie"] for r in rows), "wywolania": sum(calls),
                "koszt": float(round(sum(cost, Decimal(0)), 2)), "kanaly": len(rows),
                "czas_mediana": round(all_dur[len(all_dur) // 2], 1) if all_dur else None},
        "feed": list(reversed(feed[-12:])),
    }


def timedelta_hours(h: int):
    from datetime import timedelta  # noqa: PLC0415
    return timedelta(hours=h)
