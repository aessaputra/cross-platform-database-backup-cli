"""Task 4 — S3 backend: abort on failure, endpoint_url, retry config, multipart threshold."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from dbbackup.storage.s3 import S3Backend


def test_upload_aborts_on_failure():
    backend = S3Backend(bucket="b", region="us-east-1")
    backend._client = MagicMock()
    backend._client.upload_fileobj.side_effect = Exception("fail")
    backend._client.abort_multipart_upload = MagicMock()
    try:
        backend.upload(MagicMock(), "key")
    except Exception:
        pass
    # abort must be attempted on terminal failure to avoid orphan parts
    assert (
        backend._client.abort_multipart_upload.called
        or backend._client.abort_multipart_upload.call_count >= 0
    )
    # stricter: must have been called at least once
    assert backend._client.abort_multipart_upload.call_count >= 1, (
        "abort_multipart_upload not called on upload failure"
    )


def test_endpoint_url_passed_to_boto3():
    with patch("dbbackup.storage.s3.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        S3Backend(bucket="b", region="us-east-1", endpoint_url="http://minio:9000")
        _, kwargs = mock_boto3.client.call_args
        assert kwargs.get("endpoint_url") == "http://minio:9000"


def test_retry_config_default_3():
    with patch("dbbackup.storage.s3.boto3") as mock_boto3:
        with patch("dbbackup.storage.s3.Config") as MockConfig:
            mock_boto3.client.return_value = MagicMock()
            S3Backend(bucket="b", region="us-east-1")
            MockConfig.assert_called_once()
            _, kwargs = MockConfig.call_args
            retries = kwargs.get("retries", {})
            assert retries.get("max_attempts") == 3


def test_retry_config_custom():
    with patch("dbbackup.storage.s3.boto3") as mock_boto3:
        with patch("dbbackup.storage.s3.Config") as MockConfig:
            mock_boto3.client.return_value = MagicMock()
            S3Backend(bucket="b", region="us-east-1", max_attempts=7)
            _, kwargs = MockConfig.call_args
            assert kwargs.get("retries", {}).get("max_attempts") == 7


def test_multipart_threshold_used_for_large_upload():
    """Upload of >100MB artifact should use multipart TransferConfig."""
    with patch("dbbackup.storage.s3.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        backend = S3Backend(bucket="b", region="us-east-1")
        # patch TransferConfig inside the method's module
        with patch("dbbackup.storage.s3.TransferConfig") as MockTC:
            mock_tc_instance = MagicMock()
            MockTC.return_value = mock_tc_instance
            # large artifact: size_hint > 100MB
            artifact = MagicMock()
            artifact.size_hint = 150 * 1024 * 1024
            stream = io.BytesIO(b"x" * 10)
            artifact.open_stream.return_value = stream
            artifact.stream_or_path = stream
            backend._client = mock_client
            try:
                backend.upload(artifact, "big/key")
            except Exception:
                pass
            # TransferConfig must have been instantiated with multipart threshold ~100MB
            assert MockTC.called, "TransferConfig not used for large upload"
            _, tc_kwargs = MockTC.call_args
            # threshold key may be multipart_threshold
            assert tc_kwargs.get("multipart_threshold") == 100 * 1024 * 1024


def test_upload_small_does_not_require_multipart_threshold():
    """Small upload should still succeed without requiring large-file setup."""
    backend = S3Backend(bucket="my-bucket", region="us-east-1")
    backend._client = MagicMock()
    artifact = MagicMock()
    artifact.size_hint = 1024
    artifact.open_stream.return_value = io.BytesIO(b"hello")
    artifact.stream_or_path = io.BytesIO(b"hello")
    # should not raise
    backend.upload(artifact, "small/key")
    assert backend._client.upload_fileobj.called


def test_error_message_is_redacted():
    backend = S3Backend(bucket="b", region="us-east-1")
    backend._client = MagicMock()
    # simulate failure whose message contains a secret
    backend._client.upload_fileobj.side_effect = Exception("password=secret123 upload failed")
    backend._client.abort_multipart_upload = MagicMock()
    artifact = MagicMock()
    artifact.open_stream.return_value = io.BytesIO(b"data")
    artifact.stream_or_path = io.BytesIO(b"data")
    with pytest.raises(Exception, match=r"\*\*\*"):
        backend.upload(artifact, "key")


def test_download_returns_stream():
    backend = S3Backend(bucket="b", region="us-east-1")
    backend._client = MagicMock()

    def fake_download_fileobj(Bucket, Key, Fileobj, **kwargs):
        Fileobj.write(b"hello world")

    backend._client.download_fileobj.side_effect = fake_download_fileobj
    stream = backend.download("some/key")
    assert stream.read() == b"hello world"
