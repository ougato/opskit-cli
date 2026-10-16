"""Binary package selectors must not disguise unsupported hardware as x86_64."""
import importlib

import pytest

from software.base import InstallError


SELECTORS = [
    ("java", "_java_arch", "x64", "aarch64"),
    ("nodejs", "_node_arch", "x64", "arm64"),
    ("clickhouse", "clickhouse_arch", "amd64", "arm64"),
    ("mysql", "_mysql_arch", "x86_64", "aarch64"),
    ("mysql", "_mysql_arch_darwin", "x86_64", "arm64"),
    ("mongodb", "_mongo_arch", "x86_64", "aarch64"),
    ("mongodb", "_mongo_arch_darwin", "x86_64", "arm64"),
    ("postgresql", "_pgsql_arch", "x86_64", "aarch64"),
    ("postgresql", "_pgsql_arch_darwin", "x86_64", "aarch64"),
    ("redis", "_redis_arch", "x86_64", "aarch64"),
    ("redis", "_deb_arch", "amd64", "arm64"),
]


@pytest.mark.parametrize("recipe,selector,x64,arm64", SELECTORS)
@pytest.mark.parametrize("machine", ["x86_64", "AMD64", "aarch64", "ARM64"])
def test_known_binary_architecture_aliases(monkeypatch, recipe, selector, x64, arm64, machine):
    module = importlib.import_module(f"software.recipes.{recipe}.common")
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    assert getattr(module, selector)() == (x64 if machine.lower() in ("x86_64", "amd64") else arm64)


@pytest.mark.parametrize("recipe,selector,x64,arm64", SELECTORS)
@pytest.mark.parametrize("machine", ["", "unknown", "armv99", "riscv64", "s390x"])
def test_unmapped_binary_architecture_rejected(monkeypatch, recipe, selector, x64, arm64, machine):
    module = importlib.import_module(f"software.recipes.{recipe}.common")
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    with pytest.raises(InstallError, match="Unsupported .* CPU architecture"):
        getattr(module, selector)()


@pytest.mark.parametrize("machine", ["armv6l", "armv7l", "armv8l", "i386", "i686", "x86"])
def test_java_explicit_32bit_aliases(monkeypatch, machine):
    from software.recipes.java import common

    monkeypatch.setattr(common.platform, "machine", lambda: machine)
    assert common._java_arch() == ("arm" if machine.startswith("armv") else "x86")


@pytest.mark.parametrize("machine", ["armv7l", "armv8l", "i386", "i686", "x86"])
def test_node_explicit_32bit_aliases(monkeypatch, machine):
    from software.recipes.nodejs import common

    monkeypatch.setattr(common.platform, "machine", lambda: machine)
    assert common._node_arch() == ("armv7l" if machine.startswith("armv") else "x86")


def test_node_armv6_cannot_run_armv7_package(monkeypatch):
    from software.recipes.nodejs import common

    monkeypatch.setattr(common.platform, "machine", lambda: "armv6l")
    with pytest.raises(InstallError, match="Unsupported nodejs CPU architecture"):
        common._node_arch()


@pytest.mark.parametrize("machine", ["x86_64", "AMD64"])
def test_windows_fixed_x64_accepts_known_native_aliases(monkeypatch, machine):
    from software._shared import architecture

    monkeypatch.setattr(architecture.platform, "machine", lambda: machine)
    architecture.require_windows_x64("fixture")


@pytest.mark.parametrize("recipe,download,version", [
    ("mysql", "download_mysql_tarball", "8.4.0"),
    ("mongodb", "download_mongodb_tarball", "8.0.0"),
    ("postgresql", "download_pgsql_tarball", "17.0"),
    ("redis", "download_redis_windows", "7.4.0"),
])
@pytest.mark.parametrize("machine", ["", "unknown", "arm64", "aarch64", "i386"])
def test_windows_fixed_x64_rejects_before_network_or_directory_creation(
    tmp_path, monkeypatch, recipe, download, version, machine,
):
    import sys
    import httpx
    from core import mirror

    module = importlib.import_module(f"software.recipes.{recipe}.common")
    # Restore the platform before fixtures/pytest can perform native path probes.
    with monkeypatch.context() as patcher:
        patcher.setattr(sys, "platform", "win32")
        patcher.setattr(module.platform, "machine", lambda: machine)
        patcher.setattr(mirror, "download_file", lambda *a, **k: pytest.fail("download started"))
        patcher.setattr(mirror, "get_download_cache_path", lambda *a, **k: pytest.fail("cache touched"))
        patcher.setattr(httpx, "stream", lambda *a, **k: pytest.fail("HTTP stream opened"))
        if recipe == "redis":
            patcher.setattr(module, "_pick_fastest_url", lambda *a: pytest.fail("network probe started"))
        with pytest.raises(InstallError, match="Windows package requires x64"):
            getattr(module, download)(version, tmp_path / "new" / "package")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("machine,expected_count", [("arm64", 1), ("x86_64", 2)])
def test_postgresql_macos_download_uses_native_packages(tmp_path, monkeypatch, machine, expected_count):
    import sys
    from core import mirror
    from software.recipes.postgresql import common

    captured = []

    def capture_download(**kwargs):
        captured.extend(kwargs["urls"])
        return kwargs["dest"]

    with monkeypatch.context() as patcher:
        patcher.setattr(sys, "platform", "darwin")
        patcher.setattr(common.platform, "machine", lambda: machine)
        patcher.setattr(mirror, "download_file", capture_download)
        patcher.setattr(common, "pgsql_download_cache_path", lambda *a: tmp_path / "cache")
        assert common.download_pgsql_tarball("17.0", tmp_path / "download.tar.gz") == tmp_path / "download.tar.gz"
    assert len(captured) == expected_count
    if machine == "arm64":
        assert all("aarch64-apple-darwin" in url for url in captured)
    else:
        assert any("get.enterprisedb.com" in url for url in captured)
        assert any("x86_64-apple-darwin" in url for url in captured)
