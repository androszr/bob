"""Bob's panel (scripts/bob_panel) and the control-chat commands it adds.

Everything runs on tmp_path + monkeypatch: no real files, Teams, Claude or network. The panel is served in-process
on an ephemeral port for the Origin / Host / static-file tests.
"""

import http.client
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "scripts" / "teams_agent"))

import ai  # noqa: E402
import cli  # noqa: E402
import login  # noqa: E402
import roles  # noqa: E402
from bob_panel import approvals, chatcfg, data, logs, rolecfg, server, supervisor  # noqa: E402

# ---------------------------------------------------------------------------
# supervisor
# ---------------------------------------------------------------------------


def test_find_bob_takes_the_python_process_not_the_wrapping_shell(monkeypatch):
    rows = [(10, "Thu Oct 23 09:00:00 2026", "/bin/zsh -il -c cd '/repo' && exec python3 scripts/teams_agent/cli.py"),
            (11, "Thu Oct 23 09:00:01 2026", "/Library/.../Python scripts/teams_agent/cli.py"),
            (12, "Thu Oct 23 09:00:02 2026", "python3 scripts/jira/mcp_server.py")]
    monkeypatch.setattr(supervisor, "_ps", lambda: rows)
    assert supervisor.find_bob()["pid"] == 11
    monkeypatch.setattr(supervisor, "_ps", lambda: rows[2:])
    assert supervisor.find_bob() is None


def test_a_finished_child_is_not_alive_even_before_it_is_reaped():
    p = subprocess.Popen(["true"])
    time.sleep(0.3)                     # exited, not waited for: a zombie that still answers kill(pid, 0)
    assert supervisor._alive(p.pid) is False


def test_start_and_restart_refuse_a_stale_session_and_never_leave_bob_down(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "SESSION_FILE", tmp_path / "missing.json")
    stopped = []
    monkeypatch.setattr(supervisor, "find_bob", lambda: None)
    monkeypatch.setattr(supervisor, "stop", lambda *a, **k: stopped.append(1) or {"ok": True, "message": ""})
    assert supervisor.start("t")["needs_login"]
    assert supervisor.restart("t")["needs_login"] and not stopped


def test_panel_login_waits_for_the_button_then_starts_bob(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "STATE_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(supervisor, "LOGIN_LOG", tmp_path / "login.log")
    monkeypatch.setattr(supervisor, "LOGIN_DONE", tmp_path / "done")
    script = tmp_path / "fake_login.py"     # stands in for login.py: waits for --done-file, exits 0
    script.write_text("import sys, time, pathlib\nf = pathlib.Path(sys.argv[2])\n"
                      "while not f.exists(): time.sleep(0.05)\nprint('saved')\n")
    monkeypatch.setattr(supervisor, "LOGIN_REL", str(script))
    shell = tmp_path / "shell.sh"           # like `zsh -il -c CMD` but without sourcing any login profile
    shell.write_text('#!/bin/sh\nexec /bin/sh -c "$3"\n')
    shell.chmod(0o700)
    monkeypatch.setattr(supervisor, "SHELL", str(shell))
    monkeypatch.setattr(supervisor, "_login", {})        # no state from an earlier test
    started = []
    monkeypatch.setattr(supervisor, "start", lambda by: started.append(by) or {"ok": True, "message": "ok"})
    assert supervisor.login_confirm()["ok"] is False                     # nothing to confirm yet
    assert supervisor.login("owner", then_start=True)["ok"]
    assert supervisor.login_status()["running"]
    assert supervisor.login_confirm()["ok"]
    for _ in range(200):
        if started and supervisor.login_status()["exit_code"] is not None:
            break
        time.sleep(0.05)
    assert started == ["owner (after login)"] and supervisor.login_status()["exit_code"] == 0
    assert os.stat(tmp_path / "login.log").st_mode & 0o077 == 0


def test_autostart_only_when_bob_was_meant_to_run_and_the_session_is_valid(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(supervisor, "find_bob", lambda: None)
    monkeypatch.setattr(supervisor, "start", lambda by: {"ok": True, "by": by})
    monkeypatch.setattr(supervisor, "session_ok", lambda: True)
    assert supervisor.autostart() is None                                  # never started from the panel
    (tmp_path / "s.json").write_text('{"should_run": true}')
    assert supervisor.autostart()["by"] == "panel (autostart)"
    (tmp_path / "s.json").write_text('{"ma_dzialac": true}')               # a state file of an older build
    assert supervisor.autostart()["by"] == "panel (autostart)"
    (tmp_path / "s.json").write_text('{"ma_dzialac": true, "should_run": false}')    # the English key wins
    assert supervisor.autostart() is None
    monkeypatch.setattr(supervisor, "session_ok", lambda: False)
    assert supervisor.autostart() is None


@pytest.fixture()
def sup(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "STATE_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(supervisor, "find_bob", lambda: {"pid": 424242})
    monkeypatch.setattr(supervisor, "_alive", lambda pid: False)
    monkeypatch.setattr(supervisor, "session_ok", lambda: True)
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: None)
    supervisor._write_state(should_run=True)
    return supervisor


def test_restart_keeps_bob_meant_to_run(sup, monkeypatch):
    monkeypatch.setattr(sup, "start", lambda by: {"ok": True, "message": "ok"})
    assert sup.restart("t")["ok"]
    assert sup.read_state()["should_run"] is True


def test_manual_stop_clears_should_run(sup):
    assert sup.stop("owner")["ok"]
    assert sup.read_state()["should_run"] is False


class _Page:
    def __init__(self, closed=False):
        self.closed, self.waits = closed, 0

    def is_closed(self):
        return self.closed

    def wait_for_timeout(self, ms):
        self.waits += 1


def test_wait_for_panel_returns_true_and_removes_the_done_file(tmp_path):
    done = tmp_path / "done"
    done.touch()
    assert login._wait_for_panel(_Page(), done) is True
    assert not done.exists()


def test_wait_for_panel_gives_up_when_the_browser_is_closed(tmp_path):
    assert login._wait_for_panel(_Page(closed=True), tmp_path / "done") is False


def test_wait_for_panel_gives_up_when_the_window_dies(tmp_path):
    class Dying(_Page):
        def wait_for_timeout(self, ms):
            raise RuntimeError("Target closed")
    assert login._wait_for_panel(Dying(), tmp_path / "done") is False


def test_main_exits_4_on_a_stale_session_without_a_terminal(monkeypatch):
    class NoTty:
        def isatty(self):
            return False
    old_umask = os.umask(0o022)
    try:
        monkeypatch.setattr(cli, "ensure_fresh_session", lambda: False)
        monkeypatch.setattr(sys, "stdin", NoTty())
        with pytest.raises(SystemExit) as exc:
            cli.main([])
        assert exc.value.code == 4
    finally:
        os.umask(old_umask)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


def test_loops_and_groups_are_read_from_the_bot_sources():
    loops = {x["name"]: x for x in data.loops()}
    assert loops["panel_approvals"]["every_s"] == 60
    assert loops["notif_check"]["every_s"] == 60
    assert loops["sharepoint_poll"]["every_s"] == 3600           # "60 * 60" evaluated without eval()
    assert {"jira", "todos", "datetime"} <= set(data._mcp_groups())
    assert data._personas() == list(ai._PERSONA_PROMPTS)


def test_arith_accepts_numbers_and_refuses_everything_else():
    assert data._arith("25 * 60") == 1500
    for bad in ("__import__('os').getcwd()", "1 if True else 2", "[1]", "True"):
        with pytest.raises(ValueError):
            data._arith(bad)


def test_chats_expand_all_groups_and_extras(monkeypatch, tmp_path):
    cfg = {"chats": {"Notes": {"mcp": None, "mcp_extra": ["leave"], "control_chat": True},
                     "Project": {"mcp": ["jira"], "ingest_only": True, "external_chat": True},
                     "Old": {"role": ["asystent"], "kanal_fr": True}}}           # Polish keys of an older build
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    (tmp_path / "roles.json").write_text(json.dumps({"roles": {"assistant": {"name": "A", "access": []}}}))
    monkeypatch.setattr(roles, "ROLES_FILE", tmp_path / "roles.json")
    monkeypatch.setattr(data, "CHAT_CONFIG", tmp_path / "c.json")
    monkeypatch.setattr(data, "WATCH_STATE", tmp_path / "missing.json")
    rows = {r["name"]: r for r in data.chats()}
    assert rows["Notes"]["all_groups"] and "jira" in rows["Notes"]["groups"]
    assert rows["Project"]["groups"] == ["jira"] and "ingest_only" in rows["Project"]["flags"]
    assert "external chat" in rows["Project"]["flags"]
    assert rows["Old"]["roles"] == ["assistant"] and "external chat" in rows["Old"]["flags"]


def _ts(hours_ago, z=False):
    s = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    return s.replace("+00:00", "Z") if z else s


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_dashboard_counts_per_chat_and_ignores_old_or_odd_rows(monkeypatch, tmp_path):
    log, perf = tmp_path / "log.jsonl", tmp_path / "perf.jsonl"
    _jsonl(log, [
        {"action": "watch_send", "chat_name": "A", "ts": _ts(1, z=True)},                 # "Z" suffix
        {"action": "watch_skip_silent_sentinel", "chat_name": "A", "ts": _ts(2)},
        {"action": "watch_send", "chat_name": "A", "ts": _ts(30)},                        # older than 24 h
        {"action": "watch_send", "chat_name": ["A", "B"], "ts": _ts(1)},                  # not a string
        {"action": "watch_send", "chat_name": "B", "ts": "not a date"},
    ])
    _jsonl(perf, [{"ts": _ts(1), "total_cost_usd": 0.5, "duration_ms": 4000, "ctx": {"chat": "A"}},
                  {"ts": _ts(1), "total_cost_usd": "0.1", "ctx": {"chat": "A"}},         # a string cost still adds up
                  {"ts": _ts(1), "total_cost_usd": "n/a", "ctx": {"chat": "A"}},         # garbage counts as zero
                  {"ts": _ts(30), "total_cost_usd": 9, "ctx": {"chat": "A"}}])
    monkeypatch.setattr(data, "LOG_JSONL", log)
    monkeypatch.setattr(data, "PERF_JSONL", perf)
    d = data.dashboard(24)
    assert [c["name"] for c in d["chats"]] == ["A"]
    a = d["chats"][0]
    assert a["total"] == 2 and a["replies"] == 1 and a["silent"] == 1 and a["cost"] == 0.6
    assert sum(sum(cell) for cell in a["cells"]) == 2
    assert d["kpi"] == {"handled": 2, "replies": 1, "silent": 1, "calls": 3, "cost": 0.6,
                        "chats": 1, "median_s": 4.0}
    assert {f["kind"] for f in d["feed"]} == {"reply", "silent"}


def test_dashboard_keeps_both_local_two_oclock_hours_apart_at_the_dst_change(monkeypatch, tmp_path):
    log = tmp_path / "log.jsonl"
    # 2026-10-25: Central Europe goes from +02:00 to +01:00 at 03:00 local - 02:00 local happens twice
    monkeypatch.setattr(data.org_config, "TIMEZONE", "Europe/Warsaw")
    _jsonl(log, [{"action": "watch_send", "chat_name": "A", "ts": "2026-10-25T00:30:00+00:00"},   # 02:30 CEST
                 {"action": "watch_send", "chat_name": "A", "ts": "2026-10-25T01:30:00+00:00"}])  # 02:30 CET
    monkeypatch.setattr(data, "LOG_JSONL", log)
    monkeypatch.setattr(data, "PERF_JSONL", tmp_path / "none.jsonl")

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 25, 4, 10, tzinfo=UTC).astimezone(tz) if tz else datetime(2026, 10, 25, 5, 10)
    monkeypatch.setattr(data, "datetime", _Frozen)
    d = data.dashboard(6)
    assert len(set(d["hours"])) == 6                                # six real hours, six distinct labels
    assert [sum(c) for c in d["chats"][0]["cells"]].count(1) == 2   # one message in each of the two 02:00 hours


def test_tasks_drops_watch_stop_and_list_like_chat_names(monkeypatch, tmp_path):
    log = tmp_path / "log.jsonl"
    _jsonl(log, [
        {"ts": "1", "chat_name": "A", "reply": "ok", "incoming_text": "hej"},
        {"ts": "2", "action": "watch_stop", "chat_name": "A"},
        {"ts": "3", "chat_name": ["A", "B"], "reply": "x"},
        {"ts": "4", "chat_name": "[A, B]", "reply": "x"},
        {"ts": "5", "action": "watch_skip_empty", "chat_name": "A"},
    ])
    monkeypatch.setattr(data, "LOG_JSONL", log)
    assert [(t["ts"], t["chat"]) for t in data.tasks()] == [("1", "A")]


def test_status_names_the_owner_and_has_only_the_generic_fields(monkeypatch):
    s = data.status()
    assert s["owner"] and "queues" not in s and set(s) == {
        "bob", "supervisor", "last_activity", "last_log", "owner", "teams_session", "login", "now"}
    assert {"bob", "teams_session", "login"} <= set(s)


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


def test_generic_approval_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setattr(approvals, "STORE", tmp_path / "approvals.json")
    req = approvals.create("Restart Bob?", "after the deploy", "Claude Code")
    assert req["status"] == "pending" and approvals.listing()["pending"][0]["id"] == req["id"]
    assert approvals.decide(req["id"], True, "ok")["ok"]
    assert approvals.get(req["id"])["status"] == "approved"
    assert not approvals.decide(req["id"], False)["ok"]          # decided once
    with pytest.raises(ValueError):
        approvals.create("  ")


def test_the_approval_owner_label_comes_from_the_org_config():
    import org_config  # noqa: PLC0415
    assert approvals.OWNER == org_config.OWNER_DISPLAY_NAME


def test_new_requests_are_announced_once_and_retried_when_the_send_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(approvals, "STORE", tmp_path / "approvals.json")
    req = approvals.create("Deploy X?", "details", "Claude Code")
    assert approvals.announce_pending(lambda t: False) == 0
    sent = []
    assert approvals.announce_pending(lambda t: sent.append(t) or True) == 1
    assert f"Bob, approve {req['id']}" in sent[0] and f"Bob, reject {req['id']}" in sent[0]
    assert "Claude Code" in sent[0]
    assert approvals.announce_pending(lambda t: sent.append(t) or True) == 0 and len(sent) == 1


def test_announce_pending_truncates_long_details(monkeypatch, tmp_path):
    monkeypatch.setattr(approvals, "STORE", tmp_path / "approvals.json")
    approvals.create("Big diff", "x" * 5000, "test")
    sent = []
    assert approvals.announce_pending(lambda text: sent.append(text) or True) == 1
    assert len(sent[0]) < 1600 and "in the panel" in sent[0]
    assert approvals.announce_pending(lambda text: sent.append(text) or True) == 0     # once only


def test_the_json_list_store_updates_under_a_lock(tmp_path):
    from bob_panel import store  # noqa: PLC0415
    path = tmp_path / "list.json"
    assert store.load(path) == []
    threads = [threading.Thread(target=lambda i=i: store.update(lambda items: [*items, {"i": i}], path))
               for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(x["i"] for x in store.load(path)) == list(range(20))       # no lost update


# ---------------------------------------------------------------------------
# the control-chat commands: approvals, pause / off / on
# ---------------------------------------------------------------------------


def test_decision_command_regexes():
    m = cli._APR_DECIDE_RE.search("Bob, approve apr-0123456789: only after 6 pm")
    assert m and m.group(2) == "apr-0123456789" and m.group(3) == "only after 6 pm"
    assert cli._APR_DECIDE_RE.search("bob reject apr-abcdef0123").group(1) == "reject"
    assert cli._APR_DECIDE_BARE_RE.search("Bob, approve")
    # the Polish commands of older habits still work
    m = cli._APR_DECIDE_RE.search("Bob, zatwierdzam apr-0123456789: tylko po 18")
    assert m and m.group(2) == "apr-0123456789" and m.group(3) == "tylko po 18"
    m = cli._APR_DECIDE_RE.search("bob odrzucam apr-abcdef0123")
    assert m and m.group(1) == "odrzucam"
    assert not cli._APR_DECIDE_RE.search("Bob, zatwierdzam arz-01234567")
    assert cli._APR_DECIDE_BARE_RE.search("Bob zatwierdzam")
    assert cli._APR_DECIDE_BARE_RE.search("Bob, odrzucam: nie teraz").group(2) == "nie teraz"
    assert not cli._APR_DECIDE_BARE_RE.search("Bob zatwierdzam zmianę skryptu Anny")      # the AI handles it
    assert not cli._APR_DECIDE_BARE_RE.search("Bob approve the script change from Anna")


def _args(tmp_path):
    return cli.argparse.Namespace(
        model="claude-haiku-4-5", max_chats=1, force_login=False, log_file=str(tmp_path / "log.jsonl"),
        watch_chat=None, poll=10, watch_state=str(tmp_path / "state.json"), rate_limit=0, caller="owner")


def _owner_msg(text, seq=2):
    return {"id": f"msg_{seq}", "sequence_id": seq, "sender": "Boss", "sender_mri": cli.api_client.USER_MRI,
            "text": text, "raw_html": f"<p>{text}</p>", "timestamp": "2026-10-24T10:00:00Z", "image_urls": []}


def _run_control(tmp_path, monkeypatch, text):
    """Drive one owner message through the control-chat block; returns the acks Bob sent."""
    acks = []
    _run_control.ai_calls = []
    monkeypatch.setattr(ai, "freeform_reply", lambda *a, **kw: _run_control.ai_calls.append(a) or "<silent/>")
    monkeypatch.setattr(ai, "_run_claude_cli", lambda *a, **kw: pytest.fail("real claude CLI called"))
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: [_owner_msg(text)])
    monkeypatch.setattr(cli.api_client, "reply_to_message", lambda t, *a, **kw: acks.append(t) or True)
    monkeypatch.setattr(cli, "_load_chat_config", lambda path: {"Notes": {"control_chat": True}})
    cli._process_chat_messages("Notes", "conv-notes", _args(tmp_path), {"last_sequence_id": 1}, [0.0],
                               control_chat=True, caller="owner")
    return acks


@pytest.fixture()
def decisions(monkeypatch):
    calls = []

    def fake_decide(req_id, approve, note="", by=approvals.OWNER):
        calls.append({"id": req_id, "approve": approve, "note": note, "by": by})
        return {"ok": True, "message": "Saved."}
    monkeypatch.setattr(approvals, "decide", fake_decide)
    return calls


def test_control_chat_explicit_approval_passes_note_and_by(tmp_path, monkeypatch, decisions):
    acks = _run_control(tmp_path, monkeypatch, "Bob, approve apr-0123456789: a note")
    assert decisions == [{"id": "apr-0123456789", "approve": True, "note": "a note",
                          "by": f"{approvals.OWNER} (chat)"}]
    assert any("Approved" in a for a in acks) and not _run_control.ai_calls


def test_control_chat_bare_reject_resolves_the_single_pending_request(tmp_path, monkeypatch, decisions):
    monkeypatch.setattr(approvals, "listing", lambda *a, **k: {"pending": [{"id": "apr-aaaaaaaaaa"}], "done": []})
    acks = _run_control(tmp_path, monkeypatch, "Bob odrzucam")                  # the Polish alias
    assert [(d["id"], d["approve"]) for d in decisions] == [("apr-aaaaaaaaaa", False)]
    assert any("Rejected" in a for a in acks)


def test_control_chat_bare_decision_is_ambiguous_with_two_requests_waiting(tmp_path, monkeypatch, decisions):
    monkeypatch.setattr(approvals, "listing", lambda *a, **k: {
        "pending": [{"id": "apr-aaaaaaaaaa"}, {"id": "apr-bbbbbbbbbb"}], "done": []})
    _run_control(tmp_path, monkeypatch, "Bob approve")
    assert decisions == [] and len(_run_control.ai_calls) == 1       # falls through to the AI, which asks


@pytest.mark.parametrize("text,check", [
    ("Bob pause 5 min", lambda c: c["paused_until"] and c["disabled"] is False),
    ("Bob off", lambda c: c["disabled"] is True and c["paused_until"] is None),
    ("Bob on", lambda c: c["disabled"] is False and c["paused_until"] is None),
])
def test_pause_off_on_keep_disabled_chats(tmp_path, monkeypatch, text, check):
    ctrl = tmp_path / "bob-control.json"
    ctrl.write_text(json.dumps({"disabled": True, "disabled_chats": ["Old"]}))
    monkeypatch.setattr(cli, "_BOB_CONTROL_FILE", str(ctrl))
    _run_control(tmp_path, monkeypatch, text)
    state = json.loads(ctrl.read_text(encoding="utf-8"))
    assert state["disabled_chats"] == ["Old"] and check(state), state


def test_save_control_is_atomic_and_merges(tmp_path, monkeypatch):
    ctrl = tmp_path / "bob-control.json"
    ctrl.write_text(json.dumps({"disabled_chats": ["A"]}))
    monkeypatch.setattr(cli, "_BOB_CONTROL_FILE", str(ctrl))
    cli._save_control({**cli._load_control(), "disabled": True})
    assert cli._load_control() == {"disabled_chats": ["A"], "disabled": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_explicit_mcp_change_drops_roles_but_keeps_the_external_flag(tmp_path):
    path = tmp_path / "chat-config.json"
    cfg = {"X": {"roles": ["pm"], "remove_groups": ["todos"], "add_groups": ["leave"], "external_chat": True}}
    cli._apply_chat_config_change("X", "mcp", ["jira"], cfg, path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))["chats"]["X"]
    assert saved["mcp"] == ["jira"] and saved["external_chat"] is True
    assert not {"roles", "remove_groups", "add_groups"} & set(saved)
    assert cfg["X"] == saved


# ---------------------------------------------------------------------------
# chat configuration from the panel
# ---------------------------------------------------------------------------


@pytest.fixture()
def chat_files(monkeypatch, tmp_path):
    cfg = {"_comment": "field notes", "chats": {
        "Notes": {"conv_id": "48:notes", "control_chat": True, "caller": "owner", "mcp": None},
        "Project": {"conv_id": "19:abc@thread.v2", "caller": "all", "mcp": ["jira"], "trigger": "bob"}}}
    (tmp_path / "chat-config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    monkeypatch.setattr(chatcfg, "CHAT_CONFIG", tmp_path / "chat-config.json")
    monkeypatch.setattr(chatcfg, "CONTROL_FILE", tmp_path / "bob-control.json")
    monkeypatch.setattr(chatcfg, "BACKUP_DIR", tmp_path / "arch")
    monkeypatch.setattr(data, "_mcp_groups", lambda: ["jira", "datetime", "todos", "leave"])
    (tmp_path / "bob-control.json").write_text(json.dumps({"paused_until": None, "disabled_chats": ["Old"]}))
    return chatcfg, tmp_path


def test_chat_change_is_previewed_validated_backed_up_and_keeps_the_rest(chat_files):
    chatcfg, tmp = chat_files
    new = {"caller": "auto", "mcp": ["jira", "todos"], "trigger": "bob", "conv_id": "19:abc@thread.v2"}
    prev = chatcfg.preview("save", "Project", new)
    assert '+        "todos"' in prev["diff"] and json.loads((tmp / "chat-config.json").read_text())[
        "chats"]["Project"]["caller"] == "all"                       # a preview writes nothing
    assert chatcfg.apply("save", "Project", new)["ok"]
    doc = json.loads((tmp / "chat-config.json").read_text())
    assert doc["_comment"] == "field notes" and doc["chats"]["Project"]["mcp"] == ["jira", "todos"]
    assert len(list((tmp / "arch").glob("chat-config.*.json"))) == 1
    for bad in ({"mcp": ["no_such_group"]}, {"caller": "everyone"}, {"internet": "yes"}, {"control_chat": True},
                {"conv_id": "http://evil"}, {"ingest_only": "no"}, {"persona": "no-such"}):
        with pytest.raises(ValueError):
            chatcfg.apply("save", "Project", bad)


def test_added_chat_is_enabled_and_a_removed_one_switched_off_until_the_restart(chat_files):
    chatcfg, tmp = chat_files
    r = chatcfg.apply("add", "Old one", None)
    assert r["restart_needed"] and json.loads((tmp / "chat-config.json").read_text())["chats"]["Old one"] == \
        chatcfg.NEW_CHAT
    assert "Old one" not in chatcfg.disabled()
    assert chatcfg.apply("remove", "Project")["ok"]
    assert "Project" not in json.loads((tmp / "chat-config.json").read_text())["chats"]
    assert "Project" in chatcfg.disabled()          # still watched until the restart, with no config left
    ctrl = json.loads((tmp / "bob-control.json").read_text())
    assert ctrl["paused_until"] is None             # the rest of bob-control.json is kept
    with pytest.raises(ValueError):
        chatcfg.apply("remove", "Notes")
    with pytest.raises(ValueError):
        chatcfg.set_enabled("Notes", False)
    with pytest.raises(ValueError):
        chatcfg.apply("add", "Old one", None)          # already there


def test_renaming_a_switched_off_chat_keeps_it_off(chat_files):
    chatcfg, tmp = chat_files
    base = {"conv_id": "19:abc@thread.v2", "caller": "all", "mcp": ["jira"], "trigger": "bob"}
    chatcfg.set_enabled("Project", False)
    assert chatcfg.apply("save", "Project", base, new_name="Project 2")["ok"]
    assert {"Project", "Project 2"} <= chatcfg.disabled()          # the new name is off too
    chatcfg.set_enabled("Project 2", True)
    assert chatcfg.apply("save", "Project 2", base, new_name="Project 3")["ok"]
    assert "Project 3" not in chatcfg.disabled() and "Project 2" in chatcfg.disabled()


def test_flipping_listen_only_needs_a_restart_and_says_so(chat_files):
    chatcfg, tmp = chat_files
    base = {"conv_id": "19:abc@thread.v2", "caller": "all", "mcp": ["jira"], "trigger": "bob"}
    r = chatcfg.apply("save", "Project", {**base, "ingest_only": True})
    assert r["restart_needed"] and "restart" in r["message"]
    assert not chatcfg.apply("save", "Project", {**base, "ingest_only": True, "caller": "owner"})["restart_needed"]
    back = chatcfg.apply("save", "Project", base)                         # and in the other direction
    assert back["restart_needed"] and "restart" in back["message"]


def test_changing_a_conv_id_needs_a_restart_and_says_so(chat_files):
    chatcfg, tmp = chat_files
    base = {"conv_id": "19:abc@thread.v2", "caller": "all", "mcp": ["jira"], "trigger": "bob"}
    r = chatcfg.apply("save", "Project", {**base, "conv_id": "19:def@thread.v2"})
    assert r["restart_needed"] and "restart" in r["message"]
    assert not chatcfg.apply("save", "Project", {**base, "conv_id": "19:def@thread.v2", "caller": "owner"})[
        "restart_needed"]


def test_the_control_chat_is_the_flagged_one_not_a_literal(chat_files):
    chatcfg, tmp = chat_files
    doc = json.loads((tmp / "chat-config.json").read_text())
    doc["chats"] = {"Control room": {**doc["chats"]["Notes"]}, "Project": doc["chats"]["Project"]}
    (tmp / "chat-config.json").write_text(json.dumps(doc))
    assert chatcfg.control_chat() == "Control room"
    with pytest.raises(ValueError):
        chatcfg.apply("remove", "Control room")
    (tmp / "chat-config.json").write_text(json.dumps({"chats": {"Project": {}}}))
    assert chatcfg.control_chat() == "Notes"          # none flagged: the default name


def test_a_missing_chat_config_is_an_empty_one(chat_files):
    chatcfg, tmp = chat_files
    (tmp / "chat-config.json").unlink()
    assert chatcfg.listing()["chats"] == []
    assert chatcfg.apply("add", "First", None)["ok"] and (tmp / "chat-config.json").exists()


@pytest.fixture()
def role_chats(monkeypatch, tmp_path):
    cfg = {"chats": {"Notes": {"conv_id": "48:notes", "control_chat": True, "caller": "owner", "mcp": None},
                     "Project": {"roles": ["pm"], "mcp": ["jira"]}}}
    (tmp_path / "chat-config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    (tmp_path / "roles.json").write_text(json.dumps(
        {"roles": {"assistant": {"name": "A", "access": []}, "pm": {"name": "PM", "access": ["jira"]}}}))
    monkeypatch.setattr(roles, "ROLES_FILE", tmp_path / "roles.json")
    monkeypatch.setattr(chatcfg, "CHAT_CONFIG", tmp_path / "chat-config.json")
    monkeypatch.setattr(chatcfg, "CONTROL_FILE", tmp_path / "bob-control.json")
    monkeypatch.setattr(chatcfg, "BACKUP_DIR", tmp_path / "arch")
    monkeypatch.setattr(data, "_mcp_groups", lambda: ["jira", "datetime", "todos", "leave"])
    return tmp_path


def test_role_chat_save_strips_hand_written_mcp(role_chats):
    new = {"roles": ["pm"], "mcp": ["jira"], "mcp_extra": ["todos"], "remove_groups": ["todos"]}
    assert chatcfg.preview("save", "Project", new)["ok"]
    assert chatcfg.apply("save", "Project", new)["ok"]
    saved = json.loads((role_chats / "chat-config.json").read_text())["chats"]["Project"]
    assert saved == {"roles": ["pm"], "remove_groups": ["todos"]}


def test_a_polish_keyed_chat_is_read_and_saved_back_with_english_keys(role_chats):
    doc = json.loads((role_chats / "chat-config.json").read_text())
    doc["chats"]["Old"] = {"role": ["asystent"], "kanal_fr": True, "dodatkowo": ["urlopy"], "bez": ["todos"],
                           "caller": "all"}
    (role_chats / "chat-config.json").write_text(json.dumps(doc))
    listed = {c["name"]: c["config"] for c in chatcfg.listing()["chats"]}
    assert listed["Old"] == {"roles": ["assistant"], "external_chat": True, "add_groups": ["leave"],
                             "remove_groups": ["todos"], "caller": "all"}
    assert chatcfg.listing()["new_chat"] == {"caller": "all", "roles": [], "add_groups": ["datetime"]}
    assert chatcfg.apply("save", "Old", {"role": ["asystent"], "kanal_fr": True, "caller": "owner"})["ok"]
    saved = json.loads((role_chats / "chat-config.json").read_text())["chats"]
    assert saved["Old"] == {"roles": ["assistant"], "external_chat": True, "caller": "owner"}
    assert saved["Project"] == {"roles": ["pm"], "mcp": ["jira"]}                # other chats untouched


@pytest.mark.parametrize("bad", [
    {"roles": ["no_such"]},
    {"roles": ["pm"], "external_chat": "yes"},
    {"roles": ["pm"], "trigger": 5},
    {"roles": ["pm"], "remove_groups": ["no_such"]},
    {"role": ["pm"], "kanal_fr": "tak"},                                       # the Polish keys are checked too
])
def test_role_chat_validation_errors(role_chats, bad):
    for fn in (chatcfg.preview, chatcfg.apply):
        with pytest.raises(ValueError):
            fn("save", "Project", bad)
    assert "pm" in json.loads((role_chats / "chat-config.json").read_text())["chats"]["Project"]["roles"]


def test_panel_sees_the_same_groups_and_access_as_bob():
    known, default = rolecfg.groups()
    assert known == list(ai._MCP_GROUPS) and default == list(ai._ALL_GROUPS)
    cc = {"A": {"mcp": ["jira"]}, "B": {"mcp": None}, "C": {"mcp": ["jira"], "mcp_extra": ["leave"]}}
    for name, cfg in cc.items():
        assert rolecfg.effective(cfg) == cli._mcp_for(name, cc), name


# ---------------------------------------------------------------------------
# roles from the panel
# ---------------------------------------------------------------------------


@pytest.fixture()
def rfiles(monkeypatch, tmp_path):
    # written by an older build: Polish keys - the panel reads them and writes English back
    doc = {"grupy": {}, "role": {"asystent": {"nazwa": "A", "dostep": []},
                                 "pm": {"nazwa": "PM", "dostep": ["jira"], "wiedza": [
                                     {"id": "w", "nazwa": "W", "plik": "knowledge/role/pm/w.md",
                                      "wpiecie": "prompt"}]}}}
    (tmp_path / "roles.json").write_text(json.dumps(doc))
    (tmp_path / "knowledge" / "role" / "pm").mkdir(parents=True)
    (tmp_path / "knowledge" / "role" / "pm" / "w.md").write_text("# W\nold text\n")
    (tmp_path / "chat-config.json").write_text(json.dumps({"chats": {"Project": {"role": ["pm"]}}}))
    monkeypatch.setattr(roles, "ROLES_FILE", tmp_path / "roles.json")
    monkeypatch.setattr(roles, "REPO", tmp_path)
    for name, val in (("REPO", tmp_path), ("CHAT_CONFIG", tmp_path / "chat-config.json"),
                      ("ARCHIVE", tmp_path / "arch"), ("KNOWLEDGE_ARCHIVE", tmp_path / "karch"),
                      ("PROPOSALS", tmp_path / "prop"), ("JOBS_FILE", tmp_path / "jobs.json"),
                      ("LEGACY_PROPOSALS", tmp_path / "old-prop"), ("LEGACY_JOBS_FILE", tmp_path / "old-jobs.json")):
        monkeypatch.setattr(rolecfg, name, val)
    monkeypatch.setattr(approvals, "STORE", tmp_path / "approvals.json")
    monkeypatch.setattr(rolecfg.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda self: target(*args)})())
    return tmp_path


def test_roles_are_saved_validated_and_a_used_role_is_not_deleted(rfiles):
    new = {"name": "HR", "access": ["leave"], "skills": [{"name": "x"}]}
    assert rolecfg.save_role("hr", new, new=True)["ok"]
    assert roles.load()["roles"]["hr"]["access"] == ["leave"] and list((rfiles / "arch").glob("roles.*.json"))
    on_disk = json.loads((rfiles / "roles.json").read_text())
    assert set(on_disk) == {"groups", "roles"} and "assistant" in on_disk["roles"]     # written back in English
    assert on_disk["roles"]["pm"]["knowledge"][0]["file"] == "knowledge/role/pm/w.md"
    assert rolecfg.save_role("hr2", {"nazwa": "Old page", "dostep": ["leave"]}, new=True)["ok"]   # Polish body keys
    assert roles.load()["roles"]["hr2"]["name"] == "Old page"
    with pytest.raises(ValueError):
        rolecfg.save_role("hr", {"name": "K", "access": ["no_such"]})               # unknown group
    with pytest.raises(ValueError):
        rolecfg.save_role("hr3", {"name": "X", "knowledge": [{"id": "a", "file": "../../etc/passwd"}]}, new=True)
    with pytest.raises(ValueError):
        rolecfg.delete_role("pm")                                                   # Project has it
    with pytest.raises(ValueError):
        rolecfg.delete_role("assistant")
    assert rolecfg.delete_role("hr")["ok"] and "hr" not in roles.load()["roles"]


def test_knowledge_change_goes_through_approvals_and_new_knowledge_joins_the_role(rfiles):
    rolecfg.start_knowledge("pm", "add a new rule", kid="w", claude=lambda s, p: "# W\nold text\n- new rule")
    req = approvals.listing()["pending"][0]
    assert "+- new rule" in req["details"] and "old text" in (rfiles / "knowledge/role/pm/w.md").read_text()
    assert approvals.decide(req["id"], True)["ok"]
    assert "new rule" in (rfiles / "knowledge/role/pm/w.md").read_text()
    assert list((rfiles / "karch").rglob("*.before-*.md"))                          # old version kept
    rolecfg.start_knowledge("pm", "rules of the ticket queue", new={"name": "Ticket queue", "team_only": True},
                            claude=lambda s, p: "# Queue\n- we answer within 1 day")
    req = approvals.listing()["pending"][0]
    assert roles.load()["roles"]["pm"]["knowledge"][-1]["id"] == "w"                # not before the approval
    approvals.decide(req["id"], True)
    entry = roles.load()["roles"]["pm"]["knowledge"][-1]
    assert entry["file"] == "knowledge/role/pm/ticket-queue.md" and entry["team_only"]
    assert "1 day" in roles.knowledge_text(roles.knowledge_for({"roles": ["pm"]}, roles.load()))
    assert rolecfg.listing()["jobs"][0]["name"] == "Ticket queue"


def test_new_knowledge_with_an_unknown_wiring_is_refused_before_claude_runs(rfiles):
    with pytest.raises(ValueError):
        rolecfg.start_knowledge("pm", "rules of the queue", new={"name": "Queue", "wiring": "magic"},
                                claude=lambda s, p: pytest.fail("Claude must not be asked"))
    assert approvals.listing()["pending"] == []


def test_a_knowledge_proposal_that_loses_most_of_the_file_is_refused(rfiles):
    (rfiles / "knowledge/role/pm/w.md").write_text("# W\n" + "x" * 1000)
    rolecfg.start_knowledge("pm", "make it short", kid="w", claude=lambda s, p: "# W\nshort")
    assert approvals.listing()["pending"] == []
    assert next(iter(rolecfg._jobs().values()))["status"] == "failed"


def _propose():
    rolecfg.start_knowledge("pm", "add a rule", kid="w", claude=lambda s, p: "# W\nold text\n- new rule")
    req = approvals.listing()["pending"][0]
    job = next(j for j in rolecfg._jobs().values() if j.get("approval_id") == req["id"])
    return req["id"], job["id"]


def test_knowledge_file_changed_after_the_proposal_is_refused(rfiles):
    apr, jid = _propose()
    target = rfiles / "knowledge/role/pm/w.md"
    target.write_text("# W\nsomebody changed it\n")
    res = approvals.decide(apr, True)
    assert "changed in the meantime" in res["message"]
    assert target.read_text() == "# W\nsomebody changed it\n"
    assert rolecfg._jobs()[jid]["status"] == "failed"


def test_rejected_knowledge_leaves_the_file_and_drops_the_proposal(rfiles):
    apr, jid = _propose()
    assert (rfiles / "prop" / f"{apr}.json").exists()
    approvals.decide(apr, False)
    assert (rfiles / "knowledge/role/pm/w.md").read_text() == "# W\nold text\n"
    assert not (rfiles / "prop" / f"{apr}.json").exists()
    assert rolecfg._jobs()[jid]["status"] == "rejected"


def test_group_info_team_only_and_unknown_group(rfiles):
    assert rolecfg.save_group_info("leave", "x", True)["ok"]
    assert "leave" in roles.team_only(roles.load())
    rolecfg.save_group_info("leave", "x", False)
    assert "leave" not in roles.team_only(roles.load())
    with pytest.raises(ValueError):
        rolecfg.save_group_info("no_such_group", "x", True)
    assert json.loads((rfiles / "roles.json").read_text())["groups"]["leave"] == {"description": "x"}


def test_a_role_wears_a_bob_from_the_shipped_set_only(rfiles):
    assert "bob-happy" in rolecfg.bob_images() and len(rolecfg.bob_images()) == 20
    rolecfg.save_role("pm", {"name": "PM", "access": ["jira"], "image": "bob-happy"})
    assert roles.load()["roles"]["pm"]["image"] == "bob-happy"
    rolecfg.save_role("pm", {"name": "PM", "access": ["jira"], "image": "../../etc/passwd"})
    assert "image" not in roles.load()["roles"]["pm"]


def test_every_group_has_a_listing_of_its_real_tools():
    tools = rolecfg.group_tools()
    assert set(tools) == set(ai._MCP_GROUPS)
    assert tools["todos"] == [t.split("__")[-1] for t in ai._MCP_GROUPS["todos"].split(",")]


# ---------------------------------------------------------------------------
# the HTTP server
# ---------------------------------------------------------------------------


@pytest.fixture()
def panel(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    monkeypatch.setattr(server, "_ALLOWED_ORIGINS", {f"http://localhost:{port}"})
    monkeypatch.setattr(server, "_ALLOWED_HOSTS", {f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port
    srv.shutdown()


def _post(port, path, body, origin, host=None):
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {}),
               **({"Host": host} if host else {})}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 -- http://127.0.0.1 test server
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)                       # raw path: no client-side dot-segment cleanup
        r = conn.getresponse()
        return r.status, r.getheader("Content-Type"), r.read(), r
    finally:
        conn.close()


def test_posts_need_the_panel_origin(panel, monkeypatch):
    called = []
    monkeypatch.setattr(supervisor, "restart", lambda by: called.append(by) or {"ok": True, "message": "x"})
    assert _post(panel, "/api/bob/restart", {}, None)[0] == 403
    assert _post(panel, "/api/bob/restart", {}, "https://evil.example")[0] == 403
    assert not called
    code, body = _post(panel, "/api/bob/restart", {"by": "Claude Code"}, f"http://localhost:{panel}")
    assert code == 200 and called == ["Claude Code"]


def test_a_foreign_host_name_is_refused_even_with_the_right_origin(panel):
    code, body = _post(panel, "/api/approvals", {"title": "x"}, f"http://localhost:{panel}", host="evil.example")
    assert code == 403 and body["error"] == "host"                       # DNS rebinding
    assert _get(panel, "/api/status")[0] == 200


def test_oversized_bodies_are_refused(panel):
    big = {"title": "x" * (server.MAX_BODY + 10)}
    code, _ = _post(panel, "/api/approvals", big, f"http://localhost:{panel}")
    assert code == 400


def test_a_negative_content_length_is_refused(panel):
    conn = http.client.HTTPConnection("127.0.0.1", panel, timeout=5)
    try:
        conn.putrequest("POST", "/api/approvals")
        conn.putheader("Origin", f"http://localhost:{panel}")
        conn.putheader("Content-Length", "-5")
        conn.endheaders()
        r = conn.getresponse()
        assert r.status == 400
    finally:
        conn.close()


def test_page_and_status_are_served(panel):
    code, ctype, body, resp = _get(panel, "/")
    assert code == 200 and b"<title>Bob" in body
    assert resp.getheader("X-Frame-Options") == "DENY"
    csp = resp.getheader("Content-Security-Policy")
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "http" not in csp
    code, _, body, _ = _get(panel, "/api/status")
    s = json.loads(body)
    assert code == 200 and "bob" in s and "teams_session" in s and "queues" not in s


def test_static_serves_svg_and_css_with_their_type(panel):
    code, ctype, body, _ = _get(panel, "/static/bob/bob-happy.svg")
    assert code == 200 and ctype == "image/svg+xml" and b"<svg" in body
    code, ctype, _, _ = _get(panel, "/static/tokens.css")
    assert code == 200 and ctype.startswith("text/css")
    assert _get(panel, "/static/fonts/x.woff2")[0] == 404                     # no font types in the map


@pytest.mark.parametrize("path", [
    "/static/../server.py", "/static/%2e%2e/server.py", "/static/index.html",
    "/api/roles/knowledge?plik=../chat-config.json", "/api/roles/knowledge?plik=scripts/org_config.py",
    "/api/roles/knowledge?file=../chat-config.json", "/api/roles/knowledge?file=scripts/org_config.py",
])
def test_static_and_knowledge_reject_traversal(panel, path):
    assert _get(panel, path)[0] in (404, 400)


def test_chat_config_api_needs_the_origin(panel, chat_files):
    assert _post(panel, "/api/chat-config/apply", {"op": "remove", "name": "Project"}, None)[0] == 403
    code, body = _post(panel, "/api/chat-config/apply", {"op": "save", "name": "Project", "config": {"caller": "x"}},
                       f"http://localhost:{panel}")
    assert code == 400 and "caller" in body["message"]


def test_the_lessons_routes_are_gone(panel):
    assert _get(panel, "/api/lessons")[0] == 404
    assert _post(panel, "/api/lessons/merge", {}, f"http://localhost:{panel}")[0] == 404


# ---------------------------------------------------------------------------
# the log view
# ---------------------------------------------------------------------------


def test_log_parser_levels_runs_repeats_and_blocks():
    raw = "\n".join([
        "===== start 2026-10-24T23:59:50 (panel) =====",
        "23:59:55 [watch] +0 wiad, +0 obrazow, rundy: 6",
        "00:00:05 [api] get_messages blad [ReadTimeout status=None] | token=memory age=12s",
        "00:00:06 [watch] sp-session-warm blad (https://x): Page.goto: Timeout 20000ms exceeded.",
        "Call log:",
        "  - navigating to https://x",
        "00:00:07 [watch] -> 9b odp w 3.1s",
        "00:00:08 [watch] -> 9b odp w 2.2s",
        "00:00:09 [watch] Przerwano (Ctrl-C).",
    ])
    e = logs.parse(raw, date(2026, 10, 24))
    assert e[0]["source"] == "start" and e[1]["level"] == "debug"
    assert e[2]["level"] == "warn" and e[2]["ts"].startswith("2026-10-25T00:00")       # transient, next day
    assert e[3]["level"] == "error" and e[3]["extra"] == ["Call log:", "  - navigating to https://x"]
    assert e[6]["level"] == "info"                                                   # a restart, not an error
    c = logs.collapse(e)
    assert [x["count"] for x in c if x["text"].startswith("-> ")] == [2]


def test_logs_view_filters_by_level_source_and_text(monkeypatch, tmp_path):
    now = datetime.now()

    def t(m):
        return (now - timedelta(minutes=m)).strftime("%H:%M:%S")
    log = tmp_path / "bob.log"
    log.write_text("\n".join([
        f"===== start {(now - timedelta(minutes=30)).isoformat(timespec='seconds')} (test) =====",
        f"{t(20)} [watch] witaj swiecie",
        f"{t(18)} [api] retry something",
        f"{t(15)} [watch] BLAD cos sie zepsulo",
        f"{t(10)} [watch] Token refresh ok",
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "LOG_FILE", log)
    warn = logs.view(hours=1, level="warn")
    assert {e["level"] for e in warn["entries"]} == {"warn", "error"}
    assert [e["source"] for e in logs.view(hours=1, level="debug", source="api")["entries"]] == ["api"]
    q = logs.view(hours=1, level="debug", q="ZEPSUL")["entries"]
    assert len(q) == 1 and "zepsulo" in q[0]["text"]
    assert logs.view(hours=1, level="debug")["counts"]["debug"] == 1
    assert warn["problems"][0]["level"] == "error"


def test_log_levels_understand_the_english_lines_too():
    assert logs.level_of("[api] ERROR: send failed") == "error"
    assert logs.level_of("[watch] path refused") == "error"
    assert logs.level_of("[jira] WARNING: field missing") == "warn"
    assert logs.level_of("[api] get_messages error [ReadTimeout status=None]") == "warn"   # transient
    assert logs.level_of("[api] sent (200)") == "debug" and logs.level_of("[api] token updated") == "debug"
    assert logs.level_of("[watch] hello world") == "info"
    e = logs.parse("===== start 2026-10-24T10:00:00 (panel) =====\n10:00:01 [watch] x", date(2026, 10, 24))
    assert e[0]["text"] == "Bob start (panel)"


# ---------------------------------------------------------------------------
# state written by older builds
# ---------------------------------------------------------------------------


def test_a_pending_proposal_of_an_older_build_is_still_applied(rfiles):
    (rfiles / "old-prop").mkdir()
    req = approvals.create("Old proposal", "", "Panel", action="knowledge_apply")
    (rfiles / "old-prop" / f"{req['id']}.json").write_text(json.dumps(
        {"job": "wz-1", "rola": "pm", "nowy": True, "tekst": "# N\nnew\n", "przed": "",
         "wpis": {"id": "n", "nazwa": "N", "plik": "knowledge/role/pm/n.md", "wpiecie": "prompt"}}))
    (rfiles / "old-jobs.json").write_text(json.dumps({"wz-1": {"id": "wz-1", "status": "awaiting", "rola": "pm",
                                                              "nazwa": "N", "polecenie": "x"}}))
    assert rolecfg._jobs()["wz-1"] == {"id": "wz-1", "status": "awaiting", "role": "pm", "name": "N",
                                       "instruction": "x"}
    assert "added to the role" in approvals.decide(req["id"], True)["message"]
    assert (rfiles / "knowledge/role/pm/n.md").read_text() == "# N\nnew\n"
    assert roles.load()["roles"]["pm"]["knowledge"][-1] == {"id": "n", "name": "N", "file": "knowledge/role/pm/n.md",
                                                            "wiring": "prompt", "description": ""}
    assert rolecfg._jobs()["wz-1"]["status"] == "applied" and (rfiles / "jobs.json").exists()


def test_the_knowledge_api_takes_the_english_and_the_old_query_and_body(panel, monkeypatch):
    seen = []
    monkeypatch.setattr(rolecfg, "read_knowledge", lambda rel: seen.append(rel) or {"file": rel, "text": "t"})
    for q in ("file", "plik"):
        code, _, body, _ = _get(panel, f"/api/roles/knowledge?{q}=knowledge/x.md")
        assert code == 200 and json.loads(body) == {"file": "knowledge/x.md", "text": "t"}
    monkeypatch.setattr(rolecfg, "save_group_info", lambda *a: seen.append(a) or {"ok": True})
    monkeypatch.setattr(rolecfg, "start_knowledge", lambda *a: seen.append(a) or {"ok": True})
    origin = f"http://localhost:{panel}"
    _post(panel, "/api/roles/group", {"group": "jira", "description": "d", "team_only": True}, origin)
    _post(panel, "/api/roles/group", {"group": "jira", "opis": "d", "tylko_zespol": True}, origin)
    _post(panel, "/api/roles/knowledge", {"role": "pm", "instruction": "do it", "new": {"name": "N"}}, origin)
    _post(panel, "/api/roles/knowledge", {"rola": "pm", "polecenie": "do it", "nowy": {"name": "N"}}, origin)
    assert seen[:2] == ["knowledge/x.md", "knowledge/x.md"]
    assert seen[2] == seen[3] == ("jira", "d", True, "panel")
    assert seen[4] == seen[5] == ("pm", "do it", None, {"name": "N"}, "panel")


def test_the_roles_listing_uses_the_english_keys(rfiles):
    listing = rolecfg.listing()
    assert {"roles", "groups", "known_groups", "default_groups", "team_only", "wirings", "jobs", "images", "tools",
            "without_roles", "no_file"} <= set(listing)
    pm = next(r for r in listing["roles"] if r["id"] == "pm")
    assert pm["knowledge"][0]["exists"] and pm["knowledge"][0]["file"] == "knowledge/role/pm/w.md"
    assert pm["channels"][0]["chat"] == "Project" and pm["channels"][0]["external_chat"] is False
    assert listing["wirings"] == ["code", "prompt", "tool"]
    assert rolecfg.read_knowledge("knowledge/role/pm/w.md") == {"file": "knowledge/role/pm/w.md",
                                                               "text": "# W\nold text\n"}


def test_adopt_legacy_moves_old_archive_once_and_renames_its_log(tmp_path):
    """An archive kept under the older Polish folder name is moved to the English one, its log renamed."""
    old, new = tmp_path / "roles-archiwum", tmp_path / "roles-archive"
    old.mkdir()
    (old / "zmiany.jsonl").write_text('{"what": "old"}\n', encoding="utf-8")
    (old / "roles.20260101-000000.json").write_text("{}", encoding="utf-8")
    assert supervisor.adopt_legacy(old, new, {"zmiany.jsonl": "changes.jsonl"}) == new
    assert not old.exists()
    assert (new / "changes.jsonl").read_text(encoding="utf-8") == '{"what": "old"}\n'
    assert (new / "roles.20260101-000000.json").exists()
    old.mkdir()                                            # a new archive already exists: the old one stays put
    (old / "x").write_text("", encoding="utf-8")
    supervisor.adopt_legacy(old, new)
    assert (old / "x").exists() and not (new / "x").exists()


def test_control_chat_falls_back_to_the_notes_chat_by_its_old_or_new_name(monkeypatch):
    monkeypatch.delenv("BOB_NOTES_CHAT", raising=False)
    assert chatcfg.control_chat({"chats": {"Team": {}, "Notatki": {}}}) == "Notatki"
    assert chatcfg.control_chat({"chats": {"Notes": {}, "Notatki": {}}}) == "Notes"
    assert chatcfg.control_chat({"chats": {"Team": {}, "Mine": {"control_chat": True}}}) == "Mine"
    assert chatcfg.control_chat({"chats": {"Team": {}}}) == "Notes"
    monkeypatch.setenv("BOB_NOTES_CHAT", "Inbox")
    assert chatcfg.control_chat({"chats": {"Inbox": {}, "Notes": {}}}) == "Inbox"
