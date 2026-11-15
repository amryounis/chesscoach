"""Report a problem: what a tester sends."""
from urllib.parse import parse_qs, urlparse

from chesscoach import config, problem as report


def _setup(monkeypatch, tmp_path, log=""):
    monkeypatch.setattr(config, "settings", config.load_settings())
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "config.toml")
    (tmp_path / "chesscoach.log").write_text(log, encoding="utf-8")


def test_the_report_has_the_details_and_never_a_key(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "--- review\nTraceback: engine event loop dead\n"
           "POST https://x/v1?key=AIzaSyA1b2c3d4e5f6g7h8i9j0 failed\n")
    config.settings.online_key = "sk-secret-123456"
    config.settings.lichess_token = "lip_abcdefabcdef"
    monkeypatch.setenv("X", "sk-secret-123456")
    (tmp_path / "chesscoach.log").write_text(
        (tmp_path / "chesscoach.log").read_text() + "token sk-secret-123456 lip_abcdefabcdef\n")
    r = report.build("The review stopped.")
    t = r["text"]
    assert t.startswith("What happened (please describe it):\nThe review stopped.")
    assert "chesscoach " in t and "Coach: Ollama, model" in t and "engine event loop dead" in t
    for secret in ("sk-secret-123456", "lip_abcdefabcdef", "AIzaSyA1b2c3d4e5f6g7h8i9j0"):
        assert secret not in t and secret not in r["url"]
    q = parse_qs(urlparse(r["url"]).query)
    assert r["url"].startswith(report.ISSUES)
    assert q["title"] == [f"Problem in chesscoach {report.__version__}"]
    assert "engine event loop dead" in q["body"][0]


def test_a_long_log_is_cut_for_the_link_but_not_for_copy(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "\n".join(f"slow position {i}: " + "x" * 150 for i in range(400)))
    r = report.build("")
    assert len(r["url"]) <= report.URL_MAX
    assert r["text"].count("slow position") == report.LOG_LINES     # the copy has the whole tail
    assert "slow position 399" in parse_qs(urlparse(r["url"]).query)["body"][0]   # newest kept
