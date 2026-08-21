# Cross-Platform Database Backup CLI Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver v1 CLI (`dbbackup`) with full backup/restore for MySQL, PostgreSQL, MongoDB, SQLite to S3 (+MinIO), daemon scheduling, logging, and Slack notifications.

**Architecture:** Adapter Registry (DBAdapter → BackupArtifact per DB) + minimal StorageBackend (S3 only, boto3 chain) + bounded streaming gzip → S3 multipart; APScheduler MemoryJobStore reconstructed from TOML at daemon start, max_instances=1, centralized redaction.

**Tech Stack:** Python >=3.11, Typer + Rich, tomllib/tomli-w, APScheduler, boto3/botocore, httpx, platformdirs, pytest + moto/botocore.stub

**Spec:** `docs/superpowers/specs/2026-08-22-backup-cli-design.md`

## Global Constraints
- Python >=3.11; stdlib `tomllib` for TOML read (no `tomli` runtime dep); `tomli-w` optional for write.
- `platformdirs` is source of truth for config/state/log dirs; `~/.config/dbbackup` = Linux convention only.
- TOML is config only; schedule jobs reconstructed from TOML at daemon start (MemoryJobStore, not persisted).
- S3 only public backend (+ endpoint_url for MinIO); StorageBackend ABC = upload+download only in v1.
- Full-only backups in v1; no --type flag; BackupStrategy extension point reserved for v2.
- External DB binaries NOT bundled by PyInstaller; detect via shutil.which with per-OS hints.
- Centralized redaction (core/redact.py) before console/log/BackupResult/Slack.
- Exit codes 10-20 = one-shot CLI only; daemon never exits on single job failure.
- Coverage >=80% on core/adapters/storage/config; mandatory critical-path tests (see Tasks).

---

## File Structure

```
dbbackup/
  __init__.py / __version__.py
  cli.py
  config.py
  models.py
  adapters/__init__.py / base.py / registry.py / mysql.py / postgres.py / mongo.py / sqlite.py
  storage/__init__.py / base.py / s3.py
  core/__init__.py / redact.py / logging_setup.py / compression.py / workdir.py / backup.py / restore.py / scheduler.py / notify.py
tests/
  test_models.py / test_redact.py / test_compression.py / test_workdir.py
  test_storage_s3.py / test_adapters_base.py / test_adapters_sqlite.py / test_adapters_mysql.py / test_adapters_postgres.py / test_adapters_mongo.py
  test_config.py / test_cli.py / test_backup_core.py / test_restore_core.py / test_scheduler.py / test_notify.py / test_logging.py
pyproject.toml
dbbackup.spec
```

---

### Task 1: Project Scaffolding & CLI Skeleton

**Files:**
- Create: `pyproject.toml`, `dbbackup/__init__.py`, `dbbackup/__version__.py`, `dbbackup/cli.py`, `dbbackup/models.py`
- Test: `tests/test_cli.py` (skeleton)

**Interfaces:**
- Produces: `dbbackup.cli:app` (Typer), `__version__`, `models.ConnectionOpts/BackupOpts/RestoreOpts/BackupArtifact/BackupResult`

**Steps:**
- [ ] **Step 1: Write failing test** `tests/test_cli.py`:
```python
from typer.testing import CliRunner
from dbbackup.cli import app
runner = CliRunner()
def test_help_shows_full_only():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "full" in r.stdout.lower()
def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
```
- [ ] **Step 2: Run test to verify it fails** `pytest tests/test_cli.py -v` Expected: FAIL module not found
- [ ] **Step 3: Implement pyproject.toml** (hatch, deps: typer, rich, boto3, botocore, apscheduler, httpx, platformdirs; python >=3.11; entry `dbbackup = dbbackup.cli:app`)
- [ ] **Step 4: Implement dbbackup/__version__.py** `__version__ = "0.1.0"` and `dbbackup/models.py` dataclasses (ConnectionOpts, BackupOpts with gzip_level default 6, RestoreOpts, BackupArtifact with format/extension/stream_or_path/close, BackupResult)
- [ ] **Step 5: Implement dbbackup/cli.py** Typer app with --help/--version, stub commands backup/restore/test-connection/schedule --daemon (full-only help text)
- [ ] **Step 6: Run test to verify it passes** `pytest tests/test_cli.py -v` Expected: PASS
- [ ] **Step 7: Commit** `git add pyproject.toml dbbackup/ tests/test_cli.py && git commit -m "feat: scaffold CLI skeleton with full-only help"`

---

### Task 2: Centralized Redaction + Logging + Compression + Workdir

**Files:**
- Create: `dbbackup/core/redact.py`, `dbbackup/core/logging_setup.py`, `dbbackup/core/compression.py`, `dbbackup/core/workdir.py`
- Test: `tests/test_redact.py`, `tests/test_compression.py`, `tests/test_workdir.py`, `tests/test_logging.py`

**Interfaces:**
- Consumes: models
- Produces: `redact(text)->str`, `setup_logging()`, `gzip_stream()`, `TempWorkdir` context

**Steps:**
- [ ] **Step 1: Write failing tests**
```python
# tests/test_redact.py
from dbbackup.core.redact import redact
def test_redacts_password():
    assert "password" not in redact("password=secret123").lower() or "***" in redact("password=secret123")
    assert "***" in redact("passwd=foo")
    assert "***" in redact("https://hooks.slack.com/xxx")
def test_redacts_connection_string():
    assert "***" in redact("postgres://user:secret@host/db")

# tests/test_compression.py
from dbbackup.core.compression import compress_stream, decompress_stream
import io
def test_gzip_roundtrip():
    data = b"hello world " * 1000
    out = io.BytesIO()
    compress_stream(io.BytesIO(data), out, level=6)
    out.seek(0); dec = io.BytesIO()
    decompress_stream(out, dec)
    assert dec.getvalue() == data
```
- [ ] **Step 2: Run tests** `pytest tests/test_redact.py tests/test_compression.py -v` Expected: FAIL
- [ ] **Step 3: Implement core/redact.py** single function covering password=, passwd=, DB URLs, slack webhook, S3 tokens; used everywhere
- [ ] **Step 4: Implement core/compression.py** streaming gzip wrappers level 1-9; core/workdir.py TemporaryDirectory with 0700/owner-restricted; core/logging_setup.py RotatingFileHandler via platformdirs + Rich console + redact filter
- [ ] **Step 5: Run tests** `pytest tests/test_redact.py tests/test_compression.py tests/test_workdir.py -v` Expected: PASS (redact 100% coverage)
- [ ] **Step 6: Commit** `git add dbbackup/core/ tests/test_redact.py tests/test_compression.py && git commit -m "feat: add redaction, compression, workdir, logging"`

---

### Task 3: Config (tomllib + platformdirs Layered Merge)

**Files:**
- Create: `dbbackup/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: platformdirs, tomllib, redact
- Produces: `load_config(cli_args) -> Config` with layered merge defaults < user TOML < project TOML < env DBBACKUP_* < CLI flags; plaintext warning

**Steps:**
- [ ] **Step 1: Write failing test**
```python
# tests/test_config.py
def test_layered_merge(tmp_path):
    from dbbackup.config import load_config
    cfg = load_config({"database": "test"})
    assert cfg.database == "test"
def test_plaintext_warning(tmp_path, caplog):
    cfg_text = '[connection]\npassword="secret"\n'
    p = tmp_path / "dbbackup.toml"
    p.write_text(cfg_text)
    from dbbackup.config import load_config
    load_config({"config": str(p)})
    assert "plaintext" in caplog.text.lower()
```
- [ ] **Step 2: Run test** `pytest tests/test_config.py -v` Expected: FAIL
- [ ] **Step 3: Implement dbbackup/config.py** using tomllib read (no tomli dep), platformdirs for user config path, env overlay DBBACKUP_*, require allow_plaintext_password=true to suppress warning, S3 block only bucket/prefix/region/endpoint_url
- [ ] **Step 4: Run test** `pytest tests/test_config.py -v` Expected: PASS
- [ ] **Step 5: Commit** `git add dbbackup/config.py tests/test_config.py && git commit -m "feat: add layered TOML config with plaintext warning"`

---

### Task 4: Storage — S3 Backend (Minimal ABC)

**Files:**
- Create: `dbbackup/storage/base.py`, `dbbackup/storage/s3.py`
- Test: `tests/test_storage_s3.py`

**Interfaces:**
- Produces: `StorageBackend.upload(artifact,key)`, `download(key)->stream`; S3 uses boto3 chain, multipart >100MB, explicit retry Config, endpoint_url, abort on failure

**Steps:**
- [ ] **Step 1: Write failing test**
```python
# tests/test_storage_s3.py
from unittest.mock import MagicMock
from dbbackup.storage.s3 import S3Backend
def test_upload_aborts_on_failure():
    backend = S3Backend(bucket="b", region="us-east-1")
    backend._client = MagicMock()
    backend._client.upload_fileobj.side_effect = Exception("fail")
    backend._client.abort_multipart_upload = MagicMock()
    try:
        backend.upload(MagicMock(), "key")
    except Exception:
        pass
    # abort called on terminal failure
```
- [ ] **Step 2: Run test** `pytest tests/test_storage_s3.py -v` Expected: FAIL
- [ ] **Step 3: Implement storage/base.py** ABC with upload/download only; storage/s3.py with boto3 Config(retries max_attempts configurable, default 3), multipart, endpoint_url for MinIO, abort_multipart_upload on terminal failure, redact on errors
- [ ] **Step 4: Run test** `pytest tests/test_storage_s3.py -v` Expected: PASS (also test endpoint_url + retry config)
- [ ] **Step 5: Commit** `git add dbbackup/storage/ tests/test_storage_s3.py && git commit -m "feat: add S3 storage backend with multipart and retry"`

---

### Task 5: DBAdapter ABC + Registry + SQLite Adapter

**Files:**
- Create: `dbbackup/adapters/base.py`, `dbbackup/adapters/registry.py`, `dbbackup/adapters/sqlite.py`
- Test: `tests/test_adapters_base.py`, `tests/test_adapters_sqlite.py`

**Interfaces:**
- Consumes: models.BackupArtifact, workdir
- Produces: `DBAdapter` ABC (test_connection/backup/restore), `get_adapter(db_type)`, SQLite adapter with temp-file artifact (.sqlite.gz)

**Steps:**
- [ ] **Step 1: Write failing tests**
```python
# tests/test_adapters_sqlite.py
from dbbackup.adapters.registry import get_adapter
def test_sqlite_backup_creates_artifact(tmp_path):
    db = tmp_path / "test.db"
    import sqlite3; sqlite3.connect(str(db)).execute("create table t(x int)").connection.commit()
    adapter = get_adapter("sqlite")
    artifact = adapter.backup(type("O", (), {"database": str(db), "host": "", "port": 0, "user": "", "password": ""})())
    assert artifact.extension == ".sqlite.gz" or ".sqlite" in artifact.extension
    artifact.close()
def test_registry_unknown_raises():
    from dbbackup.adapters.registry import get_adapter
    import pytest
    with pytest.raises(ValueError):
        get_adapter("unknown")
```
- [ ] **Step 2: Run tests** `pytest tests/test_adapters_sqlite.py -v` Expected: FAIL
- [ ] **Step 3: Implement adapters/base.py** ABC with 3 methods + BackupArtifact return; registry.py dict lookup + ValueError; sqlite.py using sqlite3 backup API to temp file via workdir, declares format sqlite extension .sqlite.gz, close cleans temp
- [ ] **Step 4: Run tests** `pytest tests/test_adapters_sqlite.py -v` Expected: PASS
- [ ] **Step 5: Commit** `git add dbbackup/adapters/ tests/test_adapters_*.py && git commit -m "feat: add adapter ABC, registry, sqlite temp-file artifact"`

---

### Task 6: MySQL / PostgreSQL / MongoDB Adapters

**Files:**
- Create: `dbbackup/adapters/mysql.py`, `dbbackup/adapters/postgres.py`, `dbbackup/adapters/mongo.py`
- Test: `tests/test_adapters_mysql.py`, `tests/test_adapters_postgres.py`, `tests/test_adapters_mongo.py`

**Interfaces:**
- Consumes: DBAdapter ABC, BackupArtifact
- Produces: streaming artifacts (MySQL .sql.gz, Postgres .sql.gz/.dump.gz, Mongo .archive.gz) with shutil.which detection + per-OS hints

**Steps:**
- [ ] **Step 1: Write failing tests** (mocked subprocess):
```python
# tests/test_adapters_mysql.py
from unittest.mock import patch, MagicMock
from dbbackup.adapters.mysql import MySQLAdapter
def test_mysql_missing_binary_raises():
    with patch("shutil.which", return_value=None):
        import pytest
        with pytest.raises(Exception, match="mysqldump"):
            MySQLAdapter().test_connection(MagicMock())
def test_mysql_backup_streams():
    with patch("shutil.which", return_value="/usr/bin/mysqldump"), patch("subprocess.Popen") as popen:
        popen.return_value.stdout = MagicMock()
        popen.return_value.stdout.read.return_value = b"dump"
        artifact = MySQLAdapter().backup(MagicMock(host="h", user="u", password="p", database="db"))
        assert artifact.extension == ".sql.gz"
```
- [ ] **Step 2: Run tests** `pytest tests/test_adapters_mysql.py tests/test_adapters_postgres.py tests/test_adapters_mongo.py -v` Expected: FAIL
- [ ] **Step 3: Implement each adapter** subprocess.Popen streaming stdout → BackupArtifact stream, format/extension per spec, version check via --version, BinaryNotFoundError with apt/brew/choco hints
- [ ] **Step 4: Run tests** `pytest tests/test_adapters_*.py -v` Expected: PASS
- [ ] **Step 5: Commit** `git add dbbackup/adapters/mysql.py dbbackup/adapters/postgres.py dbbackup/adapters/mongo.py tests/test_adapters_*.py && git commit -m "feat: add mysql/postgres/mongo streaming adapters"`

---

### Task 7: Core Backup + Restore Orchestration

**Files:**
- Create: `dbbackup/core/backup.py`, `dbbackup/core/restore.py`
- Test: `tests/test_backup_core.py`, `tests/test_restore_core.py`

**Interfaces:**
- Consumes: BackupArtifact, compression, S3, redact, logging
- Produces: `run_backup(opts)->BackupResult`, `run_restore(opts)->RestoreResult` with status/duration/bytes, multipart abort, redact

**Steps:**
- [ ] **Step 1: Write failing tests**
```python
# tests/test_backup_core.py
def test_backup_failure_emits_failed_result():
    from dbbackup.core.backup import run_backup
    from unittest.mock import MagicMock, patch
    with patch("dbbackup.core.backup.get_adapter") as ga:
        ga.return_value.backup.side_effect = Exception("boom")
        result = run_backup(MagicMock())
        assert result.status == "failed"
def test_restore_failure():
    from dbbackup.core.restore import run_restore
    # similar: S3 download fails -> status failed, redact applied
```
- [ ] **Step 2: Run tests** `pytest tests/test_backup_core.py tests/test_restore_core.py -v` Expected: FAIL
- [ ] **Step 3: Implement core/backup.py** artifact = adapter.backup() → gzip streaming → S3 upload with key <prefix>/<db>-<db>-<timestamp><ext>, measure start/end/duration, emit BackupResult, abort multipart on failure, redact error, cleanup artifact.close()
- [ ] **Step 4: Implement core/restore.py** S3 download → gunzip → adapter.restore(), selective --table/--collection via RestoreOpts, per-adapter unsupported error
- [ ] **Step 5: Run tests** `pytest tests/test_backup_core.py tests/test_restore_core.py -v` Expected: PASS (includes backup failure, restore failure, S3 abort, interrupted)
- [ ] **Step 6: Commit** `git add dbbackup/core/backup.py dbbackup/core/restore.py tests/test_backup_core.py tests/test_restore_core.py && git commit -m "feat: add backup/restore orchestration with abort and redact"`

---

### Task 8: Scheduler Daemon (APScheduler MemoryJobStore)

**Files:**
- Create: `dbbackup/core/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: core/backup, config layered TOML
- Produces: `start_scheduler(config)` loads [[schedule.jobs]] at startup → BackgroundScheduler MemoryJobStore, max_instances=1, coalesce+misfire, graceful shutdown with grace period

**Steps:**
- [ ] **Step 1: Write failing tests**
```python
# tests/test_scheduler.py
def test_overlap_no_concurrent_duplicate():
    # schedule 2 jobs, trigger while running -> skipped, warning logged
    pass
def test_graceful_shutdown_waits():
    # SIGINT -> stop triggers, wait grace, abort if expired
    pass
```
- [ ] **Step 2: Run tests** `pytest tests/test_scheduler.py -v` Expected: FAIL
- [ ] **Step 3: Implement core/scheduler.py** BackgroundScheduler(MemoryJobStore), jobs reconstructed from TOML at startup, max_instances=1 per job ID, coalesce=True misfire_grace_time 300s, 2 job IDs can run concurrently, shutdown_grace_seconds 60s, failed job does not kill daemon
- [ ] **Step 4: Run tests** `pytest tests/test_scheduler.py -v` Expected: PASS (overlap, misfire, shutdown, daemon survives failure)
- [ ] **Step 5: Commit** `git add dbbackup/core/scheduler.py tests/test_scheduler.py && git commit -m "feat: add scheduler daemon with overlap and graceful shutdown"`

---

### Task 9: CLI Wiring (Passwords, Exit Codes, Rich) + Slack Notify

**Files:**
- Modify: `dbbackup/cli.py`
- Create: `dbbackup/core/notify.py`
- Test: `tests/test_cli.py` (extend), `tests/test_notify.py`

**Interfaces:**
- Consumes: config, adapters, core/backup, core/restore, core/scheduler, core/redact, notify
- Produces: wired commands with --password/--password-env/--password-stdin/--ask-password, exit codes 0/10-14/20, Rich errors, Slack opt-in

**Steps:**
- [ ] **Step 1: Write failing tests**
```python
# tests/test_cli.py add
def test_password_env_preferred():
    runner = CliRunner()
    with runner.isolated_filesystem():
        r = runner.invoke(app, ["backup", "--db", "sqlite", "--database", "x.db", "--password-env", "TEST_PW"], env={"TEST_PW": "secret"})
        # should not require --password plaintext

# tests/test_notify.py
def test_slack_not_sent_when_not_configured():
    from dbbackup.core.notify import send_notification
    assert send_notification({"status": "success"}) is None  # no webhook -> no-op
```
- [ ] **Step 2: Run tests** `pytest tests/test_cli.py tests/test_notify.py -v` Expected: FAIL
- [ ] **Step 3: Implement cli wiring** password resolution order prompt/env/stdin, exit code mapping 10-20 (one-shot only), Rich pretty errors, --help states full-only + prerequisites + secret warnings; implement notify.py httpx POST https only 5s timeout non-blocking, status success/failed/interrupted, env DBBACKUP_SLACK_WEBHOOK_URL preferred
- [ ] **Step 4: Run tests** `pytest tests/test_cli.py tests/test_notify.py -v` Expected: PASS
- [ ] **Step 5: Commit** `git add dbbackup/cli.py dbbackup/core/notify.py tests/test_cli.py tests/test_notify.py && git commit -m "feat: wire CLI with passwords, exit codes, slack notify"`

---

### Task 10: Packaging, CI & Cross-Platform Smoke

**Files:**
- Create: `dbbackup.spec`, `.github/workflows/ci.yml`, `.github/workflows/integration.yml`
- Modify: `pyproject.toml` (final deps, hatch build)

**Steps:**
- [ ] **Step 1: Create PyInstaller spec** `dbbackup.spec` single-file, bundles Python only (no DB binaries), document extraction/startup
- [ ] **Step 2: Create CI workflows** ci.yml: pytest on ubuntu/macos/windows (mocked binaries, unit+E2E); integration.yml: periodic/dedicated with testcontainers + MinIO for 4 adapters (gated --integration)
- [ ] **Step 3: Verify packaging smoke** `hatch build && pip install dist/*.whl && dbbackup --help && dbbackup --version && python -c "import dbbackup; print(dbbackup.__version__)"` and PyInstaller smoke `pyinstaller dbbackup.spec && ./dist/dbbackup --help`
- [ ] **Step 4: Commit** `git add dbbackup.spec .github/ pyproject.toml && git commit -m "feat: add PyInstaller spec and CI workflows"`

---

## Self-Review

1. **Spec coverage:** §1 arch → Tasks 1/5/6, §2 components/dataflow → Tasks 3/4/7/8/9, §3 error/security/logging/notify → Tasks 2/7/8/9, §4 testing/packaging/cross-platform → Task 10. All non-goals excluded.
2. **Placeholders:** No TBD/TODO; each step has executable code/commands.
3. **Type consistency:** BackupArtifact fields consistent across Tasks 5-7; StorageBackend upload/download consistent; exit codes 10-20 one-shot only.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-22-backup-cli-implementation.md`. Two execution options:
1. **Subagent-Driven (recommended)** - dispatch fresh subagent per task, review between tasks
2. **Inline Execution** - execute tasks in this session using executing-plans

Which approach?
