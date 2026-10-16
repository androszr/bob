"""MCP server: MD-to-MP3 converter using Google Gemini TTS + ffmpeg.

Start: python scripts/md_to_mp3/mcp_server.py
Claude connects via stdio (managed automatically by claude --mcp-config).

Reads GOOGLE_GEMINI (and optional overrides) from env.
Outputs MP3 files to user-data/md-to-mp3/ (default) or MD_TO_MP3_DIR.
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import logts  # noqa: E402, F401 — patches sys.stderr with CET timestamps
from dotenv import load_dotenv  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.fastmcp.utilities.types import Audio  # noqa: E402
from mcp.types import TextContent  # noqa: E402

mcp = FastMCP("md-to-mp3-bob")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(_PROJECT_ROOT / ".env", override=False)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHUNK_MAX_CHARS = 4500
_VALID_VOICES = ("kobieta", "mezczyzna")
_DEFAULT_VOICE_IDS = {
    "kobieta": "Kore",
    "mezczyzna": "Charon",
}
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------


def _tlog(tag: str, t0: float, extra: str = "") -> None:
    ms = (time.perf_counter() - t0) * 1000
    suffix = f", {extra}" if extra else ""
    print(f"[md-to-mp3] {tag} ({ms:.0f}ms{suffix})", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _get_voice_id(voice: str) -> str:
    """Return Gemini voice ID for the given voice alias, with env override."""
    env_key_map = {
        "kobieta": "MD_TO_MP3_VOICE_KOBIETA_ID",
        "mezczyzna": "MD_TO_MP3_VOICE_MEZCZYZNA_ID",
    }
    env_key = env_key_map.get(voice)
    if env_key:
        override = os.getenv(env_key, "").strip()
        if override:
            return override
    return _DEFAULT_VOICE_IDS.get(voice, "")


def _resolve_api_key() -> str:
    """Return the Gemini API key, re-reading .env each call so key rotations don't require a daemon restart."""
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    raw = os.getenv("GOOGLE_GEMINI", "")
    if ":" in raw:
        return raw.split(":", 1)[1]
    return raw


def _sanitize_filename(raw: str, ext: str) -> str | None:
    """Sanitize a user-supplied filename.

    Returns the sanitized name (with ext appended if missing), or None if invalid.
    """
    if not raw or not isinstance(raw, str):
        return None
    name = raw.strip()
    if "/" in name or "\\" in name or "\0" in name:
        return None
    if not _FILENAME_RE.match(name):
        return None
    if name in (".", ".."):
        return None
    if name.startswith("."):
        return None
    if not name.endswith(ext):
        name = name + ext
    if len(name) == 0 or len(name) > 200:
        return None
    return name


def _build_default_filename(slug: str, voice: str) -> str:
    """Build auto-generated filename: YYYY-MM-DD-HHMMSS-<slug>-<voice>.mp3."""
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d-%H%M%S")
    safe_slug = slug if slug else "md-to-mp3"
    return f"{ts}-{safe_slug}-{voice}.mp3"


def _split_chunks(text: str, max_chars: int = _CHUNK_MAX_CHARS) -> list[str]:
    """Split text into paragraph-aware chunks, each at most max_chars long.

    Splits on paragraph boundaries first, then on sentences, then words.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""

    for para in re.split(r"\n\n+", text):
        p = para.strip()
        if not p:
            continue
        if len(p) > max_chars:
            # Paragraph too big -> split by sentences, then words.
            if current:
                chunks.append(current.strip())
                current = ""
            sentences = re.split(r"(?<=[.!?…])\s+", p)
            for sent in sentences:
                s = sent.strip()
                if not s:
                    continue
                if len(current) + len(s) + 1 <= max_chars:
                    current = (current + " " + s) if current else s
                else:
                    if current:
                        chunks.append(current.strip())
                        current = ""
                    if len(s) > max_chars:
                        for word in s.split():
                            if len(current) + len(word) + 1 <= max_chars:
                                current = (current + " " + word) if current else word
                            else:
                                if current:
                                    chunks.append(current.strip())
                                current = word
                    else:
                        current = s
        elif len(current) + len(p) + 2 <= max_chars:
            current = (current + "\n\n" + p) if current else p
        else:
            if current:
                chunks.append(current.strip())
            current = p

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# Gemini TTS
# ---------------------------------------------------------------------------


async def _gemini_tts_chunk(
    client: httpx.AsyncClient,
    chunk: str,
    voice_id: str,
    api_key: str,
    base_url: str,
) -> bytes:
    """Call Gemini TTS for a single text chunk; return raw PCM bytes (s16le 24kHz mono)."""
    url = (
        f"{base_url}/v1beta/models/gemini-3.1-flash-tts-preview"
        f":generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": chunk}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_id},
                },
            },
        },
    }
    try:
        resp = await client.post(url, json=payload)
    except httpx.RequestError as exc:
        raise RuntimeError(f"gemini_unreachable: {exc}") from exc

    if resp.status_code >= 400:
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text
        err_text = err_body if isinstance(err_body, str) else json.dumps(err_body or {})
        looks_like_bad_voice = resp.status_code in (400, 404) and bool(
            re.search(r"voice|prebuilt|Kore|Charon", err_text, re.IGNORECASE)
        )
        if looks_like_bad_voice:
            raise ValueError(f"invalid_voice_name: {err_text[:200]}")
        raise RuntimeError(
            f"gemini_error: HTTP {resp.status_code}: {err_text[:200]}"
        )

    try:
        resp_json = resp.json()
    except Exception as exc:
        raise RuntimeError(f"gemini_read_error: {exc}") from exc

    inline_data = (
        resp_json.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("inlineData")
    )
    if not inline_data or not isinstance(inline_data.get("data"), str) or not inline_data["data"]:
        raise RuntimeError("gemini_error: no_inline_data")

    try:
        pcm = base64.b64decode(inline_data["data"])
    except Exception as exc:
        raise RuntimeError(f"gemini_read_error: {exc}") from exc

    # s16le requires even number of bytes
    if len(pcm) % 2 != 0:
        pcm = pcm + b"\x00"

    if len(pcm) < 1000:
        print(
            f"[md-to-mp3] warning: suspiciously small PCM chunk: {len(pcm)} bytes",
            file=sys.stderr,
            flush=True,
        )

    return pcm


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------


def _run_ffmpeg(pcm_bytes: bytes, out_path: Path) -> None:
    """Convert concatenated PCM to MP3 via ffmpeg (one call, one buffer)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-f", "s16le",
                "-ar", "24000",
                "-ac", "1",
                "-i", "pipe:",
                "-codec:a", "libmp3lame",
                "-b:a", "96k",
                "-y",
                str(out_path),
            ],
            input=pcm_bytes,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg_failed: ffmpeg not found in PATH") from exc

    if result.returncode != 0:
        stderr_snippet = result.stderr[:300].decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg_failed: returncode={result.returncode}: {stderr_snippet}")


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def convert_md_to_mp3(
    text: str,
    voice: str = "kobieta",
    filename: str = "",
) -> list:
    """Konwertuj tekst Markdown/TXT na plik MP3 przez Gemini TTS i ffmpeg.

    Parametry:
      text:     Wymagany. Tekst do narracji.
      voice:    'kobieta' (domyslnie) lub 'mezczyzna'.
      filename: Opcjonalna nazwa pliku (np. 'raport.mp3').
                Gdy pusta, generowana automatycznie: YYYY-MM-DD-HHMMSS-md-to-mp3-<voice>.mp3.

    Zwraca liste: TextContent z metadanymi + EmbeddedResource z plikiem MP3 (base64).
    Bledy: ValueError (walidacja), RuntimeError (Gemini/ffmpeg).
    """
    t0 = time.perf_counter()
    print(
        f"[md-to-mp3] convert_md_to_mp3 start voice={voice!r} textLen={len(text)}",
        file=sys.stderr,
        flush=True,
    )

    # --- Validation ---
    if not text or not text.strip():
        raise ValueError("text_required: text parameter is empty")
    if voice not in _VALID_VOICES:
        raise ValueError(
            f"invalid_voice: '{voice}' not in {list(_VALID_VOICES)}"
        )
    api_key = _resolve_api_key()
    if not api_key:
        raise ValueError("gemini_key_missing: GOOGLE_GEMINI env var not set")

    # --- Filename ---
    if filename:
        safe_name = _sanitize_filename(filename, ".mp3")
        if safe_name is None:
            raise ValueError(f"invalid_filename: '{filename}' is not a safe filename")
        out_filename = safe_name
    else:
        out_filename = _build_default_filename("md-to-mp3", voice)

    # --- Output dir ---
    raw_dir = os.getenv("MD_TO_MP3_DIR", "").strip()
    if raw_dir:
        out_dir = Path(raw_dir).resolve()
    else:
        out_dir = _PROJECT_ROOT / "user-data" / "md-to-mp3"
    out_path = out_dir / out_filename

    # --- Split into chunks ---
    chunks = _split_chunks(text)
    chunk_count = len(chunks)

    # --- Concurrency limit ---
    raw_conc = os.getenv("MD_TO_MP3_TTS_CONCURRENCY", "3")
    try:
        concurrency = int(raw_conc)
        if concurrency < 1 or concurrency > 10:
            concurrency = 3
    except ValueError:
        concurrency = 3

    # --- Base URL ---
    base_url = os.getenv(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"
    ).rstrip("/")

    voice_id = _get_voice_id(voice)

    # --- Parallel TTS calls (cancel remaining tasks on first error) ---
    semaphore = asyncio.Semaphore(concurrency)

    async def _fetch_chunk_sem(idx: int) -> bytes:
        async with semaphore:
            return await _gemini_tts_chunk(
                client, chunks[idx], voice_id, api_key, base_url
            )

    # Differentiated timeouts: fast-fail on connection issues, generous read
    # window for Gemini TTS which can take 30-120s per chunk.
    tts_timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0)
    async with httpx.AsyncClient(timeout=tts_timeout) as client:
        tasks = [asyncio.create_task(_fetch_chunk_sem(i)) for i in range(chunk_count)]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        error_task = next(
            (t for t in done if not t.cancelled() and t.exception() is not None),
            None,
        )
        if error_task is not None:
            # Cancel remaining tasks immediately — don't wait 120s for each.
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        elif pending:
            # No error yet; wait for remaining tasks to finish.
            await asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED)

    # Collect results in original order; raise on first real error.
    # Cancelled tasks (siblings of a failed chunk) are skipped.
    for chunk_index, t in enumerate(tasks):
        if t.cancelled():
            continue
        exc = t.exception()
        if exc is not None:
            raise type(exc)(
                f"{exc} [chunkIndex={chunk_index}, chunkCount={chunk_count}]"
            ) from exc

    # --- Concatenate PCM and convert ---
    combined_pcm = b"".join(t.result() for t in tasks)
    _run_ffmpeg(combined_pcm, out_path)

    abs_out = out_path.resolve()
    try:
        rel_path = str(abs_out.relative_to(_PROJECT_ROOT))
    except ValueError:
        rel_path = str(abs_out)

    _tlog(
        "convert_md_to_mp3 done",
        t0,
        f"chunks={chunk_count} bytes={len(combined_pcm)} out={out_filename}",
    )
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "path": rel_path,
                    "absolutePath": str(abs_out),
                    "voiceId": voice_id,
                    "chunkCount": chunk_count,
                },
                ensure_ascii=False,
            ),
        ),
        Audio(path=abs_out),
    ]


@mcp.tool()
def list_md_to_mp3_files() -> str:
    """Lista wygenerowanych plikow MP3 w katalogu wyjsciowym.

    Zwraca JSON z polem 'files' (lista {name, absolutePath, bytes, mtime})
    i 'total'. Zwraca pusta liste gdy katalog nie istnieje.
    """
    t0 = time.perf_counter()
    print("[md-to-mp3] list_md_to_mp3_files start", file=sys.stderr, flush=True)

    raw_dir = os.getenv("MD_TO_MP3_DIR", "").strip()
    if raw_dir:
        out_dir = Path(raw_dir).resolve()
    else:
        out_dir = _PROJECT_ROOT / "user-data" / "md-to-mp3"

    if not out_dir.exists():
        result = {"files": [], "total": 0}
        _tlog("list_md_to_mp3_files done", t0, "dir_missing")
        return json.dumps(result, ensure_ascii=False)

    files = []
    for p in sorted(out_dir.glob("*.mp3")):
        stat = p.stat()
        files.append({
            "name": p.name,
            "absolutePath": str(p.resolve()),
            "bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })

    result = {"files": files, "total": len(files)}
    _tlog("list_md_to_mp3_files done", t0, f"count={len(files)}")
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from mcp_admin.ports import MCP_PORTS
    from mcp_admin.transport import run_mcp

    run_mcp(mcp, default_port=MCP_PORTS["md-to-mp3-bob"])
