"""Regression tests for release-hardening fixes (S3-01, SEC-03, INT-01, CLI).

These tests cover the minimal fixes and do NOT expand scope to V2 features.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbbackup.storage.local import LocalBackend


def _artifact(data: bytes = b"x"):
    import gzip

    from dbbackup.models import BackupArtifact

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(data)
    buf.seek(0)
    return BackupArtifact(db_type="postgres", format="sql", extension=".sql.gz", stream_or_path=buf)


# S3-01: bucket required


def test_s3_missing_bucket_raises():
    from dbbackup.models import BackupOpts, ConnectionOpts
    from dbbackup.storage import get_storage_backend

    opts = BackupOpts(
        connection=ConnectionOpts(db_type="postgres"), storage_type="s3", s3_bucket=""
    )
    with pytest.raises(ValueError, match="bucket"):
        get_storage_backend(opts)


def test_s3_empty_bucket_raises():
    from dbbackup.models import BackupOpts, ConnectionOpts
    from dbbackup.storage import get_storage_backend

    opts = BackupOpts(
        connection=ConnectionOpts(db_type="postgres"), storage_type="s3", s3_bucket="   "
    )
    with pytest.raises(ValueError, match="bucket"):
        get_storage_backend(opts)


def test_s3_valid_bucket_ok():
    from unittest.mock import MagicMock, patch

    from dbbackup.models import BackupOpts, ConnectionOpts
    from dbbackup.storage import get_storage_backend
    from dbbackup.storage.s3 import S3Backend

    opts = BackupOpts(
        connection=ConnectionOpts(db_type="postgres"), storage_type="s3", s3_bucket="my-bucket"
    )
    with patch("dbbackup.storage.s3.boto3.client", return_value=MagicMock()):
        backend = get_storage_backend(opts)
        assert isinstance(backend, S3Backend)


def test_cli_s3_without_bucket_exits_10(tmp_path: Path):
    runner = CliRunner()
    from dbbackup.cli import app

    result = runner.invoke(
        app, ["backup", "--db", "postgres", "--database", "mydb", "--storage", "s3"]
    )
    assert result.exit_code == 10
    assert "bucket" in result.output.lower()


# SEC-03: Windows drive/UNC rejected on Linux


@pytest.mark.parametrize(
    "key",
    [
        "C:\\Windows\\System32\\evil",
        "C:/Windows/System32/evil",
        "\\\\server\\share\\file",
        "//server/share/file",
        "\\\\?\\C:\\evil",
    ],
)
def test_windows_absolute_keys_rejected(tmp_path: Path, key):
    backend = LocalBackend(root=tmp_path)
    with pytest.raises(ValueError):
        backend.upload(_artifact(b"hi"), key)


# INT-01: verify fail-closed


def test_verify_missing_sidecar_fails(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    backend.upload(_artifact(b"hello"), "postgres/a.sql.gz")
    sidecar = tmp_path / "postgres/a.sql.gz.json"
    sidecar.unlink()
    from unittest.mock import MagicMock, patch

    from dbbackup.core.restore import run_restore
    from dbbackup.models import ConnectionOpts, RestoreOpts

    opts = RestoreOpts(
        connection=ConnectionOpts(db_type="postgres", database="mydb"),
        s3_key="postgres/a.sql.gz",
        storage_type="local",
        local_path=str(tmp_path),
        verify=True,
    )
    with patch("dbbackup.core.restore.get_adapter", return_value=MagicMock()):
        result = run_restore(opts)
        assert result.status == "failed"
        assert "sidecar missing" in (result.error or "").lower()


def test_verify_malformed_sidecar_fails(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    backend.upload(_artifact(b"hello"), "postgres/a.sql.gz")
    (tmp_path / "postgres/a.sql.gz.json").write_text("{bad json")
    from unittest.mock import MagicMock, patch

    from dbbackup.core.restore import run_restore
    from dbbackup.models import ConnectionOpts, RestoreOpts

    opts = RestoreOpts(
        connection=ConnectionOpts(db_type="postgres", database="mydb"),
        s3_key="postgres/a.sql.gz",
        storage_type="local",
        local_path=str(tmp_path),
        verify=True,
    )
    with patch("dbbackup.core.restore.get_adapter", return_value=MagicMock()):
        result = run_restore(opts)
        assert result.status == "failed"
        assert "malformed" in (result.error or "").lower()


def test_verify_missing_sha256_fails(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    backend.upload(_artifact(b"hello"), "postgres/a.sql.gz")
    sidecar = tmp_path / "postgres/a.sql.gz.json"
    meta = json.loads(sidecar.read_text())
    del meta["sha256"]
    sidecar.write_text(json.dumps(meta))
    from unittest.mock import MagicMock, patch

    from dbbackup.core.restore import run_restore
    from dbbackup.models import ConnectionOpts, RestoreOpts

    opts = RestoreOpts(
        connection=ConnectionOpts(db_type="postgres", database="mydb"),
        s3_key="postgres/a.sql.gz",
        storage_type="local",
        local_path=str(tmp_path),
        verify=True,
    )
    with patch("dbbackup.core.restore.get_adapter", return_value=MagicMock()):
        result = run_restore(opts)
        assert result.status == "failed"
        assert "missing sha256" in (result.error or "").lower()


def test_verify_invalid_sha256_fails(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    backend.upload(_artifact(b"hello"), "postgres/a.sql.gz")
    sidecar = tmp_path / "postgres/a.sql.gz.json"
    meta = json.loads(sidecar.read_text())
    meta["sha256"] = "zzz"
    sidecar.write_text(json.dumps(meta))
    from unittest.mock import MagicMock, patch

    from dbbackup.core.restore import run_restore
    from dbbackup.models import ConnectionOpts, RestoreOpts

    opts = RestoreOpts(
        connection=ConnectionOpts(db_type="postgres", database="mydb"),
        s3_key="postgres/a.sql.gz",
        storage_type="local",
        local_path=str(tmp_path),
        verify=True,
    )
    with patch("dbbackup.core.restore.get_adapter", return_value=MagicMock()):
        result = run_restore(opts)
        assert result.status == "failed"
        assert "invalid sha256" in (result.error or "").lower()


def test_verify_correct_succeeds(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    backend.upload(_artifact(b"hello"), "postgres/a.sql.gz")
    from unittest.mock import MagicMock, patch

    from dbbackup.core.restore import run_restore
    from dbbackup.models import ConnectionOpts, RestoreOpts

    opts = RestoreOpts(
        connection=ConnectionOpts(db_type="postgres", database="mydb"),
        s3_key="postgres/a.sql.gz",
        storage_type="local",
        local_path=str(tmp_path),
        verify=True,
    )
    m = MagicMock()
    with patch("dbbackup.core.restore.get_adapter", return_value=m):
        result = run_restore(opts)
        assert result.status == "success"


def test_incomplete_publication_artifact_without_sidecar_fails_verify(tmp_path: Path):
    # Simulate crash between artifact publish and sidecar: artifact exists, sidecar removed
    backend = LocalBackend(root=tmp_path)
    backend.upload(_artifact(b"hello"), "postgres/a.sql.gz")
    (tmp_path / "postgres/a.sql.gz.json").unlink()
    from unittest.mock import MagicMock, patch

    from dbbackup.core.restore import run_restore
    from dbbackup.models import ConnectionOpts, RestoreOpts

    opts = RestoreOpts(
        connection=ConnectionOpts(db_type="postgres", database="mydb"),
        s3_key="postgres/a.sql.gz",
        storage_type="local",
        local_path=str(tmp_path),
        verify=True,
    )
    with patch("dbbackup.core.restore.get_adapter", return_value=MagicMock()):
        result = run_restore(opts)
        assert result.status == "failed"
