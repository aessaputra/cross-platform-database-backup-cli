from dbbackup.core.redact import redact


def test_redacts_password():
    assert "secret123" not in redact("password=secret123")
    assert "***" in redact("password=secret123")
    assert "***" in redact("passwd=foo")
    out = redact("password=secret123")
    assert "secret123" not in out


def test_redacts_password_case_insensitive():
    assert "***" in redact("PASSWORD=secret")
    assert "***" in redact("Passwd=foo")
    assert "secret" not in redact("PASSWORD=secret")


def test_redacts_connection_string():
    # Real secret — not pre-redacted input. Must redact password and not leak it.
    raw = "postgres://alice:s3cret@db.example.com:5432/mydb"
    out = redact(raw)
    assert "s3cret" not in out
    assert "***" in out
    assert "alice" in out  # user preserved, password redacted
    # Idempotent: already-redacted stays redacted
    assert "***" in redact("postgres://user:***@host/db")
    # Second host variant with @-like password (redact still masks)
    raw2 = "mysql://root:p@ssw0rd@localhost/mydb"
    out2 = redact(raw2)
    assert "***" in out2


def test_redacts_slack_webhook():
    assert "***" in redact("https://hooks.slack.com/services/T000/B000/XXXX")
    assert "XXXX" not in redact("https://hooks.slack.com/services/T000/B000/XXXX")
    assert "***" in redact("https://hooks.slack.com/xxx")


def test_redacts_s3_token():
    assert "***" in redact("aws_secret_access_key=«redacted:AKIA…»")
    assert "«redacted:AKIA…»" not in redact("aws_secret_access_key=«redacted:AKIA…»")
    assert "***" in redact("token=mysecrettoken123")


def test_redacts_none_and_empty():
    assert redact("") == ""
    assert redact(None) == ""  # type: ignore[arg-type]
    assert redact("hello world") == "hello world"

def test_redacts_multiple_secrets():
    text = "password=foo postgres://u:s3cret@host/db https://hooks.slack.com/xxx"
    out = redact(text)
    assert "foo" not in out
    assert "s3cret" not in out
    assert "***" in out
