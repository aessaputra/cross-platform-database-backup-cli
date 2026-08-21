from typer.testing import CliRunner

from dbbackup.cli import app

runner = CliRunner()


def test_help_shows_full_only():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "full" in r.stdout.lower()


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
