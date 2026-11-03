"""Host-owned logical disk policy. Checks are not filesystem hard quotas."""
from pathlib import Path
import shutil

from core.config import configuration_transaction, load_config, set_config_value
from core.i18n import t

DEFAULTS = {"minimum_free_mib": 2048, "creator_output_directory": "", "keep_builds": 5}


def policy():
    plugin = load_config().get("plugin", {})
    if not isinstance(plugin, dict):
        raise ValueError(t("plugin_native.disk.invalid"))
    value = plugin.get("disk", {})
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError(t("plugin_native.disk.invalid"))
    return validate({**DEFAULTS, **value})


def validate(value):
    if (type(value["minimum_free_mib"]) is not int or not 256 <= value["minimum_free_mib"] <= 1048576
            or type(value["keep_builds"]) is not int or not 1 <= value["keep_builds"] <= 100):
        raise ValueError(t("plugin_native.disk.invalid"))
    directory = value["creator_output_directory"]
    if not isinstance(directory, str):
        raise ValueError(t("plugin_native.disk.invalid"))
    if directory:
        from core.plugin_host_tools import _ordinary
        value = dict(value, creator_output_directory=str(_ordinary(directory, directory=True)))
        if ";" in directory:
            raise ValueError(t("plugin_native.disk.invalid"))
    return dict(value)


def configure(minimum_free_mib, creator_output_directory, keep_builds):
    value = validate(dict(minimum_free_mib=minimum_free_mib,
                          creator_output_directory=creator_output_directory, keep_builds=keep_builds))
    with configuration_transaction():
        set_config_value({}, "plugin.disk", value)
    return value


def check_space(path, *, reserve_bytes=0):
    """Check the actual destination volume; never claim to reserve its free bytes."""
    target = Path(path).absolute()
    while not target.exists():
        if target == target.parent:
            raise ValueError(t("plugin_native.disk.invalid"))
        target = target.parent
    from core.plugin_state import _directory
    for parent in (target, *target.parents):
        _directory(parent)
    minimum = policy()["minimum_free_mib"] * 1024**2
    free = shutil.disk_usage(target).free
    if free < minimum + reserve_bytes:
        raise OSError(t("plugin_native.disk.low_space", path=str(target),
                        required=(minimum + reserve_bytes + 1024**2 - 1) // 1024**2,
                        available=free // 1024**2))
    return {"directory": str(target), "free_bytes": free, "minimum_free_bytes": minimum,
            "reserve_bytes": reserve_bytes, "hard_quota": False}
