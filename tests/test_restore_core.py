"""Task 7 TDD: restore orchestration."""

from unittest.mock import MagicMock, patch

from dbbackup.models import ConnectionOpts, RestoreOpts


def _opts():
    return RestoreOpts(
        connection=ConnectionOpts(db_type="mysql", database="mydb"),
        s3_key="backups/mydb-20260101T000000.sql.gz",
    )


def test_restore_failure_status_failed():
    from dbbackup.core.restore import run_restore

    with patch("dbbackup.core.restore.get_adapter") as ga:
        m = MagicMock()
        m.restore.side_effect = Exception("restore boom password=s3cret")
        ga.return_value = m
        with patch("dbbackup.core.restore.get_storage_backend") as sb_cls:
            sb = MagicMock()
            sb.download.return_value = __import__("io").BytesIO(b"gz")
            sb_cls.return_value = sb
            # also mock decompression to avoid needing real gzip
            with patch("dbbackup.core.restore.decompress_stream"):
                result = run_restore(_opts())
                assert result.status == "failed"
                assert "s3cret" not in (result.error or "")


def test_restore_selective_table_passes_through():
    import gzip
    import io

    from dbbackup.core.restore import run_restore

    # real gz artifact for successful path
    raw = b"select * from t"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)
    buf.seek(0)

    opts = RestoreOpts(
        connection=ConnectionOpts(db_type="mysql", database="mydb"),
        s3_key="backups/mydb-20260101T000000.sql.gz",
        tables=["t"],
    )
    with patch("dbbackup.core.restore.get_adapter") as ga:
        m = MagicMock()
        ga.return_value = m
        with patch("dbbackup.core.restore.get_storage_backend") as sb_cls:
            sb = MagicMock()
            sb.download.return_value = buf
            sb_cls.return_value = sb
            result = run_restore(opts)
            assert result.status == "success"
            m.restore.assert_called_once()
