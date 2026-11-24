import io
import lzma
import os
import tarfile

import pytest

from core.directory_archive import (
    MANIFEST_NAME, ArchiveError, archive_entry, manifest_bytes, read_manifest, unpack_package,
    validate_path, verify_installed_tree,
)
from core.paths import absolute_path
from scripts.directory_package import create_package


def source_tree(root, *, updated=False):
    root.mkdir()
    (root / "library").mkdir()
    (root / "library/shared.dat").write_bytes(b"same content\n")
    executable = root / ("opskit.exe" if os.name == "nt" else "opskit")
    executable.write_bytes(b"second executable" if updated else b"first executable")
    executable.chmod(0o755)
    (root / ("added" if updated else "removed")).write_bytes(b"side data")
    return executable.name


def test_full_delta_deterministic_members_and_independent_copy(tmp_path):
    old_source, new_source = tmp_path / "old-source", tmp_path / "new-source"
    app = source_tree(old_source)
    source_tree(new_source, updated=True)
    full, duplicate, delta = (tmp_path / name for name in ("full.tar.xz", "duplicate.tar.xz", "delta.tar.xz"))
    old_manifest = create_package(old_source, full, 1)
    create_package(old_source, duplicate, 1)
    assert full.read_bytes() == duplicate.read_bytes()
    new_manifest = create_package(new_source, delta, 2, base=full)
    assert new_manifest.base_version == 1
    assert new_manifest.base_tree_sha256 == old_manifest.tree_sha256
    assert new_manifest.changed == ("added", app)
    assert read_manifest(delta) == new_manifest

    old, new = tmp_path / "old-install", tmp_path / "new-install"
    old.mkdir()
    new.mkdir()
    assert unpack_package(full, old, 1) == old_manifest
    assert unpack_package(delta, new, 2, base=old) == new_manifest
    verify_installed_tree(new, version=2, tree_sha256=new_manifest.tree_sha256)
    assert (old / app).read_bytes() == b"first executable"
    assert (new / app).read_bytes() == b"second executable"
    assert (old / "removed").exists()
    assert (new / "added").exists()
    assert not (new / "removed").exists()
    if os.name != "nt":
        assert (new / app).stat().st_mode & 0o111
    old_shared, new_shared = old / "library/shared.dat", new / "library/shared.dat"
    assert not os.path.samefile(old_shared, new_shared)
    new_shared.write_bytes(b"independent")
    assert old_shared.read_bytes() == b"same content\n"
    with pytest.raises(ArchiveError):
        verify_installed_tree(new, version=2, tree_sha256=new_manifest.tree_sha256)


@pytest.mark.parametrize("damage", ["changed", "extra", "missing"])
def test_delta_refuses_modified_base_including_files_removed_by_update(tmp_path, damage):
    old_source, new_source = tmp_path / "old-source", tmp_path / "new-source"
    source_tree(old_source)
    source_tree(new_source, updated=True)
    full, delta = tmp_path / "full.tar.xz", tmp_path / "delta.tar.xz"
    create_package(old_source, full, 1)
    create_package(new_source, delta, 2, base=full)
    old, new = tmp_path / "old-install", tmp_path / "new-install"
    old.mkdir()
    new.mkdir()
    unpack_package(full, old, 1)
    if damage == "changed":
        (old / "removed").write_bytes(b"other dat")
    elif damage == "extra":
        (old / "unexpected").touch()
    else:
        (old / "removed").unlink()
    with pytest.raises(ArchiveError, match="inventory|digest"):
        unpack_package(delta, new, 2, base=old)
    assert not list(new.iterdir())


@pytest.mark.parametrize("path", ["../bad", "/outside", "good/../bad", "good//bad", "good/x:stream",
                                "good/a\\b", "good/nul.txt", "good/lpt²", "good/trailing.", MANIFEST_NAME])
def test_reject_archive_paths_incompatible_with_either_platform(path):
    with pytest.raises(ArchiveError):
        validate_path(path)


@pytest.mark.parametrize("tamper", ["symlink", "extra-entry", "changed-bytes", "trailing", "wrong-version"])
def test_reject_malformed_archive_members(tmp_path, tamper):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app").write_bytes(b"package body")
    full, malformed = tmp_path / "full.tar.xz", tmp_path / "malformed.tar.xz"
    manifest = create_package(source, full, 1)
    if tamper == "trailing":
        malformed.write_bytes(full.read_bytes() + lzma.compress(b"trailing stream"))
    elif tamper == "wrong-version":
        malformed = full
    else:
        payload = b"package body" if tamper != "changed-bytes" else b"package copy"
        with tarfile.open(malformed, "w:xz", format=tarfile.USTAR_FORMAT) as tar:
            encoded = manifest_bytes(manifest)
            tar.addfile(archive_entry(MANIFEST_NAME, len(encoded), 0o644), io.BytesIO(encoded))
            app = archive_entry("payload/app", len(payload), 0o644)
            if tamper == "symlink":
                app.type = tarfile.SYMTYPE
                app.linkname = "../outside"
                app.size = 0
            tar.addfile(app, io.BytesIO(payload))
            if tamper == "extra-entry":
                tar.addfile(archive_entry("payload/extra", 0, 0o644), io.BytesIO())
    output = tmp_path / "destination"
    output.mkdir()
    with pytest.raises(ArchiveError):
        unpack_package(malformed, output, 2 if tamper == "wrong-version" else 1)
    assert not (output / MANIFEST_NAME).exists()
    assert not (tmp_path / "outside").exists()


def test_internal_relative_library_links_are_materialized(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "lib.example.1").write_bytes(b"example")
    try:
        (source / "lib.example").symlink_to("lib.example.1")
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    full = tmp_path / "full.tar.xz"
    create_package(source, full, 1)
    destination = tmp_path / "destination"
    destination.mkdir()
    unpack_package(full, destination, 1)
    assert (destination / "lib.example").read_bytes() == b"example"
    assert not (destination / "lib.example").is_symlink()

    (tmp_path / "outside").write_bytes(b"private")
    (source / "bad-link").symlink_to("../outside")
    with pytest.raises(ArchiveError, match="inside"):
        create_package(source, full, 1)


@pytest.mark.skipif(os.name != "nt", reason="extended Windows paths")
def test_full_and_delta_extract_files_beyond_windows_path_limit(tmp_path):
    old_source, new_source = tmp_path / "old-source", tmp_path / "new-source"
    source_tree(old_source)
    source_tree(new_source, updated=True)
    relative = "/".join(("library-" + "a" * 47, "support-" + "b" * 47, "resource-" + "c" * 80))
    for source in old_source, new_source:
        file = absolute_path(source) / relative
        file.parent.mkdir(parents=True)
        file.write_bytes(b"unchanged long-path file")
    full, delta = tmp_path / "full.tar.xz", tmp_path / "delta.tar.xz"
    create_package(old_source, full, 1)
    candidate = create_package(new_source, delta, 2, base=full)
    target = tmp_path / ("install-location-" + "x" * 55)
    target.mkdir()
    old, new = target / "old-install", target / "new-install"
    assert len(str(old / relative)) > 260
    for destination in old, new:
        destination.mkdir()
    unpack_package(full, old, 1)
    unpack_package(delta, new, 2, base=old)
    verify_installed_tree(new, version=2, tree_sha256=candidate.tree_sha256)
    assert (absolute_path(new) / relative).read_bytes() == b"unchanged long-path file"
