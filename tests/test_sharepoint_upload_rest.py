"""sharepoint_upload: REST first (cookies from the attachment jar), the browser flow only as a fallback."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import attachments  # noqa: E402
import sharepoint_upload as sp  # noqa: E402

URL = "https://contoso.sharepoint.com/:f:/r/sites/Team/Shared%20Documents/Bob?csf=1"
SITE = "https://contoso.sharepoint.com/sites/Team"
FOLDER = "/sites/Team/Shared Documents/Bob"


def test_site_and_folder_come_from_the_sharing_url():
    assert sp._site_and_path(URL) == (SITE, FOLDER)
    for bad in ("", "https://contoso.sharepoint.com/sites/Team", "not a url", "https://x/:f:/r/a/b"):
        assert sp._site_and_path(bad) == ("", ""), bad


def test_suffix_keeps_the_extension():
    assert sp._suffixed("notes.md", 2) == "notes-2.md"
    assert sp._suffixed("noext", 3) == "noext-3"


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body or {}, text

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _session(responses):
    s = MagicMock()
    s.post.side_effect = list(responses)
    return lambda: s, s


@pytest.fixture()
def cookies(monkeypatch):
    monkeypatch.setattr(attachments, "ensure_cookies", lambda url: {"FedAuth": "x"})
    invalidated = []
    monkeypatch.setattr(attachments, "invalidate_cookies", lambda: invalidated.append(1))
    return invalidated


def test_rest_upload_returns_the_stored_file_url(cookies):
    info = {"Name": "a.md", "Length": 3, "ServerRelativeUrl": "/sites/Team/Shared Documents/Bob/a.md"}
    factory, sess = _session([_Resp(200, {"FormDigestValue": "d"}), _Resp(200, info)])
    url = sp._upload_rest(b"abc", FOLDER, "a.md", session_factory=factory, site_url=SITE)
    assert url == "https://contoso.sharepoint.com/sites/Team/Shared%20Documents/Bob/a.md"
    assert "AddUsingPath" in sess.post.call_args_list[1].args[0]


def test_rest_upload_renames_on_a_name_collision(cookies):
    info = {"Name": "a-2.md", "Length": 3, "ServerRelativeUrl": "/sites/Team/Shared Documents/Bob/a-2.md"}
    exists = _Resp(400, text=f"... {sp._FILE_EXISTS_CODE} ...")
    factory, sess = _session([_Resp(200, {"FormDigestValue": "d"}), exists, _Resp(200, info)])
    assert sp._upload_rest(b"abc", FOLDER, "a.md", session_factory=factory, site_url=SITE).endswith("/a-2.md")
    assert "a-2.md" in sess.post.call_args_list[2].args[0]


def test_rest_upload_refreshes_cookies_once_on_401_and_gives_up_after(cookies):
    factory, sess = _session([_Resp(401), _Resp(401)])
    assert sp._upload_rest(b"abc", FOLDER, "a.md", session_factory=factory, site_url=SITE) is None
    assert len(cookies) == 1                                  # one refresh, then the caller falls back


def test_rest_upload_rejects_a_short_write(cookies):
    info = {"Name": "a.md", "Length": 1, "ServerRelativeUrl": "/x/a.md"}
    factory, _ = _session([_Resp(200, {"FormDigestValue": "d"}), _Resp(200, info)])
    assert sp._upload_rest(b"abc", FOLDER, "a.md", session_factory=factory, site_url=SITE) is None


def test_upload_file_tries_rest_then_the_browser(tmp_path, monkeypatch):
    f = tmp_path / "a.md"
    f.write_text("abc")
    monkeypatch.setattr(sp, "DEFAULT_FOLDER_URL", URL)
    monkeypatch.setattr(sp, "SITE_URL", SITE)
    monkeypatch.setattr(sp, "DEFAULT_FOLDER_PATH", FOLDER)
    calls = []
    monkeypatch.setattr(sp, "_upload_ui", lambda *a: calls.append("ui") or "https://ui/a.md")
    monkeypatch.setattr(sp, "_upload_rest", lambda *a, **k: calls.append("rest") or "https://rest/a.md")
    assert sp.upload_file(f, folder_url=URL) == "https://rest/a.md" and calls == ["rest"]
    calls.clear()
    monkeypatch.setattr(sp, "_upload_rest", lambda *a, **k: calls.append("rest") or None)
    assert sp.upload_file(f, folder_url=URL) == "https://ui/a.md" and calls == ["rest", "ui"]


def test_an_unconfigured_folder_skips_the_upload(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("abc")
    assert sp.upload_file(f, folder_url="") is None
