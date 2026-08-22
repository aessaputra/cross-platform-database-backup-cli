"""Tests for MongoDB streaming adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_mongo_missing_binary_raises():
    from dbbackup.adapters.mongo import MongoAdapter

    with patch("shutil.which", return_value=None), pytest.raises(Exception, match="mongodump"):
        MongoAdapter().test_connection(MagicMock())


def test_mongo_backup_missing_binary_raises():
    from dbbackup.adapters.mongo import MongoAdapter

    with patch("shutil.which", return_value=None), pytest.raises(Exception, match="mongodump"):
        MongoAdapter().backup(
            MagicMock(host="h", user="u", password="p", database="db", port=27017)
        )


def test_mongo_backup_streams():
    from dbbackup.adapters.mongo import MongoAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/mongodump"),
        patch("dbbackup.adapters.mongo.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        artifact = MongoAdapter().backup(
            MagicMock(host="h", user="u", password="p", database="db", port=27017)
        )
        assert artifact.extension == ".archive.gz"
        assert artifact.format == "archive"
        assert artifact.db_type == "mongo"
        # With password hardening, stdout is wrapped for --config cleanup; unwrap for check
        underlying = getattr(artifact.stream_or_path, "wrapped_stdout", artifact.stream_or_path)
        assert underlying is popen.return_value.stdout


def test_mongo_missing_binary_hint_contains_install_guidance():
    from dbbackup.adapters.mongo import MongoAdapter

    with patch("shutil.which", return_value=None):
        with pytest.raises(Exception) as exc:
            MongoAdapter().test_connection(MagicMock())
        msg = str(exc.value).lower()
        assert "apt" in msg or "brew" in msg or "choco" in msg
