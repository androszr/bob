"""Shared "has this SharePoint file changed?" check for the xlsx pollers.

One cheap REST call (no full xlsx download) gates the heavier download+transform
poller (sharepoint_poller.py) so it skips work when nothing changed upstream.

TimeLastModified comes from SharePoint's _api/web/GetFileByServerRelativeUrl(...) REST
endpoint. A bare REST call (no prior page navigation on the same site collection) gets
a 403 -- observed for both a team-site document library and the personal -my site --
navigating to any page on that site collection first mints additional session-scoped
cookies the REST call needs. Every check therefore does: goto(referer_url), then
request.get(rest_api_url), inside one throwaway browser context.

Uses the same SSO session.json as the pollers.
"""
from __future__ import annotations

import json
from pathlib import Path

from session import load_storage_state

SESSION_FILE = Path(__file__).parent / "session.json"
STATE_FILE = Path(__file__).parent / "sharepoint_mtimes.json"


def fetch_remote_mtime(rest_api_url: str, referer_url: str) -> str:
    """Return the file's TimeLastModified (ISO-8601 UTC, e.g. '2026-07-28T19:08:17Z').

    Raises RuntimeError if session.json is missing, the session is expired
    (redirected to a login page), or the REST call fails.
    """
    from playwright.sync_api import sync_playwright  # local import -- isolates Playwright

    if not SESSION_FILE.exists():
        raise RuntimeError("session.json not found -- run login.py first")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=load_storage_state(SESSION_FILE))
        page = context.new_page()

        page.goto(referer_url, wait_until="domcontentloaded", timeout=15_000)
        current_url = page.url
        if "login.microsoftonline.com" in current_url or "login.live.com" in current_url:
            browser.close()
            raise RuntimeError(
                "Session expired -- redirected to login page. Run login.py to refresh."
            )

        resp = context.request.get(
            rest_api_url,
            headers={"Accept": "application/json;odata=nometadata"},
            timeout=15_000,
        )
        body = resp.text()
        browser.close()

    if not resp.ok:
        raise RuntimeError(f"SharePoint metadata check failed: HTTP {resp.status} {body[:200]}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"SharePoint metadata response nie jest JSON: {body[:200]}") from exc

    mtime = data.get("TimeLastModified")
    if not mtime:
        raise RuntimeError(f"SharePoint metadata response bez TimeLastModified: {data}")
    return mtime


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def has_changed(key: str, remote_mtime: str, state: dict) -> bool:
    """True if remote_mtime differs from the last-recorded mtime for key.

    Plain string inequality (not a > comparison) on purpose: any change --
    including a revert to an earlier timestamp -- should trigger a re-pull.
    """
    return state.get(key) != remote_mtime


def mark_processed(key: str, remote_mtime: str) -> None:
    """Persist remote_mtime as the last-processed value for key."""
    state = load_state()
    state[key] = remote_mtime
    save_state(state)
