import io

from dbbackup.core.compression import compress_stream, decompress_stream


def test_gzip_roundtrip():
    data = b"hello world " * 1000
    out = io.BytesIO()
    compress_stream(io.BytesIO(data), out, level=6)
    out.seek(0)
    dec = io.BytesIO()
    decompress_stream(out, dec)
    assert dec.getvalue() == data


def test_gzip_levels():
    data = b"abcd" * 500
    for level in (1, 6, 9):
        out = io.BytesIO()
        compress_stream(io.BytesIO(data), out, level=level)
        out.seek(0)
        dec = io.BytesIO()
        decompress_stream(out, dec)
        assert dec.getvalue() == data


def test_gzip_invalid_level_raises():
    import pytest

    with pytest.raises(ValueError):
        compress_stream(io.BytesIO(b"x"), io.BytesIO(), level=0)
    with pytest.raises(ValueError):
        compress_stream(io.BytesIO(b"x"), io.BytesIO(), level=10)


def test_gzip_empty():
    out = io.BytesIO()
    compress_stream(io.BytesIO(b""), out, level=6)
    out.seek(0)
    dec = io.BytesIO()
    decompress_stream(out, dec)
    assert dec.getvalue() == b""
