"""Phase 7a: annotated PGN games for the reader pane."""
import chess

from chesscoach.pgn_reader import game_list, game_tree, read_games, split_languages
from conftest import DATA


def _games():
    return read_games((DATA / "abasov_annotated.pgn").read_text(encoding="utf-8",
                                                                  errors="replace"))


def test_split_english_then_german():
    assert split_languages("This felt wrong to me. Das fühlte sich für mich falsch an.") == {
        "en": "This felt wrong to me.", "de": "Das fühlte sich für mich falsch an."}
    # The English part need not end with a full stop.
    out = split_languages("Thus, I decided to change the opening move and begin with In "
                          "meiner vorherigen Partie hatte ich mit 1.d4 ein Remis erreicht.")
    assert out["en"].endswith("begin with In") or out["en"].endswith("begin with")
    assert out["de"].startswith(("meiner", "In meiner"))
    # Short pairs the word lists cannot place, and English with names.
    assert split_languages("After Nach") == {"en": "After", "de": "Nach"}
    assert split_languages("The engines prefer Die Engines bevorzugen") == {
        "en": "The engines prefer", "de": "Die Engines bevorzugen"}
    assert split_languages("Caruana and Shankland played") == {
        "en": "Caruana and Shankland played"}
    assert split_languages("Strong move! White wins.") == {"en": "Strong move! White wins."}
    assert split_languages("Ein starker Zug. Weiß gewinnt.") == {
        "de": "Ein starker Zug. Weiß gewinnt."}


def test_collection_reads_and_splits_cleanly():
    games = _games()
    assert len(games) == 25
    lst = game_list(games)
    assert lst[15]["black"] == "Caruana, Fabiano" and all(g["annotated"] for g in lst)
    from chesscoach.pgn_reader import _lang_of
    comments = [c for g in games for n in game_tree(g)["nodes"]
                for c in (n["comment"], n["before"]) if c]
    german_in_english = [c for c in comments
                         if sum(_lang_of(w) > 0 for w in c.get("en", "").split()) >= 2]
    assert len(comments) > 1000 and len(german_in_english) <= 5


def test_tree_has_variations_symbols_arrows_and_evaluations():
    t = game_tree(_games()[0])
    nodes = t["nodes"]
    assert len(t["mainline"]) == 101 and t["annotated"]
    assert t["evp"][:3] == [80, -1, 103] and len(t["evp"]) == 102
    by_label = {(n["move_label"], n["main"]): n for n in nodes}
    a4 = by_label[("11. a4", True)]
    assert [g["glyph"] for g in a4["nags"]] == ["!"] and a4["nags"][0]["move"]
    assert a4["comment"]["en"] == "I waste no time to ask questions on the queenside."
    nbd7 = by_label[("7...Nbd7", False)]                  # a variation
    assert nbd7["depth"] == 1 and not nbd7["main"]
    assert chess.Board(nbd7["fen"]).piece_at(chess.D7).symbol() == "n"
    # The annotator's arrow after 11...bxa4 12.Rxa4: castling next (e1-g1).
    arrows = [a for n in nodes for a in n["arrows"]]
    assert {"from": "e1", "to": "g1", "color": "green"} in arrows
    # Position symbols are not move symbols.
    plus = [g for n in nodes for g in n["nags"] if g["glyph"] == "±"]
    assert plus and not plus[0]["move"] and plus[0]["words"] == "White is clearly better"
