"""Bounded ELF metadata reads; never invoke a downloaded executable or loader."""
import os
import re
import stat
import struct


def dependencies(path, remaining):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 512 * 1024**2:
            raise ValueError('invalid ELF file')

        def read(offset, size):
            if offset < 0 or size < 0 or offset + size > info.st_size or size > remaining[0]:
                raise ValueError('ELF metadata exceeds bounds')
            remaining[0] -= size
            stream.seek(offset)
            data = stream.read(size)
            if len(data) != size:
                raise ValueError('truncated ELF metadata')
            return data

        header = struct.unpack('<16sHHIQQQIHHHHHH', read(0, 64))
        ident, kind, machine, version, _, offset, _, _, header_size, entry_size, count, *_ = header
        if (ident[:7] != b'\x7fELF\x02\x01\x01' or kind not in (2, 3) or machine not in (62, 183)
                or version != 1 or header_size != 64 or entry_size != 56 or not 1 <= count <= 1024):
            raise ValueError('unsupported ELF metadata')
        programs = list(struct.iter_unpack('<IIQQQQQQ', read(offset, count * entry_size)))
        loads = [entry for entry in programs if entry[0] == 1]
        dynamic = [entry for entry in programs if entry[0] == 2]
        if not loads or len(dynamic) > 1 or any(entry[2] + entry[5] > info.st_size or entry[5] > entry[6] for entry in loads):
            raise ValueError('invalid ELF segments')
        if not dynamic:
            return machine, set(), []
        segment = dynamic[0]
        if not 16 <= segment[5] <= 65536 or segment[5] % 16:
            raise ValueError('invalid ELF dynamic table')
        if not any(entry[2] <= segment[2] and segment[2] + segment[5] <= entry[2] + entry[5]
                   and segment[3] == entry[3] + segment[2] - entry[2] for entry in loads):
            raise ValueError('ELF dynamic table is not file-backed')
        table = list(struct.iter_unpack('<qQ', read(segment[2], segment[5])))
        entries = []
        for tag, value in table:
            if tag == 0:
                break
            entries.append((tag, value))
        else:
            raise ValueError('unterminated ELF dynamic table')
        strings = [entry for entry in entries if entry[0] in (1, 15, 29)]
        if not strings:
            return machine, set(), []
        pointers = [value for tag, value in entries if tag == 5]
        sizes = [value for tag, value in entries if tag == 10]
        if len(pointers) != 1 or len(sizes) != 1 or not 0 < sizes[0] <= 8 * 1024**2:
            raise ValueError('invalid ELF string table')
        matches = [entry for entry in loads if entry[3] <= pointers[0] and pointers[0] + sizes[0] <= entry[3] + entry[5]]
        if len(matches) != 1:
            raise ValueError('ELF string table is not file-backed')
        data = read(matches[0][2] + pointers[0] - matches[0][3], sizes[0])
        needed, paths = set(), {15: [], 29: []}
        for tag, value in strings:
            end = data.find(b'\0', value, min(len(data), value + 4097))
            if end < 0:
                raise ValueError('invalid ELF string')
            text = data[value:end].decode('ascii')
            if tag == 1:
                if not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,255}', text):
                    raise ValueError('invalid ELF soname')
                needed.add(text)
            else:
                if paths[tag]:
                    raise ValueError('duplicate ELF library search path')
                paths[tag] = text.split(':')
        search = paths[29] if any(tag == 29 for tag, _ in strings) else paths[15]
        if len(needed) > 512 or len(search) > 64:
            raise ValueError('ELF dependency metadata exceeds quota')
        return machine, needed, search


def runtime_requirements(root, budget, machine):
    root = root.resolve()
    pending = [root / 'bin' / name for name in ('mysql', 'mysqld', 'mysqladmin', 'mysqldump')]
    seen, required = set(), set()
    remaining = [32 * 1024**2]
    while pending:
        budget.remaining()
        path = pending.pop().resolve()
        if not path.is_relative_to(root):
            raise ValueError('runtime library escapes owned installation')
        if path in seen:
            continue
        seen.add(path)
        if len(seen) > 256:
            raise ValueError('runtime dependency graph exceeds quota')
        actual, needed, search = budget.call(dependencies, path, remaining)
        if actual != machine:
            raise ValueError('runtime binary architecture does not match host')
        directories = []
        for value in search:
            budget.remaining()
            if value in ('$ORIGIN', '${ORIGIN}') or value.startswith(('$ORIGIN/', '${ORIGIN}/')):
                expanded = value.replace('${ORIGIN}', str(path.parent)).replace('$ORIGIN', str(path.parent))
                directory = type(path)(expanded).resolve()
                if directory.is_relative_to(root):
                    directories.append(directory)
        for name in needed:
            budget.remaining()
            bundled = next((directory / name for directory in directories if (directory / name).is_file()), None)
            if bundled is None:
                required.add(name)
                if len(required) > 256:
                    raise ValueError('system dependency graph exceeds quota')
            else:
                pending.append(bundled)
    return required
