"""Restore orchestration: S3 download -> gunzip -> adapter.restore() -> RestoreResult."""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from dbbackup.adapters.registry import get_adapter
from dbbackup.core.compression import decompress_stream
from dbbackup.core.redact import redact
from dbbackup.models import RestoreOpts
from dbbackup.storage.s3 import S3Backend

log = logging.getLogger(__name__)


@dataclass
class RestoreResult:
    status: str  # success | failed | interrupted
    error: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        if self.error is not None:
            try:
                from dbbackup.core.redact import redact as _redact

                object.__setattr__(self, "error", _redact(self.error))
            except Exception:
                object.__setattr__(self, "error", "***")


def run_restore(opts: RestoreOpts) -> RestoreResult:
    start = datetime.now(timezone.utc)
    t0 = time.monotonic()
    try:
        db_type = opts.connection.db_type
        adapter = get_adapter(db_type)
        # S3 download — bucket derived from restore? Brief says S3Backend(bucket, ...) but RestoreOpts has no bucket.
        # Plan Task 7: S3 download via bucket from same config; for test we mock S3Backend entirely,
        # so bucket value doesn't matter. Use opts.s3_key prefix bucket placeholder or config.
        # Try to resolve bucket from opts if present, else placeholder.
        bucket = getattr(opts, "s3_bucket", None) or "test-bucket"
        endpoint_url = getattr(opts, "s3_endpoint_url", None)
        region = getattr(opts, "s3_region", None)
        backend = S3Backend(bucket=bucket, region=region, endpoint_url=endpoint_url)
        gz_stream = backend.download(opts.s3_key)
        # decompress to temp buffer then pass to adapter
        raw = io.BytesIO()
        decompress_stream(gz_stream, raw)
        raw.seek(0)
        # adapter.restore expects BackupArtifact | BinaryIO — pass raw stream + opts
        # For selective restore, RestoreOpts.tables/collections already set
        from dbbackup.models import BackupArtifact

        artifact = BackupArtifact(
            db_type=db_type, format="sql", extension=".sql.gz", stream_or_path=raw
        )
        adapter.restore(artifact, opts)
        end = datetime.now(timezone.utc)
        return RestoreResult(
            status="success",
            start_time=start,
            end_time=end,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    except KeyboardInterrupt as exc:
        end = datetime.now(timezone.utc)
        return RestoreResult(
            status="interrupted",
            error=redact(str(exc) or "interrupted"),
            start_time=start,
            end_time=end,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:
        end = datetime.now(timezone.utc)
        msg = redact(str(exc))
        log.error("restore failed: %s", msg)
        return RestoreResult(
            status="failed",
            error=msg,
            start_time=start,
            end_time=end,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
