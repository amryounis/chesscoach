"""Your games: PGN files with an index, autosave, saved reviews, explorer."""
import io

import chess
import chess.pgn
import pytest

from chesscoach import config, library
from conftest import DATA


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "settings", config.load_settings())
    config.settings.player_name = "Homer"
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "config.toml")
    return library.library_dir()


def _game(sans, white="Homer", black="Maia 1500", result="*"):
    g = chess.pgn.Game()
    g.headers.update(White=white, Black=black, Result=result, Date="2026.10.29")
    node = g
    for s in sans.split():
        node = node.add_variation(node.board().parse_san(s))
    return g


def test_add_query_load_and_explore(lib):
    name, n = library.add_file(str(DATA / "abasov_annotated.pgn"))
    assert (name, n) == ("abasov_annotated.pgn", 25)
    assert library.add_file(str(DATA / "abasov_annotated.pgn"))[0] == "abasov_annotated (2).pgn"
    q = library.query(text="Caruana")
    assert q["total"] == 2 and all("Caruana" in g["black"] for g in q["games"])
    game, f = library.load(q["games"][0]["id"])
    assert f.startswith("abasov") and "Caruana" in game.headers["Black"]
    ex = library.explore(chess.Board(), file="abasov_annotated.pgn")
    assert ex["games"] == 25 and ex["moves"][0]["games"] >= ex["moves"][-1]["games"]
    assert sum(m["games"] for m in ex["moves"]) == 25


def test_played_games_saved_once_and_replaced(lib):
    g = _game("e4 e5 Nf3")
    gid = library.save_played(g)
    g2 = _game("e4 e5 Nf3 Nc6 Bb5", result="1-0")
    library.save_played(g2, replaces=gid)                  # the same game, played on
    q = library.query(file=library.PLAYED)
    assert q["total"] == 1 and q["games"][0]["plies"] == 5 and q["games"][0]["result"] == "1-0"
    library.save_played(_game("d4 d5"))                     # another game
    assert library.query(file=library.PLAYED)["total"] == 2
    assert library.query(mine="white")["total"] == 2 and library.query(mine="black")["total"] == 0


def test_index_follows_files_edited_elsewhere(lib):
    library.save_played(_game("e4"))
    assert library.query()["total"] == 1
    with open(lib / library.PLAYED, "a", encoding="utf-8") as f:
        f.write(str(_game("c4", white="Someone")) + "\n\n")
    assert library.query()["total"] == 2
    (lib / "library.db").unlink()                           # the index is disposable
    assert library.query()["total"] == 2


def test_remove_rewrites_only_that_game(lib):
    for s in ("e4", "d4", "c4"):
        library.save_played(_game(s))
    q = library.query(sort="plies")
    target = next(g for g in q["games"] if g["gid"] == library.game_id(_game("d4")))
    library.remove_game(target["id"])
    left = chess.pgn.read_game(io.StringIO((lib / library.PLAYED).read_text()))
    assert library.query()["total"] == 2 and left is not None
    assert {g["gid"] for g in library.query()["games"]} == {
        library.game_id(_game("e4")), library.game_id(_game("c4"))}


def test_saved_review_round_trip(lib):
    from chesscoach.review import GameReview, MoveReview
    g = _game("e4 e5")
    b = chess.Board()
    moves = []
    for i, s in enumerate(("e4", "e5")):
        m = b.parse_san(s)
        moves.append(MoveReview(ply=i, move_label=s, san=s, uci=m.uci(), color="white" if b.turn else "black",
                                fen_before=b.fen(), fen_after="", label="Good", score_cp=20,
                                score_mate=None, verdict="equal", best_san=s, best_uci=m.uci(), book=False))
        b.push(m)
    r = GameReview(headers=dict(g.headers), side="white", models=["m"],
                   start_fen=chess.STARTING_FEN, moves=moves)
    library.save_review(r)
    library.save_review(r)                                   # a re-review replaces it
    saved = library.saved_review(g)
    assert saved["side"] == "white" and saved["reviewed_at"]
    assert library.query(file=library.REVIEWED)["total"] == 1
    assert library.query(reviewed=True)["total"] == 1


def test_explorer_scores_from_your_side(lib):
    library.save_played(_game("e4 e5", result="1-0"))
    library.save_played(_game("e4 c5", result="0-1"))
    library.save_played(_game("d4 d5", white="Maia", black="Homer", result="1/2-1/2"))
    ex = library.explore(chess.Board(), mine="white")
    assert [(m["san"], m["games"], m["score"]) for m in ex["moves"]] == [("e4", 2, 50)]
    b = chess.Board(); b.push_san("e4")
    ex = library.explore(b, mine="white")                    # Black to move, still your score
    assert {m["san"]: m["score"] for m in ex["moves"]} == {"e5": 100, "c5": 0}
    assert library.explore(chess.Board(), mine="black")["moves"][0]["san"] == "d4"
