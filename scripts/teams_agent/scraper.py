"""Playwright wrappers for interacting with the Teams web client.

Exports four functions:
    list_chats(page)               -> list[dict]
    switch_to_chat(page, chat_id)  -> bool
    read_messages(page, max_messages=20) -> list[dict]
    send_message(page, text)       -> bool

Each function uses a priority-ordered selector list and a frame-search
fallback.  Non-breaking spaces
are normalised with .replace('\\xa0', ' ') on every inner_text result.
Debug screenshots are written to scripts/teams_agent/*.png on selector miss.

NOTE: Requires a display (headless=False from login; headless=True is fine for
scraping after session reuse).  Do NOT import from this module without
playwright installed.
"""

import os
import re
import sys
import time as _time_mod
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Priority-ordered selector lists — listed most-specific first so that when
# Microsoft updates the DOM, the broader fallbacks still catch the element.
# ---------------------------------------------------------------------------

CHAT_LIST_SELECTORS = [
    # Teams v2 (Fluent UI v9), checked against a live DOM dump.
    # Leaf treeitems = individual chats. Group headers ("Ulubione", etc.) are
    # also treeitems but contain nested treeitems; :not(:has(...)) filters them.
    'div[role="treeitem"].fui-TreeItem:not(:has(div[role="treeitem"]))',
    'div[role="treeitem"]:not(:has(div[role="treeitem"]))',
    # Legacy fallbacks (older Teams DOMs).
    '[data-tid="chat-list-item"]',
    'li[data-tid*="chat"]',
    'div[role="listitem"]',
]

# Section labels we always want to skip even when they happen to look like
# leaf treeitems. ASCII-only — Teams uses these literally in Polish locale.
_CHAT_NON_ITEM_LABELS = {
    'Copilot',
    'Szybki podglad',
    'Wersje robocze',
    'Nieprzeczytane',
    'Wzmianki',
    'Spotkania',
    'Ulubione',
    'Czaty',
    'Czaty spotkan',
}

# Matches HH:MM (time) and DD.MM (Polish short date) — Teams shows exactly one
# such marker per chat row. Group headers contain zero (Copilot) or many
# (concatenated group text). Real chats have exactly one.
_TIME_DATE_PATTERN = re.compile(r'\b\d{1,2}[:.]\d{2}\b')

CHAT_ITEM_NAME_SELECTORS = [
    '[data-tid="chat-title"]',
    '[class*="chatTitle"]',
    '[class*="chat-title"]',
    'span[dir="auto"]',
]

MESSAGE_THREAD_SELECTORS = [
    '[data-tid="message-list"]',
    '[aria-label*="conversation" i]',
    '[class*="messageList"]',
    '[class*="message-list"]',
    '[role="list"][aria-label]',
]

MESSAGE_ITEM_SELECTORS = [
    '[data-tid="message-body"]',
    '[class*="messageBody"]',
    '[class*="message-body"]',
    '[class*="messageContent"]',
    'div[role="group"] p',
    'div[class*="message"] p',
]

REPLY_INPUT_SELECTORS = [
    '[data-tid="ckeditor"]',
    'div[contenteditable="true"][aria-label*="reply" i]',
    'div[contenteditable="true"][aria-label*="odpowiedz" i]',
    'div[contenteditable="true"][class*="ql-editor"]',
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
]

SEND_BUTTON_SELECTORS = [
    '[data-tid="send-message-button"]',
    'button[aria-label*="Send" i]',
    'button[aria-label*="Wyslij" i]',
    'button[title*="Send" i]',
    'button[class*="sendButton"]',
    'button[class*="send-button"]',
]

# Toolbar that appears on message hover — "Reply/Odpowiedz" action button.
REPLY_ACTION_SELECTORS = [
    '[data-tid="reply-button"]',
    'button[aria-label*="Reply" i]',
    'button[aria-label*="Odpowiedz" i]',
    'button[title*="Reply" i]',
    '[class*="replyButton"]',
    '[class*="reply-button"]',
]

_SCRIPT_DIR = Path(__file__).parent


def _log(msg: str) -> None:
    print(f"[teams] {msg}", file=sys.stderr, flush=True)


# Set TEAMS_ROBOT_DEBUG=1 to echo full DOM/frame diagnostics to the console.
# By default only a one-line summary is logged; full detail goes to a file
# (see _dump_candidates, _diag_page).
_DEBUG = os.environ.get("TEAMS_ROBOT_DEBUG", "").strip() not in ("", "0")


def _log_debug(msg: str) -> None:
    if _DEBUG:
        _log(msg)


def _find_in_context(ctx, selectors: list[str], timeout_ms: int = 300):
    """Try each selector in priority order. Return (locator, ctx) or None.

    Sequential search preserves priority — important because Teams may render
    multiple elements matching a broad selector; the first in priority order
    is typically the correct one.
    """
    for sel in selectors:
        try:
            loc = ctx.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc, ctx
        except Exception:
            continue
    return None


def _find_with_frame_fallback(page, selectors: list[str], timeout_ms: int = 300):
    """Try selectors on page, then on each iframe frame.  Return (locator, ctx) or None."""
    result = _find_in_context(page, selectors, timeout_ms)
    if result:
        return result
    for frame in page.frames:
        result = _find_in_context(frame, selectors, timeout_ms)
        if result:
            return result
    return None


def _debug_screenshot(page, name: str) -> None:
    path = str(_SCRIPT_DIR / f"teams-{name}-miss.png")
    try:
        page.screenshot(path=path)
        _log(f"Screenshot zapisany: teams-{name}-miss.png")
    except Exception as exc:
        _log(f"Nie udalo sie zapisac screenshota: {exc}")


def _diag_page(page, label: str) -> None:
    """Log a one-line page context (URL, frame count) before giving up.

    Full per-frame URL list only appears with TEAMS_ROBOT_DEBUG=1.
    """
    try:
        frames = page.frames
        _log(f"diagnostyka [{label}]: URL={page.url[:100]} ramek={len(frames)}")
        for i, f in enumerate(frames):
            try:
                _log_debug(f"  ramka[{i}] url={f.url[:120]}")
            except Exception:
                _log_debug(f"  ramka[{i}] url=<blad odczytu>")
    except Exception as exc:
        _log(f"diagnostyka [{label}] blad: {exc}")


_CANDIDATE_JS = r"""() => {
  const probes = [
    ['role=treeitem',  () => document.querySelectorAll('[role="treeitem"]')],
    ['role=listitem',  () => document.querySelectorAll('[role="listitem"]')],
    ['role=option',    () => document.querySelectorAll('[role="option"]')],
    ['role=row',       () => document.querySelectorAll('[role="row"]')],
    ['data-tid*=chat', () => document.querySelectorAll('[data-tid*="chat" i]')],
    ['aria czat/chat', () => Array.from(document.querySelectorAll('*[aria-label]'))
                                .filter(e => /czat|chat/i.test(e.getAttribute('aria-label') || ''))],
    ['nav/aside',      () => document.querySelectorAll('nav, aside')],
  ];
  const out = [];
  for (const [label, getter] of probes) {
    let elems;
    try { elems = Array.from(getter()); } catch { elems = []; }
    if (!elems.length) continue;
    const sample = elems.slice(0, 5).map(e => {
      const rect = e.getBoundingClientRect ? e.getBoundingClientRect() : {width:0,height:0};
      return {
        tag: (e.tagName || '').toLowerCase(),
        role: e.getAttribute ? e.getAttribute('role') : null,
        aria: ((e.getAttribute && e.getAttribute('aria-label')) || '').slice(0, 80),
        dataTid: e.getAttribute ? e.getAttribute('data-tid') : null,
        cls: ((e.className && e.className.toString) ? e.className.toString() : '').slice(0, 120),
        text: ((e.innerText || '') + '').slice(0, 80).replace(/\s+/g, ' ').trim(),
        visible: rect.width > 0 && rect.height > 0,
      };
    });
    out.push({ probe: label, total: elems.length, sample });
  }
  return out;
}"""


def _dump_candidates(page, label: str) -> None:
    """Enumerate candidate DOM elements when selectors miss.

    The console gets ONE summary line; the full per-element breakdown is
    written to teams-{label}-diag.txt (same pattern as _dump_body_html).
    Set TEAMS_ROBOT_DEBUG=1 to also echo the full breakdown to the console.
    """
    try:
        candidates = page.evaluate(_CANDIDATE_JS)
    except Exception as exc:
        _log(f"diagnostyka [{label}]: blad evaluate: {exc}")
        return
    if not candidates:
        _log(f"diagnostyka [{label}]: strona nie zawiera zadnych typowych elementow Teams")
        return

    lines = [f"URL: {page.url}", ""]
    for c in candidates:
        lines.append(f"probe={c['probe']!r} total={c['total']}")
        for i, s in enumerate(c['sample']):
            vis = "" if s['visible'] else " (HIDDEN)"
            lines.append(f"  [{i}] tag={s['tag']} role={s['role']!r} "
                         f"data-tid={s['dataTid']!r}{vis}")
            for key in ('aria', 'cls', 'text'):
                if s[key]:
                    lines.append(f"      {key}={s[key]!r}")
        lines.append("")

    path = _SCRIPT_DIR / f"teams-{label}-diag.txt"
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
        where = path.name
    except Exception as exc:
        where = f"<zapis nieudany: {exc}>"

    summary = ", ".join(f"{c['total']}x {c['probe']}" for c in candidates)
    _log(f"diagnostyka [{label}]: w DOM widoczne m.in. {summary} — pelny zrzut: {where}")
    for line in lines:
        _log_debug(f"  {line}")


def _dump_body_html(page, label: str) -> None:
    """Save body outerHTML to disk for offline inspection."""
    try:
        html = page.evaluate("() => document.body ? document.body.outerHTML : ''")
    except Exception as exc:
        _log(f"DUMP HTML blad: {exc}")
        return
    path = _SCRIPT_DIR / f"teams-{label}-body.html"
    try:
        path.write_text(html, encoding='utf-8')
        _log(f"HTML zapisany: {path.name} ({len(html) // 1024} KB)")
    except Exception as exc:
        _log(f"DUMP HTML zapis blad: {exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_chats(page) -> list[dict]:
    """Return a list of visible chats from the left rail.

    Each dict has keys: chat_id (str), name (str), unread (bool).
    Returns [] when no chat items are found within 30 s.
    """
    deadline = _time_mod.time() + 30
    attempt = 0
    result = None
    last_url = None
    stable_count = 0
    while result is None and _time_mod.time() < deadline:
        attempt += 1
        frames = page.frames
        frame_urls = [_safe_url(f) for f in frames]
        _log_debug(f"list_chats proba {attempt}: {len(frames)} ramek. URLs: {frame_urls}")
        result = _find_with_frame_fallback(page, CHAT_LIST_SELECTORS, timeout_ms=300)
        if result is not None:
            break

        # Fail fast: once the URL is stable on teams.microsoft.com for 3 attempts,
        # Teams is loaded but our selectors don't match — no point polling further.
        cur_url = (page.url or "").lower()
        if cur_url == last_url:
            stable_count += 1
        else:
            stable_count = 0
            last_url = cur_url
        if stable_count >= 3 and "teams.microsoft.com" in cur_url:
            _log(f"URL stabilny od {stable_count + 1} prob — Teams zaladowany ale "
                 f"CHAT_LIST_SELECTORS nie pasuja do biezacego DOM. Przerywam polling.")
            break

        page.wait_for_timeout(500)

    if result is None:
        elapsed = 30 - max(0.0, deadline - _time_mod.time())
        _log(f"BLAD: Nie znaleziono listy czatow ({elapsed:.1f}s, {attempt} prob)")
        _diag_page(page, "list_chats")
        _dump_candidates(page, "list_chats")
        _dump_body_html(page, "list_chats")
        _debug_screenshot(page, "list_chats")
        return []

    loc, ctx = result
    # Collect all matching items.
    items = ctx.locator(CHAT_LIST_SELECTORS[0]).all()
    if not items:
        # Fallback: try remaining selectors for the all() call.
        for sel in CHAT_LIST_SELECTORS[1:]:
            items = ctx.locator(sel).all()
            if items:
                break

    chats: list[dict] = []
    skipped = 0
    for i, item in enumerate(items):
        try:
            raw = item.inner_text().replace('\xa0', ' ').strip()
            if not _is_chat_item_text(raw):
                skipped += 1
                continue
            name = _extract_chat_name(raw)
            # Heuristic unread detection: Teams adds aria attributes or CSS classes.
            unread = False
            try:
                cls = item.get_attribute("class") or ""
                unread = "unread" in cls.lower()
            except Exception:
                pass
            chats.append({"chat_id": str(i), "name": name, "unread": unread})
        except Exception as exc:
            _log(f"list_chats item {i} error: {exc}")

    _log(f"list_chats: znaleziono {len(chats)} czatow ({skipped} naglowkow/grup pominieto)")
    return chats


def _is_chat_item_text(text: str) -> bool:
    """Heuristic: a real chat row has exactly one HH:MM or DD.MM marker.

    Headers (Copilot, Szybki podglad, ...) contain none. Group treeitems
    (Ulubione + nested chats) contain many. Real chat rows contain exactly one.
    """
    if not text:
        return False
    stripped = text.strip()
    if stripped in _CHAT_NON_ITEM_LABELS:
        return False
    # Some section labels are followed by aggregated child text — they all
    # collapse to multiple time/date markers, so the count check catches them.
    matches = _TIME_DATE_PATTERN.findall(stripped)
    return len(matches) == 1


def _extract_chat_name(text: str) -> str:
    """Pull the chat name (everything before the time/date marker)."""
    m = _TIME_DATE_PATTERN.search(text)
    if not m:
        return text.strip()
    name = text[:m.start()].strip()
    return name or text.strip()


_PL_TRANS = str.maketrans(
    'ąćęłńóśźżĄĆĘŁŃÓŚŹŻ',
    'acelnoszzACELNOSZZ',
)


def _strip_accents(s: str) -> str:
    """Remove diacritical marks, including non-decomposable Polish letters.

    'Paweł' → 'Pawel', 'Ziółkowski' → 'Ziolkowski', 'Rafał' → 'Rafal'.
    ł/Ł are handled explicitly because they have no NFKD ASCII decomposition.
    """
    return unicodedata.normalize('NFKD', s.translate(_PL_TRANS)).encode('ascii', 'ignore').decode()


def _name_tokens_match(target: str, candidate: str) -> bool:
    """Order-insensitive, accent-insensitive token match.

    Every whitespace-split token of `target` must appear as a substring in
    `candidate` (both already lowercased).  Diacritics are stripped before
    comparison so 'Paweł' matches 'Pawel' and vice-versa.

    Handles Teams' display format: reversed names ("Surname Firstname"),
    dept-prefix ("EXT Ewa Nowak"), and missing
    Polish diacritics in Teams' user directory.
    """
    t_norm = _strip_accents(target)
    c_norm = _strip_accents(candidate)
    return all(tok in c_norm for tok in t_norm.split())


def _current_chat_title(page, timeout_ms: int = 1500) -> str | None:
    """Read the currently-open chat's title (right-pane h2[data-tid=chat-title]).

    Returns None if the title element isn't visible within timeout_ms.
    """
    try:
        loc = page.locator('[data-tid="chat-title"]').first
        loc.wait_for(state="visible", timeout=timeout_ms)
        raw = loc.inner_text() or ""
        return raw.replace('\xa0', ' ').strip() or None
    except Exception:
        return None


def switch_to_chat_by_name(page, name: str, timeout_s: int = 60) -> bool:
    """Find a chat whose name contains `name` and open it (substring, case-insensitive).

    Polls TWO paths in parallel until one wins or `timeout_s` is reached:
      A) Fast path: Teams already shows the target chat in the right pane
         (h2[data-tid=chat-title] matches). Teams restores the last-open chat
         across sessions, so we frequently land here without needing to click.
      B) Slow path: walk the left-rail tree (treeitems) and click the match.
         Slow path requires the rail to be rendered — in compact view or
         immediately after app-shell ready, treeitems may not yet exist.

    Polling interleaves both paths every ~700 ms so a late-rendering DOM
    has many chances to be picked up by whichever path lands first.
    """
    target_lower = name.lower()
    deadline = _time_mod.time() + timeout_s
    attempts = 0
    last_title_seen: str | None = None

    while _time_mod.time() < deadline:
        attempts += 1

        # --- Path A: already on the chat? ---
        title = _current_chat_title(page, timeout_ms=200)
        if title:
            last_title_seen = title
            if _name_tokens_match(target_lower, title.lower()):
                _log(f"switch_to_chat_by_name: juz na chacie {title!r} "
                     f"(fast path, {attempts} prob)")
                return True

        # --- Path B: chat in the left rail? ---
        # First try the fast JS path — searches via innerText (preserves whitespace
        # so timestamp regex works) and uses token-based matching.
        # Tested: handles "EXT Ewa Nowak" matching "Ewa Nowak" correctly.
        # Returns True as soon as item is found+clicked (same contract as dispatch_event
        # fallback below — caller verifies via phase-2 message-list check).
        try:
            js_result = page.evaluate(_CLICK_BY_NAME_TOKENS_JS, name)
            if js_result.get("found"):
                page.wait_for_timeout(800)
                new_title = _current_chat_title(page, timeout_ms=800)
                _log(f"switch_to_chat_by_name: JS click {js_result['chatName']!r} "
                     f"title={new_title!r} ({attempts} prob)")
                return True
        except Exception:
            pass

        # Playwright fallback — also uses inner_text() + token matching.
        # Covers frames and edge cases where JS evaluate is unavailable.
        result = _find_in_context(page, CHAT_LIST_SELECTORS, timeout_ms=200)
        if result is None:
            for frame in page.frames:
                result = _find_in_context(frame, CHAT_LIST_SELECTORS, timeout_ms=100)
                if result:
                    break

        if result is not None:
            _loc, ctx = result
            items: list = []
            for sel in CHAT_LIST_SELECTORS:
                candidates = ctx.locator(sel).all()
                if candidates:
                    items = candidates
                    break

            for i, item in enumerate(items):
                try:
                    # inner_text() — unlike text_content() it preserves whitespace
                    # between DOM nodes, so "Maciej 11:23" stays intact and the
                    # timestamp word-boundary (\b) is detected correctly.
                    raw = (item.inner_text() or '').replace('\xa0', ' ').strip()
                    if not _is_chat_item_text(raw):
                        continue
                    chat_name = _extract_chat_name(raw)
                    if _name_tokens_match(target_lower, chat_name.lower()):
                        try:
                            item.scroll_into_view_if_needed(timeout=2000)
                        except Exception:
                            pass
                        item.dispatch_event("click")
                        page.wait_for_timeout(800)
                        _log(f"switch_to_chat_by_name: kliknieto {chat_name!r} "
                             f"(index {i}, rail path, {attempts} prob)")
                        return True
                except Exception as exc:
                    _log(f"switch_to_chat_by_name item {i} error: {exc}")

        if attempts in (1, 5, 15, 30):  # heartbeat — not every tick
            _log(f"szukam czatu {name!r}... (~{attempts // 2}s; aktualnie otwarty: "
                 f"{last_title_seen or 'brak'})")

        page.wait_for_timeout(500)

    elapsed = timeout_s - max(0.0, deadline - _time_mod.time())
    _log(f"NIE ZNALEZIONO czatu {name!r} po {elapsed:.0f}s "
         f"— otwarty pozostal: {last_title_seen or 'brak'}")
    _dump_candidates(page, "switch_to_chat_by_name")
    return False


_CLICK_ANY_OTHER_CHAT_JS = """
() => {
    /* Click the first rail treeitem that looks like a chat (has exactly one
       timestamp in its innerText) and is NOT currently selected/active.
       Used for token refresh when we just need ANY navigation, not a specific chat.
       Returns {found: true, chatName} or {found: false}.
    */
    const selectors = [
        'div[role="treeitem"].fui-TreeItem:not(:has(div[role="treeitem"]))',
        'div[role="treeitem"]:not(:has(div[role="treeitem"]))',
    ];
    for (const sel of selectors) {
        for (const item of document.querySelectorAll(sel)) {
            const sel2 = item.getAttribute('aria-selected');
            const cur  = item.getAttribute('aria-current');
            if (sel2 === 'true' || cur === 'true') continue;
            const text = (item.innerText || '').replace(/\\u00a0/g, ' ').trim();
            const allTimes = text.match(/\\b\\d{1,2}[:.:]\\d{2}\\b/g);
            if (!allTimes || allTimes.length !== 1) continue;
            const mIdx = text.search(/\\b\\d{1,2}[:.:]\\d{2}\\b/);
            const chatName = (mIdx >= 0 ? text.slice(0, mIdx) : text).trim();
            item.click();
            return {found: true, chatName};
        }
    }
    return {found: false};
}
"""


def click_any_other_chat(page) -> str | None:
    """Click the first non-active rail chat item to trigger a chatsvc request.

    Used by token refresh — we don't care which chat, just need a real navigation.
    Returns the chat name clicked, or None if nothing was found/clicked.
    """
    try:
        result = page.evaluate(_CLICK_ANY_OTHER_CHAT_JS)
        if result.get("found"):
            name = result.get("chatName", "?")
            _log(f"click_any_other_chat: kliknieto {name!r}")
            return name
    except Exception as exc:
        _log(f"click_any_other_chat error: {exc}")
    return None


_CLICK_BY_NAME_TOKENS_JS = """
(name) => {
    /* Find a rail treeitem whose display name contains all tokens of `name`
       (order-insensitive, case-insensitive, accent-insensitive substring per token).
       Uses innerText — NOT textContent — because textContent concatenates
       adjacent DOM nodes without spaces (e.g. "Maciej11:23") which breaks
       the \\b word-boundary in the timestamp regex.  innerText reflects the
       rendered whitespace, so "Maciej 11:23" is preserved and the timestamp
       is detected correctly.  On success: calls item.click() (JS-native, not
       dispatchEvent) and returns {found: true, chatName}.
    */
    function stripAccents(s) {
        const pl = {'ą':'a','ć':'c','ę':'e','ł':'l','ń':'n','ó':'o','ś':'s','ź':'z','ż':'z',
                    'Ą':'A','Ć':'C','Ę':'E','Ł':'L','Ń':'N','Ó':'O','Ś':'S','Ź':'Z','Ż':'Z'};
        s = s.replace(/[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]/g, c => pl[c] || c);
        return s.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
    }
    const tokens = stripAccents(name.toLowerCase()).split(/\\s+/).filter(Boolean);
    const selectors = [
        'div[role="treeitem"].fui-TreeItem:not(:has(div[role="treeitem"]))',
        'div[role="treeitem"]:not(:has(div[role="treeitem"]))',
    ];
    for (const sel of selectors) {
        for (const item of document.querySelectorAll(sel)) {
            const text = (item.innerText || '').replace(/\\u00a0/g, ' ').trim();
            const allTimes = text.match(/\\b\\d{1,2}[:.:]\\d{2}\\b/g);
            if (!allTimes || allTimes.length !== 1) continue;
            const mIdx = text.search(/\\b\\d{1,2}[:.:]\\d{2}\\b/);
            const chatName = (mIdx >= 0 ? text.slice(0, mIdx) : text).trim();
            const chatNorm = stripAccents(chatName.toLowerCase());
            if (tokens.every(tok => chatNorm.includes(tok))) {
                item.click();
                return {found: true, chatName};
            }
        }
    }
    return {found: false};
}
"""

_CLICK_BY_CONV_ID_JS = """
(convId) => {
    // Teams rail items carry the conv_id in child <a href> or data-* attributes.
    // We search all known rail selectors, check every attribute + child link href.
    const encId = encodeURIComponent(convId);
    const selectors = [
        'div[role="treeitem"].fui-TreeItem:not(:has(div[role="treeitem"]))',
        'div[role="treeitem"]:not(:has(div[role="treeitem"]))',
        '[data-tid="chat-list-item"]',
        'li[data-tid*="chat"]',
        'div[role="listitem"]',
    ];
    for (const sel of selectors) {
        const items = document.querySelectorAll(sel);
        for (const item of items) {
            // Check child link hrefs (most reliable — href contains the full conv_id)
            for (const a of item.querySelectorAll('a[href]')) {
                const href = a.getAttribute('href') || '';
                if (href.includes(convId) || href.includes(encId)) {
                    item.click();
                    return true;
                }
            }
            // Check data-* attributes on the item itself
            for (const attr of item.attributes) {
                const v = attr.value;
                if (v.includes(convId) || v.includes(encId)) {
                    item.click();
                    return true;
                }
            }
        }
    }
    return false;
}
"""


def switch_to_chat_by_conv_id(page, conv_id: str, timeout_s: int = 45) -> bool:
    """Find a chat by conv_id via DOM rail search and click it.

    Scans rail items' child <a href> and data-* attributes for the conv_id.
    More reliable than switch_to_chat_by_name for accounts with non-standard
    Teams display names (company prefix, ASCII, reversed order, self-chat).

    Falls back to nothing — does NOT attempt the Teams deep link which
    redirects to launcher.html in headless Chromium.
    """
    _log(f"switch_to_chat_by_conv_id: szukam {conv_id!r} w railu")
    deadline = _time_mod.time() + timeout_s
    attempts = 0

    while _time_mod.time() < deadline:
        attempts += 1

        # Fast path: check if we're already on the right chat (not reliable for all
        # conv_id formats, but covers the self-chat 48:notes case).
        _current_chat_title(page, timeout_ms=200)

        try:
            clicked = page.evaluate(_CLICK_BY_CONV_ID_JS, conv_id)
        except Exception as exc:
            _log(f"switch_to_chat_by_conv_id evaluate blad: {exc}")
            clicked = False

        if clicked:
            page.wait_for_timeout(800)
            new_title = _current_chat_title(page, timeout_ms=1500)
            if new_title:
                _log(f"switch_to_chat_by_conv_id: OK title={new_title!r} ({attempts} prob)")
                return True

        if attempts in (1, 5, 15, 30):
            _log(f"switch_to_chat_by_conv_id: czekam... proba {attempts}")

        page.wait_for_timeout(500)

    _log(f"switch_to_chat_by_conv_id: TIMEOUT po {timeout_s}s ({attempts} prob)")
    return False


def switch_to_chat(page, chat_id: str) -> bool:
    """Click on the chat at index chat_id in the left rail.

    chat_id is a string index matching list_chats() output.
    Returns True on success, False when the element is not found.
    """
    deadline = _time_mod.time() + 30
    attempt = 0
    result = None
    while result is None and _time_mod.time() < deadline:
        attempt += 1
        frames = page.frames
        frame_urls = [_safe_url(f) for f in frames]
        _log_debug(f"switch_to_chat proba {attempt}: {len(frames)} ramek. URLs: {frame_urls}")
        result = _find_with_frame_fallback(page, CHAT_LIST_SELECTORS, timeout_ms=300)
        if result is None:
            page.wait_for_timeout(500)

    if result is None:
        _log("BLAD: Nie znaleziono listy czatow (switch_to_chat) po 30s")
        _diag_page(page, "switch_to_chat")
        _dump_candidates(page, "switch_to_chat")
        _debug_screenshot(page, "switch_to_chat")
        return False

    _loc, ctx = result
    try:
        idx = int(chat_id)
    except (ValueError, TypeError):
        _log(f"switch_to_chat: nieprawidlowe chat_id={chat_id!r}")
        return False

    items = None
    for sel in CHAT_LIST_SELECTORS:
        candidates = ctx.locator(sel).all()
        if candidates:
            items = candidates
            break

    if not items or idx >= len(items):
        _log(f"switch_to_chat: chat_id {idx} poza zakresem ({len(items or [])} czatow)")
        return False

    try:
        items[idx].dispatch_event("click")
        page.wait_for_timeout(800)
        _log(f"switch_to_chat: przelaczono na chat {idx}")
        return True
    except Exception as exc:
        _log(f"switch_to_chat: blad klikniecia: {exc}")
        _debug_screenshot(page, "switch_to_chat")
        return False


def read_messages(page, max_messages: int = 20) -> list[dict]:
    """Read the last max_messages messages from the current chat thread.

    Each dict has keys: sender (str), text (str).
    Returns [] when the message thread is not found within 30 s.
    Non-breaking spaces are normalised with .replace('\\xa0', ' ').
    """
    deadline = _time_mod.time() + 30
    attempt = 0
    result = None
    while result is None and _time_mod.time() < deadline:
        attempt += 1
        frames = page.frames
        frame_urls = [_safe_url(f) for f in frames]
        _log_debug(f"read_messages proba {attempt}: {len(frames)} ramek. URLs: {frame_urls}")
        result = _find_with_frame_fallback(page, MESSAGE_THREAD_SELECTORS, timeout_ms=300)
        if result is None:
            page.wait_for_timeout(500)

    if result is None:
        _log("BLAD: Nie znaleziono watku wiadomosci po 30s")
        _diag_page(page, "read_messages")
        _dump_candidates(page, "read_messages")
        _debug_screenshot(page, "read_messages")
        return []

    _loc, ctx = result
    items = []
    for sel in MESSAGE_ITEM_SELECTORS:
        candidates = ctx.locator(sel).all()
        if candidates:
            items = candidates
            break

    if not items:
        _log("read_messages: brak elementow wiadomosci w watku")
        return []

    items = items[-max_messages:]
    messages: list[dict] = []
    for item in items:
        # Read text first — if this fails, skip the item.
        try:
            raw = item.inner_text().replace('\xa0', ' ').strip()
        except Exception as exc:
            _log(f"read_messages inner_text error: {exc}")
            continue
        if not raw:
            continue
        # Walk up the DOM for data-mid — optional, never drops the message.
        mid = None
        try:
            mid = item.evaluate("""el => {
                let node = el;
                for (let i = 0; i < 20; i++) {
                    if (!node.parentElement) break;
                    node = node.parentElement;
                    if (node.hasAttribute('data-mid')) return node.getAttribute('data-mid');
                    if ((node.getAttribute('data-tid') || '') === 'message-container') return null;
                }
                return null;
            }""")
        except Exception:
            pass
        messages.append({"sender": "", "text": raw, "mid": mid})

    _log(f"read_messages: przeczytano {len(messages)} wiadomosci")
    return messages


def _fill_and_send(page, text: str, label: str = "send_message") -> bool:
    """Find the reply composer, fill it with text, and send (Enter or button).

    Shared by send_message and reply_to_message so both paths use identical
    fill+send logic.  label is used in log/screenshot names for diagnostics.
    """
    deadline = _time_mod.time() + 30
    attempt = 0
    result = None
    while result is None and _time_mod.time() < deadline:
        attempt += 1
        result = _find_with_frame_fallback(page, REPLY_INPUT_SELECTORS, timeout_ms=300)
        if result is None:
            page.wait_for_timeout(500)

    if result is None:
        _diag_page(page, label)
        _log(f"BLAD: Nie znaleziono pola odpowiedzi po 30s ({label})")
        _debug_screenshot(page, label)
        return False

    input_loc, _ctx = result
    try:
        input_loc.click()
        input_loc.fill(text)
        page.wait_for_timeout(300)
    except Exception as exc:
        _log(f"{label}: blad wypelniania pola: {exc}")
        _debug_screenshot(page, f"{label}-fill")
        return False

    try:
        input_loc.press("Enter")
        page.wait_for_timeout(800)
        _log(f"{label}: wiadomosc wyslana (Enter)")
        return True
    except Exception as exc:
        _log(f"{label}: Enter nie zadzialal ({exc}), fallback do przycisku Wyslij")

    send_result = _find_with_frame_fallback(page, SEND_BUTTON_SELECTORS, timeout_ms=3000)
    if send_result is None:
        _log(f"BLAD: Nie znaleziono przycisku Wyslij (fallback) i Enter nie zadzialal ({label})")
        _debug_screenshot(page, f"{label}-send")
        return False

    send_loc, _ctx = send_result
    try:
        send_loc.dispatch_event("click")
        page.wait_for_timeout(800)
        _log(f"{label}: wiadomosc wyslana (fallback przycisk)")
        return True
    except Exception as exc:
        _log(f"{label}: blad klikniecia Wyslij: {exc}")
        _debug_screenshot(page, f"{label}-click")
        return False


def send_message(page, text: str) -> bool:
    """Type text into the reply composer and click Send.

    Returns True on success, False when the input or send button is not found.
    """
    if not text or not text.strip():
        _log("send_message: pusta wiadomosc — pomijam")
        return False
    return _fill_and_send(page, text, label="send_message")


_REPLY_BTN_JS = """(el) => {
    /* Walk up from the message body to the nearest container that either
       carries data-mid (Teams message ID) or data-tid="message-container".
       Teams v2 nests: message-list > article[data-mid] > ... > body-element. */
    let container = el;
    for (let i = 0; i < 20; i++) {
        if (!container.parentElement) break;
        container = container.parentElement;
        const tid = container.getAttribute('data-tid') || '';
        if (container.hasAttribute('data-mid') || tid === 'message-container') break;
    }

    const replySelectors = [
        '[data-tid="reply-button"]',
        'button[aria-label="Reply"]',
        'button[aria-label="Odpowiedz"]',
        'button[title="Reply"]',
        'button[title="Odpowiedz"]',
        '[class*="replyButton"]',
        '[class*="reply-button"]',
    ];

    /* Pass 1: direct click — works even on opacity-0 / pointer-events-none
       buttons that are already in the DOM but invisible until hover. */
    for (const sel of replySelectors) {
        const btn = container.querySelector(sel);
        if (btn) { btn.click(); return 'direct'; }
    }

    /* Pass 2: dispatch hover events on the container so React / Fluent UI
       transitions the toolbar to visible, then let Playwright poll for it. */
    for (const type of ['mouseenter', 'mouseover', 'mousemove']) {
        container.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true}));
    }
    return 'hover-dispatched';
}"""


def reply_to_message(page, source_text: str, reply_text: str) -> bool:
    """Quote-reply to the message matching source_text, then send reply_text.

    Approach (no physical mouse movement required):
      1. Find the message body element whose inner_text matches source_text.
      2. Run _REPLY_BTN_JS via evaluate():
           a. Walk up the DOM to the data-mid container.
           b. Try a direct .click() on any Reply button already in the DOM
              (Teams keeps them in the DOM but hides them; direct JS click works).
           c. If not found, dispatch mouseenter/mouseover on the container so
              the React toolbar transitions to visible.
      3. If JS returned 'hover-dispatched', poll for visible Reply button.
      4. Fill the composer and send.
      5. Fallback to send_message (no quote) if any step fails.
    """
    if not reply_text or not reply_text.strip():
        _log("reply_to_message: pusta odpowiedz — pomijam")
        return False

    source_stripped = source_text.strip()

    # --- Step 1: find the message body element. ---
    target_item = None
    for sel in MESSAGE_ITEM_SELECTORS:
        try:
            items = page.locator(sel).all()
        except Exception:
            continue
        for item in items:
            try:
                raw = item.inner_text().replace('\xa0', ' ').strip()
                if raw == source_stripped or source_stripped in raw:
                    target_item = item
                    break
            except Exception:
                continue
        if target_item is not None:
            break

    if target_item is None:
        _log("reply_to_message: nie znaleziono wiadomosci — fallback send")
        return send_message(page, reply_text)

    # --- Step 2: JS — direct click or hover-event dispatch. ---
    try:
        outcome = target_item.evaluate(_REPLY_BTN_JS)
        _log(f"reply_to_message: JS outcome={outcome!r}")
    except Exception as exc:
        _log(f"reply_to_message: JS evaluate blad: {exc} — fallback send")
        return send_message(page, reply_text)

    if outcome == 'direct':
        # Button was clicked directly — composer should open immediately.
        page.wait_for_timeout(400)
        _log("reply_to_message: bezposrednie klikniecie Reply OK")
    else:
        # Hover events dispatched — poll for the now-visible Reply button.
        page.wait_for_timeout(600)
        clicked = False
        for sel in REPLY_ACTION_SELECTORS:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=1500)
                loc.click()
                clicked = True
                _log(f"reply_to_message: kliknieto Reply przez selektor {sel!r}")
                break
            except Exception:
                continue
        if not clicked:
            _log("reply_to_message: przycisk Reply niewidoczny po hover-dispatch — fallback send")
            return send_message(page, reply_text)
        page.wait_for_timeout(400)

    # --- Step 3: fill and send in the threaded composer. ---
    return _fill_and_send(page, reply_text, label="reply_to_message")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_url(frame) -> str:
    try:
        return frame.url[:80]
    except Exception:
        return "<err>"
