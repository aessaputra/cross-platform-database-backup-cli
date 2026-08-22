"""Focused coverage for LocalBackend atomic-publish branches (EXDEV/EEXIST).

These tests mock os.link to exercise fallback paths that tmpfs never hits.
"""

from __future__ import annotations

import errno
import io
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from dbbackup.storage.local import LocalBackend


def _artifact(data: bytes = b"hi"):
    from dbbackup.models import BackupArtifact

    return BackupArtifact(
        db_type="postgres", format="sql", extension=".sql.gz", stream_or_path=io.BytesIO(data)
    )


def test_force_false_link_eexist_raises(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    backend.upload(_artifact(b"a"), "postgres/a.sql.gz")
    with patch("dbbackup.storage.local.os.link", side_effect=FileExistsError(17, "exists")):
        with pytest.raises(FileExistsError, match="already exists"):
            backend.upload(_artifact(b"b"), "postgres/b.sql.gz")
    with patch.object(Path, "exists", return_value=True):
        with pytest.raises(FileExistsError, match="already exists"):
            backend.upload(_artifact(b"c"), "postgres/c.sql.gz")


def test_force_false_exdev_fallback_copies(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)

    def fake_link(src, dst):
        raise OSError(errno.EXDEV, "cross-device")

    with patch("dbbackup.storage.local.os.link", side_effect=fake_link):
        backend.upload(_artifact(b"exdev-data"), "postgres/exdev.sql.gz")
    assert (tmp_path / "postgres/exdev.sql.gz").read_bytes() == b"exdev-data"


def test_force_false_exdev_eexist_raises(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)

    def fake_link(src, dst):
        raise OSError(errno.EXDEV, "cross-device")

    with patch("dbbackup.storage.local.os.link", side_effect=fake_link):
        orig_open = os.open

        def fake_open(path, flags, mode=0o777):
            if flags & os.O_EXCL:
                raise OSError(errno.EEXIST, "exists")
            return orig_open(path, flags, mode)

        with patch("dbbackup.storage.local.os.open", side_effect=fake_open):
            with pytest.raises(FileExistsError):
                backend.upload(_artifact(b"x"), "postgres/eexist.sql.gz")
            assert not (tmp_path / "postgres/eexist.sql.gz").exists()


def test_force_false_link_other_oserror_fallback_replace(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)

    def fake_link(src, dst):
        raise OSError(errno.EPERM, "perm")

    with patch("dbbackup.storage.local.os.link", side_effect=fake_link):
        with patch.object(Path, "exists", return_value=False):
            backend.upload(_artifact(b"other"), "postgres/other.sql.gz")


def test_force_false_link_other_oserror_dest_exists_raises(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)

    def fake_link(src, dst):
        raise OSError(errno.EPERM, "perm")

    with patch("dbbackup.storage.local.os.link", side_effect=fake_link):
        with patch.object(Path, "exists", return_value=True):
            with pytest.raises(FileExistsError, match="already exists"):
                backend.upload(_artifact(b"x"), "postgres/exists2.sql.gz")


def test_exdev_inner_enospc_raises_atomic(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)

    def fake_link(src, dst):
        raise OSError(errno.EXDEV, "cross-device")

    with patch("dbbackup.storage.local.os.link", side_effect=fake_link):
        orig_open = os.open

        def fake_open(path, flags, mode=0o777):
            if flags & os.O_EXCL:
                raise OSError(errno.ENOSPC, "no space")
            return orig_open(path, flags, mode)

        with patch("dbbackup.storage.local.os.open", side_effect=fake_open):
            with pytest.raises(FileExistsError, match="cannot be created atomically"):
                backend.upload(_artifact(b"x"), "postgres/enospc.sql.gz")


def test_exdev_copy_eexist_via_shutil(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)

    def fake_link(src, dst):
        raise OSError(errno.EXDEV, "cross-device")

    with patch("dbbackup.storage.local.os.link", side_effect=fake_link):
        with patch("shutil.copyfileobj", side_effect=OSError(errno.EEXIST, "exists")):
            with pytest.raises(FileExistsError):
                backend.upload(_artifact(b"x"), "postgres/shutil_eexist.sql.gz")


def test_world_readable_warning_branch(tmp_path: Path, caplog):
    import logging

    root = tmp_path / "wr"
    root.mkdir()
    root.chmod(0o777)
    with caplog.at_level(logging.WARNING):
        lb = LocalBackend(root=root)
        assert lb.root.exists()


def test_resolve_fallback_attribute_error(tmp_path: Path):
    backend = LocalBackend(root=tmp_path)
    with patch.object(Path, "is_relative_to", side_effect=AttributeError):
        with pytest.raises(ValueError, match="escapes storage root"):
            backend.upload(_artifact(b"hi"), "../../etc/passwd")
