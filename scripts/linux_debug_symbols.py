"""Split Linux runtime ELF debug information into a GDB build-ID tree."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class ElfLayout:
    header: tuple
    segments: tuple
    allocated: tuple
    build_id: str | None
    has_debug: bool
    has_overlay: bool


def _section_data(image: memoryview, section: tuple) -> memoryview:
    offset, size = section[4:6]
    if offset + size > len(image):
        raise ValueError("ELF section extends beyond end of file")
    return image[offset:offset + size]


def _build_id(note: memoryview, endian: str) -> str:
    namesize, descsize, kind = struct.unpack_from(endian + "III", note)
    offset = 12 + ((namesize + 3) & ~3)
    if bytes(note[12:12 + namesize]) != b"GNU\0" or kind != 3:
        raise ValueError("Invalid GNU build-ID note")
    if descsize < 2 or offset + descsize > len(note):
        raise ValueError("Invalid GNU build-ID size")
    return bytes(note[offset:offset + descsize]).hex()


def _inspect_elf(path: Path) -> ElfLayout | None:
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            return None
        stream.seek(0)
        data = stream.read()
    image = memoryview(data)
    if len(image) < 16 or image[4] not in (1, 2) or image[5] not in (1, 2):
        raise ValueError(f"Unsupported ELF encoding in {path}")

    elf64 = image[4] == 2
    order = "<" if image[5] == 1 else ">"
    fmt = order + ("HHIQQQIHHHHHH" if elf64 else "HHIIIIIHHHHHH")
    kind, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, shstr = (
        struct.unpack_from(fmt, image, 16)
    )
    if kind not in (2, 3) or shoff == 0:  # Executables and shared objects with section tables.
        return None
    sfmt = struct.Struct(order + ("IIQQQQIIQQ" if elf64 else "IIIIIIIIII"))
    pfmt = struct.Struct(order + ("IIQQQQQQ" if elf64 else "IIIIIIII"))
    if shsize < sfmt.size or phnum and phsize < pfmt.size:
        raise ValueError(f"Invalid ELF table entry size in {path}")

    first = sfmt.unpack_from(image, shoff)
    if shnum == 0:
        shnum = first[5]
    if shstr == 0xFFFF:
        shstr = first[6]
    if phnum == 0xFFFF:
        phnum = first[7]
    if shnum <= shstr or shoff + shsize * shnum > len(image) or phoff + phsize * phnum > len(image):
        raise ValueError(f"Invalid ELF table bounds in {path}")

    sections = tuple(sfmt.unpack_from(image, shoff + idx * shsize) for idx in range(shnum))
    segments = tuple(pfmt.unpack_from(image, phoff + idx * phsize) for idx in range(phnum))
    names = bytes(_section_data(image, sections[shstr]))
    section_names = tuple(names[s[0]:names.index(b"\0", s[0])] for s in sections)
    end = max(ehsize, shoff + shsize * shnum, phoff + phsize * phnum)
    for segment in segments:
        off, size = (segment[2], segment[5]) if elf64 else (segment[1], segment[4])
        if size:
            end = max(end, off + size)

    build_id = None
    has_debug = False
    allocated = []
    for name, section in zip(section_names[1:], sections[1:]):
        _, section_kind, attributes, address, offset, size, link, info, alignment, entsize = section
        has_bytes = section_kind != 8 and size > 0  # SHT_NOBITS only occupies virtual memory.
        if has_bytes:
            end = max(end, offset + size)
        if name == b".note.gnu.build-id":
            build_id = _build_id(_section_data(image, section), order)
        if name.startswith((b".debug_", b".zdebug_")) or name in (b".stab", b".stabstr"):
            has_debug = True
        if attributes & 2:  # SHF_ALLOC
            target = section_names[link]
            info = section_names[info] if attributes & 0x40 else info  # SHF_INFO_LINK
            digest = hashlib.sha256(_section_data(image, section)).digest() if has_bytes else None
            allocated.append((name, section_kind, attributes, address, size, target, info, alignment, entsize, digest))
    if end > len(image):
        raise ValueError(f"Invalid ELF file extent in {path}")
    return ElfLayout(
        (elf64, order, kind, machine, version, entry, flags), segments, tuple(allocated), build_id, has_debug,
        end < len(image),
    )


def _regular_files(root: Path) -> list[Path]:
    files = []
    for directory, _, names in os.walk(root, followlinks=False):
        for name in names:
            path = Path(directory, name)
            if stat.S_ISREG(path.lstat().st_mode):
                files.append(path)
    return sorted(files)


def _objcopy(executable: str, mode: str, original: Path, output: Path) -> None:
    result = subprocess.run(
        [executable, mode, str(original), str(output)], capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"objcopy {mode} failed for {original}: {result.stderr.strip()}")


def _make_split(path: Path, before: ElfLayout, runtime: Path, debug: Path, executable: str) -> None:
    _objcopy(executable, "--only-keep-debug", path, debug)
    _objcopy(executable, "--strip-debug", path, runtime)
    after = _inspect_elf(runtime)
    symbols = _inspect_elf(debug)
    if symbols is None or not symbols.has_debug or symbols.build_id != before.build_id:
        raise ValueError(f"Debug sidecar lost symbols or the build ID for {path}")
    if after is None or after.has_debug or after.has_overlay or (
        after.header, after.segments, after.allocated, after.build_id
    ) != (
        before.header, before.segments, before.allocated, before.build_id
    ):
        raise ValueError(f"ELF runtime contents or metadata changed for {path}")
    shutil.copystat(path, runtime)


def split_debug_symbols(
    distribution: Path, symbols: Path, launcher: str = "opskit", *, objcopy: str = "objcopy",
) -> list[dict[str, str | int]]:
    """Strip eligible Linux ELF files and retain their matching GDB symbols."""
    distribution, symbols = Path(distribution), Path(symbols)
    if distribution.is_symlink() or symbols.exists() or symbols.is_symlink():
        raise ValueError("Use a regular distribution directory and a new symbols directory")
    distribution = distribution.resolve(strict=True)
    symbols = symbols.parent.resolve(strict=True) / symbols.name
    if not distribution.is_dir() or symbols.is_relative_to(distribution) or distribution.is_relative_to(symbols):
        raise ValueError("Linux distribution and symbols directories must be separate")
    entry = Path(launcher)
    if entry.is_absolute() or ".." in entry.parts or not (distribution / entry).is_file():
        raise ValueError("Specify the launcher path relative to the Linux distribution")
    paths = _regular_files(distribution)

    records = []
    skipped = []
    pending = []
    with (
        TemporaryDirectory(prefix=".split-elf-", dir=distribution) as build_tmp,
        TemporaryDirectory(prefix=".elf-symbols-", dir=symbols.parent) as symbol_tmp,
    ):
        build, debug_root = Path(build_tmp), Path(symbol_tmp, "symbols")
        debug_root.mkdir()
        for index, path in enumerate(paths):
            relative = path.relative_to(distribution)
            if relative == entry:
                continue
            before = _inspect_elf(path)
            if before is None or not before.has_debug:
                continue
            if before.has_overlay or before.build_id is None:
                skipped.append({
                    "path": relative.as_posix(),
                    "reason": "overlay" if before.has_overlay else "missing-build-id",
                })
                continue
            build_id = before.build_id
            sidecar = Path(".build-id", build_id[:2], build_id[2:] + ".debug")
            output = debug_root / sidecar
            output.parent.mkdir(parents=True, exist_ok=True)
            new, debug, original = (build / f"{index}.{suffix}" for suffix in ("elf", "debug", "original"))
            _make_split(path, before, new, debug, objcopy)
            if output.exists():
                if debug.read_bytes() != output.read_bytes():
                    raise ValueError(f"Conflicting debug files for build ID {build_id}")
            else:
                shutil.copyfile(debug, output)
            records.append({
                "path": relative.as_posix(),
                "build_id": build_id,
                "debug_path": sidecar.as_posix(),
                "removed_bytes": path.stat().st_size - new.stat().st_size,
            })
            pending.append((path, new, original))

        (debug_root / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "files": records, "skipped": skipped}, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        replaced = []
        try:
            for path, new, original in pending:
                os.link(path, original, follow_symlinks=False)
                os.replace(new, path)
                replaced.append((path, original))
            os.rename(debug_root, symbols)
        except OSError:
            for path, original in reversed(replaced):
                os.replace(original, path)
            raise
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution", type=Path)
    parser.add_argument("symbols", type=Path)
    parser.add_argument("--launcher", default="opskit")
    parser.add_argument("--objcopy", default="objcopy")
    args = parser.parse_args()
    files = split_debug_symbols(args.distribution, args.symbols, args.launcher, objcopy=args.objcopy)
    print(f"Separated Linux debug symbols from {len(files)} files")


if __name__ == "__main__":
    main()
