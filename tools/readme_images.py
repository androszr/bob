"""Render and check the README pictures.

    python3 tools/readme_images.py            # redraw all four from docs/images/src/
    python3 tools/readme_images.py --only why-bob
    python3 tools/readme_images.py --check    # verify the committed pictures; no browser needed

Standard library plus Playwright (imported lazily, only when rendering).
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "docs" / "images" / "src"
OUT = REPO / "docs" / "images"
README = REPO / "README.md"

SOURCES = ("hero-bob-in-teams", "why-bob", "a-day-with-bob", "architecture")
CSS_WIDTH = 1200
WIDTH = 2400
MAX_BYTES = 600 * 1024
ALLOWED_CHUNKS = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"sRGB", b"gAMA", b"pHYs"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_IMG = re.compile(r'<img\s+src="docs/images/([^"]+)"\s+alt="([^"]*)"\s+width="(\d+)"')


def _chunk_header(data: bytes, pos: int) -> tuple[int, bytes]:
    """(length, type) of the chunk at pos; ValueError when the chunk runs past the end of the data."""
    if pos + 8 > len(data):
        raise ValueError("truncated PNG")
    length, kind = struct.unpack(">I4s", data[pos:pos + 8])
    if pos + 12 + length > len(data):
        raise ValueError("truncated PNG")
    return length, kind


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """(type, body) for every chunk, in file order."""
    if not data.startswith(PNG_MAGIC):
        raise ValueError("not a PNG")
    chunks, pos = [], len(PNG_MAGIC)
    while pos < len(data):
        length, kind = _chunk_header(data, pos)
        chunks.append((kind, data[pos + 8:pos + 8 + length]))
        pos += 12 + length
    if not chunks or chunks[-1][0] != b"IEND":
        raise ValueError("truncated PNG")
    return chunks


def png_size(data: bytes) -> tuple[int, int]:
    kind, body = png_chunks(data)[0]
    if len(body) < 8 or kind != b"IHDR":
        raise ValueError("first chunk is not IHDR")
    width, height = struct.unpack(">II", body[:8])
    return width, height


def strip_chunks(data: bytes) -> bytes:
    """Drop every chunk outside ALLOWED_CHUNKS; the CRCs of the kept chunks are copied as they were."""
    out, pos = [PNG_MAGIC], len(PNG_MAGIC)
    while pos < len(data):
        length, kind = _chunk_header(data, pos)
        end = pos + 12 + length
        if kind in ALLOWED_CHUNKS:
            out.append(data[pos:end])
        pos = end
    return b"".join(out)


def render(stem: str) -> Path:
    from playwright.sync_api import sync_playwright

    source = SRC / f"{stem}.html"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": CSS_WIDTH, "height": CSS_WIDTH}, device_scale_factor=2)
            # the sources are local files; refuse anything that would leave the machine
            context.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file://") else r.abort())
            page = context.new_page()
            page.goto(source.as_uri())
            page.evaluate("document.fonts.ready.then(() => true)")
            shot = page.locator("#frame").screenshot(type="png")
        finally:
            browser.close()
    target = OUT / f"{stem}.png"
    target.write_bytes(strip_chunks(shot))
    return target


def verify(out: Path = OUT, readme: Path = README) -> tuple[list[str], list[str]]:
    """(report lines, findings). An empty findings list means every pin holds."""
    lines: list[str] = []
    findings: list[str] = []
    expected = {f"{stem}.png" for stem in SOURCES}
    found = {p.name for p in out.glob("*.png")}
    if found != expected:
        findings.append(f"picture set differs: missing {sorted(expected - found)}, extra {sorted(found - expected)}")
    for name in sorted(expected & found):
        data = (out / name).read_bytes()
        try:
            width, height = png_size(data)
            kinds = {kind for kind, _ in png_chunks(data)}
        except ValueError as exc:
            findings.append(f"{name}: {exc}")
            continue
        lines.append(f"{name}: {width}x{height}, {len(data)} bytes")
        if width != WIDTH:
            findings.append(f"{name}: width {width}, expected {WIDTH}")
        if height % 2:
            findings.append(f"{name}: odd height {height}")
        if len(data) > MAX_BYTES:
            findings.append(f"{name}: {len(data)} bytes is over the {MAX_BYTES} cap")
        if not kinds <= ALLOWED_CHUNKS:
            extra = sorted(k.decode() for k in kinds - ALLOWED_CHUNKS)
            findings.append(f"{name}: chunks outside the allow-list: {extra}")
    text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    embeds = _IMG.findall(text)
    for name in sorted(expected | {"app-icon.svg"}):
        mine = [(alt, w) for src, alt, w in embeds if src == name]
        if len(mine) != 1:
            findings.append(f"{name}: embedded {len(mine)} times in the README, expected once")
            continue
        alt, width_attr = mine[0]
        if not 20 <= len(alt) <= 200 or not alt.endswith("."):
            findings.append(f"{name}: alt must be 20 to 200 characters and end with a full stop")
        if int(width_attr) > 880 and name != "app-icon.svg":
            findings.append(f"{name}: width {width_attr} is over 880")
    return lines, findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=sorted(SOURCES), help="redraw just this picture")
    parser.add_argument("--check", action="store_true", help="verify the committed pictures and exit")
    args = parser.parse_args(argv)
    if not args.check:
        for stem in [args.only] if args.only else sorted(SOURCES):
            print(f"rendered {render(stem).relative_to(REPO)}")
    lines, findings = verify()
    for line in lines:
        print(line)
    for finding in findings:
        print(f"FINDING: {finding}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
