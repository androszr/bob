"""Adaptive Card poll builder/parser for Bob agent configuration via Teams native polls.

Adaptive Card schema reference:
  - Adaptive Cards 1.5 spec: https://adaptivecards.io/explorer/
  - Teams chatsvc envelope: messagetype "RichText/Media_Card", properties.cards array.
  - Action.Submit vote-response shape observed in sniff:
      m["properties"]["cardAction"] = {"type": "Action.Submit", "data": {...}}
  - Multi-select ChoiceSet: values returned as comma-separated string in data field.
    Fallback: Input.Text with CSV pre-fill if multi-select is not supported.

In-memory poll registry (_ACTIVE_POLLS): keyed by poll_id (uuid4 hex).
Ephemeral — lost on agent restart (by design, per plan scope).

Public API:
    build_poll_card(topic, chat_config) -> tuple[dict, str]
    parse_vote(card_action)             -> dict | None
    apply_vote(vote, conv_id, chat_config, args) -> str
"""

import argparse
import time
import uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EPITHET_CHOICES = (
    "zwiezly",
    "formalny",
    "analityczny",
    "humorystyczny",
    "rzeczowy",
    "krotki",
    "szczegolowy",
    "empatyczny",
)

_MCP_CHOICES = ("jira", "todos")

# In-memory registry: poll_id -> {"topic": str, "creator_mri": str, "sent_ts": float}
_ACTIVE_POLLS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_poll_id() -> str:
    return uuid.uuid4().hex


def _card(title: str, body_items: list, poll_id: str) -> dict:
    """Wrap body items in an Adaptive Card 1.5 envelope."""
    return {
        "type": "AdaptiveCard",
        "version": "1.5",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": [
            {
                "type": "TextBlock",
                "text": title,
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
            },
            *body_items,
        ],
        "metadata": {"poll_id": poll_id},
    }


def _submit_button(label: str, data: dict) -> dict:
    return {
        "type": "Action.Submit",
        "title": label,
        "data": data,
    }


def _choice_set(
    choices: list[tuple[str, str]],
    data_key: str,
    poll_id: str,
    action: str,
    default_values: list[str] | None = None,
    multi: bool = True,
    extra_data: dict | None = None,
) -> tuple[list, list]:
    """Build an Input.ChoiceSet + Submit button.

    Returns (body_items, actions) pair.
    choices: list of (title, value) tuples.
    default_values: pre-selected values (CSV string for Adaptive Card value field).
    """
    default_csv = ",".join(default_values) if default_values else ""
    choice_items = [{"title": t, "value": v} for t, v in choices]
    body_items = [
        {
            "type": "Input.ChoiceSet",
            "id": data_key,
            "isMultiSelect": multi,
            "value": default_csv,
            "choices": choice_items,
            "wrap": True,
        }
    ]
    submit_data: dict = {"poll_id": poll_id, "action": action, data_key: f"{{{data_key}}}"}
    if extra_data:
        submit_data.update(extra_data)
    actions = [
        {
            "type": "Action.Submit",
            "title": "Zatwierdz",
            "data": submit_data,
        }
    ]
    return body_items, actions


# ---------------------------------------------------------------------------
# build_poll_card
# ---------------------------------------------------------------------------

def build_poll_card(topic: str, chat_config: dict) -> tuple[dict, str]:
    """Build an Adaptive Card payload for the given topic.

    Returns (card_dict, poll_id).
    Raises ValueError if topic is not recognized.

    Supported topics:
      model, pause, onoff,
      chat <name> onoff, chat <name> mcp, chat <name> knowledge,
      chat <name> caller, chat <name> epithets
    """
    poll_id = _new_poll_id()
    topic_l = topic.strip().lower()

    # --- Global: model ---
    if topic_l == "model":
        actions = [
            _submit_button("haiku", {"poll_id": poll_id, "action": "model", "value": "haiku"}),
            _submit_button("sonnet", {"poll_id": poll_id, "action": "model", "value": "sonnet"}),
            _submit_button("opus", {"poll_id": poll_id, "action": "model", "value": "opus"}),
        ]
        card = {
            **_card("Wybierz model Claude:", [], poll_id),
            "actions": actions,
        }

    # --- Global: pause ---
    elif topic_l == "pause":
        actions = [
            _submit_button("30 min", {"poll_id": poll_id, "action": "pause", "value": "30m"}),
            _submit_button("1 godz", {"poll_id": poll_id, "action": "pause", "value": "1h"}),
            _submit_button("2 godz", {"poll_id": poll_id, "action": "pause", "value": "2h"}),
            _submit_button("Anuluj pause", {"poll_id": poll_id, "action": "pause", "value": "cancel"}),
        ]
        card = {
            **_card("Pauza Boba:", [], poll_id),
            "actions": actions,
        }

    # --- Global: onoff ---
    elif topic_l == "onoff":
        actions = [
            _submit_button("Wylacz (off)", {"poll_id": poll_id, "action": "onoff", "value": "off"}),
            _submit_button("Wlacz (on)", {"poll_id": poll_id, "action": "onoff", "value": "on"}),
            _submit_button("Status", {"poll_id": poll_id, "action": "onoff", "value": "status"}),
        ]
        card = {
            **_card("Wlacz/wylacz Boba:", [], poll_id),
            "actions": actions,
        }

    # --- Per-chat topics: chat <name> <sub-topic> ---
    elif topic_l.startswith("chat "):
        rest = topic_l[len("chat "):].strip()
        # Detect sub-topic suffix
        _sub_topics = ("onoff", "mcp", "knowledge", "caller", "epithets")
        sub = None
        chat_name_raw = rest
        for st in _sub_topics:
            if rest.endswith(" " + st):
                sub = st
                chat_name_raw = rest[: -(len(st) + 1)].strip()
                break
        if sub is None:
            raise ValueError(
                f"Nieznany sub-topic: {rest!r}. Dostepne: {', '.join(_sub_topics)}"
            )
        # Reconstruct original-case chat name from chat_config keys (case-insensitive match).
        chat_name = chat_name_raw
        for key in chat_config:
            if key.lower() == chat_name_raw.lower():
                chat_name = key
                break

        if sub == "onoff":
            actions = [
                _submit_button(
                    "Wlacz",
                    {"poll_id": poll_id, "action": "chat_onoff", "chat": chat_name, "value": "on"},
                ),
                _submit_button(
                    "Wylacz",
                    {"poll_id": poll_id, "action": "chat_onoff", "chat": chat_name, "value": "off"},
                ),
            ]
            card = {
                **_card(f"Wlacz/wylacz chat: {chat_name}", [], poll_id),
                "actions": actions,
            }

        elif sub == "mcp":
            current_mcp = (chat_config.get(chat_name) or {}).get("mcp")
            if current_mcp is None:
                default_values = list(_MCP_CHOICES)
            else:
                default_values = list(current_mcp)
            choices = [(g, g) for g in _MCP_CHOICES]
            body_items, actions = _choice_set(
                choices, "values", poll_id, "chat_mcp",
                default_values=default_values,
                extra_data={"chat": chat_name},
            )
            card = {
                **_card(f"Grupy MCP dla chatu: {chat_name}", body_items, poll_id),
                "actions": actions,
            }

        elif sub == "caller":
            actions = [
                _submit_button(
                    "owner",
                    {"poll_id": poll_id, "action": "chat_caller", "chat": chat_name, "value": "owner"},
                ),
                _submit_button(
                    "all",
                    {"poll_id": poll_id, "action": "chat_caller", "chat": chat_name, "value": "all"},
                ),
                _submit_button(
                    "auto",
                    {"poll_id": poll_id, "action": "chat_caller", "chat": chat_name, "value": "auto"},
                ),
                _submit_button(
                    "clear (fallback globalny)",
                    {"poll_id": poll_id, "action": "chat_caller", "chat": chat_name, "value": "clear"},
                ),
            ]
            card = {
                **_card(f"Caller mode dla chatu: {chat_name}", [], poll_id),
                "actions": actions,
            }

        elif sub == "epithets":
            current_ep = (chat_config.get(chat_name) or {}).get("epithets")
            if current_ep is None:
                default_values = []
            else:
                default_values = list(current_ep)
            choices = [("Wyczyszczone (brak traitow)", "__none__")]
            choices += [(e, e) for e in _EPITHET_CHOICES]
            body_items, actions = _choice_set(
                choices, "values", poll_id, "chat_epithets",
                default_values=default_values,
                extra_data={"chat": chat_name},
            )
            card = {
                **_card(f"Personality epithets dla chatu: {chat_name}", body_items, poll_id),
                "actions": actions,
            }

        else:
            raise ValueError(f"Nieznany sub-topic: {sub!r}")

    else:
        raise ValueError(
            f"Nieznany temat: {topic!r}. Dostepne: model, pause, onoff, "
            f"chat <name> onoff|mcp|knowledge|caller|epithets"
        )

    _ACTIVE_POLLS[poll_id] = {
        "topic": topic,
        "sent_ts": time.time(),
    }
    return card, poll_id


# ---------------------------------------------------------------------------
# parse_vote
# ---------------------------------------------------------------------------

def parse_vote(card_action: dict) -> dict | None:
    """Extract structured vote from an Adaptive Card Action.Submit payload.

    Returns dict with keys: poll_id, action, and either value (str) or values (list[str]).
    Returns None if poll_id is missing or the poll is not in _ACTIVE_POLLS (expired).

    Multi-select ChoiceSet values arrive as a comma-separated string in the 'values' key.
    The function splits them into a list.
    """
    if not isinstance(card_action, dict):
        return None
    poll_id = card_action.get("poll_id")
    if not poll_id:
        return None
    if poll_id not in _ACTIVE_POLLS:
        return None
    action = card_action.get("action")
    if not action:
        return None

    # Multi-select actions carry their data in a 'values' key (CSV string or list).
    _multi_actions = {"chat_mcp", "chat_knowledge", "chat_epithets"}
    if action in _multi_actions:
        raw = card_action.get("values", "")
        if isinstance(raw, list):
            values = [v.strip() for v in raw if str(v).strip()]
        elif isinstance(raw, str):
            values = [v.strip() for v in raw.split(",") if v.strip()]
        else:
            values = []
        result: dict = {"poll_id": poll_id, "action": action, "values": values}
    else:
        result = {"poll_id": poll_id, "action": action, "value": card_action.get("value", "")}

    # Per-chat actions carry the chat name.
    if "chat" in card_action:
        result["chat"] = card_action["chat"]

    return result


# ---------------------------------------------------------------------------
# apply_vote
# ---------------------------------------------------------------------------

def apply_vote(
    vote: dict,
    conv_id: str,
    chat_config: dict,
    args: argparse.Namespace,
) -> str:
    """Execute the configuration change described by vote.

    Modifies chat_config in-place and persists via cli helpers.
    Returns a human-readable confirmation string.

    Imports cli helpers lazily to avoid circular imports (polls is imported by cli).
    """
    # Lazy import to avoid circular dependency (cli imports polls).
    import cli as _cli  # noqa: PLC0415

    action = vote.get("action", "")
    poll_id = vote.get("poll_id", "")

    # Remove from registry once processed.
    _ACTIVE_POLLS.pop(poll_id, None)

    # --- model ---
    if action == "model":
        value = vote.get("value", "")
        model_id = _cli.ai.MODEL_ALIASES.get(value, value)
        args.model = model_id
        return f"Model zmieniony na: {model_id}."

    # --- pause ---
    elif action == "pause":
        value = vote.get("value", "")
        if value == "cancel":
            ctrl = _cli._load_control()
            ctrl["paused_until"] = None
            _cli._save_control(ctrl)
            return "Pauza anulowana."
        elif value.endswith("h"):
            minutes = int(value[:-1]) * 60
        elif value.endswith("m"):
            minutes = int(value[:-1])
        else:
            return f"Nieznana wartosc pause: {value!r}."
        from datetime import UTC, datetime, timedelta
        until_dt = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=minutes)
        _cli._save_control({"paused_until": until_dt.isoformat(), "disabled": False})
        return f"Pauza ustawiona do {until_dt.strftime('%H:%M')} UTC ({minutes} min)."

    # --- onoff ---
    elif action == "onoff":
        value = vote.get("value", "")
        if value == "off":
            _cli._save_control({"disabled": True, "paused_until": None})
            return "Bob wylaczony (off)."
        elif value == "on":
            _cli._save_control({"disabled": False, "paused_until": None})
            return "Bob wlaczony (on)."
        elif value == "status":
            paused, reason = _cli._is_paused()
            return f"Status: {'pauza — ' + reason if paused else 'aktywny'}."
        return f"Nieznana wartosc onoff: {value!r}."

    # --- chat onoff ---
    elif action == "chat_onoff":
        chat_name = vote.get("chat", "")
        value = vote.get("value", "")
        if value == "off":
            _cli.disable_chat(chat_name)
            return f"Chat '{chat_name}' wylaczony."
        elif value == "on":
            _cli.enable_chat(chat_name)
            return f"Chat '{chat_name}' wlaczony."
        return f"Nieznana wartosc chat_onoff: {value!r}."

    # --- chat mcp ---
    elif action == "chat_mcp":
        chat_name = vote.get("chat", "")
        values = vote.get("values", [])
        mcp_val = None if not values else list(values)
        _cli._apply_chat_config_change(chat_name, "mcp", mcp_val, chat_config)
        ack_val = "wszystkie" if mcp_val is None else (", ".join(mcp_val) or "brak")
        return f"MCP dla '{chat_name}' zmienione na: {ack_val}."

    # --- chat caller ---
    elif action == "chat_caller":
        chat_name = vote.get("chat", "")
        value = vote.get("value", "")
        caller_val = None if value == "clear" else value
        _cli._apply_chat_config_change(chat_name, "caller", caller_val, chat_config)
        ack_val = "usuniety (fallback do globalnego)" if caller_val is None else caller_val
        return f"Caller dla '{chat_name}' zmieniony na: {ack_val}."

    # --- chat epithets ---
    elif action == "chat_epithets":
        chat_name = vote.get("chat", "")
        values = vote.get("values", [])
        if not values or values == ["__none__"]:
            ep_val = None
        else:
            ep_val = [v for v in values if v != "__none__"] or None
        _cli._apply_chat_config_change(chat_name, "epithets", ep_val, chat_config)
        ack_val = "wyczyszczone (default prompt)" if ep_val is None else ", ".join(ep_val)
        return f"Epithets dla '{chat_name}' zmienione na: {ack_val}."

    return f"Nieznana akcja: {action!r}."
