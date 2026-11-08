"""config/jira-fields.json reader: English keys, older Polish keys still load."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "jira"))
import jira_fields  # noqa: E402
from test_jira_mcp_server import _load_jira_mcp, _make_jira_stubs  # noqa: E402

_OLD_STYLE = {
    "extra_custom_fields": {"customfield_1": "typ_pracy", "customfield_2": "dotyczy", "customfield_9": "custom"},
    "issue_type_ids": {"PRJ1": {"task": "3"}},
    "write_fields": {
        "typ_pracy": "customfield_1", "dotyczy": "customfield_2", "rodzaj_zmiany": "customfield_3",
        "numer_zamowienia": "customfield_4", "numer_projektu": "customfield_5",
        "stan_obecny": "customfield_6", "stan_docelowy": "customfield_7",
    },
    "typ_pracy_options": {"Development": "90003"},
    "rodzaj_zmiany_default": "90010",
}


def test_old_polish_keys_are_read_as_english():
    doc = jira_fields.normalize(_OLD_STYLE)
    assert doc["extra_custom_fields"] == {"customfield_1": "work_type", "customfield_2": "affects",
                                          "customfield_9": "custom"}
    assert doc["write_fields"] == {
        "work_type": "customfield_1", "affects": "customfield_2", "change_type": "customfield_3",
        "order_number": "customfield_4", "project_number": "customfield_5",
        "current_state": "customfield_6", "target_state": "customfield_7",
    }
    assert doc["work_type_options"] == {"Development": "90003"}
    assert doc["change_type_default"] == "90010"
    assert "typ_pracy_options" not in doc and "rodzaj_zmiany_default" not in doc
    assert doc["issue_type_ids"] == {"PRJ1": {"task": "3"}}


def test_english_wins_when_both_names_are_present():
    doc = jira_fields.normalize({
        "write_fields": {"typ_pracy": "customfield_old", "work_type": "customfield_new"},
        "typ_pracy_options": {"Old": "1"}, "work_type_options": {"New": "2"},
        "rodzaj_zmiany_default": "1", "change_type_default": "2",
    })
    assert doc["write_fields"] == {"work_type": "customfield_new"}
    assert doc["work_type_options"] == {"New": "2"}
    assert doc["change_type_default"] == "2"


def test_missing_or_empty_config_is_an_empty_dict():
    assert jira_fields.normalize(None) == {}
    assert jira_fields.normalize({}) == {}


def test_load_reads_an_old_style_file(tmp_path, monkeypatch):
    path = tmp_path / "jira-fields.json"
    path.write_text(json.dumps(_OLD_STYLE), encoding="utf-8")
    monkeypatch.setenv("JIRA_FIELDS_FILE", str(path))
    assert jira_fields.load()["write_fields"]["work_type"] == "customfield_1"


def test_mcp_server_writes_english_fields_from_an_old_style_config(tmp_path, monkeypatch):
    path = tmp_path / "jira-fields.json"
    path.write_text(json.dumps(_OLD_STYLE), encoding="utf-8")
    monkeypatch.setenv("JIRA_FIELDS_FILE", str(path))
    mod = _load_jira_mcp(_make_jira_stubs())
    captured: dict = {}

    def _put(url, data=None, **kwargs):
        captured["fields"] = json.loads(data)["fields"]
        return type("R", (), {"status_code": 204})()

    mod.requests.put = _put
    assert mod.jira_update_ticket(issue_key="PRJ1-1", work_type="development", affects="FieldOps",
                                  order_number="PO-1") == "OK"
    assert captured["fields"] == {"customfield_1": {"id": "90003"}, "customfield_2": ["FieldOps"],
                                  "customfield_4": "PO-1"}
