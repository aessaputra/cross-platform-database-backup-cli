"""Centralized redaction — single sanitization layer for all sensitive data.

Applied before console, file log, BackupResult.error, and Slack payload.

Covers: DB passwords (password=/passwd=/pwd=), DB URL credentials,
Slack webhook URLs, S3 tokens/keys.
"""

from __future__ import annotations

import re

# password / passwd / pwd key=value (exclude & so query strings don't swallow next param)
_RE_PASSWORD_KV = re.compile(r"(?i)(password|passwd|pwd)(\s*[:=]\s*)([^\s\"'`,;&]+)")

# S3 / generic token key=value  (aws_secret_access_key, secret_access_key, access_key, token)
_RE_TOKEN_KV = re.compile(
    r"(?i)(aws_secret_access_key|secret_access_key|aws_access_key_id|access_key|secret_key|token)(\s*[:=]\s*)([^\s\"'`,;]+)"
)

# DB URL credentials: scheme://user:password@  -> redact password part
_RE_URL_CREDS = re.compile(r"(\w+://[^/\s:]+:)([^@\s/]+)(@)")

# Query-string password: ?password=secret, &passwd=secret, ?pwd=secret -> keep trailing &
_RE_QUERY_PASSWORD = re.compile(r"(?i)([?&](password|passwd|pwd)=)([^&\s\"'`,;]*)(&?)")

# Slack webhook URLs
_RE_SLACK_WEBHOOK = re.compile(
    r"https://hooks\.slack\.com[^\s\"']*",
    re.IGNORECASE,
)

_REDACTED = "***"


def redact(text: str | None) -> str:
    """Redact sensitive values from *text*.

    Returns sanitized string with secrets replaced by ``***``.
    Handles None/empty gracefully.
    Idempotent — already-redacted values remain redacted.
    """
    if not text:
        return ""
    s = str(text)

    # Slack webhooks first (broad URL match before more granular URL handling)
    s = _RE_SLACK_WEBHOOK.sub("https://hooks.slack.com/***", s)

    # DB URL credentials: postgres://user:***@host  -> postgres://user:***@host
    # Apply before KV patterns so URL password is caught even without password= prefix
    s = _RE_URL_CREDS.sub(rf"\1{_REDACTED}\3", s)

    # Query-string password: ?password=secret&foo= -> ?password=***&
    def _query_pw_repl(m: re.Match[str]) -> str:
        prefix = m.group(1)
        val = m.group(3)
        trail = m.group(4) or ""
        if val == _REDACTED:
            return m.group(0)
        # preserve ? or & and key= part, keep trailing &
        return f"{prefix}{_REDACTED}{trail}"

    s = _RE_QUERY_PASSWORD.sub(_query_pw_repl, s)

    # password= / passwd= / pwd=
    def _pw_repl(m: re.Match[str]) -> str:
        key, sep = m.group(1), m.group(2)
        val = m.group(3)
        if val == _REDACTED:
            return m.group(0)
        return f"{key}{sep}{_REDACTED}"

    s = _RE_PASSWORD_KV.sub(_pw_repl, s)

    # S3 / token key=value
    def _token_repl(m: re.Match[str]) -> str:
        key, sep = m.group(1), m.group(2)
        val = m.group(3)
        if val == _REDACTED:
            return m.group(0)
        return f"{key}{sep}{_REDACTED}"

    s = _RE_TOKEN_KV.sub(_token_repl, s)

    # Generic fallback: if the original URL creds pattern left a bare :***@ that's fine;
    # also catch any remaining ://user:*** already handled. No extra step needed.

    return s
