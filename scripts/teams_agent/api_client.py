"""Teams REST API client — bypasses Playwright DOM for read/send operations.

Uses cookies from session.json (written by login.py / Playwright) to make
authenticated HTTP calls to the Teams chatsvc API discovered via network sniff.

Public API:
    get_messages(conversation_id, n)          -> list[dict]
    send_message(text, conversation_id)       -> bool
    reply_to_message(text, reply_to_id, ...)  -> bool
    find_conversation_id(display_name)        -> str | None
    search_gif(query)                         -> str | None
    send_gif(query, conversation_id)          -> bool
"""

import hashlib as _hashlib
import json
import os
import random
import re
import sys
import threading
import urllib.parse
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import requests as _requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import org_config  # noqa: E402
from session import SESSION_FILE  # noqa: E402

# ---------------------------------------------------------------------------
# Known constants (from a network sniff)
# ---------------------------------------------------------------------------

_BASE = "https://teams.microsoft.com/api/chatsvc/emea/v1"
# The Teams account the bot posts as (the owner) — OWNER_TEAMS_USER_ID / OWNER_DISPLAY_NAME.
USER_MRI = org_config.OWNER_MRI
USER_DISPLAY = org_config.OWNER_DISPLAY_NAME

# Owner's private notes-to-self conversation ("Notatki" in chat-config.json) —
# the default/anchor conversation for all send/reply/fetch helpers below.
NOTES_CONV_ID = "48:notes"

# ---------------------------------------------------------------------------
# Agent personas — SINGLE SOURCE OF TRUTH for every user-visible agent label.
#
# All personas post through the same Teams account (USER_MRI), so the HTML
# prefix stamped into each message is the ONLY way to tell Bob / a persona /
# the real owner apart afterwards. Anything that needs a persona
# label — reply_to_message(), send_gif(), cli._agent_html_prefix(),
# cli._owner_label(), quote-of-bot detection, delete_message()'s safety check —
# derives it from here. Adding a persona = one entry in AGENT_LABELS + one in
# _AGENT_EMOJI; nothing else needs a new branch.
#
# RENAMING a persona = change its AGENT_LABELS value and add the old
# (label, emoji) pair to _LEGACY_AGENT_LABELS. The persona KEY never changes: it is stored in
# chat-config.json, names the audit files on disk and the reply-token namespace
# in cli.py, so renaming it would orphan all three.
# ---------------------------------------------------------------------------
DEFAULT_AGENT_LABEL = "Bob"
_DEFAULT_AGENT_EMOJI = "\U0001f9d1‍\U0001f4bb"
# persona key (as used in chat-config.json) -> display label
AGENT_LABELS: dict[str, str] = {
    "example": "Asystent",
}
_AGENT_EMOJI: dict[str, str] = {
    "example": "\U0001f4a1",  # light bulb
}
# Labels a persona used to post under. DETECTION ONLY — never used when sending.
# Messages already on a channel keep the label they were stamped with, and every
# own-message guard (skip-own-message, quote-of-bot, delete_message safety,
# _owner_label) matches on that stamp: drop the old label here and the agent
# starts reading its own history as if a human had written it.
# Each entry is (label, emoji) EXACTLY as it was stamped: the HTML prefix is
# emoji+label, so a rename that also changed the emoji must keep the OLD emoji
# here. The legacy label with the persona's CURRENT emoji is recognised too
# (see _legacy_prefix_pairs) in case anything was posted mid-rename.
# Shape: {"example": (("OldLabel", "\U0001f4dd"),)}.
_LEGACY_AGENT_LABELS: dict[str, tuple[tuple[str, str], ...]] = {}


def agent_label(persona: str | None = None) -> str:
    """Display label for a persona ('example' -> 'Asystent'). None -> 'Bob'."""
    return AGENT_LABELS.get(persona or "", DEFAULT_AGENT_LABEL)


def _agent_emoji(persona: str | None = None) -> str:
    return _AGENT_EMOJI.get(persona or "", _DEFAULT_AGENT_EMOJI)


def agent_html_prefix(persona: str | None = None) -> str:
    """Emoji+label fragment placed inside <p>...</p> of every bot message."""
    return f"{_agent_emoji(persona)}<strong>[{agent_label(persona)}]</strong>"


_AGENT_PERSONAS: tuple[str | None, ...] = (None, *AGENT_LABELS)

# (persona, prefix) pairs, current labels first then retired ones. Sending always
# uses agent_html_prefix(); this table is what recognises a message as ours.
def _legacy_prefix_pairs() -> tuple[tuple[str | None, str], ...]:
    """(persona, prefix) for every retired label: with its stored emoji first,
    then with the persona's current emoji. Deduplicated, order preserved."""
    pairs: list[tuple[str | None, str]] = []
    for persona, entries in _LEGACY_AGENT_LABELS.items():
        for label, emoji in entries:
            for e in (emoji, _agent_emoji(persona)):
                pair = (persona, f"<p>{e}<strong>[{label}]</strong>")
                if pair not in pairs:
                    pairs.append(pair)
    return tuple(pairs)


_PREFIX_PAIRS: tuple[tuple[str | None, str], ...] = tuple(
    (p, f"<p>{agent_html_prefix(p)}") for p in _AGENT_PERSONAS
) + _legacy_prefix_pairs()

# Canonical HTML prefixes the agents' own messages start with.
# delete_message() refuses to delete anything that doesn't start with one of
# these, so it can never remove a real user message even if a stale/wrong ID is
# passed in.
BOT_HTML_PREFIXES = tuple(prefix for _, prefix in _PREFIX_PAIRS)

# Plain-text forms ("[Bob]", "[Asystent]", retired labels) — used
# where only the rendered text of a message is available (quote detection,
# skip-answered).
AGENT_TEXT_LABELS = tuple(
    dict.fromkeys(
        [f"[{agent_label(p)}]" for p in _AGENT_PERSONAS]
        + [
            f"[{label}]"
            for entries in _LEGACY_AGENT_LABELS.values()
            for label, _emoji in entries
        ]
    )
)


def agent_label_from_html(raw_html: str) -> str | None:
    """Return the agent label if raw_html is one of our own messages, else None.

    A message stamped with a retired label resolves to the persona's CURRENT
    label — it is the same agent, and the model should see one identity in the
    history, not two.
    """
    raw = raw_html.lstrip()
    for persona, prefix in _PREFIX_PAIRS:
        if raw.startswith(prefix):
            return agent_label(persona)
    return None

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-Ms-Client-Type": "web",
}


# ---------------------------------------------------------------------------
# Session builder
# ---------------------------------------------------------------------------

# Bearer token captured from Playwright browser at runtime.
_auth_token: str = ""
_auth_token_ts: float = 0.0   # epoch seconds when token was last set
# Bumped ONLY when the token VALUE actually changes -- _auth_token_ts is bumped on
# every capture, including re-captures of the identical token, so it cannot answer
# "did the refresh produce a new token?". cli.py's refresh gate reads this instead.
_auth_token_gen: int = 0
_needs_token_refresh: bool = False  # set True on 401; cleared by cli after browser nav
# Detail of the last failed send (edit_message / _post_returning_id / post_card).
# Set on every failure path, cleared to None on success. Read by cli.py to enrich
# log.jsonl records so send failures no longer vanish with only a stderr print.
# Thread-local (not a plain module global): _process_chat_messages is dispatched
# per-chat into a shared ThreadPoolExecutor, so concurrent sends for different
# chats run on different threads at the same time. A plain global would let one
# chat's thread clear/overwrite another chat's in-flight error during the
# multi-second retry backoff window, corrupting log.jsonl for the wrong chat.
_send_error_local = threading.local()


def _get_last_send_error() -> dict | None:
    """Return this thread's last recorded send error (None if none/cleared)."""
    return getattr(_send_error_local, "value", None)


def _set_last_send_error(err: dict | None) -> None:
    """Record this thread's last send error (or clear it with None on success)."""
    _send_error_local.value = err

# Persisted token cache — lets external scripts (stats.py) reuse the token without Playwright.
_TOKEN_CACHE = Path(__file__).parent / ".auth-token"


def set_auth_token(token: str) -> None:
    """Store the Bearer token intercepted from Playwright for use in REST calls.

    Clearing _needs_token_refresh here is deliberate but only sound when the value
    really changed: re-capturing the SAME token cannot fix a 401, and clearing the
    flag for it makes the next 401 look brand new.
    """
    global _auth_token, _auth_token_ts, _auth_token_gen, _needs_token_refresh
    changed = token != _auth_token
    _auth_token = token
    _auth_token_ts = __import__("time").time()
    if changed:
        _auth_token_gen += 1
        _needs_token_refresh = False
    if changed:
        print(f"[api] Bearer token zaktualizowany ({len(token)} znakow)", file=sys.stderr, flush=True)
        try:
            # Teams Bearer token -- owner-only, whatever the process umask.
            # Created 0600 from the start (no 0644 window), then swapped in atomically.
            tmp = _TOKEN_CACHE.with_name(f"{_TOKEN_CACHE.name}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(token)
            os.replace(tmp, _TOKEN_CACHE)
        except Exception:
            pass


def _load_cached_token() -> tuple[str, float]:
    """Read token from cache file. Returns (token, mtime_epoch) or ('', 0)."""
    try:
        if _TOKEN_CACHE.exists():
            mtime = _TOKEN_CACHE.stat().st_mtime
            return _TOKEN_CACHE.read_text(encoding="utf-8").strip(), mtime
    except Exception:
        pass
    return "", 0.0


# ---------------------------------------------------------------------------
# ic3-audience token -- a DIFFERENT token than the chatsvc Bearer token above.
# Required to talk to AMS (asyncgw.teams.microsoft.com / asm.skype.com) for
# Bob's own gif uploads ([BOB_GIF: mood]). Same cache shape as the chatsvc token.
# ---------------------------------------------------------------------------

_ic3_token: str = ""
_ic3_token_ts: float = 0.0   # epoch seconds when token was last set
_IC3_TOKEN_CACHE = Path(__file__).parent / ".ic3-auth-token"


def set_ic3_token(token: str) -> None:
    """Store the ic3-audience Bearer token intercepted from Playwright (AMS calls)."""
    global _ic3_token, _ic3_token_ts
    changed = token != _ic3_token
    _ic3_token = token
    _ic3_token_ts = __import__("time").time()
    if changed:
        print(f"[api] ic3 token zaktualizowany ({len(token)} znakow)", file=sys.stderr, flush=True)
        try:
            # Owner-only from the start, swapped in atomically (as .auth-token).
            tmp = _IC3_TOKEN_CACHE.with_name(f"{_IC3_TOKEN_CACHE.name}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(token)
            os.replace(tmp, _IC3_TOKEN_CACHE)
        except Exception:
            pass


def _load_cached_ic3_token() -> tuple[str, float]:
    """Read ic3 token from cache file. Returns (token, mtime_epoch) or ('', 0)."""
    try:
        if _IC3_TOKEN_CACHE.exists():
            mtime = _IC3_TOKEN_CACHE.stat().st_mtime
            return _IC3_TOKEN_CACHE.read_text(encoding="utf-8").strip(), mtime
    except Exception:
        pass
    return "", 0.0


def token_fingerprint(token: str) -> str:
    """Short stable digest of a token — safe to print, identifies the VALUE.

    len() does not work as an identity check: every token for the same user and
    audience is the same length (2262 for us), so a log line saying len=2262
    cannot distinguish "refresh produced a new token" from "refresh produced the
    same token again" -- an ambiguity that can hide a 401 loop in which the token
    never changes at all.
    """
    if not token:
        return "-"
    return _hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def _token_debug_info() -> str:
    """One-line string describing current token state — appended to error messages."""
    import time as _time
    if _auth_token:
        age = _time.time() - _auth_token_ts
        return (f"token=memory age={age:.0f}s len={len(_auth_token)} "
                f"sha={token_fingerprint(_auth_token)}")
    cached, mtime = _load_cached_token()
    if cached:
        age = _time.time() - mtime
        return (f"token=cache age={age:.0f}s len={len(cached)} "
                f"sha={token_fingerprint(cached)}")
    return "token=brak"


# A 401 is not proof the token is dead. Concurrent get_messages calls can all
# get a transient 401 while the token is still valid -- replaying the very same
# token shortly afterwards returns 200. Treating the first 401 as "token expired"
# would turn that blip into a refresh loop: each refresh re-captures an identical
# token, clears the flag, and the next 401 restarts it. So: retry a 401 in place first, and only declare the token bad
# when a retry with the SAME token also fails.
_401_RETRIES = 1          # extra attempts on 401 before blaming the token
_401_RETRY_DELAY_S = 2.0  # short -- a transient 401 clears fast; a dead token will not


def _retry_401(label: str, attempts_done: int) -> bool:
    """True if this 401 should be retried in place rather than blamed on the token."""
    if attempts_done > _401_RETRIES:
        return False
    import time as _t
    print(
        f"[api] {label} 401 — retry {attempts_done}/{_401_RETRIES} za "
        f"{_401_RETRY_DELAY_S:.0f}s (ten sam token, sprawdzam czy przejsciowe) | "
        f"{_token_debug_info()}",
        file=sys.stderr, flush=True,
    )
    _t.sleep(_401_RETRY_DELAY_S)
    return True


def _log_network_diag() -> None:
    """Log proxy settings and a quick TCP reachability check. Call once on first error."""
    import socket
    import subprocess
    import urllib.request
    proxy_vars = {k: v for k, v in __import__("os").environ.items()
                  if k.lower() in ("http_proxy", "https_proxy", "no_proxy", "all_proxy")}
    sys_proxies = _requests.utils.get_environ_proxies("https://teams.microsoft.com") or {}
    print(f"[api][diag] proxy_env={proxy_vars or 'none'}", file=sys.stderr, flush=True)
    print(f"[api][diag] sys_proxies={sys_proxies or 'none'}", file=sys.stderr, flush=True)
    try:
        urllib_proxies = urllib.request.getproxies()
        print(f"[api][diag] urllib_proxies={urllib_proxies or 'none'}", file=sys.stderr, flush=True)
    except Exception:
        pass

    # macOS system proxy via scutil (catches PAC/system-level proxies that Python misses)
    try:
        r = subprocess.run(["scutil", "--proxy"], capture_output=True, text=True, timeout=3)
        lines = [ln.strip() for ln in r.stdout.splitlines()
                 if ln.strip() and ln.strip() != "{" and ln.strip() != "}"]
        print(f"[api][diag] scutil_proxy={lines or 'none'}", file=sys.stderr, flush=True)
    except Exception:
        pass

    # /etc/hosts override check
    try:
        hosts_hits = [ln for ln in Path("/etc/hosts").read_text().splitlines()
                      if "teams" in ln.lower() and not ln.strip().startswith("#")]
        print(f"[api][diag] /etc/hosts teams entries={hosts_hits or 'none'}", file=sys.stderr, flush=True)
    except Exception:
        pass

    # DNS check first so we know which IPs to probe
    resolved_ips: list[str] = []
    try:
        addrs = socket.getaddrinfo("teams.microsoft.com", 443, proto=socket.IPPROTO_TCP)
        resolved_ips = list({a[4][0] for a in addrs})[:4]
        print(f"[api][diag] DNS teams.microsoft.com -> {resolved_ips}", file=sys.stderr, flush=True)
    except OSError as e:
        print(f"[api][diag] DNS teams.microsoft.com -> FAIL: {e}", file=sys.stderr, flush=True)

    # TCP via hostname (goes through OS resolver + any transparent proxy)
    try:
        with socket.create_connection(("teams.microsoft.com", 443), timeout=5):
            print("[api][diag] TCP teams.microsoft.com:443 -> OK", file=sys.stderr, flush=True)
    except OSError as e:
        print(f"[api][diag] TCP teams.microsoft.com:443 -> FAIL errno={e.errno} ({e})", file=sys.stderr, flush=True)

    # TCP directly to each resolved IP (bypasses DNS-based transparent proxy routing)
    for ip in resolved_ips[:2]:
        try:
            with socket.create_connection((ip, 443), timeout=5):
                print(f"[api][diag] TCP direct {ip}:443 -> OK", file=sys.stderr, flush=True)
        except OSError as e:
            print(f"[api][diag] TCP direct {ip}:443 -> FAIL errno={e.errno} ({e})", file=sys.stderr, flush=True)

    # Control: check if HTTPS generally works (not just Teams)
    try:
        with socket.create_connection(("google.com", 443), timeout=5):
            print("[api][diag] TCP google.com:443 -> OK (HTTPS dziala ogolnie)", file=sys.stderr, flush=True)
    except OSError as e:
        print(f"[api][diag] TCP google.com:443 -> FAIL errno={e.errno} ({e})", file=sys.stderr, flush=True)

    # Check if there's a local process listening on 443 (would cause ECONNREFUSED redirect)
    try:
        result = subprocess.run(
            ["lsof", "-i", "TCP:443", "-n", "-P"],
            capture_output=True, text=True, timeout=5,
        )
        listeners = [ln for ln in result.stdout.splitlines() if "LISTEN" in ln]
        if listeners:
            print(f"[api][diag] local_443_listeners={listeners}", file=sys.stderr, flush=True)
        else:
            print("[api][diag] local_443_listeners=none", file=sys.stderr, flush=True)
    except Exception:
        pass


_diag_logged_at: float = 0.0   # epoch — re-run diag after 5 min if errors persist

# Shared HTTP adapter — reuses the underlying TCP/TLS connection pool across all sessions.
# Each _session() call still creates a fresh Session (thread-safe: no shared mutable headers),
# but all sessions share the same urllib3 connection pool via this adapter.
# pool_connections=4 hosts, pool_maxsize=20 to avoid "pool full" warnings with 10 workers.
_teams_adapter = _requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=20)

# Cookie cache — avoid reading session.json on every API call.
_cached_cookies: list[dict] = []
_cookies_loaded_at: float = 0.0
_COOKIE_RELOAD_S: float = 300.0  # reload cookies from disk every 5 min


def _session() -> _requests.Session:
    """Build a per-call Session that reuses the shared TCP connection pool.

    Thread-safe: each caller gets its own Session object (no shared mutable state),
    but the underlying urllib3 pool is shared — so TLS connections are reused without
    the race conditions of a single shared Session.
    """
    global _cached_cookies, _cookies_loaded_at
    import time as _time

    # Refresh cookie cache periodically.
    now = _time.time()
    if now - _cookies_loaded_at >= _COOKIE_RELOAD_S:
        try:
            state = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            _cached_cookies = state.get("cookies", [])
            _cookies_loaded_at = now
        except Exception as exc:
            print(f"[api] Nie mozna wczytac session.json: {exc}", file=sys.stderr, flush=True)

    s = _requests.Session()
    s.mount("https://", _teams_adapter)
    s.mount("http://", _teams_adapter)
    s.headers.update(_HEADERS)
    token = _auth_token or _load_cached_token()[0]
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    for c in _cached_cookies:
        s.cookies.set(
            c["name"],
            c["value"],
            domain=c.get("domain", ".teams.microsoft.com"),
            path=c.get("path", "/"),
        )
    return s


# ---------------------------------------------------------------------------
# HTML stripping — skip quoted blockquotes so we read only the new text
# ---------------------------------------------------------------------------

class _StripHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self._parts: list[str] = []
        # Stack of (href, idx_into_parts) for currently open <a> tags so we can
        # compare the anchor's visible text against href when the tag closes.
        self._a_stack: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "blockquote":
            self._depth += 1
        elif tag == "img" and self._depth == 0:
            alt = dict(attrs).get("alt", "")
            if alt:
                self._parts.append(alt)
        elif tag == "a" and self._depth == 0:
            href = dict(attrs).get("href", "")
            self._a_stack.append((href, len(self._parts)))

    def handle_endtag(self, tag: str) -> None:
        if tag == "blockquote" and self._depth:
            self._depth -= 1
        elif tag == "a" and self._a_stack and self._depth == 0:
            href, start = self._a_stack.pop()
            if href and href.startswith(("http://", "https://")):
                anchor_text = "".join(self._parts[start:]).strip()
                # Only append href when the visible label is not already the URL
                # (otherwise Teams' bare-URL anchors would duplicate every link).
                if anchor_text != href:
                    self._parts.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if self._depth == 0:
            self._parts.append(data)

    def result(self) -> str:
        return " ".join(" ".join(self._parts).split()).strip()


def _strip_html(html: str) -> str:
    p = _StripHTML()
    p.feed(html)
    return p.result()


class _ImageURLExtractor(HTMLParser):
    """Extract <img src> URLs from Teams HTML, skipping blockquotes."""
    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "blockquote":
            self._depth += 1
        elif tag == "img" and self._depth == 0:
            src = dict(attrs).get("src", "")
            if src and src.startswith("http"):
                self.urls.append(src)

    def handle_endtag(self, tag: str) -> None:
        if tag == "blockquote" and self._depth:
            self._depth -= 1


_EMOJI_DOMAINS = ("statics.teams.cdn.office.net", "res.cdn.office.net", "res-1.cdn.office.net")

# Legit Microsoft/Teams hosts an inline <img src> can point to. _session() always
# attaches the live Teams Bearer token, so fetching an attacker-supplied URL
# (e.g. an <img src="https://evil.example/x"> planted in a message) would leak
# that token and act as an SSRF proxy. Refuse anything outside this allowlist
# instead of trusting _EMOJI_DOMAINS' exclusion list to be exhaustive.
_IMAGE_HOST_SUFFIXES = (".skype.com", ".cdn.office.net", ".teams.microsoft.com")


def _image_host_allowed(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == s.lstrip(".") or host.endswith(s) for s in _IMAGE_HOST_SUFFIXES)


def _extract_attachments(properties: dict) -> list[dict]:
    """Parse properties.files and properties.links for SharePoint file attachments.

    Schema reference: a Teams chatsvc network sniff of a Notes-to-self file upload. Shape:
        properties.files = JSON string of [{
            fileName, fileType,
            fileInfo: {fileUrl, siteUrl, ...},
            objectUrl, title, sharepointIds: {listItemUniqueId, ...},
            ...
        }, ...]
    fileUrl is a direct SharePoint deep link; downloading it requires FedAuth
    cookies in the *.sharepoint.com cookie jar (see attachments.py).

    properties.links covers SharePoint-shared files pasted as hyperlinks (e.g.
    Teams Recording links). Shape: JSON string of [{
        "@type": "http://schema.skype.com/HyperLink",
        "title": "filename.mp4",
        "fileShareUrlMeta": {"objectUrl": "https://...sharepoint.com/...", "shareUrl": "..."},
    }, ...]
    """
    _MD_EXTS: set[str] = {"md"}
    _MEDIA_EXTS: set[str] = {"mp4", "mp3", "m4a", "wav"}
    _IMAGE_EXTS: set[str] = {"png", "jpg", "jpeg", "gif", "webp"}
    _ALLOWED_EXTS = _MD_EXTS | _MEDIA_EXTS | _IMAGE_EXTS

    out: list[dict] = []

    # --- properties.files (standard file upload) ---
    raw = (properties or {}).get("files")
    if raw:
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            items = []
        for f in items or []:
            if not isinstance(f, dict):
                continue
            info = f.get("fileInfo") or {}
            url = info.get("fileUrl") or f.get("objectUrl") or ""
            if not url:
                continue
            name = f.get("fileName") or f.get("title") or "file"
            ftype = (f.get("fileType") or "").lower()
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ftype not in _ALLOWED_EXTS and ext not in _ALLOWED_EXTS:
                continue
            out.append({"name": name, "url": url, "type": ftype or ext})

    # --- properties.links (SharePoint shared links, Teams Recording, etc.) ---
    raw_links = (properties or {}).get("links")
    if raw_links:
        try:
            links = json.loads(raw_links) if isinstance(raw_links, str) else raw_links
        except (json.JSONDecodeError, TypeError):
            links = []
        seen_names = {a["name"] for a in out}
        for lnk in links or []:
            if not isinstance(lnk, dict):
                continue
            title = lnk.get("title") or ""
            ext = title.rsplit(".", 1)[-1].lower() if "." in title else ""
            if ext not in _ALLOWED_EXTS:
                continue
            if title in seen_names:
                continue
            fsmeta = lnk.get("fileShareUrlMeta") or {}
            url = fsmeta.get("objectUrl") or fsmeta.get("shareUrl") or ""
            if not url:
                continue
            out.append({"name": title, "url": url, "type": ext})
            seen_names.add(title)

    return out


def _extract_image_urls(html: str) -> list[str]:
    """Extract real image URLs from Teams message HTML (skips emoji/sticker domains)."""
    if not html or "<img" not in html:
        return []
    p = _ImageURLExtractor()
    p.feed(html)
    return [u for u in p.urls if not any(d in u for d in _EMOJI_DOMAINS)]


def download_image(url: str) -> tuple[bytes, str] | None:
    """Download image from Teams CDN using Bearer token. Returns (bytes, media_type) or None."""
    if not _image_host_allowed(url):
        print(f"[api] download_image odrzucony (host spoza allowlisty): {url!r}",
              file=sys.stderr, flush=True)
        return None
    try:
        r = _session().get(url, timeout=15, allow_redirects=False)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if not ct.startswith("image/"):
            ct = "image/jpeg"
        if len(r.content) < 2000:  # skip tiny icons/emoji
            return None
        return r.content, ct
    except Exception as exc:
        print(f"[api] download_image blad: {exc}", file=sys.stderr, flush=True)
        return None


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_mention_chip(
    display_name: str, mri: str, start_itemid: int = 0
) -> tuple[str, list[dict], int]:
    """Build the HTML chip markup + mention entries for ONE person.

    display_name/mri: same as one entry of build_mentions_html()'s `people` list.
    start_itemid: itemid to start numbering this person's word-spans from — lets
    callers compose several build_mention_chip() calls into a single message
    (e.g. one @mention per ticket line) with globally unique itemid values.
    Reusing itemid across people in the same message corrupts the Teams
    properties["mentions"] payload.

    Teams' chatsvc splits a multi-word display name into one mention <span> per word,
    each with its own sequential itemid but all sharing the person's mri — observed via
    network sniff of a real @-mention send. Returns (html, entries,
    next_itemid) where html is the chip markup to splice into <p> content, entries is
    the list to JSON-encode into properties["mentions"], and next_itemid is the value
    the caller should pass as start_itemid for its next build_mention_chip() call.
    """
    spans: list[str] = []
    entries: list[dict] = []
    itemid = start_itemid
    for word in display_name.split():
        spans.append(
            '<readonly class="skipProofing" itemtype="http://schema.skype.com/Mention" '
            'spellcheck="false" contenteditable="false">'
            f'<span itemtype="http://schema.skype.com/Mention" itemscope itemid="{itemid}">'
            f'{_escape(word)}</span></readonly>'
        )
        entries.append({
            "@type": "http://schema.skype.com/Mention",
            "itemid": str(itemid),
            "mri": mri,
            "mentionType": "person",
            "displayName": word,
        })
        itemid += 1
    return "&nbsp;".join(spans), entries, itemid


def build_mentions_html(people: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """Build the HTML chip markup + 'mentions' entries for a real Teams @mention ping.

    people: list of (display_name, mri), e.g. [("EXT Ewa Nowak", "8:orgid:...")].

    Thin wrapper around build_mention_chip() that composes multiple people into one
    message with non-colliding itemid. Returns (html, entries) where html is the chip
    markup to splice into <p> content and entries is the list to JSON-encode into
    properties["mentions"].
    """
    spans: list[str] = []
    entries: list[dict] = []
    itemid = 0
    for display_name, mri in people:
        chip_html, chip_entries, itemid = build_mention_chip(display_name, mri, itemid)
        spans.append(chip_html)
        entries.extend(chip_entries)
    return "&nbsp;".join(spans), entries


# ---------------------------------------------------------------------------
# Read messages
# ---------------------------------------------------------------------------

def _parse_card_action(m: dict) -> dict | None:
    """Extract Adaptive Card Action.Submit payload from a Teams message dict.

    Teams chatsvc returns card vote-responses as messages whose content contains
    a JSON payload in m["properties"]["cardAction"] or m["properties"]["cards"].
    The payload from Action.Submit carries a "data" field with the poll values.

    Schema reference: Adaptive Card 1.5 public spec (adaptivecards.io) combined
    with Teams chatsvc network sniff patterns (api-sniff.jsonl). The observed
    shape for Action.Submit responses is:
        messagetype: "RichText/Media_Card" or omitted
        properties.cardAction: {"type": "Action.Submit", "data": {...}}
    Multi-select ChoiceSet returns values as a comma-separated string in data.

    If no card action is detected, returns None.
    """
    props = m.get("properties") or {}
    card_action_raw = props.get("cardAction")
    if card_action_raw:
        try:
            if isinstance(card_action_raw, str):
                card_action_raw = json.loads(card_action_raw)
            if isinstance(card_action_raw, dict) and card_action_raw.get("type") == "Action.Submit":
                data = card_action_raw.get("data") or {}
                from_url = m.get("from", "")
                sender_mri = from_url.split("/contacts/")[-1] if "/contacts/" in from_url else ""
                return {**data, "sender_mri": sender_mri}
        except Exception:
            pass
    return None


def get_messages(conversation_id: str = NOTES_CONV_ID, n: int = 20) -> list[dict]:
    """Fetch the last n messages from a conversation via REST.

    Each dict has keys:
        id          — Teams message ID (string, use as cursor)
        sender      — display name (e.g. "Ewa Nowak")
        sender_mri  — MRI like "8:orgid:<guid>" (use to detect own messages)
        text        — plain text (HTML stripped, blockquotes removed)
        raw_html    — original HTML content from the API
        image_urls  — list of image URLs in the message (empty if none)
        attachments — list of {name, url, type} for SharePoint file attachments
        timestamp   — ISO composetime string
        sequence_id — int, monotonically increasing; sort key
        card_action — dict | None: Adaptive Card Action.Submit payload if present
    """
    global _needs_token_refresh
    if _needs_token_refresh:
        return []
    url = f"{_BASE}/users/ME/conversations/{urllib.parse.quote(conversation_id, safe='')}/messages"
    # Teams chatsvc returns HTTP 400 for pageSize > ~200 on some 1:1 chats, so cap defensively.
    # n + 10 covers control / system events between polls (5 s); was max(3n, 50) = 60 per chat per tick
    params = {"view": "msnp24Equivalent", "pageSize": min(max(n + 10, 30), 200)}
    import time as _time
    _last_exc: Exception | None = None
    for _attempt in range(3):
        try:
            r = _session().get(url, params=params, timeout=10)
            r.raise_for_status()
            _last_exc = None
            break
        except Exception as exc:
            _last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 401:
                if _retry_401("get_messages", _attempt + 1):
                    continue
                _needs_token_refresh = True
                dbg = _token_debug_info()
                print(f"[api] get_messages 401 — token wymaga odswiezenia | {dbg}", file=sys.stderr, flush=True)
                return []
            if status is not None and 500 <= status < 600 and _attempt < 2:
                _delay = 3 * (2 ** _attempt)
                print(f"[api] get_messages {status} — retry {_attempt + 1}/2 za {_delay}s", file=sys.stderr, flush=True)
                _time.sleep(_delay)
                continue
            break
    if _last_exc is not None:
        status = getattr(getattr(_last_exc, "response", None), "status_code", None)
        err_type = type(_last_exc).__name__
        dbg = _token_debug_info()
        print(
            f"[api] get_messages blad [{err_type} status={status}] | {dbg} | {_last_exc}",
            file=sys.stderr, flush=True,
        )
        global _diag_logged_at
        if status is None and _time.time() - _diag_logged_at > 300:
            _diag_logged_at = _time.time()
            _log_network_diag()
        return []

    messages = r.json().get("messages", [])
    result: list[dict] = []
    for m in messages:
        if m.get("type") != "Message":
            continue
        mtype = m.get("messagetype", "")
        if "Control" in mtype or "typing" in mtype.lower():
            continue
        content = m.get("content", "")
        text = _strip_html(content) if "<" in content else content.replace("\xa0", " ").strip()
        card_action = _parse_card_action(m)
        attachments = _extract_attachments(m.get("properties") or {})
        image_urls = _extract_image_urls(content)
        # Pass-through messages with card_action, attachments, or inline images even when
        # text is empty — a Notes file upload has empty content; an image-only message has
        # no text after HTML stripping but does have image_urls.
        if not text and not card_action and not attachments and not image_urls:
            continue
        from_url = m.get("from", "")
        sender_mri = from_url.split("/contacts/")[-1] if "/contacts/" in from_url else ""
        result.append({
            "id": m.get("id", ""),
            "sender": m.get("fromDisplayNameInToken") or m.get("imdisplayname", ""),
            "sender_mri": sender_mri,
            "text": text,
            "raw_html": content,
            "image_urls": image_urls,
            "attachments": attachments,
            "timestamp": m.get("composetime", ""),
            "sequence_id": int(m.get("sequenceId") or 0),
            "card_action": card_action,
        })

    result.sort(key=lambda m: m["sequence_id"])
    return result[-n:]


# ---------------------------------------------------------------------------
# Send message
# ---------------------------------------------------------------------------

_JIRA_URL = org_config.JIRA_URL
_JIRA_KEY_RE = re.compile(r'\b([A-Z][A-Z0-9]+-\d+)\b')


def _md_to_html(text: str) -> str:
    """Convert markdown subset to Teams-compatible HTML.

    Escapes HTML special chars first, then converts [text](url) and bare URLs
    into clickable <a> tags, Jira ticket IDs (e.g. PRJ1-123) into browse links,
    **bold**, *italic*, _italic_, and newlines.
    Links are stashed as placeholders before italic/bold runs so URLs that
    contain `_` (e.g. Teams URLs) aren't mangled by the italic regex.
    Code fences (``` ... ```) are stashed before HTML escaping and emitted as
    <pre> blocks — Teams renders <pre> as monospace preformatted text.
    """
    # Stash code fences BEFORE HTML escaping so their delimiters don't get mangled.
    # Match optional language tag on the opening line (```python, ```text, etc.).
    blocks: list[str] = []

    def _stash_block(m: re.Match[str]) -> str:
        content = m.group(1)
        # HTML-escape only the content inside the block.
        safe_c = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        blocks.append(f"<pre>{safe_c}</pre>")
        return f"\x00C{len(blocks) - 1}\x00"

    text = re.sub(r"```(?:[^\n`]*\n)?(.*?)```", _stash_block, text, flags=re.DOTALL)

    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    links: list[str] = []

    def _stash(anchor: str) -> str:
        links.append(anchor)
        return f"\x00L{len(links) - 1}\x00"

    safe = re.sub(
        # no quotes in the URL: it lands inside href="..." (a quote would open a new attribute)
        r"\[([^\]]+)\]\((https?://[^\s)\"']+)\)",
        lambda m: _stash(f'<a href="{m.group(2)}">{m.group(1)}</a>'),
        safe,
    )

    def _autolink(m: re.Match[str]) -> str:
        url = m.group(0)
        trail = ""
        while url and url[-1] in ".,;:!?":
            trail = url[-1] + trail
            url = url[:-1]
        return _stash(f'<a href="{url}">{url}</a>') + trail

    safe = re.sub(r'https?://[^\s<>")\']+', _autolink, safe)

    safe = _JIRA_KEY_RE.sub(
        lambda m: _stash(f'<a href="{_JIRA_URL}/browse/{m.group(1)}">{m.group(1)}</a>'),
        safe,
    )

    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe, flags=re.DOTALL)
    safe = re.sub(r"\*(.+?)\*", r"<em>\1</em>", safe, flags=re.DOTALL)
    safe = re.sub(r"_(.+?)_", r"<em>\1</em>", safe, flags=re.DOTALL)
    safe = safe.replace("\n", "<br>")

    for i, anchor in enumerate(links):
        safe = safe.replace(f"\x00L{i}\x00", anchor)
    for i, block in enumerate(blocks):
        safe = safe.replace(f"\x00C{i}\x00", block)
    return safe


def md_to_html(text: str) -> str:
    """Public alias for _md_to_html — convert markdown to Teams-compatible HTML."""
    return _md_to_html(text)


def bob_html(text: str) -> str:
    """Bob's message body as Teams HTML: the [Bob] prefix + markdown converted."""
    return f"<p>🧑‍💻<strong>[Bob]</strong>: {_md_to_html(text)}</p>"


def send_message(text: str, conversation_id: str = NOTES_CONV_ID) -> bool:
    """Post a message to the conversation, converting markdown to Teams HTML."""
    return _post(conversation_id, bob_html(text))


def send_html_message(
    html: str,
    conversation_id: str = NOTES_CONV_ID,
    mentions: list[dict] | None = None,
    amsreferences: list[str] | None = None,
) -> str | None:
    """Post an HTML message. Returns the Teams message ID on success, None on failure."""
    return _post_returning_id(conversation_id, html, mentions, amsreferences)


def edit_message(
    msg_id: str,
    html: str,
    conversation_id: str = NOTES_CONV_ID,
    mentions: list[dict] | None = None,
) -> bool:
    """Edit an existing message in-place via PUT.

    Uses the same chatsvc endpoint as send — only the HTTP method and path differ.
    Returns True on success.

    Retry loop follows the same backoff schedule as get_messages (3 attempts,
    3 * 2**attempt seconds), but additionally retries bare network errors
    (status is None), not just 5xx. A 401 is never retried here — it sets
    _needs_token_refresh and returns immediately.
    """
    global _needs_token_refresh
    if _refuse_write(conversation_id, "edit"):
        return False
    import time as _time
    url = (
        f"{_BASE}/users/ME/conversations/"
        f"{urllib.parse.quote(conversation_id, safe='')}/messages/{msg_id}"
    )
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    properties: dict = {"importance": ""}
    if mentions:
        properties["mentions"] = json.dumps(mentions, ensure_ascii=False)
    body = {
        "id": msg_id,
        "type": "Message",
        "conversationid": conversation_id,
        "conversationLink": (
            f"https://teams.microsoft.com/api/chatsvc/emea/v1/users/ME/conversations/"
            f"{conversation_id}"
        ),
        "from": USER_MRI,
        "fromUserId": USER_MRI,
        "composetime": now,
        "originalarrivaltime": now,
        "content": html,
        "messagetype": "RichText/Html",
        "contenttype": "Text",
        "imdisplayname": USER_DISPLAY,
        "clientmessageid": str(random.randint(10**18, 10**19 - 1)),  # noqa: S311
        "callId": "",
        "state": 0,
        "version": "0",
        "amsreferences": [],
        "properties": properties,
    }
    _last_exc: Exception | None = None
    for _attempt in range(3):
        try:
            r = _session().put(url, json=body, timeout=10)
            r.raise_for_status()
            print(f"[api] edit_message ({r.status_code})", file=sys.stderr, flush=True)
            _set_last_send_error(None)
            return True
        except Exception as exc:
            _last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            err_type = type(exc).__name__
            if status == 401:
                if _retry_401("edit_message", _attempt + 1):
                    continue
                _needs_token_refresh = True
                _set_last_send_error({
                    "status": 401, "err_type": err_type,
                    "message": str(exc), "attempts": _attempt + 1,
                })
                print("[api] edit_message 401 — token wymaga odświeżenia", file=sys.stderr, flush=True)
                return False
            if (status is None or 500 <= status < 600) and _attempt < 2:
                _delay = 3 * (2 ** _attempt)
                print(
                    f"[api] edit_message blad [{err_type} status={status}] — retry {_attempt + 1}/2 za {_delay}s",
                    file=sys.stderr, flush=True,
                )
                _time.sleep(_delay)
                continue
            print(f"[api] edit_message błąd: {exc}", file=sys.stderr, flush=True)
            break
    if _last_exc is not None:
        status = getattr(getattr(_last_exc, "response", None), "status_code", None)
        err_type = type(_last_exc).__name__
        _set_last_send_error({
            "status": status, "err_type": err_type,
            "message": str(_last_exc), "attempts": _attempt + 1,
        })
        return False
    return False


def _is_bots_own_message(msg_id: str, conversation_id: str) -> bool:
    """True only if msg_id's current content starts with a BOT_HTML_PREFIXES prefix.

    Fails closed: if the message can't be found in the recent window (or fetch
    fails), returns False so the caller refuses to delete.
    """
    try:
        recent = get_messages(conversation_id, n=50)
    except Exception:
        return False
    for m in recent:
        if str(m.get("id", "")) == str(msg_id):
            raw = m.get("raw_html", "").lstrip()
            return any(raw.startswith(p) for p in BOT_HTML_PREFIXES)
    return False


def delete_message(msg_id: str, conversation_id: str = NOTES_CONV_ID) -> bool:
    """Soft-delete a message via DELETE on the chatsvc endpoint.

    Safety check: re-fetches the message first and only proceeds if its
    content starts with one of BOT_HTML_PREFIXES (i.e. it's actually one of
    Bob's own placeholder/reply messages). This guards against ever deleting
    a real user message — e.g. if a stale or wrong ID reached this call.
    If the message can't be found or doesn't match, the delete is refused.

    Returns True on success (2xx). Falls back gracefully on error.
    """
    global _needs_token_refresh
    if _refuse_write(conversation_id, "delete"):
        return False
    if not _is_bots_own_message(msg_id, conversation_id):
        print(
            f"[api] delete_message ODMOWA — {msg_id} nie wyglada na wiadomosc Boba, "
            "pomijam usuwanie",
            file=sys.stderr, flush=True,
        )
        return False
    url = (
        f"{_BASE}/users/ME/conversations/"
        f"{urllib.parse.quote(conversation_id, safe='')}/messages/{msg_id}"
    )
    try:
        r = _session().delete(url, timeout=10)
        r.raise_for_status()
        print(f"[api] delete_message ({r.status_code})", file=sys.stderr, flush=True)
        return True
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 401:
            _needs_token_refresh = True
            print("[api] delete_message 401 — token wymaga odświeżenia", file=sys.stderr, flush=True)
        else:
            print(f"[api] delete_message błąd: {exc}", file=sys.stderr, flush=True)
        return False


def reply_to_message(
    text: str,
    reply_to_id: str,
    reply_to_sender: str,
    reply_to_text: str,
    conversation_id: str = NOTES_CONV_ID,
    reply_to_sender_mri: str = "",
    mentions: list[dict] | None = None,
    mentions_html: str = "",
    persona: str | None = None,
) -> bool:
    """Post a quote-reply using Teams' native blockquote format.

    The blockquote schema (http://schema.skype.com/Reply) is what Teams renders
    as a quoted message bubble — found in a network sniff.

    mentions/mentions_html: optional real @mention chip(s) to splice in front of the
    reply text — see build_mentions_html(). mentions_html is inserted as-is; mentions
    is the matching properties["mentions"] entries list.

    persona: picks the agent prefix via agent_html_prefix(); unknown/None -> Bob's.
    """
    preview = _escape(reply_to_text[:200])
    blockquote = (
        f'<blockquote itemscope itemtype="http://schema.skype.com/Reply"'
        f' itemid="{reply_to_id}">'
        f'<strong itemprop="mri" itemid="{reply_to_sender_mri}">'
        f"{reply_to_sender}</strong>"
        f'<span itemprop="time" itemid="{reply_to_id}"></span>'
        f"<p><em>{preview}</em></p>"
        f"</blockquote>"
    )
    html_text = _md_to_html(text)
    prefix = f"{mentions_html}&nbsp;" if mentions_html else ""
    agent_prefix = agent_html_prefix(persona)
    return _post(
        conversation_id,
        blockquote + f'<p>{agent_prefix}: {prefix}{html_text}</p>',
        mentions,
    )


def add_reaction(msg_id: str, conversation_id: str, reaction_type: str = "like") -> bool:
    """Add an emoji reaction (e.g. 'like' = 👍) to a Teams message as the owner's account.

    PUT .../messages/{id}/properties?name=emotions with the emotion as a JSON string —
    the call Teams web makes: a 2xx and the message's properties.emotions then lists the reaction. Returns True on 2xx.
    """
    global _needs_token_refresh
    if _refuse_write(conversation_id, "reaction"):
        return False
    import time as _time
    url = (
        f"{_BASE}/users/ME/conversations/"
        f"{urllib.parse.quote(conversation_id, safe='')}/messages/{msg_id}/properties"
    )
    body = {"emotions": json.dumps({"key": reaction_type, "value": int(_time.time() * 1000)})}
    try:
        r = _session().put(url, params={"name": "emotions"}, json=body, timeout=10)
        r.raise_for_status()
        print(f"[api] add_reaction {reaction_type} ({r.status_code})", file=sys.stderr, flush=True)
        return True
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 401:
            _needs_token_refresh = True
            print("[api] add_reaction 401 — token wymaga odświeżenia", file=sys.stderr, flush=True)
        else:
            print(f"[api] add_reaction błąd: {exc}", file=sys.stderr, flush=True)
        return False


# ---------------------------------------------------------------------------
# GIF via Giphy
# ---------------------------------------------------------------------------

def search_gif(query: str) -> str | None:
    """Search Giphy for a GIF matching query. Returns direct media URL or None.

    Requires GIPHY_API_KEY env var. Uses /v1/gifs/search, picks a random result
    from the top 10 so Bob doesn't always send the same GIF.
    """
    api_key = os.environ.get("GIPHY_API_KEY", "")
    if not api_key:
        print("[api] GIPHY_API_KEY nie ustawiony — pomijam gif", file=sys.stderr, flush=True)
        return None
    try:
        r = _requests.get(
            "https://api.giphy.com/v1/gifs/search",
            params={
                "api_key": api_key,
                "q": re.sub(r'[^\w\s]', '', query)[:50],
                "limit": 10,
                "rating": "pg-13",
                "lang": "en",
            },
            timeout=5,
        )
        r.raise_for_status()
        items = r.json().get("data", [])
        if not items:
            return None
        item = random.choice(items)  # noqa: S311
        return item["images"]["original"]["url"]
    except Exception as exc:
        print(f"[api] search_gif blad: {exc}", file=sys.stderr, flush=True)
        return None


def send_gif(query: str, conversation_id: str = NOTES_CONV_ID, persona: str | None = None) -> bool:
    """Send a Giphy GIF matching query as an inline image to the conversation.

    persona: picks the agent prefix via agent_html_prefix(); unknown/None -> Bob's.
    The prefix always matches BOT_HTML_PREFIXES so delete_message()/is_bot-detection
    recognize this as a bot-authored message like every other agent post.
    """
    url = search_gif(query)
    if not url:
        return False
    safe_query = _escape(query)
    safe_url = _escape(url)
    agent_prefix = agent_html_prefix(persona)
    html = (
        f'<p>{agent_prefix}: wysłał GIF-a:</p>'
        f'<p><img src="{safe_url}" alt="{safe_query}" width="320"/></p>'
    )
    return _post(conversation_id, html)


def send_message_with_gif(text: str, gif_query: str, conversation_id: str = NOTES_CONV_ID) -> bool:
    """Send text and a Giphy GIF in a single HTML message."""
    url = search_gif(gif_query)
    bob_prefix = "🧑‍💻<strong>[Bob]</strong>: "
    safe_text = _escape(text)
    if url:
        safe_url = _escape(url)
        safe_query = _escape(gif_query)
        html = (f'<p>{bob_prefix}{safe_text}</p>'
                f'<p><img src="{safe_url}" alt="{safe_query}" width="320"/></p>')
    else:
        html = f'<p>{bob_prefix}{safe_text}</p>'
    return _post(conversation_id, html) is not None


def _build_post_body(
    conversation_id: str,
    content: str,
    mentions: list[dict] | None = None,
    amsreferences: list[str] | None = None,
) -> dict:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    properties: dict = {"importance": ""}
    if mentions:
        properties["mentions"] = json.dumps(mentions, ensure_ascii=False)
    return {
        "id": "-1",
        "type": "Message",
        "conversationid": conversation_id,
        "conversationLink": f"https://teams.microsoft.com/api/chatsvc/emea/v1/users/ME/conversations/{conversation_id}",
        "from": USER_MRI,
        "fromUserId": USER_MRI,
        "composetime": now,
        "originalarrivaltime": now,
        "content": content,
        "messagetype": "RichText/Html",
        "contenttype": "Text",
        "imdisplayname": USER_DISPLAY,
        "clientmessageid": str(random.randint(10**18, 10**19 - 1)),  # noqa: S311
        "callId": "",
        "state": 0,
        "version": "0",
        "amsreferences": amsreferences if amsreferences is not None else [],
        "properties": properties,
    }


# Conversations Bob only listens to (chat-config "ingest_only"). Refreshed by cli.py at
# startup and after every chat-config hot reload. Every write entry point refuses these.
RECEIVE_ONLY_CONV_IDS: set[str] | frozenset[str] = set()


def _seed_receive_only(path: Path | None = None) -> None:
    """Best-effort import-time seed from chat-config.json so every process that imports this module
    (broadcast_runner, notifiers, manual CLI) refuses writes to ingest_only chats. Never raises."""
    try:
        cfg_path = path or Path(__file__).resolve().parents[2] / "chat-config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        chats = data.get("chats", data) if isinstance(data, dict) else {}  # real file: {"_comment", "chats"}
        for cfg in chats.values():
            if isinstance(cfg, dict) and cfg.get("ingest_only") and isinstance(cfg.get("conv_id"), str):
                RECEIVE_ONLY_CONV_IDS.add(cfg["conv_id"])
    except Exception:  # noqa: BLE001 -- best effort, cli refreshes the set anyway
        pass


_seed_receive_only()


def _refuse_write(conv_id: str, op: str) -> bool:
    """True (and logs) when conv_id is receive-only: the caller must fail without sending."""
    if conv_id in RECEIVE_ONLY_CONV_IDS:
        print(f"[api] ODMOWA {op}: receive-only conv", file=sys.stderr, flush=True)
        return True
    return False


def send_ams_image_message(
    caption_html: str,
    image_file: Path,
    conversation_id: str,
    allowed_conv_id: str | None = None,
    after_html: str = "",
    mentions: list[dict] | None = None,
) -> str | None:
    """Upload a static image (PNG) to AMS and post caption_html + the inline <img> (+ after_html).

    mentions: properties["mentions"] entries for @mention chips already in caption_html /
    after_html (see build_mentions_html()).

    Reuses the gif upload flow (same object-create + content PUT, ic3 token). When
    allowed_conv_id is given the send is refused unless conversation_id equals it
    (callers that must never post anywhere else). The
    _post_returning_id receive-only guard still applies. Returns the message id or None.
    """
    if allowed_conv_id is not None and conversation_id != allowed_conv_id:
        print("[api] ODMOWA ams_image: inna konwersacja niz dozwolona", file=sys.stderr, flush=True)
        return None
    if _refuse_write(conversation_id, "ams_image"):
        return None
    uploaded = _ams_upload_gif(image_file, conversation_id)  # generic: filename + bytes
    if uploaded is None:
        return None
    object_id, byte_count = uploaded
    img_url = f"{_AMS_VIEW_BASE}/v1/objects/{object_id}/views/imgo"
    img_tag = (
        f'<img src="{img_url}" itemtype="http://schema.skype.com/AMSImage" '
        f'data-inline-image="true" data-file-size="{byte_count}" '
        f'alt="{_escape(image_file.stem)}" id="{object_id}" itemid="{object_id}"/>'
    )
    return _post_returning_id(
        conversation_id, f"{caption_html}<p>{img_tag}</p>{after_html}", mentions, amsreferences=[object_id])


def fetch_raw_messages_since(conversation_id: str, after_seq: int, max_pages: int = 20) -> list[dict]:
    """Raw chatsvc `Message` items with sequenceId > after_seq, ascending by sequenceId.

    Paginates via _metadata.backwardLink (newest first) and stops once a page's lowest
    sequenceId <= after_seq. Returns [] without side effects on 401 / pending token
    refresh / any error, so the caller never moves its cursor on a failed fetch.
    """
    global _needs_token_refresh
    if _needs_token_refresh:
        return []
    url: str | None = f"{_BASE}/users/ME/conversations/{urllib.parse.quote(conversation_id, safe='')}/messages"
    params: dict | None = {"view": "msnp24Equivalent", "pageSize": 200}
    found: dict[int, dict] = {}
    for _page in range(max_pages):
        if not url:
            break
        try:
            r = _session().get(url, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 401:
                _needs_token_refresh = True
            print(f"[api] fetch_raw_messages_since blad (status={status}): {exc}", file=sys.stderr, flush=True)
            return []
        msgs = data.get("messages", [])
        seqs = []
        for m in msgs:
            try:
                seq = int(m.get("sequenceId"))
            except (TypeError, ValueError):
                continue
            seqs.append(seq)
            if seq > after_seq and m.get("type") == "Message":
                found[seq] = m
        url = (data.get("_metadata") or {}).get("backwardLink")
        if url and not str(url).startswith(_BASE + "/"):
            print("[api] fetch_raw_messages_since: backwardLink poza Teams, nie podazam", file=sys.stderr, flush=True)
            url = None                                   # the bearer session goes to the Teams API only
        params = None  # backwardLink already carries the query
        if not seqs or min(seqs) <= after_seq:
            break
    return [found[k] for k in sorted(found)]


def _post(conversation_id: str, content: str, mentions: list[dict] | None = None) -> bool:
    return _post_returning_id(conversation_id, content, mentions) is not None


def _post_returning_id(
    conversation_id: str,
    content: str,
    mentions: list[dict] | None = None,
    amsreferences: list[str] | None = None,
) -> str | None:
    """POST a message; return the Teams-assigned message ID or None on failure.

    Retry loop follows the same backoff schedule as get_messages (3 attempts,
    3 * 2**attempt seconds), but additionally retries bare network errors
    (status is None), not just 5xx. A 401 is never retried here (api_client
    has no Playwright access to refresh the token) — it sets
    _needs_token_refresh and returns immediately so the caller (cli.py) can
    decide whether/how to refresh and retry.

    amsreferences: optional list of AMS object ids — default keeps the
    historical behavior of always sending an empty list.
    """
    global _needs_token_refresh
    if _refuse_write(conversation_id, "post"):
        return None
    import time as _time
    url = f"{_BASE}/users/ME/conversations/{urllib.parse.quote(conversation_id, safe='')}/messages"
    body = _build_post_body(conversation_id, content, mentions, amsreferences)
    _last_exc: Exception | None = None
    for _attempt in range(3):
        try:
            r = _session().post(url, json=body, timeout=10)
            r.raise_for_status()
            print(f"[api] wyslano ({r.status_code})", file=sys.stderr, flush=True)
            # Teams returns the assigned message ID in the response body.
            try:
                msg_id = r.json().get("id") or r.json().get("OriginalArrivalTime")
            except Exception:
                msg_id = None
            # Fallback: parse from Location header if present.
            if not msg_id:
                loc = r.headers.get("Location", "")
                msg_id = loc.rstrip("/").split("/")[-1] if loc else None
            _set_last_send_error(None)
            return str(msg_id) if msg_id else "unknown"
        except Exception as exc:
            _last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            err_type = type(exc).__name__
            dbg = _token_debug_info()
            if status == 401:
                if _retry_401("_post", _attempt + 1):
                    continue
                _needs_token_refresh = True
                _set_last_send_error({
                    "status": 401, "err_type": err_type,
                    "message": str(exc), "attempts": _attempt + 1,
                })
                print(f"[api] _post 401 — token wymaga odswiezenia | {dbg}", file=sys.stderr, flush=True)
                return None
            if (status is None or 500 <= status < 600) and _attempt < 2:
                _delay = 3 * (2 ** _attempt)
                print(
                    f"[api] _post blad [{err_type} status={status}] — retry {_attempt + 1}/2 za {_delay}s",
                    file=sys.stderr, flush=True,
                )
                _time.sleep(_delay)
                continue
            print(f"[api] _post blad [{err_type} status={status}] | {dbg} | {exc}", file=sys.stderr, flush=True)
            break
    if _last_exc is not None:
        status = getattr(getattr(_last_exc, "response", None), "status_code", None)
        err_type = type(_last_exc).__name__
        _set_last_send_error({
            "status": status, "err_type": err_type,
            "message": str(_last_exc), "attempts": _attempt + 1,
        })
        return None
    return None


def post_card(card_json: dict, conversation_id: str = NOTES_CONV_ID) -> str | None:
    """Post an Adaptive Card as a Teams message attachment. Returns message ID or None.

    Schema reference: Adaptive Card 1.5 (adaptivecards.io).
    Teams chatsvc envelope based on public Teams developer docs and sniff patterns:
      messagetype: "RichText/Media_Card"
      content: JSON-serialized card payload
      contenttype: "Application/Json"
      properties.cards: JSON array of card attachment objects

    The card_json argument is a full Adaptive Card payload dict (type, version, body).
    Falls back gracefully — returns None on any HTTP or serialization error.

    Retry loop follows the same backoff schedule as get_messages (3 attempts,
    3 * 2**attempt seconds), for parity with _post_returning_id/edit_message,
    but additionally retries bare network errors (status is None), not just
    5xx. A 401 is never retried here — it sets _needs_token_refresh and
    returns immediately.
    """
    global _needs_token_refresh
    if _refuse_write(conversation_id, "card"):
        return None
    import time as _time
    url = f"{_BASE}/users/ME/conversations/{urllib.parse.quote(conversation_id, safe='')}/messages"
    now = (
        datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    )
    card_str = json.dumps(card_json, ensure_ascii=False)
    # Teams chatsvc Adaptive Card envelope — messagetype and properties.cards observed
    # in Teams client network traffic for Bot Framework adaptive card messages.
    body = {
        "id": "-1",
        "type": "Message",
        "conversationid": conversation_id,
        "conversationLink": (
            f"https://teams.microsoft.com/api/chatsvc/emea/v1/users/ME/conversations/"
            f"{conversation_id}"
        ),
        "from": USER_MRI,
        "fromUserId": USER_MRI,
        "composetime": now,
        "originalarrivaltime": now,
        "content": card_str,
        "messagetype": "RichText/Media_Card",
        "contenttype": "Application/Json",
        "imdisplayname": USER_DISPLAY,
        "clientmessageid": str(random.randint(10**18, 10**19 - 1)),  # noqa: S311
        "callId": "",
        "state": 0,
        "version": "0",
        "amsreferences": [],
        "properties": {
            "importance": "",
            "cards": json.dumps([{"contentType": "application/vnd.microsoft.card.adaptive",
                                   "content": card_json}], ensure_ascii=False),
        },
    }
    _last_exc: Exception | None = None
    for _attempt in range(3):
        try:
            r = _session().post(url, json=body, timeout=10)
            r.raise_for_status()
            print(f"[api] post_card ({r.status_code})", file=sys.stderr, flush=True)
            try:
                msg_id = r.json().get("id") or r.json().get("OriginalArrivalTime")
            except Exception:
                msg_id = None
            if not msg_id:
                loc = r.headers.get("Location", "")
                msg_id = loc.rstrip("/").split("/")[-1] if loc else None
            _set_last_send_error(None)
            return str(msg_id) if msg_id else "unknown"
        except Exception as exc:
            _last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            err_type = type(exc).__name__
            if status == 401:
                if _retry_401("post_card", _attempt + 1):
                    continue
                _needs_token_refresh = True
                _set_last_send_error({
                    "status": 401, "err_type": err_type,
                    "message": str(exc), "attempts": _attempt + 1,
                })
                print("[api] post_card 401 — token wymaga odswiezenia", file=sys.stderr, flush=True)
                return None
            if (status is None or 500 <= status < 600) and _attempt < 2:
                _delay = 3 * (2 ** _attempt)
                print(
                    f"[api] post_card blad [{err_type} status={status}] — retry {_attempt + 1}/2 za {_delay}s",
                    file=sys.stderr, flush=True,
                )
                _time.sleep(_delay)
                continue
            print(f"[api] post_card blad: {exc}", file=sys.stderr, flush=True)
            break
    if _last_exc is not None:
        status = getattr(getattr(_last_exc, "response", None), "status_code", None)
        err_type = type(_last_exc).__name__
        _set_last_send_error({
            "status": status, "err_type": err_type,
            "message": str(_last_exc), "attempts": _attempt + 1,
        })
        return None
    return None


# ---------------------------------------------------------------------------
# Conversation discovery — find ID by display name substring
# ---------------------------------------------------------------------------

def find_conversation_id(display_name: str) -> str | None:
    """Search all conversations (group + 1:1) for one matching display_name.

    Uses the chatsvc endpoint — same token as get_messages, no extra auth needed.
    Matches on thread topic (group chats) and on member display names (1:1 chats).
    """
    if not display_name or not display_name.strip():
        return None
    url = f"{_BASE}/users/ME/conversations"
    params = {"view": "msnp24Equivalent", "pageSize": 200}
    try:
        r = _session().get(url, params=params, timeout=10)
        r.raise_for_status()
    except Exception as exc:
        print(f"[api] find_conversation_id blad: {exc}", file=sys.stderr, flush=True)
        return None

    data = r.json()
    conversations = data.get("conversations", [])
    if not conversations and isinstance(data, list):
        conversations = data
    target = display_name.lower()

    for conv in conversations:
        conv_id = conv.get("id", "")
        if not conv_id:
            continue
        # Group chat: match by topic / subject
        thread_props = conv.get("threadProperties") or {}
        topic = (thread_props.get("topic") or thread_props.get("chatSubject") or "").lower()
        if target in topic:
            return conv_id
        # 1:1 chat: match by member display name
        for member in conv.get("members") or []:
            name = (member.get("displayName") or member.get("name") or "").lower()
            if target in name:
                return conv_id

    print(f"[api] find_conversation_id: brak dopasowania dla {display_name!r} "
          f"(sprawdzono {len(conversations)} konwersacji)", file=sys.stderr, flush=True)
    return None


def _fuzzy_find_conversations(display_name: str) -> list[tuple[str, str]]:
    """Return ALL conversations whose display name contains display_name (case-insensitive).

    Returns a list of (conv_id, matched_label) tuples.
    Uses the same chatsvc endpoint as find_conversation_id.
    Deduplicates by conv_id, preserves first-match order.
    Returns [] on request failure or no match.
    """
    if not display_name or not display_name.strip():
        return []
    url = f"{_BASE}/users/ME/conversations"
    params = {"view": "msnp24Equivalent", "pageSize": 200}
    try:
        r = _session().get(url, params=params, timeout=10)
        r.raise_for_status()
    except Exception as exc:
        print(f"[api] _fuzzy_find_conversations blad: {exc}", file=sys.stderr, flush=True)
        return []

    data = r.json()
    conversations = data.get("conversations", [])
    if not conversations and isinstance(data, list):
        conversations = data
    target = display_name.strip().lower()

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for conv in conversations:
        conv_id = conv.get("id", "")
        if not conv_id or conv_id in seen:
            continue
        thread_props = conv.get("threadProperties") or {}
        topic = (thread_props.get("topic") or thread_props.get("chatSubject") or "").strip()
        if target in topic.lower():
            results.append((conv_id, topic))
            seen.add(conv_id)
            continue
        for member in conv.get("members") or []:
            name = (member.get("displayName") or member.get("name") or "").strip()
            if name and target in name.lower():
                results.append((conv_id, name))
                seen.add(conv_id)
                break

    return results


# ---------------------------------------------------------------------------
# GIF via AMS (Attachment Management Service) -- Teams' native inline-image
# upload flow, used for Bob's own mascot gifs ([BOB_GIF: mood], bob_gifs.py).
# Unlike Giphy (external URL), this uploads GIF bytes directly and Teams serves
# them from its own CDN.
# ---------------------------------------------------------------------------

_AMS_BASE = "https://eu-prod.asyncgw.teams.microsoft.com"
_AMS_VIEW_BASE = "https://eu-api.asm.skype.com"

# AMS object-create rejects requests missing browser Client Hints even with a
# valid User-Agent ("The 'User-Agent' header is missing or invalid"). Values
# copied verbatim from a live Chrome/Teams capture -- the sec-ch-ua GREASE brand
# and the Chrome version MUST stay consistent with each other or AMS 400s.
_AMS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
_AMS_CLIENT_HINTS = {
    "sec-ch-ua": '"Not/A)Brand";v="99", "Chromium";v="148"',
    "sec-ch-ua-platform": '"macOS"',
    "sec-ch-ua-mobile": "?0",
    "Referer": "https://teams.microsoft.com/",
    "x-ms-client-version": "1415/26070215711",
    "x-ms-migration": "True",
    "x-ms-test-user": "False",
}


def _ams_upload_gif(gif_file: Path, conv_id: str) -> tuple[str, int] | None:
    """Upload a local GIF file to AMS. Returns (object_id, byte_count) or None on failure.

    Two-step flow: POST object-create (permissions scoped to conv_id), then PUT the
    raw bytes. Requires the ic3-audience token. Never raises.
    """
    token = _ic3_token or _load_cached_ic3_token()[0]
    if not token:
        print("[api] ams_upload_gif: brak tokenu ic3 — pomijam upload", file=sys.stderr, flush=True)
        return None
    try:
        data = gif_file.read_bytes()
    except Exception as exc:
        print(f"[api] ams_upload_gif: nie mozna wczytac {gif_file}: {exc}", file=sys.stderr, flush=True)
        return None

    base_headers = dict(_AMS_CLIENT_HINTS)
    base_headers["User-Agent"] = _AMS_USER_AGENT
    base_headers["Authorization"] = f"Bearer {token}"
    create_body = {
        "type": "pish/image",
        "permissions": {conv_id: ["read"]},
        "sharingMode": "Inline",
        "filename": gif_file.name,
    }
    try:
        r = _requests.post(
            f"{_AMS_BASE}/v1/objects/",
            json=create_body,
            headers={
                **base_headers,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-ams-post-sharing-mode": "Inline",
            },
            timeout=15,
        )
        r.raise_for_status()
        object_id = r.json().get("id")
        if not object_id:
            print("[api] ams_upload_gif: brak id w odpowiedzi object-create", file=sys.stderr, flush=True)
            return None
    except Exception as exc:
        print(f"[api] ams_upload_gif object-create blad: {exc}", file=sys.stderr, flush=True)
        return None

    try:
        r2 = _requests.put(
            f"{_AMS_BASE}/v1/objects/{object_id}/content/imgpsh",
            data=data,
            headers={**base_headers, "Content-Type": "application/octet-stream"},
            timeout=30,
        )
        r2.raise_for_status()
    except Exception as exc:
        print(f"[api] ams_upload_gif content-put blad: {exc}", file=sys.stderr, flush=True)
        return None
    return object_id, len(data)


def send_ams_gif_message(
    caption_html: str,
    gif_file: Path,
    conversation_id: str = NOTES_CONV_ID,
    mentions: list[dict] | None = None,
) -> str | None:
    """Upload gif_file to AMS and post caption_html + the AMS <img> as one message.

    caption_html MUST start with a BOT_HTML_PREFIXES prefix so delete_message() /
    own-message detection keep recognizing it as a bot post. Returns the Teams
    message id, or None when the upload/post failed (caller skips silently).
    """
    if _refuse_write(conversation_id, "ams_gif"):
        return None
    uploaded = _ams_upload_gif(gif_file, conversation_id)
    if uploaded is None:
        return None
    object_id, byte_count = uploaded
    img_url = f"{_AMS_VIEW_BASE}/v1/objects/{object_id}/views/imgpsh_mobile_save_anim"
    img_tag = (
        f'<img src="{img_url}" itemtype="http://schema.skype.com/AMSImage" '
        f'itemscope="gif" data-inline-image="true" data-file-size="{byte_count}" '
        f'width="200" height="200" alt="{_escape(gif_file.stem)}" id="{object_id}" itemid="{object_id}"/>'
    )
    content = f"{caption_html}<p>{img_tag}</p>"
    return _post_returning_id(conversation_id, content, mentions, amsreferences=[object_id])
