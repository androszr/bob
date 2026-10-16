"""jira-bob for evals: the real MCP server with real reads but FAKED writes.

Every POST/PUT/DELETE that scripts/jira/mcp_server.py would send to Jira is answered
locally (jira_create gets a fake key like PRJ1-99001) and appended as one JSON line to
$BOB_EVAL_WRITE_LOG. GETs pass through to Jira, except for the fake keys, so
jira_create's reporter verification sees the reporter it "set".

Tool names, schemas and descriptions are the production ones -- the model under test
cannot tell the difference, which is the point.
"""

import itertools
import json
import os
import sys
import threading
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SCRIPTS / "jira"))
sys.path.insert(0, str(_SCRIPTS))
os.environ["JIRA_MCP_TOOLSET"] = "write"

import mcp_server  # noqa: E402
import requests  # noqa: E402

_LOG = os.environ.get("BOB_EVAL_WRITE_LOG", "")
_counter = itertools.count(1)
_fake_issues: dict[str, dict] = {}
_lock = threading.Lock()


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()
        self.headers: dict = {}

    def json(self):
        return self._payload


def _body(kwargs: dict):
    raw = kwargs.get("json", kwargs.get("data"))
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw if raw is not None else None


def _record(method: str, url: str, body) -> None:
    if not _LOG:
        return
    with _lock, open(_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"method": method, "url": url, "body": body}, ensure_ascii=False) + "\n")


def _fake(method: str):
    def call(url, *args, **kwargs):
        body = _body(kwargs)
        if kwargs.get("files"):
            body = {"files": [str(v[0]) if isinstance(v, tuple) else "?" for v in kwargs["files"].values()]}
        _record(method, url, body)
        if method == "POST" and url.rstrip("/").endswith("/rest/api/2/issue"):
            fields = (body or {}).get("fields", {}) if isinstance(body, dict) else {}
            project = (fields.get("project") or {}).get("key", "FAKE")
            key = f"{project}-99{next(_counter):03d}"
            _fake_issues[key] = fields
            return _FakeResponse(201, {"id": "990000", "key": key, "self": f"{url}/{key}"})
        if "/attachments" in url:
            return _FakeResponse(200, [{"id": "1", "filename": "plik"}])
        if "/comment" in url:
            text = body.get("body", "") if isinstance(body, dict) else ""
            return _FakeResponse(201, {"id": "1", "body": text})
        return _FakeResponse(204, {})
    return call


def _get(url, *args, **kwargs):
    for key, fields in _fake_issues.items():
        if f"/issue/{key}" in url:
            return _FakeResponse(200, {"key": key, "fields": fields})
    return requests.get(url, *args, **kwargs)  # noqa: S113 -- caller passes timeout in kwargs


class _RequestsProxy:
    """Stands in for the `requests` module inside mcp_server only."""

    post = staticmethod(_fake("POST"))
    put = staticmethod(_fake("PUT"))
    delete = staticmethod(_fake("DELETE"))
    patch = staticmethod(_fake("PATCH"))
    get = staticmethod(_get)

    def __getattr__(self, name):
        return getattr(requests, name)


mcp_server.requests = _RequestsProxy()

if __name__ == "__main__":
    mcp_server.mcp.run(transport="stdio")
