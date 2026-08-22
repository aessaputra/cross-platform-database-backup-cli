"""Regression: Postgres PGPASSWORD env — no prompt, no argv leak."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dbbackup.core.url import parse_connection_url


def test_postgres_backup_password_via_env_not_argv():
    from dbbackup.adapters.postgres import PostgresAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/pg_dump"),
        patch("dbbackup.adapters.postgres.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        opts = MagicMock(host="h", port=5432, user="u", password="s3cret!@#", database="db")
        PostgresAdapter().backup(opts)
        args, kwargs = popen.call_args
        joined = " ".join(args[0])
        assert "s3cret" not in joined
        assert kwargs["env"]["PGPASSWORD"] == "s3cret!@#"


def test_postgres_backup_no_password_no_env():
    from dbbackup.adapters.postgres import PostgresAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/pg_dump"),
        patch("dbbackup.adapters.postgres.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        PostgresAdapter().backup(
            MagicMock(host="h", port=5432, user="u", password="", database="db")
        )
        _, kwargs = popen.call_args
        assert kwargs.get("env") is None


def test_postgres_url_percent_decoded_via_env():
    from dbbackup.adapters.postgres import PostgresAdapter

    opts = parse_connection_url("postgresql://u:p%40ss%3Aw@host:5432/mydb?sslmode=require")
    assert opts.password == "p@ss:w"
    with (
        patch("shutil.which", return_value="/usr/bin/pg_dump"),
        patch("dbbackup.adapters.postgres.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        PostgresAdapter().backup(opts)
        _, kwargs = popen.call_args
        assert kwargs["env"]["PGPASSWORD"] == "p@ss:w"


def test_postgres_restore_password_via_env():
    import io

    from dbbackup.adapters.postgres import PostgresAdapter
    from dbbackup.models import BackupArtifact, ConnectionOpts, RestoreOpts

    artifact = BackupArtifact(
        db_type="postgres", format="sql", extension=".sql.gz", stream_or_path=io.BytesIO(b"dump")
    )
    conn = ConnectionOpts(
        db_type="postgres", host="h", port=5432, user="u", password="restoresecret", database="mydb"
    )
    opts = RestoreOpts(connection=conn, s3_key="k")
    with (
        patch("dbbackup.adapters.postgres.require_binary", return_value="/usr/bin/psql"),
        patch("dbbackup.adapters.postgres.subprocess.Popen") as popen,
    ):
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        popen.return_value = mock_proc
        PostgresAdapter().restore(artifact, opts)
        _, kwargs = popen.call_args
        assert kwargs["env"]["PGPASSWORD"] == "restoresecret"
