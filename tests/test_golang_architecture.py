"""Reject unknown hardware before selecting or caching an executable package."""
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
