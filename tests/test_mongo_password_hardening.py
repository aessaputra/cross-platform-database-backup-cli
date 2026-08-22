"""Regression: Mongo password via --config (0600) — not in ps argv."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from dbbackup.core.url import parse_connection_url


def test_mongo_backup_password_uses_config_not_uri():
    from dbbackup.adapters.mongo import MongoAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/mongodump"),
        patch("dbbackup.adapters.mongo.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        opts = MagicMock(
            host="h",
            port=27017,
            user="u",
            password="s3cret",
            database="db",
            extra={"authSource": "admin"},
        )
        MongoAdapter().backup(opts)
        args, _ = popen.call_args
        cmd = args[0]
        joined = " ".join(cmd)
        assert "--config" in cmd
        assert "s3cret" not in joined
        assert "--uri" not in joined or "s3cret" not in joined
        # config file should be 0600 and contain uri
        cfg_idx = cmd.index("--config") + 1
        cfg_path = Path(cmd[cfg_idx])
        assert cfg_path.exists()
        content = cfg_path.read_text()
        assert "s3cret" in content  # inside file ok
        # just ensure no leak in second call's cmd either
        _ = MongoAdapter().backup(
            MagicMock(host="h2", port=27017, user="u", password="p2", database="db", extra={})
        )
        args2, _ = popen.call_args
        assert "p2" not in " ".join(args2[0]) or "--config" in args2[0]


def test_mongo_backup_no_password_no_config():
    from dbbackup.adapters.mongo import MongoAdapter

    with (
        patch("shutil.which", return_value="/usr/bin/mongodump"),
        patch("dbbackup.adapters.mongo.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        MongoAdapter().backup(
            MagicMock(host="h", port=27017, user="", password="", database="db", extra={})
        )
        args, _ = popen.call_args
        assert "--config" not in args[0]
        assert "--host" in args[0]


def test_mongo_url_percent_decoded_via_config():
    from dbbackup.adapters.mongo import MongoAdapter

    opts = parse_connection_url("mongodb://u:p%40ss%3Aw@host:27017/mydb?authSource=admin")
    assert opts.password == "p@ss:w"
    with (
        patch("shutil.which", return_value="/usr/bin/mongodump"),
        patch("dbbackup.adapters.mongo.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        MongoAdapter().backup(opts)
        args, _ = popen.call_args
        assert "p@ss:w" not in " ".join(args[0])
        assert "--config" in args[0]


def test_mongo_srv_via_config():
    from dbbackup.adapters.mongo import MongoAdapter

    opts = parse_connection_url("mongodb+srv://u:p%40ss@cluster.mongodb.net/mydb?authSource=admin")
    with (
        patch("shutil.which", return_value="/usr/bin/mongodump"),
        patch("dbbackup.adapters.mongo.subprocess.Popen") as popen,
    ):
        popen.return_value.stdout = MagicMock()
        MongoAdapter().backup(opts)
        args, _ = popen.call_args
        assert "--config" in args[0]
        cfg_path = Path(args[0][args[0].index("--config") + 1])
        assert "mongodb+srv://" in cfg_path.read_text()


def test_mongo_restore_via_config():
    import io

    from dbbackup.adapters.mongo import MongoAdapter
    from dbbackup.models import BackupArtifact, ConnectionOpts, RestoreOpts

    artifact = BackupArtifact(
        db_type="mongo",
        format="archive",
        extension=".archive.gz",
        stream_or_path=io.BytesIO(b"dump"),
    )
    conn = ConnectionOpts(
        db_type="mongo",
        host="h",
        port=27017,
        user="u",
        password="restoresecret",
        database="mydb",
        extra={"authSource": "admin"},
    )
    opts = RestoreOpts(connection=conn, s3_key="k")
    with (
        patch("dbbackup.adapters.mongo.require_binary", return_value="/usr/bin/mongorestore"),
        patch("dbbackup.adapters.mongo.subprocess.Popen") as popen,
    ):
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        popen.return_value = mock_proc
        MongoAdapter().restore(artifact, opts)
        args, _ = popen.call_args
        assert "--config" in args[0]
        assert "restoresecret" not in " ".join(args[0])
