"""scripts/org_config.py and the features that read it.

The suite's conftest pins org values for the older fixtures, so everything here
builds configs from explicit mappings (org_config.from_env) or patches the
consuming module — never os.environ alone, which the modules read at import.
"""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "scripts" / "teams_agent"))
import org_config  # noqa: E402

# Real values must never be named in this public repo, not even as a deny-list, so
# "fictional" is checked by shape: every email/URL host is a placeholder domain and
# every GUID is a zero-GUID. (The real-value scan runs outside the repo.)
_PLACEHOLDER_HOSTS = ("example.com", ".example", "example.dev", "contoso.sharepoint.com",
                      "contoso-my.sharepoint.com")
_GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _assert_fictional(text: str) -> None:
    text = text.lower()
    hosts = re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text) + re.findall(r"https?://([\w.-]+)", text)
    for host in hosts:
        assert host.endswith(_PLACEHOLDER_HOSTS), host
    for guid in _GUID_RE.findall(text):
        assert guid.startswith("00000000-0000-0000-0000-"), guid


# ---------------------------------------------------------------------------
# Defaults are neutral
# ---------------------------------------------------------------------------

def test_defaults_are_neutral_placeholders():
    cfg = org_config.from_env({})
    for value in vars(cfg).values():
        _assert_fictional(json.dumps(value, default=str))
    assert cfg.jira_url == "https://jira.example.com"
    assert cfg.jira_board_id is None
    assert cfg.jira_teams == ()
    assert cfg.sharepoint_hosts == ()


def test_unset_owner_id_matches_no_sender():
    """Owner-only gates compare sender MRIs against owner_mri; unset must not open them."""
    mri = org_config.from_env({}).owner_mri
    assert mri == org_config.UNCONFIGURED_OWNER_MRI
    assert mri not in ("", "8:orgid:", None)


def test_empty_values_fall_back_to_defaults():
    cfg = org_config.from_env({"JIRA_URL": "  ", "OWNER_ALIAS": "", "JIRA_BOARD_ID": "nope"})
    assert cfg.jira_url == org_config.DEFAULT_JIRA_URL
    assert cfg.owner_alias == org_config.DEFAULT_OWNER_ALIAS
    assert cfg.jira_board_id is None


# ---------------------------------------------------------------------------
# Env overrides win
# ---------------------------------------------------------------------------

def test_env_values_override_defaults():
    cfg = org_config.from_env({
        "OWNER_TEAMS_USER_ID": "00000000-0000-0000-0000-00000000000a",
        "OWNER_DISPLAY_NAME": "Kowalski Jan",
        "OWNER_ALIAS": "Szef",
        "JIRA_URL": "https://jira.example.test/",
        "JIRA_BOARD_ID": "42",
        "JIRA_PROJECT": "OPS",
        "JIRA_TEAMS": "Alpha, Beta,,",
        "SHAREPOINT_TENANT": "Contoso",
    })
    assert cfg.owner_mri == "8:orgid:00000000-0000-0000-0000-00000000000a"
    assert (cfg.owner_display_name, cfg.owner_alias) == ("Kowalski Jan", "Szef")
    assert cfg.jira_url == "https://jira.example.test"
    assert cfg.jira_browse_url == "https://jira.example.test/browse/"
    assert (cfg.jira_board_id, cfg.jira_project, cfg.jira_teams) == (42, "OPS", ("Alpha", "Beta"))
    assert cfg.sharepoint_hosts == ("contoso.sharepoint.com", "contoso-my.sharepoint.com")


def test_load_dotenv_does_not_override_shell(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nORG_TEST_A=from-file\nORG_TEST_B=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ORG_TEST_A", "from-shell")
    monkeypatch.delenv("ORG_TEST_B", raising=False)
    org_config.load_dotenv(env_file)
    assert os.environ["ORG_TEST_A"] == "from-shell"
    assert os.environ["ORG_TEST_B"] == "from-file"
    monkeypatch.delenv("ORG_TEST_B")


def test_load_json_config_missing_or_broken_is_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("ORG_TEST_JSON", raising=False)
    assert org_config.load_json_config("ORG_TEST_JSON", tmp_path / "absent.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    assert org_config.load_json_config("ORG_TEST_JSON", broken) == {}
    good = tmp_path / "good.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    monkeypatch.setenv("ORG_TEST_JSON", str(good))
    assert org_config.load_json_config("ORG_TEST_JSON", tmp_path / "absent.json") == {"a": 1}


# ---------------------------------------------------------------------------
# SharePoint host allow-list (security boundary) — tenant-driven, exact match
# ---------------------------------------------------------------------------

def test_sharepoint_hosts_are_exactly_the_tenant_pair():
    hosts = org_config.from_env({"SHAREPOINT_TENANT": "contoso"}).sharepoint_hosts
    assert hosts == ("contoso.sharepoint.com", "contoso-my.sharepoint.com")


# ---------------------------------------------------------------------------
# urlopy poller: every URL derives from URLOPY_SHAREPOINT_URL; unset = skip
# ---------------------------------------------------------------------------

_SHARE = ("https://contoso-my.sharepoint.com/:x:/r/personal/jan_kowalski_contoso_com"
          "/Documents/Urlopy%20-%20live.xlsx?d=w0123456789abcdef0123456789ABCDEF&csf=1&web=1")


def test_poller_derives_all_urls_from_share_link():
    import sharepoint_poller
    urls = sharepoint_poller.derive_urls(_SHARE)
    site = "https://contoso-my.sharepoint.com/personal/jan_kowalski_contoso_com"
    assert urls == {
        "view": _SHARE,
        "download": f"{site}/_layouts/15/download.aspx?UniqueId=01234567-89ab-cdef-0123-456789abcdef",
        "rest_mtime": (f"{site}/_api/web/GetFileByServerRelativeUrl("
                       "'/personal/jan_kowalski_contoso_com/Documents/Urlopy - live.xlsx')"
                       "?$select=TimeLastModified"),
        "referer": f"{site}/_layouts/15/onedrive.aspx",
    }


@pytest.mark.parametrize("bad", ["", "http://contoso-my.sharepoint.com/:x:/r/personal/x/Documents/a.xlsx?d=w"
                                 + "0" * 32, "https://contoso-my.sharepoint.com/personal/x/Documents/a.xlsx",
                                 "https://contoso-my.sharepoint.com/elsewhere/a.xlsx?d=w" + "0" * 32])
def test_poller_rejects_incomplete_share_links(bad):
    import sharepoint_poller
    assert sharepoint_poller.derive_urls(bad) is None


def test_poller_unconfigured_skips_without_network(monkeypatch, capsys):
    import sharepoint_poller
    monkeypatch.setattr(sharepoint_poller, "_URLS", None)
    monkeypatch.setattr(sharepoint_poller, "_existing_count", lambda: 7)
    monkeypatch.setattr(sharepoint_poller.sharepoint_meta, "fetch_remote_mtime",
                        lambda *a: pytest.fail("must not touch SharePoint"))
    assert sharepoint_poller.poll() == {"status": "not_configured", "count": 7}
    assert "URLOPY_SHAREPOINT_URL not configured" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Other "not configured" paths
# ---------------------------------------------------------------------------

def test_warmup_site_root_from_upload_folder():
    import sp_warmup_urls
    folder = "https://contoso.sharepoint.com/:f:/r/sites/Team/Shared%20Documents/Bob?csf=1"
    assert sp_warmup_urls._site_root(folder) == "https://contoso.sharepoint.com/sites/Team/"
    assert sp_warmup_urls._site_root("") == ""


def test_warmup_list_skips_unset_urls():
    import sp_warmup_urls
    assert sp_warmup_urls.SP_WARMUP_URLS == []  # conftest leaves both URLs unset


def test_jira_sprints_without_board_skip_http(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("jira_client_org_test", _ROOT / "scripts" / "jira" / "jira_client.py")
    jc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jc)
    monkeypatch.setattr(jc, "BOARD_ID", None)
    monkeypatch.setattr(jc, "_get", lambda *a, **k: pytest.fail("must not call Jira"))
    assert jc.get_sprints() == []
    assert "JIRA_BOARD_ID not configured" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# People tables (cli.py) — gitignored people.json, "@owner" shorthand
# ---------------------------------------------------------------------------

def test_people_table_missing_file_is_empty(monkeypatch):
    import cli
    monkeypatch.setattr(cli, "_PEOPLE", {})
    assert cli._people_table("example") == {}


def test_people_table_resolves_owner_shorthand_and_skips_malformed(monkeypatch):
    import cli
    monkeypatch.setattr(cli, "_PEOPLE", {"mention_people": {"example": {
        "Owner": "@owner",
        "nowak": ["Nowak Ewa", "8:orgid:00000000-0000-0000-0000-000000000002"],
        "broken": ["only-a-name"],
    }}})
    assert cli._people_table("example") == {
        "owner": (cli.api_client.USER_DISPLAY, cli.api_client.USER_MRI),
        "nowak": ("Nowak Ewa", "8:orgid:00000000-0000-0000-0000-000000000002"),
    }


def test_people_example_file_is_fictional():
    raw = (_ROOT / "scripts" / "teams_agent" / "people.example.json").read_text(encoding="utf-8")
    _assert_fictional(raw)
    data = json.loads(raw)
    for table in data["mention_people"].values():
        for entry in table.values():
            assert entry == "@owner" or entry[1].startswith("8:orgid:00000000-")


def test_jira_fields_example_is_fictional():
    raw = (_ROOT / "config" / "jira-fields.example.json").read_text(encoding="utf-8")
    _assert_fictional(raw)


def test_name_noise_prefixes_are_folded_and_split():
    assert org_config.from_env({}).name_noise_prefixes == ()
    assert org_config.from_env({"NAME_NOISE_PREFIXES": " EXT, Vnd ,"}).name_noise_prefixes == ("ext", "vnd")
