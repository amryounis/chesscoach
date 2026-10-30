"""The grounding check, on claims models actually made."""
import re

import chess
import pytest

from chesscoach.evaluate import load_keys
from chesscoach.grounding import (_relatively_pinned, check_sentence, ground,
                                  remove_failed, split_sentences)
from conftest import DATA

ROOK_CHECK = "2b1k2r/1r4pp/1pp5/p7/2P2B2/3B1N2/1P1K1PPP/R6R w k - 1 18"


def boards_for(fen, *sans):
    b = chess.Board(fen)
    out = [b.copy()]
    for san in sans:
        b.push_san(san)
        out.append(b.copy())
    return out


def kinds(sentence, boards, move=None):
    return [f.kind for f in check_sentence(sentence, boards, move)]


# --- Claims from the October 2026 model comparison -----------------------

def test_rook_on_e1_does_not_attack_c8():
    bs = boards_for(ROOK_CHECK, "Rhe1+")
    assert kinds("The rook on e1 attacks the bishop on c8.", bs) == ["attack"]
    assert kinds("The rook on e1 attacks the king on e8.", bs) == []


def test_knight_on_c6_fork_of_b7_is_false():
    bs = boards_for(ROOK_CHECK, "Rae1+", "Kd8", "Ne5", "Rf8", "Nxc6+")
    assert kinds("The knight on c6 forks the rook on b7 and the king.", bs) == ["fork"]


def test_material_equal_is_false_two_pieces_down():
    bs = boards_for(ROOK_CHECK)
    assert kinds("Material is equal.", bs) == ["material"]
    assert kinds("White is up two pieces.", bs) == []
    assert kinds("Black is ahead in material.", bs) == ["material"]
    assert kinds("Black is up a pawn.", bs) == ["material"]


def test_six_legal_replies_and_only_one_reply():
    bs = boards_for(ROOK_CHECK, "Rhe1+")
    assert kinds("Black has six legal replies.", bs) == []
    assert kinds("Black has 7 legal moves.", bs) == ["legal"]
    assert kinds("Black has only one legal move.", bs) == ["legal"]


def test_invented_pieces():
    bs = boards_for(ROOK_CHECK)
    assert kinds("White's bishop on c4 controls the long diagonal.", bs) == ["placement"]
    assert kinds("The bishop on d3 eyes h7.", bs) == []


# --- The baseline run, 5 October 2026 -------------------------------------

def test_king_on_f7_still_defends_e7():
    # qwen3 in the pipeline: "Moving the king to f7 removes its protection
    # from the pawn on e7" — the king on f7 is next to e7.
    key = load_keys(ids=["homer-12-kxf7"])[0]
    b = key.board()
    mv = key.played()
    a = b.copy()
    a.push(mv)
    s = ("Moving the king to f7 removes its protection from the pawn on e7 "
         "and leaves it completely exposed.")
    assert kinds(s, [b, a], mv) == ["defence"]


def test_no_false_alarms_on_the_clean_pipeline_texts():
    # The four pipeline annotations without the e7 error must pass (with
    # the engine line positions the pipeline also passes, e.g. e7 for 9.Nxd4).
    report = (DATA / "baseline" / "qwen3_14b_2026-10-05.md").read_text(encoding="utf-8")
    keys = {k.title: k for k in load_keys()}
    checked = 0
    for sec in re.split(r"\n## (?=\d)", report)[1:]:
        title, rest = sec.split("\n", 1)
        kt, mode = title.rsplit(" — ", 1)
        if not mode.startswith("pipeline") or "kxf7" in keys[kt].id:
            continue
        k = keys[kt]
        text = re.split(r"\n(?:Missed|Board errors|False claims):", rest)[0]
        b = k.board()
        a = b.copy()
        a.push(k.played())
        extra = []
        if k.id == "test-9-nxd4":           # the engine's line exf6 dxe3 fxe7
            extra = boards_for(k.fen, "exf6", "dxe3", "fxe7")
        assert ground(text, [b, a] + extra, k.played()) == [], k.id
        checked += 1
    assert checked == 4


def test_raw_texts_are_caught():
    report = (DATA / "baseline" / "qwen3_14b_2026-10-05.md").read_text(encoding="utf-8")
    sec = report[report.index("## 9.Nxd4"):]
    sec = sec[sec.index("raw · qwen3"):]
    k = load_keys(ids=["test-9-nxd4"])[0]
    fails = ground(sec, [k.board()])
    found = {f.kind for f in fails}
    assert {"placement", "pin"} <= found       # bishop on b5 pinning a knight on d7


# --- Pins, defences, removal ----------------------------------------------

def test_pins_absolute_and_relative():
    # 8...Bg4 position: the e2 pawn stands between f3 and d1, so no pin.
    b = chess.Board("rn1qk2r/pp2pp2/2p2bpp/3p3P/2PP2b1/2N2N2/PP2PPP1/R2QKB1R w KQkq - 1 9")
    assert kinds("The bishop pins the knight on f3.", [b]) == ["pin"]
    b.remove_piece_at(chess.E2)
    assert _relatively_pinned(b, chess.F3)                  # pinned to the queen
    assert kinds("The bishop pins the knight on f3.", [b]) == []


def test_defence_claims():
    b = chess.Board(ROOK_CHECK)
    assert kinds("The pawn on b6 defends the pawn on a5.", [b]) == []
    assert kinds("The rook on b7 defends the pawn on a5.", [b]) == ["defence"]
    assert kinds("The bishop on c8 is undefended.", [b]) == []
    assert kinds("The pawn on a5 is undefended.", [b]) == ["undefended"]


def test_negated_and_passive_claims_are_not_misread():
    b = chess.Board(ROOK_CHECK)
    assert kinds("The rook on h8 no longer defends the pawn on h7.", [b]) == []
    assert kinds("The pawn on a5 is attacked by the rook on a1.", [b]) == []


def test_removal_keeps_headings_and_other_sentences():
    text = ("**Assessment**: Inaccuracy\n"
            "**What happened**: Black takes on f7. Material is equal. The king is exposed.\n"
            "**Better was**: Material is equal.\n"
            "**What now**: White checks.")
    fails = ground(text, [chess.Board(ROOK_CHECK)])
    out = remove_failed(text, fails)
    assert out == ("**Assessment**: Inaccuracy\n"
                   "**What happened**: Black takes on f7. The king is exposed.\n"
                   "**What now**: White checks.")


def test_bold_text_is_not_a_heading():
    parts = split_sentences("- **White has a pawn on e5**, which is weak.")
    assert parts[0][1] == ""


@pytest.mark.parametrize("text", [
    "The knight on f3 is strong.",          # true placement
    "White has a big advantage.",           # no checkable claim
    "Two moves later White wins the pawn.",  # 'two moves' is not a legal count
])
def test_true_or_unparsed_claims_pass(text):
    assert ground(text, [chess.Board(ROOK_CHECK)]) == []


# --- Every pipeline annotation from the 5 October runs (needs Stockfish) ---

# 0.16.1: "9...dxc4 weakens Black's king position by reducing the number of
# shield pawns" is said of the move itself, and dxc4 leaves the e8 king's
# pawns as they were (the review that pointed it out: DeepSeek, 0.15.0).
DXC4_SHELTER = ("shelter", "weakens Black's king position")
EXPECTED = {
    # file suffix -> key id -> failed claims; every other text must pass
    "": {"homer-12-kxf7": [("defence", "removes its protection from the pawn on e7"),
                           ("bishop pair", "keeps the bishop pair")],
         "homer-9-dxc4": [DXC4_SHELTER]},
    "_grounded": {
        "homer-12-kxf7": [("passed pawn", "passed pawn on the f-file"),
                          ("passed pawn", "passed pawn")],
        "test-9-nxd4": [("threat", "removes the threat to the knight on c3")],
        "homer-9-dxc4": [DXC4_SHELTER]},
    "_unchecked": {"homer-12-kxf7": [("defence", "removes its protection from the pawn on e7"),
                                     ("bishop pair", "keeps the bishop pair")],
                   # 0.13 file check: after 9.hxg6 fxg6 the g-file keeps two pawns
                   "homer-8-bg4": [("file", "opens up the g-file")],
                   "homer-9-dxc4": [DXC4_SHELTER]},
    "_grounded2": {"homer-9-dxc4": [DXC4_SHELTER, ("file", "open the g-file")]},
    "qwen3_14b_2026-10-10_phase3b": {
        "homer-9-dxc4": [("defence", "protected the knight on f3")]},
    "qwen3_14b_2026-10-08_phase3": {
        "test-9-nxd4": [("keeps", "preserves White's pawn on e5")]},
}

# Claims whose truth depends on the engine's line, which differs between
# Stockfish builds: after 9.Nxd4 Nxe5 f4 the knight goes to c6 here and
# (judging by the annotation) to g6 on the machine that ran the model.
ENGINE_DEPENDENT = {("placement", "Black's knight on g6")}


@pytest.mark.engine
@pytest.mark.parametrize("suffix", list(EXPECTED))
def test_all_baseline_annotations(suffix, stockfish):
    from chesscoach import config
    from chesscoach.evaluate import Engines, prepared
    config.settings = config.load_settings(stockfish_path=stockfish)
    name = suffix if suffix.startswith("qwen") else f"qwen3_14b_2026-10-05{suffix}"
    report = (DATA / "baseline" / f"{name}.md").read_text(encoding="utf-8")
    keys = {k.title: k for k in load_keys()}
    seen = 0
    with Engines(stockfish) as eng:
        for sec in re.split(r"\n## (?=\d)", report)[1:]:
            title, rest = sec.split("\n", 1)
            kt, mode = title.rsplit(" — ", 1)
            if not mode.startswith("pipeline"):
                continue
            k = keys[kt]
            p = prepared(k, eng)
            text = re.split(r"\n(?:Missed|Board errors|False claims|Caught|Removed)", rest)[0]
            got = [(f.kind, f.claim) for f in ground(
                text, p["boards"], p["move"], keep=p["fixed"],
                played=p["played_boards"], better=p["better_boards"])
                   if (f.kind, f.claim) not in ENGINE_DEPENDENT]
            assert got == EXPECTED[suffix].get(k.id, []), k.id
            seen += 1
    assert seen == 5


def test_consequences_are_checked_on_the_right_line():
    # 9.Nxd4 does remove the threat to c3; 9.exf6 would not.
    b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3pP3/3p4/2N1BN2/PPP1BPPP/R2Q1RK1 w - - 0 9")
    mv = b.parse_san("Nxd4")
    played = boards_for(b.fen(), "Nxd4")[1:]
    better = boards_for(b.fen(), "exf6")[1:]
    text = ("**What happened**: It removes the threat to the knight on c3.\n"
            "**Better was**: Taking on f6, which removes the threat to the knight on c3.")
    fails = ground(text, [b] + played + better, mv, played=played, better=better)
    assert [f.sentence for f in fails] == [
        "Taking on f6, which removes the threat to the knight on c3."]


def test_words_ending_in_a_piece_name_are_not_pieces():
    b = chess.Board(ROOK_CHECK)
    for s in ("Taking on f6 was better.", "Attacking on e5 fails.",
              "Checking on b5 wins time."):
        assert ground(s, [b]) == [], s


@pytest.mark.engine
def test_full_review_of_the_fritz_game(stockfish):
    # The seven comments from the 5 October review: the only false claim
    # is 12...Kxf7 "creating doubled pawns on the c-file" (Black's c-pawns,
    # doubled before the move), in 'What happened' and 'Better was'.
    import chess.pgn
    from chesscoach import config
    from chesscoach.evaluate import Engines
    from chesscoach.narrator import prepare_move
    config.settings = config.load_settings(stockfish_path=stockfish)
    with open(DATA / "homer_vs_fritz.pgn", encoding="utf-8") as fh:
        game = chess.pgn.read_game(fh)
    moves = list(game.mainline_moves())
    boards = [game.board()]
    for m in moves:
        nb = boards[-1].copy()
        nb.push(m)
        boards.append(nb)
    label = {}
    for i, b in enumerate(boards[:-1]):
        n = b.fullmove_number
        label[(f"{n}." if b.turn else f"{n}...") + b.san(moves[i])] = i
    report = (DATA / "baseline" / "qwen3_14b_2026-10-05_review.md").read_text(encoding="utf-8")
    found = {}
    with Engines(stockfish) as eng:
        for sec in re.split(r"\n### ", report)[1:]:
            title, rest = sec.split("\n", 1)
            i = label[title.split(" — ")[0].replace(". ", ".")]
            p = prepare_move(boards[i], moves[i], eng.analyse(boards[i]),
                             eng.analyse(boards[i + 1]), None)
            text = re.sub(r"\*\*(Assessment|What happened|Better was|What now)\*\*\s+",
                          r"**\1**: ", rest.split("\n> ")[0])
            fs = ground(text, p["boards"], p["move"], keep=p["fixed"],
                        played=p["played_boards"], better=p["better_boards"])
            if fs:
                found[title.split(" — ")[0]] = sorted({f.kind for f in fs})
    # 0.16.1: 8...Bg4 "opening up the f-file" is said of the bishop move;
    # the f-file opens only after 9.hxg6 fxg6.
    assert found == {"12...Kxf7": ["doubled pawn"], "8...Bg4": ["file"]}


def test_structure_claims():
    # Black's c-pawns are doubled before and after 12...Kxf7.
    b = chess.Board("rn1qk2r/p3pP2/2p2b1p/8/2pP2b1/2N1PN2/PP3PP1/R2QK2R b KQkq - 0 12")
    mv = b.parse_san("Kxf7")
    played = boards_for(b.fen(), "Kxf7")[1:]
    better = boards_for(b.fen(), "Kf8")[1:]
    text = ("**What happened**: It allows White a structural edge by creating "
            "doubled pawns on the c-file.\n"
            "**Better was**: The king to f8, which avoids creating doubled pawns on the c-file.")
    kinds_ = [f.kind for f in ground(text, [b] + played + better, mv,
                                     played=played, better=better)]
    assert kinds_ == ["doubled pawn", "doubled pawn"]


def test_continuation_must_follow_the_engine():
    b = chess.Board(ROOK_CHECK)
    played = boards_for(ROOK_CHECK, "Rhe1+", "Kd8", "Ne5")
    mv = b.parse_san("Rhe1+")
    ok = "**What now**: Black will move the king to d8. White then moves the knight to e5."
    bad = "**What now**: Black will move the king to f7."
    assert ground(ok, [b] + played, mv, played=played) == []
    assert [f.kind for f in ground(bad, [b] + played, mv, played=played)] == ["continuation"]


def test_preserves_claims_are_checked_after_that_move():
    b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3pP3/3p4/2N1BN2/PPP1BPPP/R2Q1RK1 w - - 0 9")
    mv = b.parse_san("Nxd4")
    played = boards_for(b.fen(), "Nxd4")[1:]
    better = boards_for(b.fen(), "exf6")[1:]
    bad = "**Better was**: Taking on f6, which preserves White's pawn on e5."
    good = "**Better was**: Taking on f6, which keeps the bishop on e3."
    assert [f.kind for f in ground(bad, [b] + played + better, mv,
                                   played=played, better=better)] == ["keeps"]
    assert ground(good, [b] + played + better, mv, played=played, better=better) == []


def test_curly_apostrophes_are_read_and_removed():
    # "Black’s" with a typographic apostrophe (qwen3 writes both kinds).
    b = chess.Board("rn1qk2r/pp2pp2/2p2bpp/3p3P/2PP2b1/2N1PN2/PP3PP1/R2QKB1R b KQkq - 0 9")
    mv = b.parse_san("dxc4")
    a = b.copy()
    a.push(mv)
    ok = "**What happened**: The move left Black’s pawn on c4 undefended."
    assert ground(ok, [b, a], mv, played=[a]) == []
    bad = "**What happened**: Material is equal. It’s a pity."
    fails = ground(bad, [chess.Board(ROOK_CHECK)])
    assert remove_failed(bad, fails) == "**What happened**: It’s a pity."


def test_leaves_undefended_is_judged_right_after_the_move():
    # 9...dxc4 does not leave g6 undefended: the f7 pawn still guards it.
    # (Further along the line a different Black pawn stands on g6.)
    b = chess.Board("rn1qk2r/pp2pp2/2p2bpp/3p3P/2PP2b1/2N1PN2/PP3PP1/R2QKB1R b KQkq - 0 9")
    mv = b.parse_san("dxc4")
    line = boards_for(b.fen(), "dxc4", "hxg6", "fxg6")
    text = "**What happened**: By capturing on c4, Black leaves the pawn on g6 undefended."
    assert [f.kind for f in ground(text, [b] + line[1:], mv, played=line[1:])] == ["undefended"]


def test_bishop_pair_claims():
    b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3pP3/3p4/2N1BN2/PPP1BPPP/R2Q1RK1 w - - 0 9")
    mv = b.parse_san("Nxd4")
    better = boards_for(b.fen(), "exf6", "dxe3", "fxe7", "exf2+")[1:]
    played = boards_for(b.fen(), "Nxd4")[1:]
    text = "**Better was**: Capturing on f6 with the pawn, preserving the bishop pair."
    assert [f.kind for f in ground(text, [b] + played + better, mv, played=played,
                                   better=better)] == ["bishop pair"]


FIVE_RUNS = {
    # 25 annotations each (five per key). Reading them found false claims
    # the check then missed; it must now flag exactly those and nothing else.
    # 0.16.1: "8...Bg4 weakens/exposes Black's king by reducing the shield
    # pawns": said of the bishop move, which moves no pawn.
    "qwen3_14b_2026-10-10_phase3c": {
        ("homer-8-bg4", 1): [("shelter", "exposes Black's king")],
        ("homer-8-bg4", 4): [("shelter", "weakens Black's king position")],
        ("homer-8-bg4", 5): [("shelter", "weakens Black's king position")],
        ("homer-9-dxc4", 5): [("undefended", "leaves the pawn on g6 undefended")],
        ("homer-12-kxf7", 4): [("doubled pawn", "doubled pawns on the c-file")],
        ("test-9-nxd4", 2): [("bishop pair", "preserving the bishop pair")]},
    "qwen3_14b_2026-10-11_phase3d": {
        ("homer-8-bg4", 1): [("shelter", "exposes Black's king")],
        ("homer-8-bg4", 2): [("shelter", "weakens Black's king position")],
        ("homer-8-bg4", 3): [("shelter", "weakens Black's king position")],
        ("homer-8-bg4", 4): [("shelter", "weakens Black's king position")],
        ("homer-8-bg4", 5): [("shelter", "weakens Black's king position")],
        ("homer-12-kxf7", 2): [("bishop pair", "maintain the bishop pair")],
        ("homer-12-kxf7", 3): [("bishop pair", "keeps the bishop pair")],
        ("test-9-nxd4", 3): [("captured", "Black's pawn on d4")]},
}


@pytest.mark.engine
@pytest.mark.parametrize("name", list(FIVE_RUNS))
def test_five_runs_per_key(stockfish, name):
    from chesscoach import config
    from chesscoach.evaluate import Engines, prepared
    config.settings = config.load_settings(stockfish_path=stockfish)
    expected = FIVE_RUNS[name]
    report = (DATA / "baseline" / f"{name}.md").read_text(encoding="utf-8")
    keys = {k.title: k for k in load_keys()}
    count, got = {}, {}
    with Engines(stockfish) as eng:
        for sec in re.split(r"\n## (?=\d)", report)[1:]:
            title, rest = sec.split("\n", 1)
            k = keys[title.rsplit(" — ", 1)[0]]
            count[k.id] = count.get(k.id, 0) + 1
            p = prepared(k, eng)
            text = re.split(r"\n(?:Missed|Board errors|False claims|Caught|Removed)", rest)[0]
            fs = [(f.kind, f.claim) for f in ground(
                text, p["boards"], p["move"], keep=p["fixed"],
                played=p["played_boards"], better=p["better_boards"])
                  if (f.kind, f.claim) not in ENGINE_DEPENDENT]
            if fs:
                got[(k.id, count[k.id])] = fs
    assert sum(count.values()) == 25
    assert got == expected


def test_captured_piece_spoken_of_as_still_in_play():
    b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3pP3/3p4/2N1BN2/PPP1BPPP/R2Q1RK1 w - - 0 9")
    mv = b.parse_san("Nxd4")
    after = boards_for(b.fen(), "Nxd4")[1:]
    bad = ("**What happened**: It fails to address the danger posed by Black's pawn on "
           "d4, which now has a clear path to attack White's pieces.")
    ok = "**What happened**: White captures the pawn on d4, which was attacking c3 and e3."
    past = ("**What happened**: It captures the pawn on d4, which was left undefended "
            "and still attacking c3.")
    assert ground(past, [b] + after, mv, played=after) == []
    assert [f.kind for f in ground(bad, [b] + after, mv, played=after)] == ["captured"]
    assert ground(ok, [b] + after, mv, played=after) == []


def test_bishop_pair_claims_in_what_now():
    # After 12...Kxf7 Ne5+ Bxe5 Qxg4 Black no longer has two bishops.
    b = chess.Board("rn1qk2r/p3pP2/2p2b1p/8/2pP2b1/2N1PN2/PP3PP1/R2QK2R b KQkq - 0 12")
    mv = b.parse_san("Kxf7")
    played = boards_for(b.fen(), "Kxf7", "Ne5+", "Bxe5", "Qxg4")[1:]
    text = ("**What now**: White will move the knight to e5, giving check. Black should "
            "respond by capturing the knight with the bishop on f6, which keeps the "
            "bishop pair intact.")
    assert [f.kind for f in ground(text, [b] + played, mv, played=played)] == ["bishop pair"]


def test_up_a_piece_is_about_that_piece():
    # 8...Bg4 position: White has an extra knight, Black an extra bishop.
    b = chess.Board("rnbqk2r/pp2pp2/2p2bpp/3p3P/2PP4/2N2N2/PP2PPP1/R2QKB1R b KQkq - 0 8")
    assert ground("White is up a knight; Black is up a bishop.", [b]) == []
    assert [f.kind for f in ground("Black is up a knight.", [b])] == ["material"]


def test_claims_about_candidate_moves_are_judged_after_that_move():
    # Phase 4 ask run 3: two true sentences were removed because "Bxd4
    # removes the threat to c3" and "Nxd4 leaves e5 undefended" were judged
    # on the position BEFORE either move.
    b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3pP3/3p4/2N1BN2/PPP1BPPP/R2Q1RK1 w - - 0 9")
    cands = []
    for san in ["exf6", "Bxd4", "Nxd4", "Qxd4"]:
        a = b.copy()
        a.push_san(san)
        cands.append((b.copy(), b.parse_san(san), [a]))

    def kinds(t):
        return [f.kind for f in ground(t, [b], candidates=cands)]
    assert kinds("Bxd4 removes the threat to the knight on c3.") == []
    assert kinds("Nxd4 leaves the pawn on e5 undefended.") == []
    assert kinds("Taking on d4 with the knight would leave White's pawn on e5 "
                 "undefended.") == []                    # no SAN: any candidate
    assert kinds("Bxd4 leaves the pawn on e5 undefended.") == ["undefended"]
    assert kinds("exf6 removes the threat to the knight on c3.") == ["threat"]
    assert kinds("The knight on f3 attacks the rook on a8.") == ["attack"]


def test_an_attack_given_up_must_have_existed():
    b = chess.Board("2b1k2r/1r4pp/1pp5/p7/2P2B2/3B1N2/1P1K1PPP/R6R w k - 1 18")
    boards = [b.copy()]
    for san in ["Rae1+", "Kd8", "Ne5", "Rf8", "Nxc6+"]:
        b.push_san(san)
        boards.append(b.copy())
    f = ground("The knight's move to c6 gives up an attack on the rook on f8.", boards)
    assert [x.kind for x in f] == ["attack"]
    assert ground("The knight no longer attacks the pawn on c6.", boards) == []
    assert ground("Nxc6+ does not involve giving up an attack on the rook on f8.",
                  boards) == []


def test_what_now_cannot_defend_a_piece_that_was_just_taken():
    # Amr's game, 6...Bxd4: "Black should move the knight from c6 to e5 to
    # defend the bishop" after 7.Nxd4 has taken it (Ne5 itself is the
    # engine's move, so the move check passed it).
    b = chess.Board("r1bqk2r/pppp1ppp/2n2n2/2b5/2BPP3/5N2/PP3PPP/RNBQK2R b KQkq - 0 6")
    a = b.copy()
    a.push_san("Bxd4")
    played, t = [a], a.copy()
    for san in ["Nxd4", "Ne5", "Be2"]:
        t.push_san(san)
        played.append(t.copy())
    text = ("**What now**: White will capture Black's bishop on d4 with the knight from f3. "
            "Black should respond by moving the knight from c6 to e5 to defend the bishop.")
    f = ground(text, [b, a] + played, b.parse_san("Bxd4"), played=played)
    # (the knight on e5 does not defend the other bishop either)
    assert "the bishop was captured on d4 by Nxd4; the other bishop is not attacked" in [
        x.correction for x in f]
    ok = text.replace("to defend the bishop", "to keep the position dynamic")
    assert ground(ok, [b, a] + played, b.parse_san("Bxd4"), played=played) == []


def test_king_shelter_side_and_a_defence_from_a_named_square():
    # Amr's report, 25 Oct: 10.Bxc4 "weakens White's king position by removing
    # a shield pawn" (the facts were about Black's shelter), and 15.Rdg1
    # "Better was: moving the queen to c2, which maintains defense of the a2
    # pawn" (the b2 pawn is in the way). Both got through.
    from chesscoach.grounding import _check_move_defends, _check_shelter, _shield
    b = chess.Board("r2qk2r/p3pp2/2p2bpp/7P/2BP2b1/2N1PN2/PP3PP1/R2QK2R b KQkq - 0 10")
    t = "It weakens White's king position by removing a shield pawn."
    assert [f.kind for f in _check_shelter(t, b, [b])] == ["shelter"]
    weaker = b.copy()
    weaker.remove_piece_at(chess.F2)
    assert _check_shelter(t, b, [weaker]) == []
    # Relative to the engine's choice counts too; king safety without the
    # shelter is another claim.
    stronger = b.copy()
    stronger.set_piece_at(chess.H3, chess.Piece(chess.PAWN, chess.WHITE))
    assert _check_shelter(t, b, [b], better=[stronger]) == [] or _shield(stronger, True) <= _shield(b, True)
    assert _check_shelter("It weakens White's king safety with a check.", b, [b]) == []
    q = chess.Board("5k2/8/8/8/8/8/PP6/2Q1K3 w - - 0 1")
    assert [f.correction for f in _check_move_defends(
        "Moving the queen to c2, which maintains defense of the a2 pawn.", [q])] == []
    q2 = chess.Board("5k2/8/8/8/8/8/PPQ5/4K3 w - - 0 1")
    assert [f.correction for f in _check_move_defends(
        "Moving the queen to c2, which maintains defense of the a2 pawn.", [q2])] == [
        "a queen on c2 does not defend the pawn on a2"]


def _hvf(n):
    b = chess.Board()
    for s in ("Nf3 d5 c4 Nf6 d4 g6 Nc3 Bg7 Bg5 c6 h4 h6 Bxf6 Bxf6 h5 Bg4 e3 dxc4 "
              "Bxc4 b5 hxg6 bxc4 gxf7+ Kxf7").split()[:n]:
        b.push_san(s)
    return b


def _line(start, sans):
    out, b = [start.copy()], start.copy()
    for s in sans.split():
        b.push_san(s)
        out.append(b.copy())
    return out


def test_what_now_move_must_be_by_the_named_side():
    # 11.hxg6: "White should ... and then retreating the king to f8": the
    # engine's Kf8 is Black's move, not White's.
    before = _hvf(20)
    mv = before.parse_san("hxg6")
    after = before.copy(); after.push(mv)
    played = _line(after, "bxc4 gxf7+ Kf8 Qc2")
    text = ("**What now**: Black will immediately capture the White bishop on c4 with the "
            "pawn from b5. White should respond by capturing the Black pawn on f7 with the "
            "pawn from g6, giving check, and then retreating the king to f8.")
    fails = ground(text, [before] + played, mv, played=played, better=[])
    assert [f.claim for f in fails] == ["retreating the king to f8"]
    ok = ("**What now**: White will capture Black's pawn on f7 with the pawn from g6, "
          "giving check. Black should move the king to f8.")
    after2 = _hvf(21); after2.push_san("bxc4")
    played2 = _line(after2, "gxf7+ Kf8 Qc2")
    assert ground(ok, [after2] + played2, after2.peek(), played=played2, better=[]) == []


def test_move_defends_a_piece_named_without_its_square():
    # 10.Bxc4 Bxh5: "moving the queen to b3, preparing to support the knight"
    # - the attacked knight is on f3, and the knight on c3 is in the way.
    before = _hvf(18)
    mv = before.parse_san("Bxc4")
    after = before.copy(); after.push(mv)
    played = _line(after, "Bxh5 Qb3 O-O")
    text = ("**What now**: Black will capture White's pawn on h5 with the bishop from g4. "
            "White should respond by moving the queen to b3, preparing to support the knight.")
    fails = ground(text, [before] + played, mv, played=played, better=[])
    assert any(f.kind == "defence" and "f3" in f.correction for f in fails)



# --- Gaps from the DeepSeek review (0.13) ------------------------------------

def test_shelter_claim_names_the_black_king():
    """'exposes the Black king by reducing the shield pawns' was not read
    at all (the pattern wanted "Black's king")."""
    import chess
    from chesscoach.grounding import ground
    b = chess.Board("rn1qk2r/pp2ppb1/2p3pp/7P/2pP4/2N1PN2/PP3PP1/R2QKB1R b KQkq - 0 9")
    b.castling_rights = chess.BB_EMPTY
    b.set_piece_at(chess.G8, chess.Piece(chess.KING, chess.BLACK))
    b.remove_piece_at(chess.E8)
    mv = b.parse_san("Qa5")
    after = b.copy(); after.push(mv)
    text = "**What happened**: The move exposes the Black king by reducing its shield pawns."
    fails = ground(text, [b, after], mv, played=[after], better=[])
    assert [f.kind for f in fails] == ["shelter"]


def test_opens_the_file_needs_a_pawn_to_leave_it():
    import chess
    from chesscoach.grounding import ground
    b = chess.Board()
    for san in "e4 e5 Nf3".split():
        b.push_san(san)
    mv = b.parse_san("Nc6")
    after = b.copy(); after.push(mv)
    text = "**What happened**: The knight move opens the f-file."
    assert [f.kind for f in ground(text, [b, after], mv, played=[after], better=[])] == ["file"]
    b2 = chess.Board("4k3/5p2/6P1/8/8/8/8/4K3 b - - 0 1")
    mv2 = b2.parse_san("fxg6")
    a2 = b2.copy(); a2.push(mv2)
    text2 = "**What happened**: Taking on g6 opens the f-file."
    assert ground(text2, [b2, a2], mv2, played=[a2], better=[]) == []


def test_fork_must_hit_the_pieces_it_names():
    import chess
    from chesscoach.grounding import _check_forks
    # Knight on c7 forks the king on e8 and the rook on a8 (not the queen).
    b = chess.Board("r2qk3/2N5/8/8/8/8/8/4K3 b - - 0 1")
    assert _check_forks("The knight forks the king and rook.", [b]) == []
    assert _check_forks("The knight forks the king and queen.", [b])[0].kind == "fork"
    assert _check_forks("Nc7 forks the king on e8 and the rook on a8.", [b]) == []
    assert _check_forks("Nc7 forks the queen on d8 and the rook on a8.", [b])


def test_the_move_gets_credit_only_for_what_it_did():
    """Review feedback: '8...Bg4 opens the f-file' passed because 9.hxg6 fxg6
    opened it two plies later. Said of the move, it is checked right after
    the move; 'allows White to open' is checked along the line."""
    import chess
    from chesscoach.grounding import _check_opens_file
    b = chess.Board("rnbqk2r/pp2pp2/2p2bpp/3p3P/2PP4/2N2N2/PP2PPP1/R2QKB1R b KQkq - 0 8")
    mv = b.parse_san("Bg4")
    line = []
    x = b.copy(); x.push(mv); line.append(x.copy())
    for san in ("e3", "gxh5"):
        x.push_san(san); line.append(x.copy())
    assert _check_opens_file("The move opens the g-file.", b, mv, line)          # it was gxh5, later
    assert _check_opens_file("The move allows Black to open the g-file.", b, mv, line) == []
