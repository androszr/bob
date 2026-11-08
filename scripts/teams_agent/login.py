"""Standalone Teams SSO login utility.

Opens Chromium (headless=False) and waits for the user to complete Microsoft
SSO + tenant SAML + MFA, then persists `storage_state` (cookies + localStorage)
to SESSION_FILE with mode 600.

The terminal-Enter handshake is intentional: the initial Teams URL and the
post-login URL are visually indistinguishable, so an auto-detect predicate
would either fire too early (closing the browser before login finishes) or
miss tenant-specific redirect chains.

NOTE: Requires a real display (X server / macOS Aqua). Do NOT run on a headless
CI server — the browser will fail with 'Missing X server'.

Usage:
    python scripts/teams_agent/login.py                      # terminal: Enter when logged in
    python scripts/teams_agent/login.py --done-file PATH     # Bob's panel: "I'm signed in" creates PATH

With --done-file (the panel runs it without a terminal) the Enter is replaced by the file: the panel's button
creates it, this script then checks the page and saves the session. Closing the browser or 20 minutes without
the button cancels it.
"""

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from session import SESSION_FILE, load_storage_state, save_storage_state
from sp_warmup_urls import SP_WARMUP_URLS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 -- side-effect: local-time timestamps on stdout/stderr

TEAMS_URL = "https://teams.microsoft.com/v2/"

_LOGIN_HOST_FRAGMENTS = (
    "login.microsoftonline.com",
    "login.microsoft.com",
    "account.activedirectory.windowsazure.com",
    "login.live.com",
)


def _looks_like_login(url: str) -> bool:
    u = (url or "").lower()
    return any(frag in u for frag in _LOGIN_HOST_FRAGMENTS)


def _safe_close(browser) -> None:
    """Close the browser without raising if it's already been closed."""
    try:
        browser.close()
    except Exception:
        pass


PANEL_TIMEOUT_S = 20 * 60


def _wait_for_panel(page, done_file: Path) -> bool:
    """Panel mode: True once the panel's "I'm signed in" button created done_file; False when the browser was
    closed or nothing happened for PANEL_TIMEOUT_S."""
    deadline = time.time() + PANEL_TIMEOUT_S
    while time.time() < deadline:
        if done_file.exists():
            done_file.unlink(missing_ok=True)
            return True
        if page.is_closed():
            return False
        try:
            page.wait_for_timeout(1000)
        except Exception:                               # the browser window was closed
            return False
    return False


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Teams SSO sign-in (session.json).")
    ap.add_argument("--done-file", help="panel mode: wait for this file instead of Enter")
    args = ap.parse_args(argv)
    done_file = Path(args.done_file) if args.done_file else None
    if done_file:
        done_file.unlink(missing_ok=True)
    print("Opening the browser for Teams SSO sign-in...", file=sys.stderr, flush=True)
    with sync_playwright() as p:
        # --proxy-auto-detect intentionally omitted: PAC proxy routes traffic through a
        # corporate proxy that blocks direct SharePoint connections during SP warmup below.
        browser = p.chromium.launch(headless=False, args=["--ignore-certificate-errors"])
        context = browser.new_context()
        page = context.new_page()
        page.goto(TEAMS_URL)
        print(
            "Browser open. Sign in with Microsoft SSO (+ your organisation's SAML, MFA).",
            file=sys.stderr,
            flush=True,
        )
        print(
            "IMPORTANT: answer 'Stay signed in' = YES.",
            file=sys.stderr,
            flush=True,
        )
        if done_file:
            print("When you see the Teams interface (chat list on the left), press "
                  "\"I'm signed in\" in Bob's panel.", file=sys.stderr, flush=True)
            if not _wait_for_panel(page, done_file):
                print("Cancelled (browser closed, or 20 min without confirmation in the panel).",
                      file=sys.stderr, flush=True)
                _safe_close(browser)
                sys.exit(1)
        else:
            print(
                "When you see the Teams interface (chat list on the left), come back here.",
                file=sys.stderr,
                flush=True,
            )
            try:
                input("Press Enter once you are signed in to Teams (Ctrl-C to cancel)... ")
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled by the user.", file=sys.stderr, flush=True)
                _safe_close(browser)
                sys.exit(1)

        current_url = page.url or ""
        if _looks_like_login(current_url) or "teams.microsoft.com" not in current_url.lower():
            print(
                f"ERROR: the page does not look like a signed-in Teams (URL: {current_url}).",
                file=sys.stderr,
                flush=True,
            )
            print(
                "The session was NOT saved. Run login.py again and finish signing in.",
                file=sys.stderr,
                flush=True,
            )
            _safe_close(browser)
            sys.exit(2)

        save_storage_state(context)
        _safe_close(browser)

    # Warm up both SharePoint subdomains using a separate headless browser —
    # same setup as the pollers — to set FedAuth cookies before saving session.json.
    print("Warming up the SharePoint session (both domains, headless)...", file=sys.stderr, flush=True)
    with sync_playwright() as p2:
        browser2 = p2.chromium.launch(headless=True)
        context2 = browser2.new_context(storage_state=load_storage_state())
        any_ok = False
        for sp_url in SP_WARMUP_URLS:
            try:
                sp_page = context2.new_page()
                sp_page.goto(sp_url, wait_until="domcontentloaded", timeout=20_000)
                sp_page.wait_for_timeout(1_500)
                sp_page.close()
                print(f"  OK: {sp_url}", file=sys.stderr, flush=True)
                any_ok = True
            except Exception as exc:
                print(f"  WARNING: could not warm up {sp_url}: {exc}", file=sys.stderr, flush=True)
        if any_ok:
            save_storage_state(context2)
            print("SharePoint session saved to session.json.", file=sys.stderr, flush=True)
        browser2.close()

    print(f"Session saved ({SESSION_FILE.name}, chmod 600)", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
