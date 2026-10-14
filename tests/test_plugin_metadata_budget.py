"""Untrusted manifests fail before YAML object construction or plugin import."""
import os

import pytest

from core import plugin, bounded_yaml as metadata

VALID = b"name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo_pkg\n"


@pytest.mark.parametrize("content", [b"x" * (1024 * 1024 + 1), b"[" * 65 + b"0" + b"]" * 65,
                                    b"a: &a [0]\nb: *a\n", b"[" + b"0," * 20000 + b"0]"],
                         ids=["bytes", "depth", "aliases", "events"])
def test_manifest_rejected_before_construction(tmp_path, monkeypatch, content):
    (tmp_path / "plugin.yaml").write_bytes(content)
    monkeypatch.setattr(metadata.yaml, "safe_load", lambda *args: pytest.fail("overbudget construction"))
    assert plugin.load_manifest(tmp_path) is None


def test_normal_manifest_remains_valid(tmp_path):
    (tmp_path / "plugin.yaml").write_bytes(VALID)
    manifest = plugin.load_manifest(tmp_path)
    assert manifest.name == "demo"
    assert manifest.entry == "demo_pkg"


def test_manifest_hardlink_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(VALID)
    (tmp_path / "plugin.yaml").hardlink_to(source)
    assert plugin.load_manifest(tmp_path) is None


def test_file_replaced_before_open_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "plugin.yaml"
    path.write_bytes(VALID)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(VALID.replace(b"demo", b"evil"))
    original = metadata.os.open
    def swap(source, flags):
        replacement.replace(path)
        return original(source, flags)
    monkeypatch.setattr(metadata.os, "open", swap)
    assert plugin.load_manifest(tmp_path) is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX nonblocking special-file rejection")
def test_fifo_manifest_rejected_without_open(tmp_path, monkeypatch):
    os.mkfifo(tmp_path / "plugin.yaml")
    monkeypatch.setattr(metadata.os, "open", lambda *args: pytest.fail("opened FIFO"))
    assert plugin.load_manifest(tmp_path) is None


def test_invalid_yaml_content_is_not_logged(tmp_path, monkeypatch):
    from core import plugin_integrity as integrity
    secret = "synthetic-sensitive-marker"
    warnings = []
    monkeypatch.setattr(plugin._log, "warning", lambda message, *args: warnings.append(message % args))
    monkeypatch.setattr(integrity._log, "warning", lambda message, *args: warnings.append(message % args))
    for name in ("plugin.yaml", "CHECKSUMS.yaml"):
        (tmp_path / name).write_text("key: [" + secret, encoding="utf-8")
    assert plugin.load_manifest(tmp_path) is None
    assert integrity.verify_checksums(tmp_path) == integrity.CHECK_MISMATCH
    assert len(warnings) == 2
    assert all(secret not in warning for warning in warnings)


def test_metadata_read_detects_actual_growth_after_fstat(tmp_path, monkeypatch):
    path = tmp_path / "plugin.yaml"
    path.write_bytes(VALID)
    original = metadata.os.fstat
    calls = []
    def grow(descriptor):
        result = original(descriptor)
        if not calls:
            with path.open("ab") as stream:
                stream.write(b"x" * metadata.MAX_BYTES)
        calls.append(True)
        return result
    monkeypatch.setattr(metadata.os, "fstat", grow)
    with pytest.raises(ValueError, match="byte limit"):
        metadata.load_bounded_yaml(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate short path spelling")
def test_windows_short_path_alias_is_not_a_link(tmp_path):
    import ctypes
    from ctypes import wintypes
    from pathlib import Path
    path = tmp_path / "plugin.yaml"
    path.write_bytes(VALID)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_short = kernel.GetShortPathNameW
    get_short.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    get_short.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_short(str(path), buffer, len(buffer))
    assert 0 < length < len(buffer)
    alias = Path(buffer.value)
    assert metadata.load_bounded_yaml(alias)["name"] == "demo"
