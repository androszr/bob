"""Unit tests for scripts/md_to_mp3/mcp_server.py."""

import asyncio
import base64
import importlib.util
import json
import struct
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Load mcp_server module via importlib (pattern from test_kb_mcp_server.py)
# ---------------------------------------------------------------------------

_MD_TO_MP3_DIR = Path(__file__).resolve().parent.parent / "scripts" / "md_to_mp3"

_spec = importlib.util.spec_from_file_location(
    "md_to_mp3_mcp_server",
    str(_MD_TO_MP3_DIR / "mcp_server.py"),
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["md_to_mp3_mcp_server"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

# Remove scripts/md_to_mp3/ from sys.path injected by mcp_server.py
_md_dir_str = str(_MD_TO_MP3_DIR)
if _md_dir_str in sys.path:
    sys.path.remove(_md_dir_str)

_split_chunks = _mod._split_chunks
_sanitize_filename = _mod._sanitize_filename
_build_default_filename = _mod._build_default_filename
_get_voice_id = _mod._get_voice_id
_gemini_tts_chunk = _mod._gemini_tts_chunk
convert_md_to_mp3 = _mod.convert_md_to_mp3
list_md_to_mp3_files = _mod.list_md_to_mp3_files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_silent_pcm(samples: int = 1000) -> bytes:
    """Return silence as s16le PCM bytes (must be even)."""
    # Each sample is a 2-byte little-endian signed int (value 0 = silence)
    return struct.pack(f"<{samples}h", *([0] * samples))


def _make_gemini_response(pcm_bytes: bytes) -> dict:
    """Build a fake Gemini TTS API response with base64-encoded PCM."""
    b64 = base64.b64encode(pcm_bytes).decode("ascii")
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/pcm",
                                "data": b64,
                            }
                        }
                    ]
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# _split_chunks
# ---------------------------------------------------------------------------


def test_split_chunks_short_text_single_chunk():
    text = "Krotki tekst."
    chunks = _split_chunks(text, max_chars=4500)
    assert len(chunks) == 1
    assert chunks[0] == "Krotki tekst."


def test_split_chunks_long_text_multiple_chunks():
    # Create text longer than max_chars via repeated paragraphs
    para = "A" * 500 + "."
    text = "\n\n".join([para] * 20)  # ~10000 chars
    chunks = _split_chunks(text, max_chars=4500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4500


def test_split_chunks_paragraph_too_large_sentence_split():
    # Single paragraph larger than max -> must split by sentences
    sentences = [f"Zdanie numer {i}." for i in range(300)]
    big_para = " ".join(sentences)
    assert len(big_para) > 4500
    chunks = _split_chunks(big_para, max_chars=4500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4500


def test_split_chunks_filters_empty():
    text = "Para1.\n\n\n\nPara2."
    chunks = _split_chunks(text, max_chars=4500)
    assert all(c.strip() for c in chunks)
    assert len(chunks) == 1  # fits in one chunk


def test_split_chunks_exactly_at_limit():
    text = "X" * 4500
    chunks = _split_chunks(text, max_chars=4500)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------


def test_sanitize_filename_happy_path():
    assert _sanitize_filename("raport", ".mp3") == "raport.mp3"


def test_sanitize_filename_already_has_ext():
    assert _sanitize_filename("raport.mp3", ".mp3") == "raport.mp3"


def test_sanitize_filename_rejects_path_traversal_slash():
    assert _sanitize_filename("../evil", ".mp3") is None


def test_sanitize_filename_rejects_path_traversal_backslash():
    assert _sanitize_filename("a\\b", ".mp3") is None


def test_sanitize_filename_rejects_null_byte():
    assert _sanitize_filename("a\x00b", ".mp3") is None


def test_sanitize_filename_rejects_dot():
    assert _sanitize_filename(".", ".mp3") is None


def test_sanitize_filename_rejects_dotdot():
    assert _sanitize_filename("..", ".mp3") is None


def test_sanitize_filename_rejects_leading_dot():
    assert _sanitize_filename(".hidden", ".mp3") is None


def test_sanitize_filename_rejects_too_long():
    name = "a" * 201
    assert _sanitize_filename(name, ".mp3") is None


def test_sanitize_filename_rejects_non_ascii_whitelist():
    assert _sanitize_filename("plik z spacjami", ".mp3") is None


def test_sanitize_filename_empty():
    assert _sanitize_filename("", ".mp3") is None


# ---------------------------------------------------------------------------
# _get_voice_id + env override
# ---------------------------------------------------------------------------


def test_get_voice_id_kobieta_default():
    with patch.dict("os.environ", {}, clear=False):
        # Remove override if set
        import os
        os.environ.pop("MD_TO_MP3_VOICE_KOBIETA_ID", None)
        assert _get_voice_id("kobieta") == "Kore"


def test_get_voice_id_mezczyzna_default():
    import os
    os.environ.pop("MD_TO_MP3_VOICE_MEZCZYZNA_ID", None)
    assert _get_voice_id("mezczyzna") == "Charon"


def test_get_voice_id_kobieta_env_override(monkeypatch):
    monkeypatch.setenv("MD_TO_MP3_VOICE_KOBIETA_ID", "Aoede")
    assert _get_voice_id("kobieta") == "Aoede"


def test_get_voice_id_mezczyzna_env_override(monkeypatch):
    monkeypatch.setenv("MD_TO_MP3_VOICE_MEZCZYZNA_ID", "Puck")
    assert _get_voice_id("mezczyzna") == "Puck"




# ---------------------------------------------------------------------------
# convert_md_to_mp3 — happy path (mock httpx + subprocess)
# ---------------------------------------------------------------------------


def _make_mock_httpx_response(pcm_bytes: bytes) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = _make_gemini_response(pcm_bytes)
    return resp


def _make_mock_async_client(pcm_bytes: bytes):
    """Create a mock httpx.AsyncClient whose post() returns a Gemini-like response."""
    fake_resp = _make_mock_httpx_response(pcm_bytes)

    async def _post(url, **kwargs):
        return fake_resp

    mock_client = MagicMock()
    mock_client.post = _post
    # Support async context manager protocol
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def test_convert_md_to_mp3_happy_path(tmp_path, monkeypatch):
    """Happy path: short text, kobieta voice, mock Gemini + ffmpeg."""
    monkeypatch.setenv("GOOGLE_GEMINI", "test-key")
    monkeypatch.setenv("MD_TO_MP3_DIR", str(tmp_path))
    monkeypatch.delenv("MD_TO_MP3_TTS_CONCURRENCY", raising=False)
    monkeypatch.delenv("GEMINI_BASE_URL", raising=False)

    pcm = _make_silent_pcm(2000)
    mock_client = _make_mock_async_client(pcm)

    def fake_subprocess_run(cmd, **kwargs):
        out_path = cmd[-1]
        Path(out_path).write_bytes(b"ID3fake")
        return MagicMock(returncode=0, stderr=b"")

    with patch.object(_mod.httpx, "AsyncClient", return_value=mock_client):
        with patch("subprocess.run", side_effect=fake_subprocess_run):
            content = asyncio.run(convert_md_to_mp3("Hello world", voice="kobieta"))

    # convert_md_to_mp3 returns [TextContent(json), Audio] for MCP audio delivery
    assert len(content) == 2
    result = json.loads(content[0].text)
    assert "absolutePath" in result
    assert "path" in result
    assert result["voiceId"] == "Kore"
    assert result["chunkCount"] == 1
    assert Path(result["absolutePath"]).exists()


def test_convert_md_to_mp3_mezczyzna_voice(tmp_path, monkeypatch):
    """Voice 'mezczyzna' maps to Charon."""
    monkeypatch.setenv("GOOGLE_GEMINI", "test-key")
    monkeypatch.setenv("MD_TO_MP3_DIR", str(tmp_path))

    pcm = _make_silent_pcm(2000)
    mock_client = _make_mock_async_client(pcm)

    def fake_subprocess_run(cmd, **kwargs):
        out_path = cmd[-1]
        Path(out_path).write_bytes(b"ID3fake")
        return MagicMock(returncode=0, stderr=b"")

    with patch.object(_mod.httpx, "AsyncClient", return_value=mock_client):
        with patch("subprocess.run", side_effect=fake_subprocess_run):
            content = asyncio.run(convert_md_to_mp3("Tekst", voice="mezczyzna"))

    result = json.loads(content[0].text)
    assert result["voiceId"] == "Charon"


# ---------------------------------------------------------------------------
# convert_md_to_mp3 — validation errors
# ---------------------------------------------------------------------------


def test_convert_md_to_mp3_empty_text(monkeypatch):
    monkeypatch.setenv("GOOGLE_GEMINI", "test-key")
    try:
        asyncio.run(convert_md_to_mp3(""))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "text_required" in str(exc)


def test_convert_md_to_mp3_whitespace_only_text(monkeypatch):
    monkeypatch.setenv("GOOGLE_GEMINI", "test-key")
    try:
        asyncio.run(convert_md_to_mp3("   \n  "))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "text_required" in str(exc)


def test_convert_md_to_mp3_invalid_voice(monkeypatch):
    monkeypatch.setenv("GOOGLE_GEMINI", "test-key")
    try:
        asyncio.run(convert_md_to_mp3("hello", voice="robot"))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "invalid_voice" in str(exc)



def test_convert_md_to_mp3_invalid_filename(monkeypatch):
    monkeypatch.setenv("GOOGLE_GEMINI", "test-key")
    try:
        asyncio.run(convert_md_to_mp3("hello", filename="../evil.mp3"))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "invalid_filename" in str(exc)


# ---------------------------------------------------------------------------
# convert_md_to_mp3 — hard-fail on chunk error
# ---------------------------------------------------------------------------


def test_convert_md_to_mp3_chunk_error_raises(tmp_path, monkeypatch):
    """When Gemini returns an error for a chunk, convert_md_to_mp3 raises RuntimeError."""
    monkeypatch.setenv("GOOGLE_GEMINI", "test-key")
    monkeypatch.setenv("MD_TO_MP3_DIR", str(tmp_path))

    error_resp = MagicMock()
    error_resp.status_code = 500
    error_resp.json.return_value = {"error": {"message": "internal server error"}}
    error_resp.text = "internal server error"

    async def _post_fail(url, **kwargs):
        return error_resp

    mock_client = MagicMock()
    mock_client.post = _post_fail
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    try:
        with patch.object(_mod.httpx, "AsyncClient", return_value=mock_client):
            asyncio.run(convert_md_to_mp3("Hello"))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "gemini_error" in str(exc)


# ---------------------------------------------------------------------------
# list_md_to_mp3_files
# ---------------------------------------------------------------------------


def test_list_md_to_mp3_files_empty_dir(tmp_path, monkeypatch):
    """Returns empty list when dir does not exist."""
    non_existent = tmp_path / "does-not-exist"
    monkeypatch.setenv("MD_TO_MP3_DIR", str(non_existent))
    result = json.loads(list_md_to_mp3_files())
    assert result["total"] == 0
    assert result["files"] == []


def test_list_md_to_mp3_files_with_files(tmp_path, monkeypatch):
    """Returns files found in output dir."""
    monkeypatch.setenv("MD_TO_MP3_DIR", str(tmp_path))
    (tmp_path / "2026-01-01-120000-test-kobieta.mp3").write_bytes(b"ID3fake")
    (tmp_path / "2026-01-02-120000-test-mezczyzna.mp3").write_bytes(b"ID3fake2")
    result = json.loads(list_md_to_mp3_files())
    assert result["total"] == 2
    names = [f["name"] for f in result["files"]]
    assert "2026-01-01-120000-test-kobieta.mp3" in names
    assert "2026-01-02-120000-test-mezczyzna.mp3" in names
    for f in result["files"]:
        assert "absolutePath" in f
        assert "bytes" in f
        assert "mtime" in f


def test_list_md_to_mp3_files_ignores_non_mp3(tmp_path, monkeypatch):
    """Only .mp3 files are returned."""
    monkeypatch.setenv("MD_TO_MP3_DIR", str(tmp_path))
    (tmp_path / "file.mp3").write_bytes(b"x")
    (tmp_path / "file.md").write_bytes(b"y")
    (tmp_path / "file.txt").write_bytes(b"z")
    result = json.loads(list_md_to_mp3_files())
    assert result["total"] == 1
    assert result["files"][0]["name"] == "file.mp3"


# ---------------------------------------------------------------------------
# PCM order verification (index-based gather)
# ---------------------------------------------------------------------------


def test_convert_md_to_mp3_chunk_order_preserved(tmp_path, monkeypatch):
    """PCM chunks must be concatenated in order, not appended as they complete.

    We give each chunk a unique PCM signature and verify the output buffer
    is in the correct chunk order.
    """
    monkeypatch.setenv("GOOGLE_GEMINI", "test-key")
    monkeypatch.setenv("MD_TO_MP3_DIR", str(tmp_path))
    monkeypatch.setenv("MD_TO_MP3_TTS_CONCURRENCY", "3")

    # Build two distinct chunks > 4500 chars total but each < 4500
    chunk_a_marker = b"\x01\x00" * 100  # distinct signature for chunk 0
    chunk_b_marker = b"\x02\x00" * 100  # distinct signature for chunk 1

    # Two paragraphs, each ~3000 chars so total > 4500 -> split into 2
    para_a = "A" * 3000
    para_b = "B" * 3000
    text = para_a + "\n\n" + para_b

    async def _post_ordered(url, **kwargs):
        body = kwargs.get("json", kwargs.get("data", {}))
        chunk_text = body["contents"][0]["parts"][0]["text"]
        # Identify chunk by its content
        if "A" in chunk_text:
            pcm = chunk_a_marker
        else:
            pcm = chunk_b_marker
        return _make_mock_httpx_response(pcm)

    mock_client = MagicMock()
    mock_client.post = _post_ordered
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    captured_pcm: list[bytes] = []

    def fake_subprocess_run(cmd, **kwargs):
        captured_pcm.append(kwargs["input"])
        out_path = cmd[-1]
        Path(out_path).write_bytes(b"ID3fake")
        return MagicMock(returncode=0, stderr=b"")

    with patch.object(_mod.httpx, "AsyncClient", return_value=mock_client):
        with patch("subprocess.run", side_effect=fake_subprocess_run):
            asyncio.run(convert_md_to_mp3(text, voice="kobieta"))

    # The combined PCM must start with chunk_a_marker bytes
    assert len(captured_pcm) == 1
    combined = captured_pcm[0]
    assert combined[: len(chunk_a_marker)] == chunk_a_marker, (
        "PCM order wrong: chunk 0 must come first regardless of async completion order"
    )
