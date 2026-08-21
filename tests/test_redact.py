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
    assert "***" in redact("postgres://user:s3cret@host/db")
    assert "s3cret" not in redact("postgres://user:s3cret@host/db")
    assert "***" in redact("postgres://user:***@host/db")  # idempotent
    assert "***" in redact("mysql://root:p@ssw0rd@localhost/mydb")


def test_redacts_slack_webhook():
    assert "***" in redact("https://hooks.slack.com/services/T000/B000/XXXX")
    assert "XXXX" not in redact("https://hooks.slack.com/services/T000/B000/XXXX")
    assert "***" in redact("https://hooks.slack.com/xxx")


def test_redacts_s3_token():
    assert "***" in redact("aws_secret_access_key=AKIAIOSFODNN7EXAMPLE")
    assert "AKIAIOSFODNN7EXAMPLE" not in redact("aws_secret_access_key=AKIAIOSFODNN7EXAMPLE")
    assert "***" in redact("token=mysecrettoken123")


def test_redacts_none_and_empty():
    assert redact("") == ""
    assert redact(None) == ""  # type: ignore[arg-type]
    assert redact("hello world") == "hello world"


def test_redacts_multiple_secrets():
    text = "password=foo postgres://u:bar@host/db https://hooks.slack.com/xxx"
    out = redact(text)
    assert "foo" not in out
    assert "bar" not in out
    assert "***" in out
