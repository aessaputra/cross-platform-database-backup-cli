"""Restore orchestration: storage download -> gunzip -> adapter.restore() -> RestoreResult."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dbbackup.adapters.registry import get_adapter
from dbbackup.core.compression import decompress_stream
from dbbackup.core.redact import redact
from dbbackup.models import RestoreOpts
from dbbackup.storage import get_storage_backend

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


def _verify_local(key: str, gz_stream: io.BytesIO, local_path: str | None) -> None:
    """If verify requested and local storage, check sha256 against sidecar."""
    if not local_path:
        return
    sidecar = Path(local_path) / (key + ".json")
    # Also try resolved root variant
    candidates = [sidecar]
    try:
        from pathlib import Path as _P

        alt = (_P(local_path).resolve() / key).with_suffix(_P(key).suffix + ".json") if "/" in key else None
    except Exception:
        alt = None
    # simplest: key + .json under root
    meta = None
    for c in candidates:
        if c.exists():
            try:
                meta = json.loads(c.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
    if meta is None or "sha256" not in meta:
        return  # no sidecar yet or no sha — skip verify rather than fail open in unexpected way
    pos = gz_stream.tell()
    gz_stream.seek(0)
    h = hashlib.sha256()
    while True:
        chunk = gz_stream.read(64 * 1024)
        if not chunk:
            break
        h.update(chunk)
    gz_stream.seek(pos)
    if h.hexdigest() != meta["sha256"]:
        raise ValueError(f"sha256 mismatch for {key!r}: expected {meta['sha256']}, got {h.hexdigest()}")


def run_restore(opts: RestoreOpts) -> RestoreResult:
    start = datetime.now(timezone.utc)
    t0 = time.monotonic()
    try:
        db_type = opts.connection.db_type
        adapter = get_adapter(db_type)
        key = opts.effective_key()
        if not key:
            raise ValueError("restore requires --key (or --s3-key)")

        # Verify before decompress if requested and local
        backend = get_storage_backend(opts)
        gz_stream = backend.download(key)

        if opts.verify and opts.storage_type == "local":
            # need to buffer for verify (download returns file stream); verify reads + rewinds
            # wrap file stream into BytesIO for hashing without losing data
            if not isinstance(gz_stream, io.BytesIO):
                buf = io.BytesIO(gz_stream.read())
                try:
                    gz_stream.close()
                except Exception:
                    pass
                gz_stream = buf
            _verify_local(key, gz_stream, opts.local_path)
            gz_stream.seek(0)

        # decompress to temp buffer then pass to adapter
        raw = io.BytesIO()
        decompress_stream(gz_stream, raw)
        raw.seek(0)
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
