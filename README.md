# dbbackup — Cross-Platform Database Backup CLI

Full, consistent backups for **MySQL, PostgreSQL, MongoDB, and SQLite** — streamed through `gzip` to **S3** (and S3-compatible stores like MinIO) **or the local filesystem**. Built with [Typer](https://typer.tiangolo.com/) + [Rich](https://github.com/Textualize/rich) for a fast, predictable CLI on Linux, macOS, and Windows.

Full database backups to **S3** or the **local filesystem**. Each backup is streamed through `gzip` with `S3` as the default destination.

## Features

- **4 DBMS** — MySQL (`mysqldump`), PostgreSQL (`pg_dump`), MongoDB (`mongodump --archive`), SQLite (`sqlite3` backup API, no binary required)
- **Bounded streaming** — `dump stdout → gzip → upload` with natural backpressure (SQLite is the documented temp-file exception)
- **S3 + local** — `boto3` credential chain (`env`, `~/.aws/credentials`, SSO, IAM role), `endpoint_url` for MinIO, multipart threshold 100 MB / chunk 10 MB, retries, or `LocalBackend` atomik dengan sidecar `sha256`
- **Scheduler daemon** — `dbbackup schedule --daemon` loads `[[schedule.jobs]]` from TOML at startup into APScheduler `MemoryJobStore`, `max_instances=1`, `coalesce=True`, `misfire_grace_time=300s`, mixed `local`+`S3` jobs, active-job grace wait, `SIGINT`/`SIGTERM` dengan `shutdown_grace_seconds=60s`
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
# with URL (e.g. Neon)
dbbackup test-connection --url "postgresql://user:password@host:5432/mydb?sslmode=require"

# 2. full backup to S3 (streams dump → gzip → S3)
dbbackup backup --db postgres --host localhost --user backup --database mydb \
  --ask-password \
  --s3-bucket my-backups --s3-prefix prod/mydb
# 2a. full backup via URL (alternative to structured flags)
dbbackup backup --url "postgresql://user:password@host:5432/mydb?sslmode=require" --s3-bucket my-backups --s3-prefix prod/mydb
dbbackup backup --url "mongodb+srv://user:password@cluster.mongodb.net/mydb?authSource=admin" --s3-bucket my-backups

# 2b. full backup to local filesystem (atomik + sha256 sidecar)
dbbackup backup --db postgres --host localhost --user backup --database mydb \
  --ask-password --storage local --local-path /data/backups

# 3. restore (selective restore per-adapter)
dbbackup restore --db postgres --s3-key prod/mydb/mydb-20260822T120000.sql.gz --target-db mydb_restored
dbbackup restore --db mysql --s3-key prod/mydb/mydb-20260822T120000.sql.gz --table users
dbbackup restore --db mongo --s3-key prod/mydb/mydb-20260822T120000.archive.gz --collection events

# local restore with verify (fail-closed bila sidecar hilang/rusak)
dbbackup restore --db postgres --key postgres/mydb-20260822T120000.sql.gz \
  --storage local --local-path /data/backups --verify

# MinIO / S3-compatible
dbbackup backup --db sqlite --database ./app.db \
  --s3-bucket backups --s3-endpoint-url http://localhost:9000 --s3-region us-east-1
# sqlite via file: URI
dbbackup backup --url "file:./app.db" --s3-bucket backups --s3-endpoint-url http://localhost:9000 --s3-region us-east-1

# run scheduled jobs from TOML
dbbackup schedule --daemon --config ./dbbackup.toml
```

Structured flags (`--db`/`--host`/`--port`/`--user`/`--database`) and `--url` are two equivalent connection methods. Each database has its own URL semantics (`postgresql://`/`postgres://`, `mysql://` (dbbackup convenience), `mongodb://`/`mongodb+srv://`, `file:` for sqlite). See CLI Reference for details.

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

# Default storage (s3 | local) — per-job dapat override
[storage]
type = "s3"
[storage.local]
path = "/data/backups"

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
id = "hourly-sqlite-local"
interval_seconds = 3600
db_type = "sqlite"
database = "./app.db"
storage = "local"
local_path = "/data/backups"

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
dbbackup backup   [--db {mysql|postgres|mongo|sqlite}] --host --port --user --database
                  [--password | --password-env VAR | --password-stdin | --ask-password]
                  [--url URL]
                  [--storage {s3|local} --s3-bucket --s3-prefix --local-path --key/--s3-key --force]
                  [--gzip-level 6] [--config path]
dbbackup restore  [--db ...] --key <key> [--s3-key <key> alias] [--storage {s3|local} --local-path --verify] [--target-db ...] [--table <name>]* [--collection <name>]* [--url URL] [--host --port --user --database]
dbbackup test-connection [--db ...] [--host ... --user ... | --url URL]   # no dump/upload
dbbackup schedule --daemon [--config path]                  # foreground daemon, storage per [[schedule.jobs]] via TOML [storage]
```

Connection: structured flags (`--db`+`--host`/`--port`/`--user`/`--database`+password) and `--url` are alternative methods. `--db` is required without `--url`; with `--url` the database type is inferred from the scheme (`postgresql`/`postgres`→postgres, `mongodb`/`mongodb+srv`→mongo, `mysql`→mysql, `file:`→sqlite). `--db`+`--url` must be consistent or fails (exit 10). `--url` cannot be combined with `--host`/`--port`/`--user`/`--database`/password flags.

Supported URL schemes (database-specific, not universal): `postgresql://`, `postgres://`, `mysql://` (dbbackup convenience; verify against adapter), `mongodb://`, `mongodb+srv://`, `file:` (sqlite: `file:./db`, `file:/abs/db`, `file:///abs/db?mode=ro`). SQLite also supports the existing `--db sqlite --database PATH`.

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
- Mixed `local`+`S3` jobs — each job uses its own `StorageBackend` per `storage`/`local_path`, isolated. One failed job never kills daemon.
- `SIGINT`/`SIGTERM` → stop accepting triggers → **aktif job terdeteksi via registry**, wait up to `shutdown_grace_seconds` (default 60s) → if expired, abort subprocess/multipart, clean temp files, emit `BackupResult{status=interrupted}`, exit `14`.

## Storage

Public `StorageBackend` exposes only `upload` + `download`. Two backends:

- **S3Backend** (`--storage s3`, default): multipart for artifacts `>100 MB` (`TransferConfig` 10 MB chunks), `botocore.config.Config(retries={"max_attempts": 3, "mode": "standard"})` (override via TOML `storage.s3.max_attempts`), `endpoint_url` passthrough, `abort_multipart_upload` on failure. Missing/empty `--s3-bucket` now **fails closed** (exit `10`) — no `test-bucket` fallback.
- **LocalBackend** (`--storage local --local-path /data/backups`): `DBBACKUP_STORAGE_TYPE`/`DBBACKUP_LOCAL_PATH` or TOML `[storage]`/`[storage.local].path` + per-job `storage`/`local_path`, single destination per backup, layout `<root>/<db_type>/<database>-<timestamp><ext>` + sidecar `<artifact>.json` (`bytes`+`sha256`, `0600`), `resolve()/is_relative_to()` jail + `PureWindowsPath` + `sanitize_database`, `0700` dirs, `--force` overwrite, `--verify` **fail-closed** (streaming sha).

S3 key: `<prefix>/<database>-<timestamp><extension>` where `<extension>` comes from the adapter (`.sql.gz`, `.archive.gz`, `.sqlite.gz`, `.dump.gz`).

## Development

```bash
pip install -e .[dev]
pytest -q                          # unit + CLI E2E (mocked binaries, 135 tests)
pytest --cov=dbbackup -q           # coverage (77% total, storage/local.py 82%)
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
  config.py            layered TOML loader (tomllib, platformdirs, [storage]/DBBACKUP_*)
  models.py            ConnectionOpts, BackupOpts (--storage/--local-path), RestoreOpts (--key alias, --verify), BackupArtifact, BackupResult
  adapters/            DBAdapter ABC + mysql/postgres/mongo/sqlite + registry
  storage/             StorageBackend ABC + S3Backend + LocalBackend (--force, sidecar sha)
  core/                backup, restore (--verify streaming), scheduler (active-job registry), compression, workdir, logging_setup, notify, redact
tests/                 mocked subprocess/shutil.which, moto/botocore stub, CliRunner, scheduler overlap, EXDEV/EEXIST atomic, verify fail-closed, S3 bucket closed
dbbackup.spec          PyInstaller single-file spec (distribution format)
.github/workflows/     ci.yml (unit+E2E on ubuntu/macos/windows) + integration.yml (periodic, --integration)
```

> [!NOTE]
> `tomli` is not a runtime dependency — `tomllib` (stdlib, 3.11+) is used for reading. `tomli-w` is optional, only if TOML writing is ever needed. `platformdirs` is the source of truth for paths; `~/.config/dbbackup` is the Linux convention only.

## Limitations (Non-Goals)

Incremental/differential backups, GCS/Azure Blob, replication/local+S3 fan-out, retention, `StorageBackend.list()`/`delete()`, `DBAdapter.list_targets()`, `systemd`/Windows Service integration, auto-update, and code signing are out of scope. The Local Filesystem Storage enhancement is strictly second `StorageBackend`, single destination, `upload`/`download` only.

See `docs/superpowers/specs/2026-08-22-backup-cli-design.md` for the locked spec and `docs/superpowers/plans/2026-08-22-backup-cli-implementation.md` for the implementation plan.
