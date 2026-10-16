"""Guard: no private value may enter this public repo.

Two layers, both run over every tracked text file AND every tracked path:

1. Structural rules, in clear text (safe to publish -- they name shapes, not values):
   real Teams user/chat IDs, e-mail domains, SharePoint tenants, Jira hosts, and the
   fingerprints of the original instance (store codes, postcodes, Jira custom-field ids,
   personal logins). Each has a short placeholder allow-list.
2. A hashed denylist of names and organisation words: SHA-256 of each lowercase,
   accent-folded token and token bigram. The list is NOT in the repo -- plain hashes of
   guessable words are reversible with a word list -- so it is read from
   $BOB_PRIVATE_DENYLIST (default ~/.bob-anon/private-denylist.sha256) and this layer is
   skipped where the file is absent (CI). The owner's pre-commit hook runs it.

A failure names file:line and the rule, never the value it matched.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DENYLIST = Path(os.environ.get("BOB_PRIVATE_DENYLIST", Path.home() / ".bob-anon" / "private-denylist.sha256"))

# The four README pictures: rendered by tools/readme_images.py from the committed sources, every chunk outside the
# allow-list stripped, pinned by tests/test_readme_images.py.
REVIEWED_BINARIES: frozenset[str] = frozenset({
    "docs/images/hero-bob-in-teams.png",
    "docs/images/why-bob.png",
    "docs/images/a-day-with-bob.png",
    "docs/images/architecture.png",
})

_ZERO_GUID_PREFIX = "00000000-0000-0000-0000-"
_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_EMAIL_OK = ("example.com", "example.test", "anthropic.com")
# Teams IDs look like addresses ("19:…@thread.v2", "…@unq.gbl.spaces"); their shape has its own rules.
_TEAMS_ID_DOMAINS = ("thread.v2", "thread.skype", "thread.tacv2", "unq.gbl.spaces")
# "tenant"/"my": the <tenant>.sharepoint.com pattern in docs; "evilcontoso": a lookalike-host test.
_SHAREPOINT_OK = {"contoso", "contoso-my", "tenant", "my", "-my", "evilcontoso"}
_LOGIN_OK = {"a.nowak", "e.nowak", "j.kowalski", "jan.kowalski", "a.kowalska", "r.width", "r.height"}

# (rule name, compiled pattern, predicate(match) -> True when the match is a placeholder)
STRUCTURAL_RULES = [
    ("teams-user-id", re.compile(r"8:orgid:(" + _GUID + ")"),
     lambda m: m.group(1).lower().startswith(_ZERO_GUID_PREFIX)),
    ("teams-meeting-chat-id", re.compile(r"19:meeting_[A-Za-z0-9_-]{8,}"), lambda m: False),
    ("teams-thread-id", re.compile(r"19:([0-9a-f]{16,})@thread"), lambda m: m.group(1).startswith("0" * 16)),
    ("teams-1on1-chat-id", re.compile(r"19:(" + _GUID + ")_(" + _GUID + ")@unq"),
     lambda m: m.group(1).startswith(_ZERO_GUID_PREFIX) and m.group(2).startswith(_ZERO_GUID_PREFIX)),
    ("email-domain", re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)"),
     lambda m: m.group(1).lower().endswith(_EMAIL_OK) or m.group(1).lower() in _TEAMS_ID_DOMAINS),
    ("sharepoint-tenant", re.compile(r"\b([a-z0-9-]+)\.sharepoint\.com", re.I),
     lambda m: m.group(1).lower() in _SHAREPOINT_OK),
    ("jira-host", re.compile(r"\b((?:jira|[a-z0-9-]+\.atlassian)\.[a-z0-9.-]+\.[a-z]{2,})", re.I),
     lambda m: m.group(1).lower() in ("jira.example.com", "jira.example.test")),
    ("store-code", re.compile(r"\bZA?\d{3,4}\b"), lambda m: False),
    ("postcode", re.compile(r"(?<![\w-])\d{2}-\d{3}(?![\w-])"), lambda m: m.group(0) == "00-000"),
    ("jira-custom-field", re.compile(r"customfield_(\d{5})"), lambda m: m.group(1).startswith("10")),
]
_LOGIN = re.compile(r"(?<![\w.{@/])[a-z]\.(?!(?:png|gif|jpe?g|json|txt|md|py|html|xlsx|docx|mp3|wav|log|csv|pdf|svg)\b)"
                    r"[a-z]{3,}(?![\w(.\[}@/])")


# ---------------------------------------------------------------------------
# Normalisation (make_denylist.py outside the repo uses exactly the same)
# ---------------------------------------------------------------------------

def fold(s: str) -> str:
    s = s.replace("ł", "l").replace("Ł", "L")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def token_hashes(line: str) -> set[str]:
    """SHA-256 of every token and every adjacent-token bigram on one line."""
    # Literal \n / \t escapes inside strings are word boundaries, not letters.
    toks = re.findall(r"[a-z0-9]+", fold(re.sub(r"\\[ntr]", " ", line)))
    grams = set(toks) | {f"{a} {b}" for a, b in zip(toks, toks[1:], strict=False)}
    return {hashlib.sha256(g.encode()).hexdigest() for g in grams}


def load_denylist(path: Path) -> frozenset[str]:
    return frozenset(s.strip() for s in path.read_text(encoding="utf-8").splitlines() if s.strip())


# ---------------------------------------------------------------------------
# File set: what would be published
# ---------------------------------------------------------------------------

def tracked_files(root: Path) -> list[str]:
    """What `git add -A` would publish: tracked plus untracked, unignored files (a temp git dir outside a repo)."""
    git = shutil.which("git")
    if git and (root / ".git").exists():
        out = subprocess.run([git, "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True).stdout
    elif git:
        with tempfile.TemporaryDirectory() as gd:
            subprocess.run([git, "init", "-q", gd], check=True)
            out = subprocess.run([git, f"--git-dir={gd}/.git", f"--work-tree={root}", "ls-files", "-z",
                                  "--others", "--exclude-standard"], capture_output=True, check=True, cwd=root).stdout
    else:
        pytest.skip("git not available")
    return sorted(p for p in out.decode().split("\0") if p and (root / p).is_file())


def _python_text_spans(txt: str) -> list[tuple[int, int]]:
    """Line/column ranges of string literals and comments (where a login is text, not code)."""
    import io
    import tokenize
    kinds = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1), tokenize.COMMENT}
    try:
        return [(t.start, t.end) for t in tokenize.generate_tokens(io.StringIO(txt).readline) if t.type in kinds]
    except (tokenize.TokenError, SyntaxError):
        return [((1, 0), (10**9, 0))]


def _inside_fstring_expression(line: str, col: int) -> bool:
    before = line[:col].replace("{{", "").replace("}}", "")
    return before.rfind("{") > before.rfind("}")


def scan(root: Path, files: list[str], denylist: frozenset[str] = frozenset()) -> list[str]:
    """Return 'file:line: rule' findings (paths use line 0). Never includes the value."""
    findings: list[str] = []
    for rel in files:
        if denylist and token_hashes(rel) & denylist:
            findings.append(f"{rel}:0: denylisted name/word in path")
        try:
            txt = (root / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            if rel not in REVIEWED_BINARIES:
                findings.append(f"{rel}:0: binary file not reviewed (add to REVIEWED_BINARIES after checking metadata)")
            continue
        spans = _python_text_spans(txt) if rel.endswith(".py") else None
        for lineno, line in enumerate(txt.splitlines(), 1):
            for rule, rx, is_placeholder in STRUCTURAL_RULES:
                if any(not is_placeholder(m) for m in rx.finditer(line)):
                    findings.append(f"{rel}:{lineno}: {rule}")
            for m in _LOGIN.finditer(line):
                if m.group(0) in _LOGIN_OK:
                    continue
                if spans is not None and not any(a <= (lineno, m.start()) < b for a, b in spans):
                    continue  # attribute access in code, not a login
                if spans is not None and _inside_fstring_expression(line, m.start()):
                    continue  # {obj.attr} in an f-string (Python < 3.12 tokenizes it as one STRING)
                findings.append(f"{rel}:{lineno}: login-shaped token")
            if denylist and token_hashes(line) & denylist:
                findings.append(f"{rel}:{lineno}: denylisted name/word")
    return findings


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

def test_tracked_files_hold_no_private_values():
    denylist = load_denylist(DENYLIST) if DENYLIST.is_file() else frozenset()
    findings = scan(ROOT, tracked_files(ROOT), denylist)
    assert not findings, "private-looking values (fix or use a placeholder):\n" + "\n".join(findings)


def test_hashed_denylist_layer_ran_or_is_reported_skipped():
    if not DENYLIST.is_file():
        pytest.skip(f"hashed denylist not present ({DENYLIST}) -- structural rules only")
    assert len(load_denylist(DENYLIST)) > 0


# ---------------------------------------------------------------------------
# Self-test: the guard catches what it should and passes the example configs
# ---------------------------------------------------------------------------

def _planted_tree(tmp_path: Path) -> Path:
    real_shaped_mri = "8:orgid:" + "3f2a9c1e-7b4d-4e8a-9c2f-" + "5d1e8a7b6c40"  # assembled: not a literal here
    (tmp_path / "notes.md").write_text(f"Ping Plantedname Zzyx\nmri {real_shaped_mri}\nok line\n", encoding="utf-8")
    (tmp_path / "plantedname").mkdir()
    (tmp_path / "plantedname" / "x.txt").write_text("nothing here\n", encoding="utf-8")
    return tmp_path


def test_self_test_planted_values_fail_without_echoing_them(tmp_path):
    root = _planted_tree(tmp_path)
    deny = frozenset({hashlib.sha256(b"plantedname zzyx").hexdigest(), hashlib.sha256(b"plantedname").hexdigest()})
    findings = scan(root, ["notes.md", "plantedname/x.txt"], deny)
    assert "notes.md:1: denylisted name/word" in findings
    assert "notes.md:2: teams-user-id" in findings
    assert "plantedname/x.txt:0: denylisted name/word in path" in findings
    assert not any("3f2a9c1e" in f or "Zzyx" in f for f in findings)


def test_self_test_folding_catches_case_and_accents(tmp_path):
    (tmp_path / "a.py").write_text('X = "ŁUKASZ_ŻÓŁW"  # test_łukasz_żółw\n', encoding="utf-8")
    deny = frozenset({hashlib.sha256(b"lukasz zolw").hexdigest()})
    assert scan(tmp_path, ["a.py"], deny) == ["a.py:1: denylisted name/word"]


def test_self_test_fingerprints_and_their_placeholders(tmp_path):
    # Assembled at runtime so this file does not trip its own guard.
    lines = [
        "store " + "Z" + "1234",                            # 1 store-code
        "kod 12" + "-345 Miasto",                           # 2 postcode
        "customfield" + "_29001",                           # 3 jira-custom-field
        "mail ewa" + "@corp.pl",                            # 4 email-domain
        "https://acmecorp" + ".sharepoint.com",             # 5 sharepoint-tenant
        "login b" + ".kowal",                               # 6 login-shaped token
        "ok: customfield_10101 ewa@example.com https://contoso.sharepoint.com a.nowak 00-000",
        "ok: 8:orgid:00000000-0000-0000-0000-000000000042 19:" + "0" * 32 + "@thread.v2",
    ]
    (tmp_path / "f.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rules = {f.split(": ", 1)[1] for f in scan(tmp_path, ["f.md"])}
    assert rules == {"store-code", "postcode", "jira-custom-field", "email-domain", "sharepoint-tenant",
                     "login-shaped token"}
    assert all(f.split(":")[1] in "123456" for f in scan(tmp_path, ["f.md"]))


def test_self_test_login_in_code_is_not_a_login(tmp_path):
    code = ("def f(m):\n    return m" + ".group(1) + m" + ".status  # see a.nowak\nX = \"b" + ".kowal\"\n"
            "Y = f\"{m" + ".hours}h\"\n")
    (tmp_path / "m.py").write_text(code, encoding="utf-8")
    assert scan(tmp_path, ["m.py"]) == ["m.py:3: login-shaped token"]


def test_self_test_unreviewed_binary_fails(tmp_path):
    (tmp_path / "logo.gif").write_bytes(b"GIF89a\x00\xff\xfe")
    assert scan(tmp_path, ["logo.gif"]) == [
        "logo.gif:0: binary file not reviewed (add to REVIEWED_BINARIES after checking metadata)"]


def test_example_configs_pass():
    examples = [p for p in tracked_files(ROOT) if ".example." in Path(p).name or p == ".env.example"]
    assert len(examples) >= 6
    denylist = load_denylist(DENYLIST) if DENYLIST.is_file() else frozenset()
    assert scan(ROOT, examples, denylist) == []


def test_gitignored_paths_are_not_scanned(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not available")
    (tmp_path / ".gitignore").write_text("secret.json\n", encoding="utf-8")
    (tmp_path / "secret.json").write_text('{"x": "anything"}', encoding="utf-8")
    (tmp_path / "public.md").write_text("hi\n", encoding="utf-8")
    assert tracked_files(tmp_path) == [".gitignore", "public.md"]


# ---------------------------------------------------------------------------
# Runtime state, live configs and credentials can never become tracked
# ---------------------------------------------------------------------------

SENSITIVE_PATHS = """
.env
chat-config.json
chat-config.tmp
mcp-config.json
.mcp-config.json.123.456.tmp
scripts/broadcast/jobs.json
scripts/broadcast/worklog-teams.json
scripts/broadcast/worklog-teams.json.123.tmp
scripts/broadcast/broadcast-schedule.json
scripts/teams_agent/people.json
config/jira-fields.json
scripts/todos/todos.json
plans/2026-10-13-x.md
.dark-army/state.json
.claude/settings.local.json
.claude/settings.json
scout/2026-10-14-x/report.md
.github/workflows/deploy.yml
knowledge/risks/r.md
knowledge/urlopy/urlopy.json
knowledge/daily-recap/2026-10-13.md
scripts/teams_agent/log.jsonl
scripts/otel/otel-spans.jsonl
scripts/broadcast/broadcast.log.jsonl
eval-results.jsonl
scripts/jira/jira-comments.log.jsonl
scripts/teams_agent/session.json
scripts/teams_agent/session.tmp.json
scripts/teams_agent/session-backup.json
scripts/teams_agent/sp-cookies.json
scripts/teams_agent/.auth-token
scripts/teams_agent/.ic3-auth-token
scripts/teams_agent/.auth-token.4242.tmp
scripts/teams_agent/sharepoint_mtimes.tmp.json
scripts/teams_agent/teams-read_messages-diag.txt
scripts/teams_agent/user-data/x.json
scripts/teams_agent/history/c.json
scripts/teams_agent/owner-schedule.json
user-data/x.json
source_data/x.md
manual-check/2026-10-14-x/check.md
.github/workflows/deploy.yml
AGENTS.md
docs/context.md
.agents/skills/x/SKILL.md
.codex/config.toml
.grok/config.toml
.claude/skills/ship/SKILL.md
.claude/agents/bps-planner.md
.claude/leads/gate.md
""".split()


def test_sensitive_runtime_paths_are_gitignored():
    """Every live config / log / session / token path the code writes must stay ignored."""
    git = shutil.which("git")
    if not git:
        pytest.skip("git not available")
    with tempfile.TemporaryDirectory() as gd:
        subprocess.run([git, "init", "-q", gd], check=True)
        out = subprocess.run([git, f"--git-dir={gd}/.git", f"--work-tree={ROOT}", "check-ignore", "--no-index",
                              "--stdin"], input="\n".join(SENSITIVE_PATHS), capture_output=True, text=True,
                             cwd=ROOT).stdout
    assert sorted(set(SENSITIVE_PATHS) - set(out.split())) == []
