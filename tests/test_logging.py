import logging

from dbbackup.core.logging_setup import RedactFilter, setup_logging


def test_redact_filter_redacts_password(caplog=None):  # noqa: ARG001
    # I3 fix: RedactFilter no longer mutates LogRecord in place; redaction
    # happens at format time via RedactingFormatter/JsonFormatter (propagate-safe).
    f = RedactFilter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="password=secret123", args=(), exc_info=None,
    )
    assert f.filter(record)
    # filter must not mutate the shared record
    assert "secret123" in record.getMessage()
    from dbbackup.core.logging_setup import JsonFormatter, RedactingFormatter

    fmt = RedactingFormatter("%(message)s")
    out = fmt.format(record)
    assert "secret123" not in out
    assert "***" in out
    # JSON formatter also redacts message
    jfmt = JsonFormatter()
    jout = jfmt.format(record)
    assert "secret123" not in jout
    assert "***" in jout


def test_setup_logging_creates_handlers(tmp_path, monkeypatch):
    # monkeypatch platformdirs to use tmp_path
    import platformdirs

    monkeypatch.setattr(platformdirs, "user_log_dir", lambda *a, **kw: str(tmp_path / "logs"))
    monkeypatch.setattr(platformdirs, "user_state_dir", lambda *a, **kw: str(tmp_path / "state"))
    logger = setup_logging(level="DEBUG", json_format=False)
    assert logger is not None
    # should have at least a handler (file or console)
    assert len(logger.handlers) >= 1
    # redact filter attached
    has_redact = any(isinstance(flt, RedactFilter) for h in logger.handlers for flt in h.filters)
    assert has_redact
    # cleanup handlers to avoid leaking into other tests
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)


def test_setup_logging_redacts_file_output(tmp_path, monkeypatch):
    import platformdirs

    monkeypatch.setattr(platformdirs, "user_log_dir", lambda *a, **kw: str(tmp_path / "logs2"))
    monkeypatch.setattr(platformdirs, "user_state_dir", lambda *a, **kw: str(tmp_path / "state2"))
    logger = setup_logging(level="DEBUG", json_format=False)
    logger.info("connecting with password=supersecret url postgres://u:pwd@host/db")
    # flush all handlers
    for h in logger.handlers:
        try:
            h.flush()
        except Exception:
            pass
    # check file content is redacted
    log_dir = tmp_path / "logs2"
    # also try state2 if fallback
    candidates = list(tmp_path.rglob("*.log"))
    assert candidates, f"no log file created, tmp contents: {list(tmp_path.rglob('*'))}"
    content = candidates[0].read_text()
    assert "supersecret" not in content
    assert "pwd" not in content or "***" in content
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)
