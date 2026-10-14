"""Ambiguous ZIP layouts must fail before writing even the first safe member."""
import os
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

from core import archive as extraction


def package_at(tmp_path, names):
    package = tmp_path / "fixture.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("root/safe", b"safe")
            for name in names:
                archive.writestr(name, b"" if name.endswith("/") else b"inert")
    return package


@pytest.mark.parametrize("strip", [0, 1])
@pytest.mark.parametrize("names", [
    ["root/bin/tool", "root/bin/tool"],
    ["root/bin/tool", "root/bin/./tool"],
    ["root/bin/tool", "root\\bin\\tool"],
    ["root/bin", "root/bin/tool"],
    ["root/bin/tool", "root/bin"],
    ["root/bin", "root/bin/"],
    ["root/", "root/"],
])
def test_invalid_layout_is_rejected_before_writing(tmp_path, strip, names):
    package = package_at(tmp_path, names)
    dest = tmp_path / "dest"
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError):
        extraction.extract_zip(archive, dest, strip_components=strip)
    assert not dest.exists()


@pytest.mark.parametrize("mode,name", [
    (stat.S_IFDIR, "root/not-a-directory"),
    (stat.S_IFREG, "root/not-a-file/"),
])
def test_type_metadata_cannot_disagree_with_directory_marker(tmp_path, mode, name):
    package = package_at(tmp_path, [])
    with zipfile.ZipFile(package, "a") as archive:
        member = zipfile.ZipInfo(name)
        member.external_attr = (mode | 0o755) << 16
        archive.writestr(member, b"")
    dest = tmp_path / "dest"
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="type conflicts"):
        extraction.extract_zip(archive, dest)
    assert not dest.exists()


def test_existing_file_is_not_overwritten_and_no_earlier_member_is_written(tmp_path):
    package = package_at(tmp_path, ["root/bin/tool"])
    dest = tmp_path / "dest"
    old = dest / "root/bin/tool"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"owned by previous installation")
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="already exists"):
        extraction.extract_zip(archive, dest)
    assert old.read_bytes() == b"owned by previous installation"
    assert not (dest / "root/safe").exists()


def test_link_to_another_directory_inside_destination_is_not_followed(tmp_path):
    package = package_at(tmp_path, ["root/bin/tool"])
    dest = tmp_path / "dest"
    real = dest / "real"
    real.mkdir(parents=True)
    (dest / "root").mkdir()
    try:
        (dest / "root/bin").symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"native symlink unavailable: {exc}")
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="traverses a link"):
        extraction.extract_zip(archive, dest)
    assert not (real / "tool").exists()
    assert not (dest / "root/safe").exists()


@pytest.mark.skipif(os.name != "nt", reason="case aliases are filesystem-dependent")
@pytest.mark.parametrize("names", [
    ["root/Bin/tool", "root/bin/other"],
    ["root/tool", "root/TOOL"],
    ["Root/bin/tool"],
])
def test_windows_case_aliases_are_rejected_before_writing(tmp_path, names):
    package = package_at(tmp_path, names)
    dest = tmp_path / "dest"
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="aliases"):
        extraction.extract_zip(archive, dest)
    assert not dest.exists()


@pytest.mark.parametrize("part", ["NUL", "con.txt", "COM1", "LPT³.log", "CONIN$", "tool.", "tool ", "a?b", "a\x01b"])
def test_windows_alias_policy_rejects_device_names_and_unsupported_components(part):
    assert extraction._unsafe_windows_component(part)


@pytest.mark.skipif(os.name != "nt", reason="Windows path policy")
@pytest.mark.parametrize("name", ["root/NUL", "root/tool.", "root/tool ", "root/COM1.txt"])
def test_windows_invalid_paths_are_rejected_in_actual_extraction(tmp_path, name):
    package = package_at(tmp_path, [name])
    dest = tmp_path / "dest"
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="unsafe archive path"):
        extraction.extract_zip(archive, dest)
    assert not dest.exists()


def test_normal_explicit_and_implicit_directories_extract(tmp_path):
    package = package_at(tmp_path, ["root/bin/", "root/bin/tool", "root/lib/data"])
    dest = tmp_path / "dest"
    with zipfile.ZipFile(package) as archive:
        extraction.extract_zip(archive, dest, strip_components=1)
    assert (dest / "bin/tool").read_bytes() == b"inert"
    assert (dest / "lib/data").read_bytes() == b"inert"


@pytest.mark.parametrize("name", ["root/" + "a" * 4096, "root/" + "d/" * 128 + "tool"])
def test_path_quota_rejects_before_layout_construction(tmp_path, name):
    package = package_at(tmp_path, [name])
    dest = tmp_path / "dest"
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="path exceeds limits"):
        extraction.extract_zip(archive, dest)
    assert not dest.exists()


def test_cancellation_removes_only_the_file_created_by_this_extraction(tmp_path, monkeypatch):
    package = package_at(tmp_path, [])
    dest = tmp_path / "dest"
    dest.mkdir()
    old = dest / "keep"
    old.write_bytes(b"old data")
    def cancelled(source, output, **kwargs):
        output.write(b"partial")
        raise KeyboardInterrupt
    monkeypatch.setattr(extraction.shutil, "copyfileobj", cancelled)
    with zipfile.ZipFile(package) as archive, pytest.raises(KeyboardInterrupt):
        extraction.extract_zip(archive, dest)
    assert not (dest / "root/safe").exists()
    assert old.read_bytes() == b"old data"


def test_competing_file_created_after_validation_is_not_replaced(tmp_path, monkeypatch):
    package = package_at(tmp_path, [])
    dest = tmp_path / "dest"
    competitor = dest / "root/safe"
    original_open = Path.open
    def competing_open(path, mode="r", *args, **kwargs):
        if path == competitor and mode == "xb":
            with original_open(path, "wb") as output:
                output.write(b"competing owner")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", competing_open)
    with zipfile.ZipFile(package) as archive, pytest.raises(FileExistsError):
        extraction.extract_zip(archive, dest)
    assert competitor.read_bytes() == b"competing owner"
