"""Tests for sharepoint_meta: has_changed / load_state / save_state (no live Playwright)."""
import json

from scripts.teams_agent import sharepoint_meta


def test_has_changed_true_when_key_missing():
    assert sharepoint_meta.has_changed("pionierzy", "2026-07-28T19:08:17Z", {}) is True


def test_has_changed_false_when_same_mtime():
    state = {"pionierzy": "2026-07-28T19:08:17Z"}
    assert sharepoint_meta.has_changed("pionierzy", "2026-07-28T19:08:17Z", state) is False


def test_has_changed_true_when_mtime_differs():
    state = {"pionierzy": "2026-07-28T19:08:17Z"}
    assert sharepoint_meta.has_changed("pionierzy", "2026-07-29T08:00:00Z", state) is True


def test_has_changed_true_when_mtime_reverted_to_earlier_value():
    """Plain inequality, not a > comparison -- a revert must also trigger a re-pull."""
    state = {"pionierzy": "2026-07-29T08:00:00Z"}
    assert sharepoint_meta.has_changed("pionierzy", "2026-07-28T19:08:17Z", state) is True


def test_has_changed_only_checks_its_own_key():
    state = {"rada": "2026-07-28T19:08:17Z"}
    assert sharepoint_meta.has_changed("pionierzy", "2026-07-28T19:08:17Z", state) is True


def test_load_state_missing_file_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(sharepoint_meta, "STATE_FILE", tmp_path / "nope.json")
    assert sharepoint_meta.load_state() == {}


def test_load_state_corrupt_file_returns_empty_dict(tmp_path, monkeypatch):
    state_file = tmp_path / "sharepoint_mtimes.json"
    state_file.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(sharepoint_meta, "STATE_FILE", state_file)
    assert sharepoint_meta.load_state() == {}


def test_save_state_then_load_state_roundtrip(tmp_path, monkeypatch):
    state_file = tmp_path / "sharepoint_mtimes.json"
    monkeypatch.setattr(sharepoint_meta, "STATE_FILE", state_file)
    sharepoint_meta.save_state({"pionierzy": "2026-07-28T19:08:17Z"})
    assert sharepoint_meta.load_state() == {"pionierzy": "2026-07-28T19:08:17Z"}


def test_save_state_writes_atomically_no_leftover_tmp_file(tmp_path, monkeypatch):
    state_file = tmp_path / "sharepoint_mtimes.json"
    monkeypatch.setattr(sharepoint_meta, "STATE_FILE", state_file)
    sharepoint_meta.save_state({"rada": "x"})
    assert not state_file.with_suffix(".tmp.json").exists()


def test_mark_processed_merges_into_existing_state(tmp_path, monkeypatch):
    state_file = tmp_path / "sharepoint_mtimes.json"
    state_file.write_text(json.dumps({"rada": "old"}), encoding="utf-8")
    monkeypatch.setattr(sharepoint_meta, "STATE_FILE", state_file)
    sharepoint_meta.mark_processed("pionierzy", "2026-07-28T19:08:17Z")
    assert json.loads(state_file.read_text(encoding="utf-8")) == {
        "rada": "old",
        "pionierzy": "2026-07-28T19:08:17Z",
    }


def test_mark_processed_overwrites_existing_key(tmp_path, monkeypatch):
    state_file = tmp_path / "sharepoint_mtimes.json"
    state_file.write_text(json.dumps({"pionierzy": "old"}), encoding="utf-8")
    monkeypatch.setattr(sharepoint_meta, "STATE_FILE", state_file)
    sharepoint_meta.mark_processed("pionierzy", "new")
    assert json.loads(state_file.read_text(encoding="utf-8")) == {"pionierzy": "new"}
