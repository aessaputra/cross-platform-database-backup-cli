"""Scheduler release-hardening regression tests.

Focus: active-job detection, grace wait, no-job immediate, failed-job isolation, mixed backends.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch


def _slow_result(delay=0.6):
    def _fn(opts=None):
        time.sleep(delay)
        return MagicMock(status="success")

    return _fn


def test_scheduler_active_job_detection():
    from apscheduler.executors.pool import ThreadPoolExecutor
    from apscheduler.jobstores.memory import MemoryJobStore
    from apscheduler.schedulers.background import BackgroundScheduler

    from dbbackup.core.scheduler import SchedulerDaemon, _make_job_runner

    sched = BackgroundScheduler(
        jobstores={"default": MemoryJobStore()},
        executors={"default": ThreadPoolExecutor(max_workers=2)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )
    daemon = SchedulerDaemon(sched, shutdown_grace_seconds=4)
    daemon.scheduler.start()
    try:
        runner = _make_job_runner(
            "j1",
            lambda: MagicMock(connection=MagicMock(db_type="sqlite", database="d")),
            daemon_ref=daemon._active_jobs,
        )
        # inject into scheduler
        from datetime import datetime, timedelta

        from apscheduler.triggers.date import DateTrigger

        sched.add_job(
            runner,
            trigger=DateTrigger(run_date=datetime.now() + timedelta(milliseconds=50)),
            id="j1",
            replace_existing=True,
        )
        # start job quickly
        time.sleep(0.2)
        # _any_job_running should eventually be True while runner active; we test via direct registry
        # Simulate active by calling runner in thread
        with patch("dbbackup.core.scheduler.run_backup", side_effect=_slow_result(0.8)):
            runner2 = _make_job_runner(
                "j1b",
                lambda: MagicMock(connection=MagicMock(db_type="sqlite", database="d")),
                daemon_ref=daemon._active_jobs,
            )
            t = threading.Thread(target=runner2, daemon=True)
            t.start()
            time.sleep(0.15)
            assert daemon._any_job_running() is True
            t.join(timeout=2)
            assert daemon._any_job_running() is False
    finally:
        try:
            daemon.shutdown(wait=False)
        except Exception:
            pass


def test_scheduler_grace_waits_within_period():
    from dbbackup.core.scheduler import start_scheduler

    started = threading.Event()

    def slow(opts=None):
        started.set()
        time.sleep(0.7)
        return MagicMock(status="success")

    with patch("dbbackup.core.scheduler.run_backup", side_effect=slow):
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "a",
                        "interval_seconds": 9999,
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                    }
                ],
                "shutdown_grace_seconds": 3,
            }
        }
        daemon = start_scheduler(config)
        try:
            # trigger job via direct call in thread
            job = daemon.get_job("a")
            t = threading.Thread(target=job.func, daemon=True)  # type: ignore[union-attr]
            t.start()
            started.wait(timeout=2)
            assert started.is_set()
            t0 = time.monotonic()
            # wait_for_shutdown path: set event and wait
            daemon.request_shutdown()
            code = daemon.wait_for_shutdown()
            elapsed = time.monotonic() - t0
            assert code == 0
            assert elapsed >= 0.5
        finally:
            try:
                daemon.shutdown(wait=False)
            except Exception:
                pass


def test_scheduler_no_active_jobs_immediate():
    from dbbackup.core.scheduler import start_scheduler

    with patch("dbbackup.core.scheduler.run_backup"):
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "a",
                        "interval_seconds": 9999,
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                    }
                ],
                "shutdown_grace_seconds": 2,
            }
        }
        daemon = start_scheduler(config)
        try:
            daemon.request_shutdown()
            t0 = time.monotonic()
            code = daemon.wait_for_shutdown()
            elapsed = time.monotonic() - t0
            assert code == 0
            assert elapsed < 1.0
        finally:
            try:
                daemon.shutdown(wait=False)
            except Exception:
                pass


def test_scheduler_mixed_local_s3_jobs():
    from dbbackup.core.scheduler import start_scheduler

    with patch(
        "dbbackup.core.scheduler.run_backup", return_value=MagicMock(status="success")
    ) as mock_run:
        config = {
            "schedule": {
                "jobs": [
                    {
                        "id": "local_job",
                        "interval_seconds": 9999,
                        "db_type": "sqlite",
                        "database": "d",
                        "storage": "local",
                        "local_path": "/tmp",
                    },
                    {
                        "id": "s3_job",
                        "interval_seconds": 9999,
                        "db_type": "sqlite",
                        "database": "d",
                        "s3_bucket": "b",
                    },
                ],
                "shutdown_grace_seconds": 2,
            }
        }
        daemon = start_scheduler(config)
        try:
            assert daemon.get_job("local_job") is not None
            assert daemon.get_job("s3_job") is not None
            # invoke both
            daemon.get_job("local_job").func()  # type: ignore
            daemon.get_job("s3_job").func()  # type: ignore
            assert mock_run.call_count == 2
            # check opts were correctly typed
            calls = [c.args[0] for c in mock_run.call_args_list]
            storages = {c.storage_type for c in calls}
            assert storages == {"local", "s3"}
        finally:
            try:
                daemon.shutdown(wait=False)
            except Exception:
                pass
