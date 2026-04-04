"""
E2E test for issue #40: Mouse position offset in terminal cards.

Verifies that click coordinates inside a terminal card are translated
correctly to xterm.js — i.e. the 32px titlebar and 4px body padding
are NOT included in xterm's coordinate origin.

Requires: playwright, pytest-playwright
Install:  pip install pytest-playwright && playwright install chromium
Run:      pytest tests/test_e2e_mouse_offset.py --headed=false
"""

import asyncio
import concurrent.futures
import threading
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from playwright.sync_api import Page

from claude_rts.server import create_app

TITLEBAR_H = 32  # px — .terminal-titlebar height in CSS
BODY_PADDING = 4  # px — .terminal-body padding in CSS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def live_server():
    """Start the aiohttp server in a background thread for Playwright tests.

    asyncio_mode=auto means pytest-asyncio has a running loop on the main
    thread, so we must run the server loop entirely in a worker thread.
    """
    loop = asyncio.new_event_loop()
    started: concurrent.futures.Future = concurrent.futures.Future()
    runner_holder: list[web.AppRunner] = []

    async def _run():
        app = create_app()
        runner = web.AppRunner(app)
        runner_holder.append(runner)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        started.set_result(port)
        # Keep the loop alive until stopped
        await asyncio.Event().wait()

    def _thread_main():
        loop.run_until_complete(_run())

    with patch(
        "claude_rts.server.discover_hubs",
        new_callable=AsyncMock,
        return_value=[{"hub": "test-hub", "container": "test-container"}],
    ):
        thread = threading.Thread(target=_thread_main, daemon=True)
        thread.start()

        port = started.result(timeout=10)
        yield f"http://127.0.0.1:{port}"

        if runner_holder:
            asyncio.run_coroutine_threadsafe(
                runner_holder[0].cleanup(), loop
            ).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)


@pytest.fixture()
def page_with_card(page: Page, live_server: str):
    """Navigate to the app and spawn a terminal card via the context menu."""
    # Intercept WebSocket connections so they don't fail without a real PTY
    page.route("**/ws/**", lambda route: route.fulfill(status=101))

    page.goto(live_server)

    # Wait for the app JS to boot (hubs endpoint must have loaded)
    page.wait_for_function("() => typeof window.cards !== 'undefined'")
    page.wait_for_function("() => window.hubs && window.hubs.length > 0")

    # Spawn a card directly via JS (avoids needing right-click menu to open)
    page.evaluate("""() => {
        const hub = window.hubs[0];
        window.spawnCard(hub, 100, 100, 600, 400);
    }""")

    page.wait_for_selector(".terminal-card", timeout=5000)
    page.wait_for_selector(".terminal-body", timeout=5000)
    page.wait_for_selector(".xterm-screen", timeout=8000)

    return page


# ---------------------------------------------------------------------------
# Geometry tests — verify the titlebar is excluded from xterm's coordinate space
# ---------------------------------------------------------------------------

class TestTerminalCardGeometry:
    def test_titlebar_height_is_32px(self, page_with_card: Page):
        """The titlebar must be exactly TITLEBAR_H tall (sanity check on CSS)."""
        height = page_with_card.evaluate(
            "() => document.querySelector('.terminal-titlebar').getBoundingClientRect().height"
        )
        assert height == TITLEBAR_H, (
            f"Titlebar height changed — update TITLEBAR_H constant. Got {height}px"
        )

    def test_terminal_body_starts_below_titlebar(self, page_with_card: Page):
        """terminal-body top must equal card top + titlebar height."""
        card_top, body_top = page_with_card.evaluate("""() => {
            const card = document.querySelector('.terminal-card');
            const body = document.querySelector('.terminal-body');
            const cardRect = card.getBoundingClientRect();
            const bodyRect = body.getBoundingClientRect();
            return [cardRect.top, bodyRect.top];
        }""")
        assert body_top == pytest.approx(card_top + TITLEBAR_H, abs=1), (
            f"terminal-body top ({body_top}) should be card top ({card_top}) + {TITLEBAR_H}px titlebar. "
            f"Difference: {body_top - card_top}px. The titlebar offset is not being excluded from "
            f"xterm's coordinate space."
        )

    def test_xterm_screen_top_matches_body_inner_top(self, page_with_card: Page):
        """
        The xterm canvas (.xterm-screen) top edge must align with the
        terminal-body inner top (body.top + padding).

        If this fails with xterm_top == card.top, the bug from issue #40 is
        present: xterm is receiving viewport coords relative to the whole card
        instead of the body element.
        """
        body_top, xterm_top = page_with_card.evaluate("""() => {
            const body = document.querySelector('.terminal-body');
            const screen = document.querySelector('.xterm-screen');
            return [
                body.getBoundingClientRect().top,
                screen.getBoundingClientRect().top,
            ];
        }""")
        expected_top = body_top + BODY_PADDING
        assert xterm_top == pytest.approx(expected_top, abs=2), (
            f"xterm-screen top ({xterm_top:.1f}) should be body top ({body_top:.1f}) + "
            f"{BODY_PADDING}px padding = {expected_top:.1f}. "
            f"If xterm_top ≈ body_top - {TITLEBAR_H} that means titlebar height "
            f"is leaking into xterm's coordinate origin (issue #40)."
        )

    def test_xterm_screen_left_matches_body_inner_left(self, page_with_card: Page):
        """Horizontal alignment check — body padding must be accounted for on x-axis too."""
        body_left, xterm_left = page_with_card.evaluate("""() => {
            const body = document.querySelector('.terminal-body');
            const screen = document.querySelector('.xterm-screen');
            return [
                body.getBoundingClientRect().left,
                screen.getBoundingClientRect().left,
            ];
        }""")
        expected_left = body_left + BODY_PADDING
        assert xterm_left == pytest.approx(expected_left, abs=2), (
            f"xterm-screen left ({xterm_left:.1f}) should be body left ({body_left:.1f}) + "
            f"{BODY_PADDING}px padding = {expected_left:.1f}."
        )


# ---------------------------------------------------------------------------
# Functional test — click row N, selection must start at row N
# ---------------------------------------------------------------------------

class TestClickRowAccuracy:
    def test_click_targets_correct_row(self, page_with_card: Page):
        """
        Click in the centre of the Nth visible row of the terminal.
        xterm's selection anchor must be at row N, not row N±1.

        This is the direct regression test for issue #40: if the titlebar
        height is added as a spurious offset, the selection lands one or more
        rows above the intended target.
        """
        TARGET_ROW = 3  # 0-indexed row to click on (not the very first, to give margin)

        # Get cell height and terminal-body origin from the browser
        cell_h, body_left, body_top, body_width = page_with_card.evaluate("""() => {
            const screen = document.querySelector('.xterm-screen');
            const body   = document.querySelector('.terminal-body');
            const bodyRect = body.getBoundingClientRect();

            // xterm exposes cell dimensions via the core render service
            let cellH = 17; // fallback
            try {
                const term = window.cards[0].term;
                cellH = term._core._renderService.dimensions.css.cell.height;
            } catch (_) {}

            return [cellH, bodyRect.left, bodyRect.top, bodyRect.width];
        }""")

        # Click coordinates: horizontal centre of body, vertical centre of target row
        # We offset from the body top (+ BODY_PADDING) so the click lands inside xterm
        click_x = body_left + body_width / 2
        click_y = body_top + BODY_PADDING + cell_h * TARGET_ROW + cell_h / 2

        # Click and drag slightly to create a selection (single click may not anchor)
        page_with_card.mouse.move(click_x, click_y)
        page_with_card.mouse.down()
        page_with_card.mouse.move(click_x + 5, click_y)
        page_with_card.mouse.up()

        # Read back the selection start row from xterm
        selection_start_row = page_with_card.evaluate("""() => {
            const term = window.cards[0].term;
            const pos = term.getSelectionPosition();
            return pos ? pos.startRow : null;
        }""")

        assert selection_start_row is not None, (
            "No xterm selection was created — the click may not have reached xterm."
        )
        assert selection_start_row == TARGET_ROW, (
            f"Selection started at row {selection_start_row}, expected row {TARGET_ROW}. "
            f"Offset = {selection_start_row - TARGET_ROW} row(s). "
            f"This indicates the titlebar/padding height is being added as a spurious "
            f"offset to click coordinates (issue #40)."
        )
