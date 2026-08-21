"""Data models for the backup CLI v1.

Full-only v1; extension point for incremental/differential reserved for v2.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO


@dataclass
class ConnectionOpts:
    """Database connection options."""

    db_type: str = ""
    host: str = ""
    port: int = 0
    user: str = ""
    password: str = ""
    database: str = ""


@dataclass
class BackupOpts:
    """Options for a full backup (v1 only)."""

    connection: ConnectionOpts = field(default_factory=ConnectionOpts)
    s3_bucket: str = ""
    s3_prefix: str = ""
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    gzip_level: int = 6
    config: str | None = None


@dataclass
class RestoreOpts:
    """Options for restoring from a backup artifact."""

    connection: ConnectionOpts = field(default_factory=ConnectionOpts)
    s3_key: str = ""
    target_database: str | None = None
    tables: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)


@dataclass
class BackupArtifact:
    """Artifact produced by an adapter's backup().

    Holds the native dump artifact (streaming or temp-file) plus format metadata.
    Implements ``close()`` and context-manager for cleanup of temp files.
    """

    db_type: str
    format: str  # e.g. "sql", "archive", "sqlite", "dump"
    extension: str  # e.g. ".sql.gz" suffix before S3 gzip (adapter-defined)
    stream_or_path: BinaryIO | str | Path | None = None
    size_hint: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)
    needs_cleanup: bool = False
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        stream = self.stream_or_path
        if stream is None:
            return
        # If it's a file path that needs cleanup, remove it
        if isinstance(stream, (str, Path)):
            if self.needs_cleanup:
                try:
                    Path(stream).unlink(missing_ok=True)
                except OSError:
                    pass
            return
        # Otherwise it's a stream — close it
        try:
            close_fn = getattr(stream, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            pass

    def __enter__(self) -> BackupArtifact:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def open_stream(self) -> BinaryIO:
        """Return a readable BinaryIO for the artifact contents.

        If ``stream_or_path`` is a path, open it; otherwise return the stream.
        """
        s = self.stream_or_path
        if s is None:
            return io.BytesIO(b"")
        if isinstance(s, (str, Path)):
            return open(s, "rb")
        return s  # type: ignore[return-value]


@dataclass
class BackupResult:
    """Result of a backup run (one-shot or scheduled)."""

    status: str  # "success" | "failed" | "interrupted" | "skipped"
    s3_key: str | None = None
    bytes_written: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    error_code: int | None = None
    db_type: str | None = None
    database: str | None = None
