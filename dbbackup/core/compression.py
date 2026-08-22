"""Streaming gzip compression helpers (level 1-9)."""

from __future__ import annotations

import gzip
import shutil
from typing import BinaryIO

_CHUNK = 64 * 1024


def _validate_level(level: int) -> None:
    if not 1 <= level <= 9:
        raise ValueError(f"gzip level must be 1-9, got {level}")


def compress_stream(src: BinaryIO, dest: BinaryIO, level: int = 6) -> int:
    """Stream-compress *src* into *dest* using gzip at *level* (1-9).

    Returns number of compressed bytes written.
    """
    _validate_level(level)
    # Use GzipFile streaming so we don't buffer entire input
    # dest is assumed writable binary
    start = dest.tell() if hasattr(dest, "tell") else 0
    with gzip.GzipFile(fileobj=dest, mode="wb", compresslevel=level) as gz:
        shutil.copyfileobj(src, gz, length=_CHUNK)
    if hasattr(dest, "tell"):
        return dest.tell() - start
    return 0


def decompress_stream(src: BinaryIO, dest: BinaryIO) -> int:
    """Stream-decompress gzip *src* into *dest*.

    Returns number of decompressed bytes written.
    """
    start = dest.tell() if hasattr(dest, "tell") else 0
    with gzip.GzipFile(fileobj=src, mode="rb") as gz:
        shutil.copyfileobj(gz, dest, length=_CHUNK)
    if hasattr(dest, "tell"):
        return dest.tell() - start
    return 0


# Alias required by brief name
def gzip_stream(src: BinaryIO, dest: BinaryIO, level: int = 6) -> int:
    return compress_stream(src, dest, level=level)
