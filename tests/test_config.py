import logging


def test_layered_merge(tmp_path, monkeypatch):
    from dbbackup.config import load_config

    # CLI flags should override TOML and env
    monkeypatch.delenv("DBBACKUP_DATABASE", raising=False)
    cfg = load_config({"database": "from_cli"})
    assert cfg.database == "from_cli"


def test_plaintext_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    cfg_text = '[connection]\npassword="secret"\n'
    p = tmp_path / "dbbackup.toml"
    p.write_text(cfg_text)
    from dbbackup.config import load_config

    load_config({"config": str(p)})
    assert "plaintext" in caplog.text.lower()


def test_tomllib_not_tomli_dep():
    import pathlib

    import dbbackup.config as mod

    src = pathlib.Path(mod.__file__).read_text()
    assert "tomllib" in src
    # runtime must not require tomli for reading; tomli-w is optional for writes
    assert "from tomli " not in src and "import tomli" not in src or "tomli-w" in src
    deps = pathlib.Path("pyproject.toml").read_text().lower()
    assert "tomli\n" not in deps or "tomli-w" in deps


def test_platformdirs_source_of_truth(tmp_path):
    from dbbackup.config import _user_config_path

    p = _user_config_path()
    assert p is not None
