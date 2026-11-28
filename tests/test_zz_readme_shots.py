"""Not part of the suite: the README's screenshots (docs/). Run with SHOTS=1."""
import os

import pytest

from chesscoach import coach, narrator
from test_gui_page import browser, page  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SHOTS"), reason="screenshots only")
DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")

# qwen3:14b's own sentence on 8...Bg4 in Amr's review of this game (chesscoach
# 0.27.6, homer_vs_fritz_annotated.pgn); the app writes the rest itself.
QWEN = ("**What happened**: Black moves the bishop from c8 to g4. The move fails to address "
        "White's immediate threat of capturing the pawn on g6 with the pawn from h5, and "
        "instead creates a counter-threat that is less valuable.")


def test_review_shot(page, monkeypatch):  # noqa: F811
    monkeypatch.setattr(narrator.ollama, "chat", lambda model, messages, options=None, **kw: {
        "message": {"content": QWEN}})
    page.click("#open-pgn")
    page.select_option("#review-side", "black")
    page.click("#start-review")
    page.wait_for_selector("#moments:not([hidden]) button", timeout=300000)
    page.click("#moments button:has-text('g4')")
    page.wait_for_selector("#coach .move")
    page.click("#viewtabs [data-view=moves]")
    page.click("#compare")
    page.wait_for_function("document.getElementById('toast').hidden", timeout=20000)
    page.wait_for_timeout(300)
    page.screenshot(path=os.path.join(DOCS, "review.png"))


def test_explain_shot(page, monkeypatch):  # noqa: F811
    def no_model(*a, **kw):
        raise ConnectionError("no model in the screenshot")
    monkeypatch.setattr(coach, "_chat", no_model)
    page.evaluate("() => { if (navigator.clipboard) navigator.clipboard.readText = () => Promise.reject(new Error('no')); }")
    page.click("#paste-btn")
    page.fill("#paste-area", "rn1qkbnr/pp2pppp/8/2PpPb2/8/8/PPP2PPP/RNBQKBNR w KQkq - 0 1")
    page.click("#paste-open")
    page.wait_for_function("document.querySelector('#game-title').textContent === 'Pasted position'")
    page.click("#explain-btn")
    page.wait_for_selector(".ask-answer .meta", timeout=120000)
    page.wait_for_function("document.getElementById('toast').hidden", timeout=20000)
    page.locator("#coach-scroll").evaluate("e => e.scrollTop = 0")
    page.hover("ul.sheet li:has-text('has no defender. Black')")
    page.wait_for_timeout(300)
    page.screenshot(path=os.path.join(DOCS, "explain.png"))
