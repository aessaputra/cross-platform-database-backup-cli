from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dbbackup.cli import app

runner = CliRunner()


def _ok_backup(**extra):
    m = MagicMock(status="success", s3_key="k", bytes_written=1, error=None)
    return m


def test_url_without_db_postgres():
    with patch("dbbackup.core.backup.run_backup") as rb:
        rb.return_value = _ok_backup()
        r = runner.invoke(
            app, ["backup", "--url", "postgresql://user:pass@host/db", "--s3-bucket", "bkt"]
        )
        assert r.exit_code == 0, r.output


def test_db_plus_matching_url():
    with patch("dbbackup.core.backup.run_backup") as rb:
        rb.return_value = _ok_backup()
        r = runner.invoke(
            app,
            [
                "backup",
                "--db",
                "postgres",
                "--url",
                "postgresql://user:pass@host/db",
                "--s3-bucket",
                "bkt",
            ],
        )
        assert r.exit_code == 0, r.output


def test_db_conflicting_url():
    r = runner.invoke(
        app,
        [
            "backup",
            "--db",
            "mysql",
            "--url",
            "postgresql://user:pass@host/db",
            "--s3-bucket",
            "bkt",
        ],
    )
    assert r.exit_code == 10
    assert "conflict" in r.output.lower()


def test_url_plus_host_conflict():
    r = runner.invoke(
        app,
        ["backup", "--url", "postgresql://user@host/db", "--host", "other", "--s3-bucket", "bkt"],
    )
    assert r.exit_code == 10
    assert "cannot be combined" in r.output.lower()


def test_url_plus_password_flag_conflict():
    r = runner.invoke(
        app,
        [
            "backup",
            "--url",
            "postgresql://user:pass@host/db",
            "--password",
            "x",
            "--s3-bucket",
            "bkt",
        ],
    )
    assert r.exit_code == 10


def test_unsupported_scheme():
    r = runner.invoke(app, ["backup", "--url", "redis://host/db", "--s3-bucket", "bkt"])
    assert r.exit_code == 10
    assert "unsupported" in r.output.lower()


def test_structured_still_requires_db():
    r = runner.invoke(app, ["backup", "--host", "h", "--database", "db", "--s3-bucket", "bkt"])
    assert r.exit_code == 10
    assert "--db" in r.output


def test_mongo_srv_without_db():
    with patch("dbbackup.core.backup.run_backup") as rb:
        rb.return_value = _ok_backup()
        r = runner.invoke(
            app,
            [
                "backup",
                "--url",
                "mongodb+srv://user:pass@cluster.mongodb.net/mydb?authSource=admin",
                "--s3-bucket",
                "bkt",
            ],
        )
        assert r.exit_code == 0, r.output


def test_test_connection_url_without_db():
    with patch("dbbackup.cli.get_adapter") as ga:
        m = MagicMock()
        m.test_connection.return_value = None
        ga.return_value = m
        r = runner.invoke(app, ["test-connection", "--url", "postgresql://user:pass@host/db"])
        assert r.exit_code == 0, r.output
        ga.assert_called_once()
        assert ga.call_args[0][0] == "postgres"


def test_restore_url_without_db():
    with patch("dbbackup.core.restore.run_restore") as rr:
        rr.return_value = MagicMock(status="success", error=None)
        r = runner.invoke(app, ["restore", "--url", "postgresql://user:pass@host/db", "--key", "k"])
        assert r.exit_code == 0, r.output
