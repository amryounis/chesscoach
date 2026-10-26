"""
The desktop page in headless Chromium, wired to the real Python Api.
Skipped unless Playwright (and its Chromium) is installed:
    pip install playwright && playwright install chromium
"""
import asyncio
import sys
import warnings

import pytest

from chesscoach import config, narrator
from chesscoach.gui.api import Api
from chesscoach.gui.app import serve_static
from conftest import DATA

sync_api = pytest.importorskip("playwright.sync_api")

BRIDGE = """
window.pywebview = { api: new Proxy({}, { get: (_, n) => (...a) => window.__py(n, a) }) };
window.addEventListener('DOMContentLoaded', () =>
  setTimeout(() => window.dispatchEvent(new Event('pywebviewready')), 0));
"""


class FakeWindow:
    path = DATA / "homer_vs_fritz.pgn"

    def create_file_dialog(self, kind, **kw):
        return [str(self.path)]


@pytest.fixture(scope="module")
def browser():
    if sys.platform != "win32" and sys.version_info < (3, 12):
        # Earlier tests start engines from threads, which can leave asyncio's
        # global child watcher unusable for Playwright's own subprocess.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_child_watcher(asyncio.ThreadedChildWatcher())
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as e:
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser, stockfish, monkeypatch, tmp_path):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia",
                                           depth=10)
    config.settings.player_name = "Homer"
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(narrator.ollama, "chat", lambda model, messages, options=None, **kw: {
        "message": {"content": "**Assessment**: x\n**What happened**: A reason.\n"
                               "**Better was**: Something else."}})
    api = Api()
    api._window = FakeWindow()
    server = serve_static()
    ctx = browser.new_context(viewport={"width": 1320, "height": 860})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.expose_function("__py", lambda name, args: getattr(api, name)(*args))
    pg.add_init_script(BRIDGE)
    pg.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html")
    pg.wait_for_timeout(400)
    yield pg
    ctx.close()
    server.shutdown()
    api.shutdown()


def test_review_flow(page):
    page.click("#open-pgn")
    assert page.input_value("#review-side") == "black"          # guessed from the name
    page.click("#start-review")
    page.wait_for_selector("#moments:not([hidden]) button", timeout=180000)
    assert page.locator("#moments button").count() >= 2
    # The review opens on the game summary, written by code from stored facts.
    summary = page.inner_text("#summary")
    assert "Game summary" in summary and "You made" in summary
    assert "Across your reviewed games" in summary          # stored in the history
    if page.locator("#summary a.ply").count():
        page.click("#summary a.ply >> nth=0")
        assert page.locator("#summary").count() == 0        # now on that move
    page.click("#moments button >> nth=1")
    assert "A reason." in page.inner_text("#coach")
    # Facts behind the comment: the panel lists them and circles their squares.
    if page.locator("#facts").count():
        page.click("#facts summary")
        assert page.locator("#facts li").count() + page.locator("#facts .threat").count() >= 1
        if page.evaluate("[...document.querySelectorAll('#facts li')].some(l => l.dataset.squares)"):
            page.wait_for_function("document.querySelectorAll('.cg-shapes circle').length >= 1",
                                   timeout=5000)
    if page.locator("#compare").count():
        page.click("#compare")
        # chessground draws arrows on the next animation frame
        page.wait_for_function(
            "document.querySelectorAll('.cg-shapes line, .cg-shapes path').length >= 2",
            timeout=5000)
    page.keyboard.press("End")
    assert "e5#" in page.inner_text(".movelist .mv.current")
    assert page.errors == []


def test_play_flow(page):
    page.click("#tab-play")
    page.click("#start-game")
    page.wait_for_selector("#play-live:not([hidden])")
    box = page.locator("#board").bounding_box()
    sq = box["width"] / 8
    for s in ("e2", "e4"):
        f, r = ord(s[0]) - 97, int(s[1]) - 1
        page.mouse.click(box["x"] + (f + .5) * sq, box["y"] + (7 - r + .5) * sq)
        page.wait_for_timeout(150)
    page.wait_for_function("document.querySelectorAll('#play-moves .mv').length === 2")
    page.click("#resign")
    page.wait_for_selector("#review-played:not([hidden])")
    page.click("#review-played")
    page.wait_for_selector("#panel-review:not([hidden])")
    assert page.inner_text("#game-title").startswith("Homer")
    page.click("#cancel-review")
    assert page.errors == []


def test_explain_and_ask(page):
    page.click("#open-pgn")
    assert page.is_visible("#ask")
    page.keyboard.press("End")                     # last position of the game
    page.click("#explain-btn")
    page.wait_for_selector(".ask-answer .meta", timeout=120000)
    text = page.inner_text("#ask-answer")
    assert "Explain this position" in text
    assert "is to move" in text or "checkmated" in text       # code-written verdict
    page.fill("#ask-q", "Who is better?")
    page.press("#ask-q", "Enter")
    page.wait_for_function(
        "document.querySelectorAll('.ask-answer .meta').length >= 2", timeout=120000)
    assert "You asked: Who is better?" in page.inner_text("#ask-answer")
    # Answers belong to the position: another move shows none (the keys are
    # ignored while typing in the box, so use the button).
    page.click('[data-nav="first"]')
    assert page.inner_text("#ask-answer").strip() == ""
    assert not page.errors


def test_annotated_reader(page, monkeypatch):
    monkeypatch.setattr(FakeWindow, "path", DATA / "abasov_annotated.pgn")
    page.click("#open-pgn")
    page.wait_for_selector("#annotations a.tok")
    assert page.locator("#game-pick option").count() == 25
    assert not page.is_hidden("#annotations") and page.is_hidden("#movelist")
    text = page.inner_text("#annotations")
    assert "This felt wrong to me." in text and "fühlte" not in text     # English only
    assert page.locator("#evalgraph svg").count() == 1
    # A move in a variation: the board follows it, the panel says so.
    page.click("#annotations .var-block a.tok >> nth=0")
    assert "annotator's variations" in page.inner_text("#coach")
    page.keyboard.press("ArrowRight")
    assert page.locator("#annotations a.tok.cur").count() == 1
    # The annotator's arrow is drawn: 11...bxa4 12.Rxa4 (castling next, e1-g1).
    page.click("#annotations a.tok:has-text('♖xa4') >> nth=0")
    page.wait_for_function("document.querySelectorAll('.cg-shapes line').length >= 1", timeout=5000)
    # Another game from the file.
    page.select_option("#game-pick", "15")
    page.wait_for_function("document.querySelector('#game-title').textContent.includes('Caruana')")
    assert page.errors == []
