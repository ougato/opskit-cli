"""Prefix removal cannot bypass original archive safety checks."""
import stat
import zipfile

import pytest

from core import archive as extraction


@pytest.mark.parametrize("strip", [0, 1])
def test_default_and_stripped_layout_are_preserved(tmp_path, strip):
    package = tmp_path / "archive.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("root/bin/tool", b"inert fixture")
    dest = tmp_path / "dest"
    with zipfile.ZipFile(package) as archive:
        extraction.extract_zip(archive, dest, strip_components=strip)
    expected = dest / ("root/bin/tool" if strip == 0 else "bin/tool")
    assert expected.read_bytes() == b"inert fixture"


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO])
def test_skipped_top_level_member_still_rejects_links_and_special_files(tmp_path, mode):
    package = tmp_path / "archive.zip"
    with zipfile.ZipFile(package, "w") as archive:
        member = zipfile.ZipInfo("root")
        member.external_attr = (mode | 0o777) << 16
        archive.writestr(member, b"../outside")
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="special files"):
        extraction.extract_zip(archive, tmp_path / "dest", strip_components=1)
    assert not (tmp_path / "dest").exists()


def test_mixed_prefixes_fail_before_any_payload_is_written(tmp_path):
    package = tmp_path / "archive.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("first/tool", b"first")
        archive.writestr("second/tool", b"second")
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="inconsistent root"):
        extraction.extract_zip(archive, tmp_path / "dest", strip_components=1)
    assert not (tmp_path / "dest").exists()


def test_stripped_zip_still_enforces_total_size_limit(tmp_path, monkeypatch):
    package = tmp_path / "archive.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("root/tool", b"too large")
    monkeypatch.setattr(extraction, "MAX_EXTRACT_BYTES", 1)
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="limits"):
        extraction.extract_zip(archive, tmp_path / "dest", strip_components=1)
    assert not (tmp_path / "dest").exists()


@pytest.mark.parametrize("strip", [True, -1, 1.0, None])
def test_invalid_strip_count_rejected_before_reading_archive(tmp_path, strip):
    with pytest.raises(ValueError, match="prefix count"):
        extraction.extract_zip(None, tmp_path / "dest", strip_components=strip)
