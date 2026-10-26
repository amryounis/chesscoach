"""Two fixed test positions, for checking a model quickly."""

from __future__ import annotations


import chess

from .engine import run_maia, run_stockfish
from .narrator import narrate, prepare_move
from .report import print_scoreboard


TEST_GAME = ("e4 e6 d4 d5 Nc3 Nf6 Be3 c5 Nf3 Nc6 Be2 Be7 "
             "O-O O-O e5 cxd4 Nxd4").split()


def run_fixed_tests(models: list):
    rows = []
    for label, ply in (("TEST 1: 8...cxd4", 16), ("TEST 2: 9.Nxd4", 17)):
        print("\n" + "=" * 60 + f"\n  {label}\n" + "=" * 60)
        before = chess.Board()
        for san in TEST_GAME[:ply - 1]:
            before.push_san(san)
        played = before.parse_san(TEST_GAME[ply - 1])
        after = before.copy()
        after.push(played)
        eb, ea = run_stockfish(before), run_stockfish(after)
        maia = run_maia(before)
        prep = prepare_move(before, played, eb, ea, maia)
        print(prep["prompt"])
        for model in models:
            r = narrate(model, prep)
            print("\n" + "─" * 40 + f"\n{model}  ({r['secs']:.1f}s)")
            print(r["err"] or r["text"])
            for w in r["issues"]:
                print(f"  ! {w}")
            rows.append((label, model, r))
    print_scoreboard(rows)
