"""Unit tests for scripts/teams_agent/scraper.py.

Uses MagicMock for page / frame / locator — no live Playwright required.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent"))
import scraper  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers for building mock page objects
# ---------------------------------------------------------------------------


def _make_visible_locator(text: str = "item") -> MagicMock:
    """Return a locator mock that reports visible state."""
    loc = MagicMock()
    loc.wait_for.return_value = None  # visible
    loc.inner_text.return_value = text
    loc.get_attribute.return_value = ""
    return loc


def _make_invisible_locator() -> MagicMock:
    """Return a locator mock that raises TimeoutError on wait_for (not visible)."""
    loc = MagicMock()
    loc.wait_for.side_effect = Exception("Timeout waiting for visible")
    return loc


def _make_page_with_match(selector_index: int = 0, selectors_list=None) -> MagicMock:
    """Return a page mock where only the selector at selector_index in selectors_list is visible."""
    if selectors_list is None:
        selectors_list = scraper.CHAT_LIST_SELECTORS

    visible_sel = selectors_list[selector_index]

    page = MagicMock()
    page.frames = []
    page.url = "https://teams.microsoft.com/v2/"

    def _locator(sel):
        loc_wrap = MagicMock()
        if sel == visible_sel:
            loc_wrap.first = _make_visible_locator()
            loc_wrap.all.return_value = [_make_visible_locator("Chat Alice"), _make_visible_locator("Chat Bob")]
        else:
            loc_wrap.first = _make_invisible_locator()
            loc_wrap.all.return_value = []
        return loc_wrap

    page.locator.side_effect = _locator
    return page


# ---------------------------------------------------------------------------
# _find_in_context
# ---------------------------------------------------------------------------


class TestFindInContext:
    def test_returns_first_visible_selector(self):
        """Returns the locator for the first selector that is visible."""
        ctx = MagicMock()
        visible_loc = _make_visible_locator()
        invisible_loc = _make_invisible_locator()

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = visible_loc if sel == scraper.CHAT_LIST_SELECTORS[0] else invisible_loc
            return wrap

        ctx.locator.side_effect = _locator
        result = scraper._find_in_context(ctx, scraper.CHAT_LIST_SELECTORS, timeout_ms=100)
        assert result is not None
        loc, returned_ctx = result
        assert loc is visible_loc
        assert returned_ctx is ctx

    def test_falls_through_to_second_selector(self):
        """When the first selector times out, tries the second."""
        ctx = MagicMock()
        visible_loc = _make_visible_locator()
        invisible_loc = _make_invisible_locator()

        def _locator(sel):
            wrap = MagicMock()
            if sel == scraper.CHAT_LIST_SELECTORS[0]:
                wrap.first = invisible_loc
            elif sel == scraper.CHAT_LIST_SELECTORS[1]:
                wrap.first = visible_loc
            else:
                wrap.first = invisible_loc
            return wrap

        ctx.locator.side_effect = _locator
        result = scraper._find_in_context(ctx, scraper.CHAT_LIST_SELECTORS, timeout_ms=100)
        assert result is not None
        loc, _ = result
        assert loc is visible_loc

    def test_returns_none_when_all_fail(self):
        """Returns None when no selector matches."""
        ctx = MagicMock()
        invisible_loc = _make_invisible_locator()

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = invisible_loc
            return wrap

        ctx.locator.side_effect = _locator
        result = scraper._find_in_context(ctx, scraper.CHAT_LIST_SELECTORS, timeout_ms=100)
        assert result is None


# ---------------------------------------------------------------------------
# Frame fallback
# ---------------------------------------------------------------------------


class TestFindWithFrameFallback:
    def test_frame_fallback_engages(self):
        """When page returns None, falls back to searching frames."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"

        invisible_loc = _make_invisible_locator()
        visible_loc = _make_visible_locator()

        def _page_locator(sel):
            wrap = MagicMock()
            wrap.first = invisible_loc
            return wrap

        page.locator.side_effect = _page_locator

        frame = MagicMock()
        frame.url = "https://teams.microsoft.com/frame"

        def _frame_locator(sel):
            wrap = MagicMock()
            if sel == scraper.CHAT_LIST_SELECTORS[0]:
                wrap.first = visible_loc
            else:
                wrap.first = invisible_loc
            return wrap

        frame.locator.side_effect = _frame_locator
        page.frames = [frame]

        result = scraper._find_with_frame_fallback(page, scraper.CHAT_LIST_SELECTORS, timeout_ms=100)
        assert result is not None
        loc, ctx = result
        assert loc is visible_loc
        assert ctx is frame


# ---------------------------------------------------------------------------
# list_chats
# ---------------------------------------------------------------------------


class TestListChats:
    def test_returns_empty_on_no_match(self):
        """Returns [] when no selector matches within deadline (deadline patched to 0s)."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.title.return_value = "Teams"
        page.frames = []

        invisible_loc = _make_invisible_locator()

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = invisible_loc
            wrap.all.return_value = []
            return wrap

        page.locator.side_effect = _locator

        # Patch time to make deadline expire immediately. Allow plenty of
        # _time_mod.time() calls because the miss branch logs elapsed time
        # after dumping candidates + body HTML.
        with patch("scraper._time_mod") as mock_time:
            mock_time.time.side_effect = [0] + [99999] * 50
            with patch("scraper._debug_screenshot"), \
                 patch("scraper._dump_candidates"), \
                 patch("scraper._dump_body_html"), \
                 patch("scraper._diag_page"):
                result = scraper.list_chats(page)

        assert result == []

    def test_normalises_nbsp(self):
        """Non-breaking spaces in chat names are replaced with regular spaces."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.frames = []

        # Time marker (12:34) makes this look like a real chat row to the filter.
        nbsp_text = "Chat\xa0Alice 12:34 ostatnia wiadomosc"
        visible_loc = _make_visible_locator(nbsp_text)
        visible_loc.get_attribute.return_value = ""

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = visible_loc
            wrap.all.return_value = [visible_loc]
            return wrap

        page.locator.side_effect = _locator

        result = scraper.list_chats(page)
        assert len(result) >= 1
        assert '\xa0' not in result[0]["name"]
        # Name is extracted as everything before the time marker.
        assert result[0]["name"] == "Chat Alice"

    def test_priority_order_first_selector_tried_first(self):
        """The first CHAT_LIST_SELECTORS entry is tried before later ones."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.frames = []

        call_order = []
        # Time marker so the chat-item filter accepts this row.
        visible_loc = _make_visible_locator("Alice 09:00 hello")
        visible_loc.get_attribute.return_value = ""

        def _locator(sel):
            call_order.append(sel)
            wrap = MagicMock()
            wrap.first = visible_loc
            wrap.all.return_value = [visible_loc]
            return wrap

        page.locator.side_effect = _locator
        scraper.list_chats(page)

        # Confirm the first selector was tried before any other.
        assert scraper.CHAT_LIST_SELECTORS[0] in call_order
        first_idx = call_order.index(scraper.CHAT_LIST_SELECTORS[0])
        for sel in scraper.CHAT_LIST_SELECTORS[1:]:
            if sel in call_order:
                assert call_order.index(sel) >= first_idx


# ---------------------------------------------------------------------------
# read_messages — nbsp normalisation
# ---------------------------------------------------------------------------


class TestReadMessages:
    def test_normalises_nbsp_in_messages(self):
        """Message text with \\xa0 is normalised to regular spaces."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.frames = []

        nbsp_msg = "Hello\xa0World"
        visible_loc = _make_visible_locator(nbsp_msg)

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = visible_loc
            wrap.all.return_value = [visible_loc]
            return wrap

        page.locator.side_effect = _locator
        result = scraper.read_messages(page, max_messages=20)

        assert len(result) >= 1
        for msg in result:
            assert '\xa0' not in msg["text"]

    def test_returns_empty_on_no_thread(self):
        """Returns [] when the message thread is not found (deadline patched)."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.title.return_value = "Teams"
        page.frames = []

        invisible_loc = _make_invisible_locator()

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = invisible_loc
            wrap.all.return_value = []
            return wrap

        page.locator.side_effect = _locator

        with patch("scraper._time_mod") as mock_time:
            mock_time.time.side_effect = [0, 99999]
            with patch("scraper._debug_screenshot"):
                result = scraper.read_messages(page)

        assert result == []


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_returns_false_for_empty_text(self):
        """Returns False immediately for empty or whitespace text."""
        page = MagicMock()
        assert scraper.send_message(page, "") is False
        assert scraper.send_message(page, "   ") is False

    def test_returns_false_when_input_not_found(self):
        """Returns False when reply input is not found (deadline patched)."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.title.return_value = "Teams"
        page.frames = []

        invisible_loc = _make_invisible_locator()

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = invisible_loc
            return wrap

        page.locator.side_effect = _locator

        with patch("scraper._time_mod") as mock_time:
            mock_time.time.side_effect = [0, 99999]
            with patch("scraper._debug_screenshot"):
                result = scraper.send_message(page, "hello")

        assert result is False

    def test_debug_screenshot_written_on_miss(self):
        """_debug_screenshot is called when the reply input is not found."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.title.return_value = "Teams"
        page.frames = []

        invisible_loc = _make_invisible_locator()

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = invisible_loc
            return wrap

        page.locator.side_effect = _locator

        with patch("scraper._time_mod") as mock_time:
            mock_time.time.side_effect = [0, 99999]
            with patch("scraper._debug_screenshot") as mock_screenshot:
                scraper.send_message(page, "hello")

        mock_screenshot.assert_called()


# ---------------------------------------------------------------------------
# switch_to_chat
# ---------------------------------------------------------------------------


class TestSwitchToChat:
    def test_returns_false_for_invalid_chat_id(self):
        """Returns False when chat_id cannot be parsed as int."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.frames = []
        visible_loc = _make_visible_locator()

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = visible_loc
            wrap.all.return_value = [visible_loc]
            return wrap

        page.locator.side_effect = _locator
        result = scraper.switch_to_chat(page, "not-a-number")
        assert result is False

    def test_returns_false_when_list_not_found(self):
        """Returns False when chat list is not found (deadline patched)."""
        page = MagicMock()
        page.url = "https://teams.microsoft.com/v2/"
        page.title.return_value = "Teams"
        page.frames = []

        invisible_loc = _make_invisible_locator()

        def _locator(sel):
            wrap = MagicMock()
            wrap.first = invisible_loc
            wrap.all.return_value = []
            return wrap

        page.locator.side_effect = _locator

        with patch("scraper._time_mod") as mock_time:
            mock_time.time.side_effect = [0, 99999]
            with patch("scraper._debug_screenshot"):
                result = scraper.switch_to_chat(page, "0")

        assert result is False
