"""Opt-in internal links preserve npm/JDK layouts without creating write-through paths."""
import io
import tarfile

import pytest

from core.archive import extract_tar


def package(path, links, files=("bin/node", "lib/npm/cli.js"), directories=()):
    with tarfile.open(path, "w:gz") as archive:
        # Links deliberately precede regular files to test delayed creation.
        for name, target in links:
            member = tarfile.TarInfo("root/" + name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            archive.addfile(member)
        for name in directories:
            member = tarfile.TarInfo("root/" + name)
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
        for name in files:
            member = tarfile.TarInfo("root/" + name)
            member.size, member.mode = 5, 0o755
            archive.addfile(member, io.BytesIO(b"inert"))


def extract(source, dest, **kwargs):
    with tarfile.open(source, "r:gz") as archive:
        extract_tar(archive, dest, strip_components=1, **kwargs)


def test_internal_file_and_directory_links(tmp_path):
    try:
        (tmp_path / "probe").symlink_to("missing")
        (tmp_path / "probe").unlink()
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    source, dest = tmp_path / "package.tar.gz", tmp_path / "dest"
    package(source, [("bin/npm", "../lib/npm/cli.js"), ("bin/npx", "npm"), ("legal/alias", "../lib/npm")])
    extract(source, dest, allow_symlinks=True)
    assert (dest / "bin/npm").is_symlink()
    assert (dest / "bin/npm").read_bytes() == b"inert"
    assert (dest / "bin/npx").read_bytes() == b"inert"
    assert (dest / "legal/alias/cli.js").read_bytes() == b"inert"


@pytest.mark.parametrize("links,files", [
    ([("bin/npm", "../../../outside")], ("bin/node",)),
    ([("bin/npm", "/outside")], ("bin/node",)),
    ([("bin/npm", "C:/outside")], ("bin/node",)),
    ([("bin/npm", "..\\..\\outside")], ("bin/node",)),
    ([("bin/npm", "missing")], ("bin/node",)),
    ([("bin/npm", "npm")], ("bin/node",)),
    ([("bin/npm", "npx"), ("bin/npx", "npm")], ("bin/node",)),
    ([("lib", "bin")], ("bin/node", "lib/overwrite")),
    ([("lib/loop", "..")], ("lib/node",)),
    ([("a/loop", "../b"), ("b/loop", "../a")], ("a/file", "b/file")),
    ([("bin/npm", "../alias/cli.js"), ("alias", "lib/npm")], ("lib/npm/cli.js",)),
])
def test_invalid_link_graph_rejected_before_writes(tmp_path, links, files):
    source, dest = tmp_path / "package.tar.gz", tmp_path / "dest"
    package(source, links, files)
    with pytest.raises(ValueError):
        extract(source, dest, allow_symlinks=True)
    assert not dest.exists()


def test_links_remain_disabled_by_default(tmp_path):
    source, dest = tmp_path / "package.tar.gz", tmp_path / "dest"
    package(source, [("bin/npm", "../lib/npm/cli.js")])
    with pytest.raises(ValueError):
        extract(source, dest)
    assert not dest.exists()


def test_duplicate_member_rejected_before_writes(tmp_path):
    source, dest = tmp_path / "package.tar.gz", tmp_path / "dest"
    package(source, [], ("bin/node", "bin/node"))
    with pytest.raises(ValueError, match="duplicate"):
        extract(source, dest)
    assert not dest.exists()


def test_file_directory_collision_rejected_before_writes(tmp_path):
    source, dest = tmp_path / "package.tar.gz", tmp_path / "dest"
    package(source, [], ("bin/node", "bin/node/child"))
    with pytest.raises(ValueError):
        extract(source, dest)
    assert not dest.exists()
