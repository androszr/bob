"""Upload pliku do Teams chatu — dostarczenie pliku przez przegladarke.

Sciezka REST-only (PUT na SP + POST chatsvc z properties.files) nie wyszla:
SharePoint odrzuca PUT bez OAuth handshake Teams Web Client (`accessDenied`,
401/403 lub `invalidRequestDigest`). Pelen handshake wymaga POST do
`/_layouts/15/filebrowser.aspx?app=TeamsFile` + `/_api/SP.OAuth.NativeClient/Authenticate`
ktore web client uruchamia jako boot flow — odtworzenie tego z poziomu Pythona
to znacznie wiecej niz POC.

Co dziala niezawodnie i jest tu zaimplementowane:
  1. Playwright launches headless Chromium z `session.json`.
  2. Switch na target chat (`scraper.switch_to_chat_by_name`).
  3. **CDP `Input.dispatchDragEvent`** doreczy plik do composera — CDP-level event
     ma `isTrusted=true`, wiec Teams akceptuje go tak samo jak prawdziwy drop
     z desktopu. JS-synthesized `DragEvent` z DataTransfer (isTrusted=false)
     jest odrzucany.
  4. Wciska Enter w composerze.
  5. Polling REST chatsvc (`api_client.get_messages`) sprawdza ze swieza wiadomosc
     z `properties.files` faktycznie pojawila sie na chacie — bez tego latwo
     przegapic ciche failure (np. upload zablokowany po stronie Teams).

Koszt: ~5-8 s na wywolanie (launch + Teams shell + chat switch + drop + verify).
Bob nie powinien tego wolac na kazdej wiadomosci, ale dla okazjonalnego "wrzuc
plik X na chat Y" jest OK.

Public API:
  upload_and_send(chat_name, file_path, text="") -> str | None
       Returns Teams message ID on success, None on failure.

CLI:
  python3 scripts/teams_agent/attachments_upload.py <file> [--chat NAME] [--text ""]
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_client as api  # noqa: E402
import scraper  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from session import load_storage_state  # noqa: E402

SESSION_FILE = Path(__file__).parent / "session.json"
TEAMS_URL = "https://teams.microsoft.com/v2/"

# Bezpiecznik: notatki .md <= 512 KB, pliki audio (MP3) do ~50 MB.
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


_COMPOSER_RECT_JS = """
(selectors) => {
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (r && r.width > 20 && r.height > 5) {
            return {sel, x: r.x, y: r.y, w: r.width, h: r.height};
        }
    }
    return null;
}
"""

_COMPOSER_SELECTORS = [
    '[data-tid="ckeditor"]',
    '[role="textbox"][contenteditable="true"]',
    '[data-tid="messageBodyContent"]',
    '[data-tid="newMessageContainer"]',
]


def _capture_token(page) -> None:
    """Sciagnij Bearer token z pierwszego chatsvc call zeby REST verify dzialal."""
    def _on_request(req) -> None:
        if "chatsvc" not in req.url:
            return
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            api.set_auth_token(auth[len("Bearer "):])
    page.on("request", _on_request)


def _find_composer_rect(page, timeout_s: float = 10.0) -> dict | None:
    """Poll for the message composer's bounding rect via getBoundingClientRect."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            rect = page.evaluate(_COMPOSER_RECT_JS, _COMPOSER_SELECTORS)
            if rect:
                return rect
        except Exception:
            pass
        page.wait_for_timeout(500)
    return None


def _cdp_drop_file(page, file_path: Path, x: float, y: float) -> bool:
    """Dispatch a TRUSTED drag-and-drop sequence via Chrome DevTools Protocol.

    `Input.dispatchDragEvent` z `data.files` przepuszczonym przez Chromium daje
    eventy z `isTrusted=true` — odwrotnie niz JS-syntezowany DragEvent. To
    JEDYNA metoda ktora dostarcza plik do composera (paperclip
    ukryty, drag-JS odrzucany, paste-JS odrzucany).
    """
    try:
        client = page.context.new_cdp_session(page)
    except Exception as exc:
        print(f"[upload] CDP session blad: {exc}", file=sys.stderr, flush=True)
        return False
    drag_data = {
        "items": [],
        "files": [str(file_path.resolve())],
        "dragOperationsMask": 1,  # Copy
    }
    try:
        for evt_type in ("dragEnter", "dragOver", "drop"):
            client.send("Input.dispatchDragEvent", {
                "type": evt_type, "x": x, "y": y,
                "data": drag_data, "modifiers": 0,
            })
            page.wait_for_timeout(120)
    except Exception as exc:
        print(f"[upload] CDP dispatchDragEvent blad: {exc}",
              file=sys.stderr, flush=True)
        return False
    return True


def _type_text(page, text: str) -> None:
    if not text:
        return
    try:
        box = page.locator('[data-tid="ckeditor"]').first
        box.click(timeout=2000)
        box.type(text, delay=10)
    except Exception as exc:
        print(f"[upload] type_text blad: {exc}", file=sys.stderr, flush=True)


def _press_enter(page, cx: float | None = None, cy: float | None = None) -> bool:
    """Focus the composer and press Enter to send.

    Prefers mouse.click(cx, cy) when coordinates are supplied — avoids
    re-querying the selector (which may differ from the one _find_composer_rect
    matched) and bypasses Playwright's actionability check that blocks on a
    ck-blurred/hidden CKEditor.
    """
    try:
        if cx is not None and cy is not None:
            page.mouse.click(cx, cy)
        else:
            page.locator('[data-tid="ckeditor"]').first.click(timeout=5000)
        page.keyboard.press("Enter")
        return True
    except Exception as exc:
        print(f"[upload] Enter blad: {exc}", file=sys.stderr, flush=True)
        return False


def _verify(conv_id: str, baseline_seq: int, file_name: str,
            timeout_s: float = 45.0) -> dict | None:
    """Poll chatsvc REST aż pojawi sie wiadomosc z sequence_id > baseline + attachmentem.

    Nie matchujemy po file_name bo SharePoint moze zrobic conflict rename
    (`name.md` → `name (1).md`). Zaufaj sekwencji + obecnosci jakiegokolwiek
    attachmentu — od momentu baseline tylko nasz upload moze dodac plik.

    sender_mri check: probujemy USER_MRI, ale jesli sender_mri jest pusty
    (zdarza sie gdy from field ma inny format), akceptujemy kazda wiadomosc
    z attachmentem powyzej baseline — to jedyny wpis po naszym uploading.
    """
    deadline = time.time() + timeout_s
    poll = 0
    while time.time() < deadline:
        poll += 1
        # Reset _needs_token_refresh before every poll — if the watch loop set it
        # to True (due to its own 401), it would silently block all get_messages calls.
        # Fresh token was captured by _capture_token during Playwright page load.
        if api._auth_token and api._needs_token_refresh:
            print(f"[upload] verify poll {poll}: reset _needs_token_refresh (token age ok)",
                  file=sys.stderr, flush=True)
            api.set_auth_token(api._auth_token)

        # n=30 (not 10) — a busy chat can push the just-sent attachment message
        # out of a small window before the next poll runs, producing a false
        # "not found yet" even though the upload already succeeded.
        msgs = api.get_messages(conv_id, n=30)
        if not msgs:
            print(f"[upload] verify poll {poll}: get_messages=[] "
                  f"needs_refresh={api._needs_token_refresh} "
                  f"token_set={bool(api._auth_token)}",
                  file=sys.stderr, flush=True)
        else:
            seqs = [m.get("sequence_id", 0) for m in msgs]
            has_att = sum(1 for m in msgs if m.get("attachments"))
            print(f"[upload] verify poll {poll}: {len(msgs)} msg, "
                  f"seq={min(seqs)}-{max(seqs)} baseline={baseline_seq} "
                  f"z_att={has_att}",
                  file=sys.stderr, flush=True)

        for m in reversed(msgs):
            seq = m.get("sequence_id") or 0
            if seq <= baseline_seq:
                continue
            atts = m.get("attachments") or []
            if not atts:
                continue
            mri = m["sender_mri"]
            # Accept if sender_mri matches USER_MRI OR if sender_mri is empty
            # (API sometimes omits /contacts/ path for CDP-sent messages).
            if mri and mri != api.USER_MRI:
                print(f"[upload] verify: pomin wiadomosc od {mri!r} (nie USER_MRI)",
                      file=sys.stderr, flush=True)
                continue
            return {
                "msg_id": m["id"],
                "timestamp": m["timestamp"],
                "sequence_id": seq,
                "attachment": atts[0],
                "text": m["text"],
            }
        time.sleep(2)
    return None


def _find_already_posted(conv_id: str, floor_seq: int, file_name: str) -> dict | None:
    """Look for a prior attempt's upload that actually landed despite a verify timeout.

    Called only before a RETRY, not on the first attempt. `_verify` deliberately
    avoids matching by filename (SharePoint may rename on conflict, e.g.
    `name.md` -> `name (1).md`), but here that's exactly the right tool: we're
    not verifying THIS attempt, we're asking "did an earlier attempt already
    post this file?" so we don't drop a second/third copy into the chat just
    because the REST verify poll can be flaky (dozens of polls timing out on a
    stale/cached read while the message had already landed).
    """
    stem = re.escape(Path(file_name).stem)
    suffix = re.escape(Path(file_name).suffix)
    name_re = re.compile(rf"^{stem}( \(\d+\))?{suffix}$", re.IGNORECASE)
    try:
        msgs = api.get_messages(conv_id, n=30)
    except Exception as exc:
        print(f"[upload] dedup-check blad: {exc}", file=sys.stderr, flush=True)
        return None
    if not msgs:
        return None
    for m in reversed(msgs):
        seq = m.get("sequence_id") or 0
        if seq <= floor_seq:
            continue
        for att in (m.get("attachments") or []):
            if name_re.match(att.get("name") or ""):
                return {
                    "msg_id": m["id"],
                    "timestamp": m["timestamp"],
                    "sequence_id": seq,
                    "attachment": att,
                    "text": m["text"],
                }
    return None


def upload_and_send(
    chat_name: str,
    file_path: Path | str,
    text: str = "",
    conv_id: str | None = None,
    timeout_s: float = 60.0,
) -> str | None:
    """Wgraj plik do Teams chatu i wyslij wiadomosc (opcjonalnie z dopiskiem).

    Args:
        chat_name: substring nazwy chatu do switch_to_chat_by_name (np. "Notatki",
                   "Ewa Nowak"). Powinno wystarczyc kilka znakow unikalnych.
        file_path: lokalny plik do uploadu (max 512 KB).
        text: opcjonalny tekst dopisany w wiadomosci.
        conv_id: jezeli znamy id (np. "48:notes") to omijamy REST find. Uzywany
                 do REST verify tak czy tak — jesli None, prob lookup via
                 api_client.find_conversation_id(chat_name).
        timeout_s: max czekanie na verify upload.

    Returns:
        Teams message ID (str) gdy verify wyszedl, None w razie bledu.
    """
    file_path = Path(file_path)
    if not file_path.exists() or not file_path.is_file():
        print(f"[upload] BLAD: plik nie istnieje: {file_path}", file=sys.stderr)
        return None
    size = file_path.stat().st_size
    if size == 0:
        print(f"[upload] BLAD: plik pusty (0 B), odrzucam: {file_path.name}",
              file=sys.stderr)
        return None
    if size > _MAX_UPLOAD_BYTES:
        print(f"[upload] BLAD: plik {size} B > limit {_MAX_UPLOAD_BYTES}",
              file=sys.stderr)
        return None

    if not conv_id:
        conv_id = api.find_conversation_id(chat_name)
        if not conv_id:
            print(f"[upload] BLAD: nie znalazlem conv_id dla {chat_name!r}",
                  file=sys.stderr)
            return None

    try:
        _pre_msgs = api.get_messages(conv_id, n=20)
        floor_seq = max((m.get("sequence_id") or 0) for m in _pre_msgs) \
            if _pre_msgs else 0
        _floor_seq_known = True
    except Exception as exc:
        # Without a real floor_seq we can't scope the dedup-check to messages
        # from THIS call — a floor of 0 would match same-named files from any
        # earlier, unrelated upload in the last 30 messages and falsely report
        # a genuinely-failed retry as "already posted". Safer to skip the
        # dedup-check below (risking a duplicate post) than to silently drop
        # a real upload.
        floor_seq = 0
        _floor_seq_known = False
        print(f"[upload] BLAD ustalania floor_seq, dedup-check na retry pominiety: {exc}",
              file=sys.stderr, flush=True)

    _MAX_ATTEMPTS = 3
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if attempt > 1:
            # A previous attempt's *verify* can time out (stale/cached REST read)
            # even though the drag-drop already landed in Teams. Check for that
            # before dragging the same file in again — otherwise a flaky verify
            # duplicates the post instead of just re-confirming it.
            already = _find_already_posted(conv_id, floor_seq, file_path.name) \
                if _floor_seq_known else None
            if already:
                print(f"[upload] plik juz na chacie z wczesniejszej proby "
                      f"(msg_id={already['msg_id']}) — pomijam retry",
                      file=sys.stderr, flush=True)
                return str(already["msg_id"])
            print(f"[upload] retry {attempt}/{_MAX_ATTEMPTS} za 4s ...",
                  file=sys.stderr, flush=True)
            time.sleep(4)
        result = _upload_attempt(p_file=file_path, p_chat=chat_name,
                                 p_conv_id=conv_id, p_text=text,
                                 p_timeout_s=timeout_s)
        if result is not None:
            return result
    return None


def _upload_attempt(
    p_file: Path,
    p_chat: str,
    p_conv_id: str | None,
    p_text: str,
    p_timeout_s: float,
) -> str | None:
    """Single Playwright attempt — extracted so upload_and_send can retry."""
    conv_id = p_conv_id
    chat_name = p_chat
    file_path = p_file
    text = p_text
    timeout_s = p_timeout_s

    if not conv_id:
        conv_id = api.find_conversation_id(chat_name)
        if not conv_id:
            print(f"[upload] BLAD: nie znalazlem conv_id dla {chat_name!r}",
                  file=sys.stderr)
            return None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--proxy-auto-detect", "--ignore-certificate-errors"],
        )
        try:
            context = browser.new_context(
                storage_state=load_storage_state(SESSION_FILE),
                viewport={"width": 1600, "height": 1000},
            )
            page = context.new_page()
            _capture_token(page)

            page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=60000)

            # Teams has a two-phase loading splash that intercepts CDP drag events:
            #   Phase 1 "Just another minute" — rail not yet rendered
            #   Phase 2 "Almost there..."    — rail visible, chat content loading
            # Both phases put an overlay over the viewport. CDP drag events land on
            # the overlay and are silently ignored even though ckeditor rect is
            # non-zero (pre-rendered DOM behind the overlay).
            #
            # Phase 1 ends when div[role="treeitem"] becomes visible.
            # Phase 2 ends a few seconds later (variable).
            try:
                page.wait_for_selector(
                    'div[role="treeitem"]', state="visible", timeout=60000,
                )
                page.wait_for_timeout(500)
                print("[upload] Teams faza 1 OK (treeitem visible)", file=sys.stderr, flush=True)
            except Exception:
                page.wait_for_timeout(10000)
                print("[upload] Teams faza 1 timeout — fallback 10s", file=sys.stderr, flush=True)

            # Switch to target chat (rail is loaded → conv_id match is fast, 10s max)
            switched = scraper.switch_to_chat_by_conv_id(page, conv_id, timeout_s=10)
            if not switched:
                print(f"[upload] conv_id rail miss, fallback do switch_by_name {chat_name!r}",
                      file=sys.stderr)
                switched = scraper.switch_to_chat_by_name(page, chat_name, timeout_s=20)
            if not switched:
                print(f"[upload] BLAD: nie wszedlem na chat {chat_name!r} / {conv_id!r}",
                      file=sys.stderr)
                return None

            # Phase 2: wait for chat content to finish loading.
            # New Teams UI (Fluent v9) uses message-pane-layout, not message-list.
            _PHASE2_SELS = [
                '[data-tid="message-pane-layout"]',
                '[data-tid="message-pane-list-viewport"]',
                '[data-tid="message-list"]',
            ]
            _phase2_ok = False
            for _sel in _PHASE2_SELS:
                try:
                    page.wait_for_selector(_sel, state="visible", timeout=5000)
                    page.wait_for_timeout(300)
                    print(f"[upload] Teams faza 2 OK ({_sel})", file=sys.stderr, flush=True)
                    _phase2_ok = True
                    break
                except Exception:
                    continue
            if not _phase2_ok:
                page.wait_for_timeout(10000)
                print("[upload] Teams faza 2 fallback 10s", file=sys.stderr, flush=True)

            # Baseline ZANIM dropniemy — REST verify potem szuka swiezszego seq.
            try:
                _msgs = api.get_messages(conv_id, n=20)
                baseline_seq = max((m.get("sequence_id") or 0) for m in _msgs) \
                    if _msgs else 0
            except Exception:
                baseline_seq = 0

            # wait_for_selector(state="visible") ZAWSZE timeoutuje bo ckeditor jest
            # mounted jako `ck-blurred` (hidden) az do focus — ale w trakcie tego
            # timeoutu Teams konczy mounting layoutu, wiec po jego wygasnieciu
            # polling rect (z `getBoundingClientRect`, ktore widzi hidden) zwraca
            # poprawny non-zero rect w 1-2 iter. To nielogiczne ale dziala
            # niezawodnie.
            try:
                page.wait_for_selector('[data-tid="ckeditor"]', state="attached", timeout=25000)
            except Exception:
                pass
            rect = _find_composer_rect(page, timeout_s=10.0)
            if not rect:
                print("[upload] BLAD: composer nie zmountowal sie",
                      file=sys.stderr)
                return None
            cx = rect["x"] + rect["w"] / 2
            cy = rect["y"] + rect["h"] / 2

            # Click the composer first to focus it — Teams sometimes needs this before
            # accepting a CDP drag drop (especially for 1:1 chats).
            try:
                page.mouse.click(cx, cy)
                page.wait_for_timeout(300)
            except Exception:
                pass

            print(f"[upload] drag -> cx={cx:.0f} cy={cy:.0f} sel={rect['sel']!r} "
                  f"w={rect['w']:.0f} h={rect['h']:.0f}", file=sys.stderr, flush=True)
            if not _cdp_drop_file(page, file_path, cx, cy):
                return None
            # Daj Teamsowi czas na upload + chip mounting przed Enter.
            page.wait_for_timeout(4000)

            # Screenshot po dropie.
            _dbg = Path(tempfile.gettempdir()) / "bob-upload-after-drop.png"
            try:
                page.screenshot(path=str(_dbg))
                print(f"[upload] screenshot po drop: {_dbg}", file=sys.stderr, flush=True)
            except Exception as _e:
                print(f"[upload] screenshot blad: {_e}", file=sys.stderr, flush=True)

            # Re-query rect after chip mounted — chip adds ~40px height, shifting layout.
            # Using stale cx/cy would click the chip itself instead of the text area.
            new_rect = _find_composer_rect(page, timeout_s=3.0)
            if new_rect:
                cx = new_rect["x"] + new_rect["w"] / 2
                cy = new_rect["y"] + new_rect["h"] / 2
                print(f"[upload] composer pos post-chip: cx={cx:.0f} cy={cy:.0f} "
                      f"dy={new_rect['y'] - rect['y']:+.0f}",
                      file=sys.stderr, flush=True)

            if text:
                _type_text(page, text)

            if not _press_enter(page, cx, cy):
                return None
            page.wait_for_timeout(2500)

            verify = _verify(conv_id, baseline_seq, file_path.name, timeout_s=timeout_s)
            if not verify:
                print("[upload] BLAD: REST nie potwierdzilo uploadu w timeout",
                      file=sys.stderr)
                return None
            print(f"[upload] OK msg_id={verify['msg_id']} "
                  f"attachment={verify['attachment'].get('name')!r}",
                  file=sys.stderr)
            return str(verify["msg_id"])
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Upload pliku do Teams chatu (Playwright + CDP drag).",
    )
    ap.add_argument("file", type=Path, help="Sciezka do pliku.")
    ap.add_argument("--chat", default="Notatki",
                    help="Substring nazwy chatu. Default: Notatki (48:notes).")
    ap.add_argument("--text", default="",
                    help="Opcjonalny tekst dopisany do wiadomosci.")
    ap.add_argument("--conv-id", default=None,
                    help="Opcjonalny conv_id (omija REST lookup po nazwie).")
    args = ap.parse_args(argv)

    msg_id = upload_and_send(
        chat_name=args.chat,
        file_path=args.file,
        text=args.text,
        conv_id=args.conv_id,
    )
    if msg_id:
        print(f"OK msg_id={msg_id}")
        return 0
    print("BLAD: upload/send nie powiodl sie", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
