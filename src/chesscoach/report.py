"""Console scoreboard (the markdown report is written by review)."""

from __future__ import annotations


def print_scoreboard(rows: list):
    """rows: (label, model, result-dict)"""
    print("\n" + "=" * 72)
    print("  SCOREBOARD (automatic check only — still read the text for")
    print("  inverted facts, e.g. 'removes the threat' vs 'removes the defender')")
    print("=" * 72)
    print(f"  {'model':<22}{'move':<22}{'issues 1st→final':>18}{'secs':>8}")
    for label, model, r in rows:
        iss = "ERR" if r["err"] else f"{r['first_issues']} → {len(r['issues'])}"
        print(f"  {model:<22}{label:<22}{iss:>18}{r['secs']:>8.1f}")
    for model in dict.fromkeys(m for _, m, _ in rows):
        rs = [r for _, m, r in rows if m == model and not r["err"]]
        if rs:
            print(f"  TOTAL {model}: {len(rs)} moves, "
                  f"{sum(r['first_issues'] for r in rs)} issues before retry, "
                  f"{sum(len(r['issues']) for r in rs)} after, "
                  f"avg {sum(r['secs'] for r in rs)/len(rs):.1f}s/move")
