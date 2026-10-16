"""Chat recap — daily summary of Claude Code JSONL chat files.

Usage:
    python scripts/chat_recap.py [YYYY-MM-DD] [--no-write] [--chats-dir DIR]
        [--public-output-dir DIR] [--private-output-dir DIR]

Reads source_data/chats/*.jsonl, filters by mtime (local Europe/Warsaw date),
classifies as public (other speakers found) or private (only Jan/Bob),
and upserts a "## Chaty" section into:
  - knowledge/daily-recap/YYYY-MM-DD.md  (public, committed)
  - source_data/chat-recap-private/YYYY-MM-DD.md  (private, gitignored)
Both sections are also printed to stdout.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import logts  # noqa: F401, I001 — patches sys.stderr with CET timestamps
import org_config  # noqa: E402

_TZ = ZoneInfo("Europe/Warsaw")

# Speakers treated as "own" — do not classify chat as public: the bot, the owner's
# alias and each word of the owner's display name (transcripts use the first name).
_OWN_SPEAKERS: set[str] = {"Bob", org_config.OWNER_ALIAS, *org_config.OWNER_DISPLAY_NAME.split()}

# Regex: capture speaker name (up to 40 chars, no newline or colon) before ": "
_SPEAKER_RE = re.compile(r"^([^\n:\[\]]{1,40}):\s", re.MULTILINE)

# Regex to strip the knowledge-base preamble injected by queue-operation
_BAZA_WIEDZY_RE = re.compile(
    r"^\[BAZA WIEDZY\].*?(?=\[HISTORIA ROZMOWY\]|\Z)", re.DOTALL
)
_HISTORIA_HEADER_RE = re.compile(r"^\[HISTORIA ROZMOWY\]\s*\n?", re.MULTILINE)

# Regex to match the Chaty section for upsert
_CHATY_SECTION_RE = re.compile(r"## Chaty[^\n]*\n.*?(?=\n## |\Z)", re.DOTALL)


@dataclass
class ChatSummary:
    session_id: str
    channel: str  # last path component of cwd, e.g. "teams-agent"
    ai_title: str
    first_question: str
    classification: Literal["public", "private"]
    participants: list[str]
    start_time: datetime | None
    end_time: datetime | None
    messages: list[str] = field(default_factory=list, repr=False)


def _list_chat_files(chats_dir: Path, target_date: date) -> list[Path]:
    """Return .jsonl files whose mtime falls on target_date in Europe/Warsaw."""
    result = []
    for p in sorted(chats_dir.glob("*.jsonl")):
        mtime_utc = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
        mtime_local = mtime_utc.astimezone(_TZ)
        if mtime_local.date() == target_date:
            result.append(p)
    return result


def _parse_chat(path: Path) -> ChatSummary | None:
    """Parse a single JSONL chat file into a ChatSummary.

    Returns None if the file has no user messages or no timestamps
    (nothing to report).
    """
    ai_title = ""
    cwd: str = ""
    timestamps: list[datetime] = []
    user_messages: list[str] = []  # string message.content only
    queue_contents: list[str] = []  # content from queue-operation enqueue

    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[skip] {path.name}:{lineno} — invalid JSON", file=sys.stderr)
                continue

            rtype = rec.get("type", "")

            if rtype == "ai-title":
                title = rec.get("aiTitle", "")
                if title:
                    ai_title = title

            elif rtype == "queue-operation" and rec.get("operation") == "enqueue":
                ts_str = rec.get("timestamp", "")
                if ts_str:
                    try:
                        timestamps.append(_parse_ts(ts_str))
                    except ValueError:
                        pass
                content = rec.get("content", "")
                if isinstance(content, str) and content:
                    queue_contents.append(content)

            elif rtype == "user":
                ts_str = rec.get("timestamp", "")
                if ts_str:
                    try:
                        timestamps.append(_parse_ts(ts_str))
                    except ValueError:
                        pass
                if not cwd:
                    cwd = rec.get("cwd", "")
                msg = rec.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    user_messages.append(content)

    # No timestamps or no user messages => skip
    if not timestamps or not user_messages:
        return None

    session_id = path.stem
    channel = Path(cwd).name if cwd else ""
    all_messages = queue_contents + user_messages
    classification = _classify(all_messages)
    participants = _extract_participants(all_messages)
    first_question = _extract_first_user_question(user_messages)

    start_time = min(timestamps)
    end_time = max(timestamps)

    return ChatSummary(
        session_id=session_id,
        channel=channel,
        ai_title=ai_title or session_id[:8],
        first_question=first_question,
        classification=classification,
        participants=participants,
        start_time=start_time,
        end_time=end_time,
        messages=all_messages,
    )


def _parse_ts(ts_str: str) -> datetime:
    """Parse ISO8601 timestamp (possibly ending with Z) to aware datetime."""
    # Python 3.11+ fromisoformat handles Z; for safety replace Z with +00:00
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def _strip_knowledge_preamble(text: str) -> str:
    """Strip [BAZA WIEDZY] preamble, keeping only [HISTORIA ROZMOWY] content."""
    stripped = _BAZA_WIEDZY_RE.sub("", text).strip()
    stripped = _HISTORIA_HEADER_RE.sub("", stripped).strip()
    return stripped


def _extract_speakers(text: str) -> list[str]:
    """Extract speaker names from text using liberal regex."""
    return _SPEAKER_RE.findall(text)


def _classify(messages: list[str]) -> Literal["public", "private"]:
    """Classify a chat as public (other speakers present) or private."""
    for msg in messages:
        # Strip knowledge preamble before looking for speakers to avoid false positives
        # e.g. "Dostepne foldery:" in BAZA WIEDZY being treated as a speaker
        text = _strip_knowledge_preamble(msg)
        for speaker in _extract_speakers(text):
            speaker_stripped = speaker.strip()
            if speaker_stripped and speaker_stripped not in _OWN_SPEAKERS:
                return "public"
    return "private"


def _extract_first_user_question(messages: list[str]) -> str:
    """Return first 80 chars of first user message, stripping knowledge preamble."""
    for msg in messages:
        text = msg.strip()
        if not text:
            continue
        # Strip [BAZA WIEDZY] preamble
        text = _BAZA_WIEDZY_RE.sub("", text).strip()
        # Strip [HISTORIA ROZMOWY] header line
        text = _HISTORIA_HEADER_RE.sub("", text).strip()
        # Take first non-empty line
        for line in text.splitlines():
            line = line.strip()
            if line:
                if len(line) > 80:
                    return line[:80] + "..."
                return line
    return ""


def _extract_participants(messages: list[str]) -> list[str]:
    """Return unique non-own speakers in order of first appearance."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for msg in messages:
        text = _strip_knowledge_preamble(msg)
        for speaker in _extract_speakers(text):
            s = speaker.strip()
            if s and s not in _OWN_SPEAKERS and s not in seen_set:
                seen_set.add(s)
                seen.append(s)
    return seen


def _format_time_range(start: datetime | None, end: datetime | None) -> str:
    """Format start-end as HH:MM-HH:MM in Europe/Warsaw."""
    if start is None or end is None:
        return "??:??-??:??"
    s = start.astimezone(_TZ).strftime("%H:%M")
    e = end.astimezone(_TZ).strftime("%H:%M")
    return f"{s}-{e}"


def _format_section(summaries: list[ChatSummary], target_date: date) -> str:
    """Build the '## Chaty' markdown section."""
    date_str = target_date.isoformat()
    public = [s for s in summaries if s.classification == "public"]
    private = [s for s in summaries if s.classification == "private"]

    lines = [f"## Chaty -- {date_str}", ""]

    lines.append("### Publiczne")
    lines.append("")
    if public:
        for s in public:
            time_range = _format_time_range(s.start_time, s.end_time)
            participants_str = (
                (", ".join(s.participants)) if s.participants else "brak"
            )
            channel_str = f" #{s.channel}" if s.channel else ""
            lines.append(f"**{s.ai_title}**{channel_str} ({time_range}) `{s.session_id}`")
            lines.append(f"Uczestnicy: {participants_str}")
            lines.append(f"Pierwsze pytanie: {s.first_question}")
            lines.append("")
    else:
        lines.append("*(brak)*")
        lines.append("")

    lines.append("### Prywatne")
    lines.append("")
    if private:
        for s in private:
            time_range = _format_time_range(s.start_time, s.end_time)
            channel_str = f" #{s.channel}" if s.channel else ""
            lines.append(f"**{s.ai_title}**{channel_str} ({time_range}) `{s.session_id}`")
            lines.append(f"Pierwsze pytanie: {s.first_question}")
            lines.append("")
    else:
        lines.append("*(brak)*")
        lines.append("")

    return "\n".join(lines)


def _upsert_section(file_path: Path, section: str, target_date: date) -> None:
    """Create or update the Chaty section in target markdown file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if not file_path.exists():
        date_str = target_date.isoformat()
        content = f"# Recap {date_str}\n\n{section}\n"
        file_path.write_text(content, encoding="utf-8")
        return

    existing = file_path.read_text(encoding="utf-8")

    if _CHATY_SECTION_RE.search(existing):
        # Replace existing section
        updated = _CHATY_SECTION_RE.sub(section, existing)
    else:
        # Append to end
        updated = existing.rstrip("\n") + "\n\n" + section + "\n"

    file_path.write_text(updated, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    repo_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(
        description="Daily summary of Claude Code chat files."
    )
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Target date YYYY-MM-DD (default: today in Europe/Warsaw)",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print only, do not write files",
    )
    parser.add_argument(
        "--chats-dir",
        default=str(repo_root / "source_data" / "chats"),
        help="Directory with chat JSONL files",
    )
    parser.add_argument(
        "--public-output-dir",
        default=str(repo_root / "knowledge" / "daily-recap"),
        help="Output dir for public recap (default: knowledge/daily-recap/)",
    )
    parser.add_argument(
        "--private-output-dir",
        default=str(repo_root / "source_data" / "chat-recap-private"),
        help="Output dir for private recap (default: source_data/chat-recap-private/)",
    )

    args = parser.parse_args(argv)

    if args.date is None:
        target_date = datetime.now(tz=_TZ).date()
    else:
        target_date = date.fromisoformat(args.date)

    chats_dir = Path(args.chats_dir)
    public_output_dir = Path(args.public_output_dir)
    private_output_dir = Path(args.private_output_dir)

    if not chats_dir.exists():
        print(f"[warn] chats-dir not found: {chats_dir}", file=sys.stderr)
        chat_files: list[Path] = []
    else:
        chat_files = _list_chat_files(chats_dir, target_date)

    summaries: list[ChatSummary] = []
    for path in chat_files:
        summary = _parse_chat(path)
        if summary is not None:
            summaries.append(summary)

    # Sort by start_time ascending
    summaries.sort(key=lambda s: s.start_time or datetime.min.replace(tzinfo=UTC))

    public_summaries = [s for s in summaries if s.classification == "public"]
    private_summaries = [s for s in summaries if s.classification == "private"]

    # Build separate sections for each output file
    public_section = _format_section(public_summaries, target_date)
    private_section = _format_section(private_summaries, target_date)

    # Build combined stdout section (all chats together)
    combined_section = _format_section(summaries, target_date)
    print(combined_section)

    if not args.no_write:
        date_str = target_date.isoformat()

        # Public output (only public chats)
        public_file = public_output_dir / f"{date_str}.md"
        _upsert_section(public_file, public_section, target_date)
        print(f"Zapisano publiczne: {public_file}")

        # Private output (only private chats)
        private_file = private_output_dir / f"{date_str}.md"
        _upsert_section(private_file, private_section, target_date)
        print(f"Zapisano prywatne: {private_file}")


if __name__ == "__main__":
    main()
