"""Tests for scripts/teams_agent/polls.py.

Covers build_poll_card, parse_vote, and apply_vote.
Mocks: cli._save_control, cli.disable_chat, cli.enable_chat,
       cli._apply_chat_config_change, cli._save_chat_config.
"""

import sys
from pathlib import Path
from unittest.mock import patch

_TEAMS_DIR = str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent")
if _TEAMS_DIR not in sys.path:
    sys.path.insert(0, _TEAMS_DIR)

import polls  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CHAT_CONFIG = {
    "Kanal AI": {
        "conv_id": "19:chat06@thread.v2",
        "mcp": ["jira", "todos", "datetime"],
        "knowledge": None,
    },
    "Zespol Wewnetrzny": {
        "conv_id": "19:chat32@thread.v2",
        "mcp": ["jira", "todos", "knowledge"],
        "knowledge": ["projekty"],
    },
}


def _make_args(**kwargs):
    import argparse
    defaults = dict(model="claude-sonnet-4-6", caller="all", rate_limit=3)
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _fresh_chat_config():
    """Return a deep-copy of _CHAT_CONFIG for mutation tests."""
    import copy
    return copy.deepcopy(_CHAT_CONFIG)


# ---------------------------------------------------------------------------
# build_poll_card — global topics
# ---------------------------------------------------------------------------


class TestBuildPollCardModel:
    def test_returns_tuple(self):
        card, poll_id = polls.build_poll_card("model", {})
        assert isinstance(card, dict)
        assert isinstance(poll_id, str) and len(poll_id) == 32

    def test_has_three_buttons(self):
        card, _ = polls.build_poll_card("model", {})
        actions = card.get("actions", [])
        values = [a["data"]["value"] for a in actions if a.get("type") == "Action.Submit"]
        assert "haiku" in values
        assert "sonnet" in values
        assert "opus" in values
        assert len(values) == 3

    def test_poll_id_in_registry(self):
        polls._ACTIVE_POLLS.clear()
        _, poll_id = polls.build_poll_card("model", {})
        assert poll_id in polls._ACTIVE_POLLS

    def test_action_field(self):
        card, _ = polls.build_poll_card("model", {})
        actions = card.get("actions", [])
        for a in actions:
            assert a["data"]["action"] == "model"


class TestBuildPollCardPause:
    def test_four_buttons(self):
        card, _ = polls.build_poll_card("pause", {})
        actions = card.get("actions", [])
        assert len(actions) == 4
        values = {a["data"]["value"] for a in actions}
        assert {"30m", "1h", "2h", "cancel"} == values


class TestBuildPollCardOnoff:
    def test_three_buttons(self):
        card, _ = polls.build_poll_card("onoff", {})
        actions = card.get("actions", [])
        assert len(actions) == 3
        values = {a["data"]["value"] for a in actions}
        assert {"off", "on", "status"} == values


# ---------------------------------------------------------------------------
# build_poll_card — per-chat topics
# ---------------------------------------------------------------------------


class TestBuildPollCardChatOnoff:
    def test_returns_two_options(self):
        card, _ = polls.build_poll_card("chat Kanal AI onoff", _CHAT_CONFIG)
        actions = card.get("actions", [])
        assert len(actions) == 2
        values = {a["data"]["value"] for a in actions}
        assert {"on", "off"} == values

    def test_action_is_chat_onoff(self):
        card, _ = polls.build_poll_card("chat Kanal AI onoff", _CHAT_CONFIG)
        for a in card.get("actions", []):
            assert a["data"]["action"] == "chat_onoff"
            assert a["data"]["chat"] == "Kanal AI"


class TestBuildPollCardChatMcp:
    def test_five_choices(self):
        card, _ = polls.build_poll_card("chat Kanal AI mcp", _CHAT_CONFIG)
        # ChoiceSet is in body
        body_inputs = [b for b in card.get("body", []) if b.get("type") == "Input.ChoiceSet"]
        assert len(body_inputs) == 1
        choices = body_inputs[0]["choices"]
        values = [c["value"] for c in choices]
        assert set(values) == set(polls._MCP_CHOICES)

    def test_multi_select_true(self):
        card, _ = polls.build_poll_card("chat Kanal AI mcp", _CHAT_CONFIG)
        body_inputs = [b for b in card.get("body", []) if b.get("type") == "Input.ChoiceSet"]
        assert body_inputs[0]["isMultiSelect"] is True

    def test_default_value_from_config(self):
        cfg = {"Kanal AI": {"mcp": ["jira", "todos"]}}
        card, _ = polls.build_poll_card("chat Kanal AI mcp", cfg)
        body_inputs = [b for b in card.get("body", []) if b.get("type") == "Input.ChoiceSet"]
        default = body_inputs[0]["value"]
        # CSV order may vary, just check both present
        assert "jira" in default
        assert "todos" in default


class TestBuildPollCardChatCaller:
    def test_four_buttons(self):
        card, _ = polls.build_poll_card("chat Kanal AI caller", _CHAT_CONFIG)
        actions = card.get("actions", [])
        assert len(actions) == 4
        values = {a["data"]["value"] for a in actions}
        assert {"owner", "all", "auto", "clear"} == values

    def test_action_is_chat_caller(self):
        card, _ = polls.build_poll_card("chat Kanal AI caller", _CHAT_CONFIG)
        for a in card.get("actions", []):
            assert a["data"]["action"] == "chat_caller"


class TestBuildPollCardChatEpithets:
    def test_eight_epithet_choices_plus_none(self):
        card, _ = polls.build_poll_card("chat Kanal AI epithets", _CHAT_CONFIG)
        body_inputs = [b for b in card.get("body", []) if b.get("type") == "Input.ChoiceSet"]
        values = [c["value"] for c in body_inputs[0]["choices"]]
        # 8 epithets + __none__ sentinel
        assert "__none__" in values
        assert len(values) == len(polls._EPITHET_CHOICES) + 1
        for ep in polls._EPITHET_CHOICES:
            assert ep in values

    def test_multi_select_true(self):
        card, _ = polls.build_poll_card("chat Kanal AI epithets", _CHAT_CONFIG)
        body_inputs = [b for b in card.get("body", []) if b.get("type") == "Input.ChoiceSet"]
        assert body_inputs[0]["isMultiSelect"] is True

    def test_default_value_from_config(self):
        cfg = {"Kanal AI": {"epithets": ["zwiezly", "formalny"]}}
        card, _ = polls.build_poll_card("chat Kanal AI epithets", cfg)
        body_inputs = [b for b in card.get("body", []) if b.get("type") == "Input.ChoiceSet"]
        default = body_inputs[0]["value"]
        assert "zwiezly" in default
        assert "formalny" in default

    def test_empty_epithets_config_has_empty_default(self):
        cfg = {"Kanal AI": {}}
        card, _ = polls.build_poll_card("chat Kanal AI epithets", cfg)
        body_inputs = [b for b in card.get("body", []) if b.get("type") == "Input.ChoiceSet"]
        assert body_inputs[0]["value"] == ""


class TestBuildPollCardUnknownTopic:
    def test_raises_value_error(self):
        import pytest
        with pytest.raises(ValueError, match="Nieznany temat"):
            polls.build_poll_card("unknown_topic", {})

    def test_raises_for_unknown_subtopic(self):
        import pytest
        with pytest.raises(ValueError):
            polls.build_poll_card("chat Kanal AI unknown", {})


# ---------------------------------------------------------------------------
# parse_vote
# ---------------------------------------------------------------------------


class TestParseVote:
    def setup_method(self):
        polls._ACTIVE_POLLS.clear()

    def _register(self, poll_id: str, topic: str = "model") -> None:
        polls._ACTIVE_POLLS[poll_id] = {"topic": topic, "sent_ts": 0.0}

    def test_basic_single_value(self):
        self._register("abc123")
        result = polls.parse_vote({"poll_id": "abc123", "action": "model", "value": "haiku"})
        assert result is not None
        assert result["poll_id"] == "abc123"
        assert result["action"] == "model"
        assert result["value"] == "haiku"

    def test_missing_poll_id_returns_none(self):
        result = polls.parse_vote({"action": "model", "value": "haiku"})
        assert result is None

    def test_unknown_poll_id_returns_none(self):
        result = polls.parse_vote({"poll_id": "nonexistent", "action": "model", "value": "haiku"})
        assert result is None

    def test_multi_select_csv_string(self):
        self._register("mp1", "chat_mcp")
        result = polls.parse_vote({
            "poll_id": "mp1",
            "action": "chat_mcp",
            "values": "jira,todos",
            "chat": "Kanal AI",
        })
        assert result is not None
        assert result["values"] == ["jira", "todos"]

    def test_multi_select_list(self):
        self._register("mp2", "chat_mcp")
        result = polls.parse_vote({
            "poll_id": "mp2",
            "action": "chat_mcp",
            "values": ["jira", "todos"],
            "chat": "Kanal AI",
        })
        assert result["values"] == ["jira", "todos"]

    def test_chat_field_passed_through(self):
        self._register("cp1", "chat_onoff")
        result = polls.parse_vote({
            "poll_id": "cp1",
            "action": "chat_onoff",
            "value": "off",
            "chat": "Zespol Wewnetrzny",
        })
        assert result["chat"] == "Zespol Wewnetrzny"


# ---------------------------------------------------------------------------
# apply_vote
# ---------------------------------------------------------------------------


class TestApplyVotePause:
    def setup_method(self):
        polls._ACTIVE_POLLS.clear()

    def test_pause_30m(self):
        polls._ACTIVE_POLLS["p1"] = {"topic": "pause", "sent_ts": 0.0}
        args = _make_args()
        cfg = {}
        with patch("cli._save_control") as mock_save:
            result = polls.apply_vote(
                {"poll_id": "p1", "action": "pause", "value": "30m"},
                "conv1", cfg, args,
            )
        assert mock_save.called
        saved_ctrl = mock_save.call_args[0][0]
        assert saved_ctrl.get("disabled") is False
        assert "paused_until" in saved_ctrl
        assert "min" in result or "UTC" in result

    def test_pause_cancel(self):
        polls._ACTIVE_POLLS["p2"] = {"topic": "pause", "sent_ts": 0.0}
        args = _make_args()
        cfg = {}
        with patch("cli._save_control"), \
             patch("cli._load_control", return_value={}):
            result = polls.apply_vote(
                {"poll_id": "p2", "action": "pause", "value": "cancel"},
                "conv1", cfg, args,
            )
        assert "anulowana" in result.lower()


class TestApplyVoteModel:
    def setup_method(self):
        polls._ACTIVE_POLLS.clear()

    def test_model_haiku_sets_args(self):
        polls._ACTIVE_POLLS["m1"] = {"topic": "model", "sent_ts": 0.0}
        args = _make_args()
        polls.apply_vote(
            {"poll_id": "m1", "action": "model", "value": "haiku"},
            "conv1", {}, args,
        )
        assert "haiku" in args.model


class TestApplyVoteChatOnoff:
    def setup_method(self):
        polls._ACTIVE_POLLS.clear()

    def test_chat_off_calls_disable(self):
        polls._ACTIVE_POLLS["co1"] = {"topic": "chat_onoff", "sent_ts": 0.0}
        args = _make_args()
        with patch("cli.disable_chat") as mock_disable:
            result = polls.apply_vote(
                {"poll_id": "co1", "action": "chat_onoff",
                 "chat": "Zespol Wewnetrzny", "value": "off"},
                "conv1", {}, args,
            )
        mock_disable.assert_called_once_with("Zespol Wewnetrzny")
        assert "wylaczony" in result.lower()

    def test_chat_on_calls_enable(self):
        polls._ACTIVE_POLLS["co2"] = {"topic": "chat_onoff", "sent_ts": 0.0}
        args = _make_args()
        with patch("cli.enable_chat") as mock_enable:
            polls.apply_vote(
                {"poll_id": "co2", "action": "chat_onoff",
                 "chat": "Kanal AI", "value": "on"},
                "conv1", {}, args,
            )
        mock_enable.assert_called_once_with("Kanal AI")


class TestApplyVoteChatMcp:
    def setup_method(self):
        polls._ACTIVE_POLLS.clear()

    def test_sets_mcp_list(self):
        polls._ACTIVE_POLLS["mcp1"] = {"topic": "chat_mcp", "sent_ts": 0.0}
        args = _make_args()
        cfg = _fresh_chat_config()
        with patch("cli._save_chat_config") as mock_save:
            polls.apply_vote(
                {"poll_id": "mcp1", "action": "chat_mcp",
                 "chat": "Kanal AI", "values": ["jira", "todos"]},
                "conv1", cfg, args,
            )
        assert cfg["Kanal AI"]["mcp"] == ["jira", "todos"]
        assert mock_save.called


class TestApplyVoteChatCaller:
    def setup_method(self):
        polls._ACTIVE_POLLS.clear()

    def test_sets_caller_owner(self):
        polls._ACTIVE_POLLS["cl1"] = {"topic": "chat_caller", "sent_ts": 0.0}
        args = _make_args()
        cfg = _fresh_chat_config()
        with patch("cli._save_chat_config"):
            polls.apply_vote(
                {"poll_id": "cl1", "action": "chat_caller",
                 "chat": "Kanal AI", "value": "owner"},
                "conv1", cfg, args,
            )
        assert cfg["Kanal AI"]["caller"] == "owner"

    def test_clear_removes_caller_key(self):
        polls._ACTIVE_POLLS["cl2"] = {"topic": "chat_caller", "sent_ts": 0.0}
        args = _make_args()
        cfg = {"Kanal AI": {"mcp": None, "knowledge": None, "caller": "owner"}}
        with patch("cli._save_chat_config"):
            polls.apply_vote(
                {"poll_id": "cl2", "action": "chat_caller",
                 "chat": "Kanal AI", "value": "clear"},
                "conv1", cfg, args,
            )
        assert "caller" not in cfg["Kanal AI"]


class TestApplyVoteChatEpithets:
    def setup_method(self):
        polls._ACTIVE_POLLS.clear()

    def test_sets_epithets_list(self):
        polls._ACTIVE_POLLS["ep1"] = {"topic": "chat_epithets", "sent_ts": 0.0}
        args = _make_args()
        cfg = _fresh_chat_config()
        with patch("cli._save_chat_config"):
            polls.apply_vote(
                {"poll_id": "ep1", "action": "chat_epithets",
                 "chat": "Kanal AI", "values": ["zwiezly", "formalny"]},
                "conv1", cfg, args,
            )
        assert cfg["Kanal AI"]["epithets"] == ["zwiezly", "formalny"]

    def test_none_sentinel_removes_key(self):
        polls._ACTIVE_POLLS["ep2"] = {"topic": "chat_epithets", "sent_ts": 0.0}
        args = _make_args()
        cfg = {"Kanal AI": {"epithets": ["zwiezly"], "knowledge": None}}
        with patch("cli._save_chat_config"):
            polls.apply_vote(
                {"poll_id": "ep2", "action": "chat_epithets",
                 "chat": "Kanal AI", "values": ["__none__"]},
                "conv1", cfg, args,
            )
        assert "epithets" not in cfg["Kanal AI"]

    def test_empty_values_removes_key(self):
        polls._ACTIVE_POLLS["ep3"] = {"topic": "chat_epithets", "sent_ts": 0.0}
        args = _make_args()
        cfg = {"Kanal AI": {"epithets": ["rzeczowy"], "knowledge": None}}
        with patch("cli._save_chat_config"):
            polls.apply_vote(
                {"poll_id": "ep3", "action": "chat_epithets",
                 "chat": "Kanal AI", "values": []},
                "conv1", cfg, args,
            )
        assert "epithets" not in cfg["Kanal AI"]
