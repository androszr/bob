"""The panel page stays neutral: one tokens file holds every colour, the system font, only the shipped mascots."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "scripts" / "bob_panel" / "static"
PAGE = (STATIC / "index.html").read_text(encoding="utf-8")
LAYOUT_CSS = (STATIC / "panel.css").read_text(encoding="utf-8")
TOKENS = (STATIC / "tokens.css").read_text(encoding="utf-8")
_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklab\(|oklch\(")
SHIPPED = [  # the whole set: nothing named after a person or a company
    "bob-static-base", "bob-happy", "bob-sleeping", "bob-wake", "bob-idle-living", "bob-dizzy", "bob-disconnected",
    "bob-notification", "bob-eureka", "bob-grooving", "bob-going-away", "bob-hat-mishap", "bob-walking",
    "bob-working-thinking", "bob-working-confused", "bob-working-debugger", "bob-working-wizard",
    "bob-working-overheated", "bob-working-juggling", "bob-fishing"]
_COLOUR_NAMES = re.compile(r":\s*(?:white|black|red|green|blue|gray|grey|orange|yellow|purple|pink)\b")


def _strip_color_mix(text: str) -> str:
    """color-mix(in oklab, var(--a) 40%, var(--b)) mixes two tokens - not a colour literal."""
    return re.sub(r"color-mix\(in oklab, var\([^)]*\)[^)]*var\([^)]*\)\)", "", text)


def test_no_colour_literal_outside_the_tokens_file():
    for name, text in (("index.html", PAGE), ("panel.css", LAYOUT_CSS)):
        found = _COLOUR.findall(_strip_color_mix(text))
        assert not found, f"{name}: {found[:5]}"
        assert not _COLOUR_NAMES.findall(text), name
    assert len(re.findall(r"#[0-9a-fA-F]{3,8}\b", TOKENS)) >= 10       # the colours really live here


def test_system_font_and_no_font_files():
    assert TOKENS.count("system-ui") == 1
    assert "font-face" not in LAYOUT_CSS + TOKENS + PAGE
    shipped = [p for p in STATIC.rglob("*") if p.suffix.lower() in {".woff", ".woff2", ".ttf", ".otf", ".eot"}]
    assert shipped == []


def test_no_private_name_in_the_panel_files():
    """The hashed-denylist scan of the guard, over everything the panel ships (skipped without the local list)."""
    import test_no_private_data as guard  # noqa: PLC0415
    if not guard.DENYLIST.is_file():
        pytest.skip("no local denylist: only the structural rules of the guard run in CI")
    root = STATIC.parents[2]
    files = [str(p.relative_to(root)) for p in STATIC.rglob("*") if p.is_file()]
    assert files and guard.scan(root, files, guard.load_denylist(guard.DENYLIST)) == []


def test_no_brand_token_or_design_system_folder():
    assert not re.search(r"--[a-z]+-brand-\d+", PAGE + LAYOUT_CSS + TOKENS)
    assert not (STATIC / "ds").exists()
    assert not list((STATIC / "bob").glob("ZRODLO*"))


def test_exactly_the_twenty_shipped_mascots_and_none_named_after_a_person():
    names = sorted(p.stem for p in (STATIC / "bob").glob("*.svg"))
    assert names == sorted(SHIPPED)
    readme = (STATIC / "bob" / "README.md").read_text(encoding="utf-8")
    assert all(f"`{n}.svg`" in readme for n in names)


def test_everything_the_page_references_exists():
    mascots = {p.stem for p in (STATIC / "bob").glob("*.svg")}
    icons = {p.stem for p in (STATIC / "icons").glob("*.svg")}
    used_mascots = set(re.findall(r"bobImg\('(bob-[a-z-]+)'", PAGE))
    used_mascots |= set(re.findall(r"'[a-z-]+':'(bob-[a-z-]+)'", PAGE)) | set(re.findall(r"'(bob-static-base)'", PAGE))
    assert used_mascots and used_mascots <= mascots, used_mascots - mascots
    # ic('name'), nav table rows ['key','Label','icon'] and the callout map
    used_icons = set(re.findall(r"\bic\('([a-z-]+)'\)", PAGE))
    used_icons |= set(re.findall(r"\['[a-z]+','[^']+','([a-z-]+)'\]", PAGE))
    used_icons |= set(re.findall(r"(?:success|warning|danger|info|neutral):'([a-z-]+)'", PAGE))
    assert used_icons and used_icons <= icons, used_icons - icons
    for href in re.findall(r'href="/static/([^"]+)"', PAGE):
        assert (STATIC / href).is_file(), href


def test_one_tree_stacked_by_width_not_a_second_mobile_tree():
    assert "@media (max-width:900px)" in LAYOUT_CSS and "flex-direction:column" in LAYOUT_CSS
    assert PAGE.count('id="main"') == 1 and PAGE.count('class="ds-nav"') == 1
    assert "mobile" not in PAGE.lower().replace("<meta", "")


def test_the_page_sends_a_neutral_actor_name():
    assert "by:'panel'" in PAGE
