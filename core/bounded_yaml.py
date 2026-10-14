"""Bound YAML authority and metadata reads before document construction."""
import os
import stat

import yaml

MAX_BYTES = 1024 * 1024


def load_bounded_yaml(path, *, max_bytes=MAX_BYTES):
    _validate_limit(max_bytes)
    before = path.lstat()
    if before.st_size > max_bytes:
        raise ValueError("YAML size limit exceeded")
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or any(component.is_symlink() or getattr(component, "is_junction", lambda: False)()
                   for component in (path, *path.parents))):
        raise ValueError("YAML must be a bounded ordinary file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        current = os.fstat(stream.fileno())
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                or current.st_size > max_bytes
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("YAML changed during read")
        raw = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > max_bytes:
        raise ValueError("YAML byte limit exceeded")
    if (len(raw) != current.st_size
            or (after.st_size, after.st_mtime_ns) != (current.st_size, current.st_mtime_ns)):
        raise ValueError("YAML changed during read")
    return parse_bounded_yaml(raw, max_bytes=max_bytes)


def _validate_limit(max_bytes):
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError("YAML size limit must be a positive integer at most 1MiB")


def parse_bounded_yaml(raw, *, max_bytes=MAX_BYTES):
    """Validate structure before constructing metadata or authority records."""
    _validate_limit(max_bytes)
    if len(raw) > max_bytes:
        raise ValueError("YAML byte limit exceeded")
    text = raw.decode("utf-8")
    depth = 0
    for count, event in enumerate(yaml.parse(text), 1):
        if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
            depth += 1
        elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
            depth -= 1
        if count > 20000 or depth > 64 or isinstance(event, yaml.events.AliasEvent):
            raise ValueError("YAML structure limit exceeded")
    data = yaml.safe_load(text)
    return {} if data is None else data
