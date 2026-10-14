"""Stdlib-only native archive/publication smoke; all artifacts use owned /tmp data."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import stat
import tarfile
import tempfile
import lzma
import struct
import zipfile


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    archive = load("verified_archive", root / "core/archive.py")
    reader = load("verified_archive_reader", root / "core/archive_reader.py")
    publication = load("verified_publication", root / "software/_shared/archive_install.py")
    debian = load("verified_debian", root / "core/debian.py")
    if os.name != "posix":
        raise RuntimeError("this verifier requires a native POSIX filesystem")
    if "--require-ascii" in sys.argv and sys.getfilesystemencoding() != "ascii":
        raise RuntimeError("ASCII filesystem mode was not activated")
    checks = []
    with tempfile.TemporaryDirectory(prefix="opskit-native-archive-") as work:
        work = Path(work)
        source = work / "package.tar.gz"
        with tarfile.open(source, "w:gz") as output:
            for name in ("root/bin/node", "root/lib/npm/cli.js", "root/test/\u00defoo.go"):
                member = tarfile.TarInfo(name)
                member.size, member.mode = 5, 0o755
                output.addfile(member, io.BytesIO(b"inert"))
            member = tarfile.TarInfo("root/bin/npm")
            member.type, member.linkname = tarfile.SYMTYPE, "../lib/npm/cli.js"
            output.addfile(member)
        dest = work / "version"
        with publication.staged_directory(dest, prefix=".owned-", utf8_paths=True) as payload:
            with reader.open_tar(source) as package:
                archive.extract_tar(package, payload, strip_components=1, allow_symlinks=True, utf8_paths=True)
            assert not dest.exists()
        assert (dest / "bin/npm").is_symlink()
        assert (dest / "bin/npm").read_bytes() == b"inert"
        assert (dest / "bin/node").stat().st_mode & 0o111 == 0o111
        with open(str(dest / "test/\u00defoo.go").encode("utf-8"), "rb") as handle:
            assert handle.read() == b"inert"
        checks.extend(["exclusive_publish", "relative_link_read", "executable_mode", "utf8_byte_member"])
        for populated in (False, True):
            collision = work / ("occupied" + str(populated))
            try:
                with publication.staged_directory(collision, prefix=".owned-", utf8_paths=True) as payload:
                    payload.mkdir()
                    (payload / "binary").write_bytes(b"inert")
                    collision.mkdir()
                    if populated:
                        (collision / "data").write_bytes(b"keep")
            except FileExistsError:
                pass
            else:
                raise AssertionError("publication replaced a competing directory")
            assert not (collision / "binary").exists()
            if populated:
                assert (collision / "data").read_bytes() == b"keep"
            checks.append("collision_preserved_" + str(populated))
        for failure in (OSError, KeyboardInterrupt):
            cancelled = work / "cancelled"
            try:
                with publication.staged_directory(cancelled, prefix=".owned-", utf8_paths=True) as payload:
                    payload.mkdir()
                    raise failure("injected")
            except failure:
                pass
            assert not cancelled.exists() and not list(work.glob(".owned-*"))
            checks.append("owned_cleanup_" + failure.__name__)
        bad = work / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as output:
            member = tarfile.TarInfo("root/bin/npm")
            member.type, member.linkname = tarfile.SYMTYPE, "../../../escape"
            output.addfile(member)
        with reader.open_tar(bad) as package:
            try:
                archive.extract_tar(package, work / "unsafe", strip_components=1, allow_symlinks=True)
            except ValueError:
                pass
            else:
                raise AssertionError("escaping link accepted")
        assert not (work / "unsafe").exists()
        checks.append("escaping_link_prewrite_rejection")
        zipped = work / "package.zip"
        with zipfile.ZipFile(zipped, "w") as output:
            for name, mode in [("pgsql/bin/psql", 0o6755), ("pgsql/doc/readme", 0o644)]:
                member = zipfile.ZipInfo(name)
                member.create_system = 3
                member.external_attr = (stat.S_IFREG | mode) << 16
                output.writestr(member, b"inert")
        with reader.open_zip(zipped) as package:
            archive.extract_zip(package, work / "zip-version", strip_components=1, preserve_mode=True)
        assert stat.S_IMODE((work / "zip-version/bin/psql").stat().st_mode) == 0o755
        assert stat.S_IMODE((work / "zip-version/doc/readme").stat().st_mode) == 0o644
        checks.extend(["zip_execute_without_setid", "zip_nonexecutable_mode"])
        for index, names in enumerate([
            ("root/tool", "root/./tool"),
            ("root/bin", "root/bin/tool"),
            ("root/bin/tool", "root/bin"),
        ]):
            bad_zip = work / f"bad-layout-{index}.zip"
            with zipfile.ZipFile(bad_zip, "w") as output:
                output.writestr("root/safe", b"inert")
                for name in names:
                    output.writestr(name, b"inert")
            destination = work / f"bad-layout-{index}"
            with reader.open_zip(bad_zip) as package:
                try:
                    archive.extract_zip(package, destination, strip_components=1)
                except ValueError:
                    pass
                else:
                    raise AssertionError("ambiguous ZIP layout accepted")
            assert not destination.exists()
        checks.append("zip_ambiguous_layout_prewrite_rejection")
        old_zip = work / "zip-old"
        (old_zip / "bin").mkdir(parents=True)
        (old_zip / "bin/psql").write_bytes(b"keep")
        with reader.open_zip(zipped) as package:
            try:
                archive.extract_zip(package, old_zip, strip_components=1)
            except ValueError:
                pass
            else:
                raise AssertionError("ZIP replaced existing file")
        assert (old_zip / "bin/psql").read_bytes() == b"keep"
        assert not (old_zip / "doc").exists()
        checks.append("zip_existing_file_preserved")
        class GuardedBytes(io.BytesIO):
            largest = 0
            def read(self, size=-1):
                self.largest = max(self.largest, size)
                assert 0 <= size <= 1024 * 1024
                return super().read(size)

        header = tarfile.TarInfo("metadata")
        header.type, header.size = tarfile.XHDTYPE, 64 * 1024**2
        guarded = GuardedBytes(header.tobuf())
        try:
            with reader.open_tar(fileobj=guarded):
                raise AssertionError("giant tar extension accepted")
        except ValueError:
            pass
        assert guarded.largest <= 512
        checks.append("tar_metadata_preallocation_limit")
        header.size = 0
        guarded = GuardedBytes(header.tobuf() * 100)
        try:
            with reader.open_tar(fileobj=guarded):
                raise AssertionError("recursive tar extensions accepted")
        except ValueError:
            pass
        assert guarded.tell() <= 9 * 512
        checks.append("tar_extension_nesting_limit")
        # Construct records with their actual byte length, not a parser guess.
        record = b"13 comment=v\n"
        assert len(record) == 13
        data = record * 10000
        header.size = len(data)
        guarded = GuardedBytes(header.tobuf() + data + b"\0" * (-len(data) % 512))
        try:
            with reader.open_tar(fileobj=guarded):
                raise AssertionError("duplicate PAX record amplification accepted")
        except ValueError as failure:
            assert "PAX record count" in str(failure)
        checks.append("pax_raw_record_preflight")
        previous_nodes = archive.MAX_LAYOUT_NODES
        archive.MAX_LAYOUT_NODES = 2
        try:
            for kind in ("tar", "zip"):
                destination = work / ("budget-" + kind)
                opener = reader.open_tar if kind == "tar" else reader.open_zip
                path = source if kind == "tar" else zipped
                with opener(path) as package:
                    try:
                        if kind == "tar":
                            archive.extract_tar(package, destination, strip_components=1,
                                                utf8_paths=True, allow_symlinks=True)
                        else:
                            archive.extract_zip(package, destination, strip_components=1)
                    except ValueError as failure:
                        assert "layout exceeds limits" in str(failure)
                    else:
                        raise AssertionError("implicit directory budget bypassed")
                assert not destination.exists()
                checks.append(kind + "_implicit_directory_budget")
        finally:
            archive.MAX_LAYOUT_NODES = previous_nodes
        end = struct.pack(zipfile.structEndArchive, zipfile.stringEndArchive,
                          0, 0, 1, 1, 64 * 1024**2, 0, 0)
        guarded = GuardedBytes(end)
        try:
            with reader.open_zip(fileobj=guarded):
                raise AssertionError("giant ZIP central directory accepted")
        except ValueError:
            pass
        assert guarded.largest <= 65558
        checks.append("zip_directory_preallocation_limit")
        def ar_member(name, data):
            header = (name.encode().ljust(16) + b"0".ljust(12) + b"0".ljust(6) + b"0".ljust(6)
                      + b"100644".ljust(8) + str(len(data)).encode().ljust(10) + b"`\n")
            return header + data + (b"\n" if len(data) % 2 else b"")

        deb = work / "package.deb"
        content = b"!<arch>\n" + ar_member("debian-binary/", b"2.0\n") + ar_member("data.tar.gz/", b"inert payload")
        deb.write_bytes(content)
        deb_work = work / "deb-work"
        deb_work.mkdir()
        assert debian.extract_data_tar(deb, deb_work).read_bytes() == b"inert payload"
        checks.append("debian_exact_payload")
        deb.write_bytes(content + ar_member("../escape/", b"bad"))
        bad_work = work / "bad-deb-work"
        bad_work.mkdir()
        try:
            debian.extract_data_tar(deb, bad_work)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe late ar member accepted")
        assert not list(bad_work.iterdir())
        checks.append("debian_full_table_prewrite_rejection")
        xz_payload = work / "data.tar.xz"
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w") as output:
            member = tarfile.TarInfo("./usr/bin/psql")
            member.size, member.mode = 5, 0o755
            output.addfile(member, io.BytesIO(b"inert"))
        xz_payload.write_bytes(lzma.compress(tar_bytes.getvalue()))

        def select(name, member):
            return "bin/psql" if name == "usr/bin/psql" and member.isfile() else None

        with debian.open_data_tar(xz_payload) as package:
            archive.extract_tar_files(package, work / "selected", select)
        assert (work / "selected/bin/psql").read_bytes() == b"inert"
        assert stat.S_IMODE((work / "selected/bin/psql").stat().st_mode) == 0o755
        assert not list(work.glob(".opskit-deb-tar-*"))
        checks.append("bounded_xz_allowlist_selection")
    return {"platform": sys.platform, "python": sys.version.split()[0],
            "filesystem_encoding": sys.getfilesystemencoding(), "checks": checks, "owned_data_removed": True}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=True))
