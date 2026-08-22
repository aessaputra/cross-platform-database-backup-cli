"""Shared helpers for streaming adapters."""

from __future__ import annotations

import shutil


class BinaryNotFoundError(FileNotFoundError):
    """Required external binary not found on PATH."""

    def __init__(self, binary: str, hint: str) -> None:
        super().__init__(f"{binary} not found on PATH. {hint}")
        self.binary = binary
        self.hint = hint


def require_binary(binary: str) -> str:
    """Return binary path via shutil.which or raise BinaryNotFoundError with per-OS hints."""
    found = shutil.which(binary)
    if found:
        return found
    hint = (
        f"Install {binary}: "
        f"apt: sudo apt install {binary} (or appropriate package); "
        f"brew: brew install {binary}; "
        f"choco: choco install {binary}. "
        f"Ensure {binary} is on PATH (Windows: add install dir to PATH, e.g. "
        f"C:\\Program Files\\{binary}\\bin)."
    )
    # Tailor package names slightly
    mapping = {
        "mysqldump": "mysql-client",
        "pg_dump": "postgresql-client",
        "mongodump": "mongodb-database-tools",
    }
    pkg = mapping.get(binary, binary)
    hint_pkg = hint.replace(binary, pkg, 1) if pkg != binary else hint
    # Keep binary name visible
    if binary not in hint_pkg:
        hint_pkg = f"{binary}: " + hint_pkg
    raise BinaryNotFoundError(binary, hint_pkg)
