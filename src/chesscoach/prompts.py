"""The narrator's system prompt and the per-move prompt builder."""

from __future__ import annotations

from typing import Optional

import chess

from .assessment import compute_assessment, eval_phrase, win_pct
from .describe import board_to_ascii, board_to_piece_list, describe_move, describe_move_effects, describe_pv_ideas, side_name
from .engine import EngineAnalysis, MaiaPrediction
from .features import PositionalFeatures, compare_lines


def _effects(board_before, move) -> list:
    """What the move does, verified. Phase 7g notes by default (a threat said
    once, recaptures, lines opened, pins, material after an exchange);
    `prompt_notes = "effects"` gives the older list, for comparison runs."""
    from . import config
    if getattr(config.settings, "prompt_notes", "events") == "effects":
        return describe_move_effects(board_before, move)
    from .events import move_events
    return [e.text for e in move_events(board_before, move)]


SYSTEM_PROMPT = """You are a chess coach writing move-by-move annotations in the style of John Nunn, Jeremy Silman, or Dvoretsky. Your audience is a club-level player (1200-1800 Elo).

CRITICAL RULES:
1. The MOVE ASSESSMENT section gives you a label (Excellent, Good, Inaccuracy, Mistake, Blunder) and who played the move. USE THESE EXACTLY — do not override, soften, or second-guess them.
2. ONLY refer to pieces listed in the PIECE LIST. Do NOT invent pieces on squares not listed.
3. DO NOT calculate, invent, or write out chess moves or variations. You are a narrator. The engine's ideas are described in plain English in the ENGINE EVALUATION — rephrase those, do not add your own.
4. DO NOT use algebraic notation for moves in your response. Describe everything in words: "White captures the knight on f6" not "exf6".
5. The TACTICAL FACTS and POSITIONAL FEATURES are verified and COMPLETE. If a pin, mate threat or defender is not listed, it does not exist. Do not invent additional tactics or defensive relationships.
6. To explain a bad move, use WHAT THE MOVE DID and WHAT THE ENGINE PREFERRED INSTEAD. Those sections tell you what was missed. Never suggest a move that is not described in the prompt.

Your job:
- Explain WHY the move deserves its assessment label: what strategic or tactical idea it serves or misses
- Name the positional themes: pawn structure, outposts, weak squares, piece activity, king safety, development
- When a move is bad, explain the concept it violated — don't show a refutation
- If a typical human move (from Maia) is mentioned, comment on what most players would instinctively do
- Be direct: "This is a serious mistake because..." not "This might not be optimal"
- Describe the size of an advantage ONLY with the phrase given in POSITION VERDICT. Never use any word listed under BANNED WORDS.

Follow the OUTPUT FORMAT given at the end of each prompt exactly — same headings, same order, nothing added."""


DRAMATIC_WORDS = ["collapse", "collapses", "fatal", "crushing", "decisive",
                  "lost", "losing", "winning", "only move",
                  "only way", "devastating", "catastrophic", "game over"]


MATE_WORDS = ["checkmate", "mate", "mating", "forced mate", "mating attack",
              "mating net", "mating sequence"]


def build_user_prompt(board_before: chess.Board, played: chess.Move,
                      features: PositionalFeatures,
                      engine_before: EngineAnalysis,
                      engine_after: EngineAnalysis,
                      tactical: str = "",
                      maia: Optional[MaiaPrediction] = None,
                      pgn_moves: str = "",
                      opening_note: str = "",
                      fixed_prefix: str = "",
                      focus: str = "",
                      key_facts: Optional[list] = None,
                      fixed_suffix: str = "") -> tuple:
    """
    Build the user prompt. Everything the LLM needs is stated as a verified
    fact; algebraic notation appears only to name the move being annotated.
    """
    board = board_before.copy()
    board.push(played)

    is_white_move = board_before.turn == chess.WHITE
    side = side_name(board_before.turn)
    move_san = board_before.san(played)
    n = board_before.fullmove_number
    move_label = f"{n}. {move_san}" if is_white_move else f"{n}...{move_san}"

    # --- Deterministic assessment ---
    label, cp_loss = compute_assessment(
        engine_before.score_cp, engine_after.score_cp,
        engine_before.score_mate, engine_after.score_mate,
        is_white_move, delivered_mate=board.is_checkmate())

    if label == "Unknown":
        assessment = f"- Assessment could not be determined.\n- {side} played this move."
    else:
        assessment = (f"- Assessment: **{label}**\n"
                      + (f"- Centipawn loss: {cp_loss}\n" if cp_loss is not None else "")
                      + f"- {side} played this move.")

    # --- What the move did (verified) ---
    did = [describe_move(board_before, played)]
    did += _effects(board_before, played)
    what_it_did = "\n".join(f"- {d}" for d in did)

    # --- What should have been played instead (from BEFORE position) ---
    instead = ""
    best_san = engine_before.best_move
    if best_san and best_san != move_san:
        try:
            best_mv = board_before.parse_san(best_san)
            lines = [describe_move(board_before, best_mv)]
            lines += _effects(board_before, best_mv)
            instead = ("\nWHAT THE ENGINE PREFERRED INSTEAD (from the position "
                       "before the move):\n"
                       + "\n".join(f"- {l}" for l in lines))
            if label in ("Inaccuracy", "Mistake", "Blunder"):
                diffs = compare_lines(board_before, played, engine_before, engine_after)
                instead += ("\n\nHOW THE TWO MOVES COMPARE — the position a few moves "
                            "later with best play, after the player's move vs after "
                            "the engine's choice (verified, already sorted by which "
                            "move each difference favours — keep them on the side "
                            "they are listed under). These are the ONLY reasons you "
                            "may give:\n")
                instead += ("\n".join(diffs) if diffs else
                            "- No difference in material, king safety, pawn structure "
                            "or piece placement. The engine's advantage is dynamic "
                            "(activity or tactics). Do NOT invent a specific reason; "
                            "say only that the engine's choice keeps a better position.")
        except ValueError:
            pass
    elif best_san == move_san:
        instead = "\nThis was the engine's top choice."
        # Only good move? Compare the best line with the second-best.
        if engine_before.alt_scores:
            cp2, mate2 = engine_before.alt_scores[0]
            w_best = win_pct(engine_before.score_cp, engine_before.score_mate, is_white_move)
            w_alt = win_pct(cp2, mate2, is_white_move)
            if w_best is not None and w_alt is not None and w_best - w_alt >= 20:
                alt_verdict, _ = eval_phrase(cp2, mate2)
                instead = ("\nThis was the engine's top choice and the ONLY good "
                           f"move: the next-best option would have left a position "
                           f"where {alt_verdict}, instead of one where "
                           f"{eval_phrase(engine_after.score_cp, engine_after.score_mate)[0]}. "
                           "Praise the player for finding it.")

    # --- Maia, in words ---
    maia_section = ""
    if maia and maia.predicted_move:
        try:
            mm = board_before.parse_san(maia.predicted_move)
            words = describe_move(board_before, mm)
            notes = []
            if maia.predicted_move == move_san:
                notes.append("this is the move that was played")
            if maia.predicted_move == best_san:
                notes.append("this matches the engine's top choice")
            note = f" ({'; '.join(notes)})" if notes else ""
            maia_section = ("\nHUMAN MOVE PREDICTION (Maia — what a typical "
                            f"club player would play here):\n- {words}{note}")
        except ValueError:
            pass

    # --- Engine view from the new position, as fixed wording ---
    verdict, decisive = eval_phrase(engine_after.score_cp, engine_after.score_mate)
    if board.is_checkmate():
        verdict, decisive = f"{side} has delivered checkmate — the game is over", True
    before_verdict, _ = eval_phrase(engine_before.score_cp, engine_before.score_mate)
    banned = [] if decisive else list(DRAMATIC_WORDS)
    # Mate talk only when the engine actually sees a forced mate (or one
    # just happened). 'Leads to checkmate' at -7.5 is a false claim.
    mate_real = (board.is_checkmate() or engine_after.score_mate is not None
                 or engine_before.score_mate is not None
                 or "has checkmate in one" in tactical
                 or "threatening checkmate" in tactical)
    if not mate_real:
        banned += MATE_WORDS

    # --- Exchange in progress? (capture, and the opponent can recapture) ---
    exchange = (board_before.is_capture(played)
                and bool(board.attackers(board.turn, played.to_square)))

    opponent = side_name(board.turn)
    game_context = f"\nGAME SO FAR:\n{pgn_moves}\n" if pgn_moves else ""

    # --- Output format depends on the label ---
    bad = label in ("Inaccuracy", "Mistake", "Blunder")
    # The first sentence of 'What happened' is written in code, so the model
    # can't scramble who captured what. The model writes only the 'why'.
    fixed_sentence = fixed_prefix + describe_move(board_before, played) + "."
    if fixed_suffix:
        fixed_sentence += " " + fixed_suffix
    fmt = [f"**Assessment**: {label}",
           "**What happened**: The first sentence is already written: "
           f"\"{fixed_sentence}\" Do NOT repeat it. Write only 1-2 sentences "
           "explaining WHY the move earned this label, using "
           + ("the KEY FACTS first, then " if key_facts else "") + "WHAT THE MOVE DID"
           + (" and HOW THE TWO MOVES COMPARE." if bad else ".")
           + (f" {focus}" if focus else "")]
    if bad:
        fmt.append("**Better was**: 1 sentence describing the move in WHAT "
                   "THE ENGINE PREFERRED INSTEAD, with its idea taken ONLY from "
                   "HOW THE TWO MOVES COMPARE.")
    if board.is_checkmate():
        fmt.append("**What now**: write exactly: The game is over.")
    else:
        fmt.append(f"**What now**: 1-2 sentences. {opponent} moves next: say what "
                   f"{opponent} will do (step 1 of the likely continuation) and "
                   f"how {side} should respond (step 2). Write it from {side}'s "
                   f"point of view.")
    output_format = "\n".join(fmt)

    banned_line = (", ".join(banned) if banned
                   else "(none — the advantage is decisive)")

    key_section = ""
    if key_facts:
        key_section = ("\nKEY FACTS (verified with the engine; the most important first. "
                       "Build your explanation on these before anything else):\n"
                       + "\n".join(f"- {k}" for k in key_facts) + "\n")

    prompt = f"""Annotate this chess move.

MOVE: {move_label} (played by {side})
NEXT TO MOVE: {opponent}

MOVE ASSESSMENT (determined from score change — use this label exactly):
{assessment}

POSITION VERDICT (use this exact wording for the size of any advantage):
- Before the move: {before_verdict}
- After the move: {verdict}
BANNED WORDS: {banned_line}
{key_section}
WHAT THE MOVE DID (verified):
{what_it_did}
{instead}
{opening_note}
{maia_section}
{game_context}
BOARD POSITION (after the move):
{board_to_ascii(board)}

PIECE LIST (ONLY refer to pieces listed here):
{board_to_piece_list(board)}

ENGINE VIEW OF THE NEW POSITION:
- Likely continuation, described:
{describe_pv_ideas(board, engine_after.best_line)}

POSITIONAL FEATURES:
{features.summary(exchange_in_progress=exchange)}

TACTICAL FACTS (computed from the position — complete; do not add others):
{tactical}

Use only the facts above. Do not name any move that is not described above, and do not write move notation.

OUTPUT FORMAT (exactly these headings, in this order):
{output_format}"""
    return prompt, banned, fixed_sentence
