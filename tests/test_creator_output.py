"""A successful exit cannot substitute for a fresh, bounded build output."""
import os
from pathlib import Path
import time

import pytest

from core.plugin_host_tools import _inspect_output, creator_build


def test_output_counts_nested_files_and_requires_web_entry(tmp_path):
    (tmp_path / "index.html").write_bytes(b"page")
    nested = tmp_path / "assets"
    nested.mkdir()
    (nested / "game.js").write_bytes(b"script")
    assert _inspect_output(tmp_path, "web-mobile", lambda: None) == {
        "directory": str(tmp_path), "files": 2, "bytes": 10,
    }
    (tmp_path / "index.html").unlink()
    with pytest.raises(FileNotFoundError):
        _inspect_output(tmp_path, "web-mobile", lambda: None)


def test_output_refuses_hardlinks_without_removing_any_files(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"keep")
    os.link(original, tmp_path / "linked")
    with pytest.raises(ValueError):
        _inspect_output(tmp_path, "windows", lambda: None)
    assert original.read_bytes() == b"keep"


def test_output_refuses_oversized_logical_file(tmp_path, monkeypatch):
    monkeypatch.setattr("core.plugin_host_tools.MAX_OUTPUT_BYTES", 8)
    (tmp_path / "huge").write_bytes(b"123456789")
    with pytest.raises(ValueError):
        _inspect_output(tmp_path, "android", lambda: None)


def test_output_refuses_excess_entries(tmp_path, monkeypatch):
    monkeypatch.setattr("core.plugin_host_tools.MAX_OUTPUT_ENTRIES", 2)
    for name in ("a", "b", "c"):
        (tmp_path / name).write_bytes(b"x")
    with pytest.raises(ValueError):
        _inspect_output(tmp_path, "windows", lambda: None)


def test_output_refuses_deep_tree(tmp_path):
    current = tmp_path
    for _ in range(65):
        current = current / "d"
        current.mkdir()
    with pytest.raises(ValueError):
        _inspect_output(tmp_path, "windows", lambda: None)


def test_output_inspection_checks_cancellation(tmp_path):
    (tmp_path / "index.html").write_bytes(b"page")
    def cancelled():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _inspect_output(tmp_path, "web-mobile", cancelled)


def test_output_inspection_refuses_replaced_root(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "index.html").write_bytes(b"original")
    replaced = False
    def replace_once():
        nonlocal replaced
        if not replaced:
            replaced = True
            output.rename(tmp_path / "preserved")
            output.mkdir()
            (output / "index.html").write_bytes(b"replacement")
    with pytest.raises(ValueError):
        _inspect_output(output, "web-mobile", replace_once)
    assert (tmp_path / "preserved/index.html").read_bytes() == b"original"


@pytest.mark.skipif(os.name != "nt", reason="Windows Creator host tool")
@pytest.mark.parametrize("language", ["zh", "en"])
def test_success_exit_without_new_output_never_uses_previous_build(tmp_path, monkeypatch, language):
    from core import i18n, process
    from core.plugin_capabilities import CapabilityAuthority
    from core.native_sandbox import GrantChoice
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    tool = tmp_path / "Creator.exe"
    tool.write_bytes(b"not executed")
    old = tmp_path / "build/web-mobile"
    old.mkdir(parents=True)
    (old / "index.html").write_bytes(b"old successful output")
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    require = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kw:
                        require(scope, prompt=lambda _: GrantChoice.ONCE, **kw))
    def fake_success(*args, **kwargs):
        yield "no new files"
        return 36
    monkeypatch.setattr(process, "stream", fake_success)
    logs = []
    with pytest.raises(FileNotFoundError):
        creator_build("demo", "a" * 64, str(tool), str(tmp_path), {"platform": "web-mobile"},
                      authority=authority, check_identity=lambda *args: True,
                      deadline=time.monotonic() + 10, checkpoint=lambda: None, output=logs.append)
    assert logs[-1] == i18n.t("plugin_native.creator.artifact_failed")
    assert i18n.t("plugin_native.creator.completed", platform="web-mobile") not in logs
    assert (old / "index.html").read_bytes() == b"old successful output"
