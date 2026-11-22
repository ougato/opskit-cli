"""Resolve the selected development tool's managed installation root."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from core.i18n import t
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
        if path.exists() and any(path.iterdir()):
            raise InstallError(t("software.install_location_not_empty"))
        token = _selected.set((instance.key, path))
        try:
            yield
        finally:
            _selected.reset(token)
