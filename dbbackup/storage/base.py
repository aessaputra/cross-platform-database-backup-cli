"""StorageBackend ABC — minimal upload + download only.

V1: S3 only. Local Filesystem Storage feature adds LocalBackend as second
backend. list/delete/exists remain out of scope until retention/listing
features exist — added together for both backends.
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
