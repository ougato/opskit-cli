"""Actual filesystem cases for descriptor-bound CLI secret input."""
import io
import os
from pathlib import Path

import pytest

from core import cli_secrets
from core.secrets import restrict_permissions


def _write_private(path, raw):
    path.write_bytes(raw)
    restrict_permissions(path)


@pytest.mark.parametrize("text", ["  synthetic-token\n", "中文密码\n", "x" * 65536], ids=["ascii", "unicode", "limit"])
def test_regular_input_is_bounded_text(tmp_path, text):
    source = tmp_path / "input"
    _write_private(source, text.encode("utf-8"))
    assert cli_secrets.read_secret_file(str(source)) == text.strip()


@pytest.mark.parametrize("raw,message", [(b" \n", "empty"), (b"x" * 65537, "too large"),
                                        (b"synthetic-private-value\xff", "UTF-8 text")], ids=["empty", "oversize", "encoding"])
def test_invalid_input_has_no_content_in_error(tmp_path, raw, message):
    source = tmp_path / "input"
    _write_private(source, raw)
    with pytest.raises(ValueError, match=message) as caught:
        cli_secrets.read_secret_file(str(source))
    assert "synthetic-private-value" not in str(caught.value)


def test_hardlinked_input_is_rejected_without_modification(tmp_path):
    source = tmp_path / "input"
    _write_private(source, b"synthetic-token")
    alias = tmp_path / "alias"
    os.link(source, alias)
    with pytest.raises(ValueError, match="single-link"):
        cli_secrets.read_secret_file(str(source))
    assert source.read_bytes() == alias.read_bytes() == b"synthetic-token"


def test_directory_is_rejected_before_open(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("nonregular input reached os.open")
    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(ValueError, match="regular file"):
        cli_secrets.read_secret_file(str(tmp_path))


def test_link_input_is_rejected(tmp_path):
    source = tmp_path / "input"
    _write_private(source, b"synthetic-token")
    link = tmp_path / "link"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("filesystem does not permit symlink fixture")
    with pytest.raises(ValueError, match="regular file"):
        cli_secrets.read_secret_file(str(link))


def test_replacement_at_open_is_rejected_and_descriptor_closed(tmp_path, monkeypatch):
    source = tmp_path / "input"
    _write_private(source, b"original")
    replacement = tmp_path / "replacement"
    _write_private(replacement, b"different")
    opened = []
    real_open = os.open
    def replace_then_open(path, flags, *args, **kwargs):
        if Path(path) == source:
            source.rename(tmp_path / "retained")
            replacement.rename(source)
        fd = real_open(path, flags, *args, **kwargs)
        opened.append(fd)
        return fd
    with monkeypatch.context() as context:
        context.setattr(os, "open", replace_then_open)
        with pytest.raises(ValueError, match="changed while opening"):
            cli_secrets.read_secret_file(str(source))
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])
    assert (tmp_path / "retained").read_bytes() == b"original"
    assert source.read_bytes() == b"different"


def test_same_file_write_during_read_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "input"
    _write_private(source, b"synthetic-token")
    real_fdopen = os.fdopen
    class ChangedRead:
        def __init__(self, *args, **kwargs):
            self.stream = real_fdopen(*args, **kwargs)
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def read(self, count):
            raw = self.stream.read(count)
            # Keep the inode, but change content and length through another fd.
            with source.open("ab") as writer:
                writer.write(b"changed")
            return raw
    monkeypatch.setattr(os, "fdopen", ChangedRead)
    with pytest.raises(ValueError, match="changed while reading"):
        cli_secrets.read_secret_file(str(source))


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO fixture")
def test_fifo_is_rejected_without_waiting_for_writer(tmp_path):
    source = tmp_path / "fifo"
    os.mkfifo(source)
    with pytest.raises(ValueError, match="regular file"):
        cli_secrets.read_secret_file(str(source))


@pytest.mark.parametrize("text,accepted", [("中文\n", True), ("汉" * 22000, False)], ids=["unicode", "byte-limit"])
def test_stdin_byte_budget_is_preserved(monkeypatch, text, accepted):
    monkeypatch.setattr(cli_secrets.sys, "stdin", io.StringIO(text))
    if accepted:
        assert cli_secrets.read_secret_file("-") == text.strip()
    else:
        with pytest.raises(ValueError, match="too large"):
            cli_secrets.read_secret_file("-")
