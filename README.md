# dbbackup — Cross-Platform Database Backup CLI

Full, consistent backups for **MySQL, PostgreSQL, MongoDB, and SQLite** — streamed through `gzip` to **S3** (and S3-compatible stores like MinIO). Built with [Typer](https://typer.tiangolo.com/) + [Rich](https://github.com/Textualize/rich) for a fast, predictable CLI on Linux, macOS, and Windows.

> **v1 scope: full backups only.** Incremental / differential is reserved for v2 via an internal `BackupStrategy` extension point — no `--type` flag, no `NotImplementedError` path.

## Features

- **4 DBMS in v1** — MySQL (`mysqldump`), PostgreSQL (`pg_dump`), MongoDB (`mongodump --archive`), SQLite (`sqlite3` backup API, no binary required)
- **Bounded streaming** — `dump stdout → gzip → S3 multipart upload` with natural backpressure (SQLite is the documented temp-file exception)
- **S3 only** — `boto3` credential chain (`env`, `~/.aws/credentials`, SSO, IAM role), `endpoint_url` for MinIO, multipart threshold 100 MB / chunk 10 MB, explicit `botocore` retries, abort on failure
- **Scheduler daemon** — `dbbackup schedule --daemon` loads `[[schedule.jobs]]` from TOML at startup into APScheduler `MemoryJobStore`, `max_instances=1`, `coalesce=True`, `misfire_grace_time=300s`, graceful `SIGINT`/`SIGTERM` with `shutdown_grace_seconds=60s`
- **Hardened credentials** — `--ask-password` (interactive), `--password-env VAR`, `--password-stdin` preferred ; TOML plaintext is explicit opt-in (`allow_plaintext_password = true`) with a load-time warning
- **Centralized redaction** — single `core/redact.py` layer before console, file log, `BackupResult.error`, and Slack payload (DB passwords, URL creds, S3 tokens, webhook URLs)
- **Secure by default** — owner-restricted temp files, TLS-only S3/Slack, actionable `BinaryNotFoundError` with per-OS hints

## Prerequisites

Python **3.11+**. External binaries are **not bundled** by the PyInstaller binary — install them separately:

| DBMS | Binary | Minimum | Install |
|------|--------|---------|---------|
| MySQL | `mysqldump`, `mysql` | documented in `--help` | `apt install mysql-client` · `brew install mysql` · `choco install mysql` |
| PostgreSQL | `pg_dump`, `pg_restore` | ≥14 recommended | `apt install postgresql-client` · `brew install postgresql` |
| MongoDB | `mongodump`, `mongorestore` | ≥100.5 | `brew install mongodb-database-tools` |
| SQLite | — (stdlib `sqlite3`) | — | none |

Missing binaries fail fast with a per-OS hint. `dbbackup test-connection --db <type>` checks both binary and connectivity without performing a backup.

## Installation

```bash
# pipx (recommended)
pipx install dbbackup

# pip
pip install dbbackup

# from source
pip install -e .[dev]

# single-file binary (per-OS artifact from CI)
./dist/dbbackup --help
```

## Quick Start

```bash
# 1. verify prerequisites + connectivity
dbbackup test-connection --db postgres --host localhost --user backup --database mydb --ask-password

# 2. full backup to S3 (streams dump → gzip → S3)
dbbackup backup --db postgres --host localhost --user backup --database mydb \
  --ask-password \
  --s3-bucket my-backups --s3-prefix prod/mydb

# 3. restore (selective restore per-adapter)
dbbackup restore --db postgres --s3-key prod/mydb/mydb-20260822T120000.sql.gz --target-db mydb_restored
dbbackup restore --db mysql --s3-key prod/mydb/mydb-20260822T120000.sql.gz --table users
dbbackup restore --db mongo --s3-key prod/mydb/mydb-20260822T120000.archive.gz --collection events

# MinIO / S3-compatible
dbbackup backup --db sqlite --database ./app.db \
  --s3-bucket backups --s3-endpoint-url http://localhost:9000 --s3-region us-east-1

# run scheduled jobs from TOML
dbbackup schedule --daemon --config ./dbbackup.toml
```

## Configuration

Layered resolution (later wins):

```
defaults < platformdirs user config < ./dbbackup.toml < env DBBACKUP_* < CLI flags
```

Config dir via `platformdirs` (Linux `~/.config/dbbackup`, macOS `~/Library/Application Support/dbbackup`, Windows `%APPDATA%\dbbackup`).

**Example `dbbackup.toml`:**

```toml
# Global defaults
[s3]
bucket = "my-backups"
prefix = "prod"
region = "us-east-1"
# endpoint_url = "http://localhost:9000"  # MinIO

[schedule]
shutdown_grace_seconds = 60

[[schedule.jobs]]
id = "nightly-postgres"
cron = "0 3 * * *"          # or interval_seconds = 3600
db_type = "postgres"
host = "db.example.com"
port = 5432
user = "backup"
database = "mydb"
s3_bucket = "my-backups"
s3_prefix = "prod/postgres"

[[schedule.jobs]]
id = "hourly-sqlite"
interval_seconds = 3600
db_type = "sqlite"
database = "./app.db"
s3_bucket = "my-backups"

# Plaintext password in TOML is opt-in only
# allow_plaintext_password = true
# [connection]
# password = "changeme"  # triggers: [warn] plaintext password in TOML
```

> [!WARNING]
> Prefer `--ask-password`, `--password-env DBBACKUP_PASSWORD`, or `--password-stdin`. TOML `password` requires `allow_plaintext_password = true` and is always warned + redacted. S3 credentials are **never** stored in TOML — they come from the `boto3` chain.

`DBBACKUP_SLACK_WEBHOOK_URL` (env) is the preferred Slack path; TOML `notify.slack_webhook_url` is the same opt-in with a plaintext-secret warning and is redacted everywhere.

## CLI Reference

```
dbbackup --help
dbbackup --version
dbbackup backup   --db {mysql|postgres|mongo|sqlite} --host --port --user --database
                  [--password | --password-env VAR | --password-stdin | --ask-password]
                  [--s3-bucket --s3-prefix --s3-endpoint-url --s3-region]
                  [--gzip-level 6] [--config path]
dbbackup restore  --db ... --s3-key <key> [--target-db ...] [--table <name>]* [--collection <name>]*
dbbackup test-connection --db ... --host ... --user ...   # no dump/upload
dbbackup schedule --daemon [--config path]                  # foreground daemon in v1
```

Common flags: `--help`, `--version`, `--verbose`/`--quiet` (logging), `--config path`.

**Selective restore** is parsed as `RestoreOpts` per-adapter; unsupported modes (e.g. SQLite table-level restore) return an actionable error.

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | success |
| `10` | validation / config error |
| `11` | binary missing (`BinaryNotFoundError`) |
| `12` | connection / auth failure |
| `13` | backup / restore failure |
| `14` | interrupted / grace expiry |
| `20` | unexpected internal |

Codes `10–14`/`20` apply to one-shot invocations. The daemon does **not** exit on a single `failed` job — it logs `BackupResult{status=failed, error=***}`, sends Slack if configured, and keeps serving.

### Credentials

```bash
# preferred: interactive
dbbackup backup --db mysql --database mydb --ask-password --s3-bucket bkt

# preferred: env / secret manager
export DBBACKUP_PASSWORD="s3cret"
dbbackup backup --db mysql --database mydb --password-env DBBACKUP_PASSWORD --s3-bucket bkt

# automation: stdin pipe
printf '%s' "$DBBACKUP_PASSWORD" | dbbackup backup --db mysql --database mydb --password-stdin --s3-bucket bkt
```

All error paths are redacted before reaching the console, file log, `BackupResult.error`, or Slack payload.

## Scheduling

```bash
dbbackup schedule --daemon                # loads [[schedule.jobs]] from TOML at startup
dbbackup schedule --daemon --config ./dbbackup.toml
```

- `MemoryJobStore` only — jobs disappear when the process stops and are reconstructed from TOML on next start. Never use TOML as a job store.
- `max_instances=1` per job `id` — the same job never runs concurrently; missed triggers during an active run coalesce into at most one deferred run within `misfire_grace_time` (300s), otherwise skipped with `WARNING`.
- Two different job `id`s may run concurrently (even against the same DB/host).
- `SIGINT`/`SIGTERM` → stop accepting triggers → wait up to `shutdown_grace_seconds` (default 60s) → if expired, abort subprocess/multipart, clean temp files, emit `BackupResult{status=interrupted}`, exit `14`.

## Storage

Public `StorageBackend` in v1 exposes only `upload` + `download`. S3 backend:

- Multipart for artifacts `>100 MB` (`TransferConfig` 10 MB chunks), `botocore.config.Config(retries={"max_attempts": 3, "mode": "standard"})` (override via TOML `storage.s3.max_attempts`), `endpoint_url` passthrough.
- `abort_multipart_upload` on terminal failure to avoid orphan parts.
- Local filesystem is used **internally only** for `core/workdir.py` + `BackupArtifact(format, extension, stream_or_path, needs_cleanup)` cleanup.

S3 key: `<prefix>/<database>-<timestamp><extension>` where `<extension>` comes from the adapter (`.sql.gz`, `.archive.gz`, `.sqlite.gz`, `.dump.gz`).

## Development

```bash
pip install -e .[dev]
pytest -q                          # unit + CLI E2E (mocked binaries, 82 tests)
pytest --cov=dbbackup -q           # coverage (76% core+adapters+storage+config)
pytest --integration -q            # real DB E2E via testcontainers + MinIO (gated)

hatch build                        # wheel + sdist  → dist/
pip install dist/*.whl && dbbackup --help && dbbackup --version
pyinstaller dbbackup.spec          # single-file binary → dist/dbbackup
./dist/dbbackup --help
```

**Project layout:**

```
dbbackup/
  cli.py               Typer app: backup, restore, test-connection, schedule --daemon
  config.py            layered TOML loader (tomllib, platformdirs)
  models.py            ConnectionOpts, BackupOpts, RestoreOpts, BackupArtifact, BackupResult
  adapters/            DBAdapter ABC + mysql/postgres/mongo/sqlite + registry
  storage/             StorageBackend ABC + S3Backend
  core/                backup, restore, scheduler, compression, workdir, logging_setup, notify, redact
tests/                 mocked subprocess/shutil.which, moto/botocore stub, CliRunner, scheduler overlap
dbbackup.spec          PyInstaller single-file spec (v1 distribution format)
.github/workflows/     ci.yml (unit+E2E on ubuntu/macos/windows) + integration.yml (periodic, --integration)
```

> [!NOTE]
> `tomli` is not a runtime dependency — `tomllib` (stdlib, 3.11+) is used for reading. `tomli-w` is optional, only if TOML writing is ever needed. `platformdirs` is the source of truth for paths; `~/.config/dbbackup` is the Linux convention only.

## Limitations (v1 Non-Goals)

Incremental/differential backups, GCS/Azure Blob, public `--storage local`, `StorageBackend.list()`/`delete()`, `DBAdapter.list_targets()`, `systemd`/Windows Service integration, auto-update, and code signing are out of scope for v1.

See `docs/superpowers/specs/2026-08-22-backup-cli-design.md` for the locked spec and `docs/superpowers/plans/2026-08-22-backup-cli-implementation.md` for the implementation plan.
