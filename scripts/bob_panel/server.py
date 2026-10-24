"""HTTP server of Bob's panel: 127.0.0.1:3006 (BOB_PANEL_PORT), the page + a small JSON API.

    GET  /api/status | /api/tasks | /api/schedule | /api/dashboard | /api/chats | /api/approvals | /api/approvals/<id>
    GET  /api/bob/log?lines=200 | /api/bob/logs?hours=6&level=info&source=&q=   raw tail / parsed (logs.py)
    POST /api/bob/restart | /api/bob/stop | /api/bob/start          {"by": "..."}
    GET  /api/bob/login | POST /api/bob/login {"then_start"?} | POST /api/bob/login/confirm   Teams SSO from the panel
    POST /api/approvals                {"title", "details", "source", "options"?}  -> the request (id)
    POST /api/approvals/<id>/decide    {"approve": true|false, "note"?}
    GET  /api/roles | /api/roles/knowledge?plik=   roles.json: roles, access, skills, knowledge, names per channel
    POST /api/roles/save {"id", "role", "new"?} | /api/roles/delete {"id"} | /api/roles/group {"group", "opis",
         "tylko_zespol"} | /api/roles/knowledge {"rola", "polecenie", "wpis"? | "nowy"?}   (rolecfg.py)
    GET  /api/chat-config | POST /api/chat-config/preview | /api/chat-config/apply | /api/chat-config/enable
         {"op": "add"|"save"|"remove", "name", "config"?, "new_name"?} / {"name", "on"}   (chatcfg.py)

Local only (loopback). Every POST needs Origin http://localhost:3006 (or 127.0.0.1:3006): a page on
another site the browser has open cannot drive Bob (CSRF). Claude Code sends the header with curl.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bob_panel import approvals, chatcfg, data, logs, rolecfg, supervisor  # noqa: E402

PORT = int(os.environ.get("BOB_PANEL_PORT", "3006"))
HOST = "127.0.0.1"
STATIC = Path(__file__).resolve().parent / "static"
_ALLOWED_ORIGINS = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}
_ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}   # DNS rebinding: a foreign name pointed at 127.0.0.1
_APR_RE = re.compile(r"^/api/approvals/(apr-[0-9a-f]{8,10})(/decide)?$")
MAX_BODY = 64 * 1024
_TYPES = {".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".js": "text/javascript; charset=utf-8",
          ".png": "image/png"}                       # no font types: the page uses the system font


class Handler(BaseHTTPRequestHandler):
    server_version = "bob-panel"

    def log_message(self, fmt, *args):  # quieter than the default (one line per request to stderr)
        if not self.path.startswith("/api/status"):
            sys.stderr.write(f"[bob-panel] {self.address_string()} {fmt % args}\n")

    def _send(self, code: int, body, ctype: str = "application/json; charset=utf-8") -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")                      # no other page may frame the panel
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > MAX_BODY:
            raise ValueError("too-large")
        raw = self.rfile.read(n) if n else b""
        try:
            out = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ValueError("bad-json") from exc
        return out if isinstance(out, dict) else {}

    def _host_ok(self) -> bool:
        if self.headers.get("Host") in _ALLOWED_HOSTS:
            return True
        self._send(403, {"error": "host"})
        return False

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return None
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        try:
            if p in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if p.startswith("/static/"):
                f = (STATIC / p.removeprefix("/static/")).resolve()
                if f.is_file() and f.is_relative_to(STATIC.resolve()) and f.suffix in _TYPES:
                    return self._send(200, f.read_bytes(), _TYPES[f.suffix])
                return self._send(404, {"error": "not-found"})
            if p == "/api/status":
                return self._send(200, data.status())
            if p == "/api/tasks":
                return self._send(200, data.tasks(max(1, min(int((q.get("limit") or ["60"])[0]), 500))))
            if p == "/api/schedule":
                return self._send(200, data.schedule())
            if p == "/api/dashboard":
                return self._send(200, data.dashboard(max(1, min(int((q.get("hours") or ["24"])[0]), 168))))
            if p == "/api/chats":
                return self._send(200, data.chats())
            if p == "/api/approvals":
                return self._send(200, approvals.listing())
            if p == "/api/chat-config":
                return self._send(200, chatcfg.listing())
            if p == "/api/roles":
                return self._send(200, rolecfg.listing())
            if p == "/api/roles/knowledge":
                return self._send(200, rolecfg.read_knowledge((q.get("plik") or [""])[0]))
            if p == "/api/bob/login":
                return self._send(200, supervisor.login_status())
            if p == "/api/bob/logs":
                one = lambda k, d: (q.get(k) or [d])[0]  # noqa: E731
                return self._send(200, logs.view(float(one("hours", "6")), one("level", "info"), one("source", ""),
                                                 one("q", "")))
            if p == "/api/bob/log":
                return self._send(200, {"log": supervisor.log_tail(int((q.get("lines") or ["200"])[0]))})
            m = _APR_RE.match(p)
            if m and not m.group(2):
                r = approvals.get(m.group(1))
                return self._send(200 if r else 404, r or {"error": "not-found"})
        except ValueError as exc:
            return self._send(404, {"error": "not-found", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": "internal", "message": str(exc)[:300]})
        return self._send(404, {"error": "not-found"})

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return None
        if self.headers.get("Origin") not in _ALLOWED_ORIGINS:
            return self._send(403, {"error": "origin", "message": f"Wymagany nagłówek Origin: http://localhost:{PORT}"})
        p = urlparse(self.path).path
        try:
            body = self._body()
            by = str(body.get("by") or "panel")[:60]
            if p == "/api/bob/restart":
                return self._send(200, _async_or_wait(supervisor.restart, by, body))
            if p == "/api/bob/stop":
                return self._send(200, supervisor.stop(by))
            if p == "/api/bob/start":
                return self._send(200, supervisor.start(by))
            if p == "/api/bob/login":
                return self._send(200, supervisor.login(by, then_start=bool(body.get("then_start"))))
            if p == "/api/bob/login/confirm":
                return self._send(200, supervisor.login_confirm())
            if p in ("/api/chat-config/preview", "/api/chat-config/apply"):
                fn = chatcfg.preview if p.endswith("preview") else chatcfg.apply
                args = (str(body.get("op") or ""), str(body.get("name") or ""), body.get("config"),
                        body.get("new_name"))
                return self._send(200, fn(*args) if fn is chatcfg.preview else fn(*args, by=by))
            if p == "/api/roles/save":
                return self._send(200, rolecfg.save_role(str(body.get("id") or ""), body.get("role"), by,
                                                         new=bool(body.get("new"))))
            if p == "/api/roles/delete":
                return self._send(200, rolecfg.delete_role(str(body.get("id") or ""), by))
            if p == "/api/roles/group":
                return self._send(200, rolecfg.save_group_info(str(body.get("group") or ""), body.get("opis", ""),
                                                               bool(body.get("tylko_zespol")), by))
            if p == "/api/roles/knowledge":
                return self._send(200, rolecfg.start_knowledge(
                    str(body.get("rola") or ""), body.get("polecenie", ""), body.get("wpis") or None,
                    body.get("nowy") if isinstance(body.get("nowy"), dict) else None, by))
            if p == "/api/chat-config/enable":
                return self._send(200, chatcfg.set_enabled(str(body.get("name") or ""), bool(body.get("on")), by))
            if p == "/api/approvals":
                return self._send(201, approvals.create(body.get("title", ""), body.get("details", ""),
                                                        body.get("source", ""), body.get("options")))
            m = _APR_RE.match(p)
            if m and m.group(2):
                return self._send(200, approvals.decide(m.group(1), bool(body.get("approve")),
                                                        str(body.get("note") or "")))
        except ValueError as exc:
            return self._send(400, {"ok": False, "error": str(exc), "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": "internal", "message": str(exc)[:300]})
        return self._send(404, {"error": "not-found"})


def _async_or_wait(fn, by: str, body: dict) -> dict:
    """A restart takes up to ~30 s (Bob's own Ctrl+C cleanup); the page asks with wait=false and polls status."""
    if body.get("wait", True):
        return fn(by)
    threading.Thread(target=fn, args=(by,), daemon=True).start()
    return {"ok": True, "message": "Restart w toku - status odświeży się sam."}


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[bob-panel] http://localhost:{PORT}", file=sys.stderr, flush=True)
    # after a reboot / login the panel starts first: bring Bob back if he was running and needs no SSO
    threading.Thread(target=lambda: print(f"[bob-panel] autostart: {supervisor.autostart()}", file=sys.stderr,
                                          flush=True), daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
