"""Read a Debian ar container without executing ar or trusting member filenames."""
import os
from pathlib import Path
import stat
from contextlib import contextmanager
import bz2
import gzip
import tempfile

from core.archive_reader import BoundedXZReader, open_tar

MAX_DEB_BYTES = 2 * 1024**3
MAX_DATA_BYTES = 512 * 1024**2
MAX_AR_MEMBERS = 16
DATA_NAMES = frozenset(("data.tar", "data.tar.gz", "data.tar.xz", "data.tar.bz2", "data.tar.zst"))
CONTROL_NAMES = frozenset(name.replace("data.", "control.") for name in DATA_NAMES)
MAX_EXPANDED_TAR_BYTES = 2 * 1024**3 + 64 * 1024**2
MAX_LZMA_MEMORY_BYTES = 128 * 1024**2


@contextmanager
def open_data_tar(path: Path):
    """Expand into an owned, bounded file, keeping decompression buffers small."""
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix=".opskit-deb-tar-", dir=path.parent) as work:
        expanded = Path(work) / "data.tar"
        with path.open("rb") as source:
            if path.suffix == ".xz":
                reader = BoundedXZReader(source, memory_limit=MAX_LZMA_MEMORY_BYTES)
            elif path.suffix == ".gz":
                reader = gzip.GzipFile(fileobj=source)
            elif path.suffix == ".bz2":
                reader = bz2.BZ2File(source)
            elif path.suffix == ".zst":
                try:
                    import zstandard
                except ImportError as error:
                    raise ValueError("zstd Debian payload requires zstandard support") from error
                reader = zstandard.ZstdDecompressor(max_window_size=128 * 1024).stream_reader(source)
            elif path.suffix == ".tar":
                reader = source
            else:
                raise ValueError("unsupported Debian payload compression")
            with reader, expanded.open("xb") as output:
                total = 0
                while True:
                    chunk = reader.read(min(1024 * 1024, MAX_EXPANDED_TAR_BYTES - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_EXPANDED_TAR_BYTES:
                        raise ValueError("expanded Debian tar exceeds limit")
                    output.write(chunk)
        with open_tar(expanded, "r:") as archive:
            yield archive


def extract_data_tar(deb_path: Path, work_dir: Path) -> Path:
    """Validate the entire table first, then stream one exact data.tar member."""
    with Path(deb_path).open("rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_DEB_BYTES:
            raise ValueError("Debian container exceeds limits or is not a regular file")
        if source.read(8) != b"!<arch>\n":
            raise ValueError("invalid Debian ar magic")
        selected, names, count = None, set(), 0
        while source.tell() < metadata.st_size:
            header = source.read(60)
            count += 1
            if len(header) != 60 or header[58:] != b"`\n" or count > MAX_AR_MEMBERS:
                raise ValueError("invalid Debian ar header")
            try:
                name = header[:16].decode("ascii").strip().removesuffix("/")
            except UnicodeDecodeError as error:
                raise ValueError("invalid Debian ar name") from error
            size_text = header[48:58].strip()
            if not size_text or not size_text.isdigit():
                raise ValueError("invalid Debian ar member size")
            size = int(size_text)
            offset = source.tell()
            end = offset + size
            if end + size % 2 > metadata.st_size:
                raise ValueError("truncated Debian ar member")
            if name in names or name not in DATA_NAMES | CONTROL_NAMES | {"debian-binary"}:
                raise ValueError("unexpected or duplicate Debian ar member")
            names.add(name)
            if name == "debian-binary":
                if size != 4 or source.read(4) != b"2.0\n":
                    raise ValueError("unsupported Debian container version")
            elif name in DATA_NAMES:
                if selected is not None or size > MAX_DATA_BYTES:
                    raise ValueError("ambiguous or oversized Debian data member")
                selected = name, offset, size
            source.seek(end)
            if size % 2 and source.read(1) != b"\n":
                raise ValueError("invalid Debian ar padding")
        if selected is None or "debian-binary" not in names:
            raise ValueError("Debian data member or version is missing")
        name, offset, remaining = selected
        work_dir = Path(work_dir)
        if not work_dir.is_dir() or work_dir.is_symlink():
            raise ValueError("Debian output must be an owned directory")
        target = work_dir / name
        source.seek(offset)
        try:
            output = target.open("xb")
        except FileExistsError:
            raise ValueError("Debian data output already exists") from None
        try:
            with output:
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError("Debian data member changed during reading")
                    output.write(chunk)
                    remaining -= len(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target
