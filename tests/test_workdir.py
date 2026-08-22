import os
import stat
from pathlib import Path

from dbbackup.core.workdir import TempWorkdir


def test_workdir_creates_and_cleans():
    with TempWorkdir() as wd:
        assert wd.path.exists()
        assert wd.path.is_dir()
        (wd.path / "hello.txt").write_text("hi")
        assert (wd.path / "hello.txt").exists()
    assert not wd.path.exists()


def test_workdir_owner_restricted_posix(tmp_path=None):
    import sys

    with TempWorkdir() as wd:
        p = wd.path
        assert p.exists()
        if sys.platform != "win32":
            mode = stat.S_IMODE(p.stat().st_mode)
            assert mode == 0o700, f"expected 0700 got {oct(mode)}"
            # file inside should be 0600
            f = p / "secret.bin"
            f.write_bytes(b"secret")
            os.chmod(f, 0o600)
            assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_workdir_tempfile_helper():
    with TempWorkdir() as wd:
        tmp = wd.temp_file(suffix=".dump")
        assert tmp.exists()
        tmp.write_text("data")
        assert tmp.read_text() == "data"
    assert not wd.path.exists()


def test_workdir_cleanup_on_exception():
    wd_ref = None
    try:
        with TempWorkdir() as wd:
            wd_ref = wd.path
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert wd_ref is not None
    assert not wd_ref.exists()


def test_workdir_path_is_path():
    with TempWorkdir() as wd:
        assert isinstance(wd.path, Path)
