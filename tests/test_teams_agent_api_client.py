"""Unit tests for scripts/teams_agent/api_client.py — _extract_attachments."""
import json
import sys
import time
from pathlib import Path

import pytest
import requests as _requests

_TEAMS_DIR = str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent")
if _TEAMS_DIR not in sys.path:
    sys.path.insert(0, _TEAMS_DIR)

import api_client  # noqa: E402

_ea = api_client._extract_attachments


# ---------------------------------------------------------------------------
# properties.files (standard upload)
# ---------------------------------------------------------------------------

def test_files_md_accepted():
    props = {
        "files": json.dumps([{
            "fileName": "notes.md",
            "fileType": "md",
            "fileInfo": {"fileUrl": "https://sp.example.com/notes.md"},
        }])
    }
    result = _ea(props)
    assert len(result) == 1
    assert result[0] == {"name": "notes.md", "url": "https://sp.example.com/notes.md", "type": "md"}


def test_files_mp4_accepted():
    props = {
        "files": json.dumps([{
            "fileName": "meeting.mp4",
            "fileType": "mp4",
            "fileInfo": {"fileUrl": "https://sp.example.com/meeting.mp4"},
        }])
    }
    result = _ea(props)
    assert len(result) == 1
    assert result[0]["type"] == "mp4"


def test_files_unsupported_ext_filtered():
    props = {
        "files": json.dumps([{
            "fileName": "report.docx",
            "fileType": "docx",
            "fileInfo": {"fileUrl": "https://sp.example.com/report.docx"},
        }])
    }
    assert _ea(props) == []


def test_files_missing_url_filtered():
    props = {
        "files": json.dumps([{
            "fileName": "notes.md",
            "fileType": "md",
            "fileInfo": {},
        }])
    }
    assert _ea(props) == []


def test_files_fallback_ext_when_filetype_blank():
    props = {
        "files": json.dumps([{
            "fileName": "clip.wav",
            "fileType": "",
            "fileInfo": {"fileUrl": "https://sp.example.com/clip.wav"},
        }])
    }
    result = _ea(props)
    assert len(result) == 1
    assert result[0]["type"] == "wav"


# ---------------------------------------------------------------------------
# properties.links (SharePoint shared links / Teams Recording)
# ---------------------------------------------------------------------------

def test_links_mp4_accepted():
    props = {
        "links": json.dumps([{
            "@type": "http://schema.skype.com/HyperLink",
            "title": "Kickoff recording.mp4",
            "fileShareUrlMeta": {
                "objectUrl": "https://contoso.sharepoint.com/sites/x/recording.mp4",
                "shareUrl": "https://contoso.sharepoint.com/:v:/share/abc",
            },
        }])
    }
    result = _ea(props)
    assert len(result) == 1
    assert result[0]["name"] == "Kickoff recording.mp4"
    assert result[0]["type"] == "mp4"
    assert "sharepoint" in result[0]["url"]


def test_links_uses_shareurl_when_objecturl_missing():
    props = {
        "links": json.dumps([{
            "title": "clip.mp3",
            "fileShareUrlMeta": {
                "objectUrl": "",
                "shareUrl": "https://contoso.sharepoint.com/:u:/share/xyz",
            },
        }])
    }
    result = _ea(props)
    assert len(result) == 1
    assert result[0]["url"] == "https://contoso.sharepoint.com/:u:/share/xyz"


def test_links_unsupported_ext_filtered():
    props = {
        "links": json.dumps([{
            "title": "deck.pptx",
            "fileShareUrlMeta": {"objectUrl": "https://sp.example.com/deck.pptx"},
        }])
    }
    assert _ea(props) == []


def test_links_no_url_filtered():
    props = {
        "links": json.dumps([{
            "title": "video.mp4",
            "fileShareUrlMeta": {"objectUrl": "", "shareUrl": ""},
        }])
    }
    assert _ea(props) == []


def test_links_no_title_filtered():
    props = {
        "links": json.dumps([{
            "title": "",
            "fileShareUrlMeta": {"objectUrl": "https://sp.example.com/video.mp4"},
        }])
    }
    assert _ea(props) == []


def test_links_web_link_no_ext_filtered():
    props = {
        "links": json.dumps([{
            "title": "Confluence page",
            "fileShareUrlMeta": {"objectUrl": "https://confluence.example.com/page/123"},
        }])
    }
    assert _ea(props) == []


# ---------------------------------------------------------------------------
# Dedup: files + links combined
# ---------------------------------------------------------------------------

def test_dedup_same_name_in_files_and_links():
    props = {
        "files": json.dumps([{
            "fileName": "recording.mp4",
            "fileType": "mp4",
            "fileInfo": {"fileUrl": "https://sp.example.com/files/recording.mp4"},
        }]),
        "links": json.dumps([{
            "title": "recording.mp4",
            "fileShareUrlMeta": {"objectUrl": "https://sp.example.com/links/recording.mp4"},
        }]),
    }
    result = _ea(props)
    assert len(result) == 1
    assert result[0]["url"] == "https://sp.example.com/files/recording.mp4"


def test_files_and_links_different_names_both_included():
    props = {
        "files": json.dumps([{
            "fileName": "notes.md",
            "fileType": "md",
            "fileInfo": {"fileUrl": "https://sp.example.com/notes.md"},
        }]),
        "links": json.dumps([{
            "title": "meeting.mp4",
            "fileShareUrlMeta": {"objectUrl": "https://sp.example.com/meeting.mp4"},
        }]),
    }
    result = _ea(props)
    assert len(result) == 2
    names = {r["name"] for r in result}
    assert names == {"notes.md", "meeting.mp4"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_none_properties_returns_empty():
    assert _ea(None) == []


def test_empty_dict_returns_empty():
    assert _ea({}) == []


def test_malformed_json_in_files_returns_empty():
    assert _ea({"files": "not-valid-json"}) == []


def test_malformed_json_in_links_returns_empty():
    assert _ea({"links": "{broken"}) == []


def test_already_parsed_list_in_files():
    props = {
        "files": [{
            "fileName": "clip.wav",
            "fileType": "wav",
            "fileInfo": {"fileUrl": "https://sp.example.com/clip.wav"},
        }]
    }
    result = _ea(props)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _fuzzy_find_conversations
# ---------------------------------------------------------------------------


def _make_session_mock(conversations):
    """Return a mock _session() whose .get().json() yields the given conversations list."""
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.json.return_value = {"conversations": conversations}
    resp.raise_for_status.return_value = None
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_fuzzy_finds_group_chat_by_topic(monkeypatch):
    convs = [
        {"id": "19:chat04@thread.v2", "threadProperties": {"topic": "Grafikowanie Internal"},
         "members": []},
    ]
    monkeypatch.setattr(api_client, "_session", lambda: _make_session_mock(convs))
    result = api_client._fuzzy_find_conversations("Grafikowanie")
    assert result == [("19:chat04@thread.v2", "Grafikowanie Internal")]


def test_fuzzy_finds_1on1_by_member_name(monkeypatch):
    convs = [
        {"id": "19:chat07@thread.v2", "threadProperties": {},
         "members": [{"displayName": "Jan Kowalski"}]},
    ]
    monkeypatch.setattr(api_client, "_session", lambda: _make_session_mock(convs))
    result = api_client._fuzzy_find_conversations("kowalski")
    assert len(result) == 1
    assert result[0][0] == "19:chat07@thread.v2"
    assert result[0][1] == "Jan Kowalski"


def test_fuzzy_returns_all_matches(monkeypatch):
    convs = [
        {"id": "19:chat04@thread.v2", "threadProperties": {"topic": "Alpha Team"}, "members": []},
        {"id": "19:chat07@thread.v2", "threadProperties": {"topic": "Alpha Backup"}, "members": []},
        {"id": "19:chat10@thread.v2", "threadProperties": {"topic": "Beta Team"}, "members": []},
    ]
    monkeypatch.setattr(api_client, "_session", lambda: _make_session_mock(convs))
    result = api_client._fuzzy_find_conversations("alpha")
    assert len(result) == 2
    ids = [r[0] for r in result]
    assert "19:chat04@thread.v2" in ids
    assert "19:chat07@thread.v2" in ids


def test_fuzzy_deduplicates_by_conv_id(monkeypatch):
    # Same conv_id appears twice (shouldn't happen in practice but guard it)
    convs = [
        {"id": "19:chat04@thread.v2", "threadProperties": {"topic": "Alpha"},
         "members": [{"displayName": "Alpha Person"}]},
    ]
    monkeypatch.setattr(api_client, "_session", lambda: _make_session_mock(convs))
    result = api_client._fuzzy_find_conversations("alpha")
    # Topic matches first; member also matches but same conv_id should not be duplicated
    assert len(result) == 1
    assert result[0][0] == "19:chat04@thread.v2"


def test_fuzzy_returns_empty_on_no_match(monkeypatch):
    convs = [
        {"id": "19:chat04@thread.v2", "threadProperties": {"topic": "Beta Team"}, "members": []},
    ]
    monkeypatch.setattr(api_client, "_session", lambda: _make_session_mock(convs))
    result = api_client._fuzzy_find_conversations("alpha")
    assert result == []


def test_fuzzy_returns_empty_on_request_failure(monkeypatch):
    from unittest.mock import MagicMock
    session = MagicMock()
    session.get.side_effect = Exception("network error")
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client._fuzzy_find_conversations("anything")
    assert result == []


def test_fuzzy_returns_empty_for_blank_query(monkeypatch):
    # Should return [] without making any network call
    from unittest.mock import MagicMock
    session = MagicMock()
    monkeypatch.setattr(api_client, "_session", lambda: session)
    assert api_client._fuzzy_find_conversations("") == []
    assert api_client._fuzzy_find_conversations("   ") == []
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# _md_to_html — code fence handling
# ---------------------------------------------------------------------------

def test_md_to_html_code_fence_basic():
    result = api_client._md_to_html("przed\n```\nhello world\n```\npo")
    assert "<pre>hello world\n</pre>" in result
    assert "```" not in result


def test_md_to_html_code_fence_with_lang_tag():
    result = api_client._md_to_html("```python\nprint('hi')\n```")
    assert "<pre>" in result
    assert "python" not in result  # lang tag stripped
    assert "print" in result


def test_md_to_html_code_fence_escapes_html():
    result = api_client._md_to_html("```\n<b>bold</b> & stuff\n```")
    assert "&lt;b&gt;" in result
    assert "&amp;" in result
    assert "<b>" not in result


def test_md_to_html_code_fence_ascii_art():
    art = "```\n __\n|  |\n```"
    result = api_client._md_to_html(art)
    assert "<pre>" in result
    assert "__" in result
    # Angle brackets in the pre wrapper itself are real tags, not escaped
    assert "<pre>" in result


def test_md_to_html_no_fence_unchanged():
    result = api_client._md_to_html("zwykły tekst bez fence")
    assert "pre" not in result
    assert result == "zwykły tekst bez fence"


# ---------------------------------------------------------------------------
# reply_to_message — persona-aware prefix
# ---------------------------------------------------------------------------

def test_reply_to_message_default_uses_bob_prefix(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        api_client, "_post",
        lambda conv_id, content, mentions=None: captured.setdefault("content", content) or True,
    )
    api_client.reply_to_message("odpowiedz", "msg1", "Ktos", "pytanie", "conv1")
    assert "[Bob]" in captured["content"]
    assert "[Asystent]" not in captured["content"]


def test_reply_to_message_example_persona_uses_its_prefix(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        api_client, "_post",
        lambda conv_id, content, mentions=None: captured.setdefault("content", content) or True,
    )
    api_client.reply_to_message(
        "odpowiedz", "msg1", "Ktos", "pytanie", "conv1", persona="example",
    )
    assert "[Asystent]" in captured["content"]
    assert "[Bob]" not in captured["content"]


# ---------------------------------------------------------------------------
# _post_returning_id / edit_message — retry + _last_send_error
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_send_state():
    """Reset module-level send/refresh state so tests don't leak into each other.

    _last_send_error lives in thread-local storage (api_client._send_error_local);
    tests all run on pytest's single main thread, so resetting via the setter
    behaves exactly like resetting a plain global would.
    """
    api_client._needs_token_refresh = False
    api_client._set_last_send_error(None)
    yield
    api_client._needs_token_refresh = False
    api_client._set_last_send_error(None)


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self.headers = {}
        self._json_body = json_body if json_body is not None else {"id": "msg-123"}

    def raise_for_status(self):
        if self.status_code >= 400:
            exc = _requests.exceptions.HTTPError(f"{self.status_code} error")
            exc.response = self
            raise exc

    def json(self):
        return self._json_body


def _make_send_session(side_effects, method="post"):
    """MagicMock session whose .post()/.put() yields side_effects in order.

    Each entry is either an Exception instance (raised) or a _FakeResponse.
    """
    from unittest.mock import MagicMock
    session = MagicMock()
    call_log: list = []

    def _call(*_a, **_kw):
        call_log.append(1)
        idx = len(call_log) - 1
        item = side_effects[min(idx, len(side_effects) - 1)]
        if isinstance(item, Exception):
            raise item
        item.raise_for_status()
        return item

    setattr(session, method, _call)
    return session, call_log


def test_post_returning_id_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    err1 = _requests.exceptions.ConnectionError("boom")
    session, call_log = _make_send_session([err1, _FakeResponse(200, {"id": "msg-1"})])
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client._post_returning_id("conv1", "<p>hi</p>")
    assert result == "msg-1"
    assert len(call_log) == 2
    assert api_client._get_last_send_error() is None
    assert sleeps == [3]


def test_post_returning_id_401_retries_once_then_flags_token(monkeypatch):
    """A persistent 401 still flags the token -- but only after a same-token retry.

    A single 401 is not proof of expiry (see the
    notes on _retry_401), so the token is blamed on the second failure,
    not the first.
    """
    monkeypatch.setattr(api_client, "_401_RETRY_DELAY_S", 0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    resp = _FakeResponse(401, {})
    session, call_log = _make_send_session([resp, resp])
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client._post_returning_id("conv1", "<p>hi</p>")
    assert result is None
    assert len(call_log) == api_client._401_RETRIES + 1
    assert api_client._needs_token_refresh is True
    assert api_client._get_last_send_error()["status"] == 401


def test_post_returning_id_transient_401_succeeds_without_flagging(monkeypatch):
    """The transient case: 401 then 200 on the same token -- send must succeed."""
    monkeypatch.setattr(api_client, "_401_RETRY_DELAY_S", 0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    api_client._needs_token_refresh = False
    session, call_log = _make_send_session([
        _FakeResponse(401, {}), _FakeResponse(200, {"id": "42"}),
    ])
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client._post_returning_id("conv1", "<p>hi</p>")
    assert result == "42"
    assert len(call_log) == 2
    assert api_client._needs_token_refresh is False
    assert api_client._get_last_send_error() is None


def test_post_returning_id_exhausts_retries_sets_last_send_error(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    resp = _FakeResponse(503, {})
    session, call_log = _make_send_session([resp, resp, resp])
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client._post_returning_id("conv1", "<p>hi</p>")
    assert result is None
    assert len(call_log) == 3
    assert api_client._get_last_send_error() is not None
    assert api_client._get_last_send_error()["status"] == 503
    assert api_client._get_last_send_error()["attempts"] == 3


def test_post_card_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    err1 = _requests.exceptions.ConnectionError("boom")
    session, call_log = _make_send_session([err1, _FakeResponse(200, {"id": "card-1"})])
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client.post_card({"type": "AdaptiveCard"}, "conv1")
    assert result == "card-1"
    assert len(call_log) == 2
    assert api_client._get_last_send_error() is None
    assert sleeps == [3]


def test_post_card_401_retries_once_then_flags_token(monkeypatch):
    """See test_post_returning_id_401_retries_once_then_flags_token."""
    monkeypatch.setattr(api_client, "_401_RETRY_DELAY_S", 0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    resp = _FakeResponse(401, {})
    session, call_log = _make_send_session([resp, resp])
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client.post_card({"type": "AdaptiveCard"}, "conv1")
    assert result is None
    assert len(call_log) == api_client._401_RETRIES + 1
    assert api_client._needs_token_refresh is True
    assert api_client._get_last_send_error()["status"] == 401


def test_edit_message_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    err1 = _requests.exceptions.ConnectionError("boom")
    session, call_log = _make_send_session([err1, _FakeResponse(200, {})], method="put")
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client.edit_message("msg1", "<p>hi</p>", "conv1")
    assert result is True
    assert len(call_log) == 2
    assert api_client._get_last_send_error() is None
    assert sleeps == [3]


def test_edit_message_401_retries_once_then_flags_token(monkeypatch):
    """See test_post_returning_id_401_retries_once_then_flags_token."""
    monkeypatch.setattr(api_client, "_401_RETRY_DELAY_S", 0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    resp = _FakeResponse(401, {})
    session, call_log = _make_send_session([resp, resp], method="put")
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client.edit_message("msg1", "<p>hi</p>", "conv1")
    assert result is False
    assert len(call_log) == api_client._401_RETRIES + 1
    assert api_client._needs_token_refresh is True
    assert api_client._get_last_send_error()["status"] == 401


def test_edit_message_success_clears_last_send_error(monkeypatch):
    api_client._set_last_send_error({"status": 500, "err_type": "x", "message": "y", "attempts": 3})
    session, _ = _make_send_session([_FakeResponse(200, {})], method="put")
    monkeypatch.setattr(api_client, "_session", lambda: session)
    result = api_client.edit_message("msg1", "<p>hi</p>", "conv1")
    assert result is True
    assert api_client._get_last_send_error() is None


# ---------------------------------------------------------------------------
# _build_post_body amsreferences param — default stays [] (historical behavior)
# ---------------------------------------------------------------------------

def test_build_post_body_default_amsreferences_empty():
    body = api_client._build_post_body("conv1", "<p>hi</p>")
    assert body["amsreferences"] == []


def test_build_post_body_custom_amsreferences():
    body = api_client._build_post_body("conv1", "<p>hi</p>", amsreferences=["obj-1"])
    assert body["amsreferences"] == ["obj-1"]


# ---------------------------------------------------------------------------
# download_image / _image_host_allowed — host allowlist guards the Bearer token
# ---------------------------------------------------------------------------


def test_image_host_allowed_for_known_microsoft_hosts():
    assert api_client._image_host_allowed("https://something.skype.com/x.jpg")
    assert api_client._image_host_allowed("https://eu-prod.asyncgw.teams.microsoft.com/x")
    assert api_client._image_host_allowed("https://statics.teams.cdn.office.net/evergreen-assets/x.png")


def test_image_host_allowed_rejects_arbitrary_host():
    assert not api_client._image_host_allowed("https://attacker.example/x.jpg")
    assert not api_client._image_host_allowed("https://skype.com.attacker.example/x.jpg")


def test_image_host_allowed_rejects_malformed_url():
    assert not api_client._image_host_allowed("not-a-url")
    assert not api_client._image_host_allowed("")


def test_download_image_rejects_disallowed_host_without_hitting_session(monkeypatch):
    """A forged <img src> pointing off-Microsoft must never reach _session()
    (which always attaches the live Teams Bearer token) -- this is the
    SSRF/token-exfil guard, so it must fail closed before any network call."""
    called = []
    monkeypatch.setattr(api_client, "_session", lambda: called.append(True))
    result = api_client.download_image("https://attacker.example/x.jpg")
    assert result is None
    assert called == []


def test_download_image_allows_known_host(monkeypatch):
    resp = _FakeResponse(200, {})
    resp.headers = {"Content-Type": "image/png"}
    resp.content = b"x" * 3000

    class _Sess:
        def get(self, url, timeout=None, allow_redirects=None):
            assert allow_redirects is False
            return resp

    monkeypatch.setattr(api_client, "_session", lambda: _Sess())
    result = api_client.download_image("https://statics.teams.cdn.office.net/x.png")
    assert result == (b"x" * 3000, "image/png")


# ---------------------------------------------------------------------------
# Token identity + transient-401 handling
# ---------------------------------------------------------------------------
# Concurrent get_messages calls can return a transient 401 while the Bearer
# token is still valid -- the same token replayed shortly afterwards gets 200.
# Two defects would turn that blip into a long refresh loop:
#   1. set_auth_token() bumped _auth_token_ts on every capture, so re-capturing
#      an IDENTICAL token looked like a successful refresh, and cleared
#      _needs_token_refresh so the next 401 looked brand new.
#   2. the first 401 was taken as proof the token had expired.


class _Resp401(Exception):
    """Stand-in for requests' HTTPError carrying a 401 response."""

    def __init__(self, status=401):
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()


@pytest.fixture(autouse=False)
def _reset_token(tmp_path, monkeypatch):
    """Isolate token state AND the on-disk cache.

    set_auth_token() writes through to _TOKEN_CACHE (scripts/teams_agent/.auth-token),
    which is the live process's real token file -- a test calling it with a dummy value
    would overwrite the running bot's credential. Redirect the cache at tmp_path.
    """
    monkeypatch.setattr(api_client, "_TOKEN_CACHE", tmp_path / ".auth-token")
    before = (api_client._auth_token, api_client._auth_token_ts,
              api_client._auth_token_gen, api_client._needs_token_refresh)
    yield
    (api_client._auth_token, api_client._auth_token_ts,
     api_client._auth_token_gen, api_client._needs_token_refresh) = before


def test_same_token_recaptured_does_not_bump_generation(_reset_token):
    """_auth_token_gen tracks the VALUE -- the signal cli.py's refresh gate needs."""
    api_client.set_auth_token("tok-A")
    gen_after_first = api_client._auth_token_gen
    ts_after_first = api_client._auth_token_ts

    time.sleep(0.01)
    api_client.set_auth_token("tok-A")  # Teams hands back the identical token

    assert api_client._auth_token_gen == gen_after_first, (
        "re-capturing the same token must NOT count as a refresh"
    )
    assert api_client._auth_token_ts > ts_after_first, (
        "_auth_token_ts still advances -- which is exactly why it cannot be the gate"
    )

    api_client.set_auth_token("tok-B")
    assert api_client._auth_token_gen == gen_after_first + 1


def test_recapturing_same_token_keeps_the_401_flag(_reset_token):
    """An identical token cannot fix a 401, so it must not clear the flag."""
    api_client.set_auth_token("tok-A")
    api_client._needs_token_refresh = True

    api_client.set_auth_token("tok-A")
    assert api_client._needs_token_refresh is True, (
        "clearing the flag on an unchanged token makes the next 401 look new, "
        "restarting the refresh loop indefinitely"
    )

    api_client.set_auth_token("tok-B")
    assert api_client._needs_token_refresh is False


def test_transient_401_is_retried_and_does_not_blame_the_token(monkeypatch, _reset_token):
    """A 401 that clears on retry must return data and leave the token alone."""
    api_client.set_auth_token("tok-A")
    api_client._needs_token_refresh = False
    monkeypatch.setattr(api_client, "_401_RETRY_DELAY_S", 0)

    calls = {"n": 0}

    class _FakeSession:
        def get(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _Resp401()
            return type("R", (), {
                "json": staticmethod(lambda: {"messages": []}),
                "raise_for_status": staticmethod(lambda: None),
            })()

    monkeypatch.setattr(api_client, "_session", lambda: _FakeSession())
    api_client.get_messages("48:notes", n=1)

    assert calls["n"] == 2, "the first 401 must be retried in place"
    assert api_client._needs_token_refresh is False, (
        "a 401 that clears on retry is not evidence the token expired"
    )


def test_persistent_401_still_flags_the_token(monkeypatch, _reset_token):
    """Retries must not mask a genuinely dead token."""
    api_client.set_auth_token("tok-A")
    api_client._needs_token_refresh = False
    monkeypatch.setattr(api_client, "_401_RETRY_DELAY_S", 0)

    calls = {"n": 0}

    class _FakeSession:
        def get(self, *a, **kw):
            calls["n"] += 1
            raise _Resp401()

    monkeypatch.setattr(api_client, "_session", lambda: _FakeSession())
    assert api_client.get_messages("48:notes", n=1) == []

    assert calls["n"] == api_client._401_RETRIES + 1
    assert api_client._needs_token_refresh is True


def test_token_fingerprint_distinguishes_equal_length_tokens():
    """len() cannot tell two tokens apart -- every token for one user is 2262 chars."""
    a, b = "x" * 2262, "y" * 2262
    assert len(a) == len(b)
    assert api_client.token_fingerprint(a) != api_client.token_fingerprint(b)
    assert api_client.token_fingerprint(a) == api_client.token_fingerprint(a)
    assert api_client.token_fingerprint("") == "-"
    assert len(api_client.token_fingerprint(a)) == 8


def test_token_cache_is_never_written_by_the_suite():
    """The real .auth-token must survive a test run.

    set_auth_token() writes through to _TOKEN_CACHE, so a test that calls it
    without redirecting the path overwrites the live bot's credential. Any test touching the token
    must use the _reset_token fixture (it repoints _TOKEN_CACHE at tmp_path).
    """
    live = Path(api_client.__file__).parent / ".auth-token"
    if not live.exists():
        pytest.skip("no live token cache on this machine")
    body = live.read_text(encoding="utf-8").strip()
    assert not body.startswith("tok-"), (
        f"{live} contains a test dummy ({body[:20]!r}) -- a test wrote through to "
        f"the real token cache"
    )


# ---------------------------------------------------------------------------
# Agent persona registry — single source of truth for labels/prefixes
# ---------------------------------------------------------------------------

def test_agent_html_prefix_per_persona():
    assert "[Bob]" in api_client.agent_html_prefix(None)
    assert "[Asystent]" in api_client.agent_html_prefix("example")


def test_example_persona_has_its_own_emoji():
    """A distinct emoji keeps the persona's stamp distinguishable from Bob's at a
    glance on the shared account."""
    assert api_client.agent_html_prefix("example").startswith("\U0001f4a1")
    assert api_client.agent_html_prefix("example") != api_client.agent_html_prefix(None)


def test_agent_html_prefix_unknown_persona_falls_back_to_bob():
    assert "[Bob]" in api_client.agent_html_prefix("nie-ma-takiej-persony")


def test_bot_html_prefixes_cover_every_persona():
    """delete_message()'s safety check walks this tuple — a persona missing from it
    means its own messages are unrecognisable as bot-authored. Retired labels count
    too: messages posted under an old name are still ours."""
    _legacy = len(api_client._legacy_prefix_pairs())
    assert len(api_client.BOT_HTML_PREFIXES) == 1 + len(api_client.AGENT_LABELS) + _legacy
    assert len(set(api_client.BOT_HTML_PREFIXES)) == len(api_client.BOT_HTML_PREFIXES)
    for label in ("[Bob]", "[Asystent]"):
        assert any(label in p for p in api_client.BOT_HTML_PREFIXES)
    assert all(p.startswith("<p>") for p in api_client.BOT_HTML_PREFIXES)


def test_agent_text_labels_match_prefixes():
    assert api_client.AGENT_TEXT_LABELS == ("[Bob]", "[Asystent]")


def test_agent_label_from_html_identifies_each_persona():
    for persona, expected in (
        (None, "Bob"), ("example", "Asystent"),
    ):
        raw = f"<p>{api_client.agent_html_prefix(persona)}: tresc</p>"
        assert api_client.agent_label_from_html(raw) == expected


def test_legacy_prefix_pairs_keep_the_stored_and_the_current_emoji(monkeypatch):
    """A renamed persona's messages already on the channel carry the old stamp; they
    must still read as the agent's own, whether stamped with the old emoji or (mid-
    rename) with the persona's current one."""
    monkeypatch.setattr(api_client, "_LEGACY_AGENT_LABELS", {"example": (("Pomocnik", "\U0001f4dd"),)})
    assert api_client._legacy_prefix_pairs() == (
        ("example", "<p>\U0001f4dd<strong>[Pomocnik]</strong>"),
        ("example", "<p>\U0001f4a1<strong>[Pomocnik]</strong>"),
    )


def test_legacy_prefix_uses_stored_emoji_not_current_one():
    """Regression: legacy prefixes were built with the persona's NEW emoji only,
    so the real historical stamps never matched."""
    for entries in api_client._LEGACY_AGENT_LABELS.values():
        for label, emoji in entries:
            assert f"<p>{emoji}<strong>[{label}]</strong>" in api_client.BOT_HTML_PREFIXES


def test_sending_never_uses_a_retired_label():
    """Legacy labels are for detection only — nothing may post under one."""
    for persona in api_client.AGENT_LABELS:
        prefix = api_client.agent_html_prefix(persona)
        for old, _emoji in api_client._LEGACY_AGENT_LABELS.get(persona, ()):
            assert f"[{old}]" not in prefix


def test_agent_label_from_html_returns_none_for_human_message():
    assert api_client.agent_label_from_html("<p>zwykła wiadomość</p>") is None


def test_send_gif_example_persona_uses_its_prefix(monkeypatch):
    """The GIF prefix must match BOT_HTML_PREFIXES, or delete_message() refuses to
    clean up the persona's own GIF posts."""
    captured = {}
    monkeypatch.setattr(api_client, "search_gif", lambda q: "https://example.test/x.gif")
    monkeypatch.setattr(
        api_client, "_post",
        lambda conv_id, content, mentions=None: captured.setdefault("content", content) or True,
    )
    api_client.send_gif("order form", "conv1", persona="example")
    assert captured["content"].startswith(
        f'<p>{api_client.agent_html_prefix("example")}'
    )


def test_token_cache_is_owner_only(_reset_token):
    """The cached Teams Bearer token must never be world-readable (was 0644)."""
    api_client.set_auth_token("tok-perm-check")
    assert api_client._TOKEN_CACHE.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# ic3 token + AMS upload -- Bob's own [BOB_GIF: mood] gifs
# ---------------------------------------------------------------------------

@pytest.fixture()
def _reset_ic3(tmp_path, monkeypatch):
    monkeypatch.setattr(api_client, "_IC3_TOKEN_CACHE", tmp_path / ".ic3-auth-token")
    monkeypatch.setattr(api_client, "_ic3_token", "")
    yield tmp_path / ".ic3-auth-token"


def test_set_ic3_token_caches_owner_only_and_independent_of_chatsvc(_reset_ic3, _reset_token):
    api_client.set_auth_token("chatsvc-tok")
    api_client.set_ic3_token("ic3-tok")
    assert api_client._ic3_token == "ic3-tok"  # noqa: S105
    assert _reset_ic3.read_text(encoding="utf-8") == "ic3-tok"
    assert _reset_ic3.stat().st_mode & 0o777 == 0o600
    assert api_client._TOKEN_CACHE.read_text(encoding="utf-8") == "chatsvc-tok"
    assert api_client._load_cached_ic3_token()[0] == "ic3-tok"  # noqa: S105


def test_load_cached_ic3_token_missing_file(_reset_ic3):
    assert api_client._load_cached_ic3_token() == ("", 0.0)


class _AmsResp:
    def __init__(self, status_code=201, json_body=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_body


def test_ams_upload_gif_without_ic3_token_returns_none(_reset_ic3, tmp_path):
    gif = tmp_path / "x.gif"
    gif.write_bytes(b"GIF89a")
    assert api_client._ams_upload_gif(gif, "19:chat06@thread.v2") is None


def test_ams_upload_gif_two_call_sequence(_reset_ic3, tmp_path, monkeypatch):
    monkeypatch.setattr(api_client, "_ic3_token", "ic3-tok")
    gif = tmp_path / "x.gif"
    data = b"GIF89a" + b"0" * 20
    gif.write_bytes(data)
    calls = []
    monkeypatch.setattr(api_client._requests, "post",
                        lambda url, json=None, headers=None, timeout=None:
                        calls.append(("post", url, json, headers)) or _AmsResp(201, {"id": "obj-1"}))
    monkeypatch.setattr(api_client._requests, "put",
                        lambda url, data=None, headers=None, timeout=None:
                        calls.append(("put", url, data, headers)) or _AmsResp(201))
    assert api_client._ams_upload_gif(gif, "19:chat11@thread.v2") == ("obj-1", len(data))
    assert calls[0][1] == f"{api_client._AMS_BASE}/v1/objects/"
    assert calls[0][2]["permissions"] == {"19:chat11@thread.v2": ["read"]}
    assert calls[0][3]["Authorization"] == "Bearer ic3-tok" and "sec-ch-ua" in calls[0][3]
    assert calls[1][1] == f"{api_client._AMS_BASE}/v1/objects/obj-1/content/imgpsh" and calls[1][2] == data


def test_ams_upload_gif_http_failure_returns_none(_reset_ic3, tmp_path, monkeypatch):
    monkeypatch.setattr(api_client, "_ic3_token", "ic3-tok")
    gif = tmp_path / "x.gif"
    gif.write_bytes(b"GIF89a")
    monkeypatch.setattr(api_client._requests, "post", lambda *a, **k: _AmsResp(201, {"id": "obj-2"}))
    monkeypatch.setattr(api_client._requests, "put", lambda *a, **k: _AmsResp(500))
    assert api_client._ams_upload_gif(gif, "conv1") is None


def test_send_ams_gif_message_posts_img_with_amsreferences(tmp_path, monkeypatch):
    gif = tmp_path / "bob-happy.gif"
    gif.write_bytes(b"GIF89a")
    monkeypatch.setattr(api_client, "_ams_upload_gif", lambda f, c: ("obj-9", 123))
    captured = {}

    def fake_post(conv, content, mentions=None, amsreferences=None):
        captured.update(conv=conv, content=content, amsreferences=amsreferences)
        return "msg-77"

    monkeypatch.setattr(api_client, "_post_returning_id", fake_post)
    caption = f"<p>{api_client.BOT_HTML_PREFIXES[0]}: no i git</p>"
    assert api_client.send_ams_gif_message(caption, gif, "conv1") == "msg-77"
    assert captured["amsreferences"] == ["obj-9"] and captured["content"].startswith(caption)
    assert 'itemid="obj-9"' in captured["content"] and 'data-file-size="123"' in captured["content"]


def test_send_ams_gif_message_upload_failure_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(api_client, "_ams_upload_gif", lambda f, c: None)
    assert api_client.send_ams_gif_message("<p>x</p>", tmp_path / "x.gif", "conv1") is None
