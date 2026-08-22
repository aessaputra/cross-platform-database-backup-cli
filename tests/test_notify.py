"""Notify tests — Slack opt-in, https only, timeout 5s non-blocking."""

from unittest.mock import MagicMock, patch


def test_slack_not_sent_when_not_configured(monkeypatch):
    monkeypatch.delenv("DBBACKUP_SLACK_WEBHOOK_URL", raising=False)
    from dbbackup.core.notify import send_notification

    assert send_notification({"status": "success"}) is None


def test_slack_https_only(monkeypatch):
    monkeypatch.setenv("DBBACKUP_SLACK_WEBHOOK_URL", "http://hooks.slack.com/xxx")
    from dbbackup.core.notify import send_notification

    # should not POST http
    with patch("dbbackup.core.notify.httpx.Client") as cls:
        send_notification({"status": "failed", "error": "boom"})
        cls.assert_not_called()


def test_slack_posts_when_configured(monkeypatch):
    monkeypatch.setenv("DBBACKUP_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/xxx")
    from dbbackup.core.notify import send_notification

    mock_resp = MagicMock(status_code=200)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp
    with patch("dbbackup.core.notify.httpx.Client", return_value=mock_client):
        send_notification({"status": "success"})
        mock_client.post.assert_called_once()
