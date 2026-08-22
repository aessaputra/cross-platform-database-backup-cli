"""Mongo adapter — mongodump --archive streaming via subprocess.Popen."""

from __future__ import annotations

import subprocess
from typing import BinaryIO

from dbbackup.adapters._helpers import BinaryNotFoundError, require_binary
from dbbackup.adapters.base import DBAdapter
from dbbackup.models import BackupArtifact

__all__ = ["BinaryNotFoundError", "MongoAdapter"]


class MongoAdapter(DBAdapter):
    name = "mongo"
    artifact_format = "archive"
    artifact_extension = ".archive.gz"
    binary = "mongodump"

    def _check_binary(self) -> str:
        return require_binary(self.binary)

    def test_connection(self, opts) -> None:  # type: ignore[override]
        self._check_binary()

    def backup(self, opts) -> BackupArtifact:  # type: ignore[override]
        bin_path = self._check_binary()
        host = getattr(opts, "host", "") or "localhost"
        port = getattr(opts, "port", 0) or 27017
        database = getattr(opts, "database", "") or ""
        cmd = [bin_path, "--host", f"{host}:{port}", "--archive"]
        if database:
            cmd.extend(["--db", database])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None
        return BackupArtifact(
            db_type="mongo",
            format=self.artifact_format,
            extension=self.artifact_extension,
            stream_or_path=proc.stdout,  # type: ignore[arg-type]
            size_hint=None,
        )

    def restore(self, artifact: BackupArtifact | BinaryIO, opts) -> None:  # type: ignore[override]
        bin_path = require_binary("mongorestore")
        src = artifact.open_stream() if hasattr(artifact, "open_stream") else artifact  # type: ignore[union-attr]
        import shutil as _shutil

        proc = subprocess.Popen(
            [bin_path, "--archive"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin is not None
        _shutil.copyfileobj(src, proc.stdin)  # type: ignore[arg-type]
        proc.stdin.close()
        proc.wait()
        if proc.returncode not in (0, None):
            raise RuntimeError(f"mongorestore failed (exit {proc.returncode})")
