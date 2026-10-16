"""Bounded exact-byte inventory for destructive installation rollback."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from core.plugin_tree import bounded_paths


def _version(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _inventory(root):
    root = Path(root)
    before = root.lstat()
    if not stat.S_ISDIR(before.st_mode) or getattr(before, 'st_file_attributes', 0) & 0x400:
        raise ValueError('invalid installation directory')
    digest = hashlib.sha256()
    remaining = 128 * 1024**2
    # Include .git, ignored files and empty directories: execution fingerprints
    # intentionally omit them, but rollback must preserve developer changes.
    for path in sorted(bounded_paths(root, max_entries=8192, skip_dirs=frozenset())):
        initial = path.lstat()
        if getattr(initial, 'st_file_attributes', 0) & 0x400:
            raise ValueError('linked installation entry')
        record = [path.relative_to(root).as_posix(), initial.st_mode]
        if stat.S_ISDIR(initial.st_mode):
            record.append('directory')
        elif stat.S_ISREG(initial.st_mode):
            content = hashlib.sha256()
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                # Windows Python 3.12 reports ctime differently through fstat
                # and lstat. Compare common fields across APIs, then compare
                # each API's own complete version before and after reading.
                if _version(opened)[:-1] != _version(initial)[:-1]:
                    raise ValueError('installation file changed while opening')
                while True:
                    chunk = stream.read(min(65536, remaining + 1))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError('installation receipt byte limit exceeded')
                    content.update(chunk)
                if _version(os.fstat(stream.fileno())) != _version(opened):
                    raise ValueError('installation file changed while reading')
            record.extend(['file', initial.st_size, content.hexdigest()])
        else:
            raise ValueError('installation entry is not an ordinary file')
        if _version(path.lstat()) != _version(initial):
            raise ValueError('installation entry changed during inventory')
        digest.update(json.dumps(record, ensure_ascii=True, separators=(',', ':')).encode('ascii'))
        digest.update(b'\n')
    if _version(root.lstat()) != _version(before):
        raise ValueError('installation directory changed during inventory')
    return digest.hexdigest()


def _worker(connection, root):
    from network.worker import send
    try:
        try:
            value = {'digest': _inventory(root)}
        except (OSError, ValueError, RuntimeError):
            value = {'error': 'installation content inspection failed'}
        send(connection, value)
    finally:
        connection.close()


def content_receipt(root):
    from network.worker import run
    result = run(_worker, (str(root),), timeout=30, label='installation rollback receipt')
    if (not isinstance(result, dict) or set(result) != {'digest'}
            or not isinstance(result['digest'], str) or not re.fullmatch('[0-9a-f]{64}', result['digest'])):
        raise ValueError('installation content inspection failed; rollback refused')
    return result['digest']
