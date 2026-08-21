# Task 2 Report — Centralized Redaction + Logging + Compression + Workdir

**Date:** 2026-08-22
**Task:** 2 — Centralized Redaction + Logging + Compression + Workdir
**Spec:** `docs/superpowers/specs/2026-08-22-backup-cli-design.md` §3.2, §3.3
**Plan:** `docs/superpowers/plans/2026-08-22-backup-cli-implementation.md` Task 2
**Status:** ✅ Done

## TDD

- RED: wrote `tests/test_redact.py`, `test_compression.py`, `test_workdir.py`, `test_logging.py` first; `pytest` failed with `ModuleNotFoundError: No module named 'dbbackup.core.*'` (verified).
- GREEN: implemented 4 core modules; `pytest tests/test_redact.py tests/test_compression.py tests/test_workdir.py tests/test_logging.py -v` → 19 passed; full suite with `test_cli.py` → 21 passed.

## Commits

| Hash | Message |
|------|---------|
| `e58bb2b` | `feat: add redaction, compression, workdir, logging` — redact.py, compression.py, workdir.py, logging_setup.py + 4 test files |

`git log --oneline` @ finish: `e58bb2b <- 908e18d <- 9b41fa4 <- ef30a2d <- 152a70f`

## Files

- **Created:** `dbbackup/core/__init__.py`, `dbbackup/core/redact.py`, `dbbackup/core/compression.py`, `dbbackup/core/workdir.py`, `dbbackup/core/logging_setup.py`, `tests/test_redact.py`, `tests/test_compression.py`, `tests/test_workdir.py`, `tests/test_logging.py`
- **Modified:** none pre-existing
- **Interfaces produced:**
  - `dbbackup.core.redact.redact(text)->str` — centralized redaction (password/passwd/pwd KV, DB URL credentials `://user:pass@`, Slack `hooks.slack.com`, S3 tokens `aws_secret_access_key`/`token`); idempotent, handles None/empty, used before console/log/BackupResult/Slack
  - `dbbackup.core.compression.compress_stream(src, dest, level=1-9)->int` + `decompress_stream(src, dest)->int` + alias `gzip_stream`; streaming via `gzip.GzipFile`/`shutil.copyfileobj`, level validation
  - `dbbackup.core.workdir.TempWorkdir` — context-managed owner-restricted temp dir (0700 POSIX, restricted inheritance on Windows), `temp_file(suffix,prefix)` with 0600 files, cleanup on success/failure/exception, post-exit `path` retained for inspection
  - `dbbackup.core.logging_setup.setup_logging(level, json_format, log_dir, max_bytes, backup_count)->Logger` — `RotatingFileHandler` via `platformdirs` (log_dir→`user_log_dir`/`user_state_dir` fallback), `RichHandler` console, `RedactFilter` on all handlers, `JsonFormatter` option, idempotent handler reset

## Test Summary

```
pytest tests/test_redact.py tests/test_compression.py tests/test_workdir.py tests/test_logging.py -v
tests/test_redact.py::test_redacts_password PASSED
tests/test_redact.py::test_redacts_password_case_insensitive PASSED
tests/test_redact.py::test_redacts_connection_string PASSED
tests/test_redact.py::test_redacts_slack_webhook PASSED
tests/test_redact.py::test_redacts_s3_token PASSED
tests/test_redact.py::test_redacts_none_and_empty PASSED
tests/test_redact.py::test_redacts_multiple_secrets PASSED
tests/test_compression.py::test_gzip_roundtrip PASSED
tests/test_compression.py::test_gzip_levels PASSED
tests/test_compression.py::test_gzip_invalid_level_raises PASSED
tests/test_compression.py::test_gzip_empty PASSED
tests/test_workdir.py::test_workdir_creates_and_cleans PASSED
tests/test_workdir.py::test_workdir_owner_restricted_posix PASSED
tests/test_workdir.py::test_workdir_tempfile_helper PASSED
tests/test_workdir.py::test_workdir_cleanup_on_exception PASSED
tests/test_workdir.py::test_workdir_path_is_path PASSED
tests/test_logging.py::test_redact_filter_redacts_password PASSED
tests/test_logging.py::test_setup_logging_creates_handlers PASSED
tests/test_logging.py::test_setup_logging_redacts_file_output PASSED
19 passed in 0.06s
```

Full suite including Task 1: 21 passed.

## Verification

- Global constraints met: Python >=3.11 (host 3.13), `tomllib` stdlib (no `tomli` dep added), `platformdirs` source of truth for log dir, S3-only, full-only, external binaries not bundled, centralized redaction, owner-restricted temp per OS, RotatingFileHandler (10MB/5 backups, configurable).
- Redaction covers §3.2 shapes: `password=`/`passwd=`/`pwd=`, connection-string `://user:pass@`, Slack `hooks.slack.com`, S3 `aws_secret_access_key`/`token`; applied before console/log via `RedactFilter`.
- Compression streaming, level 1-9, round-trip verified including empty input.
- Workdir 0700/0600 on POSIX verified, exception cleanup verified.

## Concerns / Follow-ups

- **Workdir fix:** initial `cleanup()` nulled `_path`, breaking post-exit `wd.path.exists()` assertion in tests; fixed to retain `_path` (exists() returns False after rmtree, still inspectable).
- **No blockers.** Task 3 (`config.py` layered merge) can consume `redact` and `platformdirs` log dir; Tasks 4-7 can use `TempWorkdir`/`compression`/`RedactFilter`.

## Checklist

- [x] Step 1: failing tests written (4 files)
- [x] Step 2: verified FAIL (ModuleNotFoundError)
- [x] Step 3: core/redact.py (KV + URL + webhook + token)
- [x] Step 4: core/compression.py + core/workdir.py + core/logging_setup.py
- [x] Step 5: tests PASS (19/19, 21 with Task 1)
- [x] Step 6: committed (e58bb2b)

---

## Fix Round 1/5 — Review Findings (base e58bb2b)

**Date:** 2026-08-22
**Findings source:** `.hermes/cache/delegation/subagent-summary-0-20260822_044643_345914.txt` (Task 2 Review — Spec FAIL / Quality: NEEDS WORK)
**Base:** `e58bb2b`

### Issues addressed

| ID | Severity | File | Fix |
|----|----------|------|-----|
| C1 | Critical | `dbbackup/core/logging_setup.py:JsonFormatter` | `exc_info`/`stack_info` now redacted via `redact(formatException(...))` / `redact(formatStack(...))` with fail-closed `***` fallback. Verified `ValueError('password=supersecret ...')` not leaked. |
| C2 | Critical | `dbbackup/models.py:BackupResult` | `__post_init__` redacts `error` via `redact()` at assignment; fail-closed to `***` if redact throws. Single enforcement layer so Slack/backup paths inherit it. |
| I1 | Important | `tests/test_redact.py:test_redacts_connection_string` | Replaced vacuous input `postgres://user:***@host/db` with real secret `postgres://alice:s3cret@host/db`; asserts `s3cret` not in output, `***` present, user preserved, idempotency still checked. Also fixed `test_redacts_multiple_secrets` to use `s3cret`. |
| I2 | Important | `dbbackup/core/workdir.py:TempWorkdir.__enter__` | TOCTOU window closed: `os.umask(0o077)` around `mkdtemp` on POSIX (restored after), plus retained `chmod 0700` for defense-in-depth. |
| I3 | Important | `dbbackup/core/logging_setup.py:RedactFilter` | Filter no longer mutates shared `LogRecord` (`msg`/`args` in place). Instead `RedactingFormatter` copies record at format time and redacts copy + final string. `RedactFilter` kept as pass-through for handler-attachment checks (`propagate=False` already but now propagate-safe). `JsonFormatter` also copy-safe via `redact(getMessage())` + redacted `exc_info`/`stack_info`. |

### Verification

```
pytest tests/test_redact.py tests/test_compression.py tests/test_workdir.py tests/test_logging.py -v
19 passed (7 redact + 4 compression + 5 workdir + 3 logging)

Manual redact checks:
- JsonFormatter with ValueError('password=supersecret ...'): exc_info redacted — PASS (*** in exc_info, supersecret absent)
- BackupResult(status='failed', error='postgres://alice:s3cret@host/db ... password=bar'): error='postgres://alice:***@host/db ... password=***' — PASS
- redact('postgres://alice:s3cret@host/db') -> 'postgres://alice:***@host/db' — PASS
- stack_info 'password=hunter2' redacted — PASS
- RedactingFormatter propagate-safe (record not mutated) — PASS
```

### Commits

| Hash | Message |
|------|---------|
| 882638e | `fix(task-2): redact exc_info/stack_info, BackupResult.error, vacuous URL test, TOCTOU + filter mutation` |

### Follow-ups / Not re-addressed this round

- I4 platformdirs fallback duplicate mkdir — trivial, left as-is (both paths mkdir exist_ok).
- M1-M4 minor — no change.

### Checklist (fix round)

- [x] JsonFormatter redacts exc_info/stack_info
- [x] BackupResult.error redacted at construction
- [x] test_redacts_connection_string uses real secret + negative assertion
- [x] workdir umask 0o077 TOCTOU fix
- [x] RedactFilter no longer mutates shared record (RedactingFormatter copy-at-format)
- [x] pytest 19/19 + manual checks PASS
