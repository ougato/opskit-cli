"""Resolve required install targets before checking their filesystems."""


def known_path_or_none(resolve):
    """Keep other paths checkable when one target cannot be resolved."""
    try:
        return resolve()
    except (OSError, ValueError):
        return None


def check_recipe_disk_space(instance, required_bytes: int) -> bool | None:
    """Return False if any required target is low, None if any is unknown."""
    get_paths = getattr(instance, "disk_check_paths", None)
    get_path = getattr(instance, "disk_check_path", None)
    if not callable(get_paths) and not callable(get_path):
        return None
    try:
        paths = get_paths() if callable(get_paths) else (get_path(),)
    except (OSError, ValueError):
        return None
    if not paths:
        return None
    from core.platform import check_disk_space
    unknown = False
    for path in paths:
        if path is None:
            unknown = True
            continue
        result = check_disk_space(required_bytes, path)
        if result is False:
            return False
        if result is None:
            unknown = True
    return None if unknown else True
