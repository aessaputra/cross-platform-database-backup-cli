"""Unit + integration tests for LocalBackend — V1.x

Happy path, failure paths, traversal, permissions, factory.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dbbackup.models import BackupArtifact, BackupOpts, ConnectionOpts, RestoreOpts
from dbbackup.storage.local import LocalBackend, sanitize_database


def _artifact(data: bytes = b"hello", db_type: str = "postgres", ext: str = ".sql.gz") -> BackupArtifact:
    return BackupArtifact(db_type=db_type, format="sql", extension=ext, stream_or_path=io.BytesIO(data))


def test_sanitize_database():
    assert sanitize_database("my db") == "my_db"
    assert sanitize_database("../../etc") == "etc"
    assert sanitize_database("CON") == "_CON"
    assert sanitize_database("") == "unknown"


def test_upload_and_download_roundtrip(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    art = _artifact(b"hello world")
    key = "postgres/mydb-20260822T120000Z.sql.gz"
    backend.upload(art, key)
    dest = tmp_path / key
    assert dest.exists()
    # sidecar
    sidecar = Path(str(dest) + ".json")
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert "sha256" in meta
    assert meta["bytes"] == dest.stat().st_size
    # download
    stream = backend.download(key)
    data = stream.read()
    stream.close()
    assert data == b"hello world"


def test_upload_fail_if_exists(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    art = _artifact(b"hi")
    key = "postgres/mydb-20260822T120000Z.sql.gz"
    backend.upload(art, key)
    with pytest.raises(FileExistsError):
        backend.upload(_artifact(b"hi2"), key)
    # with force should succeed
    backend2 = LocalBackend(root=tmp_path, force=True)
    backend2.upload(_artifact(b"hi2"), key)
    stream = backend2.download(key)
    assert stream.read() == b"hi2"
    stream.close()


def test_upload_no_partial_on_failure(tmp_path: Path, monkeypatch):
    backend = LocalBackend(root=tmp_path)
    art = _artifact(b"hello")
    key = "postgres/mydb-20260822T120000Z.sql.gz"
    # Simulate ENOSPC: stream that raises OSError during read after first chunk
    class FailingStream(io.BytesIO):
        def read(self, n=-1):
            raise OSError(28, "No space left on device")

    failing_art = BackupArtifact(db_type="postgres", format="sql", extension=".sql.gz", stream_or_path=FailingStream(b"hello"))
    with pytest.raises(OSError):
        backend.upload(failing_art, key)
    assert not (tmp_path / key).exists()
    # no tmp.* should remain as final key is absent (tmp cleaned up)
    # allow tmp leftover but ensure final not exists is the invariant


def test_traversal_rejected(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    art = _artifact(b"hi")
    with pytest.raises((ValueError, FileNotFoundError)):
        backend.upload(art, "../../etc/passwd")
    with pytest.raises((ValueError, FileNotFoundError)):
        backend.download("../../etc/passwd")


def test_absolute_key_rejected(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    with pytest.raises(ValueError):
        backend.upload(_artifact(b"hi"), "/etc/passwd")


def test_permissions_posix(tmp_path: Path):
    if os.name != "posix":
        pytest.skip("POSIX only")
    backend = LocalBackend(root=tmp_path)
    art = _artifact(b"hi")
    key = "postgres/mydb-20260822T120000Z.sql.gz"
    backend.upload(art, key)
    dest = tmp_path / key
    mode = dest.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600 got {oct(mode)}"


def test_symlink_outside_root_rejected(tmp_path: Path):
    if os.name != "posix":
        pytest.skip("POSIX symlink test")
    backend = LocalBackend(root=tmp_path)
    # create a symlink inside root that points outside
    outside = tmp_path.parent / "outside_test"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not allowed")
    # key that resolves through symlink outside should be rejected
    # Our _resolve_key resolves symlink, so link/file would resolve outside
    art = _artifact(b"hi")
    # key starting with link/ should resolve outside and be rejected
    # On our impl, (root / "link/file").resolve() -> outside/file which is not relative_to root -> ValueError
    with pytest.raises(ValueError):
        backend.upload(art, "link/file.gz")


def test_factory_local_and_s3(tmp_path: Path):
    from dbbackup.storage import get_storage_backend

    opts_local = BackupOpts(connection=ConnectionOpts(db_type="postgres"), storage_type="local", local_path=str(tmp_path))
    backend = get_storage_backend(opts_local)
    assert isinstance(backend, LocalBackend)
    opts_s3 = BackupOpts(connection=ConnectionOpts(db_type="postgres"), storage_type="s3", s3_bucket="bkt")
    from dbbackup.storage.s3 import S3Backend

    backend2 = get_storage_backend(opts_s3)
    assert isinstance(backend2, S3Backend)


def test_factory_missing_local_path_raises():
    from dbbackup.storage import get_storage_backend

    opts = BackupOpts(connection=ConnectionOpts(db_type="postgres"), storage_type="local", local_path=None)
    with pytest.raises(ValueError):
        get_storage_backend(opts)


def test_cli_backup_local_integration(tmp_path: Path):
    from typer.testing import CliRunner
    from dbbackup.cli import app

    runner = CliRunner()
    # Mock adapter and avoid real S3
    with patch("dbbackup.core.backup.get_adapter") as ga:
        m = MagicMock()
        art = BackupArtifact(db_type="postgres", format="sql", extension=".sql.gz", stream_or_path=io.BytesIO(b"select 1"))
        m.backup.return_value = art
        ga.return_value = m
        result = runner.invoke(
            app,
            ["backup", "--db", "postgres", "--database", "mydb", "--storage", "local", "--local-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        # file should exist under tmp_path/postgres/mydb-*.sql.gz
        files = list((tmp_path / "postgres").glob("*.sql.gz"))
        assert len(files) == 1
        # restore via CLI with --key and --verify
        key = files[0].relative_to(tmp_path).as_posix()
        with patch("dbbackup.core.restore.get_adapter") as ga2:
            m2 = MagicMock()
            ga2.return_value = m2
            r2 = runner.invoke(
                app,
                ["restore", "--db", "postgres", "--key", key, "--storage", "local", "--local-path", str(tmp_path), "--verify"],
            )
            assert r2.exit_code == 0, r2.output


def test_restore_verify_mismatch(tmp_path: Path):
    # create a local backup then corrupt sidecar
    backend = LocalBackend(root=tmp_path)
    art = _artifact(b"hello")
    key = "postgres/mydb-20260822T120000Z.sql.gz"
    backend.upload(art, key)
    # corrupt sidecar sha
    sidecar = tmp_path / (key + ".json")
    meta = json.loads(sidecar.read_text())
    meta["sha256"] = "0" * 64
    sidecar.write_text(json.dumps(meta))
    # run restore with verify -> should fail
    from dbbackup.core.restore import run_restore

    opts = RestoreOpts(
        connection=ConnectionOpts(db_type="postgres", database="mydb"),
        s3_key=key,
        storage_type="local",
        local_path=str(tmp_path),
        verify=True,
    )
    # need to mock adapter restore but verify happens before
    with patch("dbbackup.core.restore.get_adapter") as ga:
        ga.return_value = MagicMock()
        result = run_restore(opts)
        assert result.status == "failed"
        assert "sha256" in (result.error or "").lower()
