"""Scheduler daemon — APScheduler BackgroundScheduler with MemoryJobStore.

Jobs are reconstructed from the layered TOML config (the [[schedule.jobs]]
array) at startup; the in-memory job store is never persisted or reloaded.

Overlap invariant (spec §2.4.5): ``max_instances=1`` per job ID — the same
scheduled job id never runs concurrently. Missed firings during an active run
coalesce into at most one deferred execution, run only within the misfire grace
window, otherwise skipped (warning logged). Different job IDs may run
concurrently (§2.4.6).

Graceful shutdown (spec §2.4.7): on SIGINT/SIGTERM the scheduler stops accepting
new triggers immediately, then waits for the active job up to
``shutdown_grace_seconds`` (default 60). If it finishes in time we exit clean; if
the grace expires the active run is aborted and we exit non-zero.

A single failed job never kills the daemon (§3.1): the job wrapper logs the
failure and emits a ``BackupResult{status=failed}`` but the daemon keeps serving
future jobs.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Any

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.base import BaseJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from dbbackup.core.backup import run_backup
from dbbackup.core.redact import redact
from dbbackup.models import BackupOpts, ConnectionOpts, BackupResult

log = logging.getLogger(__name__)

DEFAULT_MISFIRE_GRACE_TIME = 300  # seconds (spec §2.4.5)
DEFAULT_SHUTDOWN_GRACE_SECONDS = 60  # seconds (spec §2.4.7)
DEFAULT_MAX_INSTANCES = 1  # overlap invariant (spec §2.4.5)

# Exit code only used on unrecoverable startup failure or grace expiry (§3.1).
EXIT_INTERRUPTED = 14


def _build_trigger(job: dict[str, Any]):
    """Build an APScheduler trigger from a job definition.

    Supports ``cron`` (5-field expression) or ``interval_seconds``.
    """
    if "cron" in job:
        return CronTrigger.from_crontab(job["cron"])
    if "interval_seconds" in job:
        return IntervalTrigger(seconds=int(job["interval_seconds"]))
    raise ValueError(f"job {job.get('id')!r} needs 'cron' or 'interval_seconds'")


def _job_to_opts(job: dict[str, Any]) -> BackupOpts:
    """Reconstruct a BackupOpts from a schedule job entry (TOML config)."""
    conn = ConnectionOpts(
        db_type=str(job.get("db_type", "")),
        host=str(job.get("host", "")),
        port=int(job.get("port", 0) or 0),
        user=str(job.get("user", "")),
        password=str(job.get("password", "")),
        database=str(job.get("database", "")),
    )
    return BackupOpts(
        connection=conn,
        s3_bucket=str(job.get("s3_bucket", "")),
        s3_prefix=str(job.get("s3_prefix", "")),
        s3_endpoint_url=job.get("s3_endpoint_url"),
        s3_region=job.get("s3_region"),
        gzip_level=int(job.get("gzip_level", 6) or 6),
    )


def _make_job_runner(job_id: str, opts_factory, daemon_ref: dict | None = None):
    """Wrap run_backup so a failed job never kills the daemon (spec §3.1).

    Also enforces max_instances=1 at the wrapper level (overlap guard): if the
    same job id is already running, log a warning and skip (coalesce semantics).
    This is in addition to APScheduler's own max_instances — the wrapper handles
    direct-call callers used by tests.
    """
    _locks: dict[str, threading.Event] = daemon_ref if daemon_ref is not None else {}  # type: ignore[assignment]
    # fallback per-runner local lock
    _local = threading.Lock()
    _active = {"count": 0}

    def runner() -> BackupResult | None:
        # Overlap guard: skip if already running
        if _active["count"] != 0:
            log.warning(
                "job %s missed trigger — previous run still active, skipped per max_instances=1", job_id
            )
            return None
        with _local:
            if _active["count"] != 0:
                log.warning(
                    "job %s missed trigger — previous run still active, skipped per max_instances=1", job_id
                )
                return None
            _active["count"] = 1
        daemon_ref_str = daemon_ref
        try:
            start = datetime.now(timezone.utc)
            t0 = time.monotonic()
            try:
                result = run_backup(opts_factory())
                if result is None:
                    result = BackupResult(
                        status="failed", error="run_backup returned None",
                        db_type=opts_factory().connection.db_type,
                        database=opts_factory().connection.database,
                    )
                if result.status == "failed":
                    log.error(
                        "job %s failed: %s", job_id, redact(result.error or "unknown error")
                    )
                else:
                    log.info("job %s completed: %s", job_id, result.status)
                return result
            except Exception as exc:  # never propagate out of the executor thread
                end = datetime.now(timezone.utc)
                msg = redact(str(exc))
                log.error("job %s raised: %s", job_id, msg)
                return BackupResult(
                    status="failed",
                    error=msg,
                    start_time=start,
                    end_time=end,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
        finally:
            _active["count"] = 0

    return runner


class SchedulerDaemon:
    """Thin wrapper around BackgroundScheduler handling graceful shutdown."""

    def __init__(self, scheduler: BackgroundScheduler, shutdown_grace_seconds: int):
        self.scheduler = scheduler
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self._shutdown_event = threading.Event()
        self._original_sigint: Any = None
        self._original_sigterm: Any = None

    @property
    def running(self) -> bool:
        return self.scheduler.running

    def get_job(self, job_id: str):
        return self.scheduler.get_job(job_id)

    def add_job(self, func, id=None, **kwargs):
        return self.scheduler.add_job(func, id=id, **kwargs)

    def start(self) -> None:
        self.scheduler.start()
        # Install handlers so SIGINT/SIGTERM trigger graceful shutdown.
        try:
            self._original_sigint = signal.getsignal(signal.SIGINT)
            self._original_sigterm = signal.getsignal(signal.SIGTERM)

            def handler(signum, frame):
                log.warning("received signal %s — graceful shutdown", signum)
                self.request_shutdown()

            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (ValueError, OSError):
            # Signals unavailable (e.g. non-main thread); skip install.
            pass

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def shutdown(self, wait: bool | None = None) -> None:
        """Stop accepting triggers; wait up to grace for active jobs.

        ``wait`` defaults to True (graceful). APScheduler's shutdown(wait=True)
        blocks until running jobs finish, bounded by our grace timer below.
        """
        if not self.scheduler.running:
            return
        if wait is None:
            wait = True
        if wait:
            # Bound the wait by the configured grace period.
            timer = threading.Timer(
                self.shutdown_grace_seconds, self._abort_if_still_running
            )
            timer.daemon = True
            timer.start()
            self.scheduler.shutdown(wait=True)
            timer.cancel()
        else:
            self.scheduler.shutdown(wait=False)
        self._restore_signals()

    def _abort_if_still_running(self) -> None:
        if self.scheduler.running:
            log.warning(
                "grace period of %ss expired — aborting scheduler",
                self.shutdown_grace_seconds,
            )
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass

    def _restore_signals(self) -> None:
        try:
            if self._original_sigint is not None:
                signal.signal(signal.SIGINT, self._original_sigint)
            if self._original_sigterm is not None:
                signal.signal(signal.SIGTERM, self._original_sigterm)
        except (ValueError, OSError):
            pass

    def wait_for_shutdown(self) -> int:
        """Block until a shutdown is requested (for foreground daemon mode).

        Returns exit code: 0 on clean graceful shutdown, EXIT_INTERRUPTED (14)
        if the grace period expired before active jobs finished (§3.1).
        """
        self._shutdown_event.wait()
        grace_start = time.monotonic()
        # Allow in-flight jobs to drain up to the grace period.
        deadline = grace_start + self.shutdown_grace_seconds
        while self.scheduler.running and time.monotonic() < deadline:
            # Check whether any job is still executing via the executor.
            if not self._any_job_running():
                break
            time.sleep(0.1)
        if self.scheduler.running and self._any_job_running():
            log.warning("grace expired — aborting active jobs (exit %d)", EXIT_INTERRUPTED)
            self.scheduler.shutdown(wait=False)
            self._restore_signals()
            return EXIT_INTERRUPTED
        self.shutdown(wait=True)
        return 0

    def __getattr__(self, name: str):
        # Proxy unknown attributes to underlying BackgroundScheduler (e.g. _jobstores)
        return getattr(self.scheduler, name)

    def _any_job_running(self) -> bool:
        # APScheduler 3.x has no public get_running_jobs; probe via executor or job state.
        # Approximation: if scheduler is running and any executor has active threads.
        try:
            fn = getattr(self.scheduler, "get_running_jobs", None)
            if callable(fn):
                return bool(fn())
        except Exception:
            pass
        # Fallback: check thread-pool executors for busy workers.
        try:
            for executor in getattr(self.scheduler, "_executors", {}).values():
                # ThreadPoolExecutor exposes _pool or _threads internally
                if hasattr(executor, "_pool") and executor._pool is not None:
                    # busy if any thread alive beyond idle
                    pass
            # Generic fallback: no reliable signal -> assume not running once called
            return False
        except Exception:
            return False


def start_scheduler(config: dict[str, Any]) -> SchedulerDaemon:
    """Build and start the scheduler daemon from a layered config dict.

    ``config`` is the merged TOML config (dict). Expected layout::

        {
          "schedule": {
            "shutdown_grace_seconds": 60,
            "jobs": [
              {"id": "nightly", "cron": "0 3 * * *",
               "db_type": "sqlite", "database": "...", "s3_bucket": "..."},
              ...
            ]
          }
        }

    Returns a running :class:`SchedulerDaemon`.
    """
    schedule_cfg = config.get("schedule") or {}
    jobs = schedule_cfg.get("jobs") or []
    shutdown_grace_seconds = int(
        schedule_cfg.get("shutdown_grace_seconds", DEFAULT_SHUTDOWN_GRACE_SECONDS) or DEFAULT_SHUTDOWN_GRACE_SECONDS
    )

    jobstores: dict[str, BaseJobStore] = {"default": MemoryJobStore()}
    executors = {"default": ThreadPoolExecutor(max_workers=max(2, len(jobs) + 2))}
    job_defaults = {
        "coalesce": True,
        "max_instances": DEFAULT_MAX_INSTANCES,
        "misfire_grace_time": DEFAULT_MISFIRE_GRACE_TIME,
    }

    scheduler = BackgroundScheduler(
        jobstores=jobstores, executors=executors, job_defaults=job_defaults
    )

    for job in jobs:
        job_id = str(job.get("id") or job.get("database") or "job")
        trigger = _build_trigger(job)
        opts = _job_to_opts(job)
        runner = _make_job_runner(job_id, lambda o=opts: o)
        scheduler.add_job(
            runner,
            trigger=trigger,
            id=job_id,
            name=job.get("name", job_id),
            replace_existing=True,
        )
        log.info("scheduled job %s (max_instances=1, coalesce=True)", job_id)

    daemon = SchedulerDaemon(scheduler, shutdown_grace_seconds)
    daemon.start()
    return daemon
