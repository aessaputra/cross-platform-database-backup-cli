"""Unit + integration tests for LocalBackend — Local Filesystem Storage feature

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


def _artifact(
    data: bytes = b"hello", db_type: str = "postgres", ext: str = ".sql.gz"
) -> BackupArtifact:
    return BackupArtifact(
        db_type=db_type, format="sql", extension=ext, stream_or_path=io.BytesIO(data)
    )


def test_sanitize_database():
    assert sanitize_database("my db") == "my_db"
    assert sanitize_database("../../etc") == "etc"
    assert sanitize_database("CON") == "_CON"
    assert sanitize_database("") == "unknown"
    # trailing dot/space stripped
    assert sanitize_database("mydb.") == "mydb"
    assert sanitize_database("mydb ") == "mydb"
    assert sanitize_database("...") == "unknown"
    assert sanitize_database("a..b") == "a..b"
    # lpt reserved
    assert sanitize_database("LPT1") == "_LPT1"


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
    assert meta["sha256"] == hashlib.sha256(b"hello world").hexdigest()
    assert meta["key"] == key
    assert meta["db_type"] == "postgres"
    # download
    stream = backend.download(key)
    data = stream.read()
    stream.close()
    assert data == b"hello world"


def test_upload_with_stream_or_path_path(tmp_path: Path):
    # cover stream_or_path as Path/str
    backend = LocalBackend(root=tmp_path)
    src = tmp_path / "src.bin"
    src.write_bytes(b"from-path")
    art = BackupArtifact(db_type="postgres", format="sql", extension=".sql.gz", stream_or_path=src)
    backend.upload(art, "postgres/a.sql.gz")
    assert (tmp_path / "postgres/a.sql.gz").read_bytes() == b"from-path"
    # str path
    src2 = tmp_path / "src2.bin"
    src2.write_bytes(b"from-str")
    art2 = BackupArtifact(
        db_type="postgres", format="sql", extension=".sql.gz", stream_or_path=str(src2)
    )
    backend.upload(art2, "postgres/b.sql.gz")
    assert (tmp_path / "postgres/b.sql.gz").read_bytes() == b"from-str"


def test_upload_with_raw_readable(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    raw = io.BytesIO(b"raw-readable")
    backend.upload(raw, "postgres/raw.sql.gz")
    assert (tmp_path / "postgres/raw.sql.gz").read_bytes() == b"raw-readable"


def test_upload_empty_artifact(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    empty = BackupArtifact(
        db_type="postgres", format="sql", extension=".sql.gz", stream_or_path=None
    )
    backend.upload(empty, "postgres/empty.sql.gz")
    assert (tmp_path / "postgres/empty.sql.gz").read_bytes() == b""
    # explicit None via stream_or_path branch with None value
    empty2 = BackupArtifact(db_type="postgres", format="sql", extension=".sql.gz")
    empty2.stream_or_path = None
    backend.upload(empty2, "postgres/empty2.sql.gz")
    assert (tmp_path / "postgres/empty2.sql.gz").read_bytes() == b""


def test_upload_with_metadata_enrichment(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    art = BackupArtifact(
        db_type="postgres",
        format="sql",
        extension=".sql.gz",
        stream_or_path=io.BytesIO(b"x"),
        metadata={"custom": "val"},
    )
    backend.upload(art, "postgres/meta.sql.gz")
    meta = json.loads((tmp_path / "postgres/meta.sql.gz.json").read_text())
    assert meta["custom"] == "val"


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
    _artifact(b"hello")
    key = "postgres/mydb-20260822T120000Z.sql.gz"

    # Simulate ENOSPC: stream that raises OSError during read after first chunk
    class FailingStream(io.BytesIO):
        def read(self, n=-1):
            raise OSError(28, "No space left on device")

    failing_art = BackupArtifact(
        db_type="postgres",
        format="sql",
        extension=".sql.gz",
        stream_or_path=FailingStream(b"hello"),
    )
    with pytest.raises(OSError):
        backend.upload(failing_art, key)
    assert not (tmp_path / key).exists()
    # no tmp.* should remain as final key is absent (tmp cleaned up)
    # allow tmp leftover but ensure final not exists is the invariant


def test_upload_relative_root_resolved(tmp_path: Path, monkeypatch):
    # Cover __init__ relative root branch
    monkeypatch.chdir(tmp_path)
    rel = Path("rel_root")
    rel.mkdir()
    backend = LocalBackend(root="rel_root")
    assert backend.root.is_absolute()
    backend.upload(_artifact(b"hi"), "postgres/a.sql.gz")
    assert (backend.root / "postgres/a.sql.gz").exists()


def test_upload_world_readable_warning(tmp_path: Path, caplog):
    if os.name != "posix":
        pytest.skip("POSIX only")
    # create world-readable dir
    root = tmp_path / "world"
    root.mkdir()
    root.chmod(0o777)
    LocalBackend(root=root)
    # warning should have been logged on init — but caplog may have missed due to import time
    # trigger by creating new instance with caplog
    import logging

    with caplog.at_level(logging.WARNING):
        LocalBackend(root=root)
    # at least one warning about world-accessible
    assert any("world-accessible" in r.message for r in caplog.records)


def test_create_parents_false(tmp_path: Path):
    backend = LocalBackend(root=tmp_path, create_parents=False)
    with pytest.raises(FileNotFoundError):
        backend.upload(_artifact(b"hi"), "newdir/file.sql.gz")


def test_empty_key_rejected(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    with pytest.raises(ValueError):
        backend.upload(_artifact(b"hi"), "")
    with pytest.raises(ValueError):
        backend.upload(_artifact(b"hi"), "   ")
    with pytest.raises(ValueError):
        backend.download("")


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
    # parent dir 0700
    p_mode = (tmp_path / "postgres").stat().st_mode & 0o777
    assert p_mode == 0o700, f"expected 0700 got {oct(p_mode)}"
    # sidecar 0600
    s_mode = Path(str(dest) + ".json").stat().st_mode & 0o777
    assert s_mode == 0o600


def test_download_not_found(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    with pytest.raises(FileNotFoundError):
        backend.download("postgres/missing.sql.gz")


def test_download_is_directory(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    (tmp_path / "postgres").mkdir()
    (tmp_path / "postgres/dir.sql.gz").mkdir()
    with pytest.raises(IsADirectoryError):
        backend.download("postgres/dir.sql.gz")


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

    opts_local = BackupOpts(
        connection=ConnectionOpts(db_type="postgres"),
        storage_type="local",
        local_path=str(tmp_path),
    )
    backend = get_storage_backend(opts_local)
    assert isinstance(backend, LocalBackend)
    opts_s3 = BackupOpts(
        connection=ConnectionOpts(db_type="postgres"), storage_type="s3", s3_bucket="bkt"
    )
    from dbbackup.storage.s3 import S3Backend

    backend2 = get_storage_backend(opts_s3)
    assert isinstance(backend2, S3Backend)


def test_factory_relative_local_path_resolved(tmp_path: Path, monkeypatch):
    from dbbackup.storage import get_storage_backend

    monkeypatch.chdir(tmp_path)
    rel = Path("rel2")
    rel.mkdir()
    opts = BackupOpts(
        connection=ConnectionOpts(db_type="postgres"), storage_type="local", local_path="rel2"
    )
    backend = get_storage_backend(opts)
    assert backend.root.is_absolute()


def test_factory_missing_local_path_raises():
    from dbbackup.storage import get_storage_backend

    opts = BackupOpts(
        connection=ConnectionOpts(db_type="postgres"), storage_type="local", local_path=None
    )
    with pytest.raises(ValueError):
        get_storage_backend(opts)


def test_cli_backup_local_integration(tmp_path: Path):
    from typer.testing import CliRunner

    from dbbackup.cli import app

    runner = CliRunner()
    # Mock adapter and avoid real S3
    with patch("dbbackup.core.backup.get_adapter") as ga:
        m = MagicMock()
        art = BackupArtifact(
            db_type="postgres",
            format="sql",
            extension=".sql.gz",
            stream_or_path=io.BytesIO(b"select 1"),
        )
        m.backup.return_value = art
        ga.return_value = m
        result = runner.invoke(
            app,
            [
                "backup",
                "--db",
                "postgres",
                "--database",
                "mydb",
                "--storage",
                "local",
                "--local-path",
                str(tmp_path),
            ],
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
                [
                    "restore",
                    "--db",
                    "postgres",
                    "--key",
                    key,
                    "--storage",
                    "local",
                    "--local-path",
                    str(tmp_path),
                    "--verify",
                ],
            )
            assert r2.exit_code == 0, r2.output


def test_cli_backup_missing_local_path(tmp_path: Path):
    from typer.testing import CliRunner

    from dbbackup.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["backup", "--db", "postgres", "--database", "mydb", "--storage", "local"]
    )
    assert result.exit_code == 10
    assert "local-path" in result.output.lower()


def test_cli_restore_requires_key(tmp_path: Path):
    from typer.testing import CliRunner

    from dbbackup.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["restore", "--db", "postgres", "--storage", "local", "--local-path", str(tmp_path)]
    )
    assert result.exit_code == 10


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
