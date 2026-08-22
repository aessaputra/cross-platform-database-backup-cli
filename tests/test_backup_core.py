"""Task 7 TDD: backup orchestration failure -> BackupResult(status=failed) with redact, abort."""
from unittest.mock import MagicMock, patch

import pytest

from dbbackup.models import BackupOpts, ConnectionOpts


def _opts():
    return BackupOpts(
        connection=ConnectionOpts(db_type="mysql", database="mydb", user="u", password="s3cret"),
        s3_bucket="bkt",
        s3_prefix="backups",
    )


def test_backup_failure_emits_failed_result():
    from dbbackup.core.backup import run_backup

    with patch("dbbackup.core.backup.get_adapter") as ga:
        m = MagicMock()
        m.backup.side_effect = Exception("boom password=s3cret")
        ga.return_value = m
        result = run_backup(_opts())
        assert result.status == "failed"
        assert "s3cret" not in (result.error or "")


def test_backup_aborts_on_storage_failure():
    from dbbackup.core.backup import run_backup
    from dbbackup.models import BackupArtifact
    import io

    art = BackupArtifact(db_type="mysql", format="sql", extension=".sql.gz", stream_or_path=io.BytesIO(b"hello"))
    with patch("dbbackup.core.backup.get_adapter") as ga:
        m = MagicMock()
        m.backup.return_value = art
        ga.return_value = m
        with patch("dbbackup.core.backup.get_storage_backend") as sb_cls:
            sb = MagicMock()
            sb.upload.side_effect = Exception("s3 fail")
            sb_cls.return_value = sb
            result = run_backup(_opts())
            assert result.status == "failed"


def test_backup_success_gzip_and_key():
    from dbbackup.core.backup import run_backup
    from dbbackup.models import BackupArtifact
    import io

    art = BackupArtifact(db_type="mysql", format="sql", extension=".sql.gz", stream_or_path=io.BytesIO(b"hello"))
    with patch("dbbackup.core.backup.get_adapter") as ga:
        m = MagicMock()
        m.backup.return_value = art
        ga.return_value = m
        with patch("dbbackup.core.backup.get_storage_backend") as sb_cls:
            sb = MagicMock()
            sb_cls.return_value = sb
            result = run_backup(_opts())
            assert result.status == "success"
            assert result.s3_key is not None
            assert "backups/" in result.s3_key
            sb.upload.assert_called_once()
