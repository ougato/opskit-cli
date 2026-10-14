"""Source build drivers copy actual build outputs before temporary sources disappear."""
import importlib
import io
import subprocess
import tarfile

import pytest

from software.base import InstallError


@pytest.fixture(params=["linux", "darwin"])
def install(request, tmp_path, monkeypatch):
    from software.recipes.redis import common
    module = importlib.import_module("software.recipes.redis." + request.param)
    driver = module.LinuxDriver() if request.param == "linux" else module.DarwinDriver()
    dest = tmp_path / "redis1"
    monkeypatch.setattr(common, "redis_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "redis_bin_dir", lambda version: dest / "bin")
    monkeypatch.setattr("software.recipes.redis.linux.shutil.which", lambda name: name)
    source = tmp_path / "source.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        entry = tarfile.TarInfo("redis-1/src/README")
        entry.size = 5
        archive.addfile(entry, io.BytesIO(b"inert"))
    return driver, dest, source


def test_real_output_locations_are_copied(install, monkeypatch):
    driver, dest, source = install
    calls = []

    def build(command, **kwargs):
        calls.append(kwargs)
        for name in ("redis-server", "redis-cli", "redis-check-rdb"):
            (kwargs["cwd"] / "src" / name).write_bytes(b"inert build output")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("core.process.capture", build)
    assert driver.install_binary("1", source) == str(dest / "bin")
    assert (dest / "bin/redis-server").read_bytes() == b"inert build output"
    assert (dest / "bin/redis-cli").read_bytes() == b"inert build output"
    assert calls[0]["timeout"] == 1800 and calls[0]["stdin"] == subprocess.DEVNULL
    assert not calls[0]["cwd"].exists()


@pytest.mark.parametrize("failure", ["nonzero", "missing", "timeout", "cancel"])
def test_failed_or_incomplete_build_not_published(install, monkeypatch, failure):
    driver, dest, source = install

    def build(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "cancel":
            raise KeyboardInterrupt()
        return subprocess.CompletedProcess(command, 1 if failure == "nonzero" else 0, "", "")

    monkeypatch.setattr("core.process.capture", build)
    with pytest.raises(KeyboardInterrupt if failure == "cancel" else InstallError):
        driver.install_binary("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-redis-*"))
