# Connection URL — Implementation Plan (Addendum dua mode setara)

> **For agentic workers:** This is an ADDENDUM to `docs/superpowers/plans/2026-08-22-backup-cli-implementation.md` (v1). Use this addendum after v1 is implemented, or alongside v1 if connection-url work is done next. All v1 tasks remain unchanged. This plan covers only the Connection URL feature per `docs/superpowers/specs/2026-08-22-connection-url-design.md` (locked: dua mode Structured vs URL, mutually exclusive).

**Goal:** Tambahkan mode Connection URL (`--url`/`--uri` + `DATABASE_URL`/`DBBACKUP_URL` + TOML `[connection].url` + scheduler `[[schedule.jobs]].url`) sebagai mode setara dengan structured mode, tanpa merusak structured mode dan tanpa mengubah kontrak `DBAdapter`.

**Spec:** `docs/superpowers/specs/2026-08-22-connection-url-design.md` (+ baseline `2026-08-22-backup-cli-design.md`)

**Constraints (dari spec yang dikunci):**
- Dua mode setara, mutually exclusive: `--url` tidak boleh digabung dengan `--host`/`--port`/`--user`/`--database`/password flags → `exit 10` actionable. Jangan precedence override.
- `--db` bukan legacy/deprecated. Structured mode tetap first-class. `--db` wajib tanpa `--url`; opsional dengan `--url` (infer dari scheme); bila keduanya ada hanya untuk validasi konsistensi, konflik → `exit 10`.
- URL diparse menjadi `ConnectionOpts` + `ConnectionOpts.extra` (query params); tidak ada raw URL passthrough ke driver; kontrak `DBAdapter` tetap.
- Config: `CLI --url > DATABASE_URL > DBBACKUP_URL > TOML [connection].url > field terpisah` — hasil env/TOML URL diperlakukan sama seperti `--url` (eksklusif). TOML tidak boleh berisi `url` + field terpisah sekaligus.
- Scheduler: per-job `url` XOR structured fields → konflik fail-fast `exit 10` saat daemon start.
- Redaction terpusat sebelum console/log/BackupResult/Slack; tidak log raw URL.
- Backward compat: semua flag existing tetap ada.

---

## Task A: URL parser + ConnectionOpts.extra

**Files:**
- Create or modify: `dbbackup/core/url.py` (new module for URL parsing; alternative: `dbbackup/core/connection_url.py` — pick one)
- Modify: `dbbackup/models.py` (add `ConnectionOpts.extra: dict[str, str] = field(default_factory=dict)`)
- Test: `tests/test_connection_url.py` (new) + extend `tests/test_redact.py` for URL redaction cases

**Interfaces:**
- Produces: `parse_connection_url(url: str) -> tuple[ConnectionOpts, dict]` or `parse_connection_url(url: str) -> ConnectionOpts` with `extra` populated internally; `SUPPORTED_SCHEMES` set; scheme→db_type mapping; sqlite/mongodb+srv special cases.

**Steps:**
- [ ] **Step 1: Write failing tests** `tests/test_connection_url.py`:
```python
import pytest
from dbbackup.core.url import parse_connection_url


def test_postgres_infers_db_type():
    opts = parse_connection_url("postgresql://user:pass@host:5432/mydb?sslmode=require")
    assert opts.db_type == "postgres"
    assert opts.host == "host"
    assert opts.port == 5432
    assert opts.user == "user"
    assert opts.password == "pass"
    assert opts.database == "mydb"
    assert opts.extra["sslmode"] == "require"


def test_mysql_scheme():
    opts = parse_connection_url("mysql://u:p%40ss@h:3306/db?ssl-mode=REQUIRED")
    assert opts.db_type == "mysql"
    assert opts.password == "p@ss"
    assert opts.extra["ssl-mode"] == "REQUIRED"


def test_mongodb_srv_no_port():
    opts = parse_connection_url("mongodb+srv://u:p@cluster.mongodb.net/mydb?authSource=admin")
    assert opts.db_type == "mongo"
    assert opts.extra["authSource"] == "admin"


def test_sqlite_abs_and_rel():
    assert parse_connection_url("sqlite:////tmp/app.db").database == "/tmp/app.db"
    assert parse_connection_url("sqlite:///rel/app.db").database == "rel/app.db"


def test_sqlite_memory_rejected():
    with pytest.raises(ValueError, match=":memory:"):
        parse_connection_url("sqlite:///:memory:")


def test_unknown_scheme():
    with pytest.raises(ValueError, match="unsupported.*scheme"):
        parse_connection_url("redis://host/db")


def test_missing_scheme_separator():
    with pytest.raises(ValueError, match="invalid.*url"):
        parse_connection_url("not-a-url")


def test_percent_encoded_password():
    opts = parse_connection_url("postgresql://u:p%3A%40s@host/db")
    assert opts.password == "p:@s"


def test_query_params_last_value_wins():
    opts = parse_connection_url("postgresql://u:p@h/db?a=1&a=2")
    assert opts.extra["a"] == "2"
```
- [ ] **Step 2: Run failing** `pytest tests/test_connection_url.py -v` Expected: FAIL (module missing)
- [ ] **Step 3: Implement `dbbackup/models.py`** — add `extra: dict[str,str]` to `ConnectionOpts` (default empty dict, `field(default_factory=dict)`). No other model changes.
- [ ] **Step 4: Implement `dbbackup/core/url.py`** — `urlparse` + `parse_qs(keep_blank_values=True)` + `unquote` for user/password/database; host/port handling (port defaults: postgres 5432/mysql 3306/mongo 27017; mongodb+srv no port); `sqlite:////` vs `sqlite:///` branches; `unquote` on query values; `extra` last-value-wins; raise `ValueError` with message suitable for `exit 10` (caller redacts).
- [ ] **Step 5: Run passing** `pytest tests/test_connection_url.py -v` Expected: PASS
- [ ] **Step 6: Redaction extension tests** — extend `tests/test_redact.py` for `redact("postgresql://user:secret@host/db")` and `redact("...?password=secret")`.

---

## Task B: CLI — dua mode eksklusif (--url/--uri + conflict)

**Files:**
- Modify: `dbbackup/cli.py` (add `--url`/`--uri`, enforce mutual exclusivity, `--db` optional when `--url` present, scheme↔`--db` consistency)
- Test: extend `tests/test_cli.py` (CliRunner conflict cases) + `tests/test_adapter_url_validation.py` if needed

**Steps:**
- [ ] **Step 1: Write failing tests** `tests/test_cli.py` add:
```python
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
from dbbackup.cli import app

runner = CliRunner()


def test_url_mode_valid_without_db():
    with (
        patch("dbbackup.adapters.registry.get_adapter") as ga,
        patch("dbbackup.core.backup.run_backup") as rb,
    ):
        ga.return_value.test_connection = MagicMock()
        rb.return_value = MagicMock(status="success", s3_key="k", bytes_written=1, error=None)
        # backup --url implies db_type from scheme; no --db/--host needed
        r = runner.invoke(
            app, ["backup", "--url", "postgresql://user:pass@host/db", "--s3-bucket", "bkt"]
        )
        # should not exit 10 for missing --db


def test_url_plus_host_conflict():
    r = runner.invoke(
        app,
        ["backup", "--url", "postgresql://user@host/db", "--host", "other", "--s3-bucket", "bkt"],
    )
    assert r.exit_code == 10
    assert "--url cannot be combined with --host" in r.output


def test_url_plus_password_flag_conflict():
    r = runner.invoke(
        app,
        [
            "backup",
            "--url",
            "postgresql://user:pass@host/db",
            "--password",
            "x",
            "--s3-bucket",
            "bkt",
        ],
    )
    assert r.exit_code == 10


def test_url_plus_db_consistent_ok():
    with (
        patch("dbbackup.adapters.registry.get_adapter") as ga,
        patch("dbbackup.core.backup.run_backup") as rb,
    ):
        ga.return_value.test_connection = MagicMock()
        rb.return_value = MagicMock(status="success", s3_key="k", bytes_written=1, error=None)
        r = runner.invoke(
            app,
            ["backup", "--db", "postgres", "--url", "postgresql://u:p@h/db", "--s3-bucket", "bkt"],
        )
        assert r.exit_code == 0


def test_url_plus_db_conflict():
    r = runner.invoke(
        app, ["backup", "--db", "mysql", "--url", "postgresql://u:p@h/db", "--s3-bucket", "bkt"]
    )
    assert r.exit_code == 10
    assert "conflicts with --db" in r.output


def test_structured_still_requires_db():
    r = runner.invoke(app, ["backup", "--host", "h", "--database", "db", "--s3-bucket", "bkt"])
    assert r.exit_code == 10
    assert "--db" in r.output.lower() or "db" in r.output.lower()
```
- [ ] **Step 2: Run failing** `pytest tests/test_cli.py -k url -v` Expected: FAIL
- [ ] **Step 3: Implement `dbbackup/cli.py`**:
  - Add `url: str | None = typer.Option(None, "--url", help="Database connection URL ... Exclusive with --host/...")` and `--uri` hidden alias both routing to same value (e.g. `url_uri` helper or two options with callback that sets single `connection_url` var).
  - In `backup`/`restore`/`test_connection`: detect `connection_url = url or uri`; if set: reject any of `host`/`port`/`user`/`database`/password flags that are explicitly provided → `err_console.print(redact(...)); raise typer.Exit(10)`. Detect explicit flags vs defaults (compare against Option defaults; for `port` check non-zero only if provided — or track via Typer callback).
  - Structured mode: if `connection_url` is None, require `--db` (existing validation, message unchanged, no legacy wording).
  - URL mode: `opts = parse_connection_url(connection_url)`; if `--db` provided validate `opts.db_type` vs `db` (normalize `postgresql`/`postgres` etc.) else use inferred `db_type`; on mismatch `exit 10` with actionable message.
  - Do NOT implement URL→flag override merging.
  - Ensure `--help` never says legacy/deprecated for `--db`.
- [ ] **Step 4: Run passing** `pytest tests/test_cli.py -k url -v` Expected: PASS (+ existing tests still pass)
- [ ] **Step 5: Manual smoke** `dbbackup backup --help` — verify help mentions two modes neutrally.

---

## Task C: Config + env (DATABASE_URL) — eksklusif

**Files:**
- Modify: `dbbackup/config.py` (add `url` resolution: CLI > DATABASE_URL > DBBACKUP_URL > TOML [connection].url > structured; conflict detection)
- Test: extend `tests/test_config.py`

**Steps:**
- [ ] **Step 1: Write failing tests** `tests/test_config.py` add:
```python
import os
from pathlib import Path


def test_database_url_treated_as_url_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    from dbbackup.config import load_config

    cfg = load_config({})
    assert cfg.connection_url == "postgresql://u:p@host/db"


def test_dbbackup_url_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DBBACKUP_URL", "mysql://u:p@host/db")
    from dbbackup.config import load_config

    cfg = load_config({})
    assert cfg.connection_url == "mysql://u:p@host/db"
    assert cfg.database == "" or cfg.host == ""  # url mode, not structured


def test_toml_url_and_structured_conflict(tmp_path):
    p = tmp_path / "dbbackup.toml"
    p.write_text('[connection]\nurl = "postgresql://u:p@host/db"\nhost = "other"\n')
    from dbbackup.config import load_config

    with pytest.raises(SystemExit) as ei:  # or ValueError depending on design
        load_config({"config": str(p)})
    # caller maps to exit 10 with redacted message


def test_env_url_plus_toml_structured_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    p = tmp_path / "dbbackup.toml"
    p.write_text('[connection]\nhost = "other"\n')
    from dbbackup.config import load_config
    # should fail: url mode (from env) conflicts with structured in TOML
```
- [ ] **Step 2: Run failing** `pytest tests/test_config.py -k url -v` Expected: FAIL
- [ ] **Step 3: Implement `dbbackup/config.py`**:
  - Add `connection_url` / `url` field to `Config` dataclass (or `connection_url: str|None`).
  - Resolution: check `cli_args["url"]`/`cli_args["uri"]` first; else `os.environ["DATABASE_URL"]` then `DBBACKUP_URL`; else `toml [connection].url` (project then user TOML per existing layered order, CLI overrides env overrides TOML).
  - Conflict detection: if effective `url` is set and any structured field (`host`/`port`/`user`/`database`/`password`) is also set from higher-priority source (env or TOML structured), raise `ConfigError` / `ValueError` that CLI maps to `exit 10` (redacted). Do NOT silently prefer url.
  - Backward compat: no `url` → existing structured resolution unchanged.
- [ ] **Step 4: Run passing** `pytest tests/test_config.py -k url -v` Expected: PASS
- [ ] **Step 5: Wire CLI to config** — `backup`/`restore`/`test_connection` should route through `load_config` for URL when not directly passed (or CLI handles env fallback directly; decide one place to avoid double resolution).

---

## Task D: Scheduler — dua mode per job (fail-fast)

**Files:**
- Modify: `dbbackup/core/scheduler.py` (per-job url vs structured exclusivity, inference, fail-fast)
- Modify: `dbbackup/config.py` if scheduler reads TOML directly (raw dict path)
- Test: extend `tests/test_scheduler.py`

**Steps:**
- [ ] **Step 1: Write failing tests** `tests/test_scheduler.py` add:
```python
def test_scheduler_url_mode_job():
    cfg = {
        "schedule": {
            "jobs": [
                {
                    "id": "a",
                    "url": "postgresql://u:p@host/db",
                    "cron": "0 3 * * *",
                    "s3_bucket": "b",
                }
            ]
        }
    }
    d = start_scheduler(cfg)
    assert d.scheduler.get_job("a") is not None
    d.shutdown(wait=False)


def test_scheduler_job_url_plus_host_conflict():
    cfg = {
        "schedule": {
            "jobs": [
                {
                    "id": "bad",
                    "url": "postgresql://u:p@host/db",
                    "host": "other",
                    "cron": "0 3 * * *",
                }
            ]
        }
    }
    with pytest.raises(ValueError, match="cannot be combined"):
        start_scheduler(cfg)


def test_scheduler_job_url_infers_db_type():
    cfg = {
        "schedule": {
            "jobs": [
                {"id": "a", "url": "mysql://u:p@host/db", "cron": "0 3 * * *", "s3_bucket": "b"}
            ]
        }
    }
    # _job_to_opts should infer db_type == mysql without explicit db_type
```
- [ ] **Step 2: Run failing** `pytest tests/test_scheduler.py -k url -v` Expected: FAIL
- [ ] **Step 3: Implement `dbbackup/core/scheduler.py`**:
  - In `_job_to_opts`: if `job.get("url")` present, reject if any of `host`/`port`/`user`/`database`/`password` also present in same job → raise `ValueError` (daemon start fails, `exit 10` in CLI `schedule --daemon`).
  - Infer `db_type` from URL scheme when `db_type` absent; validate when both present.
  - Global `[connection].url` inheritance: only if job defines neither `url` nor structured fields; do not merge global structured fields into job URL.
  - Use same `parse_connection_url` as CLI.
- [ ] **Step 4: Run passing** `pytest tests/test_scheduler.py -k url -v` Expected: PASS

---

## Task E: Redaction + docs polish + E2E

**Files:**
- Modify: `dbbackup/core/redact.py` (query-string password coverage)
- Modify: `README.md` (document two modes neutrally)
- Test: `tests/test_redact.py`, CLI E2E `tests/test_cli.py`, `tests/test_scheduler.py` E2E

**Steps:**
- [ ] **Step 1: Redaction tests** — ensure `redact("postgresql://user:secret@host/db")` → `***`, `redact("postgresql://host/db?password=secret")` → `***`, Slack/S3 unchanged.
- [ ] **Step 2: Implement `dbbackup/core/redact.py`** — add `_RE_QUERY_PASSWORD` if needed; keep idempotent; ensure `BackupResult.error` path already redacts.
- [ ] **Step 3: README** — document both modes as valid, with examples for each (no legacy wording). Mark `DATABASE_URL` support.
- [ ] **Step 4: E2E smoke** — `CliRunner` backup/restore/test-connection with `--url` and structured both succeed (mocked adapters/storage); redaction verified in output.
- [ ] **Step 5: Run full suite** `pytest -q` Expected: PASS (no regression on structured mode).

---

## Self-Review (plan-level)

1. **Spec coverage:** Seksi 1 CLI → Task B, Seksi 2 parser/extra → Task A, Seksi 3 config/env → Task C, Seksi 4 scheduler → Task D, Seksi 5 redaction/driver → Task E. Two-mode exclusivity enforced in CLI, config, and scheduler (no override).
2. **Placeholders:** No TBD/TODO; each step has executable test/code.
3. **Type consistency:** `ConnectionOpts.extra` dict consistent across Tasks A/B/D; `parse_connection_url` return type consistent; exit 10 for all conflict/validation paths.
4. **No-go enforcement:** Tasks do not modify `DBAdapter` ABC; no raw URL passthrough; no legacy wording.

---
*Addendum generated for Connection URL feature (dua mode setara). Specs: `docs/superpowers/specs/2026-08-22-connection-url-design.md` + baseline. No source code changed in this planning step.*
