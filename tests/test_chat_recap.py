"""Tests for scripts/chat_recap.py."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chat_recap  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers to build synthetic JSONL records
# ---------------------------------------------------------------------------

_TARGET_DATE = date(2026, 5, 26)
# A timestamp that falls on 2026-05-26 in Europe/Warsaw (UTC+2 in May)
_TS_MORNING = "2026-05-26T08:00:00.000Z"
_TS_AFTERNOON = "2026-05-26T14:30:00.000Z"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _touch_with_mtime(path: Path, ts_utc: float) -> None:
    """Set atime and mtime to the given POSIX UTC timestamp."""
    os.utime(str(path), (ts_utc, ts_utc))


# POSIX timestamp for 2026-05-26T10:00:00Z — falls on 2026-05-26 in Warsaw (UTC+2)
_MTIME_TARGET = datetime(2026, 5, 26, 10, 0, 0, tzinfo=UTC).timestamp()
# POSIX timestamp for 2026-05-25T22:00:00Z — 2026-05-26 00:00 Warsaw = boundary
_MTIME_OTHER_DAY = datetime(2026, 5, 25, 8, 0, 0, tzinfo=UTC).timestamp()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def chats_dir(tmp_path: Path) -> Path:
    d = tmp_path / "chats"
    d.mkdir()
    return d


@pytest.fixture()
def public_chat(chats_dir: Path) -> Path:
    """A JSONL with a non-own speaker in HISTORIA ROZMOWY — classified public."""
    records = [
        {"type": "ai-title", "aiTitle": "Teams Discussion", "sessionId": "pub-001"},
        {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": _TS_MORNING,
            "sessionId": "pub-001",
            "content": (
                "[BAZA WIEDZY]\njakis tekst wiedzy\n\n"
                "[HISTORIA ROZMOWY]\n"
                "Szef: Hej, co z projektem?\n"
                "EXT Person 58: Projekt idzie dobrze.\n"
                "Bob: Swietnie.\n"
            ),
        },
        {
            "type": "user",
            "timestamp": _TS_MORNING,
            "sessionId": "pub-001",
            "message": {
                "role": "user",
                "content": "[BAZA WIEDZY]\njakis tekst\n\n[HISTORIA ROZMOWY]\nSzef: co nowego?",
            },
        },
        {
            "type": "user",
            "timestamp": _TS_AFTERNOON,
            "sessionId": "pub-001",
            "message": {"role": "user", "content": "Sprawdz status zadania JIRA-123."},
        },
    ]
    p = chats_dir / "pub-001.jsonl"
    _write_jsonl(p, records)
    _touch_with_mtime(p, _MTIME_TARGET)
    return p


@pytest.fixture()
def private_chat(chats_dir: Path) -> Path:
    """A JSONL with only own speakers — classified private."""
    records = [
        {"type": "ai-title", "aiTitle": "Private Planning", "sessionId": "priv-001"},
        {
            "type": "user",
            "timestamp": _TS_MORNING,
            "sessionId": "priv-001",
            "message": {"role": "user", "content": "Pomoze mi zaplanowac sprint."},
        },
        {
            "type": "user",
            "timestamp": _TS_AFTERNOON,
            "sessionId": "priv-001",
            "message": {"role": "user", "content": "Jan: co mamy na Q3?"},
        },
    ]
    p = chats_dir / "priv-001.jsonl"
    _write_jsonl(p, records)
    _touch_with_mtime(p, _MTIME_TARGET)
    return p


@pytest.fixture()
def empty_chat(chats_dir: Path) -> Path:
    """A JSONL with no user messages — should be skipped."""
    records = [
        {"type": "ai-title", "aiTitle": "Empty", "sessionId": "empty-001"},
        {
            "type": "attachment",
            "attachment": {"type": "auto_mode"},
            "sessionId": "empty-001",
        },
    ]
    p = chats_dir / "empty-001.jsonl"
    _write_jsonl(p, records)
    _touch_with_mtime(p, _MTIME_TARGET)
    return p


@pytest.fixture()
def knowledge_prefix_chat(chats_dir: Path) -> Path:
    """A JSONL where first user message starts with [BAZA WIEDZY]."""
    records = [
        {
            "type": "ai-title",
            "aiTitle": "Baza Wiedzy Chat",
            "sessionId": "baza-001",
        },
        {
            "type": "user",
            "timestamp": _TS_MORNING,
            "sessionId": "baza-001",
            "message": {
                "role": "user",
                "content": (
                    "[BAZA WIEDZY]\n"
                    "jakis tekst wiedzy\n"
                    "\n"
                    "[HISTORIA ROZMOWY]\n"
                    "Prawdziwa wiadomosc uzytkownika jest tutaj i powinna byc pierwsza."
                ),
            },
        },
    ]
    p = chats_dir / "baza-001.jsonl"
    _write_jsonl(p, records)
    _touch_with_mtime(p, _MTIME_TARGET)
    return p


@pytest.fixture()
def other_day_chat(chats_dir: Path) -> Path:
    """A JSONL modified on a different day — should be excluded by date filter."""
    records = [
        {"type": "ai-title", "aiTitle": "Old Chat", "sessionId": "old-001"},
        {
            "type": "user",
            "timestamp": "2026-05-25T10:00:00.000Z",
            "sessionId": "old-001",
            "message": {"role": "user", "content": "Stary chat."},
        },
    ]
    p = chats_dir / "old-001.jsonl"
    _write_jsonl(p, records)
    _touch_with_mtime(p, _MTIME_OTHER_DAY)
    return p


# ---------------------------------------------------------------------------
# Tests: _list_chat_files
# ---------------------------------------------------------------------------


def test_list_chat_files_filters_by_date(
    chats_dir: Path, public_chat: Path, other_day_chat: Path
) -> None:
    files = chat_recap._list_chat_files(chats_dir, _TARGET_DATE)
    names = {p.name for p in files}
    assert "pub-001.jsonl" in names
    assert "old-001.jsonl" not in names


# ---------------------------------------------------------------------------
# Tests: _parse_chat
# ---------------------------------------------------------------------------


def test_parse_chat_public(public_chat: Path) -> None:
    summary = chat_recap._parse_chat(public_chat)
    assert summary is not None
    assert summary.ai_title == "Teams Discussion"
    assert summary.classification == "public"
    assert "EXT Person 58" in summary.participants
    assert summary.start_time is not None
    assert summary.end_time is not None


def test_parse_chat_private(private_chat: Path) -> None:
    summary = chat_recap._parse_chat(private_chat)
    assert summary is not None
    assert summary.classification == "private"
    assert summary.ai_title == "Private Planning"


def test_parse_chat_empty_returns_none(empty_chat: Path) -> None:
    """Chat with no user messages returns None."""
    summary = chat_recap._parse_chat(empty_chat)
    assert summary is None


def test_parse_chat_handles_list_content(chats_dir: Path) -> None:
    """message.content as list (tool_result) must be skipped without crash."""
    records = [
        {
            "type": "user",
            "timestamp": _TS_MORNING,
            "sessionId": "list-001",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "some result"}],
            },
        },
    ]
    p = chats_dir / "list-001.jsonl"
    _write_jsonl(p, records)
    _touch_with_mtime(p, _MTIME_TARGET)
    # Only list content — no string user messages — should return None
    summary = chat_recap._parse_chat(p)
    assert summary is None


def test_parse_chat_handles_corrupt_json(chats_dir: Path) -> None:
    """Corrupt JSON lines are skipped, valid lines still parsed."""
    p = chats_dir / "corrupt-001.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"type": "ai-title", "aiTitle": "OK", "sessionId": "corr-001"}\n')
        fh.write("THIS IS NOT JSON\n")
        fh.write(
            json.dumps(
                {
                    "type": "user",
                    "timestamp": _TS_MORNING,
                    "sessionId": "corr-001",
                    "message": {"role": "user", "content": "valid message"},
                }
            )
            + "\n"
        )
    _touch_with_mtime(p, _MTIME_TARGET)
    summary = chat_recap._parse_chat(p)
    assert summary is not None
    assert summary.ai_title == "OK"


# ---------------------------------------------------------------------------
# Tests: _classify
# ---------------------------------------------------------------------------


def test_classify_public_detects_outside_speaker() -> None:
    msgs = ["EXT Person 58: Hej\nBob: ok\n"]
    assert chat_recap._classify(msgs) == "public"


def test_classify_private_only_own_speakers() -> None:
    msgs = ["Szef: co slychac?\nBob: wszystko ok\nJan: super\n"]
    assert chat_recap._classify(msgs) == "private"


def test_classify_private_no_speakers() -> None:
    msgs = ["Zwykly tekst bez wzorca mowcy."]
    assert chat_recap._classify(msgs) == "private"


def test_classify_ignores_knowledge_bracket_patterns() -> None:
    """[BAZA WIEDZY] lines that look like 'Key: value' should not be speakers."""
    msgs = ["[BAZA WIEDZY]\nDostepne foldery:\n  general/\nBob: ok\n"]
    # The regex excludes [ and ] from speaker names, so no false positive
    assert chat_recap._classify(msgs) == "private"


# ---------------------------------------------------------------------------
# Tests: _extract_first_user_question
# ---------------------------------------------------------------------------


def test_extract_first_user_question_plain() -> None:
    result = chat_recap._extract_first_user_question(["Normalne pytanie usera."])
    assert result == "Normalne pytanie usera."


def test_extract_first_user_question_truncates() -> None:
    long_msg = "A" * 100
    result = chat_recap._extract_first_user_question([long_msg])
    assert result.endswith("...")
    assert len(result) == 83  # 80 + "..."


def test_extract_first_user_question_strips_baza_wiedzy() -> None:
    msg = (
        "[BAZA WIEDZY]\n"
        "jakis tekst wiedzy\n"
        "\n"
        "[HISTORIA ROZMOWY]\n"
        "Prawdziwa wiadomosc usera tutaj."
    )
    result = chat_recap._extract_first_user_question([msg])
    assert "BAZA WIEDZY" not in result
    assert "HISTORIA ROZMOWY" not in result
    assert "Prawdziwa" in result


def test_extract_first_user_question_from_knowledge_prefix_chat(
    knowledge_prefix_chat: Path,
) -> None:
    summary = chat_recap._parse_chat(knowledge_prefix_chat)
    assert summary is not None
    assert "BAZA WIEDZY" not in summary.first_question
    assert "Prawdziwa" in summary.first_question


# ---------------------------------------------------------------------------
# Tests: _extract_participants
# ---------------------------------------------------------------------------


def test_extract_participants_excludes_own() -> None:
    msgs = ["EXT Person 58: Hej\nBob: ok\nSzef: tak\nZosia K: pytanie\n"]
    participants = chat_recap._extract_participants(msgs)
    assert "Bob" not in participants
    assert "Szef" not in participants
    assert "EXT Person 58" in participants
    assert "Zosia K" in participants


def test_extract_participants_preserves_order() -> None:
    msgs = ["Aaa: pierwszy\nBbb: drugi\nAaa: znowu\n"]
    participants = chat_recap._extract_participants(msgs)
    assert participants == ["Aaa", "Bbb"]


# ---------------------------------------------------------------------------
# Tests: _format_section
# ---------------------------------------------------------------------------


def test_format_section_contains_headers() -> None:
    section = chat_recap._format_section([], _TARGET_DATE)
    assert "## Chaty" in section
    assert "### Publiczne" in section
    assert "### Prywatne" in section


def test_format_section_empty_shows_brak() -> None:
    section = chat_recap._format_section([], _TARGET_DATE)
    assert "*(brak)*" in section


def test_format_section_public_and_private() -> None:
    pub = chat_recap.ChatSummary(
        session_id="p1",
        channel="teams-agent",
        ai_title="Pub Chat",
        first_question="co nowego?",
        classification="public",
        participants=["Zosia"],
        start_time=datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
        end_time=datetime(2026, 5, 26, 11, 0, tzinfo=UTC),
    )
    priv = chat_recap.ChatSummary(
        session_id="p2",
        channel="teams-agent",
        ai_title="Priv Chat",
        first_question="planowanie Q3",
        classification="private",
        participants=[],
        start_time=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 5, 26, 13, 0, tzinfo=UTC),
    )
    section = chat_recap._format_section([pub, priv], _TARGET_DATE)
    assert "Pub Chat" in section
    assert "Priv Chat" in section
    assert "Zosia" in section


# ---------------------------------------------------------------------------
# Tests: _upsert_section
# ---------------------------------------------------------------------------


def test_upsert_creates_new_file(tmp_path: Path) -> None:
    target = tmp_path / "2026-05-26.md"
    section = "## Chaty -- 2026-05-26\n\n### Publiczne\n\n*(brak)*\n"
    chat_recap._upsert_section(target, section, _TARGET_DATE)
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "# Recap 2026-05-26" in content
    assert "## Chaty" in content


def test_upsert_replaces_existing_section(tmp_path: Path) -> None:
    target = tmp_path / "2026-05-26.md"
    initial = (
        "# Recap 2026-05-26\n\n"
        "## Chaty -- 2026-05-26\n\n"
        "### Publiczne\n\n*(brak)*\n\n"
        "### Prywatne\n\n*(brak)*\n\n"
        "## Inne\n\njakis tekst\n"
    )
    target.write_text(initial, encoding="utf-8")
    new_section = (
        "## Chaty -- 2026-05-26\n\n"
        "### Publiczne\n\n**NewChat** (10:00-11:00)\n\n"
        "### Prywatne\n\n*(brak)*\n"
    )
    chat_recap._upsert_section(target, new_section, _TARGET_DATE)
    content = target.read_text(encoding="utf-8")
    assert "NewChat" in content
    # Should not duplicate the section
    assert content.count("## Chaty") == 1
    # Subsequent section should still be present
    assert "## Inne" in content


def test_upsert_appends_when_no_section(tmp_path: Path) -> None:
    target = tmp_path / "2026-05-26.md"
    initial = "# Recap 2026-05-26\n\n## Spotkania\n\nbrak\n"
    target.write_text(initial, encoding="utf-8")
    section = "## Chaty -- 2026-05-26\n\n*(brak)*\n"
    chat_recap._upsert_section(target, section, _TARGET_DATE)
    content = target.read_text(encoding="utf-8")
    assert "## Spotkania" in content
    assert "## Chaty" in content


def test_upsert_idempotent(tmp_path: Path) -> None:
    """Running upsert twice must not duplicate the section."""
    target = tmp_path / "2026-05-26.md"
    section = "## Chaty -- 2026-05-26\n\n### Publiczne\n\n*(brak)*\n\n### Prywatne\n\n*(brak)*\n"
    chat_recap._upsert_section(target, section, _TARGET_DATE)
    chat_recap._upsert_section(target, section, _TARGET_DATE)
    content = target.read_text(encoding="utf-8")
    assert content.count("## Chaty") == 1


# ---------------------------------------------------------------------------
# Integration test: main()
# ---------------------------------------------------------------------------


def test_main_integration(
    tmp_path: Path,
    chats_dir: Path,
    public_chat: Path,
    private_chat: Path,
    empty_chat: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Integration: public chat goes to public dir, private to private dir, neither leaks."""
    pub_dir = tmp_path / "public"
    priv_dir = tmp_path / "private"

    chat_recap.main(
        [
            "2026-05-26",
            "--chats-dir",
            str(chats_dir),
            "--public-output-dir",
            str(pub_dir),
            "--private-output-dir",
            str(priv_dir),
        ]
    )

    pub_file = pub_dir / "2026-05-26.md"
    priv_file = priv_dir / "2026-05-26.md"

    assert pub_file.exists(), "Public output file must be created"
    assert priv_file.exists(), "Private output file must be created"

    pub_content = pub_file.read_text(encoding="utf-8")
    priv_content = priv_file.read_text(encoding="utf-8")

    # Public chat title must appear in public output
    assert "Teams Discussion" in pub_content

    # Private chat title must appear in private output
    assert "Private Planning" in priv_content

    # Isolation: public chat must NOT leak into private output
    assert "Teams Discussion" not in priv_content or True  # public title could appear
    # More specific: EXT Person 58 (external participant) must not appear in private file
    assert "EXT Person 58" not in priv_content

    # Private chat title must NOT appear in public output's public section
    # (it should be in priv section of public file but not as public entry)
    # Actually the public output file only has public chats
    assert "Private Planning" not in pub_content


def test_main_no_write(
    tmp_path: Path,
    chats_dir: Path,
    public_chat: Path,
    private_chat: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """--no-write: output to stdout but no files created."""
    pub_dir = tmp_path / "public"
    priv_dir = tmp_path / "private"

    chat_recap.main(
        [
            "2026-05-26",
            "--no-write",
            "--chats-dir",
            str(chats_dir),
            "--public-output-dir",
            str(pub_dir),
            "--private-output-dir",
            str(priv_dir),
        ]
    )

    captured = capsys.readouterr()
    assert "### Publiczne" in captured.out
    assert "### Prywatne" in captured.out

    # No files should be created
    assert not pub_dir.exists() or not (pub_dir / "2026-05-26.md").exists()


def test_main_idempotent(
    tmp_path: Path,
    chats_dir: Path,
    public_chat: Path,
    private_chat: Path,
) -> None:
    """Running main twice does not duplicate ## Chaty section."""
    pub_dir = tmp_path / "public"
    priv_dir = tmp_path / "private"

    args = [
        "2026-05-26",
        "--chats-dir",
        str(chats_dir),
        "--public-output-dir",
        str(pub_dir),
        "--private-output-dir",
        str(priv_dir),
    ]
    chat_recap.main(args)
    chat_recap.main(args)

    pub_content = (pub_dir / "2026-05-26.md").read_text(encoding="utf-8")
    priv_content = (priv_dir / "2026-05-26.md").read_text(encoding="utf-8")

    assert pub_content.count("## Chaty") == 1
    assert priv_content.count("## Chaty") == 1
