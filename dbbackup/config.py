"""Layered TOML config — stdlib tomllib + platformdirs, no tomli runtime dep.

Resolution order: defaults < user TOML < project TOML < env DBBACKUP_* < CLI flags.
Plaintext passwords in TOML require allow_plaintext_password=true or warning is emitted.
S3 block in TOML limited to bucket/prefix/region/endpoint_url (no secrets in TOML).
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import platformdirs

log = logging.getLogger(__name__)


def _user_config_path() -> Path:
    """Source of truth for user config dir — platformdirs on all OSes.

    Linux convention ~/.config/dbbackup is just the platformdirs result on Linux,
    not a universal physical path.
    """
    base = Path(platformdirs.user_config_dir("dbbackup"))
    return base / "config.toml"


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("failed to read TOML %s: %s", path, e)
        return {}


@dataclass
class Config:
    database: str = ""
    config: str | None = None
    host: str = ""
    port: int = 0
    password: str = ""
    # Local Filesystem Storage feature
    storage_type: str = "s3"
    local_path: str | None = None


def load_config(cli_args: dict | None = None, *, project_toml: str | Path | None = None) -> Config:
    """Load config with layered merge.

    cli_args: flat dict from CLI (e.g. {"database": "mydb", "config": "/path/to.toml"})
    project_toml: explicit project TOML path for testing; defaults to ./dbbackup.toml
    """
    cli_args = cli_args or {}
    merged: dict = {}

    # 1. user TOML via platformdirs
    user_data = _read_toml(_user_config_path())
    merged.update(user_data.get("connection", {}))
    merged.update({k: v for k, v in user_data.items() if k not in ("connection", "s3")})

    # 2. project TOML (./dbbackup.toml or explicit)
    proj_path = Path(project_toml) if project_toml else Path.cwd() / "dbbackup.toml"
    # also honour cli_args["config"] as alternate project TOML path
    cfg_path_arg = cli_args.get("config")
    if cfg_path_arg:
        proj_path = Path(cfg_path_arg)
    proj_data = _read_toml(proj_path)
    # plaintext password check for any TOML that was read
    for src_name, src_data in [("user", user_data), ("project", proj_data)]:
        conn = src_data.get("connection", {}) if isinstance(src_data, dict) else {}
        if isinstance(conn, dict) and "password" in conn and conn.get("password"):
            allow = proj_data.get("allow_plaintext_password") or user_data.get("allow_plaintext_password")
            # also check flat key
            if not allow:
                # check nested connection.allow_plaintext_password
                allow = conn.get("allow_plaintext_password")
            if not allow:
                log.warning("plaintext password in %s TOML — set allow_plaintext_password=true to suppress; prefer env/secret mechanisms", src_name)

    merged.update(proj_data.get("connection", {}))
    merged.update({k: v for k, v in proj_data.items() if k not in ("connection", "s3", "storage", "allow_plaintext_password")})
    # storage block: [storage] type, [storage.local] path
    _storage = proj_data.get("storage", {}) if isinstance(proj_data.get("storage"), dict) else {}
    if isinstance(_storage, dict):
        if "type" in _storage:
            merged["storage_type"] = str(_storage["type"]).lower()
        # also honour per-user storage block if not overwritten by project
        if "local" in _storage and isinstance(_storage["local"], dict) and "path" in _storage["local"]:
            merged["local_path"] = str(_storage["local"]["path"])
    # user-level storage fallback if not set by project
    _u_storage = user_data.get("storage", {}) if isinstance(user_data.get("storage"), dict) else {}
    if isinstance(_u_storage, dict) and "storage_type" not in merged:
        if "type" in _u_storage:
            merged["storage_type"] = str(_u_storage["type"]).lower()
    if isinstance(_u_storage, dict) and "local_path" not in merged:
        if "local" in _u_storage and isinstance(_u_storage["local"], dict) and "path" in _u_storage["local"]:
            merged["local_path"] = str(_u_storage["local"]["path"])

    # 3. env DBBACKUP_*
    for env_key, cfg_key in [("DBBACKUP_DATABASE", "database"), ("DBBACKUP_HOST", "host"), ("DBBACKUP_PORT", "port"), ("DBBACKUP_STORAGE_TYPE", "storage_type"), ("DBBACKUP_LOCAL_PATH", "local_path")]:
        if env_key in os.environ:
            merged[cfg_key] = os.environ[env_key]
            if cfg_key == "port":
                try:
                    merged[cfg_key] = int(merged[cfg_key])
                except ValueError:
                    pass

    # 4. CLI flags highest priority
    for k, v in cli_args.items():
        if k == "config":
            continue
        if v is not None and v != "":
            merged[k] = v

    # Build Config
    # normalize storage_type
    st = str(merged.get("storage_type", "s3")).lower()
    if st not in ("s3", "local"):
        st = "s3"
    return Config(
        database=str(merged.get("database", "")),
        config=str(cfg_path_arg) if cfg_path_arg else None,
        host=str(merged.get("host", "")),
        port=int(merged.get("port", 0) or 0),
        password=str(merged.get("password", "")),
        storage_type=st,
        local_path=str(merged.get("local_path")) if merged.get("local_path") else None,
    )
