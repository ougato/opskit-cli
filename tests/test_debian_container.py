"""Debian ar parsing validates every header before creating a payload file."""
import pytest

from core.debian import extract_data_tar


def member(name, data, size=None, trailer=b"`\n", padding=b"\n"):
    length = len(data) if size is None else size
    header = (name.encode().ljust(16) + b"0".ljust(12) + b"0".ljust(6) + b"0".ljust(6)
              + b"100644".ljust(8) + str(length).encode().ljust(10) + trailer)
    return header + data + (padding if length % 2 else b"")


def fixture(tmp_path, data):
    source = tmp_path / "package.deb"
    source.write_bytes(data)
    work = tmp_path / "work"
    work.mkdir()
    return source, work


@pytest.mark.parametrize("name", ["data.tar", "data.tar.gz", "data.tar.xz", "data.tar.bz2", "data.tar.zst"])
def test_exact_data_member_streamed(tmp_path, name):
    source, work = fixture(tmp_path, b"!<arch>\n" + member("debian-binary/", b"2.0\n")
                           + member("control.tar.gz/", b"inert control") + member(name + "/", b"inert payload"))
    result = extract_data_tar(source, work)
    assert result == work / name and result.read_bytes() == b"inert payload"
    assert list(work.iterdir()) == [result]


@pytest.mark.parametrize("suffix", [
    member("../escape/", b"bad"),
    member("data.tar.evil/", b"bad"),
    member("data.tar.gz/", b"duplicate"),
    member("data.tar.xz/", b"second payload"),
    member("control.tar.gz/", b"truncated", size=500),
    member("control.tar.gz/", b"x", trailer=b"!!"),
    member("control.tar.gz/", b"x", padding=b"x"),
    member("control.tar.gz/", b"", size=-1),
    b"trailing fragment",
])
def test_invalid_late_header_rejected_before_write(tmp_path, suffix):
    source, work = fixture(tmp_path, b"!<arch>\n" + member("debian-binary/", b"2.0\n")
                           + member("data.tar.gz/", b"inert payload") + suffix)
    with pytest.raises(ValueError):
        extract_data_tar(source, work)
    assert not list(work.iterdir()) and not (tmp_path / "escape").exists()


@pytest.mark.parametrize("content", [b"not ar", b"!<arch>\n", b"!<arch>\n" + member("data.tar.gz/", b"data"),
                                     b"!<arch>\n" + member("debian-binary/", b"3.0\n") + member("data.tar.gz/", b"data")])
def test_missing_or_invalid_version(tmp_path, content):
    source, work = fixture(tmp_path, content)
    with pytest.raises(ValueError):
        extract_data_tar(source, work)
    assert not list(work.iterdir())


def test_limits_and_existing_output_preserved(tmp_path, monkeypatch):
    from core import debian
    source, work = fixture(tmp_path, b"!<arch>\n" + member("debian-binary/", b"2.0\n") + member("data.tar.gz/", b"inert payload"))
    monkeypatch.setattr(debian, "MAX_DATA_BYTES", 1)
    with pytest.raises(ValueError):
        extract_data_tar(source, work)
    assert not list(work.iterdir())
    monkeypatch.setattr(debian, "MAX_DATA_BYTES", 100)
    existing = work / "data.tar.gz"
    existing.write_bytes(b"keep")
    with pytest.raises(ValueError, match="already exists"):
        extract_data_tar(source, work)
    assert existing.read_bytes() == b"keep"

