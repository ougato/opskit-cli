"""Tar prefix removal validates original and transformed members before writes."""
import io
import os
import tarfile

import pytest

from core.archive import extract_tar
from software.base import InstallError


def package(path, names, *, mode=0o755, kind=tarfile.REGTYPE):
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.type = kind
            member.linkname = "../outside"
            member.size = 5 if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(b"inert") if member.size else None)


def extract(source, dest, **kwargs):
    with tarfile.open(source, "r:gz") as archive:
        extract_tar(archive, dest, **kwargs)


def test_default_and_stripped_layout(tmp_path):
    source = tmp_path / "source.tar.gz"
    package(source, ["go/bin/go", "go/test/Þfoo.go"])
    for strip in (0, 1):
        dest = tmp_path / str(strip)
        extract(source, dest, strip_components=strip)
        base = dest if strip else dest / "go"
        assert (base / "bin/go").read_bytes() == b"inert"
        assert (base / "test/Þfoo.go").read_bytes() == b"inert"
        if os.name == "posix":
            assert (base / "bin/go").stat().st_mode & 0o111 == 0o111


@pytest.mark.parametrize("name", ["../outside", "go/../../outside", "go/..\\outside", "C:/outside", "/outside", "other/bin/tool"])
def test_original_paths_and_mixed_roots_rejected(tmp_path, name):
    source, dest = tmp_path / "source.tar.gz", tmp_path / "dest"
    package(source, ["go/bin/go", name])
    with pytest.raises(ValueError):
        extract(source, dest, strip_components=1)
    assert not dest.exists() and not (tmp_path / "outside").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_skipped_root_special_member_still_rejected(tmp_path, kind):
    source, dest = tmp_path / "source.tar.gz", tmp_path / "dest"
    package(source, ["go"], kind=kind)
    with pytest.raises(ValueError):
        extract(source, dest, strip_components=1)
    assert not dest.exists()


@pytest.mark.parametrize("strip", [-1, True, "1", 1.5])
def test_invalid_strip_rejected(tmp_path, strip):
    source = tmp_path / "source.tar.gz"
    package(source, ["go/bin/go"])
    with pytest.raises(ValueError):
        extract(source, tmp_path / "dest", strip_components=strip)


def test_quota_before_writes(tmp_path, monkeypatch):
    from core import archive
    source, dest = tmp_path / "source.tar.gz", tmp_path / "dest"
    package(source, ["go/bin/go"])
    monkeypatch.setattr(archive, "MAX_EXTRACT_BYTES", 1)
    with pytest.raises(ValueError):
        extract(source, dest, strip_components=1)
    assert not dest.exists()


@pytest.mark.skipif(os.name != "posix", reason="UTF-8 byte paths need actual POSIX filesystem")
def test_utf8_byte_paths(tmp_path):
    source, dest = tmp_path / "source.tar.gz", tmp_path / "目录"
    package(source, ["go/test/Þfoo.go"])
    extract(source, dest, strip_components=1, utf8_paths=True)
    assert (dest / "test/Þfoo.go").read_bytes() == b"inert"


@pytest.mark.parametrize("extra", [None, "go/../../outside", "other/bin/go"])
def test_actual_go_driver_uses_safe_prefix(tmp_path, monkeypatch, extra):
    from software.recipes.golang import common
    from software.recipes.golang.linux import LinuxDriver
    dest, source = tmp_path / "dest", tmp_path / "source.tar.gz"
    monkeypatch.setattr(common, "go_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "go_bin_dir", lambda version: dest / "bin")
    package(source, ["go/bin/go", "go/test/Þfoo.go"] + ([extra] if extra else []))
    if extra:
        with pytest.raises(InstallError):
            LinuxDriver().install_tarball("1", source)
        assert not dest.exists()
    else:
        assert LinuxDriver().install_tarball("1", source) == str(dest / "bin")
        assert (dest / "test/Þfoo.go").read_bytes() == b"inert"


def test_go_missing_binary_not_published(tmp_path, monkeypatch):
    from software.recipes.golang import common
    from software.recipes.golang.linux import LinuxDriver
    source, dest = tmp_path / "source.tar.gz", tmp_path / "dest"
    monkeypatch.setattr(common, "go_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "go_bin_dir", lambda version: dest / "bin")
    package(source, ["go/README"])
    with pytest.raises(InstallError):
        LinuxDriver().install_tarball("1", source)
    assert not dest.exists() and not list(tmp_path.glob(".opskit-go-*"))
