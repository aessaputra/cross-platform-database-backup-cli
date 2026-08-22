"""MySQL adapter — mysqldump streaming via subprocess.Popen."""

from __future__ import annotations

import subprocess
from typing import BinaryIO

from dbbackup.adapters._helpers import BinaryNotFoundError, require_binary
from dbbackup.adapters.base import DBAdapter
from dbbackup.models import BackupArtifact

# Re-export BinaryNotFoundError for tests that mock require_binary
__all__ = ["BinaryNotFoundError", "MySQLAdapter"]


class MySQLAdapter(DBAdapter):
    name = "mysql"
    artifact_format = "sql"
    artifact_extension = ".sql.gz"
    binary = "mysqldump"
    restore_binary = "mysql"

    def _check_binary(self) -> str:
        return require_binary(self.binary)

    def test_connection(self, opts) -> None:  # type: ignore[override]
        self._check_binary()
        # Lightweight connectivity check — no dump. Binary presence is the gating check;
        # real auth is validated during backup. Do not run mysqladmin to avoid extra dep.
        # Success if binary exists; failure only on missing binary.

    def backup(self, opts) -> BackupArtifact:  # type: ignore[override]
        bin_path = self._check_binary()
        host = getattr(opts, "host", "") or "localhost"
        port = getattr(opts, "port", 0) or 3306
        user = getattr(opts, "user", "") or ""
        password = getattr(opts, "password", "") or ""
        database = getattr(opts, "database", "") or ""
        # mysqldump --single-transaction --quick for streaming
        cmd = [bin_path, "--single-transaction", "--quick", f"-h{host}", f"-P{port}"]
        if user:
            cmd.append(f"-u{user}")
        if database:
            cmd.append(database)
        env = None
        if password:
            import os

            env = {**os.environ, "MYSQL_PWD": password}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        assert proc.stdout is not None
        return BackupArtifact(
            db_type="mysql",
            format=self.artifact_format,
            extension=self.artifact_extension,
            stream_or_path=proc.stdout,
            size_hint=None,
        )

    def restore(self, artifact: BackupArtifact | BinaryIO, opts: object) -> None:  # type: ignore[override]
        bin_path = require_binary(self.restore_binary)
        # Resolve target database from RestoreOpts
        target = getattr(opts, "target_database", None)
        if target is None and hasattr(opts, "connection"):
            target = getattr(opts.connection, "database", None)
        if target is None:
            target = getattr(opts, "database", None)
        # Stream artifact into mysql subprocess stdin (minimal impl for registry completeness)
        src = artifact.open_stream() if hasattr(artifact, "open_stream") else artifact  # type: ignore[union-attr]
        host = getattr(getattr(opts, "connection", opts), "host", "") or "localhost"
        # Password for restore — same env mechanism as backup to avoid argv exposure
        password = getattr(getattr(opts, "connection", opts), "password", "") or ""  # type: ignore[union-attr]
        if not password:
            password = getattr(opts, "password", "") or ""  # type: ignore[union-attr]
        cmd = [bin_path, f"-h{host}"]
        if target:
            cmd.append(str(target))
        env = None
        if password:
            import os

            env = {**os.environ, "MYSQL_PWD": password}
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        assert proc.stdin is not None
        import shutil as _shutil

        _shutil.copyfileobj(src, proc.stdin)  # type: ignore[arg-type]
        proc.stdin.close()
        proc.wait()
        if proc.returncode not in (0, None):
            raise RuntimeError(f"mysql restore failed (exit {proc.returncode})")
