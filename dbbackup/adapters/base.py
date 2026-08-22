"""DBAdapter abstract base class.

Minimal adapter contract for database backup/restore. Each adapter determines
its own artifact representation (streaming vs temp-file) and declares its
``format``/``extension`` via the returned ``BackupArtifact``.

``list_targets()`` is intentionally excluded from the ABC per the design spec
(§1.4) — selective restore is handled per-adapter through ``RestoreOpts``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    from dbbackup.models import BackupArtifact, ConnectionOpts, RestoreOpts


class DBAdapter(ABC):
    """Abstract adapter for a specific database management system."""

    #: Human-readable adapter name, overridden by subclasses.
    name: str = "base"

    @abstractmethod
    def test_connection(self, opts: ConnectionOpts) -> None:
        """Verify connectivity/credentials for the target database.

        Should be lightweight (no dump/upload). Raises on failure with an
        actionable, secret-free error.
        """
        raise NotImplementedError

    @abstractmethod
    def backup(self, opts: ConnectionOpts) -> BackupArtifact:
        """Produce a full backup as a ``BackupArtifact``.

        The adapter does NOT take a destination stream; the orchestrator
        consumes the returned artifact uniformly (gzip -> S3).
        """
        raise NotImplementedError

    @abstractmethod
    def restore(
        self,
        artifact: BackupArtifact | BinaryIO,
        opts: RestoreOpts,
    ) -> None:
        """Restore from a backup ``artifact`` into the target described by ``opts``.

        Per-adapter interpretation of ``RestoreOpts`` (table/collection
        selection, target database, etc.). Raises an actionable error when a
        requested restore mode is unsupported.
        """
        raise NotImplementedError
