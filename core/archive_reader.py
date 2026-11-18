"""Read-only archive openers with limits before metadata allocation.

Compression is decoded in bounded chunks into a private temporary file. Tar
headers and ZIP central records are checked before stdlib creates their full
metadata inventory. Extraction policy remains in core.archive.
"""
from contextlib import ExitStack, contextmanager
import bz2
import gzip
import io
import lzma
import struct
import tarfile
import tempfile
import zipfile

from core.archive import MAX_ARCHIVE_PATH_BYTES, MAX_ARCHIVE_PATH_DEPTH

MAX_FILES = 100_000
MAX_PAYLOAD_BYTES = 2 * 1024**3
MAX_STREAM_BYTES = MAX_PAYLOAD_BYTES + 256 * 1024**2
MAX_METADATA_BYTES = 64 * 1024**2
MAX_TAR_HEADER_BYTES = 1024**2
MAX_TAR_HEADER_DEPTH = 8
MAX_PAX_FIELDS = 128
MAX_ZIP_DIRECTORY_BYTES = 16 * 1024**2
MAX_XZ_MEMORY_BYTES = 128 * 1024**2
CHUNK_BYTES = 1024**2


class BoundedXZReader:
    """Single XZ stream, bounded dictionary/output, strict standard padding."""
    def __init__(self, source, *, memory_limit=MAX_XZ_MEMORY_BYTES):
        self.source = source
        self.decoder = lzma.LZMADecompressor(memlimit=memory_limit)
        self.padding_checked = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.decoder = None

    def read(self, size):
        if not 0 < size <= CHUNK_BYTES:
            raise ValueError("invalid bounded XZ read")
        while True:
            if self.decoder.eof:
                if not self.padding_checked:
                    chunk = self.decoder.unused_data or self.source.read(65536)
                    padding = 0
                    while chunk:
                        if any(chunk):
                            raise ValueError("unexpected data after XZ stream")
                        padding += len(chunk)
                        chunk = self.source.read(65536)
                    if padding % 4:
                        raise ValueError("invalid XZ padding")
                    self.padding_checked = True
                return b""
            chunk = self.source.read(65536) if self.decoder.needs_input else b""
            if not chunk and self.decoder.needs_input:
                raise ValueError("truncated XZ stream")
            decoded = self.decoder.decompress(chunk, max_length=size)
            if decoded:
                return decoded


class _LimitedSource:
    def __init__(self, source, max_read):
        self.source, self.max_read = source, max_read

    def tell(self):
        return self.source.tell()

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=io.SEEK_SET):
        position = self.source.seek(offset, whence)
        if not 0 <= position <= MAX_STREAM_BYTES:
            raise ValueError("archive stream exceeds limits")
        return position

    def read(self, size=-1):
        if size is None or size < 0:
            position = self.tell()
            size = self.seek(0, io.SEEK_END) - position
            self.seek(position)
        if size > self.max_read:
            raise ValueError("archive read exceeds limits")
        remaining = MAX_STREAM_BYTES - self.tell()
        if remaining < 0:
            raise ValueError("archive stream exceeds limits")
        chunk = self.source.read(min(size, remaining + 1))
        if len(chunk) > remaining:
            raise ValueError("archive stream exceeds limits")
        return chunk


class _CachedTarReader:
    def __init__(self, source, storage):
        self.source, self.storage = source, storage
        self.position = self.expanded = 0
        self.eof = False

    def _fill(self, target):
        while not self.eof and self.expanded < target:
            chunk = self.source.read(min(CHUNK_BYTES, target - self.expanded))
            if not chunk:
                self.eof = True
                break
            if self.expanded + len(chunk) > MAX_STREAM_BYTES:
                raise ValueError("expanded archive exceeds limits")
            self.storage.seek(self.expanded)
            self.storage.write(chunk)
            self.expanded += len(chunk)

    def finish(self):
        while not self.eof:
            self._fill(self.expanded + min(CHUNK_BYTES, MAX_STREAM_BYTES - self.expanded + 1))

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_CUR:
            offset += self.position
        elif whence == io.SEEK_END:
            self.finish()
            offset += self.expanded
        elif whence != io.SEEK_SET:
            raise ValueError("invalid archive seek")
        if not 0 <= offset <= MAX_STREAM_BYTES:
            raise ValueError("archive stream exceeds limits")
        self.position = offset
        return offset

    def read(self, size):
        if not 0 <= size <= CHUNK_BYTES or self.position + size > MAX_STREAM_BYTES:
            raise ValueError("archive read exceeds limits")
        self._fill(self.position + size)
        self.storage.seek(self.position)
        chunk = self.storage.read(size)
        self.position += len(chunk)
        return chunk


def _name_quota(name):
    if (len(name.encode("utf-8", "surrogatepass")) > MAX_ARCHIVE_PATH_BYTES
            or len(name.replace("\\", "/").split("/")) > MAX_ARCHIVE_PATH_DEPTH + 1):
        raise ValueError("archive path exceeds limits")


def _pax_quota(headers):
    if len(headers) > MAX_PAX_FIELDS or any(key.startswith("GNU.sparse.") for key in headers):
        raise ValueError("unsupported or excessive tar extension metadata")
    return sum(128 + len(key.encode("utf-8", "surrogatepass"))
               + len(value.encode("utf-8", "surrogatepass")) for key, value in headers.items())


def _check_pax_records(buffer, size):
    """Bound raw records before stdlib allocates tuples and decoded strings."""
    if len(buffer) != (size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE * tarfile.BLOCKSIZE:
        raise ValueError("truncated PAX record data")
    position = count = 0
    max_digits = len(str(MAX_TAR_HEADER_BYTES))
    while position < size:
        if buffer[position] == 0:
            if any(buffer[position:size]):
                raise ValueError("invalid PAX record padding")
            break
        space = buffer.find(b" ", position, min(size, position + max_digits + 1))
        if space < 0 or space == position or not buffer[position:space].isdigit():
            raise ValueError("invalid PAX record length")
        length = int(buffer[position:space])
        end = position + length
        if length < 5 or end > size or end <= space + 2 or buffer[end - 1] != 10:
            raise ValueError("invalid PAX record framing")
        equals = buffer.find(b"=", space + 1, end - 1)
        if equals <= space + 1:
            raise ValueError("invalid PAX record keyword")
        count += 1
        if count > MAX_PAX_FIELDS:
            raise ValueError("PAX record count exceeds limits")
        position = end


def _bounded_limit(value, maximum):
    if value is None:
        return maximum
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError("invalid archive parser limit")
    return value


class _BoundedTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        archive.header_count += 1
        if archive.header_count > archive.file_limit * 3 or not 0 <= self.size <= archive.payload_limit:
            raise ValueError("tar headers or payload exceed limits")
        _name_quota(self.name)
        _name_quota(self.linkname)
        _pax_quota(archive.pax_headers)
        extensions = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                      tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
        if self.type in extensions:
            if self.size > min(MAX_TAR_HEADER_BYTES, archive.metadata_limit):
                raise ValueError("tar extension header exceeds limits")
            archive.metadata_bytes += self.size
            if archive.metadata_bytes > archive.metadata_limit:
                raise ValueError("tar extension metadata exceeds limits")
        elif self.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.CONTTYPE,
                               tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE):
            raise ValueError("tar sparse and special files are forbidden")
        archive.header_depth += 1
        try:
            if archive.header_depth > MAX_TAR_HEADER_DEPTH:
                raise ValueError("tar extension nesting exceeds limits")
            result = super()._proc_member(archive)
            if not 0 <= result.size <= archive.payload_limit:
                raise ValueError("tar payload exceeds limits")
            _name_quota(result.name)
            _name_quota(result.linkname)
            _pax_quota(result.pax_headers)
            return result
        finally:
            archive.header_depth -= 1

    def _apply_pax_info(self, headers, encoding, errors):
        _pax_quota(headers)
        return super()._apply_pax_info(headers, encoding, errors)

    def _proc_pax(self, archive):
        position = archive.fileobj.tell()
        # The block is already bounded by _proc_member. Release this buffer
        # before delegating so nested headers cannot retain preflight copies.
        _check_pax_records(archive.fileobj.read(self._block(self.size)), self.size)
        archive.fileobj.seek(position)
        return super()._proc_pax(archive)

    def _proc_gnusparse_00(self, *args):
        raise ValueError("tar sparse files are forbidden")

    _proc_gnusparse_01 = _proc_gnusparse_00
    _proc_gnusparse_10 = _proc_gnusparse_00


class _BoundedTarFile(tarfile.TarFile):
    tarinfo = _BoundedTarInfo

    def __init__(self, *args, max_files=None, max_payload_bytes=None, max_metadata_bytes=None, **kwargs):
        self.file_limit = _bounded_limit(max_files, MAX_FILES)
        self.payload_limit = _bounded_limit(max_payload_bytes, MAX_PAYLOAD_BYTES)
        self.metadata_limit = _bounded_limit(max_metadata_bytes, MAX_METADATA_BYTES)
        self.header_count = self.header_depth = self.metadata_bytes = 0
        self.payload_bytes = self.member_count = self.applied_metadata_bytes = 0
        self.last_counted = None
        super().__init__(*args, **kwargs)

    def next(self):
        member = super().next()
        # TarFile.__init__ reads firstmember; next() replays that same object.
        if member is not None and member is not self.last_counted:
            self.last_counted = member
            self.member_count += 1
            self.payload_bytes += member.size
            self.applied_metadata_bytes += (128 + len(member.name.encode("utf-8", "surrogatepass"))
                                            + len(member.linkname.encode("utf-8", "surrogatepass"))
                                            + _pax_quota(member.pax_headers))
            if (self.member_count > self.file_limit or self.payload_bytes > self.payload_limit
                    or self.applied_metadata_bytes > self.metadata_limit):
                raise ValueError("tar inventory exceeds limits")
        return member


@contextmanager
def open_tar(name=None, mode="r:*", *, fileobj=None, max_files=None,
             max_payload_bytes=None, max_metadata_bytes=None):
    if mode not in {"r", "r:*", "r:", "r:gz", "r:xz", "r:bz2"}:
        raise ValueError("bounded tar opener is read-only")
    limits = {"max_files": _bounded_limit(max_files, MAX_FILES),
              "max_payload_bytes": _bounded_limit(max_payload_bytes, MAX_PAYLOAD_BYTES),
              "max_metadata_bytes": _bounded_limit(max_metadata_bytes, MAX_METADATA_BYTES)}
    with ExitStack() as stack:
        source = fileobj if fileobj is not None else stack.enter_context(open(name, "rb"))
        source = _LimitedSource(source, CHUNK_BYTES)
        start = source.tell()
        signature = source.read(6)
        source.seek(start)
        if mode in {"r", "r:*"}:
            mode = ("r:gz" if signature.startswith(b"\x1f\x8b") else
                    "r:xz" if signature.startswith(b"\xfd7zXZ\0") else
                    "r:bz2" if signature.startswith(b"BZh") else "r:")
        cached = None
        if mode == "r:":
            reader = source
        else:
            if mode == "r:gz":
                decoded = stack.enter_context(gzip.GzipFile(fileobj=source))
            elif mode == "r:xz":
                decoded = stack.enter_context(BoundedXZReader(source, memory_limit=MAX_XZ_MEMORY_BYTES))
            else:
                decoded = stack.enter_context(bz2.BZ2File(source))
            storage = stack.enter_context(tempfile.TemporaryFile())
            reader = cached = _CachedTarReader(decoded, storage)
        archive = stack.enter_context(_BoundedTarFile.open(mode="r:", fileobj=reader, **limits))
        yield archive
        if cached is not None:
            # Verify compression trailers before the surrounding installer publishes.
            cached.finish()


class _BoundedZipFile(zipfile.ZipFile):
    def _RealGetContents(self):
        # These stdlib metadata hooks are confined here and checked on each
        # supported Python version; extraction never uses private ZIP APIs.
        end = zipfile._EndRecData(self.fp)
        if not end:
            raise zipfile.BadZipFile("ZIP end record is missing")
        size, count = end[zipfile._ECD_SIZE], end[zipfile._ECD_ENTRIES_TOTAL]
        if size > MAX_ZIP_DIRECTORY_BYTES or count > MAX_FILES:
            raise ValueError("ZIP directory exceeds limits")
        if (end[zipfile._ECD_DISK_NUMBER] or end[zipfile._ECD_DISK_START]
                or end[zipfile._ECD_ENTRIES_THIS_DISK] != count):
            raise ValueError("multi-disk ZIP is unsupported")
        start = end[zipfile._ECD_LOCATION] - size
        if end[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:
            # Some stdlib security backports report the ZIP64 record location;
            # older versions retain the ordinary EOCD location. Determine the
            # contract from the actual signature, not a Python version number.
            self.fp.seek(end[zipfile._ECD_LOCATION])
            signature = self.fp.read(4)
            if signature == zipfile.stringEndArchive:
                start -= zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator
            elif signature != zipfile.stringEndArchive64:
                raise zipfile.BadZipFile("ZIP end record location is invalid")
        if start < 0:
            raise zipfile.BadZipFile("ZIP directory offset is invalid")
        self.fp.seek(start)
        consumed = actual_count = 0
        while consumed < size:
            header = self.fp.read(zipfile.sizeCentralDir)
            if len(header) != zipfile.sizeCentralDir:
                raise zipfile.BadZipFile("ZIP directory is truncated")
            fields = struct.unpack(zipfile.structCentralDir, header)
            if fields[zipfile._CD_SIGNATURE] != zipfile.stringCentralDir:
                raise zipfile.BadZipFile("ZIP directory signature is invalid")
            actual_count += 1
            if actual_count > MAX_FILES:
                raise ValueError("ZIP member count exceeds limits")
            if fields[zipfile._CD_FILENAME_LENGTH] > MAX_ARCHIVE_PATH_BYTES:
                raise ValueError("ZIP path exceeds limits")
            if fields[zipfile._CD_COMPRESS_TYPE] not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError("ZIP compression method is unsupported")
            if fields[zipfile._CD_FLAG_BITS] & 1:
                raise ValueError("encrypted ZIP is unsupported")
            variable = sum(fields[key] for key in (zipfile._CD_FILENAME_LENGTH,
                            zipfile._CD_EXTRA_FIELD_LENGTH, zipfile._CD_COMMENT_LENGTH))
            consumed += zipfile.sizeCentralDir + variable
            if consumed > size:
                raise zipfile.BadZipFile("ZIP directory entry exceeds its container")
            self.fp.seek(start + consumed)
        if actual_count != count:
            raise zipfile.BadZipFile("ZIP directory count is inconsistent")
        super()._RealGetContents()
        if sum(member.file_size for member in self.filelist) > MAX_PAYLOAD_BYTES:
            raise ValueError("ZIP payload exceeds limits")
        for member in self.filelist:
            _name_quota(member.filename)


@contextmanager
def open_zip(name=None, mode="r", *, fileobj=None):
    if mode != "r":
        raise ValueError("bounded ZIP opener is read-only")
    with ExitStack() as stack:
        source = fileobj if fileobj is not None else stack.enter_context(open(name, "rb"))
        reader = _LimitedSource(source, MAX_ZIP_DIRECTORY_BYTES)
        yield stack.enter_context(_BoundedZipFile(reader, mode="r"))
