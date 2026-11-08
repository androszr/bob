"""Managing Bob's chats from the panel: add, change (tool groups, who may call, flags), switch off, remove.

chat-config.json is Bob's own file (cli.py hot-reloads it on change), so the panel writes the same file:
  * every save is validated, shown as a diff first (preview) and backed up to user-data/bob-panel/chat-config-archive;
  * switching a chat off / on is bob-control.json "disabled_chats" - the same list as "Bob, disable chat X" in the
    control chat, effective at once;
  * a NEW chat starts being watched only after a restart (cli.py resolves the watched chats at start); a REMOVED chat
    is also put on the off list, because until that restart Bob still watches it - and with no config left it would
    get the defaults (every tool group);
  * the control chat (the one flagged "control_chat": true) cannot be removed or switched off and keeps its flag.
Chats are read through roles.normalize_chat (older Polish keys such as "role" / "kanal_fr" still load); the chat a
save writes uses the English keys only.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import threading
from datetime import datetime

from bob_panel import data, rolecfg, supervisor

roles = rolecfg.roles

CHAT_CONFIG = data.CHAT_CONFIG
CONTROL_FILE = data.TEAMS / "bob-control.json"
BACKUP_DIR = supervisor.STATE_DIR / "chat-config-archive"
CALLERS = ("all", "owner", "auto")
INTERNET = ("all", "only_me")
_CONV_RE = re.compile(r"^(?:19|48):[\w.@:-]{3,200}$")
_NAME_RE = re.compile(r"^[^\x00-\x1f]{1,120}$")
NEW_CHAT = {"caller": "all", "roles": [], "add_groups": ["datetime"]}   # starts with little - roles on purpose


def _read() -> dict:
    """chat-config.json; a missing file is an empty config (the first chat added creates it)."""
    try:
        return json.loads(CHAT_CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"chats": {}}


def personas() -> list[str]:
    """Persona keys of ai._PERSONA_PROMPTS (read from the source)."""
    return data._personas()


def control_chat(doc: dict | None = None) -> str:
    """The chat that issues Bob's control commands: the first one flagged "control_chat", else the notes chat by
    name (BOB_NOTES_CHAT, "Notes", or "Notatki" from older builds), else "Notes"."""
    chats = (doc if doc is not None else _read()).get("chats") or {}
    flagged = next((n for n, c in chats.items() if isinstance(c, dict) and c.get("control_chat")), None)
    if flagged:
        return flagged
    for name in (os.environ.get("BOB_NOTES_CHAT"), "Notes", "Notatki"):
        if name and name in chats:
            return name
    return "Notes"


def _dump(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"     # the format of cli._save_chat_config


_lock = threading.Lock()             # ThreadingHTTPServer: two panel requests may save at once


def _control(strict: bool = False) -> dict:
    """bob-control.json (Bob writes it too). strict: an existing file that does not parse is an error - writing
    over it would drop Bob's pause / off state."""
    try:
        out = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
        return out if isinstance(out, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if strict:
            raise ValueError(f"bob-control.json is unreadable ({exc}) - nothing saved, try again in a moment.") from exc
        return {}


def disabled() -> set[str]:
    return set(_control().get("disabled_chats") or [])


def _set_disabled(name: str, off: bool) -> None:
    ctrl = _control(strict=True)
    names = set(ctrl.get("disabled_chats") or [])
    (names.add if off else names.discard)(name)
    ctrl["disabled_chats"] = sorted(names)
    tmp = CONTROL_FILE.with_suffix(".panel.tmp")
    tmp.write_text(json.dumps(ctrl, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONTROL_FILE)


def listing() -> dict:
    doc = _read()
    off = disabled()
    chats = roles.normalize_chats(doc.get("chats") or {})
    return {"chats": [{"name": n, "config": c, "disabled": n in off} for n, c in chats.items()],
            "groups": data._mcp_groups(), "callers": list(CALLERS), "internet": list(INTERNET),
            "personas": personas(), "new_chat": NEW_CHAT, "control_chat": control_chat(doc)}


def validate(name: str, cfg: dict, old: dict | None) -> dict:
    """The chat's config as it will be saved (English keys); ValueError on anything wrong."""
    if not isinstance(name, str) or not _NAME_RE.match(name.strip()):
        raise ValueError("Chat name: 1-120 characters.")
    if not isinstance(cfg, dict):
        raise ValueError("A chat's config must be a JSON object.")
    cfg = roles.normalize_chat(cfg)
    old = roles.normalize_chat(old) if isinstance(old, dict) else old
    groups = set(data._mcp_groups())
    out = {k: v for k, v in cfg.items() if v is not None or k == "mcp"}
    if "roles" in out:
        return _validate_roles(out, old, groups)
    if "caller" in out and out["caller"] not in CALLERS:
        raise ValueError(f"caller: one of {', '.join(CALLERS)}.")
    if "internet" in out and out["internet"] not in INTERNET:
        raise ValueError("internet: all or only_me (no field = off).")
    if "persona" in out and out["persona"] not in personas():
        raise ValueError(f"persona: one of {', '.join(personas())} (no field = Bob).")
    for key in ("mcp", "mcp_extra"):
        v = out.get(key)
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(g, str) for g in v):
            raise ValueError(f"{key}: a list of tool groups.")
        unknown = [g for g in v if g not in groups]
        if unknown:
            raise ValueError(f"{key}: unknown groups {', '.join(unknown)}.")
        out[key] = list(dict.fromkeys(v))
    if "mcp" not in out:
        out["mcp"] = None if old is None else old.get("mcp")
    if not out.get("mcp_extra"):
        out.pop("mcp_extra", None)
    if "conv_id" in out and not (isinstance(out["conv_id"], str) and _CONV_RE.match(out["conv_id"])):
        raise ValueError("conv_id: a Teams chat id (19:...@thread.v2).")
    for key in ("ingest_only", "external_chat"):
        if key in out and not isinstance(out[key], bool):
            raise ValueError(f"{key}: true or false.")
    was_control = bool((old or {}).get("control_chat"))
    if bool(out.get("control_chat")) != was_control:
        raise ValueError("control_chat changes only in the file - the owner's commands come from that chat.")
    return out


def _proposal(op: str, name: str, cfg: dict | None, new_name: str | None) -> tuple[dict, dict, str]:
    """(current doc, proposed doc, message) for add / save / remove."""
    doc = _read()
    chats = doc.get("chats") or {}
    new = json.loads(json.dumps(doc))                    # other chats stay as they are in the file
    nchats = new.setdefault("chats", {})
    if op == "add":
        if name in chats:
            raise ValueError(f'The chat "{name}" already exists.')
        nchats[name] = validate(name, cfg if cfg is not None else dict(NEW_CHAT), None)
        msg = "Added. Bob starts watching it after a restart."
    elif op == "save":
        if name not in chats:
            raise ValueError(f'No chat "{name}".')
        target = (new_name or name).strip()
        if target != name:
            if name == control_chat(doc):
                raise ValueError("The control chat cannot be renamed.")
            if target in chats:
                raise ValueError(f'The chat "{target}" already exists.')
        valid = validate(target, cfg or {}, chats[name])
        old_keys = list(roles.normalize_chat(chats[name]))  # keep the file's key order: the diff shows real changes
        valid = {**{k: valid[k] for k in old_keys if k in valid},
                 **{k: v for k, v in valid.items() if k not in old_keys}}
        nchats.clear()
        nchats.update({(target if k == name else k): (valid if k == name else v) for k, v in chats.items()})
        msg = "Saved - Bob reloads the config in a few seconds." if target == name else \
            "Saved under the new name - Bob starts watching the chat under this name after a restart."
    elif op == "remove":
        if name not in chats:
            raise ValueError(f'No chat "{name}".')
        if name == control_chat(doc):
            raise ValueError("The control chat cannot be removed.")
        nchats.pop(name)
        msg = "Removed and switched off - after a restart Bob stops watching it."
    else:
        raise ValueError("op")
    return doc, new, msg


def preview(op: str, name: str, cfg: dict | None = None, new_name: str | None = None) -> dict:
    cur, new, msg = _proposal(op, name, cfg, new_name)
    diff = "".join(difflib.unified_diff(_dump(cur).splitlines(keepends=True), _dump(new).splitlines(keepends=True),
                                        "current", "after the change", n=2))
    out = {"ok": True, "diff": diff or "(no changes)", "message": msg}
    target = (new_name or name).strip()
    if op != "remove" and target in (new.get("chats") or {}):
        eff = rolecfg.effective(new["chats"][target])
        out["effective"] = eff
        out["effective_text"] = "every default group" if eff is None else (", ".join(eff) or "(no tools)")
    return out


def _validate_roles(out: dict, old: dict | None, groups: set[str]) -> dict:
    """A chat on roles (roles.json): roles, channel adjustments, FR flag, trigger words; no hand-written list."""
    known_roles = set(roles.load().get("roles") or {})
    r = out.get("roles")
    if not isinstance(r, list) or not all(isinstance(x, str) for x in r):
        raise ValueError("roles: a list of roles.")
    unknown = [x for x in r if x not in known_roles]
    if unknown:
        raise ValueError(f"roles: unknown {', '.join(unknown)}.")
    out["roles"] = list(dict.fromkeys(r))
    for key in ("remove_groups", "add_groups"):
        v = out.get(key)
        if v is None:
            continue
        if not isinstance(v, list) or [g for g in v if g not in groups]:
            raise ValueError(f"{key}: a list of known tool groups.")
        out[key] = list(dict.fromkeys(v))
        if not out[key]:
            out.pop(key)
    out.pop("mcp", None)
    out.pop("mcp_extra", None)
    if "external_chat" in out and not isinstance(out["external_chat"], bool):
        raise ValueError("external_chat: true or false.")
    if not out.get("external_chat"):
        out.pop("external_chat", None)
    t = out.get("trigger")
    if t is not None and not (isinstance(t, str) or (isinstance(t, list) and all(isinstance(x, str) for x in t))):
        raise ValueError("trigger: a word or a list of trigger words.")
    for key in ("ingest_only",):
        if key in out and not isinstance(out[key], bool):
            raise ValueError(f"{key}: true or false.")
    if "caller" in out and out["caller"] not in CALLERS:
        raise ValueError(f"caller: one of {', '.join(CALLERS)}.")
    if "internet" in out and out["internet"] not in INTERNET:
        raise ValueError("internet: all or only_me (no field = off).")
    if "persona" in out and out["persona"] not in personas():
        raise ValueError(f"persona: one of {', '.join(personas())} (no field = Bob).")
    if "conv_id" in out and not (isinstance(out["conv_id"], str) and _CONV_RE.match(out["conv_id"])):
        raise ValueError("conv_id: a Teams chat id (19:...@thread.v2).")
    if bool(out.get("control_chat")) != bool((old or {}).get("control_chat")):
        raise ValueError("control_chat changes only in the file - the owner's commands come from that chat.")
    return out


def apply(op: str, name: str, cfg: dict | None = None, new_name: str | None = None, by: str = "panel") -> dict:
    with _lock:
        return _apply(op, name, cfg, new_name, by)


def _apply(op: str, name: str, cfg: dict | None, new_name: str | None, by: str) -> dict:
    cur, new, msg = _proposal(op, name, cfg, new_name)
    if new == cur:
        return {"ok": True, "message": "No changes.", "restart_needed": False}
    supervisor.adopt_legacy(BACKUP_DIR.parent / "chat-config-archiwum", BACKUP_DIR, {"zmiany.jsonl": "changes.jsonl"})
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if CHAT_CONFIG.exists():
        shutil.copy2(CHAT_CONFIG, BACKUP_DIR / f"chat-config.{stamp}.json")
    tmp = CHAT_CONFIG.with_suffix(".panel.tmp")
    tmp.write_text(_dump(new), encoding="utf-8")
    os.replace(tmp, CHAT_CONFIG)
    target = (new_name or name).strip()
    was_off = name in disabled()
    if op == "remove" or (op == "save" and target != name):
        _set_disabled(name, True)          # still watched until the restart - with no config of its own
    if op == "add":
        _set_disabled(target, False)
    elif op == "save" and target != name:
        _set_disabled(target, was_off)     # a rename keeps a deliberately switched-off chat off
    _log(by, op, name, target)
    # the watch loop sorts chats into "answered" and "listen only" at start: a flip needs a restart
    listen_flip = bool(((cur.get("chats") or {}).get(name) or {}).get("ingest_only")) != \
        bool(((new.get("chats") or {}).get(target) or {}).get("ingest_only"))
    conv_flip = (((cur.get("chats") or {}).get(name) or {}).get("conv_id")
                 != ((new.get("chats") or {}).get(target) or {}).get("conv_id"))
    if listen_flip and op == "save":
        msg += " The \"listen only\" change applies after Bob restarts."
    if conv_flip and op == "save":
        msg += " The new chat id applies after Bob restarts."
    return {"ok": True, "message": msg,
            "restart_needed": (op in ("add", "remove") or target != name or listen_flip
                               or (conv_flip and op == "save")),
            "backup": f"chat-config.{stamp}.json"}


def set_enabled(name: str, on: bool, by: str = "panel") -> dict:
    with _lock:
        return _set_enabled(name, on, by)


def _set_enabled(name: str, on: bool, by: str) -> dict:
    if name not in (_read().get("chats") or {}):
        raise ValueError(f'No chat "{name}".')
    if name == control_chat() and not on:
        raise ValueError("The control chat cannot be switched off.")
    _set_disabled(name, not on)
    _log(by, "enable" if on else "disable", name, name)
    return {"ok": True, "message": f'"{name}" {"switched on" if on else "switched off - Bob ignores its messages"}.'}


def _log(by: str, op: str, name: str, target: str) -> None:
    supervisor.adopt_legacy(BACKUP_DIR.parent / "chat-config-archiwum", BACKUP_DIR, {"zmiany.jsonl": "changes.jsonl"})
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with open(BACKUP_DIR / "changes.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "by": by, "op": op,
                             "chat": name, **({"new_name": target} if target != name else {})},
                            ensure_ascii=False) + "\n")
