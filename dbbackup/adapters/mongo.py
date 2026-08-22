"""Mongo adapter — mongodump --archive streaming via subprocess.Popen.

Hardening: if ConnectionOpts.password is set, avoid `ps` visibility by
writing a temporary 0600 YAML config file for --config containing the
full --uri (with password) per MongoDB docs (recommended way aside from
interactive prompt). No MYSQL_PWD/PGPASSWORD env equivalent for mongo
tools exists generically — only AWS_* for MONGODB-AWS."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from dbbackup.adapters._helpers import BinaryNotFoundError, require_binary
from dbbackup.adapters.base import DBAdapter
from dbbackup.models import BackupArtifact

__all__ = ["BinaryNotFoundError", "MongoAdapter"]


def _build_mongo_uri(opts) -> str | None:
    host = getattr(opts, "host", "") or ""
    user = getattr(opts, "user", "") or ""
    password = getattr(opts, "password", "") or ""
    database = getattr(opts, "database", "") or ""
    extra = getattr(opts, "extra", {}) or {}
    if not host:
        return None
    port = getattr(opts, "port", 0) or 0
    # Detect SRV: single host, no port, looks like Atlas cluster
    is_srv = port == 0 and "," not in host and host.count(".") >= 2 and "mongodb.net" in host
    prefix = "mongodb+srv://" if is_srv else "mongodb://"
    # Build authority
    auth = ""
    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    # Host part: preserve comma-host as-is for replica set; otherwise append port if needed
    host_part = host
    if not is_srv and ":" not in host and port and "," not in host:
        host_part = f"{host}:{port}"
    # Database
    path = f"/{quote(database, safe='')}" if database else ""
    # Options from extra
    if extra:
        q = "&".join(f"{quote(k, safe='')}={quote(str(v), safe='')}" for k, v in extra.items())
        return f"{prefix}{auth}{host_part}{path}?{q}"
    return f"{prefix}{auth}{host_part}{path}"


def _config_yaml_for_uri(uri: str) -> str:
    # Minimal YAML — single key
    # Quote uri safely
    escaped = uri.replace("\\", "\\\\").replace('"', '\\"')
    return f'uri: "{escaped}"\n'


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
        password = getattr(opts, "password", "") or ""
        # If password present, use --config temp file to avoid ps leak (MongoDB docs recommended)
        if password:
            uri = _build_mongo_uri(opts)
            if uri:
                fd, cfg_path = tempfile.mkstemp(prefix="dbbackup-mongo-", suffix=".yaml")
                os.close(fd)
                Path(cfg_path).chmod(0o600)
                Path(cfg_path).write_text(_config_yaml_for_uri(uri), encoding="utf-8")
                try:
                    cmd = [bin_path, "--config", cfg_path, "--archive"]
                    if database:
                        cmd.extend(["--db", database])
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    assert proc.stdout is not None
                    # Wrap stdout to cleanup config on close
                    orig_stdout = proc.stdout
                    cfg = Path(cfg_path)

                    class _CfgCleanupWrapper:
                        wrapped_stdout = orig_stdout  # for test introspection

                        def __init__(self, stream, cfg_path: Path):
                            self._stream = stream
                            self._cfg = cfg_path

                        def __getattr__(self, name):
                            return getattr(self._stream, name)

                        def close(self):
                            try:
                                self._stream.close()
                            finally:
                                try:
                                    self._cfg.unlink(missing_ok=True)
                                except OSError:
                                    pass

                        def read(self, *a, **kw):
                            return self._stream.read(*a, **kw)

                    wrapped = _CfgCleanupWrapper(orig_stdout, cfg)
                    # expose for tests
                    wrapped.wrapped_stdout = orig_stdout  # type: ignore[attr-defined]
                    return BackupArtifact(
                        db_type="mongo",
                        format=self.artifact_format,
                        extension=self.artifact_extension,
                        stream_or_path=wrapped,  # type: ignore[arg-type]
                        size_hint=None,
                    )
                except Exception:
                    try:
                        Path(cfg_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
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

        # Try password via --config if present in opts.connection
        password = getattr(getattr(opts, "connection", opts), "password", "") or ""  # type: ignore[union-attr]
        if not password:
            password = getattr(opts, "password", "") or ""  # type: ignore[union-attr]
        if password:
            # Build uri from connection opts for restore as well
            conn = getattr(opts, "connection", opts)
            uri = _build_mongo_uri(conn)
            if uri:
                fd, cfg_path = tempfile.mkstemp(prefix="dbbackup-mongo-restore-", suffix=".yaml")
                os.close(fd)
                Path(cfg_path).chmod(0o600)
                Path(cfg_path).write_text(_config_yaml_for_uri(uri), encoding="utf-8")
                try:
                    proc = subprocess.Popen(
                        [bin_path, "--config", cfg_path, "--archive"],
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
                    return
                finally:
                    try:
                        Path(cfg_path).unlink(missing_ok=True)
                    except OSError:
                        pass
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
