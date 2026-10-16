"""Daily worklog post — what each team member logged in Tempo on the previous workday.

Generator for broadcast jobs with "generator": "worklog-daily" (dispatched from
broadcast_runner.run_job). Data sources:

- roster:    scripts/broadcast/worklog-teams.json (Bob's own copy, edit it there)
- worklogs:  Jira Tempo REST, queried PER PERSON (all projects), straight from Jira
- vacations: the vacation calendar (SharePoint list -> knowledge/urlopy/urlopy.json),
             matched by e-mail; a person on leave is only marked 🌴, never shown with hours
- summary:   one Claude call per team (no tools) turns the fetched worklogs into
             1-2 sentences per person; the post itself is assembled here.

Coverage guarantee: every roster member ends up in exactly one bucket (logged /
missing / vacation / no Jira account / fetch error). A fetch error after retries
blocks posting to the team channel — the preview goes to Notatki instead.

Only Monday-Friday: on Monday the post covers Friday; Polish public holidays are
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
URLOPY_FILE = _PROJECT_ROOT / "knowledge" / "urlopy" / "urlopy.json"
JIRA_BROWSE = org_config.JIRA_BROWSE_URL

_FETCH_ATTEMPTS = 3
_FETCH_RETRY_S = 2
_URLOPY_STALE_DAYS = 3
_MAX_KEYS = 4  # ticket links per person, largest first

_NAME_NOISE = {"x", *org_config.NAME_NOISE_PREFIXES}  # "x" = the inactive marker "[X]"

_WEEKDAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
_WEEKDAYS_PL_LOC = ["w poniedziałek", "we wtorek", "w środę", "w czwartek", "w piątek",
                    "w sobotę", "w niedzielę"]


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
        days.add(date(year, 12, 24))  # Wigilia -- dzien wolny od 2025
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
        raise KeyError(f"Brak zespolu '{team}' w {path.name}")
    return [m for m in team_cfg.get("members", []) if not m.get("skip")]


def load_vacations(day: date, path: Path = URLOPY_FILE) -> tuple[set[str], str]:
    """E-mails (lowercase) on vacation on *day*, plus the data's fetched_at ('' if unknown)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[worklog-daily] urlopy niedostepne: {exc}", file=sys.stderr, flush=True)
        return set(), ""
    out: set[str] = set()
    for rec in data.get("records", []):
        try:
            first = datetime.fromisoformat(rec["Pierwszy dzień urlopu"]).date()
            last = datetime.fromisoformat(rec["Ostatni dzień urlopu"]).date()
        except (KeyError, TypeError, ValueError):
            continue
        if first <= day <= last:
            out.add(str(rec.get("Adres e-mail", "")).strip().lower())
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
    Jira account is not on the roster e-mail)."""
    users = jira_get("/rest/api/2/user/search", {"username": email, "includeInactive": "true"})
    if users is None:
        return None
    want = email.lower()
    for u in users:
        if want in (str(u.get("name", "")).lower(), str(u.get("emailAddress", "")).lower()):
            return u["name"]
    if not name:
        return ""
    last = name.split()[-1]
    users = jira_get("/rest/api/2/user/search",
                     {"username": last, "maxResults": 50, "includeInactive": "true"})
    if users is None:
        return None
    hits = [u for u in users if _name_tokens(str(u.get("displayName", ""))) == _name_tokens(name)]
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
            print(f"[worklog-daily] Tempo zwrocilo worklog {author!r} dla {username!r}",
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


_SUMMARY_PROMPT = """Jesteś Bob, asystent zespołu {team}. Poniżej worklogi z Tempo za {day_label} \
(JSON: id osoby -> imię i lista zadań z tytułem, czasem i komentarzem do worklogu).

Dla KAŻDEJ osoby napisz MAKSYMALNIE 2 krótkie zdania (łącznie do ~220 znaków) po polsku, co \
robiła — naturalnie, jak kolega na daily, w 3. osobie, bez imienia (imię jest już w poście). \
Rodzaj gramatyczny dopasuj do imienia z pola "imie" (Adrian -> "pracował", Ewa -> \
"pracowała"); gdy imię nie przesądza płci, użyj form bezosobowych ("praca nad..."). Przy \
głównych zadaniach podaj czas w nawiasie dokładnie tak, jak w polu "czas", np. "(2h 30m)". \
Drobne rzeczy (daily, spotkania, refinement, code review) zbierz w jedno krótkie "plus \
spotkania". Nie podawaj kluczy Jira. Nie wymyślaj niczego, czego nie ma w danych.

Dodatkowo pole "_intro": jedno krótkie zdanie o tym, czym zespół żył tego dnia (główne wątki).

Zwróć WYŁĄCZNIE obiekt JSON {{"_intro": "...", "<id>": "...", ...}} — bez markdownu, bez komentarza.

Dane:
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
        str(i): {"imie": m.name.split()[0],
                 "zadania": [{"tytul": w.summary, "czas": fmt_hours(w.hours),
                              "komentarz": w.comment[:300]} for w in m.worklogs]}
        for i, m in enumerate(logged)
    }
    prompt = _SUMMARY_PROMPT.format(team=team, day_label=_day_label(day),
                                    data=json.dumps(payload, ensure_ascii=False, indent=1))
    raw = call_claude(prompt)
    parsed = _parse_json_object(raw)
    if not parsed:
        print("[worklog-daily] streszczenie AI nieudane -- uzywam tytulow zadan.",
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
    return f"{_WEEKDAYS_PL[day.weekday()]} {day.strftime('%d.%m')}"


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

    lines = [f"**📋 Daily {team} — co działo się {_WEEKDAYS_PL_LOC[day.weekday()]} "
             f"({day.strftime('%d.%m')})**", ""]
    lines.append(f"Zalogowane: **{fmt_hours(total)}** · {len(logged)}/{max(expected, 0)} osób"
                 + (f" · 🌴 urlop: {len(vacation)}" if vacation else ""))
    if intro:
        lines += ["", intro]
    lines.append("")
    if missing:
        who = ", ".join(person(m, True) for m in missing)
        lines.append(f"⏰ **Brak logu {_WEEKDAYS_PL_LOC[day.weekday()]}:** {who} — "
                     f"uzupełnijcie proszę Tempo 🙏")
    else:
        lines.append(f"✅ Wszyscy obecni zalogowali czas {_WEEKDAYS_PL_LOC[day.weekday()]} — dzięki!")
    if vacation:
        lines.append("🌴 Urlop: " + ", ".join(m.name for m in vacation))
    for m in logged:
        keys = " · ".join(f"[{w.key}]({JIRA_BROWSE}{w.key})"
                          for w in m.worklogs[:_MAX_KEYS] if w.key)
        if len(m.worklogs) > _MAX_KEYS:
            keys += f" · +{len(m.worklogs) - _MAX_KEYS}"
        lines += ["", f"👤 {person(m, False)} — **{fmt_hours(m.hours)}**", m.summary]
        if keys:
            lines.append(keys)
    return Post("\n".join(lines), people, tag)


def control_report(team: str, day: date, members: list[MemberDay], urlopy_fetched_at: str,
                   untagged: list[str]) -> str:
    """Short status for Notatki: coverage, problems the team post does not show."""
    counts: dict[str, int] = {}
    for m in members:
        counts[m.status] = counts.get(m.status, 0) + 1
    parts = [f"[worklog-daily] {team} za {day.isoformat()}: "
             + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
             + f" (sklad: {len(members)})"]
    errors = [m.name for m in members if m.status == "error"]
    if errors:
        parts.append("❌ Blad pobierania z Jiry (po ponowieniach): " + ", ".join(errors)
                     + " — post NIE poszedl na kanal zespolu.")
    no_jira = [m.name for m in members if m.status == "no_jira"]
    if no_jira:
        parts.append("⚠️ Brak konta w Jirze (pominieci): " + ", ".join(no_jira))
    if untagged:
        parts.append("⚠️ Bez @tagu (brak MRI w worklog-teams.json / profilach Boba): "
                     + ", ".join(untagged))
    if urlopy_fetched_at:
        try:
            age = (datetime.now(datetime.fromisoformat(urlopy_fetched_at).tzinfo)
                   - datetime.fromisoformat(urlopy_fetched_at)).days
            if age >= _URLOPY_STALE_DAYS:
                parts.append(f"⚠️ Dane urlopowe sprzed {age} dni — braki moga byc urlopami.")
        except ValueError:
            pass
    else:
        parts.append("⚠️ Brak danych urlopowych — braki moga byc urlopami.")
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

    when = _WEEKDAYS_PL_LOC[day.weekday()]
    lines = [f"**⏰ Logowanie czasu — stan na 12:00 za {_day_label(day)}**", ""]
    n_missing = 0
    for team, members in by_team.items():
        missing = [m for m in members if m.status == "missing"]
        n_missing += len(missing)
        lines.append(f"**{team}:** " + (", ".join(person(m, False) for m in missing)
                                        if missing else "✅ komplet"))
    vacation = [m.name for ms in by_team.values() for m in ms if m.status == "vacation"]
    lines += ["", "🌴 **Urlop " + when + ":** " + (", ".join(vacation) if vacation else "nikt")]
    if n_missing:
        lines += ["", f"Przekażcie proszę zespołom — te osoby muszą uzupełnić Tempo za "
                      f"{_WEEKDAYS_PL[day.weekday()]} 🙏"]
    else:
        lines += ["", f"✅ Wszyscy obecni zalogowali czas {when} — dzięki!"]
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
