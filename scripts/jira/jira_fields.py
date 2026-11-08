"""config/jira-fields.json reader shared by jira_client.py and mcp_server.py.

Field names and keys are English. Configs written before the rename used Polish ones;
they still load: every old name is accepted as an alias, and when both the old and the
new name are present the English one wins.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import org_config  # noqa: E402

# Field names used in "extra_custom_fields" values and "write_fields" keys: old -> new.
FIELD_ALIASES = {
    "typ_pracy": "work_type",
    "dotyczy": "affects",
    "rodzaj_zmiany": "change_type",
    "numer_zamowienia": "order_number",
    "numer_projektu": "project_number",
    "stan_obecny": "current_state",
    "stan_docelowy": "target_state",
}
# Top-level keys: old -> new.
KEY_ALIASES = {
    "typ_pracy_options": "work_type_options",
    "rodzaj_zmiany_default": "change_type_default",
}


def _field(name: str) -> str:
    return FIELD_ALIASES.get(name, name)


def normalize(doc: dict | None) -> dict:
    """Return a copy of a jira-fields document with English keys and field names."""
    out = dict(doc or {})
    for old, new in KEY_ALIASES.items():
        if old in out:
            value = out.pop(old)
            out.setdefault(new, value)

    extra = out.get("extra_custom_fields")
    if isinstance(extra, dict):
        out["extra_custom_fields"] = {cf: _field(str(label)) for cf, label in extra.items()}

    write = out.get("write_fields")
    if isinstance(write, dict):
        renamed = {_field(k): v for k, v in write.items() if k in FIELD_ALIASES}
        renamed.update({k: v for k, v in write.items() if k not in FIELD_ALIASES})
        out["write_fields"] = renamed
    return out


def load() -> dict:
    """Read config/jira-fields.json (or $JIRA_FIELDS_FILE) and normalize it."""
    return normalize(org_config.load_json_config(
        "JIRA_FIELDS_FILE", org_config.REPO_ROOT / "config" / "jira-fields.json",
    ))
