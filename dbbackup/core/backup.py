"""Backup orchestration: adapter -> gzip streaming -> storage upload -> BackupResult."""

from __future__ import annotations

import io
import logging
import time
from datetime import UTC, datetime

from dbbackup.adapters.registry import get_adapter
from dbbackup.core.compression import compress_stream
from dbbackup.core.redact import redact
from dbbackup.models import BackupOpts, BackupResult
from dbbackup.storage import get_storage_backend
from dbbackup.storage.local import sanitize_database as _sanitize_db

log = logging.getLogger(__name__)


def _build_key(opts: BackupOpts, artifact) -> str:
    """Build storage key.

    S3: <prefix>/<db_type>/<database>-<timestamp><ext>  (prefix optional)
    Local: <db_type>/<database>-<timestamp><ext>  (prefix ignored for local; root is prefix)
    Database segment sanitized.
    """
    ext = artifact.extension or ".dump"
    db_type = opts.connection.db_type or "unknown"
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_db = _sanitize_db(opts.connection.database or "unknown")
    key_suffix = f"{safe_db}-{ts}{ext}"
    if opts.storage_type == "local":
        return f"{db_type}/{key_suffix}"
    prefix = (opts.s3_prefix or "").strip("/")
    return f"{prefix}/{db_type}/{key_suffix}" if prefix else f"{db_type}/{key_suffix}"


def run_backup(opts: BackupOpts) -> BackupResult:
    start = datetime.now(UTC)
    t0 = time.monotonic()
    try:
        db_type = opts.connection.db_type
        database = opts.connection.database
        adapter = get_adapter(db_type)
        artifact = adapter.backup(opts.connection)

        # Build S3 key: <prefix>/<db>-<timestamp><ext>.gz (ext already .sql.gz/.archive.gz etc)
        # If artifact extension already ends with .gz, use as-is; otherwise gzip will add it.
        ext = artifact.extension or ".dump"
        # Use deterministic key builder
        s3_key = _build_key(opts, artifact)

        # Streaming: artifact.open_stream() -> compress -> storage upload
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
        raw_bytes = compressed.getvalue()
        # wrap for storage upload — use compressed
        from dbbackup.models import BackupArtifact

        comp_artifact = BackupArtifact(
            db_type=db_type,
            format=artifact.format,
            extension=ext,
            stream_or_path=compressed,
            size_hint=len(raw_bytes),
        )
        backend = get_storage_backend(opts)
        backend.upload(comp_artifact, s3_key)
        try:
            if not compressed.closed:
                compressed.close()
        except Exception:
            pass
        try:
            artifact.close()
        except Exception:
            pass
        end = datetime.now(UTC)
        duration = int((time.monotonic() - t0) * 1000)
        return BackupResult(
            status="success",
            s3_key=s3_key,
            bytes_written=len(raw_bytes),
            start_time=start,
            end_time=end,
            duration_ms=duration,
            db_type=db_type,
            database=database,
        )
    except KeyboardInterrupt as exc:
        end = datetime.now(UTC)
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
        end = datetime.now(UTC)
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
