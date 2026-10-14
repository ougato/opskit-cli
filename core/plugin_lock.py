"""Restricted, bounded hashed-wheel requirements chosen by the operator."""
import os
import re
import stat
from pathlib import Path

LOCK_LIMIT = 1024 * 1024
MAX_PACKAGES = 512
MAX_HASHES = 256
RUNTIME_PACKAGES = frozenset({"rich", "pyyaml", "typer", "platformdirs"})
_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
BASE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}")
BASE_REFERENCE_LIMIT = 4096
CATALOG_LIMIT = 16384


def valid_base_reference(value):
    return isinstance(value, str) and len(value) <= BASE_REFERENCE_LIMIT and bool(BASE_REFERENCE.fullmatch(value))


def package_key(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text):
    """No URLs, includes, index flags, extras or executable source builds."""
    packages = {}
    pending = ""
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            if len(pending) > 32768:
                raise ValueError("dependency lock entry too large")
            continue
        if len(pending) + len(line) > 32768:
            raise ValueError("dependency lock entry too large")
        tokens = (pending + line).split()
        pending = ""
        pin = _PIN.fullmatch(tokens[0])
        hashes = [_HASH.fullmatch(value) for value in tokens[1:]]
        if pin is None or not 1 <= len(hashes) <= MAX_HASHES or any(value is None for value in hashes):
            raise ValueError("lock requires exact versions and SHA256 wheel hashes")
        key = package_key(pin[1])
        if key in packages or len(packages) >= MAX_PACKAGES:
            raise ValueError("duplicate or excessive dependency lock packages")
        packages[key] = (pin[2], sorted({value[1] for value in hashes}))
    if pending or not packages:
        raise ValueError("empty or unfinished dependency lock")
    canonical = "".join(name + "==" + version + " " + " ".join("--hash=sha256:" + value for value in hashes) + "\n"
                        for name, (version, hashes) in sorted(packages.items()))
    return canonical, packages


def read_lock(path, dependencies):
    if path is None:
        raise ValueError("operator dependency lock is required")
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("linked dependency locks forbidden")
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("dependency lock file not found") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > LOCK_LIMIT:
        raise ValueError("dependency lock must be a bounded ordinary file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LOCK_LIMIT
                or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("dependency lock must be a bounded ordinary file")
        content = source.read(LOCK_LIMIT + 1)
    if len(content) > LOCK_LIMIT:
        raise ValueError("dependency lock too large")
    try:
        canonical, packages = parse_lock(content.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("dependency lock must use UTF-8") from exc
    if not RUNTIME_PACKAGES.issubset(packages):
        raise ValueError("dependency lock is missing SDK runtime packages")
    if "pip" in packages:
        raise ValueError("pip is a platform-owned build tool, not a plugin dependency")
    for dependency in dependencies:
        pin = _PIN.fullmatch(dependency)
        if pin is None or package_key(pin[1]) not in packages or packages[package_key(pin[1])][0] != pin[2]:
            raise ValueError("requested dependency does not match the lock")
    return canonical
