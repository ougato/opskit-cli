"""Reject unknown hardware before selecting or caching an executable package."""
import os
import subprocess

import pytest

from software.base import InstallError
from software.recipes.golang import common


@pytest.mark.parametrize("machine,expected", [
    ("x86_64", "amd64"), ("AMD64", "amd64"),
    ("aarch64", "arm64"), ("arm64", "arm64"),
    ("armv6l", "armv6l"), ("armv7l", "armv6l"), ("armv8l", "armv6l"),
    ("i386", "386"), ("i686", "386"), ("x86", "386"),
])
def test_supported_architecture_aliases(monkeypatch, machine, expected):
    monkeypatch.setattr(common.platform, "machine", lambda: machine)
    assert common._go_arch() == expected


@pytest.mark.parametrize("machine", ["", "unknown", "armv99", "riscv64", "s390x"])
def test_unknown_architecture_rejects_before_download(tmp_path, monkeypatch, machine):
    from core import mirror

    monkeypatch.setattr(common.platform, "machine", lambda: machine)
    monkeypatch.setattr(mirror, "get_download_cache_path", lambda *a, **k: pytest.fail("cache touched"))
    monkeypatch.setattr(mirror, "download_file", lambda *a, **k: pytest.fail("download started"))
    with pytest.raises(InstallError, match="Unsupported Go CPU architecture"):
        common.download_golang_tarball("1.26.0", tmp_path / "go.tar.gz")
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX shell")
def test_go_shims_forward_each_command_after_version_switch(tmp_path, monkeypatch):
    from software.recipes.golang import linux

    shims = tmp_path / "shims"
    shims.mkdir()
    monkeypatch.setattr(common, "shim_dir", lambda: shims)
    monkeypatch.setattr(linux.shell_path, "link_into_system_bin", lambda *args, **kwargs: None)
    monkeypatch.setenv("PATH", os.environ["PATH"])

    for version in ("first", "second"):
        bin_dir = tmp_path / f"{version} 'toolchain" / "bin"
        bin_dir.mkdir(parents=True)
        for name in ("go", "gofmt"):
            tool = bin_dir / name
            tool.write_text(f'#!/bin/sh\nprintf "%s\\n" "{version}" "{name}" "$@"\nexit 7\n')
            tool.chmod(0o755)

        linux.LinuxDriver().apply_version_link(str(bin_dir))

        for name in ("go", "gofmt"):
            result = subprocess.run([str(shims / name), "-l", "with spaces"],
                                    capture_output=True, text=True, check=False)
            assert result.returncode == 7
            assert result.stdout.splitlines() == [version, name, "-l", "with spaces"]
