"""Bounded runtime identity records, read only by the owned host reader."""
import json
import os
from pathlib import Path
import re
import stat

from core.plugin_lock import CATALOG_LIMIT, valid_base_reference


def validate_record(record):
    """Return only execution identity fields; never forward arbitrary metadata."""
    if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
            or record["schema_version"] != 2
            or not valid_base_reference(record.get("base_reference"))
            or any(not isinstance(record.get(key), str)
                   or re.fullmatch(r"[0-9a-f]{64}", record[key]) is None
                   for key in ("requirements_sha256", "code_sha256", "fingerprint"))
            or not isinstance(record.get("image"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", record["image"]) is None):
        raise ValueError("invalid runtime catalog identity")
    return {key: record[key] for key in ("schema_version", "base_reference", "requirements_sha256",
                                        "code_sha256", "fingerprint", "image")}


def read_record(path):
    from core.plugin_runtime_inventory import code_digest
    path = Path(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > CATALOG_LIMIT
            or getattr(path, "is_junction", lambda: False)()):
        raise ValueError("invalid runtime catalog")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("runtime catalog changed")
        content = source.read(CATALOG_LIMIT + 1)
        after = os.fstat(source.fileno())
    if (len(content) > CATALOG_LIMIT or len(content) != info.st_size
            or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns)):
        raise ValueError("runtime catalog changed")
    record = validate_record(json.loads(content))
    if record["code_sha256"] != code_digest():
        raise ValueError("runtime SDK inventory changed")
    return record


def validate_gateway_record(record):
    from core.plugin_gateway import BASE_REFERENCE, POLICY
    if (not isinstance(record, dict)
            or set(record) != {"schema_version", "image", "base_reference", "code_sha256"}
            or type(record["schema_version"]) is not int or record["schema_version"] != POLICY
            or record["base_reference"] != BASE_REFERENCE
            or not isinstance(record["code_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", record["code_sha256"]) is None
            or not isinstance(record["image"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", record["image"]) is None):
        raise ValueError("invalid gateway catalog identity")
    return record


def read_gateway_record(path):
    import hashlib
    from core.bounded_yaml import load_bounded_yaml
    from core.plugin_runtime_inventory import gateway_source
    record = validate_gateway_record(load_bounded_yaml(Path(path), max_bytes=CATALOG_LIMIT))
    if record["code_sha256"] != hashlib.sha256(gateway_source()).hexdigest():
        raise ValueError("gateway source changed")
    return record
