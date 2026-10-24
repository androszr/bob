"""Managing Bob's chats from the panel: add, change (tool groups, who may call, flags), switch off, remove.

chat-config.json is Bob's own file (cli.py hot-reloads it on change), so the panel writes the same file:
  * every save is validated, shown as a diff first (preview) and backed up to user-data/bob-panel/chat-config-archiwum;
  * switching a chat off / on is bob-control.json "disabled_chats" - the same list as "Bob wyłącz chat X" in the
    control chat, effective at once;
  * a NEW chat starts being watched only after a restart (cli.py resolves the watched chats at start); a REMOVED chat
    is also put on the off list, because until that restart Bob still watches it - and with no config left it would
    get the defaults (every tool group);
  * the control chat (the one flagged "control_chat": true) cannot be removed or switched off and keeps its flag.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import threading
from datetime import datetime

from bob_panel import data, supervisor

CHAT_CONFIG = data.CHAT_CONFIG
CONTROL_FILE = data.TEAMS / "bob-control.json"
BACKUP_DIR = supervisor.STATE_DIR / "chat-config-archiwum"
CALLERS = ("all", "owner", "auto")
INTERNET = ("all", "only_me")
_CONV_RE = re.compile(r"^(?:19|48):[\w.@:-]{3,200}$")
_NAME_RE = re.compile(r"^[^\x00-\x1f]{1,120}$")
NEW_CHAT = {"caller": "all", "role": [], "dodatkowo": ["datetime"]}   # starts with little - roles on purpose


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
    """The chat that issues Bob's control commands: the first one flagged "control_chat", else "Notatki"."""
    chats = (doc if doc is not None else _read()).get("chats") or {}
    return next((n for n, c in chats.items() if isinstance(c, dict) and c.get("control_chat")), "Notatki")


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
            raise ValueError(f"bob-control.json nieczytelny ({exc}) - nic nie zapisano, spróbuj za chwilę.") from exc
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
    return {"chats": [{"name": n, "config": c, "disabled": n in off} for n, c in (doc.get("chats") or {}).items()],
            "groups": data._mcp_groups(), "callers": list(CALLERS), "internet": list(INTERNET),
            "personas": personas(), "new_chat": NEW_CHAT, "control_chat": control_chat(doc)}


def validate(name: str, cfg: dict, old: dict | None) -> dict:
    """The chat's config as it will be saved; ValueError (Polish message) on anything wrong."""
    if not isinstance(name, str) or not _NAME_RE.match(name.strip()):
        raise ValueError("Nazwa czatu: 1-120 znaków.")
    if not isinstance(cfg, dict):
        raise ValueError("Konfiguracja czatu musi być obiektem JSON.")
    groups = set(data._mcp_groups())
    out = {k: v for k, v in cfg.items() if v is not None or k == "mcp"}
    if "role" in out:
        return _validate_roles(out, old, groups)
    if "caller" in out and out["caller"] not in CALLERS:
        raise ValueError(f"caller: jedno z {', '.join(CALLERS)}.")
    if "internet" in out and out["internet"] not in INTERNET:
        raise ValueError("internet: all albo only_me (brak pola = wyłączony).")
    if "persona" in out and out["persona"] not in personas():
        raise ValueError(f"persona: jedna z {', '.join(personas())} (brak pola = Bob).")
    for key in ("mcp", "mcp_extra"):
        v = out.get(key)
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(g, str) for g in v):
            raise ValueError(f"{key}: lista grup narzędzi.")
        unknown = [g for g in v if g not in groups]
        if unknown:
            raise ValueError(f"{key}: nieznane grupy {', '.join(unknown)}.")
        out[key] = list(dict.fromkeys(v))
    if "mcp" not in out:
        out["mcp"] = None if old is None else old.get("mcp")
    if not out.get("mcp_extra"):
        out.pop("mcp_extra", None)
    if "conv_id" in out and not (isinstance(out["conv_id"], str) and _CONV_RE.match(out["conv_id"])):
        raise ValueError("conv_id: identyfikator czatu Teams (19:...@thread.v2).")
    for key in ("ingest_only", "kanal_fr"):
        if key in out and not isinstance(out[key], bool):
            raise ValueError(f"{key}: true albo false.")
    was_control = bool((old or {}).get("control_chat"))
    if bool(out.get("control_chat")) != was_control:
        raise ValueError("control_chat zmienia się tylko w pliku - z tego czatu idą komendy właściciela.")
    return out


def _proposal(op: str, name: str, cfg: dict | None, new_name: str | None) -> tuple[dict, dict, str]:
    """(current doc, proposed doc, message) for add / save / remove."""
    doc = _read()
    chats = doc.get("chats") or {}
    new = json.loads(json.dumps(doc))
    nchats = new.setdefault("chats", {})
    if op == "add":
        if name in chats:
            raise ValueError(f"Czat „{name}” już jest.")
        nchats[name] = validate(name, cfg if cfg is not None else dict(NEW_CHAT), None)
        msg = "Dodany. Bob zacznie go obserwować po restarcie."
    elif op == "save":
        if name not in chats:
            raise ValueError(f"Nie ma czatu „{name}”.")
        target = (new_name or name).strip()
        if target != name:
            if name == control_chat(doc):
                raise ValueError("Czat sterujący nie zmienia nazwy.")
            if target in chats:
                raise ValueError(f"Czat „{target}” już jest.")
        valid = validate(target, cfg or {}, chats[name])
        old_keys = list(chats[name])                       # keep the file's key order: the diff shows real changes
        valid = {**{k: valid[k] for k in old_keys if k in valid},
                 **{k: v for k, v in valid.items() if k not in old_keys}}
        nchats.clear()
        nchats.update({(target if k == name else k): (valid if k == name else v) for k, v in chats.items()})
        msg = "Zapisane - Bob przeładuje konfigurację w kilka sekund." if target == name else \
            "Zapisane pod nową nazwą - Bob zacznie obserwować czat pod tą nazwą po restarcie."
    elif op == "remove":
        if name not in chats:
            raise ValueError(f"Nie ma czatu „{name}”.")
        if name == control_chat(doc):
            raise ValueError("Czatu sterującego nie da się usunąć.")
        nchats.pop(name)
        msg = "Usunięty i wyłączony - po restarcie Bob przestanie go obserwować."
    else:
        raise ValueError("op")
    return doc, new, msg


def preview(op: str, name: str, cfg: dict | None = None, new_name: str | None = None) -> dict:
    cur, new, msg = _proposal(op, name, cfg, new_name)
    diff = "".join(difflib.unified_diff(_dump(cur).splitlines(keepends=True), _dump(new).splitlines(keepends=True),
                                        "obecny", "po zmianie", n=2))
    out = {"ok": True, "diff": diff or "(bez zmian)", "message": msg}
    target = (new_name or name).strip()
    if op != "remove" and target in (new.get("chats") or {}):
        from bob_panel import rolecfg  # noqa: PLC0415
        eff = rolecfg.effective(new["chats"][target])
        out["effective"] = eff
        out["effective_text"] = "wszystkie domyślne" if eff is None else (", ".join(eff) or "(żadne narzędzie)")
    return out


def _validate_roles(out: dict, old: dict | None, groups: set[str]) -> dict:
    """A chat on roles (roles.json): roles, channel adjustments, FR flag, trigger words; no hand-written list."""
    import roles  # noqa: PLC0415 -- scripts/teams_agent is on the path via rolecfg
    known_roles = set(roles.load().get("role") or {})
    r = out.get("role")
    if not isinstance(r, list) or not all(isinstance(x, str) for x in r):
        raise ValueError("role: lista ról.")
    unknown = [x for x in r if x not in known_roles]
    if unknown:
        raise ValueError(f"role: nieznane {', '.join(unknown)}.")
    out["role"] = list(dict.fromkeys(r))
    for key in ("bez", "dodatkowo"):
        v = out.get(key)
        if v is None:
            continue
        if not isinstance(v, list) or [g for g in v if g not in groups]:
            raise ValueError(f"{key}: lista znanych grup narzędzi.")
        out[key] = list(dict.fromkeys(v))
        if not out[key]:
            out.pop(key)
    out.pop("mcp", None)
    out.pop("mcp_extra", None)
    if "kanal_fr" in out and not isinstance(out["kanal_fr"], bool):
        raise ValueError("kanal_fr: true albo false.")
    if not out.get("kanal_fr"):
        out.pop("kanal_fr", None)
    t = out.get("trigger")
    if t is not None and not (isinstance(t, str) or (isinstance(t, list) and all(isinstance(x, str) for x in t))):
        raise ValueError("trigger: słowo albo lista słów wywołania.")
    for key in ("ingest_only",):
        if key in out and not isinstance(out[key], bool):
            raise ValueError(f"{key}: true albo false.")
    if "caller" in out and out["caller"] not in CALLERS:
        raise ValueError(f"caller: jedno z {', '.join(CALLERS)}.")
    if "internet" in out and out["internet"] not in INTERNET:
        raise ValueError("internet: all albo only_me (brak pola = wyłączony).")
    if "persona" in out and out["persona"] not in personas():
        raise ValueError(f"persona: jedna z {', '.join(personas())} (brak pola = Bob).")
    if "conv_id" in out and not (isinstance(out["conv_id"], str) and _CONV_RE.match(out["conv_id"])):
        raise ValueError("conv_id: identyfikator czatu Teams (19:...@thread.v2).")
    if bool(out.get("control_chat")) != bool((old or {}).get("control_chat")):
        raise ValueError("control_chat zmienia się tylko w pliku - z tego czatu idą komendy właściciela.")
    return out


def apply(op: str, name: str, cfg: dict | None = None, new_name: str | None = None, by: str = "panel") -> dict:
    with _lock:
        return _apply(op, name, cfg, new_name, by)


def _apply(op: str, name: str, cfg: dict | None, new_name: str | None, by: str) -> dict:
    cur, new, msg = _proposal(op, name, cfg, new_name)
    if new == cur:
        return {"ok": True, "message": "Bez zmian.", "restart_needed": False}
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
    if op == "add" or (op == "save" and target != name and not was_off):
        _set_disabled(target, False)       # a rename keeps a deliberately switched-off chat off
    _log(by, op, name, target)
    # the watch loop sorts chats into "answered" and "listen only" at start: a flip needs a restart
    listen_flip = bool(((cur.get("chats") or {}).get(name) or {}).get("ingest_only")) != \
        bool(((new.get("chats") or {}).get(target) or {}).get("ingest_only"))
    conv_flip = (((cur.get("chats") or {}).get(name) or {}).get("conv_id")
                 != ((new.get("chats") or {}).get(target) or {}).get("conv_id"))
    if listen_flip and op == "save":
        msg += " Zmiana „tylko do odbioru” zadziała po restarcie Boba."
    if conv_flip and op == "save":
        msg += " Nowy identyfikator czatu zadziała po restarcie Boba."
    return {"ok": True, "message": msg,
            "restart_needed": (op in ("add", "remove") or target != name or listen_flip
                               or (conv_flip and op == "save")),
            "backup": f"chat-config.{stamp}.json"}


def set_enabled(name: str, on: bool, by: str = "panel") -> dict:
    with _lock:
        return _set_enabled(name, on, by)


def _set_enabled(name: str, on: bool, by: str) -> dict:
    if name not in (_read().get("chats") or {}):
        raise ValueError(f"Nie ma czatu „{name}”.")
    if name == control_chat() and not on:
        raise ValueError("Czatu sterującego nie da się wyłączyć.")
    _set_disabled(name, not on)
    _log(by, "enable" if on else "disable", name, name)
    return {"ok": True, "message": f"„{name}” {'włączony' if on else 'wyłączony - Bob ignoruje jego wiadomości'}."}


def _log(by: str, op: str, name: str, target: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with open(BACKUP_DIR / "zmiany.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "by": by, "op": op,
                             "chat": name, **({"new_name": target} if target != name else {})},
                            ensure_ascii=False) + "\n")
