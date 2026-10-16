"""Upload a file to a SharePoint document library folder — replacement for the
fragile Teams-composer CDP drag-drop in `attachments_upload.py`.

Why this exists: the CDP-trusted-drag-into-Teams-composer mechanism can fail —
after the synthetic `drop` event, Teams may keep showing its own full-page
"Share in this chat" drop overlay, which then intercepts ALL pointer events
site-wide, so neither the attachment chip nor the message ever lands. That
mechanism relies on undocumented Teams composer/drag internals and is
inherently fragile.

This module sidesteps the whole problem: instead of dragging a file into Teams'
proprietary composer drop-zone, it drives SharePoint's own upload button, which
is backed by a plain `<input type="file">` — Playwright's `expect_file_chooser`
handles that via a first-class, non-hacky API (no synthetic trusted-event
trickery needed). The caller then sends a normal text message with a link via
`api_client.send_message`, which is the same reliable REST path already used
for every other Bob reply.

Public API:
  upload_file(file_path, folder_url=DEFAULT_FOLDER_URL) -> str | None
       Returns a direct URL to the uploaded file, or None on failure.
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import org_config  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from session import load_storage_state  # noqa: E402

SESSION_FILE = Path(__file__).parent / "session.json"

# SHAREPOINT_UPLOAD_FOLDER_URL; empty = uploads are not configured (upload_file says so).
DEFAULT_FOLDER_URL = org_config.SHAREPOINT_UPLOAD_FOLDER_URL

_UPLOAD_BUTTON_TEXT = "Utwórz lub przekaż"
_UPLOAD_MENU_ITEM_TEXT = "Przekazywanie plików"
_CONFLICT_KEEP_BOTH_TEXT = "Zachowaj oba"

# Bezpiecznik: notatki .md <= 512 KB, pliki audio (MP3) do ~50 MB.
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _folder_path_from_page_url(page_url: str) -> str | None:
    """Extract the canonical library-relative folder path from the redirected
    AllItems.aspx URL's `id` query param (URL-decoded), e.g.
    '/sites/Team/Shared Documents/Bob'.
    """
    parsed = urllib.parse.urlparse(page_url)
    qs = urllib.parse.parse_qs(parsed.query)
    id_values = qs.get("id")
    if not id_values:
        return None
    return urllib.parse.unquote(id_values[0])


def _build_file_url(page_url: str, folder_path: str, filename: str) -> str:
    parsed = urllib.parse.urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    full_path = f"{folder_path.rstrip('/')}/{filename}"
    return origin + urllib.parse.quote(full_path)


def upload_file(
    file_path: Path | str,
    folder_url: str = DEFAULT_FOLDER_URL,
    dest_name: str | None = None,
    timeout_s: float = 30.0,
) -> str | None:
    """Upload `file_path` into the SharePoint folder at `folder_url`.

    Args:
        file_path: local file to upload.
        folder_url: SharePoint folder sharing URL (":f:/r/..." form is fine —
            we follow its redirect to the real library view to recover the
            canonical folder path).
        dest_name: filename to give the uploaded file. Defaults to
            `file_path.name`. Callers that may re-upload the same logical file
            repeatedly should pass a unique name (e.g. timestamp-prefixed) —
            SharePoint pops a blocking "file already exists" dialog on a literal
            name collision, which this function does not proactively avoid.
        timeout_s: max time to wait for the upload-confirmation toast.

    Returns:
        Direct URL to the uploaded file, or None on failure (logged to stderr).
    """
    if not folder_url:
        print("[sp-upload] SHAREPOINT_UPLOAD_FOLDER_URL not configured — upload skipped",
              file=sys.stderr)
        return None
    file_path = Path(file_path)
    if not file_path.exists() or not file_path.is_file():
        print(f"[sp-upload] BLAD: plik nie istnieje: {file_path}", file=sys.stderr)
        return None
    size = file_path.stat().st_size
    if size == 0:
        print(f"[sp-upload] BLAD: plik pusty (0 B), odrzucam: {file_path.name}",
              file=sys.stderr)
        return None
    if size > _MAX_UPLOAD_BYTES:
        print(f"[sp-upload] BLAD: plik {size} B > limit {_MAX_UPLOAD_BYTES}",
              file=sys.stderr)
        return None

    dest_name = dest_name or file_path.name

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--proxy-auto-detect", "--ignore-certificate-errors"],
        )
        try:
            context = browser.new_context(
                storage_state=load_storage_state(SESSION_FILE),
                viewport={"width": 1600, "height": 1000},
                accept_downloads=True,
            )
            page = context.new_page()
            page.goto(folder_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            current_url = page.url
            if "login.microsoftonline.com" in current_url or "login.live.com" in current_url:
                print("[sp-upload] BLAD: sesja wygasla — przekierowanie do logowania. "
                      "Uruchom login.py.", file=sys.stderr)
                return None

            folder_path = _folder_path_from_page_url(current_url)
            if not folder_path:
                print(f"[sp-upload] BLAD: nie mogę wyciągnąć folder path z URL {current_url!r}",
                      file=sys.stderr)
                return None

            try:
                page.get_by_text(_UPLOAD_BUTTON_TEXT, exact=False).first.click(timeout=10000)
            except Exception as exc:
                print(f"[sp-upload] BLAD: nie znalazlem przycisku {_UPLOAD_BUTTON_TEXT!r}: {exc}",
                      file=sys.stderr)
                return None
            page.wait_for_timeout(800)

            # Upload via a real <input type=file> through Playwright's file-chooser
            # interception — no synthetic drag events, no trusted-CDP hackery.
            _staged: Path = file_path
            if dest_name != file_path.name:
                import tempfile
                tmp_dir = Path(tempfile.mkdtemp(prefix="sp_upload_"))
                _staged = tmp_dir / dest_name
                _staged.write_bytes(file_path.read_bytes())

            try:
                with page.expect_file_chooser(timeout=10000) as fc_info:
                    page.get_by_text(_UPLOAD_MENU_ITEM_TEXT, exact=False).first.click()
                fc_info.value.set_files(str(_staged.resolve()))
            except Exception as exc:
                print(f"[sp-upload] BLAD: file chooser: {exc}", file=sys.stderr)
                return None

            deadline_conflict = 5000
            try:
                keep_both = page.get_by_text(_CONFLICT_KEEP_BOTH_TEXT, exact=False).first
                keep_both.click(timeout=deadline_conflict)
                print(f"[sp-upload] konflikt nazwy dla {dest_name!r} — kliknieto "
                      f"{_CONFLICT_KEEP_BOTH_TEXT!r}", file=sys.stderr)
            except Exception:
                pass  # no conflict dialog — expected path when dest_name is unique

            deadline = timeout_s
            found = False
            waited = 0.0
            step = 1.0
            while waited < deadline:
                page.wait_for_timeout(int(step * 1000))
                waited += step
                body_text = page.evaluate("() => document.body.innerText")
                if dest_name in body_text:
                    found = True
                    break

            if not found:
                print(f"[sp-upload] BLAD: nie potwierdzono uploadu {dest_name!r} w {deadline}s",
                      file=sys.stderr)
                return None

            file_url = _build_file_url(current_url, folder_path, dest_name)
            print(f"[sp-upload] OK: {dest_name!r} -> {file_url}", file=sys.stderr)
            return file_url
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Upload pliku do folderu SharePoint (Playwright + file chooser).",
    )
    ap.add_argument("file", type=Path, help="Sciezka do pliku.")
    ap.add_argument("--folder-url", default=DEFAULT_FOLDER_URL,
                     help="Docelowy folder SharePoint (sharing URL).")
    ap.add_argument("--dest-name", default=None,
                     help="Nazwa pliku w SharePoint (domyslnie: nazwa lokalna).")
    args = ap.parse_args(argv)

    url = upload_file(args.file, folder_url=args.folder_url, dest_name=args.dest_name)
    if url:
        print(f"OK url={url}")
        return 0
    print("BLAD: upload nie powiodl sie", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
