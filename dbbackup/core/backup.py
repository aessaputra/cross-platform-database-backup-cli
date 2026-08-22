"""Backup orchestration: adapter -> gzip streaming -> S3 upload -> BackupResult."""
from __future__ import annotations

import io
import logging
import time
from datetime import datetime, timezone

from dbbackup.adapters.registry import get_adapter
from dbbackup.core.compression import compress_stream
from dbbackup.core.redact import redact
from dbbackup.models import BackupOpts, BackupResult
from dbbackup.storage.s3 import S3Backend

log = logging.getLogger(__name__)


def run_backup(opts: BackupOpts) -> BackupResult:
    start = datetime.now(timezone.utc)
    t0 = time.monotonic()
    try:
        db_type = opts.connection.db_type
        database = opts.connection.database
        adapter = get_adapter(db_type)
        artifact = adapter.backup(opts.connection)

        # Build S3 key: <prefix>/<db>-<timestamp><ext>.gz (ext already .sql.gz/.archive.gz etc)
        # If artifact extension already ends with .gz, use as-is; otherwise gzip will add it.
        # Plan §Task 7: key <prefix>/<db>-<timestamp><ext>
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        ext = artifact.extension or ".dump"
        # extension from adapters is .sql.gz / .archive.gz / .sqlite.gz — use directly
        key_suffix = f"{database}-{ts}{ext}"
        prefix = (opts.s3_prefix or "").strip("/")
        s3_key = f"{prefix}/{key_suffix}" if prefix else key_suffix

        # Streaming: artifact.open_stream() -> compress -> S3 upload
        src = artifact.open_stream()
        compressed = io.BytesIO()
        try:
            compress_stream(src, compressed, level=opts.gzip_level)
        finally:
            try:
                src.close()
            except Exception:
                pass
        compressed.seek(0)
        # wrap for S3 upload_fileobj
        # Create minimal artifact wrapper with stream
        from dbbackup.models import BackupArtifact

        comp_artifact = BackupArtifact(
            db_type=db_type,
            format=artifact.format,
            extension=ext,
            stream_or_path=compressed,
            size_hint=len(compressed.getvalue()),
        )
        backend = S3Backend(
            bucket=opts.s3_bucket,
            region=opts.s3_region,
            endpoint_url=opts.s3_endpoint_url,
        )
        backend.upload(comp_artifact, s3_key)
        # cleanup temp artifact (sqlite temp-file)
        try:
            artifact.close()
        except Exception:
            pass
        end = datetime.now(timezone.utc)
        duration = int((time.monotonic() - t0) * 1000)
        return BackupResult(
            status="success",
            s3_key=s3_key,
            bytes_written=len(compressed.getvalue()),
            start_time=start,
            end_time=end,
            duration_ms=duration,
            db_type=db_type,
            database=database,
        )
    except KeyboardInterrupt as exc:
        end = datetime.now(timezone.utc)
        return BackupResult(
            status="interrupted",
            start_time=start,
            end_time=end,
            duration_ms=int((time.monotonic() - t0) * 1000),
            error=redact(str(exc) or "interrupted"),
            db_type=getattr(opts.connection, "db_type", None),
            database=getattr(opts.connection, "database", None),
        )
    except Exception as exc:
        end = datetime.now(timezone.utc)
        msg = redact(str(exc))
        log.error("backup failed: %s", msg)
        return BackupResult(
            status="failed",
            start_time=start,
            end_time=end,
            duration_ms=int((time.monotonic() - t0) * 1000),
            error=msg,
            db_type=getattr(opts.connection, "db_type", None),
            database=getattr(opts.connection, "database", None),
        )
