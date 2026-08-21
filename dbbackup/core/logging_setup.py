"""Logging setup: RotatingFileHandler via platformdirs + Rich console + redact filter."""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path

import platformdirs
from rich.console import Console
from rich.logging import RichHandler

from dbbackup.core.redact import redact

LOG_FILENAME = "dbbackup.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


class RedactFilter(logging.Filter):
    """Logging filter that redacts secrets from every record."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        # Redact the message
        if record.msg:
            try:
                record.msg = redact(str(record.msg))
            except Exception:
                pass
        # Redact args if present
        if record.args:
            try:
                if isinstance(record.args, dict):
                    record.args = {k: redact(str(v)) for k, v in record.args.items()}  # type: ignore[assignment]
                elif isinstance(record.args, tuple):
                    record.args = tuple(redact(str(a)) for a in record.args)  # type: ignore[assignment]
            except Exception:
                pass
        # Also redact exc_info text is handled via formatter; args/msg covers most cases
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "name": record.name,
            "message": redact(record.getMessage()),
        }
        if record.exc_info and record.exc_info[0] is not None:
            data["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(data)


def _resolve_log_dir() -> Path:
    try:
        d = Path(platformdirs.user_log_dir("dbbackup"))
    except Exception:
        d = Path(platformdirs.user_state_dir("dbbackup")) / "log"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(
    level: str | int = "INFO",
    json_format: bool = False,
    log_dir: str | Path | None = None,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
) -> logging.Logger:
    """Configure root dbbackup logger with file + Rich console handlers.

    Idempotent — clears existing handlers on the ``dbbackup`` logger.
    Returns the ``dbbackup`` logger.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    logger = logging.getLogger("dbbackup")
    logger.setLevel(level)
    logger.propagate = False

    # Clear existing handlers (idempotent re-setup, avoids duplicate logs in tests)
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)

    redact_filter = RedactFilter()

    # File handler
    try:
        resolved_dir = Path(log_dir) if log_dir is not None else _resolve_log_dir()
        resolved_dir.mkdir(parents=True, exist_ok=True)
        log_file = resolved_dir / LOG_FILENAME
        fh = logging.handlers.RotatingFileHandler(
            str(log_file), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setLevel(level)
        if json_format:
            fh.setFormatter(JsonFormatter())
        else:
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        fh.addFilter(redact_filter)
        logger.addHandler(fh)
    except Exception:
        # File handler is best-effort; console still works
        pass

    # Rich console handler
    try:
        console = Console(stderr=True)
        rh = RichHandler(console=console, show_time=True, show_path=False, markup=False)
        rh.setLevel(level)
        rh.setFormatter(logging.Formatter("%(message)s"))
        rh.addFilter(redact_filter)
        logger.addHandler(rh)
    except Exception:
        # Fallback to plain stderr
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(level)
        sh.addFilter(redact_filter)
        logger.addHandler(sh)

    return logger
