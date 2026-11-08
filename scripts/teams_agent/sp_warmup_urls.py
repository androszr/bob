"""SharePoint URLs that Bob hits — used by login.py and cli.py to warm FedAuth cookies.

Each SharePoint site collection needs its own FedAuth cookie, so the list holds one
URL per site collection that live code reads or writes; warming anything else only
slows down login.py and the 6-hourly re-warm.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import org_config  # noqa: E402


def _site_root(folder_url: str) -> str:
    """`https://<host>/sites/<site>/` for a SharePoint folder share link, else ""."""
    m = re.match(r"^(https://[^/]+)(?:/:[a-z]:/[rgs])?(/sites/[^/?#]+)", folder_url or "")
    return f"{m.group(1)}{m.group(2)}/" if m else ""


SP_WARMUP_URLS = [
    url for url in (
        # leave poller (sharepoint_poller.py) — OneDrive personal file view, same URL as the poller
        org_config.LEAVE_SHAREPOINT_URL,
        # attachment uploads (sharepoint_upload.py, [SEND_FILE] replies) — the upload
        # folder's team site, its own site collection on the main tenant domain
        _site_root(org_config.SHAREPOINT_UPLOAD_FOLDER_URL),
    ) if url
]
