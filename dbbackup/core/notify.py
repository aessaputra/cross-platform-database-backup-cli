"""Slack notify — opt-in via DBBACKUP_SLACK_WEBHOOK_URL env or TOML (opt-in with warning)."""

from __future__ import annotations

import logging
import os

import httpx

from dbbackup.core.redact import redact

log = logging.getLogger(__name__)


def send_notification(payload: dict) -> None:
    """Send backup result to Slack if webhook configured. Non-blocking on failure."""
    url = os.environ.get("DBBACKUP_SLACK_WEBHOOK_URL", "")
    # TOML webhook is explicit opt-in; caller should pass it via payload if needed
    if not url:
        url = payload.get("webhook_url") or ""
    if not url:
        return
    if not url.startswith("https://"):
        log.warning("slack webhook must be https — skipped")
        return
    # redact payload before send
    safe_payload = {
        k: redact(str(v)) if isinstance(v, str) else v
        for k, v in payload.items()
        if k != "webhook_url"
    }
    safe_payload.setdefault("status", payload.get("status", "unknown"))
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json=safe_payload)
            if resp.status_code >= 400:
                log.warning("slack notify failed: %s", resp.status_code)
    except Exception as exc:
        log.warning("slack notify error: %s", redact(str(exc)))
