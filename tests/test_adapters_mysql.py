"""Tests for MySQL streaming adapter."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_mysql_missing_binary_raises():
    from dbbackup.adapters.mysql import MySQLAdapter

    with patch("shutil.which", return_value=None):
        with pytest.raises(Exception, match="mysqldump"):
            MySQLAdapter().test_connection(MagicMock())


def test_mysql_backup_missing_binary_raises():
    from dbbackup.adapters.mysql import MySQLAdapter

    with patch("shutil.which", return_value=None):
        with pytest.raises(Exception, match="mysqldump"):
            MySQLAdapter().backup(
                MagicMock(host="h", user="u", password="p", database="db", port=3306)
            )


def test_mysql_backup_streams():
    from dbbackup.adapters.mysql import MySQLAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/mysqldump"),
        patch("dbbackup.adapters.mysql.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        popen.return_value.stdout.read.return_value = b"dump"
        artifact = MySQLAdapter().backup(
            MagicMock(host="h", user="u", password="p", database="db", port=3306)
        )
        assert artifact.extension == ".sql.gz"
        assert artifact.format == "sql"
        assert artifact.db_type == "mysql"
        assert artifact.stream_or_path is popen.return_value.stdout


def test_mysql_backup_uses_mysqldump_binary():
    from dbbackup.adapters.mysql import MySQLAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/mysqldump"),
        patch("dbbackup.adapters.mysql.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        artifact = MySQLAdapter().backup(
            MagicMock(host="myhost", user="myuser", password="secret", database="mydb", port=3306)
        )
        args, _kwargs = popen.call_args
        cmd = args[0]
        assert "mysqldump" in cmd[0]


def test_mysql_missing_binary_hint_contains_install_guidance():
    from dbbackup.adapters.mysql import MySQLAdapter

    with patch("shutil.which", return_value=None):
        with pytest.raises(Exception) as exc:
            MySQLAdapter().test_connection(MagicMock())
        msg = str(exc.value).lower()
        assert "apt" in msg or "brew" in msg or "choco" in msg
