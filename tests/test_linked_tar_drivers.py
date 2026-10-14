"""Known inert npm/JDK layouts traverse actual driver publication boundaries."""
import importlib
import io
import tarfile

import pytest

from software.base import InstallError


@pytest.fixture(params=["nodejs", "java"])
def driver(request, tmp_path, monkeypatch):
    name = request.param
    directory, bin_directory, binary = (("node_version_dir", "node_bin_dir", "node") if name == "nodejs"
                                        else ("java_version_dir", "java_bin_dir", "java"))
    common = importlib.import_module("software.recipes." + name + ".common")
    module = importlib.import_module("software.recipes." + name + ".linux")
    dest = tmp_path / "dest"
    monkeypatch.setattr(common, directory, lambda version: dest)
    monkeypatch.setattr(common, bin_directory, lambda version: dest / "bin")
    return module.LinuxDriver(), dest, binary


def package(path, binary, bad=None, missing=False):
    with tarfile.open(path, "w:gz") as archive:
        for name in (["README"] if missing else ["bin/" + binary, "lib/npm/cli.js", "legal/java.base/LICENSE"]):
            item = tarfile.TarInfo("root/" + name)
            item.size, item.mode = 5, 0o755
            archive.addfile(item, io.BytesIO(b"inert"))
        for name, target in ([] if missing else [("bin/npm", "../lib/npm/cli.js"), ("legal/java.logging", "java.base")]):
            item = tarfile.TarInfo("root/" + name)
            item.type, item.linkname = tarfile.SYMTYPE, target
            archive.addfile(item)
        if bad:
            item = tarfile.TarInfo("root/evil")
            item.type, item.linkname = tarfile.SYMTYPE, bad
            archive.addfile(item)


def test_normal_layout_retains_required_links(driver, tmp_path):
    try:
        (tmp_path / "probe").symlink_to("missing")
        (tmp_path / "probe").unlink()
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    instance, dest, binary = driver
    source = tmp_path / "package.tar.gz"
    package(source, binary)
    assert instance.install_tarball("1", source) == str(dest / "bin")
    assert (dest / "bin" / binary).read_bytes() == b"inert"
    assert (dest / "bin/npm").is_symlink()
    assert (dest / "bin/npm").read_bytes() == b"inert"
    assert (dest / "legal/java.logging/LICENSE").read_bytes() == b"inert"
    assert not list(tmp_path.glob(".opskit-*"))


@pytest.mark.parametrize("bad", ["../../outside", "/outside", "evil"])
def test_bad_link_package_not_published(driver, tmp_path, bad):
    instance, dest, binary = driver
    source = tmp_path / "package.tar.gz"
    package(source, binary, bad)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not list(tmp_path.glob(".opskit-*"))


def test_existing_version_preserved(driver, tmp_path):
    instance, dest, binary = driver
    dest.mkdir()
    (dest / "user-data").write_bytes(b"keep")
    source = tmp_path / "package.tar.gz"
    package(source, binary)
    with pytest.raises(InstallError, match="refusing to overwrite"):
        instance.install_tarball("1", source)
    assert (dest / "user-data").read_bytes() == b"keep"


def test_missing_binary_fails_before_publication(driver, tmp_path):
    instance, dest, binary = driver
    source = tmp_path / "package.tar.gz"
    package(source, binary, missing=True)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not list(tmp_path.glob(".opskit-*"))
