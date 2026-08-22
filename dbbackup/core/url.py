"""DB-specific connection URL parsing.

Supports:
  postgresql://, postgres:// -> postgres
  mysql:// -> mysql (dbbackup convenience, synthetic)
  mongodb://, mongodb+srv:// -> mongo
  file: -> sqlite (file:./db.sqlite, file:/abs/db.sqlite, file:///abs/db.sqlite)

Generic host/port/user/password/database assumption is NOT used — each
scheme is handled by its own parser. Unsupported scheme raises ValueError.
Query params are preserved per-DB in ConnectionOpts.extra.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from dbbackup.models import ConnectionOpts

SUPPORTED_SCHEMES = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "mongodb": "mongo",
    "mongodb+srv": "mongo",
    "file": "sqlite",
}

# Human-readable list for errors
_SUPPORTED_DISPLAY = "postgresql, postgres, mysql, mongodb, mongodb+srv, file:"


def _parse_query_extra(parsed) -> dict[str, str]:
    """Parse query string into last-value-wins dict with unquoting."""
    if not parsed.query:
        return {}
    qs = parse_qs(parsed.query, keep_blank_values=True)
    extra: dict[str, str] = {}
    for k, vals in qs.items():
        # last value wins
        v = vals[-1] if vals else ""
        extra[k] = unquote(v)
    return extra


def _parse_postgres(parsed) -> ConnectionOpts:
    db = parsed.path.lstrip("/")
    # database may be empty
    extra = _parse_query_extra(parsed)
    return ConnectionOpts(
        db_type="postgres",
        host=unquote(parsed.hostname or ""),
        port=parsed.port or 0,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=unquote(db),
        extra=extra,
    )


def _parse_mysql(parsed) -> ConnectionOpts:
    # Synthetic mysql:// support — not native to mysqldump, but convenient
    db = parsed.path.lstrip("/")
    extra = _parse_query_extra(parsed)
    return ConnectionOpts(
        db_type="mysql",
        host=unquote(parsed.hostname or ""),
        port=parsed.port or 0,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=unquote(db),
        extra=extra,
    )


def _parse_mongo(parsed, scheme: str) -> ConnectionOpts:
    # mongodb:// and mongodb+srv://
    # Host may contain comma-separated list; keep as-is (full netloc host part without userinfo)
    # urlparse handles single host well; for SRV/comma case we extract raw host segment
    extra = _parse_query_extra(parsed)
    # Determine host: for comma case, parsed.hostname only gives first host
    host = parsed.hostname or ""
    # If original url contains comma in host, recover full host list
    # We can get netloc and strip userinfo
    netloc = parsed.netloc
    # netloc is [user:pass@]host[:port]
    host_part = netloc.rsplit("@", 1)[-1] if "@" in netloc else netloc
    # Remove trailing :port if present and not comma case? But host_part may be "host1,host2"
    # Keep entire host_part without port splitting for comma case
    if "," in host_part:
        # strip any / that shouldn't be there (netloc never contains /)
        host = host_part
        # port is not meaningful for srv; set 0
        port = 0
    else:
        host = unquote(host or "")
        port = parsed.port or 0
        if scheme == "mongodb+srv":
            # SRV has no port
            port = 0
    # database path
    db = parsed.path.lstrip("/")
    return ConnectionOpts(
        db_type="mongo",
        host=host,
        port=port,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=unquote(db),
        extra=extra,
    )


def _parse_sqlite_file(url: str, parsed) -> ConnectionOpts:
    # file: URIs — file:./db.sqlite, file:/abs/db.sqlite, file:///abs/db.sqlite, file:./db.sqlite?mode=ro
    # urlparse("file:./db.sqlite") -> path "./db.sqlite", netloc ""
    # urlparse("file:///tmp/db.sqlite") -> path "/tmp/db.sqlite", netloc ""
    # Use parsed.path and also handle opaque part for file:./ case
    extra = _parse_query_extra(parsed)
    # For file: URIs, database is the path component
    # If netloc is present (file://), prepend? Usually file:///tmp/x -> netloc "", path "/tmp/x"
    # file://./rel is unusual; just use path
    path = parsed.path
    # Handle file:./db.sqlite where urlparse may treat "./db.sqlite" as path correctly
    # Also handle Windows file:/C:/path etc — keep as-is
    # Unquote path but preserve leading / or ./
    database = unquote(path)
    if not database or database == "/":  # noqa: SIM102 - keep nested for clarity
        if not database:
            raise ValueError(
                "sqlite file: URI requires a path, e.g. file:./database.db or file:/abs/path.db"
            )
    # Reject :memory: for backup (explicit)
    if database == ":memory:" or database.endswith("/:memory:"):
        raise ValueError("sqlite :memory: database cannot be backed up via file URI")
    # For file URI, host/port/user/password are not used
    return ConnectionOpts(
        db_type="sqlite",
        host="",
        port=0,
        user="",
        password="",
        database=database,
        extra=extra,
    )


def parse_connection_url(url: str) -> ConnectionOpts:
    """Parse a DB connection URL into ConnectionOpts.

    Raises ValueError with actionable message for invalid/unsupported URLs.
    """
    if not url or not url.strip():
        raise ValueError("invalid --url: empty URL")
    url = url.strip()
    # file: URIs have single colon, not ://
    if url.lower().startswith("file:"):
        parsed = urlparse(url)
        # urlparse lowercases scheme
        if parsed.scheme != "file":
            raise ValueError(
                f"unsupported URL scheme '{parsed.scheme}' — supported: {_SUPPORTED_DISPLAY}"
            )
        return _parse_sqlite_file(url, parsed)

    # All other schemes must contain ://
    if "://" not in url:
        raise ValueError(
            f"invalid --url: missing '://' — expected scheme://... (supported: {_SUPPORTED_DISPLAY})"
        )
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        raise ValueError(f"invalid --url: missing scheme (supported: {_SUPPORTED_DISPLAY})")
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"unsupported URL scheme '{scheme}' — supported: {_SUPPORTED_DISPLAY}")

    db_type = SUPPORTED_SCHEMES[scheme]

    if db_type == "postgres":
        return _parse_postgres(parsed)
    if db_type == "mysql":
        return _parse_mysql(parsed)
    if db_type == "mongo":
        return _parse_mongo(parsed, scheme)
    # sqlite via file: already handled; no sqlite:// fake server URL supported
    raise ValueError(f"unsupported URL scheme '{scheme}' — supported: {_SUPPORTED_DISPLAY}")
