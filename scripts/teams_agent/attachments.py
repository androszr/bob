"""Download SharePoint-hosted Teams attachments using cached FedAuth cookies.

Threading model: the Teams watch loop runs chat processing in a ThreadPoolExecutor,
but the main Playwright context (in cli.main) is single-threaded and cannot be
shared. This module keeps an in-memory map of SharePoint cookies and serves
parallel downloads via plain `requests` (thread-safe). Cookies are kept per
cookie domain: <tenant>.sharepoint.com and <tenant>-my.sharepoint.com
each have their own FedAuth, and sending the wrong one gets a 302 to the login
page (a site document "without access" while the browser opens it fine). On a 302 / HTML viewer /
unknown content response, a single worker takes a global lock, opens a
one-shot Playwright session against session.json to refresh the cookies, and
shares the result with the rest of the pool.

Cookies are persisted to sp-cookies.json (gitignored) so daemon restarts skip
the Playwright round-trip until FedAuth expires (~ a few hours).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests as _requests
from session import is_partitioned, load_storage_state

# Whitelist of acceptable host suffixes for attachment URLs. Teams.chatsvc gives
# us a URL straight from properties.files; in theory a malicious sender could
# craft one pointing anywhere. Limit to SharePoint hosts so we never blindly fetch
# from third-party hosts (and never leak FedAuth
# cookies cross-domain, although requests would already not do that by default).
_ALLOWED_HOST_SUFFIXES = (
    ".sharepoint.com",
)


def _host_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == suffix.lstrip(".") or host.endswith(suffix)
               for suffix in _ALLOWED_HOST_SUFFIXES)

_SP_COOKIES_FILE = Path(__file__).parent / "sp-cookies.json"
_SESSION_FILE = Path(__file__).parent / "session.json"

_lock = threading.Lock()
# cookie domain as Playwright reports it (".sharepoint.com", "<tenant>.sharepoint.com")
# -> {name: value}
_cookies: dict[str, dict[str, str]] = {}
_cookies_loaded = False

# FedAuth lifetime in the wild is several hours; refresh proactively well before
# the worst-case expiry. On a real expiry the post-refresh fetch will re-trigger
# Playwright anyway, so this constant only controls when we *prefer* disk cookies.
_DISK_COOKIE_TTL_S = 4 * 3600

# Hard cap on attachment size. Typical .md notes are <100 KB; anything larger
# than this is almost certainly either a misuse (token bomb) or not a real
# Markdown document. Refuse to load it into the prompt rather than truncating
# silently — silent truncation reads as success to the model.
_MAX_ATTACHMENT_BYTES = 512 * 1024   # 512 KB — text/md files
_MAX_VIDEO_BYTES = 800 * 1024 * 1024  # 800 MB — video/audio pipeline


def _cookies_for(url: str, jar: dict[str, dict[str, str]]) -> dict[str, str]:
    """The cookies a browser would send to ``url``: domain-wide ones (".sharepoint.com")
    first, then the host's own, which win on a name clash."""
    host = (urlparse(url).hostname or "").lower()
    out: dict[str, str] = {}
    for domain in sorted(jar, key=lambda d: (not d.startswith("."), len(d))):
        bare = domain.lstrip(".").lower()
        if host == bare or (domain.startswith(".") and host.endswith("." + bare)):
            out.update(jar[domain])
    return out


def _load_disk_cookies() -> dict[str, dict[str, str]]:
    try:
        if _SP_COOKIES_FILE.exists():
            data = json.loads(_SP_COOKIES_FILE.read_text(encoding="utf-8"))
            # The pre-per-domain flat "cookies" format is ignored -> one Playwright refresh.
            if data.get("expires", 0) > time.time():
                return data.get("cookies_by_domain", {}) or {}
    except Exception as exc:
        print(f"[attachments] _load_disk_cookies blad: {exc}", file=sys.stderr, flush=True)
    return {}


def _save_disk_cookies(cookies: dict[str, dict[str, str]]) -> None:
    try:
        payload = {"cookies_by_domain": cookies, "expires": time.time() + _DISK_COOKIE_TTL_S}
        _SP_COOKIES_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:
        print(f"[attachments] _save_disk_cookies blad: {exc}", file=sys.stderr, flush=True)


def _ensure_loaded() -> None:
    global _cookies, _cookies_loaded
    if _cookies_loaded:
        return
    _cookies = _load_disk_cookies()
    _cookies_loaded = True
    if _cookies:
        print(f"[attachments] SP cookies wczytane z dysku ({len(_cookies)} domen)",
              file=sys.stderr, flush=True)


def _looks_html(body: bytes, content_type: str) -> bool:
    if content_type.lower().startswith("text/html"):
        return True
    return body[:500].lstrip().lower().startswith((b"<!doctype html", b"<html"))


_HEADERS = {
    "Accept": "text/plain, application/octet-stream, */*",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _attempt_fetch(
    url: str, cookies: dict[str, str], max_bytes: int = _MAX_ATTACHMENT_BYTES
) -> tuple[bytes, str] | None:
    """One HTTP attempt. Returns (body, content_type) if it looks like a real
    file response (status 200, not an HTML viewer, size within cap), else None.

    Uses streaming so a malicious / mis-sized response can be aborted before
    the full body lands in memory.
    """
    try:
        r = _requests.get(
            url, cookies=cookies, headers=_HEADERS,
            timeout=20, allow_redirects=False, stream=True,
        )
    except Exception as exc:
        print(f"[attachments] HTTP blad: {exc}", file=sys.stderr, flush=True)
        return None
    try:
        if r.status_code != 200:
            return None
        # Bail early on advertised oversized files. Content-Length is advisory
        # (a hostile server could lie), so we also enforce the cap during streaming.
        try:
            advertised = int(r.headers.get("Content-Length", "0"))
        except ValueError:
            advertised = 0
        if advertised and advertised > max_bytes:
            print(
                f"[attachments] odrzucam: Content-Length={advertised} > cap "
                f"{max_bytes}", file=sys.stderr, flush=True,
            )
            return None

        buf = bytearray()
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > max_bytes:
                print(
                    f"[attachments] odrzucam: rozmiar przekroczyl cap "
                    f"{max_bytes} w trakcie pobierania",
                    file=sys.stderr, flush=True,
                )
                return None
        body = bytes(buf)
        ct = r.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        if _looks_html(body, ct):
            return None
        return body, ct
    finally:
        try:
            r.close()
        except Exception:
            pass


def _refresh_via_playwright(file_url: str) -> dict[str, dict[str, str]]:
    """Run a one-shot Playwright session in this thread to grab fresh SP cookies."""
    from playwright.sync_api import sync_playwright

    print(f"[attachments] refresh SP cookies via Playwright (goto {file_url[:80]}...)",
          file=sys.stderr, flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(storage_state=load_storage_state(_SESSION_FILE))
            page = context.new_page()
            try:
                page.goto(file_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                # SharePoint pages keep web parts spinning indefinitely; FedAuth
                # is already set by the time domcontentloaded would fire.
                pass
            cookies: dict[str, dict[str, str]] = {}
            for c in context.cookies():
                if "sharepoint.com" in c["domain"] and not is_partitioned(c):
                    cookies.setdefault(c["domain"], {})[c["name"]] = c["value"]
        finally:
            try:
                browser.close()
            except Exception:
                pass
    n = sum(len(v) for v in cookies.values())
    print(f"[attachments] -> got {n} SP cookies ({len(cookies)} domen)", file=sys.stderr, flush=True)
    return cookies


def ensure_cookies(probe_url: str) -> dict[str, str]:
    """Return the SharePoint cookies for probe_url's host; refresh via Playwright if missing.

    Reused by attachments_upload.py — uploading needs the same FedAuth cookies
    as downloading. probe_url is whatever SharePoint URL we're about to hit
    (used as the page.goto target for the Playwright refresh).
    """
    global _cookies
    _ensure_loaded()
    def _has_fedauth() -> bool:
        return "FedAuth" in _cookies_for(probe_url, _cookies)

    if _has_fedauth():
        return _cookies_for(probe_url, _cookies)
    with _lock:
        if _has_fedauth():
            return _cookies_for(probe_url, _cookies)
        try:
            new_cookies = _refresh_via_playwright(probe_url)
        except Exception as exc:
            print(f"[attachments] ensure_cookies refresh blad: {exc}",
                  file=sys.stderr, flush=True)
            return {}
        if new_cookies:
            _cookies = {**_cookies, **new_cookies}
            _save_disk_cookies(_cookies)
        return _cookies_for(probe_url, _cookies)


def invalidate_cookies() -> None:
    """Drop the cached cookie jar so the next call re-runs Playwright refresh."""
    global _cookies, _cookies_loaded
    with _lock:
        _cookies = {}
        _cookies_loaded = False
        try:
            if _SP_COOKIES_FILE.exists():
                _SP_COOKIES_FILE.unlink()
        except Exception:
            pass


_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB — images are binary, no token-bomb risk


def download(
    url: str, file_name: str = "", max_bytes: int = _MAX_ATTACHMENT_BYTES
) -> tuple[bytes, str] | None:
    """Download a Teams attachment URL. Returns (bytes, content_type) or None.

    Tries cached cookies first; on miss takes a global lock, refreshes via
    Playwright, and retries. Safe to call from worker threads.

    max_bytes: size cap. Use _MAX_IMAGE_BYTES for binary image files.
    """
    global _cookies
    if not url:
        return None
    if not _host_allowed(url):
        print(
            f"[attachments] odrzucam: host nie na whitelist ({url[:120]})",
            file=sys.stderr, flush=True,
        )
        return None
    _ensure_loaded()

    if _cookies:
        result = _attempt_fetch(url, _cookies_for(url, _cookies), max_bytes=max_bytes)
        if result is not None:
            return result

    with _lock:
        # Another thread may have refreshed while we waited.
        result = _attempt_fetch(url, _cookies_for(url, _cookies), max_bytes=max_bytes)
        if result is not None:
            return result

        try:
            new_cookies = _refresh_via_playwright(url)
        except Exception as exc:
            print(f"[attachments] Playwright refresh blad: {exc}",
                  file=sys.stderr, flush=True)
            return None
        if not new_cookies:
            print("[attachments] Playwright nie zwrocil cookies SP — fetch nie powiedzie sie",
                  file=sys.stderr, flush=True)
            return None
        # Merge per domain: a refresh for one host must not drop the other host's FedAuth.
        _cookies = {**_cookies, **new_cookies}
        _save_disk_cookies(_cookies)

    result = _attempt_fetch(url, _cookies_for(url, _cookies), max_bytes=max_bytes)
    if result is None:
        print(f"[attachments] po refreshu cookies nadal nie pobralo {file_name!r}",
              file=sys.stderr, flush=True)
    return result
