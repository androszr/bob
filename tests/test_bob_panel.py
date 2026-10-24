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
                      "while not f.exists(): time.sleep(0.05)\nprint('zapisane')\n")
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
    assert started == ["owner (po logowaniu)"] and supervisor.login_status()["exit_code"] == 0
    assert os.stat(tmp_path / "login.log").st_mode & 0o077 == 0


def test_autostart_only_when_bob_was_meant_to_run_and_the_session_is_valid(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(supervisor, "find_bob", lambda: None)
    monkeypatch.setattr(supervisor, "start", lambda by: {"ok": True, "by": by})
    monkeypatch.setattr(supervisor, "session_ok", lambda: True)
    assert supervisor.autostart() is None                                  # never started from the panel
    (tmp_path / "s.json").write_text('{"ma_dzialac": true}')
    assert supervisor.autostart()["by"] == "panel (autostart)"
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
    supervisor._write_state(ma_dzialac=True)
    return supervisor


def test_restart_keeps_bob_meant_to_run(sup, monkeypatch):
    monkeypatch.setattr(sup, "start", lambda by: {"ok": True, "message": "ok"})
    assert sup.restart("t")["ok"]
    assert sup.read_state()["ma_dzialac"] is True


def test_manual_stop_clears_ma_dzialac(sup):
    assert sup.stop("owner")["ok"]
    assert sup.read_state()["ma_dzialac"] is False


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
    cfg = {"chats": {"Notatki": {"mcp": None, "mcp_extra": ["urlopy"], "control_chat": True},
                     "Projekt": {"mcp": ["jira"], "ingest_only": True, "kanal_fr": True}}}
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    (tmp_path / "roles.json").write_text(json.dumps({"role": {"asystent": {"nazwa": "A", "dostep": []}}}))
    monkeypatch.setattr(roles, "ROLES_FILE", tmp_path / "roles.json")
    monkeypatch.setattr(data, "CHAT_CONFIG", tmp_path / "c.json")
    monkeypatch.setattr(data, "WATCH_STATE", tmp_path / "missing.json")
    rows = {r["name"]: r for r in data.chats()}
    assert rows["Notatki"]["all_groups"] and "jira" in rows["Notatki"]["groups"]
    assert rows["Projekt"]["groups"] == ["jira"] and "ingest_only" in rows["Projekt"]["flags"]
    assert "kanał zewnętrzny" in rows["Projekt"]["flags"]


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
        {"action": "watch_send", "chat_name": "B", "ts": "to nie data"},
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
    assert a["total"] == 2 and a["odpowiedzi"] == 1 and a["milczenie"] == 1 and a["koszt"] == 0.6
    assert sum(sum(cell) for cell in a["cells"]) == 2
    assert d["kpi"] == {"obsluzone": 2, "odpowiedzi": 1, "milczenie": 1, "wywolania": 3, "koszt": 0.6,
                        "kanaly": 1, "czas_mediana": 4.0}


def test_dashboard_keeps_both_local_two_oclock_hours_apart_at_the_dst_change(monkeypatch, tmp_path):
    log = tmp_path / "log.jsonl"
    # 2026-10-25: Warsaw goes from +02:00 to +01:00 at 03:00 local - 02:00 local happens twice
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
    req = approvals.create("Restart Boba?", "po wdrożeniu", "Claude Code")
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
    req = approvals.create("Wdrożyć X?", "szczegóły", "Claude Code")
    assert approvals.announce_pending(lambda t: False) == 0
    sent = []
    assert approvals.announce_pending(lambda t: sent.append(t) or True) == 1
    assert f"zatwierdzam {req['id']}" in sent[0] and "Claude Code" in sent[0]
    assert approvals.announce_pending(lambda t: sent.append(t) or True) == 0 and len(sent) == 1


def test_announce_pending_truncates_long_details(monkeypatch, tmp_path):
    monkeypatch.setattr(approvals, "STORE", tmp_path / "approvals.json")
    approvals.create("Duży diff", "x" * 5000, "test")
    sent = []
    assert approvals.announce_pending(lambda text: sent.append(text) or True) == 1
    assert len(sent[0]) < 1600 and "panelu" in sent[0]
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
    m = cli._APR_DECIDE_RE.search("Bob, zatwierdzam apr-0123456789: tylko po 18")
    assert m and m.group(2) == "apr-0123456789" and m.group(3) == "tylko po 18"
    m = cli._APR_DECIDE_RE.search("bob odrzucam apr-abcdef0123")
    assert m and m.group(1) == "odrzucam"
    assert not cli._APR_DECIDE_RE.search("Bob, zatwierdzam arz-01234567")
    assert cli._APR_DECIDE_BARE_RE.search("Bob zatwierdzam")
    assert cli._APR_DECIDE_BARE_RE.search("Bob, odrzucam: nie teraz").group(2) == "nie teraz"
    assert not cli._APR_DECIDE_BARE_RE.search("Bob zatwierdzam zmianę skryptu Anny")      # the AI handles it


def _args(tmp_path):
    return cli.argparse.Namespace(
        model="claude-haiku-4-5", max_chats=1, force_login=False, log_file=str(tmp_path / "log.jsonl"),
        watch_chat=None, poll=10, watch_state=str(tmp_path / "state.json"), rate_limit=0, caller="owner")


def _owner_msg(text, seq=2):
    return {"id": f"msg_{seq}", "sequence_id": seq, "sender": "Szef", "sender_mri": cli.api_client.USER_MRI,
            "text": text, "raw_html": f"<p>{text}</p>", "timestamp": "2026-10-24T10:00:00Z", "image_urls": []}


def _run_control(tmp_path, monkeypatch, text):
    """Drive one owner message through the control-chat block; returns the acks Bob sent."""
    acks = []
    _run_control.ai_calls = []
    monkeypatch.setattr(ai, "freeform_reply", lambda *a, **kw: _run_control.ai_calls.append(a) or "<silent/>")
    monkeypatch.setattr(ai, "_run_claude_cli", lambda *a, **kw: pytest.fail("real claude CLI called"))
    monkeypatch.setattr(cli.api_client, "get_messages", lambda conv_id, n=20: [_owner_msg(text)])
    monkeypatch.setattr(cli.api_client, "reply_to_message", lambda t, *a, **kw: acks.append(t) or True)
    monkeypatch.setattr(cli, "_load_chat_config", lambda path: {"Notatki": {"control_chat": True}})
    cli._process_chat_messages("Notatki", "conv-notatki", _args(tmp_path), {"last_sequence_id": 1}, [0.0],
                               control_chat=True, caller="owner")
    return acks


@pytest.fixture()
def decisions(monkeypatch):
    calls = []

    def fake_decide(req_id, approve, note="", by=approvals.OWNER):
        calls.append({"id": req_id, "approve": approve, "note": note, "by": by})
        return {"ok": True, "message": "Zapisane."}
    monkeypatch.setattr(approvals, "decide", fake_decide)
    return calls


def test_control_chat_explicit_approval_passes_note_and_by(tmp_path, monkeypatch, decisions):
    acks = _run_control(tmp_path, monkeypatch, "Bob, zatwierdzam apr-0123456789: notatka")
    assert decisions == [{"id": "apr-0123456789", "approve": True, "note": "notatka",
                          "by": f"{approvals.OWNER} (chat)"}]
    assert any("Zatwierdzone" in a for a in acks) and not _run_control.ai_calls


def test_control_chat_bare_reject_resolves_the_single_pending_request(tmp_path, monkeypatch, decisions):
    monkeypatch.setattr(approvals, "listing", lambda *a, **k: {"pending": [{"id": "apr-aaaaaaaaaa"}], "done": []})
    acks = _run_control(tmp_path, monkeypatch, "Bob odrzucam")
    assert [(d["id"], d["approve"]) for d in decisions] == [("apr-aaaaaaaaaa", False)]
    assert any("Odrzucone" in a for a in acks)


def test_control_chat_bare_decision_is_ambiguous_with_two_requests_waiting(tmp_path, monkeypatch, decisions):
    monkeypatch.setattr(approvals, "listing", lambda *a, **k: {
        "pending": [{"id": "apr-aaaaaaaaaa"}, {"id": "apr-bbbbbbbbbb"}], "done": []})
    _run_control(tmp_path, monkeypatch, "Bob zatwierdzam")
    assert decisions == [] and len(_run_control.ai_calls) == 1       # falls through to the AI, which asks


@pytest.mark.parametrize("text,check", [
    ("Bob pause 5 min", lambda c: c["paused_until"] and c["disabled"] is False),
    ("Bob off", lambda c: c["disabled"] is True and c["paused_until"] is None),
    ("Bob on", lambda c: c["disabled"] is False and c["paused_until"] is None),
])
def test_pause_off_on_keep_disabled_chats(tmp_path, monkeypatch, text, check):
    ctrl = tmp_path / "bob-control.json"
    ctrl.write_text(json.dumps({"disabled": True, "disabled_chats": ["Stary"]}))
    monkeypatch.setattr(cli, "_BOB_CONTROL_FILE", str(ctrl))
    _run_control(tmp_path, monkeypatch, text)
    state = json.loads(ctrl.read_text(encoding="utf-8"))
    assert state["disabled_chats"] == ["Stary"] and check(state), state


def test_save_control_is_atomic_and_merges(tmp_path, monkeypatch):
    ctrl = tmp_path / "bob-control.json"
    ctrl.write_text(json.dumps({"disabled_chats": ["A"]}))
    monkeypatch.setattr(cli, "_BOB_CONTROL_FILE", str(ctrl))
    cli._save_control({**cli._load_control(), "disabled": True})
    assert cli._load_control() == {"disabled_chats": ["A"], "disabled": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_explicit_mcp_change_drops_roles_but_keeps_the_external_flag(tmp_path):
    path = tmp_path / "chat-config.json"
    cfg = {"X": {"role": ["pm"], "bez": ["todos"], "dodatkowo": ["urlopy"], "kanal_fr": True}}
    cli._apply_chat_config_change("X", "mcp", ["jira"], cfg, path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))["chats"]["X"]
    assert saved["mcp"] == ["jira"] and saved["kanal_fr"] is True
    assert not {"role", "bez", "dodatkowo"} & set(saved)
    assert cfg["X"] == saved


# ---------------------------------------------------------------------------
# chat configuration from the panel
# ---------------------------------------------------------------------------


@pytest.fixture()
def chat_files(monkeypatch, tmp_path):
    cfg = {"_comment": "opis pól", "chats": {
        "Notatki": {"conv_id": "48:notes", "control_chat": True, "caller": "owner", "mcp": None},
        "Projekt": {"conv_id": "19:abc@thread.v2", "caller": "all", "mcp": ["jira"], "trigger": "bob"}}}
    (tmp_path / "chat-config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    monkeypatch.setattr(chatcfg, "CHAT_CONFIG", tmp_path / "chat-config.json")
    monkeypatch.setattr(chatcfg, "CONTROL_FILE", tmp_path / "bob-control.json")
    monkeypatch.setattr(chatcfg, "BACKUP_DIR", tmp_path / "arch")
    monkeypatch.setattr(data, "_mcp_groups", lambda: ["jira", "datetime", "todos", "urlopy"])
    (tmp_path / "bob-control.json").write_text(json.dumps({"paused_until": None, "disabled_chats": ["Stary"]}))
    return chatcfg, tmp_path


def test_chat_change_is_previewed_validated_backed_up_and_keeps_the_rest(chat_files):
    chatcfg, tmp = chat_files
    new = {"caller": "auto", "mcp": ["jira", "todos"], "trigger": "bob", "conv_id": "19:abc@thread.v2"}
    prev = chatcfg.preview("save", "Projekt", new)
    assert '+        "todos"' in prev["diff"] and json.loads((tmp / "chat-config.json").read_text())[
        "chats"]["Projekt"]["caller"] == "all"                       # a preview writes nothing
    assert chatcfg.apply("save", "Projekt", new)["ok"]
    doc = json.loads((tmp / "chat-config.json").read_text())
    assert doc["_comment"] == "opis pól" and doc["chats"]["Projekt"]["mcp"] == ["jira", "todos"]
    assert len(list((tmp / "arch").glob("chat-config.*.json"))) == 1
    for bad in ({"mcp": ["nie_ma_takiej"]}, {"caller": "każdy"}, {"internet": "tak"}, {"control_chat": True},
                {"conv_id": "http://evil"}, {"ingest_only": "nie"}, {"persona": "nie-ma"}):
        with pytest.raises(ValueError):
            chatcfg.apply("save", "Projekt", bad)


def test_added_chat_is_enabled_and_a_removed_one_switched_off_until_the_restart(chat_files):
    chatcfg, tmp = chat_files
    r = chatcfg.apply("add", "Stary", None)
    assert r["restart_needed"] and json.loads((tmp / "chat-config.json").read_text())["chats"]["Stary"] == \
        chatcfg.NEW_CHAT
    assert "Stary" not in chatcfg.disabled()
    assert chatcfg.apply("remove", "Projekt")["ok"]
    assert "Projekt" not in json.loads((tmp / "chat-config.json").read_text())["chats"]
    assert "Projekt" in chatcfg.disabled()          # still watched until the restart, with no config left
    ctrl = json.loads((tmp / "bob-control.json").read_text())
    assert ctrl["paused_until"] is None             # the rest of bob-control.json is kept
    with pytest.raises(ValueError):
        chatcfg.apply("remove", "Notatki")
    with pytest.raises(ValueError):
        chatcfg.set_enabled("Notatki", False)
    with pytest.raises(ValueError):
        chatcfg.apply("add", "Stary", None)          # already there


def test_renaming_a_switched_off_chat_keeps_it_off(chat_files):
    chatcfg, tmp = chat_files
    base = {"conv_id": "19:abc@thread.v2", "caller": "all", "mcp": ["jira"], "trigger": "bob"}
    chatcfg.set_enabled("Projekt", False)
    assert chatcfg.apply("save", "Projekt", base, new_name="Projekt 2")["ok"]
    assert {"Projekt", "Projekt 2"} <= chatcfg.disabled()          # the new name is off too
    chatcfg.set_enabled("Projekt 2", True)
    assert chatcfg.apply("save", "Projekt 2", base, new_name="Projekt 3")["ok"]
    assert "Projekt 3" not in chatcfg.disabled() and "Projekt 2" in chatcfg.disabled()


def test_flipping_listen_only_needs_a_restart_and_says_so(chat_files):
    chatcfg, tmp = chat_files
    base = {"conv_id": "19:abc@thread.v2", "caller": "all", "mcp": ["jira"], "trigger": "bob"}
    r = chatcfg.apply("save", "Projekt", {**base, "ingest_only": True})
    assert r["restart_needed"] and "restarcie" in r["message"]
    assert not chatcfg.apply("save", "Projekt", {**base, "ingest_only": True, "caller": "owner"})["restart_needed"]
    back = chatcfg.apply("save", "Projekt", base)                         # and in the other direction
    assert back["restart_needed"] and "restarcie" in back["message"]


def test_changing_a_conv_id_needs_a_restart_and_says_so(chat_files):
    chatcfg, tmp = chat_files
    base = {"conv_id": "19:abc@thread.v2", "caller": "all", "mcp": ["jira"], "trigger": "bob"}
    r = chatcfg.apply("save", "Projekt", {**base, "conv_id": "19:def@thread.v2"})
    assert r["restart_needed"] and "restarcie" in r["message"]
    assert not chatcfg.apply("save", "Projekt", {**base, "conv_id": "19:def@thread.v2", "caller": "owner"})[
        "restart_needed"]


def test_the_control_chat_is_the_flagged_one_not_a_literal(chat_files):
    chatcfg, tmp = chat_files
    doc = json.loads((tmp / "chat-config.json").read_text())
    doc["chats"] = {"Centrala": {**doc["chats"]["Notatki"]}, "Projekt": doc["chats"]["Projekt"]}
    (tmp / "chat-config.json").write_text(json.dumps(doc))
    assert chatcfg.control_chat() == "Centrala"
    with pytest.raises(ValueError):
        chatcfg.apply("remove", "Centrala")
    (tmp / "chat-config.json").write_text(json.dumps({"chats": {"Projekt": {}}}))
    assert chatcfg.control_chat() == "Notatki"          # none flagged: the default name


def test_a_missing_chat_config_is_an_empty_one(chat_files):
    chatcfg, tmp = chat_files
    (tmp / "chat-config.json").unlink()
    assert chatcfg.listing()["chats"] == []
    assert chatcfg.apply("add", "Pierwszy", None)["ok"] and (tmp / "chat-config.json").exists()


@pytest.fixture()
def role_chats(monkeypatch, tmp_path):
    cfg = {"chats": {"Notatki": {"conv_id": "48:notes", "control_chat": True, "caller": "owner", "mcp": None},
                     "Projekt": {"role": ["pm"], "mcp": ["jira"]}}}
    (tmp_path / "chat-config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    (tmp_path / "roles.json").write_text(json.dumps(
        {"role": {"asystent": {"nazwa": "A", "dostep": []}, "pm": {"nazwa": "PM", "dostep": ["jira"]}}}))
    monkeypatch.setattr(roles, "ROLES_FILE", tmp_path / "roles.json")
    monkeypatch.setattr(chatcfg, "CHAT_CONFIG", tmp_path / "chat-config.json")
    monkeypatch.setattr(chatcfg, "CONTROL_FILE", tmp_path / "bob-control.json")
    monkeypatch.setattr(chatcfg, "BACKUP_DIR", tmp_path / "arch")
    monkeypatch.setattr(data, "_mcp_groups", lambda: ["jira", "datetime", "todos", "urlopy"])
    return tmp_path


def test_role_chat_save_strips_hand_written_mcp(role_chats):
    new = {"role": ["pm"], "mcp": ["jira"], "mcp_extra": ["todos"], "bez": ["todos"]}
    assert chatcfg.preview("save", "Projekt", new)["ok"]
    assert chatcfg.apply("save", "Projekt", new)["ok"]
    saved = json.loads((role_chats / "chat-config.json").read_text())["chats"]["Projekt"]
    assert saved == {"role": ["pm"], "bez": ["todos"]}


@pytest.mark.parametrize("bad", [
    {"role": ["nie_ma"]},
    {"role": ["pm"], "kanal_fr": "tak"},
    {"role": ["pm"], "trigger": 5},
    {"role": ["pm"], "bez": ["nie_ma"]},
])
def test_role_chat_validation_errors(role_chats, bad):
    for fn in (chatcfg.preview, chatcfg.apply):
        with pytest.raises(ValueError):
            fn("save", "Projekt", bad)
    assert "pm" in json.loads((role_chats / "chat-config.json").read_text())["chats"]["Projekt"]["role"]


def test_panel_sees_the_same_groups_and_access_as_bob():
    known, default = rolecfg.groups()
    assert known == list(ai._MCP_GROUPS) and default == list(ai._ALL_GROUPS)
    cc = {"A": {"mcp": ["jira"]}, "B": {"mcp": None}, "C": {"mcp": ["jira"], "mcp_extra": ["urlopy"]}}
    for name, cfg in cc.items():
        assert rolecfg.effective(cfg) == cli._mcp_for(name, cc), name


# ---------------------------------------------------------------------------
# roles from the panel
# ---------------------------------------------------------------------------


@pytest.fixture()
def rfiles(monkeypatch, tmp_path):
    doc = {"grupy": {}, "role": {"asystent": {"nazwa": "A", "dostep": []},
                                 "pm": {"nazwa": "PM", "dostep": ["jira"], "wiedza": [
                                     {"id": "w", "nazwa": "W", "plik": "knowledge/role/pm/w.md",
                                      "wpiecie": "prompt"}]}}}
    (tmp_path / "roles.json").write_text(json.dumps(doc))
    (tmp_path / "knowledge" / "role" / "pm").mkdir(parents=True)
    (tmp_path / "knowledge" / "role" / "pm" / "w.md").write_text("# W\nstara treść\n")
    (tmp_path / "chat-config.json").write_text(json.dumps({"chats": {"Projekt": {"role": ["pm"]}}}))
    monkeypatch.setattr(roles, "ROLES_FILE", tmp_path / "roles.json")
    monkeypatch.setattr(roles, "REPO", tmp_path)
    for name, val in (("REPO", tmp_path), ("CHAT_CONFIG", tmp_path / "chat-config.json"),
                      ("ARCHIVE", tmp_path / "arch"), ("KNOWLEDGE_ARCHIVE", tmp_path / "karch"),
                      ("PROPOSALS", tmp_path / "prop"), ("JOBS_FILE", tmp_path / "jobs.json")):
        monkeypatch.setattr(rolecfg, name, val)
    monkeypatch.setattr(approvals, "STORE", tmp_path / "approvals.json")
    monkeypatch.setattr(rolecfg.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda self: target(*args)})())
    return tmp_path


def test_roles_are_saved_validated_and_a_used_role_is_not_deleted(rfiles):
    new = {"nazwa": "Kadry", "dostep": ["urlopy"], "umiejetnosci": [{"nazwa": "x"}]}
    assert rolecfg.save_role("kadry", new, new=True)["ok"]
    assert roles.load()["role"]["kadry"]["dostep"] == ["urlopy"] and list((rfiles / "arch").glob("roles.*.json"))
    with pytest.raises(ValueError):
        rolecfg.save_role("kadry", {"nazwa": "K", "dostep": ["nie_ma"]})            # unknown group
    with pytest.raises(ValueError):
        rolecfg.save_role("kadry2", {"nazwa": "X", "wiedza": [{"id": "a", "plik": "../../etc/passwd"}]}, new=True)
    with pytest.raises(ValueError):
        rolecfg.delete_role("pm")                                                   # Projekt has it
    with pytest.raises(ValueError):
        rolecfg.delete_role("asystent")
    assert rolecfg.delete_role("kadry")["ok"] and "kadry" not in roles.load()["role"]


def test_knowledge_change_goes_through_approvals_and_new_knowledge_joins_the_role(rfiles):
    rolecfg.start_knowledge("pm", "dopisz nową regułę", kid="w", claude=lambda s, p: "# W\nstara treść\n- nowa reguła")
    req = approvals.listing()["pending"][0]
    assert "+- nowa reguła" in req["details"] and "stara treść" in (rfiles / "knowledge/role/pm/w.md").read_text()
    assert approvals.decide(req["id"], True)["ok"]
    assert "nowa reguła" in (rfiles / "knowledge/role/pm/w.md").read_text()
    assert list((rfiles / "karch").rglob("*.przed-*.md"))                           # old version kept
    rolecfg.start_knowledge("pm", "zasady kolejki zgłoszeń", new={"nazwa": "Kolejka zgłoszeń", "tylko_zespol": True},
                            claude=lambda s, p: "# Kolejka\n- odpowiadamy w 1 dzień")
    req = approvals.listing()["pending"][0]
    assert roles.load()["role"]["pm"]["wiedza"][-1]["id"] == "w"                    # not before the approval
    approvals.decide(req["id"], True)
    entry = roles.load()["role"]["pm"]["wiedza"][-1]
    assert entry["plik"] == "knowledge/role/pm/kolejka-zgloszen.md" and entry["tylko_zespol"]
    assert "1 dzień" in roles.knowledge_text(roles.knowledge_for({"role": ["pm"]}, roles.load()))


def test_new_knowledge_with_an_unknown_wiring_is_refused_before_claude_runs(rfiles):
    with pytest.raises(ValueError):
        rolecfg.start_knowledge("pm", "zasady kolejki", new={"nazwa": "Kolejka", "wpiecie": "magia"},
                                claude=lambda s, p: pytest.fail("Claude must not be asked"))
    assert approvals.listing()["pending"] == []


def test_a_knowledge_proposal_that_loses_most_of_the_file_is_refused(rfiles):
    (rfiles / "knowledge/role/pm/w.md").write_text("# W\n" + "x" * 1000)
    rolecfg.start_knowledge("pm", "skróć", kid="w", claude=lambda s, p: "# W\nkrótko")
    assert approvals.listing()["pending"] == []
    assert next(iter(rolecfg._jobs().values()))["status"] == "failed"


def _propose():
    rolecfg.start_knowledge("pm", "dopisz regułę", kid="w", claude=lambda s, p: "# W\nstara treść\n- nowa reguła")
    req = approvals.listing()["pending"][0]
    job = next(j for j in rolecfg._jobs().values() if j.get("approval_id") == req["id"])
    return req["id"], job["id"]


def test_knowledge_file_changed_after_the_proposal_is_refused(rfiles):
    apr, jid = _propose()
    target = rfiles / "knowledge/role/pm/w.md"
    target.write_text("# W\nktoś zmienił\n")
    res = approvals.decide(apr, True)
    assert "zmienił się" in res["message"]
    assert target.read_text() == "# W\nktoś zmienił\n"
    assert rolecfg._jobs()[jid]["status"] == "failed"


def test_rejected_knowledge_leaves_the_file_and_drops_the_proposal(rfiles):
    apr, jid = _propose()
    assert (rfiles / "prop" / f"{apr}.json").exists()
    approvals.decide(apr, False)
    assert (rfiles / "knowledge/role/pm/w.md").read_text() == "# W\nstara treść\n"
    assert not (rfiles / "prop" / f"{apr}.json").exists()
    assert rolecfg._jobs()[jid]["status"] == "rejected"


def test_group_info_team_only_and_unknown_group(rfiles):
    assert rolecfg.save_group_info("urlopy", "x", True)["ok"]
    assert "urlopy" in roles.team_only(roles.load())
    rolecfg.save_group_info("urlopy", "x", False)
    assert "urlopy" not in roles.team_only(roles.load())
    with pytest.raises(ValueError):
        rolecfg.save_group_info("nie_ma_takiej", "x", True)


def test_a_role_wears_a_bob_from_the_shipped_set_only(rfiles):
    assert "bob-happy" in rolecfg.bob_images() and len(rolecfg.bob_images()) == 20
    rolecfg.save_role("pm", {"nazwa": "PM", "dostep": ["jira"], "obraz": "bob-happy"})
    assert roles.load()["role"]["pm"]["obraz"] == "bob-happy"
    rolecfg.save_role("pm", {"nazwa": "PM", "dostep": ["jira"], "obraz": "../../etc/passwd"})
    assert "obraz" not in roles.load()["role"]["pm"]


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
])
def test_static_and_knowledge_reject_traversal(panel, path):
    assert _get(panel, path)[0] in (404, 400)


def test_chat_config_api_needs_the_origin(panel, chat_files):
    assert _post(panel, "/api/chat-config/apply", {"op": "remove", "name": "Projekt"}, None)[0] == 403
    code, body = _post(panel, "/api/chat-config/apply", {"op": "save", "name": "Projekt", "config": {"caller": "x"}},
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
