"""Phase 5: the game summary and patterns, built only from stored facts."""

import json

import chess
import chess.engine
import pytest

from chesscoach import config, history
from chesscoach.engine import run_stockfish
from chesscoach.narrator import prepare_move
from chesscoach.summary import summarize, verify
from chesscoach.themes import move_themes


def _move(ply, label, color="black", win=(50, 40), themes=(), reason="", book=False,
          fen=chess.STARTING_FEN, move_label=None):
    return {"ply": ply, "move_label": move_label or f"{ply // 2 + 1}...x{ply}",
            "label": label, "color": color, "book": book, "reason": reason,
            "fen_before": fen, "uci": f"a{ply % 8 + 1}a{ply % 8 + 1}",
            "win_before": win[0], "win_after": win[1],
            "themes": [{"tag": t, "ply": ply, "move_label": "", "label": label,
                        "fact": f"fact {t} {ply}."} for t in themes]}


def _review(moves, side="black", headers=None):
    r = {"side": side, "moves": moves, "start_fen": chess.STARTING_FEN,
         "headers": headers or {"White": "Fritz", "Black": "Homer", "Date": "2026.10.02"}}
    r["summary"] = [c.to_dict() for c in summarize(r)]
    return r


def test_summary_claims_name_their_moves_and_trace_back():
    moves = [_move(1, "Good"),
             _move(3, "Inaccuracy", themes=["king_shelter", "threat_ignored"],
                   move_label="2...Bg4"),
             _move(5, "Mistake", win=(60, 30), themes=["king_shelter"], move_label="3...dxc4"),
             _move(7, "Best", reason="found the only good move", move_label="4...Rxf3"),
             _move(9, "Inaccuracy", themes=["king_shelter"], move_label="5...Kxf7"),
             _move(2, "Blunder", color="white", win=(80, 10))]   # not the reviewed side
    r = _review(moves)
    texts = [c["text"] for c in r["summary"]]
    assert texts[0] == "You made two inaccuracies, one mistake and no blunders in five moves."
    assert ("The biggest swing was 3...dxc4 (a mistake): your winning chance went "
            "from 60% to 30%.") in texts
    assert ("Three of your errors weakened your king's pawn shelter: 2...Bg4, 3...dxc4 "
            "and 5...Kxf7.") in texts
    assert "2...Bg4 did not deal with the opponent's threat." in texts
    assert "You found the only good move with 4...Rxf3." in texts
    shelter = next(c for c in r["summary"] if c["tag"] == "king_shelter")
    assert shelter["facts"] == ["2...Bg4: fact king_shelter 3.", "3...dxc4: fact king_shelter 5.",
                                "5...Kxf7: fact king_shelter 9."]
    assert verify(r) == []                       # the phase 5 gate


def test_verify_catches_a_claim_without_a_stored_fact():
    r = _review([_move(3, "Inaccuracy", themes=["king_shelter"], move_label="2...Bg4"),
                 _move(5, "Inaccuracy", move_label="3...dxc4")])
    r["summary"].append({"text": "Two of your errors weakened your king's pawn shelter: "
                         "2...Bg4 and 3...dxc4.", "kind": "theme", "color": "black",
                         "plies": [3, 5], "facts": [], "tag": "king_shelter"})
    assert verify(r) == ["Two of your errors weakened your king's pawn shelter: 2...Bg4 "
                         "and 3...dxc4. — 3...dxc4 has no 'king_shelter' fact"]
    r["summary"][0]["text"] = r["summary"][0]["text"].replace("two inaccuracies", "one inaccuracy")
    assert any("should say two inaccuracies" in p for p in verify(r))


def test_both_sides_are_named_by_colour():
    r = _review([_move(3, "Mistake", move_label="2...Bg4", themes=["material"]),
                 _move(2, "Mistake", color="white", move_label="2. e3",
                       themes=["material"])], side="both")
    texts = [c["text"] for c in r["summary"]]
    assert "White made no inaccuracies, one mistake and no blunders in one move." in texts
    assert ("2...Bg4 ended up with less material than the best move would have."
            in texts) and verify(r) == []


def test_patterns_across_games(tmp_path):
    g1 = _review([_move(3, "Inaccuracy", themes=["king_shelter"], move_label="8...Bg4"),
                  _move(5, "Mistake", themes=["left_loose"], move_label="9...dxc4")])
    g2 = _review([_move(3, "Blunder", themes=["king_shelter"], move_label="12...Kxf7")],
                 side="both", headers={"White": "Homer", "Black": "Maia 1500",
                                       "Date": "2026.10.05"})
    g2["moves"][0]["color"] = "white"
    g2["moves"][0]["uci"] = "e2e4"
    g3 = _review([_move(3, "Good")], side="both",
                 headers={"White": "A", "Black": "B", "Date": "2026.10.06"})
    assert history.save(g1, folder=tmp_path)
    assert history.save(g2, "homer", folder=tmp_path)      # both sides: your name decides
    assert history.save(g3, "homer", folder=tmp_path) is None   # not your game
    recs = history.load(tmp_path)
    assert [r["color"] for r in recs] == ["black", "white"]
    claims = history.patterns(recs)
    texts = [c.text for c in claims]
    assert texts[0] == ("In your last two reviewed games: 3 errors (1 inaccuracy, "
                        "1 mistake, 1 blunder).")
    assert history.patterns(recs[:1])[0].text == (
        "In your one reviewed game: 2 errors (1 inaccuracy, 1 mistake, 0 blunders).")
    assert texts[1].startswith("In two of your last two games, a move weakened your "
                               "king's pawn shelter (2 moves in all): vs Fritz")
    assert not any("hanging" in t for t in texts)          # once is not a pattern
    assert history.verify_patterns(recs, claims) == []
    # Re-reviewing a game replaces its record instead of counting it twice.
    history.save(g1, folder=tmp_path)
    assert len(history.load(tmp_path)) == 2
    # Records keep what Practice needs: the move, the engine's move, the comment.
    for rec in recs:
        for m in rec["moves"]:
            assert {"san", "uci", "best_san", "best_uci", "color"} <= set(m)
            assert ("explanation" in m) == (m["label"] in ("Inaccuracy", "Mistake", "Blunder"))


@pytest.mark.engine
def test_homer_game_errors_share_king_shelter(homer_game, stockfish):
    """The design doc's example: the three inaccuracies in Homer vs Fritz
    (8...Bg4, 9...dxc4, 12...Kxf7) all gave up king shelter."""
    config.settings = config.load_settings(stockfish_path=stockfish)
    boards, moves = homer_game
    sf = chess.engine.SimpleEngine.popen_uci(stockfish)
    try:
        found = {}
        for i, name in ((15, "8...Bg4"), (17, "9...dxc4"), (23, "12...Kxf7")):
            eb = run_stockfish(boards[i], depth=18, engine=sf)
            ea = run_stockfish(boards[i + 1], depth=18, engine=sf)
            prep = prepare_move(boards[i], moves[i], eb, ea, None, engine=sf)
            if prep["label"] != "Inaccuracy":
                pytest.skip(f"{name} is a {prep['label']} on this machine")
            found[name] = [t.tag for t in move_themes(
                i, name, prep["label"], boards[i], moves[i], eb.best_move,
                prep["concepts"], prep["diffs"])]
    finally:
        sf.quit()
    assert all("king_shelter" in tags for tags in found.values()), found


@pytest.mark.engine
def test_blunders_name_their_cause(stockfish):
    """Amr's games, 14 Oct: 6...Bxd4 and 55.b7 were summarised as 'lost
    material', an outcome. The cause is that the reply takes the piece."""
    from chesscoach.features import line_diff
    from chesscoach.assessment import win_pct
    config.settings = config.load_settings(stockfish_path=stockfish)
    sf = chess.engine.SimpleEngine.popen_uci(stockfish)
    try:
        for fen, san, color in (
                ("r1bqk2r/pppp1ppp/2n2n2/2b5/2BPP3/5N2/PP3PPP/RNBQK2R b KQkq - 0 6",
                 "Bxd4", chess.BLACK),
                ("1R6/8/1P3p1k/5P1P/7K/1r6/8/8 w - - 2 55", "b7", chess.WHITE)):
            b = chess.Board(fen)
            m = b.parse_san(san)
            a = b.copy()
            a.push(m)
            eb, ea = run_stockfish(b, engine=sf), run_stockfish(a, engine=sf)
            tags = [t.tag for t in move_themes(
                0, san, "Blunder", b, m, eb.best_move, None, line_diff(b, m, eb, ea), ea,
                win_pct(eb.score_cp, eb.score_mate, color == chess.WHITE))]
            assert "into_capture" in tags and "material" not in tags, (san, tags)
            if san == "b7":
                assert "allowed_draw" in tags
    finally:
        sf.quit()


def test_accuracy_and_mates_are_counted_from_stored_moves():
    moves = [_move(1, "Excellent", move_label="1...e5"), _move(3, "Good", move_label="2...Nc6"),
             _move(5, "Blunder", win=(90, 0), move_label="3...Qh4")]
    moves[0].update(san="e5", best_san="e5")
    moves[1].update(san="Nc6", best_san="Nf6")
    moves[2].update(san="Qh4", best_san="Qxf2#", mate_before=1, mate_after=-2)
    r = _review(moves)
    texts = [c["text"] for c in r["summary"]]
    assert ("You played the engine's first choice in 1 of 3 moves (33%); on average a move "
            "cost 36.7 points of winning chance.") in texts
    assert "You missed a forced mate with 3...Qh4." in texts
    assert "3...Qh4 allowed a forced mate." in texts
    assert verify(r) == []


def test_bad_history_records_cost_only_their_bad_parts(tmp_path):
    """Review feedback: one old or corrupted record raised in patterns() and
    the finished review was lost."""
    import json
    good = {"id": "g1", "color": "black", "white": "A", "black": "Me", "date": "2026.10.01",
            "moves": [{"ply": 15, "move_label": "8...Bg4", "label": "Mistake",
                       "fen_before": chess.STARTING_FEN,
                       "themes": [{"tag": "king_shelter", "fact": "x"},
                                  {"tag": "no-such-tag", "fact": "y"}, "junk"]},
                      {"ply": 17, "label": "Blunder"},              # no move_label
                      "junk"]}
    (tmp_path / "g1.json").write_text(json.dumps(good))
    (tmp_path / "bad1.json").write_text("{not json")
    (tmp_path / "bad2.json").write_text(json.dumps({"id": "x", "moves": "nope"}))
    (tmp_path / "bad3.json").write_text(json.dumps([1, 2, 3]))
    recs = history.load(tmp_path)
    assert [r["id"] for r in recs] == ["g1"]
    assert [m["move_label"] for m in recs[0]["moves"]] == ["8...Bg4"]
    assert [t["tag"] for t in recs[0]["moves"][0]["themes"]] == ["king_shelter"]
    history.patterns(recs)                                    # does not raise


def test_history_writes_are_atomic_and_versioned(tmp_path):
    from types import SimpleNamespace
    mv = SimpleNamespace(ply=1, move_label="1...e5", color="black", san="e5", uci="e7e5",
                         label="Good", fen_before=chess.STARTING_FEN, best_san="e5",
                         best_uci="e7e5", win_before=50, win_after=50, themes=[],
                         book=False, annotations={})
    rv = SimpleNamespace(side="black", headers={}, start_fen=chess.STARTING_FEN, moves=[mv])
    path = history.save(rv, folder=tmp_path)
    assert json.loads(path.read_text())["v"] == history.SCHEMA
    assert not list(tmp_path.glob("*.tmp"))


def test_tactics_are_counted_across_games(tmp_path):
    def rec(gid, tags):
        return {"id": gid, "color": "white", "white": "Me", "black": "X", "date": f"2026.10.0{gid[-1]}",
                "moves": [{"ply": 10, "move_label": "6. Nf3", "label": "Mistake",
                           "fen_before": chess.STARTING_FEN, "themes": [],
                           "tactics": {"allowed": tags, "missed": []}}]}
    for r in (rec("g1", ["fork"]), rec("g2", ["fork", "pin"]), rec("g3", ["pin", "nonsense"])):
        (tmp_path / f"{r['id']}.json").write_text(json.dumps(r))
    recs = history.load(tmp_path)
    assert recs[2]["moves"][0]["tactics"]["allowed"] == ["pin"]           # unknown tag dropped
    claims = history.patterns(recs)
    tac = [c for c in claims if c.kind == "pattern-tactics"]
    assert len(tac) == 1 and tac[0].text == (
        "Across your last three games, your errors allowed a fork 2 times in two games "
        "and a pin 2 times in two games.")
    assert history.verify_patterns(recs, claims) == []


def test_missed_tactics_read_as_a_sentence():
    """Review feedback: 'Instead of your errors, the engine had a fork (3. Qh5).'"""
    from chesscoach.summary import summarize
    mv = {"ply": 4, "move_label": "3. Qh5", "color": "white", "label": "Mistake", "book": False,
          "san": "Qh5", "best_san": "Nf3", "fen_before": chess.STARTING_FEN, "win_before": 50,
          "win_after": 30, "themes": [], "tactics": {"missed": ["fork"]}, "reason": "mistake"}
    texts = [c.text for c in summarize({"side": "white", "moves": [mv]}) if c.kind == "tactics"]
    assert texts == ["You missed a fork (3. Qh5)."]
    both = [c.text for c in summarize({"side": "both", "moves": [mv]}) if c.kind == "tactics"]
    assert both == ["White missed a fork (3. Qh5)."]
