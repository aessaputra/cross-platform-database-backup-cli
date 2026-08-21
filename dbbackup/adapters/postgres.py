"""Postgres adapter — pg_dump streaming via subprocess.Popen."""
from __future__ import annotations

import shutil
import subprocess
from typing import BinaryIO, Union

from dbbackup.adapters._helpers import BinaryNotFoundError, require_binary
from dbbackup.adapters.base import DBAdapter
from dbbackup.models import BackupArtifact

__all__ = ["PostgresAdapter", "BinaryNotFoundError"]


class PostgresAdapter(DBAdapter):
    name = "postgres"
    artifact_format = "sql"
    artifact_extension = ".sql.gz"
    binary = "pg_dump"

    def _check_binary(self) -> str:
        return require_binary(self.binary)

    def test_connection(self, opts) -> None:  # type: ignore[override]
        self._check_binary()
        return None

    def backup(self, opts) -> BackupArtifact:  # type: ignore[override]
        bin_path = self._check_binary()
        host = getattr(opts, "host", "") or "localhost"
        port = getattr(opts, "port", 0) or 5432
        user = getattr(opts, "user", "") or ""
        database = getattr(opts, "database", "") or ""
        cmd = [bin_path, "-h", host, "-p", str(port)]
        if user:
            cmd.extend(["-U", user])
        if database:
            cmd.extend(["-d", database])
        # Plain SQL dump (custom format would be .dump.gz; plain is .sql.gz per spec)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: S603
        assert proc.stdout is not None
        return BackupArtifact(
            db_type="postgres",
            format=self.artifact_format,
            extension=self.artifact_extension,
            stream_or_path=proc.stdout,  # type: ignore[arg-type]
            size_hint=None,
        )

    def restore(self, artifact: "BackupArtifact | BinaryIO", opts) -> None:  # type: ignore[override]
        bin_path = require_binary("psql")
        src = artifact.open_stream() if hasattr(artifact, "open_stream") else artifact  # type: ignore[union-attr]
        import shutil as _shutil

        proc = subprocess.Popen([bin_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: S603
        assert proc.stdin is not None
        _shutil.copyfileobj(src, proc.stdin)  # type: ignore[arg-type]
        proc.stdin.close()
        proc.wait()
        if proc.returncode not in (0, None):
            raise RuntimeError(f"psql restore failed (exit {proc.returncode})")
