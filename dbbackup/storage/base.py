"""StorageBackend ABC — minimal upload + download only (v1).

list/delete excluded in v1 per spec §1.5 / Task 4.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageBackend(ABC):
    """Minimal storage backend interface."""

    @abstractmethod
    def upload(self, artifact, key: str) -> None:
        """Upload *artifact* to *key*."""

    @abstractmethod
    def download(self, key: str) -> BinaryIO:
        """Download *key* and return a readable stream positioned at start."""
