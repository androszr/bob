"""Tests for scripts/broadcast/worklog_daily.py (daily worklog post, Jira/Tempo direct)."""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "broadcast"))

import worklog_daily as wd  # noqa: E402

THU = date(2026, 10, 8)


def _wl(author: str, key: str, secs: int, day: date = THU, summary: str = "Zadanie") -> dict:
    return {"author": {"name": author}, "dateStarted": f"{day.isoformat()}T00:00:00.000",
            "timeSpentSeconds": secs, "comment": "", "issue": {"key": key, "summary": summary}}


class FakeJira:
    """jira_get stand-in: users by e-mail, worklogs by username; None = API error."""

    def __init__(self, users: dict, logs: dict, fail_logs_for: set = frozenset()):
        self.users, self.logs, self.fail = users, logs, fail_logs_for
        self.calls = []

    def __call__(self, path: str, params: dict):
        self.calls.append((path, params))
        if path.endswith("/user/search"):
            q = params["username"]
            return [{"name": self.users[q], "displayName": q}] if q in self.users else []
        if path.endswith("/worklogs"):
            u = params["username"]
            return None if u in self.fail else self.logs.get(u, [])
        raise AssertionError(path)


def _roster(*emails):
    return [{"name": f"Osoba {e.split('@')[0].title()}", "email": e} for e in emails]


# --- calendar -------------------------------------------------------------


def test_prev_workday_monday_is_friday():
    assert wd.prev_workday(date(2026, 10, 12)) == date(2026, 10, 9)


def test_prev_workday_skips_holiday():
    # 11.11.2026 (sroda) -- swieto; czwartek raportuje wtorek
    assert wd.prev_workday(date(2026, 11, 12)) == date(2026, 11, 10)


def test_easter_monday_is_not_workday():
    assert not wd.is_workday(date(2026, 4, 6))
    assert wd.is_workday(date(2026, 4, 7))


# --- collection -----------------------------------------------------------


def test_every_member_gets_a_status(monkeypatch):
    monkeypatch.setattr(wd, "_FETCH_RETRY_S", 0)
    jira = FakeJira(
        users={e: e for e in ("a@example.com", "b@example.com", "d@example.com", "e@example.com")},
        logs={"a@example.com": [_wl("a@example.com", "PRJ1-1", 3600), _wl("a@example.com", "PRJ1-1", 1800)]},
        fail_logs_for={"e@example.com"},
    )
    roster = _roster("a@example.com", "b@example.com", "c@example.com", "d@example.com", "e@example.com")
    ms = wd.collect_team_day("T", THU, jira, roster, vacations={"d@example.com"})
    assert [m.status for m in ms] == ["logged", "missing", "no_jira", "vacation", "error"]
    assert ms[0].worklogs[0].hours == 1.5  # merged per issue


def test_vacation_wins_over_logged_hours():
    jira = FakeJira(users={"a@example.com": "a@example.com"},
                    logs={"a@example.com": [_wl("a@example.com", "PRJ1-1", 3600)]})
    (m,) = wd.collect_team_day("T", THU, jira, _roster("a@example.com"), vacations={"a@example.com"})
    assert m.status == "vacation" and m.worklogs == []
    assert not any(p.endswith("/worklogs") for p, _ in jira.calls)


def test_foreign_author_in_tempo_response_is_an_error(monkeypatch):
    monkeypatch.setattr(wd, "_FETCH_RETRY_S", 0)
    jira = FakeJira(users={"a@example.com": "a@example.com"},
                    logs={"a@example.com": [_wl("someone@example.com", "PRJ1-1", 60)]})
    (m,) = wd.collect_team_day("T", THU, jira, _roster("a@example.com"))
    assert m.status == "error"


def test_jira_user_name_fallback_ignores_vendor_prefix_and_inactive_marker():
    def jira_get(path, params):
        if params["username"] == "user11@example.com":
            return []
        return [{"name": "user11@example.com", "displayName": "EXT Lipa Ewa [X]"},
                {"name": "user47@example.com", "displayName": "user47@example.com"}]

    assert wd._jira_username(jira_get, "user11@example.com", "Ewa Lipa") == "user11@example.com"


def test_load_vacations_by_email(tmp_path):
    f = tmp_path / "urlopy.json"
    f.write_text(json.dumps({"fetched_at": "2026-10-08T10:00:00+00:00", "records": [
        {"Adres e-mail": "A@example.com", "Pierwszy dzień urlopu": "2026-10-07T00:00:00",
         "Ostatni dzień urlopu": "2026-10-09T00:00:00"},
        {"Adres e-mail": "b@example.com", "Pierwszy dzień urlopu": "2026-10-09T00:00:00",
         "Ostatni dzień urlopu": "2026-10-09T00:00:00"},
    ]}), encoding="utf-8")
    emails, fetched = wd.load_vacations(THU, f)
    assert emails == {"a@example.com"} and fetched.startswith("2026-10-08")


# --- summary + post -------------------------------------------------------


def _members():
    a = wd.MemberDay("Ala Kot", "a@example.com", status="logged",
                     worklogs=[wd.Worklog("PRJ1-1", "Logowanie", 6.0), wd.Worklog("PRJ1-2", "Daily", 0.25)])
    b = wd.MemberDay("Bob Pies", "b@example.com", status="missing")
    c = wd.MemberDay("Cyryl Ryba", "c@example.com", status="vacation")
    return [a, b, c]


def test_summarize_uses_ai_json_and_falls_back_on_garbage():
    ms = _members()
    intro = wd.summarize("T", THU, ms, lambda p: 'ok {"_intro": "Logowanie.", "0": "Robiła logowanie (6h)."}')
    assert intro == "Logowanie." and ms[0].summary == "Robiła logowanie (6h)."
    ms = _members()
    assert wd.summarize("T", THU, ms, lambda p: "nie json") == ""
    assert ms[0].summary == "Logowanie (6h); Daily (15m)"


def test_post_tags_missing_and_marks_vacation_without_hours():
    ms = _members()
    wd.summarize("T", THU, ms, lambda p: "")
    post = wd.build_post("TeamA", THU, ms)
    tagged = [m.name for m, t in zip(post.people, post.tag, strict=True) if t]
    assert tagged == ["Bob Pies"]
    assert "w czwartek (08.10)" in post.markdown
    assert "🌴 Urlop: Cyryl Ryba" in post.markdown
    assert "Cyryl Ryba" not in [m.name for m in post.people]
    assert "6h 15m" in post.markdown and "1/2 osób" in post.markdown
    assert "[PRJ1-1](https://jira.example.com/browse/PRJ1-1)" in post.markdown


def test_control_report_flags_errors():
    ms = _members() + [wd.MemberDay("Ewa Err", "e@example.com", status="error")]
    rep = wd.control_report("T", THU, ms, "", ["Bob Pies"])
    assert "Ewa Err" in rep and "NIE poszedl" in rep and "Bez @tagu" in rep


def test_fmt_hours():
    assert [wd.fmt_hours(h) for h in (8, 0.25, 1.5, 7.999)] == ["8h", "15m", "1h 30m", "8h"]


def test_missing_recap_lists_missing_per_team_and_vacations():
    ms = _members()
    ok = [wd.MemberDay("Ida Ok", "i@example.com", status="logged")]
    post = wd.build_missing_post(THU, {"TeamA": ms, "Incident Managerowie": ok})
    assert [m.name for m in post.people] == ["Bob Pies"] and post.tag == [False]
    assert "**Incident Managerowie:** ✅ komplet" in post.markdown
    assert "🌴 **Urlop w czwartek:** Cyryl Ryba" in post.markdown
    assert "Przekażcie proszę zespołom" in post.markdown
    assert "Tempo za czwartek" in post.markdown


def test_harvest_mris_matches_names_and_skips_ambiguous(tmp_path):
    f = tmp_path / "teams.json"
    f.write_text(json.dumps({"teams": {"T": {"members": [
        {"name": "Ida Mostek", "email": "a@example.com"},
        {"name": "Leszek Łyko", "email": "r@example.com"},
        {"name": "Jan Nowak", "email": "j@example.com"},
        {"name": "Ala Kot", "email": "k@example.com", "mri": "8:orgid:keep"},
    ]}}}), encoding="utf-8")
    msgs = [
        {"sender": "EXT Mostek Ida", "sender_mri": "8:orgid:aaa"},
        {"sender": "Łyko Leszek EXT", "sender_mri": "8:orgid:rrr"},
        {"sender": "Nowak Jan", "sender_mri": "8:orgid:n1"},
        {"sender": "Nowak Jan", "sender_mri": "8:orgid:n2"},
        {"sender": "Kot Ala", "sender_mri": "8:orgid:new"},
        {"sender": "Workflows", "sender_mri": "28:bot"},
    ]
    assert wd.harvest_mris(msgs, f) == ["Ida Mostek", "Leszek Łyko"]
    mris = {m["name"]: m.get("mri") for m in json.loads(f.read_text())["teams"]["T"]["members"]}
    assert mris == {"Ida Mostek": "8:orgid:aaa", "Leszek Łyko": "8:orgid:rrr",
                    "Jan Nowak": None, "Ala Kot": "8:orgid:keep"}


def test_inactive_email_account_is_replaced_by_the_one_active_account_of_the_same_name():
    def jira_get(path, params):
        if params["username"] == "old@example.com":
            return [{"name": "old@example.com", "emailAddress": "old@example.com", "displayName": "EXT Ewa Lipa [X]",
                     "active": False}]
        return [{"name": "old@example.com", "displayName": "EXT Ewa Lipa [X]", "active": False},
                {"name": "ewa.lipa@example.com", "displayName": "Ewa Lipa", "active": True}]

    assert wd._jira_username(jira_get, "old@example.com", "Ewa Lipa") == "ewa.lipa@example.com"


def test_inactive_account_without_an_active_twin_is_kept_and_an_active_one_wins_at_once():
    only_old = lambda path, params: [{"name": "old@example.com", "displayName": "Ewa Lipa", "active": False}]  # noqa: E731
    assert wd._jira_username(only_old, "old@example.com", "Ewa Lipa") == "old@example.com"
    assert wd._jira_username(only_old, "old@example.com", "") == "old@example.com"
    active = lambda path, params: [{"name": "a@example.com", "emailAddress": "a@example.com"}]  # noqa: E731
    assert wd._jira_username(active, "a@example.com", "Ala") == "a@example.com"
