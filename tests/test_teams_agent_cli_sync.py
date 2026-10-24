"""Features ported from the private agent into scripts/teams_agent/cli.py: hold timing, reactions, the
"still working" note, persistent timers, receive-only chats, weekly jobs, file memory, token-health planning."""

import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_TEAMS_DIR = str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent")
if _TEAMS_DIR not in sys.path:
    sys.path.insert(0, _TEAMS_DIR)

import api_client  # noqa: E402
import bob_files  # noqa: E402
import cli  # noqa: E402

# ---------------------------------------------------------------------------
# hold: measured from the message's own timestamp
# ---------------------------------------------------------------------------


def test_hold_runs_from_the_message_timestamp_not_from_our_start():
    assert cli._hold_wait_s(60, msg_ts=1000.0, now=1010.0, invited=False) == 50.0
    assert cli._hold_wait_s(60, msg_ts=1000.0, now=1200.0, invited=False) == 0.0      # it already sat in the queue
    assert cli._hold_wait_s(60, msg_ts=0.0, now=1000.0, invited=False) == 60.0        # unknown timestamp: full hold


def test_an_invited_reply_or_no_hold_never_waits():
    assert cli._hold_wait_s(60, 1000.0, 1001.0, invited=True) == 0.0
    assert cli._hold_wait_s(0, 1000.0, 1001.0, invited=False) == 0.0


def test_continuation_window_is_two_minutes():
    assert cli._CONTINUATION_WINDOW_S == 120
    assert cli._parse_teams_ts("2026-10-24T10:00:00Z") > 0 and cli._parse_teams_ts("nonsense") == 0.0


# ---------------------------------------------------------------------------
# [BOB-REAKCJA]
# ---------------------------------------------------------------------------


def test_reaction_token_puts_a_thumbs_up_and_strips_itself(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.api_client, "add_reaction", lambda *a: calls.append(a) or True)
    assert cli._handle_reaction("[BOB-REAKCJA]", "m1", "conv") == ""
    assert calls == [("m1", "conv", "like")]
    assert cli._handle_reaction("Zrobione. [bob-reakcja]", "m2", "conv") == "Zrobione."
    assert cli._is_silent_reply(cli._handle_reaction("[BOB-REAKCJA]", "m3", "conv"))    # nothing is sent


def test_reply_without_the_token_is_untouched_and_a_missing_id_reacts_to_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.api_client, "add_reaction", lambda *a: calls.append(a) or True)
    assert cli._handle_reaction("Zwykła odpowiedź", "m1", "conv") == "Zwykła odpowiedź"
    assert cli._handle_reaction("[BOB-REAKCJA]", "", "conv") == "" and calls == []


# ---------------------------------------------------------------------------
# "still working"
# ---------------------------------------------------------------------------


def test_still_working_posts_edits_and_marks_done_only_when_a_reply_went_out(monkeypatch):
    sent, edited = [], []
    monkeypatch.setattr(cli.api_client, "send_html_message", lambda html, conv: sent.append((html, conv)) or "m-1")
    monkeypatch.setattr(cli.api_client, "edit_message", lambda mid, html, conv: edited.append((mid, html)) or True)
    sw = cli._StillWorking("conv-1", first_s=0.05, every_s=0.05)
    with sw:
        deadline = time.time() + 2
        while len(edited) < 1 and time.time() < deadline:
            time.sleep(0.02)
    assert sent and sent[0][1] == "conv-1" and "Nadal pracuję" in sent[0][0]
    assert not any("Gotowe" in h for _, h in edited)                    # leaving the block says nothing yet
    sw.finish(True)
    assert edited[-1][0] == "m-1" and "Gotowe" in edited[-1][1]


def test_a_dropped_reply_deletes_the_note_and_falls_back_to_an_edit(monkeypatch):
    deleted, edited = [], []
    monkeypatch.setattr(cli.api_client, "send_html_message", lambda html, conv: "m-9")
    monkeypatch.setattr(cli.api_client, "edit_message", lambda mid, html, conv: edited.append(html) or True)
    monkeypatch.setattr(cli.api_client, "delete_message", lambda mid, conv: deleted.append(mid) or True)
    sw = cli._StillWorking("conv-1", first_s=0.01, every_s=5)
    with sw:
        time.sleep(0.15)
    sw.finish(False)
    assert deleted == ["m-9"] and not any("Gotowe" in h for h in edited)
    sw2 = cli._StillWorking("conv-1", first_s=0.01, every_s=5)
    monkeypatch.setattr(cli.api_client, "delete_message", lambda mid, conv: False)
    with sw2:
        time.sleep(0.15)
    sw2.finish(False)
    assert "bez odpowiedzi" in edited[-1] and "Gotowe" not in edited[-1]


def test_a_failing_reply_never_says_the_answer_is_below(monkeypatch):
    monkeypatch.setattr(cli.api_client, "send_html_message", lambda html, conv: "m-2")
    monkeypatch.setattr(cli.api_client, "edit_message", lambda *a: pytest.fail("no 'done' edit"))
    deleted = []
    monkeypatch.setattr(cli.api_client, "delete_message", lambda mid, conv: deleted.append(mid) or True)
    with pytest.raises(RuntimeError), cli._StillWorking("conv-1", first_s=0.01, every_s=5):
        time.sleep(0.15)
        raise RuntimeError("claude timeout")
    assert deleted == ["m-2"]


def test_an_uninvited_reply_gets_no_note(monkeypatch):
    monkeypatch.setattr(cli.api_client, "send_html_message", lambda *a: pytest.fail("no note when uninvited"))
    sw = cli._StillWorking("conv-1", first_s=0.01, every_s=0.01, enabled=False)
    with sw:
        time.sleep(0.1)
    sw.finish(True)


def test_a_fast_reply_posts_nothing(monkeypatch):
    monkeypatch.setattr(cli.api_client, "send_html_message", lambda *a: pytest.fail("no note for a fast reply"))
    monkeypatch.setattr(cli.api_client, "edit_message", lambda *a: pytest.fail("nothing to edit"))
    with cli._StillWorking("conv-1", first_s=30, every_s=30):
        pass


def test_still_working_never_raises_when_teams_fails(monkeypatch):
    def boom(*a):
        raise RuntimeError("teams down")
    monkeypatch.setattr(cli.api_client, "send_html_message", boom)
    with cli._StillWorking("conv-1", first_s=0.01, every_s=0.01):
        time.sleep(0.1)


def test_chat_replies_get_a_long_timeout():
    assert cli._CHAT_REPLY_TIMEOUT_S == 25 * 60 and cli._HISTORY_COMPACT_CHARS == 20000


# ---------------------------------------------------------------------------
# persistent timers
# ---------------------------------------------------------------------------


def test_timers_persist_across_restarts_and_survive_a_broken_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_TIMERS_FILE", tmp_path / "timers-state.json")
    assert cli._timer_last("sharepoint") == 0.0
    cli._timer_mark("sharepoint", 123.5)
    cli._timer_mark("sp_session_warm", 456.0)
    assert cli._timer_last("sharepoint") == 123.5 and cli._timer_last("sp_session_warm") == 456.0
    (tmp_path / "timers-state.json").write_text("{kaput")
    assert cli._timer_last("sharepoint") == 0.0
    cli._timer_mark("sharepoint", 7.0)                                  # writes over the broken file
    assert cli._timer_last("sharepoint") == 7.0
    (tmp_path / "timers-state.json").write_text("[1, 2]")
    assert cli._timer_last("sharepoint") == 0.0


# ---------------------------------------------------------------------------
# receive-only chats
# ---------------------------------------------------------------------------

_CFG = {"Czytany": {"conv_id": "19:ro@thread.v2", "ingest_only": True}, "Zwykly": {"conv_id": "19:rw@thread.v2"},
        "Bez id": {"ingest_only": True}}


def test_ingest_only_chats_are_found_in_the_config():
    assert cli._ingest_only_for("Czytany", _CFG) and not cli._ingest_only_for("Zwykly", _CFG)
    assert not cli._ingest_only_for("Nie ma", _CFG)
    assert cli._receive_only_conv_ids(_CFG) == {"19:ro@thread.v2"}


def test_refresh_rebinds_the_set_in_one_step_and_the_api_refuses_writes(monkeypatch):
    monkeypatch.setattr(api_client, "RECEIVE_ONLY_CONV_IDS", frozenset())
    before = api_client.RECEIVE_ONLY_CONV_IDS
    cli._refresh_receive_only(_CFG, {"19:resolved@thread.v2"})
    assert api_client.RECEIVE_ONLY_CONV_IDS == {"19:ro@thread.v2", "19:resolved@thread.v2"}
    assert before == frozenset()                                       # a new object: never cleared in place
    session = MagicMock()
    monkeypatch.setattr(api_client, "_session", lambda: session)
    assert api_client.send_message("hej", "19:ro@thread.v2") is False
    session.post.assert_not_called()
    cli._refresh_receive_only({}, None)
    assert api_client.RECEIVE_ONLY_CONV_IDS == frozenset()


# ---------------------------------------------------------------------------
# weekly jobs
# ---------------------------------------------------------------------------


class _Clock(datetime):
    fixed = datetime(2026, 10, 26, 9, 2, tzinfo=UTC)         # a Monday

    @classmethod
    def now(cls, tz=None):
        return cls.fixed.astimezone(tz) if tz else cls.fixed


@pytest.fixture()
def tick(monkeypatch, tmp_path):
    jobs = tmp_path / "jobs.json"
    runner = tmp_path / "broadcast_runner.py"
    monkeypatch.setattr(cli, "_BROADCAST_RUNNER_SCRIPT", runner)
    monkeypatch.setattr(cli, "_fired_broadcasts", {})
    monkeypatch.setattr(cli, "_append_log", lambda *a, **k: None)
    monkeypatch.setattr(cli, "datetime", _Clock)
    popen = MagicMock()
    monkeypatch.setattr(cli.subprocess, "Popen", popen)

    def run(job: dict, weekday_now: datetime):
        jobs.write_text(json.dumps([{"id": "raport", **job}]))
        monkeypatch.setattr(cli, "_load_broadcast_schedule", lambda: {"raport": {"generate_send": "10:55"}})
        _Clock.fixed = weekday_now
        popen.reset_mock()
        cli._broadcast_scheduler_tick(str(tmp_path / "log.jsonl"))
        return popen.called
    return run


def test_a_weekly_job_fires_only_on_its_weekday(tick):
    monday_cet = datetime(2026, 10, 26, 9, 56, tzinfo=UTC)                 # 10:56 in Warsaw
    assert tick({"schedule_type": "weekly", "weekday": 0}, monday_cet) is True
    cli._fired_broadcasts.clear()
    assert tick({"schedule_type": "weekly", "weekday": 2}, monday_cet) is False
    cli._fired_broadcasts.clear()
    assert tick({"schedule_type": "weekly"}, monday_cet) is True            # the weekday defaults to Monday
    cli._fired_broadcasts.clear()
    saturday = datetime(2026, 10, 24, 8, 56, tzinfo=UTC)               # 10:56 CEST
    assert tick({"schedule_type": "daily"}, saturday) is False              # the other types keep their rules
    cli._fired_broadcasts.clear()
    assert tick({"schedule_type": "always"}, saturday) is True


# ---------------------------------------------------------------------------
# mentions: the profile fallback can be switched off
# ---------------------------------------------------------------------------


def test_mention_profile_fallback_can_be_switched_off(monkeypatch):
    looked_up = []
    monkeypatch.setattr(cli.user_memory, "find_profile_by_name",
                        lambda key: looked_up.append(key) or ("Ewa Nowak", "8:orgid:ewa"))
    monkeypatch.setattr(cli.api_client, "build_mentions_html",
                        lambda people: ("<chip/>", [{"mri": p[1]} for p in people]))
    reply = "Sprawdź. [BOB-WYWOLAJ: ewa]"
    clean, html, entries = cli._resolve_mentions(reply, cli._BOB_MENTION_RE, {})
    assert html == "<chip/>" and entries == [{"mri": "8:orgid:ewa"}] and looked_up == ["ewa"]
    looked_up.clear()
    clean, html, entries = cli._resolve_mentions(reply, cli._BOB_MENTION_RE, {}, profile_fallback=False)
    assert clean == "Sprawdź." and html == "" and entries == [] and looked_up == []


# ---------------------------------------------------------------------------
# Bob's own files: remembered and read back
# ---------------------------------------------------------------------------


def test_a_sent_file_is_recorded_and_noted_in_the_chat_history(tmp_path, monkeypatch):
    monkeypatch.setattr(bob_files, "ARCHIVE_DIR", tmp_path / "files")
    monkeypatch.setattr(bob_files, "REGISTRY", tmp_path / "files" / "registry.json")
    monkeypatch.setattr(cli, "_HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(cli, "_CHAT_HISTORY", {})
    url = "https://contoso.sharepoint.com/sites/Team/Shared%20Documents/Bob/20261024T100000_plan.md"
    cli._remember_bob_file("plan.md", url, "Projekt", b"# Plan\n")
    assert (tmp_path / "files" / "20261024T100000_plan.md").read_bytes() == b"# Plan\n"
    assert cli._CHAT_HISTORY["Projekt"][-1]["sender"] == "Bob" and "plan.md" in cli._CHAT_HISTORY["Projekt"][-1]["text"]
    assert "# Plan" in bob_files.note_for(f"popraw {url}", "", "Projekt")
    assert bob_files.note_for(f"popraw {url}", "", "Inny czat") == ""


def test_remembering_a_file_never_breaks_the_send(monkeypatch, capsys):
    monkeypatch.setattr(bob_files, "record", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    cli._remember_bob_file("a.md", "https://x/a.md", "Projekt", b"x")
    assert "bob_files" in capsys.readouterr().err


def test_note_bot_post_ignores_empty_input(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(cli, "_CHAT_HISTORY", {})
    cli._note_bot_post("", "<p>x</p>")
    cli._note_bot_post("Projekt", "<p></p>")
    assert cli._CHAT_HISTORY == {}


# ---------------------------------------------------------------------------
# threads and processes
# ---------------------------------------------------------------------------


def test_the_panel_approval_poll_has_its_own_lock_and_interval():
    assert cli._PANEL_APPROVALS_INTERVAL_S == 60
    assert isinstance(cli._PANEL_APPROVALS_LOCK, type(threading.Lock()))


def test_a_note_still_being_posted_when_the_reply_is_dropped_is_deleted(monkeypatch):
    deleted = []

    def slow_send(html, conv):
        time.sleep(0.4)                                    # the POST is mid-flight when the reply is dropped
        return "m-slow"

    monkeypatch.setattr(cli.api_client, "send_html_message", slow_send)
    monkeypatch.setattr(cli.api_client, "edit_message", lambda *a: True)
    monkeypatch.setattr(cli.api_client, "delete_message", lambda mid, conv: deleted.append(mid) or True)
    sw = cli._StillWorking("conv-1", first_s=0.01, every_s=5)
    sw.__enter__()
    time.sleep(0.1)
    sw._stop.set()
    sw.finish(False)                                       # must wait for the sender, then close the note
    assert deleted == ["m-slow"]


def test_an_error_after_the_reply_still_closes_the_still_working_note(tmp_path, monkeypatch):
    finished = []

    class FakeNote:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def finish(self, sent):
            finished.append(sent)

    msg = {"id": "m1", "sequence_id": 2, "sender": "Szef", "sender_mri": cli.api_client.USER_MRI,
           "text": "Bob, hej", "raw_html": "<p>Bob, hej</p>", "timestamp": "2026-10-24T10:00:00Z", "image_urls": []}
    args = cli.argparse.Namespace(
        model="claude-haiku-4-5", max_chats=1, force_login=False, log_file=str(tmp_path / "log.jsonl"),
        watch_chat=None, poll=10, watch_state=str(tmp_path / "state.json"), rate_limit=0, caller="owner")
    monkeypatch.setattr(cli, "_StillWorking", FakeNote)
    monkeypatch.setattr(cli.ai, "freeform_reply", lambda *a, **k: "Czesc")
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv, n=20: [msg])
    monkeypatch.setattr(cli, "_load_chat_config", lambda path: {"Projekt": {}})
    monkeypatch.setattr(cli, "_extract_and_send_files", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        cli._process_chat_messages("Projekt", "conv-1", args, {"last_sequence_id": 1}, [0.0],
                                   control_chat=False, caller="owner")
    assert finished == [False]
