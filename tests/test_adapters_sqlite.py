"""Tests for SQLite adapter and registry."""

import sqlite3
from pathlib import Path

import pytest

from dbbackup.adapters.registry import get_adapter
from dbbackup.models import ConnectionOpts, RestoreOpts


def _make_opts(database: str):
    return type(
        "O", (), {"database": database, "host": "", "port": 0, "user": "", "password": ""}
    )()


def test_sqlite_backup_creates_artifact(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("create table t(x int)")
    conn.execute("insert into t values (1)")
    conn.commit()
    conn.close()
    adapter = get_adapter("sqlite")
    artifact = adapter.backup(_make_opts(str(db)))
    try:
        assert artifact.extension == ".sqlite.gz" or ".sqlite" in artifact.extension
        assert artifact.db_type == "sqlite"
        assert artifact.format == "sqlite"
        assert artifact.needs_cleanup is True
        # stream_or_path should be a path
        assert isinstance(artifact.stream_or_path, (str, Path))
        # File should exist and be non-empty
        p = Path(str(artifact.stream_or_path))
        assert p.exists()
        assert p.stat().st_size > 0
        # open_stream should be readable and gzip decompressible after gzip stage?
        # SQLite artifact before gzip is a raw sqlite file; verify it's a valid sqlite DB
        src = sqlite3.connect(str(p))
        rows = list(src.execute("select * from t"))
        src.close()
        assert rows == [(1,)]
    finally:
        artifact.close()
    # After close, temp file should be cleaned
    assert not Path(str(artifact.stream_or_path)).exists()


def test_registry_unknown_raises():
    with pytest.raises(ValueError):
        get_adapter("unknown")


def test_registry_case_insensitive():
    adapter = get_adapter("SQLite")
    from dbbackup.adapters.sqlite import SQLiteAdapter

    assert isinstance(adapter, SQLiteAdapter)


def test_sqlite_test_connection_success(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("create table t(x int)")
    conn.commit()
    conn.close()
    adapter = get_adapter("sqlite")
    # Should not raise — uses ConnectionOpts-like object with database
    adapter.test_connection(_make_opts(str(db)))


def test_sqlite_test_connection_missing_file_raises():
    adapter = get_adapter("sqlite")
    opts = _make_opts("/nonexistent/path/db.sqlite")
    with pytest.raises(Exception):
        adapter.test_connection(opts)


def test_sqlite_backup_missing_file_raises(tmp_path):
    adapter = get_adapter("sqlite")
    with pytest.raises(Exception):
        adapter.backup(_make_opts(str(tmp_path / "nope.db")))


def test_sqlite_restore_roundtrip(tmp_path):
    # Create source DB
    src_db = tmp_path / "src.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("create table t(x int)")
    conn.execute("insert into t values (42)")
    conn.commit()
    conn.close()

    adapter = get_adapter("sqlite")
    artifact = adapter.backup(_make_opts(str(src_db)))
    try:
        # Restore into new DB
        dest_db = tmp_path / "dest.db"
        restore_opts = RestoreOpts(connection=ConnectionOpts(database=str(dest_db)))
        adapter.restore(artifact, restore_opts)
        # Verify dest
        conn2 = sqlite3.connect(str(dest_db))
        rows = list(conn2.execute("select * from t"))
        conn2.close()
        assert rows == [(42,)]
    finally:
        artifact.close()


def test_sqlite_artifact_context_manager(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("create table t(x int)")
    conn.commit()
    conn.close()
    adapter = get_adapter("sqlite")
    with adapter.backup(_make_opts(str(db))) as artifact:
        assert Path(str(artifact.stream_or_path)).exists()
    assert not Path(str(artifact.stream_or_path)).exists()


def test_sqlite_no_list_targets():
    """DBAdapter should not have list_targets."""
    from dbbackup.adapters.base import DBAdapter

    assert not hasattr(DBAdapter, "list_targets")
    adapter = get_adapter("sqlite")
    assert not hasattr(adapter, "list_targets")


def test_sqlite_artifact_extension_is_sqlite_gz(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("create table t(x int)")
    conn.commit()
    conn.close()
    adapter = get_adapter("sqlite")
    artifact = adapter.backup(_make_opts(str(db)))
    try:
        assert artifact.extension == ".sqlite.gz"
    finally:
        artifact.close()
