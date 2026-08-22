"""Typer CLI — full-only backups to S3 or local filesystem. Scheduled storage selection via TOML [[schedule.jobs]]."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from dbbackup.__version__ import __version__
from dbbackup.adapters._helpers import BinaryNotFoundError
from dbbackup.adapters.registry import get_adapter
from dbbackup.core.redact import redact
from dbbackup.models import BackupOpts, ConnectionOpts, RestoreOpts

app = typer.Typer(
    name="dbbackup",
    help="Cross-platform database backup CLI — full backups only in v1 (incremental/differential reserved for v2).",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"dbbackup {__version__}")
        raise typer.Exit(code=0)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    _ = ctx
    _ = version


def _resolve_password(
    password: str | None,
    password_env: str | None,
    password_stdin: bool,
    ask_password: bool,
) -> str:
    if password_env:
        val = os.environ.get(password_env, "")
        if val:
            return val
        # if env var not set, fall through; CLI will error if still empty and required
    if password_stdin:
        data = sys.stdin.read()
        return data.strip().splitlines()[0] if data.strip() else ""
    if ask_password:
        return getpass.getpass("Database password: ")
    if password is not None and password != "":
        return password
    # no password flag -> prompt interactively if tty, else empty
    if sys.stdin.isatty():
        try:
            return getpass.getpass("Database password (leave empty if none): ")
        except Exception:
            return ""
    return password or ""


def _password_options():
    return [
        typer.Option(
            None,
            "--password",
            help="Database password (discouraged; prefer --password-env/--ask-password)",
        ),
        typer.Option(
            None,
            "--password-env",
            help="Env var name holding password (preferred: DBBACKUP_PASSWORD)",
        ),
        typer.Option(False, "--password-stdin", help="Read password from stdin (first line)"),
        typer.Option(False, "--ask-password", help="Prompt for password interactively"),
    ]


@app.command("backup")
def backup(
    db: str = typer.Option(
        ..., "--db", help="Database type: mysql | postgres | mongo | sqlite (full backup only)"
    ),
    host: str = typer.Option("", "--host", help="Database host"),
    port: int = typer.Option(0, "--port", help="Database port"),
    user: str = typer.Option("", "--user", help="Database user"),
    database: str = typer.Option("", "--database", help="Database name"),
    password: str | None = typer.Option(None, "--password", help="Database password (discouraged)"),
    password_env: str | None = typer.Option(
        None, "--password-env", help="Env var holding password"
    ),
    password_stdin: bool = typer.Option(False, "--password-stdin", help="Read password from stdin"),
    ask_password: bool = typer.Option(False, "--ask-password", help="Prompt for password"),
    s3_bucket: str = typer.Option("", "--s3-bucket", help="S3 bucket (for --storage s3)"),
    s3_prefix: str = typer.Option("", "--s3-prefix", help="S3 key prefix"),
    s3_endpoint_url: str | None = typer.Option(
        None, "--s3-endpoint-url", help="S3-compatible endpoint URL (MinIO)"
    ),
    s3_region: str | None = typer.Option(None, "--s3-region", help="S3 region"),
    gzip_level: int = typer.Option(6, "--gzip-level", help="gzip compression level 1-9"),
    config: str | None = typer.Option(None, "--config", help="Path to TOML config file"),
    storage: str | None = typer.Option(
        None, "--storage", help="Storage type: s3 | local (default: s3 or TOML [storage].type)"
    ),
    local_path: str | None = typer.Option(
        None, "--local-path", help="Local storage root path (for --storage local)"
    ),
    force: bool = typer.Option(
        False, "--force", help="Allow overwriting existing backup at destination key"
    ),
) -> None:
    """Run a full backup to S3 or local filesystem (v1: full only)."""
    # resolve storage from TOML/env if CLI not given
    cli_storage = storage
    cli_local_path = local_path
    if not cli_storage:
        try:
            from dbbackup.config import load_config as _load_config

            cfg = _load_config({"config": config} if config else {})
            if cfg.storage_type:
                cli_storage = cfg.storage_type
            if cfg.local_path and not cli_local_path:
                cli_local_path = cfg.local_path
        except Exception:
            pass
    storage_type = (cli_storage or "s3").lower()
    if storage_type not in ("s3", "local"):
        err_console.print(
            f"[red]invalid --storage: {redact(storage_type)} (expected s3|local)[/red]"
        )
        raise typer.Exit(code=10)
    if storage_type == "local" and not cli_local_path:
        err_console.print(
            "[red]--storage local requires --local-path or [storage.local].path in TOML[/red]"
        )
        raise typer.Exit(code=10)
    if storage_type == "s3" and not s3_bucket.strip():
        err_console.print(
            "[red]S3 bucket is required for --storage s3 (set --s3-bucket or [s3].bucket)[/red]"
        )
        raise typer.Exit(code=10)
    pw = _resolve_password(password, password_env, password_stdin, ask_password)
    conn = ConnectionOpts(
        db_type=db, host=host, port=port, user=user, password=pw, database=database
    )
    opts = BackupOpts(
        connection=conn,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        s3_endpoint_url=s3_endpoint_url,
        s3_region=s3_region,
        gzip_level=gzip_level,
        config=config,
        storage_type=storage_type,
        local_path=cli_local_path,
        force=force,
    )
    from dbbackup.core.backup import run_backup

    try:
        result = run_backup(opts)
    except BinaryNotFoundError as exc:
        err_console.print(f"[red]binary missing: {redact(str(exc))}[/red]")
        raise typer.Exit(code=11)
    except Exception as exc:
        err_console.print(f"[red]backup failed: {redact(str(exc))}[/red]")
        raise typer.Exit(code=13)
    if result.status == "success":
        console.print(f"[green]backup ok[/green] {result.s3_key} ({result.bytes_written} bytes)")
        raise typer.Exit(code=0)
    if result.status == "interrupted":
        err_console.print(f"[yellow]interrupted: {redact(result.error or '')}[/yellow]")
        raise typer.Exit(code=14)
    # failed
    err_console.print(f"[red]backup failed: {redact(result.error or 'unknown')}[/red]")
    # BinaryNotFound maps to 11, connection to 12, else 13
    if result.error and "not found on PATH" in result.error:
        raise typer.Exit(code=11)
    if result.error and "connection" in result.error.lower():
        raise typer.Exit(code=12)
    raise typer.Exit(code=13)


@app.command("restore")
def restore(
    db: str = typer.Option(..., "--db", help="Database type: mysql | postgres | mongo | sqlite"),
    s3_key: str | None = typer.Option(
        None, "--s3-key", help="S3 key of the backup to restore (alias: --key)"
    ),
    key: str | None = typer.Option(
        None, "--key", help="Backup key to restore (preferred; --s3-key is alias)"
    ),
    target_db: str | None = typer.Option(None, "--target-db", help="Target database name"),
    table: list[str] = typer.Option(
        None, "--table", help="Table to restore (repeatable; mysql/postgres)"
    ),
    collection: list[str] = typer.Option(
        None, "--collection", help="Collection to restore (repeatable; mongo)"
    ),
    host: str = typer.Option("", "--host", help="Database host"),
    port: int = typer.Option(0, "--port", help="Database port"),
    user: str = typer.Option("", "--user", help="Database user"),
    database: str = typer.Option("", "--database", help="Database name"),
    password: str | None = typer.Option(None, "--password", help="Database password"),
    password_env: str | None = typer.Option(
        None, "--password-env", help="Env var holding password"
    ),
    password_stdin: bool = typer.Option(False, "--password-stdin", help="Read password from stdin"),
    ask_password: bool = typer.Option(False, "--ask-password", help="Prompt for password"),
    config: str | None = typer.Option(None, "--config", help="Path to TOML config file"),
    s3_bucket: str = typer.Option("", "--s3-bucket", help="S3 bucket holding backup"),
    s3_endpoint_url: str | None = typer.Option(None, "--s3-endpoint-url", help="S3 endpoint URL"),
    s3_region: str | None = typer.Option(None, "--s3-region", help="S3 region"),
    storage: str | None = typer.Option(None, "--storage", help="Storage type: s3 | local"),
    local_path: str | None = typer.Option(None, "--local-path", help="Local storage root path"),
    verify: bool = typer.Option(
        False, "--verify", help="Verify SHA-256 sidecar before restore (local only)"
    ),
) -> None:
    """Restore a full backup from S3 or local filesystem. Selective --table/--collection per adapter."""
    effective = key or s3_key
    if not effective:
        err_console.print("[red]restore requires --key (or --s3-key)[/red]")
        raise typer.Exit(code=10)
    pw = _resolve_password(password, password_env, password_stdin, ask_password)
    conn = ConnectionOpts(
        db_type=db, host=host, port=port, user=user, password=pw, database=database
    )
    # resolve storage default from TOML if not given
    cli_storage = storage
    cli_local_path = local_path
    if not cli_storage:
        try:
            from dbbackup.config import load_config as _load_config

            cfg = _load_config({"config": config} if config else {})
            if cfg.storage_type:
                cli_storage = cfg.storage_type
            if cfg.local_path and not cli_local_path:
                cli_local_path = cfg.local_path
        except Exception:
            pass
    storage_type = (cli_storage or "s3").lower()
    opts = RestoreOpts(
        connection=conn,
        s3_key=effective or "",
        key=key,
        target_database=target_db,
        tables=list(table or []),
        collections=list(collection or []),
        storage_type=storage_type,
        local_path=cli_local_path,
        verify=verify,
    )
    # attach s3 bucket for restore if provided
    if s3_bucket:
        opts.s3_bucket = s3_bucket
    if s3_endpoint_url:
        opts.s3_endpoint_url = s3_endpoint_url
    if s3_region:
        opts.s3_region = s3_region
    from dbbackup.core.restore import run_restore

    try:
        result = run_restore(opts)
    except BinaryNotFoundError as exc:
        err_console.print(f"[red]binary missing: {redact(str(exc))}[/red]")
        raise typer.Exit(code=11)
    except Exception as exc:
        err_console.print(f"[red]restore failed: {redact(str(exc))}[/red]")
        raise typer.Exit(code=13)
    if result.status == "success":
        console.print("[green]restore ok[/green]")
        raise typer.Exit(code=0)
    if result.status == "interrupted":
        err_console.print(f"[yellow]interrupted: {redact(result.error or '')}[/yellow]")
        raise typer.Exit(code=14)
    err_console.print(f"[red]restore failed: {redact(result.error or 'unknown')}[/red]")
    raise typer.Exit(code=13)


@app.command("test-connection")
def test_connection(
    db: str = typer.Option(..., "--db", help="Database type: mysql | postgres | mongo | sqlite"),
    host: str = typer.Option("", "--host", help="Database host"),
    port: int = typer.Option(0, "--port", help="Database port"),
    user: str = typer.Option("", "--user", help="Database user"),
    database: str = typer.Option("", "--database", help="Database name"),
    password: str | None = typer.Option(None, "--password", help="Database password"),
    password_env: str | None = typer.Option(
        None, "--password-env", help="Env var holding password"
    ),
    password_stdin: bool = typer.Option(False, "--password-stdin", help="Read password from stdin"),
    ask_password: bool = typer.Option(False, "--ask-password", help="Prompt for password"),
    config: str | None = typer.Option(None, "--config", help="Path to TOML config file"),
) -> None:
    """Test database connectivity and required binaries (no backup performed)."""
    pw = _resolve_password(password, password_env, password_stdin, ask_password)
    conn = ConnectionOpts(
        db_type=db, host=host, port=port, user=user, password=pw, database=database
    )
    try:
        adapter = get_adapter(db)
        adapter.test_connection(conn)
    except BinaryNotFoundError as exc:
        err_console.print(f"[red]binary missing: {redact(str(exc))}[/red]")
        raise typer.Exit(code=11)
    except Exception as exc:
        err_console.print(f"[red]connection failed: {redact(str(exc))}[/red]")
        raise typer.Exit(code=12)
    console.print("[green]connection ok[/green]")
    raise typer.Exit(code=0)


@app.command("schedule")
def schedule(
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Run scheduler daemon (loads jobs from TOML at startup; storage per [[schedule.jobs]] via [storage] in TOML)",
    ),
    config: str | None = typer.Option(None, "--config", help="Path to TOML config file"),
) -> None:
    """Run scheduled full backups via daemon (full only; jobs from TOML at startup; storage per-job via [[schedule.jobs]] storage/local_path or global [storage])."""
    if not daemon:
        err_console.print("[yellow]schedule requires --daemon in v1[/yellow]")
        console.print("Usage: dbbackup schedule --daemon [--config path]")
        raise typer.Exit(code=10)
    # Load TOML config (layered) — use config.load_config for jobs
    import tomllib

    cfg_path = Path(config) if config else None
    # load raw TOML for schedule.jobs
    raw: dict = {}
    if cfg_path and cfg_path.exists():
        with open(cfg_path, "rb") as f:
            raw = tomllib.load(f)
    else:
        # try default location via platformdirs + ./dbbackup.toml
        from dbbackup.config import _user_config_path

        for p in [_user_config_path(), Path.cwd() / "dbbackup.toml"]:
            if p.exists():
                with open(p, "rb") as f:
                    raw = tomllib.load(f)
                break
    from dbbackup.core.scheduler import start_scheduler

    daemon_obj = start_scheduler(raw)
    # block until shutdown signal
    code = daemon_obj.wait_for_shutdown()
    raise typer.Exit(code=code)


if __name__ == "__main__":
    app()
