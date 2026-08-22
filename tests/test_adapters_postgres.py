"""Tests for Postgres streaming adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_postgres_missing_binary_raises():
    from dbbackup.adapters.postgres import PostgresAdapter

    with patch("shutil.which", return_value=None), pytest.raises(Exception, match="pg_dump"):
        PostgresAdapter().test_connection(MagicMock())


def test_postgres_backup_missing_binary_raises():
    from dbbackup.adapters.postgres import PostgresAdapter

    with patch("shutil.which", return_value=None), pytest.raises(Exception, match="pg_dump"):
        PostgresAdapter().backup(
            MagicMock(host="h", user="u", password="p", database="db", port=5432)
        )


def test_postgres_backup_streams():
    from dbbackup.adapters.postgres import PostgresAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/pg_dump"),
        patch("dbbackup.adapters.postgres.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        artifact = PostgresAdapter().backup(
            MagicMock(host="h", user="u", password="p", database="db", port=5432)
        )
        assert artifact.extension in (".sql.gz", ".dump.gz")
        assert artifact.db_type == "postgres"
        assert artifact.stream_or_path is popen.return_value.stdout


def test_postgres_missing_binary_hint_contains_install_guidance():
    from dbbackup.adapters.postgres import PostgresAdapter

    with patch("shutil.which", return_value=None):
        with pytest.raises(Exception) as exc:
            PostgresAdapter().test_connection(MagicMock())
        msg = str(exc.value).lower()
        assert "apt" in msg or "brew" in msg or "choco" in msg
