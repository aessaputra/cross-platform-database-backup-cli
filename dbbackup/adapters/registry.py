"""Adapter registry for DBAdapter resolution."""

from __future__ import annotations

from dbbackup.adapters.base import DBAdapter


def _load_registry() -> dict[str, type[DBAdapter]]:
    # Import lazily so submodules can import base without circular issues
    from dbbackup.adapters.mongo import MongoAdapter
    from dbbackup.adapters.mysql import MySQLAdapter
    from dbbackup.adapters.postgres import PostgresAdapter
    from dbbackup.adapters.sqlite import SQLiteAdapter

    return {
        "mysql": MySQLAdapter,
        "mongo": MongoAdapter,
        "postgres": PostgresAdapter,
        "sqlite": SQLiteAdapter,
    }


_REGISTRY: dict[str, type[DBAdapter]] | None = None


def _registry() -> dict[str, type[DBAdapter]]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_registry()
    return _REGISTRY


def get_adapter(db_type: str) -> DBAdapter:
    """Resolve and instantiate an adapter for *db_type* (case-insensitive).

    Raises:
        ValueError: when *db_type* is unknown.
    """
    key = db_type.lower()
    try:
        cls = _registry()[key]
    except KeyError:
        available = ", ".join(sorted(_registry().keys()))
        raise ValueError(f"unknown db_type '{db_type}'; available: {available}") from None
    return cls()
