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
    python scripts/teams_agent/login.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from session import SESSION_FILE, load_storage_state, save_storage_state
from sp_warmup_urls import SP_WARMUP_URLS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 -- side-effect: CET timestamps na stdout/stderr

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


def main() -> None:
    print("Otwieram przegladarke do logowania Teams SSO...", file=sys.stderr, flush=True)
    with sync_playwright() as p:
        # --proxy-auto-detect intentionally omitted: PAC proxy routes traffic through a
        # corporate proxy that blocks direct SharePoint connections during SP warmup below.
        browser = p.chromium.launch(headless=False, args=["--ignore-certificate-errors"])
        context = browser.new_context()
        page = context.new_page()
        page.goto(TEAMS_URL)
        print(
            "Przegladarka otwarta. Zaloguj sie przez Microsoft SSO (+ SAML organizacji, MFA).",
            file=sys.stderr,
            flush=True,
        )
        print(
            "WAZNE: zaznacz 'Pozostac zalogowanym' / 'Stay signed in' = TAK.",
            file=sys.stderr,
            flush=True,
        )
        print(
            "Gdy zobaczysz interfejs Teams (lista czatow po lewej stronie), wroc tutaj.",
            file=sys.stderr,
            flush=True,
        )
        try:
            input("Nacisnij Enter gdy jestes zalogowany do Teams (Ctrl-C aby anulowac)... ")
        except (EOFError, KeyboardInterrupt):
            print("\nAnulowane przez uzytkownika.", file=sys.stderr, flush=True)
            _safe_close(browser)
            sys.exit(1)

        current_url = page.url or ""
        if _looks_like_login(current_url) or "teams.microsoft.com" not in current_url.lower():
            print(
                f"BLAD: strona nie wyglada na zalogowany Teams (URL: {current_url}).",
                file=sys.stderr,
                flush=True,
            )
            print(
                "Sesja NIE zostala zapisana. Uruchom login.py ponownie i dokoncz logowanie.",
                file=sys.stderr,
                flush=True,
            )
            _safe_close(browser)
            sys.exit(2)

        save_storage_state(context)
        _safe_close(browser)

    # Warm up both SharePoint subdomains using a separate headless browser —
    # same setup as the pollers — to set FedAuth cookies before saving session.json.
    print("Rozgrzewam sesje SharePoint (obie domeny, headless)...", file=sys.stderr, flush=True)
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
                print(f"  OSTRZEZENIE: nie udalo sie rozgrzac {sp_url}: {exc}", file=sys.stderr, flush=True)
        if any_ok:
            save_storage_state(context2)
            print("Sesja SharePoint zapisana do session.json.", file=sys.stderr, flush=True)
        browser2.close()

    print(f"Sesja zapisana ({SESSION_FILE.name}, chmod 600)", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
