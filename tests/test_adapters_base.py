"""Tests for DBAdapter ABC.

The ABC should not be instantiated directly; adapters inherit from it.
"""

from __future__ import annotations

import pytest

from dbbackup.adapters.base import DBAdapter
from dbbackup.models import BackupArtifact


class DummyAdapter(DBAdapter):
    """Minimal concrete adapter for testing the ABC contract."""

    def test_connection(self, opts) -> None:
        """Test connection to the database."""

    def backup(self, opts) -> BackupArtifact:
        """Create a backup artifact."""
        return BackupArtifact(
            db_type="dummy",
            format="dummy",
            extension=".dummy",
            stream_or_path=None,
        )

    def restore(self, artifact: BackupArtifact | bytes, opts) -> None:
        """Restore from artifact."""


def test_dbadapter_is_abstract() -> None:
    """DBAdapter should be abstract and not instantiable directly."""
    with pytest.raises(TypeError):
        DBAdapter()  # type: ignore[abstract]


def test_dbadapter_can_be_subclassed() -> None:
    """DBAdapter can be subclassed with required methods implemented."""
    adapter = DummyAdapter()
    assert isinstance(adapter, DBAdapter)


def test_test_connection_exists() -> None:
    """test_connection method exists and can be called."""
    adapter = DummyAdapter()
    opts = type(
        "O",
        (),
        {"host": "localhost", "port": 5432, "user": "test", "password": "", "database": "testdb"},
    )()
    # Should not raise
    adapter.test_connection(opts)


def test_backup_returns_artifact() -> None:
    """backup() returns a BackupArtifact with correct metadata."""
    adapter = DummyAdapter()
    opts = type(
        "O", (), {"database": "testdb", "host": "", "port": 0, "user": "", "password": ""}
    )()
    artifact = adapter.backup(opts)
    assert isinstance(artifact, BackupArtifact)
    assert artifact.db_type == "dummy"
    assert artifact.format == "dummy"
    assert artifact.extension == ".dummy"


def test_restore_exists() -> None:
    """restore() method exists and can be called."""
    adapter = DummyAdapter()
    artifact = BackupArtifact(
        db_type="dummy",
        format="dummy",
        extension=".dummy",
        stream_or_path=None,
    )
    opts = type(
        "O",
        (),
        {"host": "localhost", "port": 5432, "user": "test", "password": "", "database": "testdb"},
    )()
    # Should not raise
    adapter.restore(artifact, opts)
