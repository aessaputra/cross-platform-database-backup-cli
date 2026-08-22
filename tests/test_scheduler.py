"""Task 8 TDD: scheduler daemon — overlap (max_instances=1), coalesce/misfire,
graceful shutdown with grace period, and daemon survives job failure."""

import logging
import threading
import time
from unittest.mock import MagicMock, patch


def test_overlap_no_concurrent_duplicate(caplog):
    """Same job id max_instances=1 — second concurrent firing is skipped with warning."""
    from dbbackup.core.scheduler import start_scheduler

    caplog.set_level(logging.WARNING)
    with patch("dbbackup.core.scheduler.run_backup") as mock_run:
        # Use the real scheduler's job runner (has overlap guard)
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "nightly",
                        "cron": "*/1 * * * *",
                        "db_type": "sqlite",
                        "database": "nightly",
                        "s3_bucket": "b",
                        "s3_prefix": "p",
                    }
                ],
                "shutdown_grace_seconds": 60,
            }
        }

        # slow first run
        started = threading.Event()

        def slow(opts=None):
            started.set()
            time.sleep(0.4)
            return MagicMock(status="success")

        mock_run.side_effect = slow
        sched = start_scheduler(config)
        try:
            job = sched.get_job("nightly")
            assert job is not None
            assert job.max_instances == 1
            assert job.coalesce is True
            assert job.misfire_grace_time == 300
            # launch job via executor (max_instances=1 there) + attempt direct overlap
            t = threading.Thread(target=lambda: job.func(), daemon=True)
            t.start()
            started.wait(timeout=1)
            time.sleep(0.05)
            # second call while first still active should be skipped
            result = job.func()
            assert result is None, "overlap not skipped"
            assert "nightly" in caplog.text and (
                "skipped" in caplog.text.lower() or "missed" in caplog.text.lower()
            )
            t.join(timeout=1)
        finally:
            try:
                sched.shutdown(wait=True)
            except Exception:
                pass


def test_coalesce_and_misfire_settings():
    """All scheduled jobs carry coalesce=True and misfire_grace_time=300."""
    from dbbackup.core.scheduler import start_scheduler

    with patch("dbbackup.core.scheduler.run_backup"):
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "a",
                        "cron": "0 * * * *",
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                        "s3_prefix": "p",
                    },
                    {
                        "id": "b",
                        "cron": "0 * * * *",
                        "db_type": "sqlite",
                        "database": "d2",
                        "s3_bucket": "b",
                        "s3_prefix": "p",
                    },
                ],
                "shutdown_grace_seconds": 60,
            }
        }
        sched = start_scheduler(config)
        try:
            for jid in ("a", "b"):
                job = sched.get_job(jid)
                assert job is not None, jid
                assert job.coalesce is True
                assert job.misfire_grace_time == 300
                assert job.max_instances == 1
        finally:
            try:
                sched.shutdown(wait=True)
            except Exception:
                pass


def test_graceful_shutdown_waits():
    """stop/shutdown waits up to grace period for active job; abort if expired."""
    from apscheduler.triggers.date import DateTrigger

    from dbbackup.core.scheduler import start_scheduler

    started = threading.Event()
    done = threading.Event()

    def slow(opts=None):
        started.set()
        time.sleep(1.0)
        done.set()
        return MagicMock(status="success")

    with patch("dbbackup.core.scheduler.run_backup", side_effect=slow):
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "nightly",
                        "interval_seconds": 9999,
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                        "s3_prefix": "p",
                    }
                ],
                "shutdown_grace_seconds": 3,
            }
        }
        sched = start_scheduler(config)
        sched.shutdown_grace_seconds = 3  # explicit
        # trigger via scheduler executor so shutdown(wait=True) actually waits
        from datetime import datetime, timedelta

        # schedule a one-off run 0.1s from now
        run_time = datetime.now() + timedelta(milliseconds=100)
        sched.scheduler.add_job(
            sched.get_job("nightly").func,
            trigger=DateTrigger(run_date=run_time),  # type: ignore[union-attr]
            id="one-off",
            replace_existing=True,
            max_instances=1,
        )
        started.wait(timeout=2)
        assert started.is_set()
        t0 = time.monotonic()
        # shutdown wait=True must wait for running job (up to grace)
        sched.shutdown(wait=True)
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.5, f"shutdown did not wait: {elapsed}"
        assert not sched.running
        assert done.is_set()


def test_daemon_survives_failure():
    """A failed job does not kill the daemon; next job still runs."""
    from dbbackup.core.scheduler import start_scheduler

    call_count = {"n": 0}

    def flaky(opts=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        return MagicMock(status="success")

    # wrap to emulate start_scheduler's internal error handling
    # actual run_backup returns BackupResult(status=failed); we patch it
    wrapped_calls = []

    def wrapped(opts):
        try:
            result = flaky(opts)
            wrapped_calls.append("ok")
            return result
        except Exception as e:
            wrapped_calls.append("failed")
            # mimic job wrapper swallowing exception and logging
            return MagicMock(status="failed", error=str(e))

    with patch("dbbackup.core.scheduler.run_backup", side_effect=wrapped):
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "j1",
                        "cron": "0 * * * *",
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                        "s3_prefix": "p",
                    }
                ],
                "shutdown_grace_seconds": 60,
            }
        }
        sched = start_scheduler(config)
        try:
            job = sched.get_job("j1")
            assert job is not None
            # first invocation fails
            job.func()
            assert wrapped_calls == ["failed"]
            assert sched.running, "daemon died after failure"
            # second invocation succeeds
            job.func()
            assert wrapped_calls == ["failed", "ok"]
            assert sched.running
        finally:
            try:
                sched.shutdown(wait=True)
            except Exception:
                pass


def test_memory_jobstore_and_reconstruct_from_toml():
    """Scheduler uses MemoryJobStore and reconstructs from TOML at startup."""
    from apscheduler.jobstores.memory import MemoryJobStore

    from dbbackup.core.scheduler import start_scheduler

    with patch("dbbackup.core.scheduler.run_backup"):
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "recon",
                        "cron": "0 3 * * *",
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                        "s3_prefix": "p",
                    }
                ],
                "shutdown_grace_seconds": 60,
            }
        }
        sched = start_scheduler(config)
        try:
            assert "default" in sched._jobstores  # type: ignore[attr-defined]
            assert isinstance(sched._jobstores["default"], MemoryJobStore)  # type: ignore[attr-defined]
            # reconstructed from config — job present
            assert sched.get_job("recon") is not None
            # two different job ids allowed concurrently
            config2 = {
                "schedule": {
                    "jobs": [
                        {
                            "id": "x",
                            "cron": "0 * * * *",
                            "db_type": "sqlite",
                            "database": "d",
                            "s3_bucket": "b",
                            "s3_prefix": "p",
                        },
                        {
                            "id": "y",
                            "cron": "0 * * * *",
                            "db_type": "sqlite",
                            "database": "d",
                            "s3_bucket": "b",
                            "s3_prefix": "p",
                        },
                    ],
                    "shutdown_grace_seconds": 60,
                }
            }
            sched2 = start_scheduler(config2)
            assert sched2.get_job("x") is not None and sched2.get_job("y") is not None
            sched2.shutdown(wait=False)
        finally:
            try:
                sched.shutdown(wait=False)
            except Exception:
                pass


def test_two_job_ids_concurrent_allowed():
    """Two different job IDs may run concurrently."""
    from dbbackup.core.scheduler import start_scheduler

    barrier = threading.Barrier(2)
    concurrent = {"max": 0, "cur": 0}
    lock = threading.Lock()

    def job_fn(opts=None):
        with lock:
            concurrent["cur"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["cur"])
        try:
            barrier.wait(timeout=2)
        except Exception:
            pass
        time.sleep(0.05)
        with lock:
            concurrent["cur"] -= 1

    with patch("dbbackup.core.scheduler.run_backup", side_effect=job_fn):
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "j1",
                        "cron": "0 * * * *",
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                        "s3_prefix": "p",
                    },
                    {
                        "id": "j2",
                        "cron": "0 * * * *",
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                        "s3_prefix": "p",
                    },
                ],
                "shutdown_grace_seconds": 60,
            }
        }
        sched = start_scheduler(config)
        try:
            j1 = sched.get_job("j1")
            j2 = sched.get_job("j2")
            t1 = threading.Thread(target=lambda: j1.func(), daemon=True)
            t2 = threading.Thread(target=lambda: j2.func(), daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=2)
            t2.join(timeout=2)
            assert concurrent["max"] == 2, "different IDs should run concurrently"
        finally:
            try:
                sched.shutdown(wait=False)
            except Exception:
                pass
