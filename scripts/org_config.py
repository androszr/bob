"""Org- and owner-specific settings, read from the environment (.env in the repo root).

Every value that names the employer, its hosts or a real person lives here, so the
code stays neutral and a private deployment gets its values back through `.env`.
Defaults are placeholders only: a feature whose value is missing reports
"not configured" instead of guessing a real host.

Loading follows the repo's dotenv convention: `os.environ.setdefault`, so a variable
exported in the shell wins over `.env`.

Import as `import org_config` with `scripts/` on sys.path (every entry point already
puts it there), or as `scripts.org_config` from the repo root.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"

# Sentinel MRI used while OWNER_TEAMS_USER_ID is unset. It can never equal a real
# sender's MRI (not a GUID) nor an empty/missing one, so owner-only gates stay closed.
UNCONFIGURED_OWNER_MRI = "8:orgid:owner-not-configured"

DEFAULT_JIRA_URL = "https://jira.example.com"
DEFAULT_JIRA_PROJECT = "PROJ"
DEFAULT_OWNER_DISPLAY_NAME = "Owner"
DEFAULT_OWNER_ALIAS = "Owner"


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Copy KEY=VALUE lines from `path` into os.environ without overriding the shell."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    # Empty value = use the default, as documented in .env.example.
    return (env.get(key) or "").strip() or default


def _board_id(raw: str) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


@dataclass(frozen=True)
class OrgConfig:
    owner_teams_user_id: str = ""
    owner_display_name: str = DEFAULT_OWNER_DISPLAY_NAME
    owner_alias: str = DEFAULT_OWNER_ALIAS
    owner_jira_login: str = ""
    org_email_domain: str = ""
    jira_url: str = DEFAULT_JIRA_URL
    jira_board_id: int | None = None
    jira_project: str = DEFAULT_JIRA_PROJECT
    jira_teams: tuple[str, ...] = ()
    sharepoint_tenant: str = ""
    urlopy_sharepoint_url: str = ""
    sharepoint_upload_folder_url: str = ""
    name_noise_prefixes: tuple[str, ...] = ()

    @property
    def owner_mri(self) -> str:
        if not self.owner_teams_user_id:
            return UNCONFIGURED_OWNER_MRI
        return f"8:orgid:{self.owner_teams_user_id}"

    @property
    def jira_browse_url(self) -> str:
        return f"{self.jira_url}/browse/"

    @property
    def sharepoint_hosts(self) -> tuple[str, ...]:
        """(`<tenant>.sharepoint.com`, `<tenant>-my.sharepoint.com`), or () when unset."""
        if not self.sharepoint_tenant:
            return ()
        return (f"{self.sharepoint_tenant}.sharepoint.com", f"{self.sharepoint_tenant}-my.sharepoint.com")


def from_env(env: Mapping[str, str] | None = None) -> OrgConfig:
    """Build an OrgConfig from `env` (default: os.environ). Pure — no .env loading."""
    env = os.environ if env is None else env
    teams = tuple(t.strip() for t in _get(env, "JIRA_TEAMS").split(",") if t.strip())
    return OrgConfig(
        owner_teams_user_id=_get(env, "OWNER_TEAMS_USER_ID"),
        owner_display_name=_get(env, "OWNER_DISPLAY_NAME", DEFAULT_OWNER_DISPLAY_NAME),
        owner_alias=_get(env, "OWNER_ALIAS", DEFAULT_OWNER_ALIAS),
        owner_jira_login=_get(env, "OWNER_JIRA_LOGIN"),
        org_email_domain=_get(env, "ORG_EMAIL_DOMAIN"),
        jira_url=_get(env, "JIRA_URL", DEFAULT_JIRA_URL).rstrip("/"),
        jira_board_id=_board_id(_get(env, "JIRA_BOARD_ID")),
        jira_project=_get(env, "JIRA_PROJECT", DEFAULT_JIRA_PROJECT),
        jira_teams=teams,
        sharepoint_tenant=_get(env, "SHAREPOINT_TENANT").lower(),
        urlopy_sharepoint_url=_get(env, "URLOPY_SHAREPOINT_URL"),
        sharepoint_upload_folder_url=_get(env, "SHAREPOINT_UPLOAD_FOLDER_URL"),
        name_noise_prefixes=tuple(p.strip().lower() for p in _get(env, "NAME_NOISE_PREFIXES").split(",") if p.strip()),
    )


def load_json_config(env_key: str, default_path: Path) -> dict:
    """Read a gitignored JSON config (path overridable by `env_key`).

    A missing or unreadable file is an empty dict, never a crash: the features that
    need it then report themselves as not configured.
    """
    path = Path(_get(os.environ, env_key) or default_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


load_dotenv()
CONFIG = from_env()

OWNER_TEAMS_USER_ID = CONFIG.owner_teams_user_id
OWNER_MRI = CONFIG.owner_mri
OWNER_DISPLAY_NAME = CONFIG.owner_display_name
OWNER_ALIAS = CONFIG.owner_alias
OWNER_JIRA_LOGIN = CONFIG.owner_jira_login
ORG_EMAIL_DOMAIN = CONFIG.org_email_domain
JIRA_URL = CONFIG.jira_url
JIRA_BROWSE_URL = CONFIG.jira_browse_url
JIRA_BOARD_ID = CONFIG.jira_board_id
JIRA_PROJECT = CONFIG.jira_project
JIRA_TEAMS = CONFIG.jira_teams
SHAREPOINT_TENANT = CONFIG.sharepoint_tenant
SHAREPOINT_HOSTS = CONFIG.sharepoint_hosts
URLOPY_SHAREPOINT_URL = CONFIG.urlopy_sharepoint_url
SHAREPOINT_UPLOAD_FOLDER_URL = CONFIG.sharepoint_upload_folder_url
NAME_NOISE_PREFIXES = CONFIG.name_noise_prefixes
