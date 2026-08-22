"""Storage backends — S3 + local with factory."""
from dbbackup.storage.base import StorageBackend
from dbbackup.storage.local import LocalBackend
from dbbackup.storage.s3 import S3Backend

__all__ = ["StorageBackend", "S3Backend", "LocalBackend", "get_storage_backend"]


def get_storage_backend(opts) -> StorageBackend:
    """Factory: opts.storage_type/local_path/s3_* -> StorageBackend.

    opts may be BackupOpts, RestoreOpts, or Config-like object.
    Defaults to S3 for backward compat.
    """
    storage_type = getattr(opts, "storage_type", None) or getattr(opts, "storage", None) or "s3"
    storage_type = str(storage_type).lower()
    if storage_type == "local":
        local_path = getattr(opts, "local_path", None)
        if not local_path:
            raise ValueError("local storage requires --local-path or [storage.local].path")
        from pathlib import Path

        p = Path(local_path)
        if not p.is_absolute():
            # allow relative but resolve; fail if empty
            p = (Path.cwd() / p).resolve()
        force = bool(getattr(opts, "force", False))
        return LocalBackend(root=p, force=force)
    # default s3
    bucket = getattr(opts, "s3_bucket", None) or getattr(opts, "bucket", None) or ""
    bucket = str(bucket).strip() if bucket is not None else ""
    if not bucket:
        raise ValueError("S3 bucket is required for storage_type='s3' (set --s3-bucket or [s3].bucket)")
    region = getattr(opts, "s3_region", None) or getattr(opts, "region", None)
    endpoint_url = getattr(opts, "s3_endpoint_url", None) or getattr(opts, "endpoint_url", None)
    # propagate local_path not needed
    return S3Backend(bucket=bucket, region=region, endpoint_url=endpoint_url)
