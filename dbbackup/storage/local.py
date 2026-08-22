"""Local filesystem storage backend — V1.x permanent destination.

Layout: <root>/<db_type>/<database>-<timestamp><ext> + sidecar <artifact>.json
Atomicity: same-dir tmp + fsync(file) + os.replace + fsync(dir) (best-effort Windows).
Security: root jail via resolve/is_relative_to, sanitize database segment, 0700/0600 POSIX, fail-if-exists unless force.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from dbbackup.storage.base import StorageBackend

log = logging.getLogger(__name__)

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_database(name: str) -> str:
    """Sanitize database name to filesystem-safe segment."""
    if not name:
        return "unknown"
    s = _SANITIZE_RE.sub("_", name).strip("._")
    if not s:
        return "unknown"
    # Windows reserved names
    if s.lower() in _WINDOWS_RESERVED:
        s = f"_{s}"
    # trailing dot/space not allowed on Windows
    s = s.rstrip(". ")
    return s or "unknown"


class LocalBackend(StorageBackend):
    """Local filesystem backend with atomic upload and traversal jail."""

    def __init__(self, root: str | Path, *, create_parents: bool = True, force: bool = False) -> None:
        p = Path(root)
        if not p.is_absolute():
            # resolve relative to cwd at construction time
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
        self.root = p
        self.create_parents = create_parents
        self.force = force
        # Warn on world-readable root if it exists
        try:
            if self.root.exists() and os.name == "posix":
                mode = self.root.stat().st_mode
                if mode & 0o007:
                    log.warning("local storage root %s is world-accessible (mode %o)", self.root, mode & 0o777)
        except Exception:
            pass

    def _resolve_key(self, key: str) -> Path:
        if not key or key.strip() == "":
            raise ValueError("key must be non-empty")
        # reject absolute keys before join
        if Path(key).is_absolute():
            raise ValueError(f"key must be relative, got absolute: {key!r}")
        dest = (self.root / key).resolve()
        # jail check
        try:
            # Python 3.9+
            if not dest.is_relative_to(self.root):
                raise ValueError(f"key escapes storage root: {key!r} -> {dest}")
        except AttributeError:
            # fallback for <3.9
            try:
                dest.relative_to(self.root)
            except ValueError:
                raise ValueError(f"key escapes storage root: {key!r} -> {dest}")
        # also reject keys that after join still contain .. traversal (already caught via resolve, but be explicit)
        return dest

    def _ensure_parent(self, dest: Path) -> None:
        parent = dest.parent
        if parent.exists():
            return
        if not self.create_parents:
            raise FileNotFoundError(f"parent directory does not exist: {parent}")
        # create with 0700 on POSIX
        parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            try:
                os.chmod(parent, 0o700)
                # also ensure ancestors are 0700 if newly created — best effort
            except Exception:
                pass

    def upload(self, artifact, key: str) -> None:
        """Upload artifact to key atomically with sidecar."""
        dest = self._resolve_key(key)
        # fail-if-exists unless force
        if dest.exists() and not self.force:
            raise FileExistsError(f"destination already exists: {dest} (use --force to overwrite)")
        self._ensure_parent(dest)

        # tmp in same dir for atomic replace
        tmp_name = f".{dest.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}"
        tmp = dest.parent / tmp_name
        stream = None
        # flag to track if tmp was created (os.open path)
        tmp_created = False
        f = None
        try:
            # obtain source stream from artifact
            # artifact may be BackupArtifact with open_stream() or raw BinaryIO
            if hasattr(artifact, "open_stream"):
                stream = artifact.open_stream()
            elif hasattr(artifact, "stream_or_path"):
                s = artifact.stream_or_path
                if isinstance(s, (str, Path)):
                    stream = open(s, "rb")
                elif s is not None:
                    stream = s
                else:
                    stream = io.BytesIO(b"")
            elif hasattr(artifact, "read"):
                stream = artifact
            else:
                stream = io.BytesIO(b"")

            # write with sha256, 0600 on POSIX
            sha256 = hashlib.sha256()
            # open tmp with restricted perms
            if os.name == "posix":
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                tmp_created = True
                f = os.fdopen(fd, "wb")
            else:
                f = open(tmp, "wb")
                tmp_created = True
            try:
                # stream copy
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    sha256.update(chunk)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            finally:
                f.close()

            # ENOSPC/EACCES etc will propagate as OSError

            # fsync dir before replace (POSIX)
            if os.name == "posix":
                try:
                    dfd = os.open(dest.parent, os.O_DIRECTORY)
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
                except Exception:
                    pass

            # atomic replace
            os.replace(tmp, dest)

            # fsync dir after replace
            if os.name == "posix":
                try:
                    dfd = os.open(dest.parent, os.O_DIRECTORY)
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
                except Exception:
                    pass

            # enforce 0600 after replace (umask may have intervened on Windows path)
            if os.name == "posix":
                try:
                    os.chmod(dest, 0o600)
                except Exception:
                    pass

            # sidecar atomically
            sidecar = Path(str(dest) + ".json")
            sidecar_tmp = dest.parent / f".{sidecar.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}"
            meta = {
                "key": key,
                "db_type": getattr(artifact, "db_type", ""),
                "format": getattr(artifact, "format", ""),
                "extension": getattr(artifact, "extension", ""),
                "bytes": dest.stat().st_size,
                "sha256": sha256.hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            # enrich from artifact.metadata if present
            try:
                extra = getattr(artifact, "metadata", None)
                if isinstance(extra, dict):
                    meta.update({k: v for k, v in extra.items() if k not in meta})
            except Exception:
                pass
            with open(sidecar_tmp, "w", encoding="utf-8") as sf:
                json.dump(meta, sf, indent=2)
                sf.flush()
                try:
                    os.fsync(sf.fileno())
                except Exception:
                    pass
            os.replace(sidecar_tmp, sidecar)
            if os.name == "posix":
                try:
                    os.chmod(sidecar, 0o600)
                except Exception:
                    pass

        except FileExistsError:
            raise
        except Exception:
            # cleanup tmp on failure
            try:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        finally:
            # Do not close caller's stream — caller (backup.py / S3Backend) owns it.
            # Previous behavior closed stream which caused double-close / read-after-close.
            pass
            # ensure tmp not leaked on success path where replace already moved it
            # (tmp no longer exists after replace, so no-op)

    def download(self, key: str) -> BinaryIO:
        """Download key and return readable stream positioned at start.
        
        Rejects traversal and symlink-outside-root. Validates existence.
        """
        dest = self._resolve_key(key)
        # symlink check: if dest is symlink pointing outside root, _resolve_key already resolved, so jail holds.
        # But also need to check lstat vs resolve divergence
        if not dest.exists():
            raise FileNotFoundError(f"key not found: {key!r}")
        if not dest.is_file():
            raise IsADirectoryError(f"key is a directory: {key!r}")
        # Stream the file
        return open(dest, "rb")
