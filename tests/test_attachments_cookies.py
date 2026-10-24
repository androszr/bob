"""attachments: SharePoint cookies are sent per host, never flattened across hosts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent"))

import attachments  # noqa: E402

_JAR = {
    ".sharepoint.com": {"rtFa": "wide", "SIMI": "s"},
    "contoso-my.sharepoint.com": {"FedAuth": "onedrive", "SPOIDCRL": "x"},
    "contoso.sharepoint.com": {"FedAuth": "sites", "SPA_RT": "r"},
}


def test_site_url_gets_only_its_own_fedauth():
    ck = attachments._cookies_for("https://contoso.sharepoint.com/sites/Projects/a.docx", _JAR)
    assert ck == {"rtFa": "wide", "SIMI": "s", "FedAuth": "sites", "SPA_RT": "r"}


def test_onedrive_url_gets_only_its_own_fedauth():
    ck = attachments._cookies_for("https://contoso-my.sharepoint.com/personal/x/a.docx", _JAR)
    assert ck == {"rtFa": "wide", "SIMI": "s", "FedAuth": "onedrive", "SPOIDCRL": "x"}


def test_host_cookie_wins_over_domain_wide_one():
    jar = {"contoso.sharepoint.com": {"FedAuth": "host"}, ".sharepoint.com": {"FedAuth": "wide"}}
    assert attachments._cookies_for("https://contoso.sharepoint.com/x", jar) == {"FedAuth": "host"}


def test_lookalike_host_gets_nothing_host_specific():
    ck = attachments._cookies_for("https://evilcontoso.sharepoint.com/x", _JAR)
    assert ck == {"rtFa": "wide", "SIMI": "s"}


def test_old_flat_disk_format_is_ignored(tmp_path, monkeypatch):
    f = tmp_path / "sp-cookies.json"
    f.write_text('{"cookies": {"FedAuth": "flat"}, "expires": 9999999999}', encoding="utf-8")
    monkeypatch.setattr(attachments, "_SP_COOKIES_FILE", f)
    assert attachments._load_disk_cookies() == {}
    attachments._save_disk_cookies(_JAR)
    assert attachments._load_disk_cookies() == _JAR


def test_refresh_for_one_host_keeps_the_other_hosts_fedauth(monkeypatch, tmp_path):
    monkeypatch.setattr(attachments, "_SP_COOKIES_FILE", tmp_path / "sp.json")
    monkeypatch.setattr(attachments, "_cookies_loaded", True)
    monkeypatch.setattr(attachments, "_cookies", {"contoso-my.sharepoint.com": {"FedAuth": "onedrive"}})
    monkeypatch.setattr(attachments, "_refresh_via_playwright",
                        lambda url: {"contoso.sharepoint.com": {"FedAuth": "sites"}})
    monkeypatch.setattr(attachments, "_attempt_fetch",
                        lambda url, ck, max_bytes: (b"PK", "x") if ck.get("FedAuth") == "sites" else None)
    assert attachments.download("https://contoso.sharepoint.com/sites/x/a.docx") == (b"PK", "x")
    assert attachments._cookies["contoso-my.sharepoint.com"] == {"FedAuth": "onedrive"}


def test_refresh_inside_asyncio_loop_runs_in_a_worker_thread(monkeypatch):
    import asyncio  # noqa: PLC0415
    import threading  # noqa: PLC0415

    seen = {}

    def fake_here(url):
        seen["thread"] = threading.current_thread().name
        return {"contoso.sharepoint.com": {"FedAuth": "fresh"}}

    monkeypatch.setattr(attachments, "_refresh_via_playwright_here", fake_here)

    async def inside_a_loop():
        return attachments._refresh_via_playwright("https://contoso.sharepoint.com/x")

    assert asyncio.run(inside_a_loop()) == {"contoso.sharepoint.com": {"FedAuth": "fresh"}}
    assert seen["thread"].startswith("sp-refresh")                      # not the loop's own thread
    seen.clear()
    assert attachments._refresh_via_playwright("https://contoso.sharepoint.com/x")
    assert seen["thread"] == threading.current_thread().name            # no loop: runs right here
