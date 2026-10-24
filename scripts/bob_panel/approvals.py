"""Yes/no requests the owner answers in the panel.

Generic requests only: anyone local (Claude Code, Bob's automations, scripts) POSTs {title, details, source};
the asker polls GET /api/approvals/<id> for the decision. The owner decides in the panel or - away from the
computer - in the notes chat ("Bob, zatwierdzam apr-..."), where announce_pending() posts each new request once.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import org_config

from bob_panel import store, supervisor

STORE = supervisor.STATE_DIR / "approvals.json"
OWNER = org_config.OWNER_DISPLAY_NAME


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _update(fn) -> object:
    """fn(items) -> (items, out); runs under the store's lock."""
    box = {}

    def wrap(items):
        items, box["out"] = fn(items)
        return items
    store.update(wrap, STORE)
    return box.get("out")


def _load() -> list[dict]:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def create(title: str, details: str = "", source: str = "", options: list[str] | None = None,
           action: str | None = None) -> dict:
    title = (title or "").strip()[:300]
    if not title:
        raise ValueError("title")
    req = {"id": "apr-" + uuid.uuid4().hex[:10], "title": title, "details": (details or "")[:20000],
           "source": (source or "")[:80], "status": "pending", "created_at": _now()}
    if options:
        req["options"] = [str(o)[:80] for o in options][:6]
    if action:                      # set only by the panel's own code (ACTIONS), never from the HTTP body
        req["action"] = action
    _update(lambda items: (items + [req], None))
    return req


def get(req_id: str) -> dict | None:
    return next((r for r in _load() if r.get("id") == req_id), None)


def decide(req_id: str, approve: bool, note: str = "", by: str = OWNER) -> dict:
    def fn(items):
        for r in items:
            if r.get("id") == req_id and r.get("status") == "pending":
                r.update(status="approved" if approve else "rejected", decided_at=_now(), decided_by=by,
                         note=(note or "")[:2000])
                return items, r
        return items, None
    hit = _update(fn)
    if not hit:
        return {"ok": False, "request": None, "message": "Nic do zatwierdzenia."}
    msg = "Zapisane."
    run = ACTIONS.get(hit.get("action") or "")
    if run:
        msg = run(approve, hit)
    return {"ok": True, "request": hit, "message": msg}


# what an approved / rejected request does, by its "action" (set by the panel itself)
def _knowledge_action(approve: bool, req: dict | None = None) -> str:
    from bob_panel import rolecfg  # noqa: PLC0415
    return rolecfg.decide_knowledge((req or {}).get("id", ""), approve)


ACTIONS = {"knowledge_apply": _knowledge_action}


def announce_pending(send) -> int:
    """Post every new request to the notes chat once, with its id, so the owner can decide away from the
    computer ("Bob, zatwierdzam apr-..." - cli.py). send(text) -> bool; a failed send is retried on the next pass."""
    fresh = [r for r in _load() if r.get("status") == "pending" and not r.get("announced_at")]
    sent = []
    for r in fresh:
        details = (r.get("details") or "").strip()
        if len(details) > 1200:
            details = details[:1200].rstrip() + " …(całość w panelu)"
        text = (f"🟡 Zgoda do decyzji: {r['title']}\n"
                + (f"Od: {r['source']}\n" if r.get("source") else "")
                + (f"\n{details}\n" if details else "")
                + f"\nNapisz „Bob, zatwierdzam {r['id']}” albo „Bob, odrzucam {r['id']}” (po dwukropku możesz "
                  "dodać notatkę) - albo kliknij w panelu.")
        if send(text):
            sent.append(r["id"])
    if sent:
        def fn(items):
            for r in items:
                if r.get("id") in sent:
                    r["announced_at"] = _now()
            return items, None
        _update(fn)
    return len(sent)


def listing(limit_done: int = 20) -> dict:
    gen = _load()
    pending_ = [r for r in gen if r.get("status") == "pending"]
    pending_.sort(key=lambda r: r.get("created_at") or "")
    done = sorted((r for r in gen if r.get("status") != "pending"), key=lambda r: r.get("decided_at") or "",
                  reverse=True)[:limit_done]
    return {"pending": pending_, "done": done}
