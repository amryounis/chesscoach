"""The answer-key harness."""
import pytest

from chesscoach import cli, config, evaluate
from chesscoach.evaluate import Engines, load_keys, run_facts, run_model, score_text


def test_bundled_keys_load():
    ids = {k.id for k in load_keys()}
    assert {"rook-check", "homer-8-bg4", "homer-9-dxc4", "homer-12-kxf7",
            "test-9-nxd4"} <= ids


def test_unknown_key_is_an_error():
    with pytest.raises(ValueError):
        load_keys(ids=["nope"])


def test_scoring_catches_the_claims_models_made():
    key = load_keys(ids=["rook-check"])[0]
    # Phrases from the October 2026 runs (Mistral, qwen3, Gemini).
    bad = ("Material is equal. The rook on e1 attacks the bishop on c8, and "
           "after the knight takes on c6 it forks the rook on b7. The bishop on "
           "c8 is still undefended.")
    r = score_text(key, bad)
    assert set(r.false_claims) == {"material-equal", "re1-attacks-c8", "fork-b7"}
    assert "loose-c8" in r.hits


def test_scoring_a_good_explanation():
    key = load_keys(ids=["rook-check"])[0]
    good = ("White is up a bishop and a knight. The check stops Black from "
            "castling, and the undefended bishop on c8 is a target. Both rook "
            "checks win; after the king steps aside the knight takes on c6 "
            "with check. Black has six legal replies.")
    r = score_text(key, good)
    assert r.misses == [] and r.false_claims == [] and r.board_errors == []


def test_board_errors_count_as_wrong():
    key = load_keys(ids=["rook-check"])[0]
    r = score_text(key, "The knight on d5 is strong.")
    assert r.board_errors and r.wrong == 1
    assert r.board_errors[0].startswith("placement:")


def test_pipeline_mode_scores_only_items_meant_for_it():
    key = load_keys(ids=["rook-check"])[0]
    r = score_text(key, "The check takes away castling.", mode="pipeline")
    assert r.hits == ["castling"] and r.misses == ["both-rook-checks"]


def test_static_facts_recall_without_stockfish():
    eng = Engines(path="")
    with eng:
        r = run_facts(load_keys(ids=["rook-check"])[0], eng)
        # "Both rook checks are equal" is an engine fact: missing without it.
        assert r.misses == ["both-rook-checks"]
        assert set(r.not_covered) == {"knight-takes-c6"}
        # Line-comparison keys need the engine and say so.
        assert "needs Stockfish" in run_facts(load_keys(ids=["homer-8-bg4"])[0], eng).skipped


def test_text_command(tmp_path, capsys):
    f = tmp_path / "reply.txt"
    f.write_text("Material is equal, and the knight forks the rook on b7.")
    code = cli.main(["eval", "--key", "rook-check", "--text", str(f), "--strict",
                     "--stockfish", ""])
    out = capsys.readouterr().out
    assert code == 1
    assert "material-equal, fork-b7" in out


@pytest.mark.engine
def test_facts_recall_with_stockfish(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish)
    with Engines(stockfish) as eng:
        results = [run_facts(k, eng) for k in load_keys()]
    for r in results:
        assert not r.misses, (r.key, r.misses)
    assert sum(1 for r in results if not r.skipped) >= 3


@pytest.mark.engine
def test_model_runs_with_a_stub(stockfish, monkeypatch):
    config.settings = config.load_settings(stockfish_path=stockfish)
    monkeypatch.setattr("chesscoach.narrator.call_llm",
                        lambda model, msgs, system=None: (
                            "**Assessment**: Excellent\n**What happened**: The "
                            "check stops castling. Material is equal.", None))
    key = load_keys(ids=["rook-check"])[0]
    with Engines(stockfish) as eng:
        # Pipeline: the grounding check removes the false sentence.
        r = run_model(key, "stub", "pipeline", eng)
        assert not r.error and not r.skipped
        assert "castling" in r.hits
        assert r.false_claims == [] and len(r.removed) == 1
        assert "Material is equal" in r.removed[0]
        # Raw: nothing removes it; it is scored as false twice over.
        r = run_model(key, "stub", "raw", eng)
        assert r.false_claims == ["material-equal"]
        assert any(e.startswith("material:") for e in r.board_errors)
        # Without the check the pipeline keeps the false sentence.
        config.settings.grounding = False
        r = run_model(key, "stub", "pipeline", eng)
        assert r.mode == "pipeline (no check)" and r.false_claims == ["material-equal"]
        config.settings.grounding = True
    with pytest.raises(ValueError):
        with Engines(stockfish) as eng:
            run_model(key, "stub", "nonsense", eng)


def test_report(tmp_path):
    key = load_keys(ids=["rook-check"])[0]
    r = score_text(key, "Material is equal.", model="m")
    path = tmp_path / "r.md"
    evaluate.write_report([r], str(path), [key])
    text = path.read_text()
    assert "False claims: Material is equal" in text


def test_wrong_model_name_fails_before_the_run(monkeypatch):
    monkeypatch.setattr(evaluate, "installed_models", lambda: ["qwen3:14b-q4_K_M", "mistral"])
    with pytest.raises(ValueError, match="Installed: qwen3:14b-q4_K_M, mistral"):
        evaluate.check_models(["qwen3:14b"])
    evaluate.check_models(["mistral:latest", "qwen3:14b-q4_K_M"])   # no error


def test_errors_show_dashes_not_zeros():
    r = evaluate.Result(key="k", mode="raw", model="m", error="boom")
    row = evaluate.summary_table([r]).splitlines()[-1]
    assert row == "| k | raw · m | - | - | - | - | - | boom |"


def test_item_table_counts_over_runs():
    key = load_keys(ids=["rook-check"])[0]
    texts = ["The check stops castling. Material is equal.",
             "The check stops castling; both rook checks are practically equal.",
             "A strong move."]
    rs = [score_text(key, t, mode="pipeline", model="m") for t in texts]
    table = evaluate.item_table(rs, [key])
    assert "| rook-check | pipeline · m | castling | 2/3 | must |" in table
    assert "| rook-check | pipeline · m | both-rook-checks | 1/3 | must |" in table
    assert "| rook-check | pipeline · m | material-equal | 1/3 | FALSE |" in table


def test_keys_have_explain_items_and_questions():
    k = load_keys(ids=["rook-check"])[0]
    assert {i.id for i in k.must if "explain" in i.modes} == {
        "material", "castling", "loose-c8", "both-rook-checks"}
    assert [q.id for q in k.asks] == ["c8-safe", "which-rook", "knight-fork"]
    # Move-specific items stay out of 'explain' unless they say so.
    bg4 = load_keys(ids=["homer-8-bg4"])[0]
    assert [i.id for i in bg4.must if "explain" in i.modes] == ["h-pawn-threat"]


@pytest.mark.engine
def test_explain_and_ask_modes_with_a_stub(stockfish, monkeypatch):
    import ollama
    config.settings = config.load_settings(stockfish_path=stockfish)
    replies = iter(["**The position**: Black can still castle. The bishop on c8 is "
                    "undefended.\n**Threats**: None.\n**Plan**: Check on e1; both rook "
                    "checks are practically equal."]
                   + ["The bishop on c8 has no defender."] * 10)
    monkeypatch.setattr(ollama, "chat", lambda **kw: {"message": {"content": next(replies)}})
    key = load_keys(ids=["rook-check"])[0]
    with Engines(stockfish) as eng:
        r = run_model(key, "stub", "explain", eng)
        assert r.mode == "explain" and not r.error
        assert set(r.hits) == {"material", "castling", "loose-c8", "both-rook-checks"}
        qs = evaluate.run_questions(key, "stub", eng)
    assert [q.key for q in qs] == ["rook-check?c8-safe", "rook-check?which-rook",
                                   "rook-check?knight-fork"]
    assert qs[0].hits == ["undefended"]
    # The router writes the verdict between the two rook checks itself, and
    # the "No: ... does not attack the rook on b7" answer to the fork question.
    assert qs[1].hits == ["equal"] and qs[2].hits == ["no-fork"]
    assert qs[1].by_code == ["equal"] and "by code: equal" in evaluate.summary_table(qs)
