"""
The desktop page in headless Chromium, wired to the real Python Api.
Skipped unless Playwright (and its Chromium) is installed:
    pip install playwright && playwright install chromium
"""
import asyncio
import re
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
    pg.api = api
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
    # The game summary, written by code from stored facts, in its own tab.
    page.click("#view-summary")
    summary = page.inner_text("#summary-view")
    assert "Game summary" in summary and "You made" in summary
    assert "Across your reviewed games" in summary          # stored in the history
    if page.locator("#summary-view a.ply").count():
        page.click("#summary-view a.ply >> nth=0")
        assert page.is_hidden("#summary-view")              # now on that move
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


def _click_square(page, sq, orientation="white"):
    box = page.locator("#board").bounding_box()
    size = box["width"] / 8
    f, r = ord(sq[0]) - 97, int(sq[1]) - 1
    if orientation == "black":
        f, r = 7 - f, 7 - r
    page.mouse.click(box["x"] + (f + .5) * size, box["y"] + (7 - r + .5) * size)
    page.wait_for_timeout(150)


def test_practice_your_mistakes(page):
    page.click("#open-pgn")
    page.select_option("#review-side", "white")          # White made the mistakes here
    page.click("#start-review")
    page.wait_for_selector("#moments .practice-link", timeout=180000)
    # how hard each error was: in the Annotations pane as well as the comment
    page.wait_for_selector("#annotations .cmt.coach .difficulty", state="attached")
    assert page.locator("#annotations .cmt.coach .difficulty").first.inner_text().split(".")[0] in (
        "In plain sight", "Partly hidden", "Hard to see")
    page.click("#moments .practice-link")
    page.wait_for_selector("#drill:not([hidden])")
    assert "Find a better move" in page.inner_text("#drill-prompt")
    listing = page.evaluate("window.pywebview.api.practice_drills('game')")["drills"]
    assert [d["ply"] for d in listing] == sorted(d["ply"] for d in listing)   # move order
    first = listing[0]
    assert first["color"] == "white"                       # the reviewed side's mistakes
    # Play the engine's move on the board: it is judged correct.
    _click_square(page, first["best_uci"][:2], "white")
    _click_square(page, first["best_uci"][2:4], "white")
    page.wait_for_selector("#drill-result .verdict", timeout=60000)
    assert page.inner_text("#drill-result .verdict").startswith("Correct")
    assert "engine line" in page.inner_text("#drill-result").lower()   # shown in caps
    # "Show me" on the next one reveals the move, the line and the comment.
    page.click("#drill-next")
    page.click("#drill-hint")                              # a hint first, one step
    page.wait_for_selector("#drill-hint-box:not([hidden]) li", timeout=60000)
    assert page.inner_text("#drill-hint") == "Hint 2 of 4"
    import os
    if os.environ.get("CC_SHOTS"):
        page.screenshot(path=os.path.join(os.environ["CC_SHOTS"], "drill-hint.png"))
    page.click("#drill-reveal")
    page.wait_for_selector("#drill-result .verdict", timeout=60000)
    assert "The engine's move was" in page.inner_text("#drill-result")
    assert "the coach said in the review" in page.inner_text("#drill-result").lower()
    # no All moves here, and Next exercise stays reachable on a small window
    assert page.locator("#drill-all").count() == 0
    page.set_viewport_size({"width": 1320, "height": 300})
    page.mouse.move(400, 150)                      # over the panel: the wheel scrolls it
    page.mouse.wheel(0, 1500)
    page.wait_for_timeout(300)
    box = page.locator("#drill-next").bounding_box()
    assert box and box["y"] >= 0 and box["y"] + box["height"] <= 300   # on screen
    page.click("#drill-next")
    page.set_viewport_size({"width": 1320, "height": 860})
    # with the answer, the coach's words and the list open, Next is still in view
    box = page.locator("#drill-next").bounding_box()
    assert box and box["y"] + box["height"] <= page.viewport_size["height"]
    assert page.errors == []


def test_version_is_shown(page):
    from chesscoach import __version__
    page.wait_for_function("document.querySelector('#app-version').textContent !== ''")
    assert page.inner_text("#app-version") == f"v{__version__}"
    assert page.title() == f"chesscoach {__version__}"


def test_play_says_who_the_opponent_is(page):
    page.click("#tab-play")
    page.fill("#elo", "800")
    page.dispatch_event("#elo", "input")
    page.wait_for_function("document.querySelector('#opponent-note').textContent.includes('1320')")
    note = page.inner_text("#opponent-note")
    assert "lowest setting" in note and "stronger than the 800" in note
    assert "warn" in page.get_attribute("#opponent-note", "class")
    page.click("#start-game")
    page.wait_for_selector("#play-live:not([hidden])")
    assert "Stockfish at 1320" in page.inner_text("#play-opponent")
    assert page.errors == []


def test_review_fills_the_annotations_and_keeps_comments_visible(page):
    page.click("#open-pgn")
    page.click("#start-review")
    page.wait_for_selector("#moments:not([hidden]) button", timeout=180000)
    # The coach's comments are in the Annotations pane, beside the annotator's.
    assert page.is_visible("#annotations")
    notes = page.inner_text("#annotations")
    assert "Comments by the coach from this review" in notes or \
        "comments by the coach from this review" in notes
    assert page.locator("#annotations .cmt.coach").count() >= 1
    assert "A reason." in page.inner_text("#annotations .cmt.coach >> nth=0")
    # The graph is a framed line, with ticks at the errors.
    assert page.locator("#evalgraph .area").count() == 0
    assert page.locator("#evalgraph .ln").count() == 1
    # Explain this position: the answer goes below the comment, which stays.
    page.click("#moments button >> nth=1")
    comment = page.inner_text("#coach")
    assert "A reason." in comment
    page.click("#explain-btn")
    page.wait_for_selector(".ask-answer .meta", timeout=120000)
    assert page.inner_text("#coach") == comment
    assert "Explain this position" in page.inner_text("#ask-answer")
    # Leave and come back: the move opens on its comment again.
    page.click("#moments button >> nth=2")
    page.click("#moments button >> nth=1")
    assert page.evaluate("document.getElementById('coach-scroll').scrollTop") == 0
    assert "A reason." in page.inner_text("#coach")
    # The splitter still resizes the move list.
    split = page.locator("#splitter").bounding_box()
    page.click("#viewtabs [data-view=moves]")
    h0 = page.locator("#movelist").bounding_box()["height"]
    page.mouse.move(split["x"] + 20, split["y"] + 2)
    page.mouse.down()
    page.mouse.move(split["x"] + 20, split["y"] + 80, steps=5)
    page.mouse.up()
    assert page.locator("#movelist").bounding_box()["height"] > h0 + 40
    assert page.errors == []


def test_the_board_follows_a_running_review(page):
    """While Stockfish works the board shows the position being analysed,
    the move list and graph follow, and the coach panel says what is on."""
    page.click("#open-pgn")
    page.click("#start-review")
    page.wait_for_function(
        "document.querySelector('#coach .live') && document.querySelector('#movelist .mv.current')",
        timeout=60000)
    assert "Stockfish is analysing" in page.inner_text("#coach") or \
        "The coach is writing" in page.inner_text("#coach")
    page.wait_for_selector("#evalgraph:not([hidden]) .ln", timeout=60000)
    # Taking over the board stops following; Follow brings it back.
    page.keyboard.press("Home")
    assert page.is_visible("#follow-review")
    page.click("#follow-review")
    assert not page.is_visible("#follow-review")
    page.wait_for_selector("#moments:not([hidden]) button", timeout=180000)
    assert page.locator("#coach .live").count() == 0           # the summary replaces it
    assert page.errors == []


def test_games_tab_lists_opens_and_explores(page):
    page.click("#open-pgn")
    page.click("#start-review")
    page.wait_for_selector("#moments:not([hidden]) button", timeout=180000)
    page.click("#tab-games")
    page.wait_for_selector("#lib-rows tr")
    row = page.locator("#lib-rows tr").first
    assert "Homer" in row.inner_text() and "✓" in row.inner_text()
    row.dblclick()
    page.wait_for_selector("#panel-review:not([hidden])")
    page.wait_for_selector("#moments:not([hidden]) button")       # the kept review, at once
    assert page.is_visible("#save-annotated")
    # the coach's notes come from the review; the PGN's copies are not shown again
    page.click("#viewtabs [data-view=notes]")
    notes = page.inner_text("#annotations")
    assert "Coach:" not in notes and "Coach summary:" not in notes
    assert notes.count("A reason.") == page.locator("#annotations .cmt.coach").count()
    page.click("#tab-openings")
    page.wait_for_selector("#ex-rows tr")
    first = page.inner_text("#ex-rows tr >> nth=0")
    assert "1" in first                                          # games count
    page.click("#ex-rows tr >> nth=0")
    page.wait_for_function("document.getElementById('ex-line').textContent.startsWith('1.')")
    page.click("#ex-back")
    page.wait_for_function("document.getElementById('ex-line').textContent === 'Starting position'")
    assert page.errors == []



def test_live_review_marks_the_king_in_check(page, monkeypatch):
    """While a review followed 12.gxf7+ (White checks), the board lit up
    White's king: it was told 'a check' but not whose. And a slow position
    shows how long it has been on it."""
    import threading
    from chesscoach.gui import api as api_mod
    hold = threading.Event()

    def stuck_review(game, side, models, every_move, progress=None, should_stop=None, **kw):
        progress("engine", 22, 79, "Analysing positions", {"ply": 23, "searching": True})
        hold.wait(30)
        raise api_mod.Cancelled()

    monkeypatch.setattr(api_mod, "review_game", stuck_review)
    page.click("#open-pgn")
    page.click("#start-review")
    page.wait_for_function("document.querySelector('#coach .live') && "
                           "document.querySelector('#coach .live').textContent.includes('gxf7')")
    box = page.locator("#board").bounding_box()
    sq = box["width"] / 8
    page.wait_for_selector("cg-board square.check")
    r = page.locator("cg-board square.check").bounding_box()
    file, rank_from_top = round((r["x"] - box["x"]) / sq), round((r["y"] - box["y"]) / sq)
    # the review is of Black (Homer): the board is from Black's side, so e8 is at
    # file index 3 (h..a), row 7 from the top
    king = "e8"
    flipped = page.evaluate("document.querySelector('.cg-wrap').classList.contains('orientation-black')")
    want = (7 - 4, 7) if flipped else (4, 0)
    assert (file, rank_from_top) == want, (file, rank_from_top, flipped, king)
    page.wait_for_function("document.getElementById('progress-text').textContent.includes('s')"
                           " && /· \\d+s/.test(document.getElementById('progress-text').textContent)",
                           timeout=10000)
    hold.set()
    page.click("#cancel-review")
    assert page.errors == []


def test_analysis_table_and_move_counts(page):
    page.click("#open-pgn")
    page.click("#start-review")
    page.wait_for_selector("#moments:not([hidden]) button", timeout=180000)
    # the counts per side, at the top of the game summary
    page.click("#view-summary")
    st = page.inner_text("#summary-view .st-table")
    assert "Accuracy" in st and "Best moves" in st and "Acceptable" in st
    page.click("#view-table")
    page.wait_for_selector("#analysis:not([hidden]) tbody tr")
    assert page.locator("#analysis tbody tr").count() == 78
    page.click("#analysis .an-head [data-side=white]")
    assert page.locator("#analysis tbody tr").count() == 39
    assert page.locator("#analysis .wdl.sm").count() >= 30          # Stockfish's W/D/L
    page.click("#analysis tbody tr >> nth=5")                          # 6. h4
    page.wait_for_function("document.querySelector('#analysis tr.cur')")
    assert "6. h4" in page.inner_text("#coach .move")
    assert page.errors == []


def test_explorer_shows_the_books_moves_and_their_ideas(page, tmp_path):
    from chesscoach import book_study as bs
    from test_book_study import LINES, write_book
    book = write_book(tmp_path / "b.bin", LINES)
    bs.run(book, "m", plies=2, min_line=0.01, depth=6,
           llm=lambda model, msgs, system=None: ("Grabs the centre and frees a bishop.", None))
    page.click("#tab-openings")
    page.wait_for_selector("#ex-rows tr.inbook")
    first = page.locator("#ex-rows tr").first
    assert first.inner_text().startswith("e4") and "%" in first.inner_text()
    assert first.get_attribute("title") == "Grabs the centre and frees a bishop."
    first.click()
    page.wait_for_selector("#ex-about .why")
    assert "The idea of e4" in page.inner_text("#ex-about") and "Grabs the centre" in page.inner_text("#ex-about")
    assert page.errors == []


def test_hints_and_the_blunder_alert_in_play(page):
    page.click("#tab-play")
    page.click("#start-game")
    page.wait_for_selector("#play-live:not([hidden])")
    assert page.inner_text("#play-hint-btn") == "Hint"
    page.click("#play-hint-btn")
    page.wait_for_selector("#play-hint:not([hidden]) li")
    assert page.inner_text("#play-hint-btn") == "Hint 2 of 4"
    page.click("#play-hint-btn")
    page.wait_for_function("document.querySelectorAll('#play-hint li').length === 2")
    assert "The move goes to" in page.inner_text("#play-hint") or "takes on" in page.inner_text("#play-hint")
    import os
    if os.environ.get("CC_SHOTS"):
        page.screenshot(path=os.path.join(os.environ["CC_SHOTS"], "hint.png"))
    _click_square(page, "e2", "white")
    _click_square(page, "e4", "white")
    page.wait_for_function("document.querySelectorAll('#play-moves .mv').length === 2")
    assert page.is_hidden("#play-hint")                       # a new position: no old hint
    # A blunder: the alert asks; Think again keeps the board as it was.
    page.api._play.board.set_fen("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 0 4")
    page.click("#takeback")                     # nothing to take back: just shows the board
    page.wait_for_timeout(300)
    _click_square(page, "h5", "white")
    _click_square(page, "h7", "white")
    page.wait_for_selector("#play-alert:not([hidden])", timeout=30000)
    assert page.inner_text("#play-alert-text").startswith("Are you sure about")
    import os
    if os.environ.get("CC_SHOTS"):
        page.screenshot(path=os.path.join(os.environ["CC_SHOTS"], "alert.png"))
    page.click("#alert-back")
    assert page.is_hidden("#play-alert")
    assert page.locator("#play-moves .mv").count() == 0
    _click_square(page, "h5", "white")
    _click_square(page, "h7", "white")
    page.wait_for_selector("#play-alert:not([hidden])", timeout=30000)
    page.click("#alert-play")
    page.wait_for_function("document.querySelectorAll('#play-moves .mv').length === 2")
    assert page.is_hidden("#play-alert")
    assert page.errors == []


def test_paste_a_position_and_copy_it(page):
    page.evaluate("() => { if (navigator.clipboard) navigator.clipboard.readText = () => Promise.reject(new Error('no')); }")
    page.click("#paste-btn")
    page.wait_for_selector("#paste-dialog[open]")
    page.fill("#paste-area", "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    page.click("#paste-open")
    page.wait_for_function("document.querySelector('#game-title').textContent === 'Pasted position'")
    assert "White to move" in page.inner_text("#game-sub")
    assert page.is_hidden("#review-controls")
    assert page.api._game.board().fen().startswith("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2")
    page.click("#copy-fen")
    page.wait_for_function("/copied|clipboard/.test(document.querySelector('#toast').textContent)")
    for tab in ("play", "practice", "games"):
        page.click(f"#tab-{tab}")
        assert page.is_visible("#copy-fen") and page.is_hidden("#nav [data-nav=first]")
    assert page.errors == []


def test_the_opening_is_shown_under_the_board(page):
    page.evaluate("() => { if (navigator.clipboard) navigator.clipboard.readText = () => Promise.reject(new Error('no')); }")
    page.click("#paste-btn")
    page.wait_for_selector("#paste-dialog[open]")
    page.fill("#paste-area", "1. Nf3 c5 2. e4 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. h3 *")
    page.click("#paste-open")
    page.wait_for_function("document.querySelector('#game-sub').textContent.includes('6 moves')")
    assert page.is_hidden("#opening-now")                       # the start position has no name
    page.click("#nav [data-nav=last]")
    page.wait_for_function("document.querySelector('#opening-name').textContent.includes('Najdorf')")
    assert page.inner_text("#opening-name").startswith("B9")      # ECO, then the name
    import os
    if os.environ.get("CC_SHOTS"):
        page.screenshot(path=os.path.join(os.environ["CC_SHOTS"], "opening.png"))
    page.click("#tab-play")
    page.click("#start-game")
    page.wait_for_selector("#play-live:not([hidden])")
    assert page.is_hidden("#opening-now")
    _click_square(page, "e2", "white")
    _click_square(page, "e4", "white")
    page.wait_for_function("document.querySelectorAll('#play-moves .mv').length === 2")
    page.wait_for_selector("#opening-now:not([hidden])")
    page.click("#tab-games")
    assert page.is_hidden("#opening-now")
    assert page.errors == []


def test_all_moves_in_review(page):
    page.evaluate("() => { if (navigator.clipboard) navigator.clipboard.readText = () => Promise.reject(new Error('no')); }")
    page.click("#paste-btn")
    page.wait_for_selector("#paste-dialog[open]")
    page.fill("#paste-area", "1. e4 c5 2. Nf3 Nc6 3. Nc3 e6 4. d4 cxd4 5. Nxd4 *")
    page.click("#paste-open")
    page.wait_for_function("document.querySelector('#game-sub').textContent.includes('5 moves')")
    for _ in range(8):
        page.click("#nav [data-nav=next]")
    page.click("#all-moves-btn")
    page.wait_for_selector("table.all-moves tr", timeout=60000)
    first = page.locator("table.all-moves tr").first
    assert "Best move: recaptures" in first.inner_text()
    assert "played in the game" in page.inner_text("table.all-moves tr.marked")
    first.hover()
    page.evaluate("document.querySelector('#ask-answer .am-wrap').scrollTop = 120")
    rows = page.locator("table.all-moves tr")
    rows.nth(6).hover()
    rows.nth(7).hover()
    page.mouse.move(5, 5)
    assert page.evaluate("document.querySelector('#ask-answer .am-wrap').scrollTop") == 120
    import os
    if os.environ.get("CC_SHOTS"):
        page.screenshot(path=os.path.join(os.environ["CC_SHOTS"], "allmoves.png"))
    assert page.errors == []


def test_the_explorer_shows_lichess_masters(page, monkeypatch, tmp_path):
    from chesscoach import lichess
    config.settings.library_dir = str(tmp_path)
    config.settings.lichess_token = ""
    page.click("#tab-openings")
    page.select_option("#ex-source", "masters")
    # no token: says how to get one, and goes back to your games
    page.wait_for_function("document.querySelector('#toast').textContent.includes('token')")
    assert page.input_value("#ex-source") == ""
    config.settings.lichess_token = "t"
    body = {"white": 6000, "draws": 3000, "black": 1000, "opening": None, "moves": [
        {"uci": "e2e4", "san": "e4", "white": 3500, "draws": 1500, "black": 700, "averageRating": 2410},
        {"uci": "d2d4", "san": "d4", "white": 2500, "draws": 1500, "black": 300, "averageRating": 2420}]
        + [{"uci": u, "white": 5, "draws": 5, "black": 5} for u in               # all twenty
           ("g1f3 c2c4 g2g3 b2b3 f2f4 b1c3 b2b4 e2e3 a2a3 d2d3 c2c3 h2h3 g2g4 a2a4 h2h4 "
            "g1h3 b1a3 f2f3").split()]}
    monkeypatch.setattr(lichess, "_get", lambda url, tok: body)
    page.select_option("#ex-source", "masters")
    page.wait_for_function("document.querySelector('#ex-about').textContent.includes('Lichess masters')")
    first = page.inner_text("#ex-rows tr >> nth=0")
    assert first.startswith("e4") and "5,700" in first
    # twenty moves: they scroll inside the tab, which stays within the window
    assert page.locator("#ex-rows tr").count() == 20
    wrap = page.evaluate("""() => { const w = document.querySelector('.ex-table-wrap');
        return [w.scrollHeight > w.clientHeight, w.getBoundingClientRect().bottom, innerHeight]; }""")
    assert wrap[0] and wrap[1] <= wrap[2]
    assert page.is_hidden("#ex-mine") and page.is_hidden("#ex-file")   # for your own games only
    import os
    if os.environ.get("CC_SHOTS"):
        page.screenshot(path=os.path.join(os.environ["CC_SHOTS"], "lichess.png"))
    page.select_option("#ex-source", "")
    page.wait_for_function("!document.querySelector('#ex-mine').hidden")
    page.click("#tab-games")                                  # the Games tab is the list alone
    assert page.is_visible("#lib-list") and page.locator("#ex-rows").is_hidden()
    assert page.errors == []


def test_the_look_can_be_chosen_previewed_and_kept(page):
    assert page.get_attribute("html", "data-theme") == "auto"
    page.click("#open-settings")
    page.select_option("#theme-pick", "parchment")
    assert page.get_attribute("html", "data-theme") == "parchment"         # preview at once
    page.keyboard.press("Escape")                                          # cancel: back to the saved one
    page.wait_for_function("document.documentElement.dataset.theme === 'auto'")
    page.click("#open-settings")
    page.select_option("#theme-pick", "night")
    page.click("#save-settings")
    page.wait_for_function("document.documentElement.dataset.theme === 'night'")
    assert config.settings.theme == "night"
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(22, 28, 31)"                                         # the night page
    page.reload()                                                          # opens in it again
    page.wait_for_function("document.documentElement.dataset.theme === 'night'")
    assert page.errors == []


def test_openings_has_its_own_tab(page):
    page.click("#tab-openings")
    page.wait_for_selector("#panel-openings:not([hidden])")
    assert page.is_hidden("#panel-games") and page.inner_text("#panel-openings h1") == "Openings"
    page.wait_for_function("document.getElementById('ex-line').textContent === 'Starting position'")
    _click_square(page, "e2", "white")                         # a move on the board goes on from here
    _click_square(page, "e4", "white")
    page.wait_for_function("document.getElementById('ex-line').textContent === '1. e4'")
    page.click("#tab-review")
    page.click("#tab-openings")                                # and the line is still there
    page.wait_for_function("document.getElementById('ex-line').textContent === '1. e4'")
    assert page.errors == []


def test_the_game_summary_has_its_own_tab(page):
    page.click("#open-pgn")
    page.click("#start-review")
    page.wait_for_selector("#moments:not([hidden]) button", timeout=180000)
    page.click("#nav [data-nav=first]")
    assert "Summary" in page.inner_text("#coach") and page.locator("#coach .summary").count() == 0
    page.click("#view-summary")
    page.wait_for_selector("#summary-view:not([hidden]) .summary")
    assert "Game summary" in page.inner_text("#summary-view")
    assert page.is_hidden("#coach-scroll") and page.is_hidden("#splitter")
    link = page.locator("#summary-view a.ply").first
    label = link.inner_text()
    link.click()                                              # a move named there: shown with its comment
    page.wait_for_selector("#summary-view", state="hidden")
    assert page.is_visible("#coach-scroll")
    assert page.inner_text("#coach .move").startswith(label)
    page.click("#view-summary")
    page.click("#moments button >> nth=0")                    # a critical moment too
    page.wait_for_selector("#summary-view", state="hidden")
    assert page.errors == []


def test_import_your_games(page, monkeypatch, tmp_path):
    from chesscoach import online_games
    config.settings.library_dir = str(tmp_path)
    pgn = ('[Site "https://lichess.org/zz"]\n[Date "2026.10.30"]\n[White "youamer"]\n[Black "b"]\n'
           '[Result "1-0"]\n[TimeControl "600+0"]\n[Opening "Scholar\'s mate"]\n\n'
           '1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0\n\n'
           '[Site "https://lichess.org/yy"]\n[Date "2026.10.29"]\n[White "c"]\n[Black "youamer"]\n'
           '[Result "0-1"]\n[TimeControl "180+2"]\n\n1. f3 e5 2. g4 Qh4# 0-1\n')
    monkeypatch.setattr(online_games, "get_text", lambda url, tok="": pgn)
    page.click("#tab-games")
    page.click("#lib-import")
    page.wait_for_selector("#import-dialog[open]")
    assert page.input_value("#import-user") == "Homer"               # your name, until you change it
    page.fill("#import-user", "youamer")
    page.select_option("#import-period", "month")
    page.click("#import-go")                                          # step 1: find them
    page.wait_for_selector("#import-rows tr")
    assert page.locator("#import-rows tr").count() == 2
    assert "2 games found on Lichess, 2 new" in page.inner_text("#import-found")
    assert page.inner_text("#import-go") == "Import 2 games"
    rows = page.inner_text("#import-rows")
    assert "rapid" in rows and "blitz" in rows and "Scholar's mate" in rows
    page.fill("#import-filter", "blitz")                              # the filter
    assert page.locator("#import-rows tr").count() == 1
    page.fill("#import-filter", "")
    page.locator("#import-rows tr >> nth=1 >> input").uncheck()       # keep one only
    assert page.inner_text("#import-go") == "Import 1 game"
    import os
    if os.environ.get("CC_SHOTS"):
        page.screenshot(path=os.path.join(os.environ["CC_SHOTS"], "import.png"))
    page.click("#import-go")                                          # step 2: import the one picked
    page.wait_for_function("document.querySelector('#toast').textContent.includes('Added 1 game')")
    assert not page.is_visible("#import-dialog")
    page.wait_for_selector("#lib-rows tr")
    assert page.locator("#lib-rows tr").count() == 1 and page.input_value("#lib-file") == "Lichess.pgn"
    # again: it remembers the name, marks the game already there, and Review opens one
    page.click("#lib-import")
    assert page.input_value("#import-user") == "youamer"
    page.click("#import-go")
    page.wait_for_selector("#import-rows tr.have")
    assert "1 new" in page.inner_text("#import-found")
    page.click("#import-rows tr:not(.have) .rv")
    page.wait_for_selector("#panel-review:not([hidden])")
    assert page.inner_text("#game-title") == "c – youamer"
    assert page.input_value("#review-side") == "black"                 # your side, by your Lichess name
    page.click("#tab-games")
    page.click("#lib-import")
    page.fill("#import-user", "bad name")
    page.click("#import-go")
    page.wait_for_selector("#import-error:not([hidden])")
    assert page.errors == []


SPEECH_STUB = """
window.__said = [];
window.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
  speak: (u) => window.__said.push(u.text), cancel: () => window.__said.push('<cancel>'),
  getVoices: () => [{ name: 'Test voice', lang: 'en-US' }], onvoiceschanged: null } });
"""


def test_moves_are_said_in_words(page):
    cases = {
        "Nf3": "knight f3", "12...Nxe5+": "knight takes e5, check", "exd5": "e takes d5",
        "O-O": "castles kingside", "O-O-O#": "castles queenside, checkmate",
        "e8=Q+": "e8 promotes to queen, check", "Qxf7#": "queen takes f7, checkmate",
        "3. Bb5 a6": "bishop b5 a6", "♘c3": "knight c3",
        "The pawn on e4 is weak.": "The pawn on e4 is weak.",
        "**Better was**: Rd1": "Better was: rook d1",
    }
    for text, want in cases.items():
        assert page.evaluate("t => window.__spoken(t)", text) == want, text


def test_comments_are_read_aloud(page):
    page.add_init_script(SPEECH_STUB)
    page.reload()
    page.wait_for_timeout(400)
    page.evaluate("localStorage.removeItem('readComments')")
    page.click("#open-pgn")
    page.click("#start-review")
    page.wait_for_selector("#moments:not([hidden]) button", timeout=180000)
    page.click("#moments button >> nth=1")
    assert page.get_attribute("#speak-comments", "aria-pressed") == "false"
    page.wait_for_timeout(50)
    assert [s for s in page.evaluate("window.__said") if s != "<cancel>"] == []   # off: silent
    page.click("#speak-comments")                                # on: the comment here, at once
    assert page.get_attribute("#speak-comments", "aria-pressed") == "true"
    page.wait_for_function("window.__said.some((s) => s.includes('A reason.'))")
    said = [s for s in page.evaluate("window.__said") if s != "<cancel>"][-1]
    assert "Better was: Something else." in said
    assert "Assessment" not in said and "**" not in said
    assert not re.search(r"\b[KQRBN]x?[a-h][1-8]", said)           # moves in words
    # Stepping to another reviewed move reads its comment; clicking the same one again doesn't.
    page.evaluate("window.__said = []")
    page.click("#moments button >> nth=2")
    page.wait_for_function("window.__said.some((s) => s.includes('A reason.'))")
    page.evaluate("window.__said = []")
    page.click("#moments button >> nth=2")
    page.wait_for_timeout(100)
    assert [s for s in page.evaluate("window.__said") if s != "<cancel>"] == []
    # Off: speech stops, and the choice is remembered.
    page.click("#speak-comments")
    assert page.evaluate("window.__said").pop() == "<cancel>"
    assert page.evaluate("localStorage.getItem('readComments')") == "0"
    # The voice list offers the computer's voices.
    page.click("#open-settings")
    assert page.inner_text("#voice-pick") == "Test voice"
    assert page.errors == []
