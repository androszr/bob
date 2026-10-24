"""Bob's own mascot gifs: [BOB_GIF: mood] reaction tag (bob_gifs.py + cli gif planning).

Gif files live outside the repo (BOB_GIF_DIR); tests point bob_gifs.GIF_DIR at a tmp
dir so they never depend on a real gif folder.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_TEAMS_DIR = str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent")
if _TEAMS_DIR not in sys.path:
    sys.path.insert(0, _TEAMS_DIR)

import bob_gifs  # noqa: E402
import cli  # noqa: E402


@pytest.fixture(autouse=True)
def gif_dir(tmp_path, monkeypatch):
    d = tmp_path / "gif"
    d.mkdir()
    for name in bob_gifs.REACTION_MOODS.values():
        (d / name).write_bytes(b"GIF89a")
    monkeypatch.setattr(bob_gifs, "GIF_DIR", d)
    return d


@pytest.fixture()
def senders(monkeypatch):
    ams = MagicMock(return_value="msg-1")
    giphy = MagicMock(return_value=True)
    monkeypatch.setattr(cli.api_client, "send_ams_gif_message", ams)
    monkeypatch.setattr(cli.api_client, "send_gif", giphy)
    return ams, giphy


def test_unset_gif_dir_means_no_reaction_gifs(monkeypatch):
    import importlib  # noqa: PLC0415

    monkeypatch.delenv("BOB_GIF_DIR", raising=False)
    fresh = importlib.reload(bob_gifs)
    assert fresh.GIF_DIR is None
    assert fresh.gif_path("bob-happy.gif") is None
    assert fresh.pick_reaction_gif_and_phrase("happy") is None
    monkeypatch.setenv("BOB_GIF_DIR", "/x/gifs")
    assert importlib.reload(bob_gifs).GIF_DIR == Path("/x/gifs")
    monkeypatch.delenv("BOB_GIF_DIR")
    importlib.reload(bob_gifs)


def test_pick_reaction_returns_pair_for_every_mood(gif_dir):
    for mood, gif in bob_gifs.REACTION_MOODS.items():
        name, phrase = bob_gifs.pick_reaction_gif_and_phrase(mood.upper())
        assert name == gif and phrase in bob_gifs._REACTION_PHRASES[mood]
        assert bob_gifs.gif_path(name) == gif_dir / gif


def test_pick_reaction_unknown_mood_or_missing_file_returns_none(gif_dir):
    assert bob_gifs.pick_reaction_gif_and_phrase("nonexistent") is None
    (gif_dir / "bob-happy.gif").unlink()
    assert bob_gifs.pick_reaction_gif_and_phrase("happy") is None


def test_plan_gifs_bob_mood_returns_plan_and_strips_tag():
    cleaned, plan = cli._plan_gifs("Zrobione! [BOB_GIF: happy]", persona=None)
    assert cleaned == "Zrobione!"
    assert plan == {"kind": "bob_gif", "mood": "happy", "gif_name": "bob-happy.gif", "phrase": plan["phrase"]}


@pytest.mark.parametrize("persona", ["example", "nie-ma-takiej-persony"])
def test_plan_gifs_personas_skip_bob_mascot_but_strip_tag(persona):
    cleaned, plan = cli._plan_gifs("Gotowe. [BOB_GIF: happy]", persona=persona)
    assert cleaned == "Gotowe." and plan is None


def test_plan_gifs_bob_precedence_drops_giphy():
    cleaned, plan = cli._plan_gifs("No i pieknie. [BOB_GIF: happy] [GIF: dog dancing]", persona=None)
    assert cleaned == "No i pieknie." and plan["kind"] == "bob_gif"


def test_plan_gifs_unknown_mood_falls_back_to_giphy_or_nothing():
    assert cli._plan_gifs("Hmm. [BOB_GIF: nonexistent_mood]", persona=None) == ("Hmm.", None)
    cleaned, plan = cli._plan_gifs("Hmm. [BOB_GIF: nope] [GIF: dog dancing]", persona=None)
    assert cleaned == "Hmm." and plan == {"kind": "giphy", "queries": ["dog dancing"]}


def test_extract_and_send_gifs_bob_mood_sends_via_ams(senders, gif_dir):
    ams, giphy = senders
    cleaned = cli._extract_and_send_gifs("Zrobione! [BOB_GIF: happy]", conv_id="19:abc", persona=None)
    assert cleaned == "Zrobione!"
    caption, gif_file, conv_id = ams.call_args[0]
    assert "[Bob]" in caption and caption.startswith("<p>")
    assert gif_file == gif_dir / "bob-happy.gif" and conv_id == "19:abc"
    giphy.assert_not_called()


def test_extract_and_send_gifs_giphy_path_intact(senders):
    ams, giphy = senders
    assert cli._extract_and_send_gifs("Patrz. [GIF: dog dancing]", conv_id="19:abc", persona=None) == "Patrz."
    ams.assert_not_called()
    giphy.assert_called_once_with("dog dancing", "19:abc", persona=None)


def test_execute_gif_plan_ams_failure_is_silent(senders):
    ams, _ = senders
    ams.return_value = None
    cli._execute_gif_plan(
        {"kind": "bob_gif", "mood": "oops", "gif_name": "bob-hat-mishap.gif", "phrase": "ups <b>"},
        "19:abc", persona=None,
    )
    assert "ups &lt;b&gt;" in ams.call_args[0][0]  # phrase is HTML-escaped


def test_bob_prompt_offers_bob_gif_moods():
    prompt = (Path(_TEAMS_DIR) / "prompts" / "bob.md").read_text(encoding="utf-8")
    assert "[BOB_GIF: mood]" in prompt
    for mood in bob_gifs.REACTION_MOODS:
        assert mood in prompt, mood


def test_ic3_interceptor_captures_ams_bearer_only(monkeypatch):
    captured = MagicMock()
    monkeypatch.setattr(cli.api_client, "set_ic3_token", captured)
    page = MagicMock()
    cli._attach_ic3_token_interceptor(page)
    handler = page.on.call_args[0][1]
    handler(MagicMock(url="https://teams.microsoft.com/api/x", headers={"authorization": "Bearer a"}))
    handler(MagicMock(url="https://eu-prod.asyncgw.teams.microsoft.com/v1", headers={"authorization": "Bearer ic3"}))
    captured.assert_called_once_with("ic3")


def test_waiting_mood_is_the_fishing_frog_and_gif_path_is_a_real_path(gif_dir):
    assert bob_gifs.REACTION_MOODS["waiting"] == "bob-fishing.gif"
    name, phrase = bob_gifs.pick_reaction_gif_and_phrase("waiting")
    assert name == "bob-fishing.gif" and phrase in bob_gifs._REACTION_PHRASES["waiting"]
    assert bob_gifs.gif_path(name) == gif_dir / "bob-fishing.gif"
    assert set(bob_gifs._REACTION_PHRASES) == set(bob_gifs.REACTION_MOODS)
