"""Regression: MySQL password must not appear in argv; must be via MYSQL_PWD env."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _structured_opts(**kw):
    return MagicMock(
        host=kw.get("host", "h"),
        port=kw.get("port", 3306),
        user=kw.get("user", "u"),
        password=kw.get("password", "p@ss:w/ord"),
        database=kw.get("database", "mydb"),
        extra={},
    )


def test_mysql_backup_password_not_in_argv():
    from dbbackup.adapters.mysql import MySQLAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/mysqldump"),
        patch("dbbackup.adapters.mysql.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        MySQLAdapter().backup(_structured_opts(password="s3cret!@#"))
        args, kwargs = popen.call_args
        cmd = args[0]
        joined = " ".join(cmd)
        assert "s3cret" not in joined, f"password leaked in argv: {cmd}"
        assert "-p" not in joined
        assert "-ps3cret" not in joined
        # env must carry password
        assert kwargs.get("env") is not None
        assert kwargs["env"].get("MYSQL_PWD") == "s3cret!@#"


def test_mysql_backup_no_password_no_env():
    from dbbackup.adapters.mysql import MySQLAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/mysqldump"),
        patch("dbbackup.adapters.mysql.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        MySQLAdapter().backup(_structured_opts(password=""))
        _, kwargs = popen.call_args
        assert kwargs.get("env") is None


def test_mysql_url_password_via_env_percent_decoded():
    """URL percent-encoded password must decode and still go via env."""
    from dbbackup.adapters.mysql import MySQLAdapter
    from dbbackup.core.url import parse_connection_url

    opts = parse_connection_url("mysql://u:p%40ss%3Aw%2Ford@host:3306/mydb")
    assert opts.password == "p@ss:w/ord"
    with (
        patch("shutil.which", return_value="/usr/bin/mysqldump"),
        patch("dbbackup.adapters.mysql.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        MySQLAdapter().backup(opts)
        args, kwargs = popen.call_args
        assert "p@ss:w/ord" not in " ".join(args[0])
        assert kwargs["env"]["MYSQL_PWD"] == "p@ss:w/ord"


def test_mysql_cli_url_still_uses_secure_backup():
    """--url mysql:// must flow through same secure adapter path."""
    from typer.testing import CliRunner

    from dbbackup.cli import app

    runner = CliRunner()
    with patch("dbbackup.core.backup.run_backup") as rb:
        # run_backup will receive ConnectionOpts with decoded password
        # we assert by checking it was called with opts whose connection.password == decoded
        rb.return_value = MagicMock(status="success", s3_key="k", bytes_written=1, error=None)
        url = "mysql://u:p%40ss@host:3306/mydb"
        r = runner.invoke(app, ["backup", "--url", url, "--s3-bucket", "bkt"])
        assert r.exit_code == 0, r.output
        bopts = rb.call_args[0][0]
        assert bopts.connection.password == "p@ss"
        assert bopts.connection.db_type == "mysql"


def test_mysql_structured_still_works_via_env():
    from dbbackup.adapters.mysql import MySQLAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/mysqldump"),
        patch("dbbackup.adapters.mysql.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        art = MySQLAdapter().backup(
            _structured_opts(host="myhost", user="myuser", password="secret", database="mydb")
        )
        assert art.db_type == "mysql"
        _args, kwargs = popen.call_args
        assert kwargs["env"]["MYSQL_PWD"] == "secret"


def test_mysql_restore_password_via_env():
    import io

    from dbbackup.adapters.mysql import MySQLAdapter
    from dbbackup.models import BackupArtifact, ConnectionOpts, RestoreOpts

    artifact = BackupArtifact(
        db_type="mysql", format="sql", extension=".sql.gz", stream_or_path=io.BytesIO(b"dump")
    )
    conn = ConnectionOpts(
        db_type="mysql", host="h", port=3306, user="u", password="restoresecret", database="mydb"
    )
    opts = RestoreOpts(connection=conn, s3_key="k")

    with (
        patch("dbbackup.adapters.mysql.require_binary", return_value="/usr/bin/mysql"),
        patch("dbbackup.adapters.mysql.subprocess.Popen") as popen,
    ):
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        popen.return_value = mock_proc
        MySQLAdapter().restore(artifact, opts)
        args, kwargs = popen.call_args
        assert "restoresecret" not in " ".join(args[0])
        assert kwargs["env"]["MYSQL_PWD"] == "restoresecret"
