"""Hourly SharePoint XLSX poller: download urlopy file -> convert to JSON -> write dest.

Pisze wylacznie do knowledge/urlopy/urlopy.json (zrodlo dla urlopy-bob).
"""
import json
import os
import re
import sys
import tempfile
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logts  # noqa: E402, F401, I001 -- side-effect: CET timestamps na stdout/stderr
import org_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sharepoint_meta  # noqa: E402, I001
from session import load_storage_state  # noqa: E402

SESSION_FILE = Path(__file__).parent / "session.json"
DOWNLOAD_DIR = Path(__file__).parent / "downloads"
# Jedyny plik wynikowy -- zrodlo dla urlopy-bob (scripts/urlopy/mcp_server.py).
# _existing_count czyta go przy "skipped".
DEST = Path(__file__).resolve().parents[2] / "knowledge" / "urlopy" / "urlopy.json"

# The urlopy workbook is a personal OneDrive file, so its location is deployment data:
# URLOPY_SHAREPOINT_URL holds the browser "share" link (…/:x:/r/personal/<user>/Documents/
# <file>.xlsx?d=w<32 hex>…) and every other URL below is derived from it. Unset = the
# poller reports "not_configured" and does nothing.
MTIME_KEY = "urlopy"
_SHARE_PREFIX_RE = re.compile(r"^/:[a-z]:/[rgs]/")
_UNIQUE_ID_RE = re.compile(r"^w?([0-9a-fA-F]{32})$")


def derive_urls(share_url: str) -> dict[str, str] | None:
    """{view, download, rest_mtime, referer} for a OneDrive/SharePoint file share link.

    None when the link is empty or lacks the pieces the poller needs (an https host,
    a /personal/ or /sites/ path with /Documents/, the d=w<id> file id).
    """
    parsed = urllib.parse.urlparse((share_url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    path = urllib.parse.unquote(_SHARE_PREFIX_RE.sub("/", parsed.path))
    if "/Documents/" not in path or not path.startswith(("/personal/", "/sites/")):
        return None
    m = _UNIQUE_ID_RE.match((urllib.parse.parse_qs(parsed.query).get("d") or [""])[0])
    if not m:
        return None
    hex_id = m.group(1).lower()
    unique_id = f"{hex_id[:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:]}"
    site = f"https://{parsed.hostname}{path[: path.index('/Documents/')]}"
    return {
        "view": share_url.strip(),
        "download": f"{site}/_layouts/15/download.aspx?UniqueId={unique_id}",
        "rest_mtime": (
            f"{site}/_api/web/GetFileByServerRelativeUrl('{path}')?$select=TimeLastModified"
        ),
        "referer": f"{site}/_layouts/15/onedrive.aspx",
    }


_URLS = derive_urls(org_config.URLOPY_SHAREPOINT_URL)
URL = _URLS["view"] if _URLS else ""
DOWNLOAD_URL = _URLS["download"] if _URLS else ""
REST_API_URL = _URLS["rest_mtime"] if _URLS else ""
SITE_REFERER_URL = _URLS["referer"] if _URLS else ""
NOT_CONFIGURED_MSG = "[sharepoint-poller] URLOPY_SHAREPOINT_URL not configured — urlopy poll skipped"


def download_xlsx() -> Path:
    """Download the XLSX from SharePoint using the existing SSO session.

    Returns the path of the downloaded file inside DOWNLOAD_DIR.
    Raises RuntimeError if session is missing or expired (login redirect detected).
    """
    from playwright.sync_api import sync_playwright  # local import — isolates Playwright

    if not SESSION_FILE.exists():
        raise RuntimeError("session.json not found — run login.py first")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=load_storage_state(SESSION_FILE),
            accept_downloads=True,
        )
        page = context.new_page()

        page.goto(URL, wait_until="domcontentloaded", timeout=30_000)

        current_url = page.url
        if (
            "login.microsoftonline.com" in current_url
            or "login.live.com" in current_url
        ):
            browser.close()
            raise RuntimeError(
                "Session expired — redirected to login page. Run login.py to refresh."
            )

        with page.expect_download(timeout=30_000) as dl_info:
            try:
                page.goto(DOWNLOAD_URL, wait_until="commit", timeout=10_000)
            except Exception:
                pass  # download-triggered navigation always raises

        download = dl_info.value
        suggested = download.suggested_filename or "urlopy_live.xlsx"
        dest = DOWNLOAD_DIR / suggested
        download.save_as(str(dest))

        browser.close()

    return dest


def xlsx_to_records(path: Path) -> list[dict]:
    """Convert an XLSX file to a list of dicts (header row -> values).

    Datetime values are serialised as ISO-8601 strings.
    Empty cells become None.
    Pure function — no network I/O.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Sheet1"]

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        return []

    headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
    records: list[dict] = []

    for row in rows[1:]:
        record: dict = {}
        for header, cell in zip(headers, row, strict=False):
            if isinstance(cell, datetime):
                record[header] = cell.isoformat()
            else:
                record[header] = cell
        records.append(record)

    return records


def write_json(records: list[dict]) -> int:
    """Serialise records to DEST as JSON and return the record count.

    Zapis atomowy (plik tymczasowy w tym samym katalogu + os.replace): urlopy-bob
    czyta DEST w dowolnym momencie i nie moze trafic na urwany JSON.
    """
    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "count": len(records),
        "records": records,
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=DEST.parent, prefix=f".{DEST.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialized)
        os.replace(tmp_name, DEST)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return len(records)


def _existing_count() -> int:
    if not DEST.exists():
        return -1
    try:
        return json.loads(DEST.read_text(encoding="utf-8")).get("count", -1)
    except (json.JSONDecodeError, OSError):
        return -1


def poll() -> dict:
    """Download XLSX, convert to JSON, write to DEST.

    Skips the download+convert if SharePoint's TimeLastModified hasn't advanced
    since the last successful run -- returns {"status": "skipped", "count": <existing>}
    in that case, or {"status": "ok", "count": <new>} after a real run.
    """
    if not _URLS:
        print(NOT_CONFIGURED_MSG, file=sys.stderr, flush=True)
        return {"status": "not_configured", "count": _existing_count()}
    remote_mtime = sharepoint_meta.fetch_remote_mtime(REST_API_URL, SITE_REFERER_URL)
    state = sharepoint_meta.load_state()
    if not sharepoint_meta.has_changed(MTIME_KEY, remote_mtime, state):
        return {"status": "skipped", "count": _existing_count()}

    path = download_xlsx()
    records = xlsx_to_records(path)
    count = write_json(records)
    sharepoint_meta.mark_processed(MTIME_KEY, remote_mtime)
    return {"status": "ok", "count": count}


if __name__ == "__main__":
    try:
        print(json.dumps(poll(), ensure_ascii=False))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
