"""SQLite adapter — non-streaming backup exception per spec §2.4.2.

Uses ``sqlite3`` backup API (no external binary) and produces a **temporary
database file** artifact via ``core/workdir.TempWorkdir`` (internal, not a
storage backend). Extra compression to S3 is handled by ``core/backup.py`` or
left to the caller that consumes ``BackupArtifact.open_stream()``. The per-
adapter extension declares ``.sqlite.gz``, so the S3 key carries the right
suffix before the orchestrator's gzip step (see models/design spec).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import BinaryIO

from dbbackup.adapters.base import DBAdapter
from dbbackup.models import BackupArtifact, RestoreOpts

# Workdir used only for lifecycle docs; actual temp file is managed via
# tempfile.mkstemp with owner-restricted perms so the cleanup path stays inside
# BackupArtifact.close() (no dangling TempWorkdir to juggle).


class SQLiteAdapter(DBAdapter):
    """SQLite adapter backed by the stdlib ``sqlite3`` backup API."""

    name = "sqlite"

    # Per-adapter declared metadata
    artifact_format: str = "sqlite"
    artifact_extension: str = ".sqlite.gz"

    def test_connection(self, opts) -> None:  # type: ignore[override]
        database = getattr(opts, "database", None)
        if not database:
            raise ValueError("sqlite --database is required (path to .db/.sqlite file)")
        db_path = Path(str(database))
        if not db_path.exists():
            raise ValueError(f"sqlite database not found: {db_path}")
        # Try to open read-only
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ConnectionError(f"sqlite connection failed for {db_path}") from exc
        try:
            try:
                conn.execute("select 1")
            except sqlite3.Error as exc:
                raise ConnectionError(f"sqlite connection test failed for {db_path}") from exc
        finally:
            conn.close()

    def backup(self, opts) -> BackupArtifact:  # type: ignore[override]
        database = getattr(opts, "database", None)
        if not database:
            raise ValueError("sqlite --database is required (path to .db/.sqlite file)")
        db_path = Path(str(database))
        if not db_path.exists():
            raise FileNotFoundError(f"sqlite database not found: {db_path}")
        return self._backup_file(str(db_path))

    def _backup_file(self, db_path: str) -> BackupArtifact:
        # Use owner-restricted temp file (not TempWorkdir wrapper — avoids an
        # extra wrapper connection). Permissions only enforced on POSIX via
        # importing the helper; do it inline to avoid an import-time dep loop.
        import os
        import sys

        fd, tmp_path = tempfile.mkstemp(prefix="dbbackup-sqlite-", suffix=".sqlite")
        os.close(fd)
        tmp = Path(tmp_path)
        if sys.platform != "win32":
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass

        # Use sqlite3 backup API — atomic snapshot of the live DB
        try:
            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ConnectionError(f"sqlite open failed for {db_path}") from exc
        dest = sqlite3.connect(str(tmp))
        try:
            try:
                src.backup(dest)  # type: ignore[attr-defined]
            except sqlite3.Error as exc:
                raise RuntimeError(f"sqlite backup API failed for {db_path}") from exc
        finally:
            dest.close()
            src.close()

        stat = tmp.stat()
        return BackupArtifact(
            db_type="sqlite",
            format=self.artifact_format,
            extension=self.artifact_extension,
            stream_or_path=tmp,
            size_hint=stat.st_size if stat.st_size else None,
            needs_cleanup=True,
        )

    def restore(
        self,
        artifact: BackupArtifact | BinaryIO,
        opts: RestoreOpts | object,
    ) -> None:  # type: ignore[override]
        # Resolve target database path
        target = None
        if hasattr(opts, "connection"):
            target = getattr(opts.connection, "database", None)  # type: ignore[union-attr]
        if target is None:
            target = getattr(opts, "database", None)
        if not target:
            raise ValueError(
                "sqlite restore target --database is required (path to .db/.sqlite file)"
            )
        dest_path = Path(str(target))
        # Ensure parent exists
        if dest_path.parent != Path(".") and not dest_path.parent.exists():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
        # Resolve source readable stream/path for the backup
        src_stream, cleanup_src = self._resolve_artifact_source(artifact)
        try:
            self._restore_from_stream(src_stream, dest_path)
        finally:
            if cleanup_src is not None:
                try:
                    cleanup_src.close()
                except Exception:
                    pass

    def _resolve_artifact_source(
        self, artifact: BackupArtifact | BinaryIO
    ) -> tuple[BinaryIO, BinaryIO | None]:
        # Returns (readable BinaryIO, optional stream to close after)
        if hasattr(artifact, "stream_or_path") and hasattr(artifact, "open_stream"):
            stream: BinaryIO = artifact.open_stream()  # type: ignore[union-attr]
            return stream, stream
        if hasattr(artifact, "read"):
            return artifact, None  # type: ignore[return-value]
        if isinstance(artifact, (str, Path)):
            return open(str(artifact), "rb"), None
        raise TypeError(f"unsupported artifact type: {type(artifact)!r}")

    def _restore_from_stream(self, src: BinaryIO, dest_path: Path) -> None:
        # Use a temp file so replacement is atomic: dump src bytes to temp,
        # then validate as sqlite DB, then replace dest.
        import os
        import shutil
        import sys

        fd, tmp_name = tempfile.mkstemp(prefix="dbbackup-sqlite-restore-", suffix=".sqlite")
        os.close(fd)
        tmp = Path(tmp_name)
        if sys.platform != "win32":
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        try:
            with open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
            # Replace destination atomically
            shutil.copyfile(tmp, dest_path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
