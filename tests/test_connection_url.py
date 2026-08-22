import pytest

from dbbackup.core.redact import redact
from dbbackup.core.url import parse_connection_url


def test_postgres_url_parsing():
    opts = parse_connection_url("postgresql://user:pass@host:5432/mydb")
    assert opts.db_type == "postgres"
    assert opts.host == "host"
    assert opts.port == 5432
    assert opts.user == "user"
    assert opts.password == "pass"
    assert opts.database == "mydb"


def test_postgres_alias():
    opts = parse_connection_url("postgres://user:pass@host/mydb")
    assert opts.db_type == "postgres"
    assert opts.host == "host"


def test_postgres_query_params():
    opts = parse_connection_url(
        "postgresql://user:pass@host:5432/mydb?sslmode=require&channel_binding=require"
    )
    assert opts.extra["sslmode"] == "require"
    assert opts.extra["channel_binding"] == "require"
    assert opts.database == "mydb"


def test_mysql_url():
    opts = parse_connection_url(
        "mysql://user:p%40ss@host:3306/mydb?ssl-mode=REQUIRED&charset=utf8mb4"
    )
    assert opts.db_type == "mysql"
    assert opts.host == "host"
    assert opts.port == 3306
    assert opts.user == "user"
    assert opts.password == "p@ss"
    assert opts.database == "mydb"
    assert opts.extra["ssl-mode"] == "REQUIRED"
    assert opts.extra["charset"] == "utf8mb4"


def test_mongodb_url():
    opts = parse_connection_url("mongodb://user:pass@host:27017/mydb?authSource=admin&tls=true")
    assert opts.db_type == "mongo"
    assert opts.host == "host"
    assert opts.port == 27017
    assert opts.user == "user"
    assert opts.password == "pass"
    assert opts.database == "mydb"
    assert opts.extra["authSource"] == "admin"
    assert opts.extra["tls"] == "true"


def test_mongodb_srv_url():
    opts = parse_connection_url("mongodb+srv://user:pass@cluster.mongodb.net/mydb?authSource=admin")
    assert opts.db_type == "mongo"
    assert "cluster.mongodb.net" in opts.host
    assert opts.port == 0  # SRV has no port
    assert opts.extra["authSource"] == "admin"


def test_mongodb_srv_with_optional_db():
    opts = parse_connection_url("mongodb+srv://user:pass@cluster.mongodb.net/?replicaSet=myReplica")
    assert opts.db_type == "mongo"
    assert opts.extra["replicaSet"] == "myReplica"


def test_sqlite_file_uri_relative():
    opts = parse_connection_url("file:./database.db")
    assert opts.db_type == "sqlite"
    assert opts.database == "./database.db"


def test_sqlite_file_uri_absolute():
    opts = parse_connection_url("file:/absolute/path/database.db")
    assert opts.db_type == "sqlite"
    assert opts.database == "/absolute/path/database.db"


def test_sqlite_file_uri_triple_slash():
    opts = parse_connection_url("file:///tmp/database.db")
    assert opts.db_type == "sqlite"
    assert opts.database == "/tmp/database.db"


def test_sqlite_file_uri_with_query():
    opts = parse_connection_url("file:./database.db?mode=ro&cache=shared")
    assert opts.db_type == "sqlite"
    assert opts.database == "./database.db"
    assert opts.extra["mode"] == "ro"
    assert opts.extra["cache"] == "shared"


def test_sqlite_structured_still_works():
    # Existing path-based behavior — not via URL parser, but ensure no regression
    from dbbackup.models import ConnectionOpts

    opts = ConnectionOpts(db_type="sqlite", database="./database.db")
    assert opts.db_type == "sqlite"
    assert opts.database == "./database.db"
    assert opts.host == ""
    assert opts.port == 0


def test_percent_encoded_credentials():
    opts = parse_connection_url("postgresql://user:p%40ss%3Aw%2Ford@host/mydb")
    assert opts.password == "p@ss:w/ord"
    opts2 = parse_connection_url("mysql://us%3Aer:p%40ss@host/db")
    assert opts2.user == "us:er"
    assert opts2.password == "p@ss"


def test_unsupported_scheme():
    with pytest.raises(ValueError, match="unsupported.*scheme"):
        parse_connection_url("redis://host/db")
    with pytest.raises(ValueError, match="unsupported.*scheme"):
        parse_connection_url("http://host/db")


def test_sqlite_fake_server_url_rejected():
    with pytest.raises(ValueError, match="unsupported.*scheme"):
        parse_connection_url("sqlite://user:pass@host/database")


def test_query_params_last_value_wins():
    opts = parse_connection_url("postgresql://user:pass@host/db?sslmode=disable&sslmode=require")
    assert opts.extra["sslmode"] == "require"


def test_url_redaction_postgres():
    raw = "postgresql://user:s3cret@host:5432/mydb"
    out = redact(raw)
    assert "s3cret" not in out
    assert "***" in out
    assert "user" in out


def test_url_redaction_mongodb_srv():
    raw = "mongodb+srv://user:mysecret@cluster.mongodb.net/mydb"
    out = redact(raw)
    assert "mysecret" not in out
    assert "***" in out


def test_url_redaction_query_password():
    raw = "postgresql://host/db?password=s3cret&foo=bar"
    out = redact(raw)
    assert "s3cret" not in out
    assert "***" in out
    assert "foo=bar" in out


def test_extra_preserved_not_forwarded_blindly():
    # Unknown params stored but not auto-forwarded to adapters — check they exist in extra
    opts = parse_connection_url("postgresql://user:pass@host/db?unknownParam=123&sslmode=require")
    assert opts.extra["unknownParam"] == "123"
    assert opts.extra["sslmode"] == "require"


def test_no_raw_url_in_extra_leak():
    opts = parse_connection_url("postgresql://user:secret@host/db?sslmode=require")
    # ConnectionOpts should not contain raw URL string in any field that leaks
    assert "secret" not in redact(str(opts.extra))
    # The password is stored decoded but redact would hide if logged via error
    assert opts.password == "secret"
    assert "secret" not in redact(f"postgres://user:{opts.password}@host/db")
