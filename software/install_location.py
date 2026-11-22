"""Resolve the selected development tool's managed installation root."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import stat

from core.i18n import t
from software._shared.snapshot import SnapshotStore
from software.base import InstallError


DEVELOPMENT_TOOLS = frozenset({"golang", "java", "nodejs", "python"})
_selected: ContextVar[tuple[str, Path] | None] = ContextVar("software_install_location", default=None)


def managed_root(key: str, default: Path, snapshot: dict) -> Path:
    selected = _selected.get()
    if selected is not None and selected[0] == key:
        return selected[1]
    location = snapshot.get("install_location")
    return Path(location) if location else default


def record_location(key: str, snapshot: dict) -> dict:
    selected = _selected.get()
    if selected is not None and selected[0] == key:
        return {**snapshot, "install_location": str(selected[1])}
    return snapshot


def _pending_location(key: str) -> SnapshotStore:
    return SnapshotStore(".opskit/snapshots", f"{key}-pending-install-location.json")


def _location_identity(path: Path) -> dict:
    metadata = path.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_reparse_tag", 0)
            or path.resolve() != path):
        raise InstallError(t("software.install_location_invalid"))
    return {"install_location": str(path), "device": metadata.st_dev, "inode": metadata.st_ino}


def _validate_new_location(key: str, path: Path) -> None:
    if path.exists():
        identity = _location_identity(path)
        if any(path.iterdir()) and (not identity["inode"] or _pending_location(key).load() != identity):
            raise InstallError(t("software.install_location_not_empty"))


def remember_pending_location(key: str) -> None:
    selected = _selected.get()
    if selected is None or selected[0] != key:
        return
    path = selected[1]
    path.mkdir(parents=True, exist_ok=True)
    _validate_new_location(key, path)
    _pending_location(key).save(_location_identity(path))


def clear_pending_location(key: str) -> None:
    selected = _selected.get()
    if selected is not None and selected[0] == key:
        _pending_location(key).delete()


def parse_location(value: str) -> Path:
    try:
        path = Path(value).expanduser()
        if not path.is_absolute() or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value):
            raise ValueError
        path = path.resolve()
        if path == Path(path.anchor) or (path.exists() and not path.is_dir()):
            raise ValueError
    except (OSError, ValueError, RuntimeError):
        raise ValueError(t("software.install_location_invalid")) from None
    return path


@contextmanager
def select_location(instance, value: str | None):
    if value is None:
        yield
        return
    if instance.key not in DEVELOPMENT_TOOLS:
        raise InstallError(t("software.install_location_unsupported"))
    path = parse_location(value)

    from software._shared.mutation import versioned_mutation_scope

    with versioned_mutation_scope():
        current = instance.disk_check_path().resolve()
        if path == current:
            yield
            return
        if instance.installed_versions():
            raise InstallError(t("software.install_location_in_use", path=current))
        _validate_new_location(instance.key, path)
        token = _selected.set((instance.key, path))
        try:
            yield
        finally:
            _selected.reset(token)
