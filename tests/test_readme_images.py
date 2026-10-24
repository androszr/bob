"""Pins for the README pictures and the front page. Never renders: it reads committed files only."""

import importlib.util
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load("readme_images_tool", "tools/readme_images.py")
guard = _load("no_private_data_guard", "tests/test_no_private_data.py")

IMAGES = ROOT / "docs" / "images"
NAMES = sorted(f"{stem}.png" for stem in tool.SOURCES)
PAGES = ["README.md", "docs/setup.md", "docs/running.md", "docs/mcp-servers.md", "docs/architecture.md"]
_LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)|<img\s[^>]*src="([^"]+)"|<a\s[^>]*href="([^"]+)"')


def slugs(text: str) -> set[str]:
    """GitHub-style anchors for every Markdown heading in text."""
    out = set()
    for heading in re.findall(r"^#+\s+(.+?)\s*$", text, flags=re.M):
        out.add(re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-"))
    return out


def broken_links(root: Path, rel: str) -> list[str]:
    page = root / rel
    bad = []
    for md_target, img_target, a_target in _LINK.findall(page.read_text(encoding="utf-8")):
        target = md_target or img_target or a_target
        if re.match(r"(https?:|mailto:|#)", target):
            continue
        target, _, fragment = target.partition("#")
        path = (page.parent / target).resolve()
        if not path.exists():
            bad.append(target)
        elif fragment and path.suffix == ".md" and fragment not in slugs(path.read_text(encoding="utf-8")):
            bad.append(f"{target}#{fragment}")
    return bad


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.open = {"p": 0, "h1": 0}
        self.close = {"p": 0, "h1": 0}
        self.bad_imgs: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.open:
            self.open[tag] += 1
        if tag == "img":
            a = dict(attrs)
            local = a.get("src", "").startswith("docs/images/")
            needed = ("src", "alt", "width") if local else ("src", "alt")
            if any(k not in a for k in needed):
                self.bad_imgs.append(a)

    def handle_endtag(self, tag):
        if tag in self.close:
            self.close[tag] += 1


def html_problems(text: str) -> list[str]:
    parser = _Balance()
    parser.feed(text)
    problems = [f"unbalanced <{t}>" for t in parser.open if parser.open[t] != parser.close[t]]
    problems += [f"img missing attributes: {a}" for a in parser.bad_imgs]
    return problems


_BANNED_SVG_TAGS = {"script", "image", "foreignobject", "style"}


def _local(name: str) -> str:
    return name.split("}")[-1]


def svg_problems(root: ET.Element) -> list[str]:
    """Ways an SVG can carry active content: banned elements, links, event attributes."""
    problems = []
    for el in root.iter():
        tag = _local(el.tag).lower()
        if tag in _BANNED_SVG_TAGS:
            problems.append(f"banned element <{tag}>")
        for attr in el.attrib:
            local = _local(attr).lower()
            if local == "href" or local.startswith("on"):
                problems.append(f"banned attribute {local} on <{tag}>")
    return problems


def test_the_folder_holds_exactly_the_five_pictures():
    assert sorted(p.name for p in IMAGES.glob("*.png")) == NAMES


PANEL = ROOT / "scripts" / "bob_panel" / "static" / "index.html"


@pytest.mark.skipif(not PANEL.is_file(), reason="panel not in tree")
def test_the_panel_picture_shows_the_real_tabs():
    # The page speaks Polish, the picture English: the README's panel table pairs the two names.
    pages = re.search(r"const PAGES = \[(.*?)\n\];", PANEL.read_text(encoding="utf-8"), flags=re.S).group(1)
    real = re.findall(r"\['[a-z]+','([^']+)','[a-z]+'\]", pages)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    english = {pl: en for en, pl in re.findall(r"^\| (\w+) \(([^)]+)\) \|", readme, flags=re.M)}
    source = (IMAGES / "src" / "bob-panel.html").read_text(encoding="utf-8")
    drawn = re.findall(r'<div class="ptab[^"]*">([^<]+)<', source)
    assert len(real) == 7
    assert drawn == [english[label] for label in real]


def test_strip_chunks_drops_metadata():
    data = (IMAGES / NAMES[0]).read_bytes()
    assert tool.png_chunks(data)[0][0] == b"IHDR"  # the offset 33 below sits right after this chunk
    text_chunk = len(b"k\0v").to_bytes(4, "big") + b"tEXt" + b"k\0v" + b"\0\0\0\0"
    # insert a tEXt chunk after IHDR (25 + 8 bytes of signature and header chunk)
    dirty = data[:33] + text_chunk + data[33:]
    assert b"tEXt" in dirty
    assert tool.strip_chunks(dirty) == data


def test_every_picture_is_listed_as_reviewed():
    assert guard.REVIEWED_BINARIES == {f"docs/images/{n}" for n in NAMES}


@pytest.mark.parametrize("rel", [*PAGES, "docs/images/README.md"])
def test_every_relative_link_resolves(rel):
    assert broken_links(ROOT, rel) == []


def test_a_missing_link_target_is_caught(tmp_path):
    (tmp_path / "page.md").write_text("[gone](nowhere.md) [fine](page.md) [later](LICENSE.md)", encoding="utf-8")
    assert broken_links(tmp_path, "page.md") == ["nowhere.md", "LICENSE.md"]


def test_a_missing_anchor_is_caught(tmp_path):
    (tmp_path / "other.md").write_text("# Title\n\n## The sandbox, around it\n", encoding="utf-8")
    page = "[ok](other.md#the-sandbox-around-it) [gone](other.md#nope)"
    (tmp_path / "page.md").write_text(page, encoding="utf-8")
    assert broken_links(tmp_path, "page.md") == ["other.md#nope"]


def test_the_readme_html_is_balanced():
    assert html_problems((ROOT / "README.md").read_text(encoding="utf-8")) == []


def test_an_image_without_alt_is_caught():
    assert html_problems('<p><img src="docs/images/x.png" width="10"></p>')
    assert html_problems("<p>open")


def test_the_front_page_keeps_the_honest_framing():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in ("This is not an official Teams bot", "browser session", "may violate your organisation",
                   "Not affiliated with or endorsed by Microsoft", "first person", "says it is an AI", "[Bob]"):
        assert phrase in text, phrase


def test_nothing_true_was_lost():
    docs = "\n".join((ROOT / "docs" / n).read_text(encoding="utf-8")
                     for n in ("setup.md", "running.md", "mcp-servers.md", "architecture.md"))
    for token in ("--watch-state", "BOB_GIF_DIR", "install_launchd.sh", "jira-read-bob", "7126",
                  "OWNER_TEAMS_USER_ID", "umask 077", "```mermaid"):
        assert token in docs, token


def test_the_icon_is_a_plain_vector():
    root = ET.parse(IMAGES / "app-icon.svg").getroot()  # noqa: S314 - our own committed file, not untrusted input
    assert "title" in {_local(el.tag) for el in root.iter()}
    assert svg_problems(root) == []


_SVG = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">{}</svg>'


@pytest.mark.parametrize(("body", "needle"), [
    pytest.param("<foreignObject><div/></foreignObject>", "foreignobject", id="foreignObject"),
    pytest.param("<style>rect{{fill:red}}</style>", "style", id="style"),
    pytest.param('<rect onload="x()"/>', "onload", id="onload"),
    pytest.param('<rect onClick="x()"/>', "onclick", id="onclick"),
    pytest.param("<script>x()</script>", "script", id="script"),
    pytest.param('<image href="x.png"/>', "image", id="image"),
    pytest.param('<a xlink:href="http://e/"><rect/></a>', "href", id="xlink-href"),
])
def test_a_hostile_svg_is_caught(body, needle):
    root = ET.fromstring(_SVG.format(body))  # noqa: S314 - literal test input
    assert any(needle in p for p in svg_problems(root)), svg_problems(root)


def test_a_clean_svg_has_no_problems():
    root = ET.fromstring(_SVG.format("<title>t</title><rect width='1' height='1'/>"))  # noqa: S314 - literal test input
    assert svg_problems(root) == []


def test_the_check_tool_is_green():
    _, findings = tool.verify(IMAGES, ROOT / "README.md")
    assert findings == []


def test_the_pictures_say_nothing_about_a_private_pack():
    pattern = re.compile(
        r"AGENTS\.md|CLAUDE\.md|GEMINI\.md|docs/context\.md|\.claude/|\.agents/|\.codex/|\.grok/|manual-check/"
    )
    sources = sorted(p.relative_to(ROOT).as_posix() for p in (IMAGES / "src").iterdir())
    for rel in [*PAGES, "docs/images/README.md", *sources]:
        assert not pattern.search((ROOT / rel).read_text(encoding="utf-8")), rel


def test_an_html_anchor_to_a_missing_file_is_caught(tmp_path):
    page = '<a href="nowhere.md">x</a> <a href="https://example.com/">y</a>'
    (tmp_path / "page.md").write_text(page, encoding="utf-8")
    assert broken_links(tmp_path, "page.md") == ["nowhere.md"]


def test_a_truncated_picture_is_a_finding_not_a_crash(tmp_path):
    data = (IMAGES / NAMES[0]).read_bytes()
    for name in NAMES:
        (tmp_path / name).write_bytes(data[:-20] if name == NAMES[0] else data[:33])
    _, findings = tool.verify(tmp_path, ROOT / "README.md")
    assert any("truncated PNG" in f for f in findings)
    with pytest.raises(ValueError, match="truncated PNG"):
        tool.strip_chunks(data[:100])


def test_a_picture_cut_at_a_chunk_boundary_is_truncated():
    data = (IMAGES / NAMES[0]).read_bytes()
    with pytest.raises(ValueError, match="truncated PNG"):
        tool.png_size(data[:33])


def test_the_readme_never_names_the_user_data_folder():
    assert "user-data" not in (ROOT / "README.md").read_text(encoding="utf-8")
