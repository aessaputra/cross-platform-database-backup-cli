"""Owner-restricted temp workdir per OS.

On POSIX: directories 0700, files 0600.
On Windows: equivalent owner-only ACL via tempfile restricted inheritance
(no literal 0600 guarantee — platform-appropriate).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


class TempWorkdir:
    """Context-managed owner-restricted temporary directory."""

    def __init__(self, prefix: str = "dbbackup-") -> None:
        self._tmpdir: str | None = None
        self._path: Path | None = None
        self._prefix = prefix

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("TempWorkdir not entered")
        return self._path

    def __enter__(self) -> TempWorkdir:
        # tempfile.mkdtemp respects umask; force 0700 on POSIX
        self._tmpdir = tempfile.mkdtemp(prefix=self._prefix)
        self._path = Path(self._tmpdir)
        if sys.platform != "win32":
            try:
                os.chmod(self._tmpdir, 0o700)
            except OSError:
                pass
        return self

    def __exit__(self, *_args: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        if self._tmpdir and Path(self._tmpdir).exists():
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = None
        # keep _path for post-exit inspection (exists() will be False after rmtree)

    def temp_file(self, suffix: str = "", prefix: str = "tmp-") -> Path:
        """Create an owner-restricted temp file inside the workdir."""
        if self._path is None:
            raise RuntimeError("TempWorkdir not entered")
        fd, name = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=str(self._path))
        os.close(fd)
        p = Path(name)
        if sys.platform != "win32":
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        return p
