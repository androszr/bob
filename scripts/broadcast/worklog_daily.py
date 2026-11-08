"""Daily worklog post — what each team member logged in Tempo on the previous workday.

Generator for broadcast jobs with "generator": "worklog-daily" (dispatched from
broadcast_runner.run_job). Data sources:

- roster:    scripts/broadcast/worklog-teams.json (Bob's own copy, edit it there)
- worklogs:  Jira Tempo REST, queried PER PERSON (all projects), straight from Jira
- vacations: the vacation calendar (SharePoint list -> knowledge/leave/leave.json),
             matched by e-mail; a person on leave is only marked 🌴, never shown with hours
- summary:   one Claude call per team (no tools) turns the fetched worklogs into
             1-2 sentences per person; the post itself is assembled here.

Coverage guarantee: every roster member ends up in exactly one bucket (logged /
missing / vacation / no Jira account / fetch error). A fetch error after retries
blocks posting to the team channel — the preview goes to the owner's notes chat instead.

Only Monday-Friday: on Monday the post covers Friday; public holidays (pl_holidays) are
skipped both as "today" (no post) and as the reported day.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import org_config  # noqa: E402

_BROADCAST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BROADCAST_DIR.parents[1]
ROSTER_FILE = _BROADCAST_DIR / "worklog-teams.json"
LEAVE_FILE = _PROJECT_ROOT / "knowledge" / "leave" / "leave.json"
# Written by older builds; read only when LEAVE_FILE is missing.
LEGACY_LEAVE_FILE = _PROJECT_ROOT / "knowledge" / "urlopy" / "urlopy.json"
JIRA_BROWSE = org_config.JIRA_BROWSE_URL

_FETCH_ATTEMPTS = 3
_FETCH_RETRY_S = 2
_LEAVE_STALE_DAYS = 3
_MAX_KEYS = 4  # ticket links per person, largest first

_NAME_NOISE = {"x", *org_config.NAME_NOISE_PREFIXES}  # "x" = the inactive marker "[X]"

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Leave row keys: English canonical <- the older Polish spellings, still accepted on read.
_LEAVE_KEY_ALIASES = {
    "first_day": ("Pierwszy dzień urlopu", "Pierwszy_dzien_urlopu", "Pierwszy_dzień_urlopu"),
    "last_day": ("Ostatni dzień urlopu", "Ostatni_dzien_urlopu", "Ostatni_dzień_urlopu"),
    "email": ("Adres e-mail", "Adres_e-mail", "Adres_email"),
}


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def pl_holidays(year: int) -> set[date]:
    easter = _easter_sunday(year)
    fixed = [(1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 25), (12, 26)]
    days = {date(year, mo, d) for mo, d in fixed}
    days |= {easter, easter + timedelta(days=1), easter + timedelta(days=49), easter + timedelta(days=60)}
    if year >= 2025:
        days.add(date(year, 12, 24))  # Christmas Eve -- a day off since 2025
    return days


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in pl_holidays(d.year)


def prev_workday(today: date) -> date:
    """Most recent workday before *today* (Monday -> Friday, skips holidays)."""
    d = today - timedelta(days=1)
    while not is_workday(d):
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class Worklog:
    key: str
    summary: str
    hours: float
    comment: str = ""


@dataclass
class MemberDay:
    name: str
    email: str
    mri: str = ""
    status: str = ""            # logged | missing | vacation | no_jira | error
    worklogs: list[Worklog] = field(default_factory=list)
    summary: str = ""

    @property
    def hours(self) -> float:
        return sum(w.hours for w in self.worklogs)


def load_roster(team: str, path: Path = ROSTER_FILE) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    team_cfg = data.get("teams", {}).get(team)
    if team_cfg is None:
        raise KeyError(f"No team '{team}' in {path.name}")
    return [m for m in team_cfg.get("members", []) if not m.get("skip")]


def _leave_record(rec: dict) -> dict:
    """A copy of a leave row with English keys; the English key wins when both exist."""
    out = dict(rec)
    for key, aliases in _LEAVE_KEY_ALIASES.items():
        for alias in aliases:
            if alias in out:
                out.setdefault(key, out.pop(alias))
    return out


def load_vacations(day: date, path: Path | None = None) -> tuple[set[str], str]:
    """E-mails (lowercase) on vacation on *day*, plus the data's fetched_at ('' if unknown)."""
    if path is None:
        path = LEAVE_FILE
        if not path.exists() and LEGACY_LEAVE_FILE.exists():
            path = LEGACY_LEAVE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[worklog-daily] leave data unavailable: {exc}", file=sys.stderr, flush=True)
        return set(), ""
    out: set[str] = set()
    for raw in data.get("records", []):
        rec = _leave_record(raw)
        try:
            first = datetime.fromisoformat(rec["first_day"]).date()
            last = datetime.fromisoformat(rec["last_day"]).date()
        except (KeyError, TypeError, ValueError):
            continue
        if first <= day <= last:
            out.add(str(rec.get("email", "")).strip().lower())
    return out, str(data.get("fetched_at", ""))


def _name_tokens(s: str) -> frozenset[str]:
    folded = unicodedata.normalize("NFKD", s.replace("ł", "l").replace("Ł", "L"))
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    # Jira display names carry vendor prefixes and an inactive marker: "EXT Ewa Nowak [X]".
    return frozenset(t for t in re.split(r"[^a-z0-9]+", folded) if t and t not in _NAME_NOISE)


def _jira_username(jira_get: Callable, email: str, name: str = "") -> str | None:
    """Jira login for a roster member, '' when Jira has no such user, None on API error.

    E-mail first (exact login/e-mail match); then the full name, accepted only when
    exactly one active user's display name has the same tokens (e.g. a person whose
    Jira account is not on the roster e-mail). An INACTIVE e-mail account is replaced by
    the one active same-name account: people moving vendors get a new login and log
    there (the old, inactive account is skipped for the new one)."""
    users = jira_get("/rest/api/2/user/search", {"username": email, "includeInactive": "true"})
    if users is None:
        return None
    want = email.lower()
    by_email = next((u for u in users
                     if want in (str(u.get("name", "")).lower(), str(u.get("emailAddress", "")).lower())),
                    None)
    if by_email and by_email.get("active", True):
        return by_email["name"]
    if not name:
        return by_email["name"] if by_email else ""
    last = name.split()[-1]
    users = jira_get("/rest/api/2/user/search",
                     {"username": last, "maxResults": 50, "includeInactive": "true"})
    if users is None:
        return None
    hits = [u for u in users if _name_tokens(str(u.get("displayName", ""))) == _name_tokens(name)]
    if by_email:
        active = [u for u in hits if u.get("active", True) and u["name"] != by_email["name"]]
        return active[0]["name"] if len(active) == 1 else by_email["name"]
    return hits[0]["name"] if len(hits) == 1 else ""


def _fetch_worklogs(jira_get: Callable, username: str, day: date) -> list[Worklog] | None:
    """Tempo worklogs of one person on one day (all projects). None on API error."""
    iso = day.isoformat()
    raw = jira_get("/rest/tempo-timesheets/3/worklogs",
                   {"username": username, "dateFrom": iso, "dateTo": iso})
    if raw is None:
        return None
    entries = raw if isinstance(raw, list) else raw.get("worklogs", [])
    out = []
    for e in entries:
        author = str((e.get("author") or {}).get("name", "")).lower()
        if author != username.lower():
            # Tempo ignoring the username filter would silently mix people -- refuse.
            print(f"[worklog-daily] ERROR: Tempo returned a worklog by {author!r} for {username!r}",
                  file=sys.stderr, flush=True)
            return None
        if not str(e.get("dateStarted", "")).startswith(iso):
            continue
        issue = e.get("issue") or {}
        out.append(Worklog(
            key=str(issue.get("key", "")),
            summary=str(issue.get("summary", "")),
            hours=(e.get("timeSpentSeconds") or 0) / 3600,
            comment=str(e.get("comment") or "").strip(),
        ))
    return out


def _retry(fn: Callable, *args):
    for attempt in range(_FETCH_ATTEMPTS):
        result = fn(*args)
        if result is not None:
            return result
        if attempt < _FETCH_ATTEMPTS - 1:
            time.sleep(_FETCH_RETRY_S)
    return None


def collect_team_day(team: str, day: date, jira_get: Callable,
                     roster: list[dict] | None = None,
                     vacations: set[str] | None = None) -> list[MemberDay]:
    """Fetch the day for every roster member. Every member gets a status."""
    roster = load_roster(team) if roster is None else roster
    vacations = set() if vacations is None else vacations
    result = []
    for m in roster:
        email = m["email"].strip()
        md = MemberDay(name=m["name"], email=email, mri=m.get("mri", ""))
        if email.lower() in vacations:
            # Calendar vacation wins: no hours shown for people on leave, even if they logged.
            md.status = "vacation"
            result.append(md)
            continue
        username = _retry(_jira_username, jira_get, email, m["name"])
        if username is None:
            md.status = "error"
        elif username == "":
            md.status = "no_jira"
        else:
            logs = _retry(_fetch_worklogs, jira_get, username, day)
            if logs is None:
                md.status = "error"
            else:
                md.worklogs = _merge_by_issue(logs)
                md.status = "logged" if md.worklogs else "missing"
        result.append(md)
    return result


def _merge_by_issue(logs: list[Worklog]) -> list[Worklog]:
    merged: dict[str, Worklog] = {}
    for w in logs:
        if w.key in merged:
            cur = merged[w.key]
            cur.hours += w.hours
            if w.comment and w.comment not in cur.comment:
                cur.comment = f"{cur.comment}; {w.comment}" if cur.comment else w.comment
        else:
            merged[w.key] = Worklog(w.key, w.summary, w.hours, w.comment)
    return sorted(merged.values(), key=lambda w: -w.hours)


# ---------------------------------------------------------------------------
# Summary (Claude, no tools)
# ---------------------------------------------------------------------------


def fmt_hours(h: float) -> str:
    total_min = int(round(h * 60))
    hh, mm = divmod(total_min, 60)
    if mm == 0:
        return f"{hh}h"
    if hh == 0:
        return f"{mm}m"
    return f"{hh}h {mm}m"


_SUMMARY_PROMPT = """You are Bob, the assistant of team {team}. Below are the Tempo worklogs for {day_label} \
(JSON: person id -> first name and a list of tasks with title, time and worklog comment).

For EACH person write AT MOST 2 short sentences (up to ~220 characters in total) in English on what \
they did — naturally, like a colleague at the daily, in the 3rd person, without the name (the name \
is already in the post). For the main tasks give the time in brackets exactly as in the "time" \
field, e.g. "(2h 30m)". Gather small things (daily, meetings, refinement, code review) into one \
short "plus meetings". Do not give Jira keys. Do not make up anything that is not in the data.

Also a "_intro" field: one short sentence on what the team was busy with that day (the main threads).

Return ONLY a JSON object {{"_intro": "...", "<id>": "...", ...}} — no markdown, no comment.

Data:
{data}
"""


def summarize(team: str, day: date, members: list[MemberDay],
              call_claude: Callable[[str], str]) -> str:
    """Fill member.summary (fallback: task titles) and return the team intro ('' if none)."""
    logged = [m for m in members if m.status == "logged"]
    for m in logged:
        m.summary = _fallback_summary(m)
    if not logged:
        return ""
    payload = {
        str(i): {"first_name": m.name.split()[0],
                 "tasks": [{"title": w.summary, "time": fmt_hours(w.hours),
                            "comment": w.comment[:300]} for w in m.worklogs]}
        for i, m in enumerate(logged)
    }
    prompt = _SUMMARY_PROMPT.format(team=team, day_label=_day_label(day),
                                    data=json.dumps(payload, ensure_ascii=False, indent=1))
    raw = call_claude(prompt)
    parsed = _parse_json_object(raw)
    if not parsed:
        print("[worklog-daily] AI summary failed -- using task titles.",
              file=sys.stderr, flush=True)
        return ""
    for i, m in enumerate(logged):
        text = str(parsed.get(str(i), "")).strip()
        if text:
            m.summary = text
    return str(parsed.get("_intro", "")).strip()


def _parse_json_object(raw: str) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _fallback_summary(m: MemberDay) -> str:
    return "; ".join(f"{w.summary} ({fmt_hours(w.hours)})" for w in m.worklogs[:4])


# ---------------------------------------------------------------------------
# Post
# ---------------------------------------------------------------------------


def _day_label(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]} {day.strftime('%d.%m')}"


@dataclass
class Post:
    markdown: str                  # with \x00P{i}\x00 tokens where a person goes
    people: list[MemberDay]        # token i -> person
    tag: list[bool]                # token i -> @mention (True) or bold name


def build_post(team: str, day: date, members: list[MemberDay], intro: str = "") -> Post:
    people: list[MemberDay] = []
    tag: list[bool] = []

    def person(m: MemberDay, mention: bool) -> str:
        people.append(m)
        tag.append(mention)
        return f"\x00P{len(people) - 1}\x00"

    logged = sorted((m for m in members if m.status == "logged"), key=lambda m: -m.hours)
    missing = [m for m in members if m.status == "missing"]
    vacation = [m for m in members if m.status == "vacation"]
    total = sum(m.hours for m in logged)
    expected = len(members) - len(vacation) - sum(1 for m in members if m.status == "no_jira")

    when = f"on {_WEEKDAYS[day.weekday()]}"
    lines = [f"**📋 Daily {team} — what happened {when} "
             f"({day.strftime('%d.%m')})**", ""]
    lines.append(f"Logged: **{fmt_hours(total)}** · {len(logged)}/{max(expected, 0)} people"
                 + (f" · 🌴 on leave: {len(vacation)}" if vacation else ""))
    if intro:
        lines += ["", intro]
    lines.append("")
    if missing:
        who = ", ".join(person(m, True) for m in missing)
        lines.append(f"⏰ **No log {when}:** {who} — "
                     f"please fill in Tempo 🙏")
    else:
        lines.append(f"✅ Everyone present logged time {when} — thanks!")
    if vacation:
        lines.append("🌴 On leave: " + ", ".join(m.name for m in vacation))
    for m in logged:
        keys = " · ".join(f"[{w.key}]({JIRA_BROWSE}{w.key})"
                          for w in m.worklogs[:_MAX_KEYS] if w.key)
        if len(m.worklogs) > _MAX_KEYS:
            keys += f" · +{len(m.worklogs) - _MAX_KEYS}"
        lines += ["", f"👤 {person(m, False)} — **{fmt_hours(m.hours)}**", m.summary]
        if keys:
            lines.append(keys)
    return Post("\n".join(lines), people, tag)


def control_report(team: str, day: date, members: list[MemberDay], leave_fetched_at: str,
                   untagged: list[str]) -> str:
    """Short status for the owner's notes chat: coverage, problems the team post does not show."""
    counts: dict[str, int] = {}
    for m in members:
        counts[m.status] = counts.get(m.status, 0) + 1
    parts = [f"[worklog-daily] {team} for {day.isoformat()}: "
             + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
             + f" (members: {len(members)})"]
    errors = [m.name for m in members if m.status == "error"]
    if errors:
        parts.append("❌ ERROR fetching from Jira (after retries): " + ", ".join(errors)
                     + " — the post did NOT go to the team channel.")
    no_jira = [m.name for m in members if m.status == "no_jira"]
    if no_jira:
        parts.append("⚠️ No Jira account (skipped): " + ", ".join(no_jira))
    if untagged:
        parts.append("⚠️ No @tag (no MRI in worklog-teams.json / Bob's profiles): "
                     + ", ".join(untagged))
    if leave_fetched_at:
        try:
            age = (datetime.now(datetime.fromisoformat(leave_fetched_at).tzinfo)
                   - datetime.fromisoformat(leave_fetched_at)).days
            if age >= _LEAVE_STALE_DAYS:
                parts.append(f"⚠️ Leave data is {age} days old — missing logs may be leave.")
        except ValueError:
            pass
    else:
        parts.append("⚠️ No leave data — missing logs may be leave.")
    return "\n".join(parts)


def build_missing_post(day: date, by_team: dict[str, list[MemberDay]]) -> Post:
    """Midday recap for the managers' chat: who has no log and who
    is on vacation, per team. The people listed are not in that chat -- names are bold,
    never @tagged, and the managers are asked to pass it on."""
    people: list[MemberDay] = []
    tag: list[bool] = []

    def person(m: MemberDay, mention: bool) -> str:
        people.append(m)
        tag.append(mention)
        return f"\x00P{len(people) - 1}\x00"

    when = f"on {_WEEKDAYS[day.weekday()]}"
    lines = [f"**⏰ Time logging — status at 12:00 for {_day_label(day)}**", ""]
    n_missing = 0
    for team, members in by_team.items():
        missing = [m for m in members if m.status == "missing"]
        n_missing += len(missing)
        lines.append(f"**{team}:** " + (", ".join(person(m, False) for m in missing)
                                        if missing else "✅ complete"))
    vacation = [m.name for ms in by_team.values() for m in ms if m.status == "vacation"]
    lines += ["", "🌴 **On leave " + when + ":** " + (", ".join(vacation) if vacation else "nobody")]
    if n_missing:
        lines += ["", f"Please pass this on to the teams — these people need to fill in Tempo for "
                      f"{_WEEKDAYS[day.weekday()]} 🙏"]
    else:
        lines += ["", f"✅ Everyone present logged time {when} — thanks!"]
    return Post("\n".join(lines), people, tag)


def harvest_mris(messages: list[dict], path: Path = ROSTER_FILE) -> list[str]:
    """Fill missing roster `mri` from chat history (sender display name -> sender MRI).

    A member gets an MRI only when exactly one distinct MRI in *messages* has a sender
    name with the same tokens ("EXT Nowak Ewa" == "Ewa Nowak"). Existing
    `mri` values are never overwritten. Returns the names that got one."""
    seen: dict[frozenset, set[str]] = {}
    for msg in messages:
        mri, sender = str(msg.get("sender_mri") or ""), str(msg.get("sender") or "")
        if mri.startswith("8:orgid:") and sender:
            seen.setdefault(_name_tokens(sender), set()).add(mri)
    # Several team jobs start at the same minute (separate processes): read-modify-write
    # under an flock, and replace the file atomically so load_roster never sees half of it.
    with open(path.with_name(path.name + ".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text(encoding="utf-8"))
        added = []
        for team in data.get("teams", {}).values():
            for m in team.get("members", []):
                hits = seen.get(_name_tokens(m["name"]), set())
                if not m.get("mri") and len(hits) == 1:
                    m["mri"] = next(iter(hits))
                    added.append(m["name"])
        if added:
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
    return added
