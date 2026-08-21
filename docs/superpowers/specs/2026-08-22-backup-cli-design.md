# Cross-Platform Database Backup CLI — Design Spec

**Date:** 2026-08-22  
**Status:** Approved (brainstorming → architectural path)  
**Scope:** v1 baseline. Incremental/differential deferred to v2 via extension point.  
**Locked decisions:** Python ≥3.11 · 4 DBMS in v1 (MySQL, PostgreSQL, MongoDB, SQLite) · S3 only (+ S3-compatible, e.g. MinIO) · daemon scheduler (APScheduler, MemoryJobStore) · full-only backups in v1 · PyPI/pipx + PyInstaller single-file distribution · TOML config · Slack opt-in.

---

## 1. Architecture & Project Layout

### 1.1 Stack

Python 3.11+, Typer + Rich, stdlib `tomllib` (read) / `tomli-w` optional (write only if needed), APScheduler, boto3/botocore, stdlib `gzip`, stdlib `logging` + `RotatingFileHandler`, `httpx` (Slack), `platformdirs`.

### 1.2 Principles

- **Adapter Registry** (`DBAdapter`) for DBMS extensibility; adding a 5th DB = one new adapter file + registry entry.
- **Storage abstraction** (`StorageBackend`) with S3 as the only public backend in v1; GCS/Azure slot in later without touching adapters.
- **Full-only v1** — no incremental/differential CLI surface; internal `BackupStrategy` extension point reserved for v2.
- **Bounded streaming** for large DBs; no spill-to-disk on backpressure.

### 1.3 Scheduler / TOML boundary

- TOML is **configuration only**. Layered resolution: `defaults < platformdirs user config < ./dbbackup.toml < env DBBACKUP_* < CLI flags`.
- Daemon `dbbackup schedule --daemon` loads `[[schedule.jobs]]` from TOML **at startup** and registers them in APScheduler with `MemoryJobStore` (in-memory only).
- If persistence becomes required later, use APScheduler's native job store (e.g. `SQLAlchemyJobStore`/SQLite) **separately** — never TOML as a job store.
- Schedule definitions live in TOML as configuration; the job store holds only runtime registrations.

### 1.4 DBAdapter contract — BackupArtifact

Core interface (minimal):

```python
class DBAdapter(ABC):
    def test_connection(self, opts: ConnectionOpts) -> None: ...
    def backup(self, opts: BackupOpts) -> BackupArtifact: ...
    def restore(self, artifact: BackupArtifact | BinaryIO, opts: RestoreOpts) -> None: ...
```

- `BackupArtifact` abstracts native semantics. Fields: `db_type`, `format` (e.g. `sql`, `archive`, `sqlite`), `extension`, `stream_or_path` (stream handle *or* temp file path + `needs_cleanup`), `size_hint`, `created_at`, `metadata{}`. Implements context-manager/`close()` for cleanup.
- Each adapter determines representation and declares `artifact_format`/`extension`:
  - MySQL: `mysqldump` streaming artifact.
  - PostgreSQL: `pg_dump` streaming artifact.
  - MongoDB: `mongodump --archive` streaming artifact (directory dump normalized internally).
  - SQLite: `sqlite3` backup API producing a **temporary database file artifact** (non-streaming exception; see §2.3).
- Orchestrator `core/backup.py` consumes `BackupArtifact` uniformly → gzip → S3. Adapters do not take a `dest_stream` parameter.

`list_targets()` is **excluded** from the `DBAdapter` ABC. Selective restore (`--table`/`--collection`) is handled as `RestoreOpts` parsed per-adapter.

### 1.5 Storage scope

- Public `StorageBackend` in v1: `S3Backend` only (`boto3`, `endpoint_url` for MinIO/S3-compatible). No `--storage local` public option.
- Local filesystem used **internally only** for temp staging/compression workdir (`core/workdir.py`, `tempfile` + streaming `gzip`), cleaned via `BackupArtifact.close()`. No `storage/local.py` public backend in v1.
- `StorageBackend` ABC in v1 exposes **only `upload` + `download`**. `list()`/`delete()` are excluded unless a concrete v1 feature (retention/cleanup or `dbbackup list` command) requires them.

### 1.6 Backup type

- v1 CLI exposes **full backup only** — no `--type incremental/differential` flag. Help text and docs state this explicitly.
- Internal abstraction reserves `BackupStrategy` / `BackupOpts` extension point so incremental/differential can be added in v2 without breaking adapter/storage interfaces or CLI contract. No `NotImplementedError` user-facing path in v1.

### 1.7 PyInstaller + external binaries

- `PyInstaller` bundles Python code + libs only. It **does not** bundle `mysqldump`, `mysql`, `pg_dump`, `pg_restore`, `mongodump`, `mongorestore`.
- Each adapter performs `shutil.which()` detection at `test_connection()` and `backup()`/`restore()` entry, failing fast with `BinaryNotFoundError` carrying: required binary name, minimum version, per-OS install hint (`apt`/`brew`/`choco` + PATH guidance including common Windows locations). Documented in `README.md` and `--help` prerequisites. SQLite requires no external binary.

### 1.8 Project layout

```
dbbackup/
  cli.py                 # Typer app: backup, restore, test-connection, schedule --daemon
  config.py              # layered TOML loader (tomllib read, tomli-w write optional)
  models.py              # ConnectionOpts, BackupOpts, RestoreOpts, BackupArtifact, BackupResult
  adapters/
    base.py              # DBAdapter ABC: test_connection(), backup()->BackupArtifact, restore()
    mysql.py             # mysqldump/mysql via subprocess, binary detection
    postgres.py          # pg_dump/pg_restore via subprocess
    mongo.py             # mongodump/mongorestore --archive
    sqlite.py            # sqlite3 backup API fallback, no binary dep, temp-file artifact
    registry.py          # {"mysql":..., "postgres":..., "mongo":..., "sqlite":...}
  storage/
    base.py              # StorageBackend ABC: upload/download (list/delete excluded in v1)
    s3.py                # boto3, multipart, endpoint_url support, explicit retry config
  core/
    backup.py            # BackupArtifact -> gzip stream -> S3 upload, emits BackupResult
    restore.py           # S3 download -> gunzip -> adapter restore
    scheduler.py         # APScheduler MemoryJobStore, jobs reconstructed from TOML at startup
    compression.py       # streaming gzip wrapper (level 1-9)
    workdir.py           # internal temp dir/file lifecycle (not a storage backend)
    logging_setup.py     # RotatingFileHandler + console, structured fields
    notify.py            # Slack webhook opt-in (httpx, https only)
    redact.py            # centralized secret/error redaction layer (see §3.2)
pyproject.toml           # hatch, entry point dbbackup = dbbackup.cli:app
dbbackup.spec            # PyInstaller single-file spec (v1 distribution format)
```

---

## 2. Components & Data Flow

### 2.1 CLI Commands (Typer + Rich)

```
dbbackup backup --db {mysql|postgres|mongo|sqlite} --host --port --user --database
                [--password | --password-env VAR | --password-stdin | --ask-password]
                [--s3-bucket --s3-prefix --s3-endpoint-url --s3-region]
                [--gzip-level 6] [--config path]

dbbackup restore --db ... --s3-key <key> [--target-db ...] [--table/--collection <name>]*

dbbackup test-connection --db ... --host ... --user ...   # connectivity + binary check only

dbbackup schedule --daemon [--config path]

dbbackup --help / --version
```

- No `--type` flag in v1 (full-only). Help states this.
- Selective restore opts passed as `RestoreOpts`; per-adapter interpretation with actionable error if unsupported (e.g. SQLite table-level restore = documented limitation).
- Rich: pretty errors, progress spinner for long dumps/uploads, summary table on success.

### 2.2 Credential model

**DB credentials — hardened, preferred order:**

1. Interactive prompt (`--ask-password` or no password flag → `getpass`).
2. Env/secret (`--password-env DBBACKUP_PASSWORD` or `DBBACKUP_PASSWORD` env / secret manager).
3. Stdin pipe for automation (`--password-stdin`).

TOML `connection.password` is supported only as **explicit opt-in** and triggers a warning at load (`[warn] plaintext password in TOML — prefer env/prompt; see docs`); docs mark it discouraged and gate it behind `allow_plaintext_password = true` in TOML. Never required.

**S3 credentials:** Not stored in TOML via access keys. `storage/s3.py` relies on boto3's standard credential provider chain — env (`AWS_*`), `~/.aws/credentials`, SSO, IAM instance/pod role. TOML S3 block holds only non-secret fields (`bucket`, `prefix`, `region`, `endpoint_url`). If `access_key` is ever added it is opt-in with the same plaintext warning and redaction (see §3.2).

### 2.3 BackupArtifact & naming

`BackupArtifact` carries `format` + `extension` so S3 key reflects actual artifact. Adapter-defined:

- **PostgreSQL:** `.sql.gz` (plain `pg_dump`) or `.dump.gz` (custom/directory-wrapped) — adapter decides.
- **MySQL:** `.sql.gz`
- **MongoDB:** `.archive.gz` (`mongodump --archive`)
- **SQLite:** `.sqlite.gz` (or `.db.gz` equivalent) — adapter-defined

S3 key: `<prefix>/<db>-<database>-<timestamp><extension>` — extension from artifact, not hardcoded.

### 2.4 Data flows

#### 2.4.1 Streaming & backpressure

Normal pipeline is **bounded streaming**: `native DB process stdout → gzip stream → S3 multipart upload`. Backpressure propagates naturally through the pipe chain; no spill-to-disk on backpressure. Temporary staging is used **only when a specific adapter/output format requires it** (SQLite below), not as a backpressure fallback. Memory bounded; progress via Rich spinner + byte counter.

#### 2.4.2 SQLite non-streaming exception

SQLite is an explicit exception. `sqlite.py` uses the `sqlite3` backup API which produces a **temporary database file artifact** in `core/workdir.py` before compression/upload. It does not produce a byte stream directly. Docs and adapter comments state this; all other adapters remain streaming.

#### 2.4.3 Backup & restore flows

```
Backup:  adapter.backup() → BackupArtifact{stream | temp file, format, extension}
                      → gzip (streaming; SQLite temp file streamed after creation)
                      → S3 multipart upload (key with per-adapter extension)
                      → BackupResult{start,end,duration,status,s3_key,bytes,error?} → log + optional Slack

Restore: S3 streaming GET → gunzip → adapter.restore(artifact_stream_or_file, targetOpts)
                                         ├─ mysql:    mysql < dump
                                         ├─ postgres: pg_restore / psql
                                         ├─ mongo:    mongorestore --archive
                                         └─ sqlite:   sqlite3 backup API / file replace (exclusive lock)

Schedule: TOML [[schedule.jobs]] @ daemon startup → reconstructed → APScheduler MemoryJobStore
          → on trigger → same backup flow as one-shot (skip if still running per §2.4.5) → log
```

#### 2.4.4 Scheduler MemoryJobStore lifecycle

`MemoryJobStore` **does not persist or reload jobs after restart**. Lifecycle:

```
process stops → in-memory jobs disappear (lost)
process starts → TOML [[schedule.jobs]] read → jobs reconstructed → registered in APScheduler (MemoryJobStore)
```

TOML is the source of truth; no reload from job store.

#### 2.4.5 Scheduler overlap — no concurrent duplicate

- **Invariant:** `max_instances=1` per scheduled job ID — no concurrent execution of the same scheduled job, ever.
- **`max_instances=1` is the concurrency invariant, not the skip mechanism itself.** Missed-trigger handling is defined separately via the misfire policy.
- **Missed-trigger policy:** When a trigger fires while that job is still active, the second execution **must not run concurrently**; its disposition follows the configured misfire policy. With `coalesce=True` and `misfire_grace_time` (default 300s), multiple missed firings during the active run coalesce into at most one deferred execution, which runs only if the job becomes free within the grace window; otherwise the missed execution is **skipped** and a warning is logged (`job <id> missed trigger at <time> — previous run still active, skipped/coalesced per misfire policy`). No concurrent duplicate.

#### 2.4.6 Concurrency scope

`max_instances=1` scopes to a **single schedule job ID**. Two different job IDs (even targeting the same database/host) are **allowed to run concurrently by default** in v1. No global database-level lock is introduced in v1. This is documented in TOML comments and `--help` for `schedule`; a shared DB-level lock can be added in v2 if required.

#### 2.4.7 Graceful shutdown

Explicit v1 policy: on `SIGINT` / `SIGTERM` (see §4.3 for platform differences), scheduler stops accepting new triggers immediately, then **waits for the active backup to finish up to a configurable grace period** (`schedule.shutdown_grace_seconds`, default 60s). If the backup completes within the grace period, exit cleanly with status logged. If grace period expires, the active subprocess/upload is cancelled, partial S3 multipart is aborted, temp files cleaned, and exit is non-zero with `BackupResult{status=interrupted}` logged. Documented in `--help` and TOML comments.

#### 2.4.8 test-connection scope

`dbbackup test-connection` validates **only**: (a) required native binary present + version satisfies minimum (`shutil.which` + `--version` check), (b) DB connectivity and credentials (lightweight ping/auth, e.g. `mysqladmin ping` / `pg_isready` / `mongosh --eval` / `sqlite3` open). It **does not perform any backup, dump, or upload**. Missing binary → per-OS install hint; auth failure reported without leaking secrets (via `core/redact.py`).

#### 2.4.9 Config resolution

```
defaults < platformdirs user config < ./dbbackup.toml < env DBBACKUP_* < CLI flags
```

Missing required fields → validation error pointing to TOML file + field + `--help` reference.

### 2.5 Component responsibilities

| Component | Owns | Notes |
|-----------|------|-------|
| `cli.py` | Arg parsing, wiring, exit codes | Credential prompt/env resolution, warning surfacing |
| `config.py` | Layered TOML merge (tomllib), validation | Plaintext-password opt-in warning; S3 secrets not required |
| `adapters/base.py` | ABC | Minimal surface |
| `adapters/{mysql,postgres,mongo,sqlite}.py` | Binary detection, subprocess streaming, format metadata | Each declares `artifact_format`/`extension`; SQLite temp-file exception |
| `models.py` | `ConnectionOpts`, `BackupOpts`, `RestoreOpts`, `BackupArtifact`, `BackupResult` | `BackupArtifact` carries `format`/`extension`/`stream_or_path` |
| `storage/base.py` | Minimal `upload` + `download` | `list`/`delete` excluded in v1 |
| `storage/s3.py` | `upload(artifact, key)`, `download(key)->stream` | boto3 chain, multipart, `endpoint_url`, explicit retry config |
| `core/backup.py` | `artifact = adapter.backup()` → gzip → S3 | Bounded streaming |
| `core/restore.py` | S3 download → gunzip → `adapter.restore()` | Respects artifact format |
| `core/scheduler.py` | APScheduler MemoryJobStore, jobs from TOML at startup, `max_instances=1` | Overlap + misfire policy per §2.4.5 |
| `core/compression.py` | Streaming `gzip.GzipFile` wrapper (level 1–9) | — |
| `core/workdir.py` | Internal `tempfile.TemporaryDirectory` lifecycle | Only for format-required staging |
| `core/logging_setup.py` | Rotating file + console | Structured fields |
| `core/notify.py` | Slack webhook opt-in via `httpx` | — |
| `core/redact.py` | Centralized secret/error redaction | Applied before console/logs/BackupResult/Slack |

---

## 3. Error Handling, Security, Logging & Notifications

### 3.1 Error handling

**Taxonomy & exit codes (one-shot CLI only):**

| Code | Meaning | Exception |
|------|---------|-----------|
| 0 | success | — |
| 10 | validation/config error | `ConfigError` |
| 11 | binary missing | `BinaryNotFoundError` |
| 12 | connection/auth failure | `ConnectionError` |
| 13 | backup/restore failure (subprocess non-zero, upload/download failure) | `BackupError` / `RestoreError` |
| 14 | interrupted/cancelled (grace expiry) | `InterruptedError` |
| 20 | unexpected internal | `InternalError` |

Exit codes `10–20` apply to **one-shot CLI invocations** (`backup`, `restore`, `test-connection`). A failed scheduled backup **does not terminate the daemon** — the daemon records `BackupResult{status=failed}`, logs at `ERROR`, sends Slack notification if configured, and continues serving future jobs. The daemon exits non-zero only on unrecoverable startup/config failure or graceful-shutdown expiry.

**Fail-fast & actionable messages:**

- Every `test-connection` and `backup`/`restore` entry validates binary presence via `shutil.which` + `--version` before touching the DB. Missing binary → `BinaryNotFoundError` with per-OS install hint and minimum version. No partial dump attempted.
- Config validation fails before scheduler registration or backup start; error points to TOML file + field + `--help` reference.
- DB auth/connection errors surfaced without leaking secrets (via `core/redact.py`).

**Resource cleanup:**

- `BackupArtifact` context-manager/`close()` — temp files in `core/workdir.py` removed on success, failure, or cancellation.
- On upload failure, S3 multipart is **aborted** (`abort_multipart_upload`) to avoid orphan parts.
- On graceful-shutdown expiry, subprocess terminated, multipart aborted, `BackupResult{status=interrupted}` emitted.

**Retries:**

- No automatic retry of DB dumps in v1 — to avoid repeated load on the source database and duplicate/extra backup artifacts. A failed dump may be retried manually (`dbbackup backup` again) or by the next scheduled execution; the scheduler does not immediately re-fire.
- S3 client uses an **explicit, configurable botocore retry policy** (`botocore.config.Config(retries={"max_attempts": N, "mode": "standard"})`, default `max_attempts=3`, override via TOML `storage.s3.max_attempts`). Bounded retries for transient network/service errors (throttling, 5xx). On terminal failure, multipart is aborted; retry never leaves orphan parts.

### 3.2 Security

- **Credentials:** Per §2.2 — prompt/env/stdin preferred; TOML plaintext requires `allow_plaintext_password = true` with load-time warning; S3 secrets via boto3 chain only.
- **Centralized redaction layer (`core/redact.py`):** Single sanitization function applied **before** any sensitive data reaches console, file log, `BackupResult.error`, or Slack payload. Covers: DB passwords, S3 access tokens, Slack webhook URLs, connection-string credentials (`password=***`, `passwd=***`), sensitive subprocess arguments. All error paths route through it.
- **Filesystem:** Temp workdir uses **owner-restricted / private** temporary files via platform-appropriate mechanisms. On POSIX, `0700` dirs / `0600` files where supported; on Windows, equivalent ACL owner-only via `tempfile` + restricted inheritance. No literal `0600` guarantee claimed on every OS. Cleanup on all paths including `KeyboardInterrupt`.
- **Transport:** S3 via TLS (boto3 default, no `verify=False`). Slack webhook via `https` only; URL stored as secret (env preferred, TOML opt-in with warning, redacted via `core/redact.py`).

### 3.3 Logging

**Setup (`core/logging_setup.py`):** stdlib `logging` + `RotatingFileHandler` (location via `platformdirs` state/log dir; `maxBytes=10MB`, `backupCount=5`, configurable via TOML). Console via Rich (human-readable), file via structured lines (key=value or JSON-lines option, `logging.json = true`).

**Fields per operation:** `timestamp`, `level`, `job_id` (scheduler) or `cmd`, `db_type`, `database`, `s3_key`, `artifact_format`, `bytes`, `start_time`/`end_time`/`duration_ms`, `status` (`success`/`failed`/`interrupted`/`skipped`), `error_code`/`error_message` (sanitized via `core/redact.py`). Scheduler overlap skips log at `WARNING`.

**Levels:** `INFO` for start/success/skip, `WARNING` for plaintext-password warning / overlap skip / grace expiry, `ERROR` for failures. `--verbose`/`--quiet` flags.

### 3.4 Notifications (Slack, opt-in v1)

- **Trigger:** Only on backup completion (success or failure) when `notify.slack_webhook_url` is set. No notification for `test-connection`.
- **Secret config:** Preferred `DBBACKUP_SLACK_WEBHOOK_URL` env. TOML `notify.slack_webhook_url` is explicit opt-in with same plaintext-secret warning as DB credentials (docs mark discouraged, redacted via `core/redact.py`, `https` only).
- **Delivery:** `core/notify.py` via `httpx` POST, `https` only, timeout 5s, non-blocking — notification failure logged at `WARNING` and never fails the backup.
- **Payload contract:** Machine-readable `status: "success" | "failed" | "interrupted"` as the semantic status field. Emoji is optional presentation detail only, not part of the status model. Other fields: `db_type`/`database`, `s3_key`, `duration`, `bytes`, `error` (truncated, sanitized), `timestamp`. No secrets. One message per backup execution.

---

## 4. Testing, Packaging & Cross-Platform

### 4.1 Testing

**Pyramid — no real DB required for default unit/CI:**

- **Unit (majority):** Adapters with mocked `subprocess.Popen` + `shutil.which` (detection/version/streaming/error mapping per DB); `BackupArtifact` lifecycle + `workdir` cleanup on all paths; `storage/s3.py` via `moto`/`botocore.stub` or mocked `boto3` (multipart, abort on failure, retry config, `endpoint_url`); `config.py` layered merge + plaintext-password warning; `core/redact.py` (all secret shapes); `core/compression.py` round-trip; `core/scheduler.py` with fake clock / mocked `BackgroundScheduler` (overlap `max_instances=1`, misfire coalesce, no concurrent duplicate).
- **Integration (gated):** `testcontainers` or local DB fixtures for `test-connection` + one full `backup→S3→restore` per adapter, behind `pytest --integration` flag; not run in default CI.
- **CLI E2E:** `typer.testing.CliRunner` for all commands — exit codes `0/10-14/20`, help text (full-only, prerequisites, secret warnings), Rich output, `--ask-password` / `--password-env` flows, TOML + env overlay, graceful shutdown path mocked.

**Coverage:**

- Target ≥80% on `core/`, `adapters/`, `storage/`, `config`.
- Coverage percentage is **not sufficient by itself**. The following critical-path tests are **mandatory** regardless of percentage: backup failure, restore failure, S3 multipart abort, secret redaction, interrupted backup/shutdown, scheduler overlap, temporary-file cleanup. Redaction paths target 100%.

**Fixtures & determinism:** Fixed timestamps for S3 key tests; seeded gzip level; `pytest` + `pytest-mock`; CI on Linux primary, Windows/macOS for E2E smoke.

### 4.2 Packaging & Distribution

- **Source:** `pyproject.toml` (hatch), Python `>=3.11`, `typer`, `rich`, `boto3`/`botocore`, `APScheduler`, `httpx`, `platformdirs`, stdlib `tomllib` for reading, `tomli-w` optional for writing. Entry point `dbbackup = dbbackup.cli:app`.
- **TOML dependency:** `tomli` is **not** a runtime dependency for reading — stdlib `tomllib` is used on Python ≥3.11. `tomli-w` is optional, only if the application ever needs to write TOML.
- **PyPI / pipx:** `hatch build` → wheel/sdist → `pipx install dbbackup` primary install path. Version from `__version__.py` (semver), `CHANGELOG.md`.
- **PyInstaller binary:** Single-file executable is the **chosen v1 distribution format** (not an architectural requirement). `pyinstaller` spec (`dbbackup.spec`) produces a single-file executable per OS in CI (`ubuntu-latest`, `macos-latest`, `windows-latest` via GitHub Actions). Binary bundles Python code/libs only — external DB binaries not bundled (§1.7). Normal PyInstaller single-file extraction/startup characteristics (temp extraction on launch, startup overhead) documented where relevant.
- **No auto-update, no code signing in v1** — documented as out of scope.

### 4.3 Cross-Platform

- **Paths:** `platformdirs` is the **source of truth** for config/state/log directories (Linux `~/.config` / `~/.local/state`, macOS `~/Library/Application Support`, Windows `%APPDATA%`). `~/.config/dbbackup` described only as the logical Linux convention, not a universal physical path. All paths via `pathlib`.
- **Binaries:** Detection handles `mysqldump` vs `mysqldump.exe`, etc.; version checks via `--version` stdout parsing per tool; Windows PATH guidance includes common install locations.
- **Subprocess:** `subprocess.Popen` with `creationflags`/`shell=False` consistent across OS.
- **Signals & graceful shutdown:** Platform-specific behavior is explicit. Unix-like: `SIGINT`/`SIGTERM` both trigger graceful shutdown per §2.4.7 (stop triggers, wait up to grace period, abort if expired). Windows: `SIGINT` (Ctrl+C) triggers the same; `SIGTERM` semantics differ and are not implied to be identical — documented as platform-specific.
- **Daemon:** Foreground `schedule --daemon` on all OS in v1; no `systemd`/Windows Service integration in v1 (documented as v2). Same `max_instances=1` + misfire policy on all platforms.
- **Filesystem & perms:** Per §3.2 — owner-restricted private temp files via platform-appropriate ACLs.
- **CI matrix:** `pytest` on all three OS; PyInstaller artifact built on all three; docs note prerequisites per OS.

### 4.4 CI Policy

- **Default workflow:** Unit + CLI E2E (mocked DB binaries) on all three OS. Fast, no real DB required.
- **Integration workflow:** Real DB end-to-end validation for all four adapters (`testcontainers` or service containers + real S3/MinIO) runs **periodically** (nightly or on `main` push) or as a **dedicated integration workflow** (manual or `workflow_dispatch`). The four DB adapters must have real end-to-end validation somewhere in CI/release testing, even though the default suite mocks external binaries.
- **Packaging smoke tests (per PyInstaller artifact):** Each executable is verified to: (a) respond to `--help` / `--version`, (b) exercise `test-connection` binary-missing path, **and** (c) start successfully and import/load all packaged Python dependencies (smoke import check — e.g. `dbbackup --help` exercises Typer/Rich/boto3 import graph, plus an explicit `python -c "import dbbackup"` equivalent against the bundled runtime where applicable).

---

## 5. Non-Goals (v1)

- Incremental/differential backups (v2 via `BackupStrategy` extension point).
- GCS / Azure Blob (interface ready, not implemented).
- Local storage as public `--storage local` backend (internal temp only).
- `StorageBackend.list()`/`delete()` (unless retention/cleanup or listing command ships).
- `DBAdapter.list_targets()` (unless a concrete listing CLI command requires it).
- `systemd` / Windows Service integration for daemon.
- Auto-update, code signing.

---

## 6. Open Questions for Implementation

- Minimum binary versions per DB (e.g. `pg_dump` ≥14, `mongodump` ≥100.5) — pin during implementation.
- Default S3 key layout and retention policy (if any) — confirm before implementing storage.
- Slack payload block shape (Blocks vs attachments) — confirm with workspace.

---

*Spec generated via superpowers:brainstorming (architectural path). Sections 1–4 approved incrementally; this document is the locked baseline for the writing-plans phase.*
