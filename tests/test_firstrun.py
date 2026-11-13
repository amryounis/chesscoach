"""The first-run setup's checks, and the installed program's own Stockfish."""
import time

from chesscoach import bundle, firstrun


def test_the_graphics_card_and_the_suggestion():
    out = "NVIDIA GeForce GTX 1650, 4096\nNVIDIA GeForce RTX 3060, 12288\n"
    card = firstrun.gpu(lambda cmd, timeout=5: out)
    assert card == {"name": "NVIDIA GeForce RTX 3060", "vram_gb": 12.0}
    assert firstrun.gpu(lambda cmd, timeout=5: "") is None              # no nvidia-smi
    assert firstrun.suggest(card, 32)["model"] == "qwen3:14b"
    eight = firstrun.suggest({"name": "RTX 3070", "vram_gb": 8.0}, 16)
    assert eight["model"] == "qwen3:8b" and "hasn't been measured" in eight["text"]
    for c in ({"name": "GTX 1650", "vram_gb": 4.0}, None):
        s = firstrun.suggest(c, 8)
        assert s["coach"] == "online" and "Gemini" in s["text"]


def test_ollama_status():
    class C:
        def list(self):
            return {"models": [{"model": "qwen3:14b"}, {"name": "gemma3:latest"}]}
    assert firstrun.ollama_status("qwen3:14b", C()) == {
        "running": True, "models": ["gemma3:latest", "qwen3:14b"], "has": True}
    assert firstrun.ollama_status("gemma3", C())["has"]
    assert not firstrun.ollama_status("qwen3:8b", C())["has"]

    class Down:
        def list(self):
            raise ConnectionError("no")
    assert firstrun.ollama_status("qwen3:14b", Down()) == {"running": False, "models": [], "has": False}


def test_downloading_a_model_reports_progress():
    class C:
        def pull(self, model, stream=True):
            yield {"status": "pulling manifest"}
            yield {"status": "pulling", "total": 100, "completed": 40}
            yield {"status": "success"}

    p = firstrun.Pull()
    p.start("qwen3:14b", C())
    for _ in range(50):
        if p.progress()["done"]:
            break
        time.sleep(0.05)
    s = p.progress()
    assert s["done"] and not s["running"] and s["total"] == 100 and s["completed"] == 40

    class Broken:
        def pull(self, model, stream=True):
            raise RuntimeError("disk full")
            yield
    p.start("x", Broken())
    time.sleep(0.3)
    assert "disk full" in p.progress()["error"] and not p.progress()["running"]


def test_the_installed_programs_stockfish(tmp_path):
    assert bundle.bundled_stockfish(tmp_path / "none") == ""
    (tmp_path / "stockfish.exe").write_text("")
    assert bundle.bundled_stockfish(tmp_path, avx2=True).endswith("stockfish.exe")
    (tmp_path / "stockfish-avx2.exe").write_text("")
    assert bundle.bundled_stockfish(tmp_path, avx2=True).endswith("stockfish-avx2.exe")
    assert bundle.bundled_stockfish(tmp_path, avx2=False).endswith("stockfish.exe")
    assert bundle.app_dir() is None and not bundle.frozen()            # run from Python
