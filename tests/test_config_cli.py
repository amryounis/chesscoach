import pytest

from chesscoach import cli, config


def test_config_file_env_and_override(tmp_path, monkeypatch):
    f = tmp_path / "c.toml"
    f.write_text('depth = 12\nmodel = "m1"\n')
    monkeypatch.setenv("CHESSCOACH_MODEL", "m2")
    s = config.load_settings(str(f), depth=20)
    assert s.model == "m2" and s.depth == 20


def test_unknown_setting_is_an_error(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("stockfsh_path = 'x'\n")
    with pytest.raises(config.ConfigError, match="stockfsh_path"):
        config.load_settings(str(f))


def test_missing_stockfish_gives_clean_error(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHESSCOACH_STOCKFISH_PATH", str(tmp_path / "nope.exe"))
    pgn = tmp_path / "g.pgn"
    pgn.write_text("1. e4 e5 *\n")
    assert cli.main(["review", str(pgn)]) == 2
    assert "Stockfish not found" in capsys.readouterr().err


def test_config_init_writes_template(tmp_path):
    p = config.write_template(tmp_path / "config.toml", stockfish="C:\\sf\\sf.exe")
    assert 'stockfish_path = "C:/sf/sf.exe"' in p.read_text()
    with pytest.raises(config.ConfigError):
        config.write_template(p)


def test_a_config_file_in_the_current_folder_is_not_read(tmp_path, monkeypatch):
    """Review feedback: ./chesscoach.toml beside a downloaded PGN could set
    stockfish_path to any program. Only --config reads a local file."""
    from chesscoach import config
    monkeypatch.delenv("CHESSCOACH_STOCKFISH_PATH", raising=False)   # CI sets it; env beats files
    (tmp_path / "chesscoach.toml").write_text('stockfish_path = "evil.exe"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "nope" / "config.toml")
    assert config.find_config_file() is None
    assert config.load_settings().stockfish_path != "evil.exe"
    assert config.load_settings(str(tmp_path / "chesscoach.toml")).stockfish_path == "evil.exe"
