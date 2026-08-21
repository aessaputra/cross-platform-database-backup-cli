"""Typer CLI skeleton — v1 full-only backups to S3.

Full backups only in v1; incremental/differential reserved for v2.
"""
from __future__ import annotations

import typer
from rich.console import Console

from dbbackup.__version__ import __version__

app = typer.Typer(
    name="dbbackup",
    help="Cross-platform database backup CLI — full backups only in v1 (incremental/differential reserved for v2).",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


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
    # When invoked with --help, Typer handles it before reaching here.
    # When invoked with no args or with a subcommand, just return; subcommands handle work.
    # The callback must exist to wire --version as an eager option.
    _ = ctx  # unused
    _ = version


@app.command("backup")
def backup(
    db: str = typer.Option(..., "--db", help="Database type: mysql | postgres | mongo | sqlite (full backup only)"),
    host: str = typer.Option("", "--host", help="Database host"),
    port: int = typer.Option(0, "--port", help="Database port"),
    user: str = typer.Option("", "--user", help="Database user"),
    database: str = typer.Option("", "--database", help="Database name"),
    s3_bucket: str = typer.Option("", "--s3-bucket", help="S3 bucket"),
    s3_prefix: str = typer.Option("", "--s3-prefix", help="S3 key prefix"),
    gzip_level: int = typer.Option(6, "--gzip-level", help="gzip compression level 1-9"),
    config: str | None = typer.Option(None, "--config", help="Path to TOML config file"),
) -> None:
    """Run a full backup to S3 (v1: full only)."""
    console.print("[yellow]backup: not yet implemented (scaffold only)[/yellow]")
    raise typer.Exit(code=0)


@app.command("restore")
def restore(
    db: str = typer.Option(..., "--db", help="Database type: mysql | postgres | mongo | sqlite"),
    s3_key: str = typer.Option(..., "--s3-key", help="S3 key of the backup to restore"),
    target_db: str | None = typer.Option(None, "--target-db", help="Target database name"),
    config: str | None = typer.Option(None, "--config", help="Path to TOML config file"),
) -> None:
    """Restore a full backup from S3."""
    console.print("[yellow]restore: not yet implemented (scaffold only)[/yellow]")
    raise typer.Exit(code=0)


@app.command("test-connection")
def test_connection(
    db: str = typer.Option(..., "--db", help="Database type: mysql | postgres | mongo | sqlite"),
    host: str = typer.Option("", "--host", help="Database host"),
    user: str = typer.Option("", "--user", help="Database user"),
    database: str = typer.Option("", "--database", help="Database name"),
    config: str | None = typer.Option(None, "--config", help="Path to TOML config file"),
) -> None:
    """Test database connectivity and required binaries (no backup performed)."""
    console.print("[yellow]test-connection: not yet implemented (scaffold only)[/yellow]")
    raise typer.Exit(code=0)


@app.command("schedule")
def schedule(
    daemon: bool = typer.Option(False, "--daemon", help="Run scheduler daemon (loads jobs from TOML at startup)"),
    config: str | None = typer.Option(None, "--config", help="Path to TOML config file"),
) -> None:
    """Run scheduled full backups via daemon (full only; jobs from TOML at startup)."""
    if not daemon:
        console.print("[yellow]schedule requires --daemon in v1[/yellow]")
        raise typer.Exit(code=0)
    console.print("[yellow]schedule --daemon: not yet implemented (scaffold only)[/yellow]")
    raise typer.Exit(code=0)


if __name__ == "__main__":
    app()
