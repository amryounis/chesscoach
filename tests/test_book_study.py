"""The opening study: an opening book walked once, every popular move's idea."""
import struct

import chess
import chess.polyglot
import pytest

from chesscoach import book_study as bs
from chesscoach import config


def write_book(path, lines):
    """A Polyglot book from {"e4 e5 Nf3": weight, ...}: every move of each
    line gets that weight (added up where lines share a move)."""
    w = {}
    for line, weight in lines.items():
        b = chess.Board()
        for san in line.split():
            mv = b.parse_san(san)
            key = (chess.polyglot.zobrist_hash(b), mv, b.is_castling(mv), b.turn)
            w[key] = w.get(key, 0) + weight
            b.push(mv)
    rows = []
    for (key, mv, castle, _), weight in w.items():
        frm, to = mv.from_square, mv.to_square
        if castle:
            to = {chess.G1: chess.H1, chess.C1: chess.A1, chess.G8: chess.H8, chess.C8: chess.A8}[to]
        promo = {None: 0, chess.KNIGHT: 1, chess.BISHOP: 2, chess.ROOK: 3, chess.QUEEN: 4}[mv.promotion]
        raw = (chess.square_file(to) | chess.square_rank(to) << 3 | chess.square_file(frm) << 6
               | chess.square_rank(frm) << 9 | promo << 12)
        rows.append((key, raw, min(weight, 65535), 0))
    rows.sort()
    with open(path, "wb") as f:
        for key, raw, weight, learn in rows:
            f.write(struct.pack(">QHHI", key, raw, weight, learn))
    return str(path)


LINES = {"e4 e5 Nf3 Nc6 Bb5 a6": 60, "e4 e5 Nf3 Nc6 Bc4 Bc5": 30, "e4 c5 Nf3 d6": 30,
         "d4 d5 c4 e6": 40, "d4 Nf6 c4 e6": 20, "e4 e5 Nf3 Nc6 Bb5 Nf6": 2,
         # a transposition: 1.Nf3 d5 2.d4 reaches 1.d4 d5 2.Nf3
         "Nf3 d5 d4": 15, "d4 d5 Nf3": 5}


def test_plan_follows_common_lines_once(tmp_path):
    book = write_book(tmp_path / "b.bin", LINES)
    p = bs.plan(book, plies=6, min_share=0.05, min_line=0.01, max_moves=4)
    start = p.positions[chess.Board().epd()]
    assert [m["san"] for m in start["moves"]] == ["e4", "d4", "Nf3"]
    assert abs(sum(m["share"] for m in start["moves"]) - 1) < 0.01
    # each position once, however it is reached
    b = chess.Board()
    for san in "d4 d5 Nf3".split():
        b.push_san(san)
    assert sum(1 for e in p.order if e == b.epd()) <= 1
    # 5...Nf6 has 2/62 of the Ruy positions: under 5%, not followed
    ruy = chess.Board()
    for san in "e4 e5 Nf3 Nc6 Bb5".split():
        ruy.push_san(san)
    assert [m["san"] for m in p.positions[ruy.epd()]["moves"]] == ["a6"]
    est = bs.estimate(p)
    assert est["moves"] == p.moves and est["by_ply"][0] == 3
    # a stricter line limit keeps fewer
    assert bs.plan(book, plies=6, min_line=0.2).moves < p.moves


def test_no_book_is_said_plainly(tmp_path):
    with pytest.raises(ValueError, match="No opening book"):
        bs.plan(str(tmp_path / "missing.bin"))


def test_rules_for_the_ideas():
    assert bs.text_issues("It prepares Nf3 and d4.", {"Nf3", "d4"}) == []
    assert bs.text_issues("Later Qh5 is strong.", {"Nf3"}) == ["names a move that is not in the facts: Qh5"]
    assert bs.text_issues("White is winning.", set()) == ["banned word used: 'winning'"]
    assert bs.text_issues("It scores 54% for White.", set())
    assert bs.tidy("**The idea**:\n- centre") == "The idea: centre"


def test_facts_for_a_move():
    b = chess.Board()
    for san in "e4 e5 Nf3 Nc6".split():
        b.push_san(san)
    mv = b.parse_san("Bb5")
    here = {"line": "1.e4 e5 2.Nf3 Nc6", "cp": 30, "mate": None, "best_san": "Bb5"}
    after = {"cp": 35, "mate": None}
    facts = bs.move_facts(b, mv, here, after, [{"san": "Bb5", "share": 0.6}, {"san": "Bc4", "share": 0.4,
                                                                          "name": "Italian Game"}],
                          [{"san": "a6", "share": 0.9, "name": "Ruy Lopez: Morphy Defense"}])
    text = " | ".join(facts)
    assert "Ruy Lopez" in text and "C60" in text
    assert "It clears the way for White to castle kingside" in text
    assert "The book's most played move here (60%" in text
    assert "3.Bc4 (Italian Game) 40%" in text and "3...a6 (Ruy Lopez: Morphy Defense) 90%" in text
    # development and the centre, from the board
    d = bs.development_facts(chess.Board(), chess.Board().parse_san("Nf3"))
    assert d[0] == "It develops the knight (its first move)" and "e5" in d[1]
    o = bs.development_facts(chess.Board(), chess.Board().parse_san("e4"))
    assert any("opens a diagonal for the bishop on f1" in f for f in o)
    assert any("opens a diagonal for the queen on d1" in f for f in o)


def test_a_false_claim_is_rewritten_or_removed():
    b = chess.Board()
    mv = b.parse_san("Nf3")
    after = b.copy(); after.push(mv)
    calls = []

    def llm(model, msgs, system=None):
        calls.append(msgs)
        return ("The knight develops towards the centre. The knight on e5 is attacked.", None)
    text, issues = bs.write_idea("m", b, mv, ["x"], [b, after], {"Nf3"}, llm=llm)
    assert len(calls) == 2                           # one rewrite asked for
    assert text == "The knight develops towards the centre."
    assert issues and issues[0].startswith("removed:")


@pytest.mark.engine
def test_study_run_is_resumable_and_read_by_the_explorer(tmp_path, stockfish, monkeypatch):
    monkeypatch.setattr(config, "settings",
                        config.load_settings(stockfish_path=stockfish, max_seconds=1))
    book = write_book(tmp_path / "b.bin", LINES)
    db = tmp_path / "openings.db"
    said = []

    def llm(model, msgs, system=None):
        said.append(msgs[0]["content"])
        return ("It develops and fights for the centre.", None)
    r1 = bs.run(book, "m", plies=2, min_line=0.01, depth=8, llm=llm, path=db)
    assert r1["written"] == r1["moves"] > 0
    r2 = bs.run(book, "m", plies=2, min_line=0.01, depth=8, llm=llm, path=db)
    assert r2["written"] == 0                        # kept, not written again
    st = bs.at(chess.Board(), db)
    assert [m["san"] for m in st["moves"]][:2] == ["e4", "d4"]
    assert st["moves"][0]["idea"] == "It develops and fights for the centre."
    assert st["moves"][0]["verdict"]                 # Stockfish after the move
    assert bs.idea(chess.Board(), chess.Move.from_uci("e2e4"), db).startswith("It develops")
    assert "Explain the idea behind 1.e4." in said[0]
    assert bs.summary(db)["ideas"] == r1["moves"]
