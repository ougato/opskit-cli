"""Explicit guest SDK code inventory, shared by preparation and execution."""
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
import zipfile

RUNTIME_POLICY = 9
BUNDLE_NAME = "plugin-runtime-sources.zip"
MAX_SOURCE_BYTES = 4 * 1024**2
MAX_CONTEXT_SOURCE_BYTES = 24 * 1024**2
RUNTIME_FILES = (
    "__init__.py", "bounded_yaml.py", "cli.py", "config.py", "constants.py", "i18n.py", "logger.py",
    "module.py", "paths.py", "plugin_json.py", "plugin_tree.py", "plugin_worker.py", "process.py",
    "prompt.py", "resource_lock.py", "runner.py", "sdk.py", "secrets.py", "theme.py", "yamlio.py",
    "windows_console_input.py", "windows_job.py", "windows_job_lifecycle.py",
    "locale/en.yaml", "locale/zh.yaml", "themes/catppuccin.yaml",
)
NATIVE_FETCH_FILES = ("__init__.py", "native_fetch_worker.py", "plugin_git_archive.py")
BUNDLED_FILES = tuple(dict.fromkeys((*RUNTIME_FILES, *NATIVE_FETCH_FILES)))


def _read_file(source, core, limit):
    for parent in (source, *source.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("linked runtime code is forbidden")
        if parent == core:
            break
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
        raise ValueError("runtime code must be bounded ordinary files")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        current = os.fstat(stream.fileno())
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_size > limit
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("runtime code changed during snapshot")
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(content) != current.st_size or (after.st_size, after.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
        raise ValueError("runtime code changed during snapshot")
    return content


def _bundle_snapshot(core):
    from core.archive_reader import open_zip
    content = _read_file(core / BUNDLE_NAME, core, MAX_CONTEXT_SOURCE_BYTES)
    try:
        with open_zip(fileobj=io.BytesIO(content)) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            fixed = {"core/" + name for name in BUNDLED_FILES}
            extra = names - fixed
            valid_extra = (1 <= len(extra) <= 128 and all(
                name.startswith("core/dulwich/") and name.endswith(".py")
                and ".." not in Path(name).parts for name in extra))
            if (len(members) != len(names) or not fixed <= names or extra and not valid_extra
                    or not any(name == "core/dulwich/__init__.py" for name in extra)
                    or any(member.file_size > MAX_SOURCE_BYTES for member in members)
                    or sum(member.file_size for member in members) > MAX_CONTEXT_SOURCE_BYTES):
                raise ValueError("invalid packaged runtime source inventory")
            return {member.filename: archive.read(member) for member in members}
    except zipfile.BadZipFile as error:
        raise ValueError("invalid packaged runtime source archive") from error


def _snapshot(core, names):
    core = Path(core or Path(__file__).parent).absolute()
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        files = _bundle_snapshot(core)
        result = {"core/" + name: files["core/" + name] for name in names}
        if "native_fetch_worker.py" in names:
            result.update({name: body for name, body in files.items() if name.startswith("core/dulwich/")})
        return result
    result, size = {}, 0
    for name in names:
        content = _read_file(core / name, core, MAX_SOURCE_BYTES)
        size += len(content)
        if size > MAX_CONTEXT_SOURCE_BYTES:
            raise ValueError("runtime source inventory exceeds limits")
        result["core/" + name] = content
    if "native_fetch_worker.py" in names:
        try:
            import dulwich
        except ImportError as error:
            from core.logger import get_logger
            from core.native_sandbox import NativeSandboxError
            get_logger("opskit.plugin.bootstrap").debug("Fetch source dependency unavailable: %s", type(error).__name__)
            raise NativeSandboxError("fetch_dependency_missing") from error
        package = Path(dulwich.__file__).parent
        for source in package.rglob("*.py"):
            if "tests" in source.relative_to(package).parts:
                continue
            name = "dulwich/" + source.relative_to(package).as_posix()
            content = _read_file(source, package, MAX_SOURCE_BYTES)
            size += len(content)
            if size > MAX_CONTEXT_SOURCE_BYTES:
                raise ValueError("runtime source inventory exceeds limits")
            result["core/" + name] = content
        if "core/dulwich/__init__.py" not in result:
            raise ValueError("Dulwich pure Python sources are unavailable")
    return result


def snapshot(core=None):
    """Only guest SDK files; host network brokers never enter the sandbox."""
    return _snapshot(core, RUNTIME_FILES)


def native_fetch_snapshot(core=None):
    """Fixed native Git object reader, including frozen distributions."""
    return _snapshot(core, NATIVE_FETCH_FILES)


def bundle_snapshot(core=None):
    """Ship both explicit inventories; execution selects its own inventory."""
    return _snapshot(core, BUNDLED_FILES)


def code_digest(files=None):
    digest = hashlib.sha256()
    for name, content in sorted((snapshot() if files is None else files).items()):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()
