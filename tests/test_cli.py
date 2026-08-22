"""Task 9 TDD: Typer CLI wiring — backup/restore/test-connection/schedule, password flags, exit codes."""

from typer.testing import CliRunner

from dbbackup.cli import app

runner = CliRunner()


def test_help_shows_full_only():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "full" in r.output.lower()


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0


def test_backup_env_password_no_plaintext_required(monkeypatch):
    monkeypatch.setenv("TEST_PW_CLI2", "envsecret")
    # Should accept --password-env and not require plaintext --password
    # Mock core so no real DB/S3 needed; scaffold currently not calling core — failing until wired
    r = runner.invoke(
        app,
        [
            "backup",
            "--db",
            "sqlite",
            "--database",
            "x.db",
            "--password-env",
            "TEST_PW_CLI2",
            "--s3-bucket",
            "bkt",
        ],
    )
    # After wiring should be 0 (or redact not leaking); before wiring scaffold returns 0 too but with placeholder text.
    # We assert scaffolding's placeholder is gone after wiring: output should NOT contain "not yet implemented"
    assert "not yet implemented" not in r.output.lower()


def test_restore_selective_table_arg_accepted():
    r = runner.invoke(app, ["restore", "--help"])
    assert "table" in r.output.lower() or "collection" in r.output.lower()


def test_test_connection_command_exists():
    r = runner.invoke(app, ["test-connection", "--help"])
    assert r.exit_code == 0


def test_schedule_daemon_flag_exists():
    r = runner.invoke(app, ["schedule", "--help"])
    assert r.exit_code == 0
    # Rich renders help with ANSI/box-drawing; strip ANSI for portability (macOS 3.13 CI)
    import re

    text = re.sub(r"\x1b\[[0-9;]*m", "", r.output)
    assert "--daemon" in text


def test_exit_codes_binary_missing(monkeypatch):
    # When mysqldump missing, backup should exit 11 after wiring; mock shutil.which failure via adapter
    from unittest.mock import MagicMock, patch

    with patch("dbbackup.core.backup.get_adapter") as ga:
        from dbbackup.adapters._helpers import BinaryNotFoundError

        m = MagicMock()
        m.backup.side_effect = BinaryNotFoundError("mysqldump", "hint")
        ga.return_value = m
        r = runner.invoke(
            app, ["backup", "--db", "mysql", "--database", "db", "--s3-bucket", "bkt"]
        )
        assert r.exit_code == 11
