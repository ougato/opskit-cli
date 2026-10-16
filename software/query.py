"""Read-only official software inventory and capability descriptions."""
from software.registry import all_recipes, get
from core.platform import get_platform


def capabilities(cls):
    wizard = bool(cls.has_wizard)
    config_install = cls.key in {"ohmyzsh", "tailscale", "xui", "wg_server"}
    return {"name": cls.key, "platforms": cls.platforms, "category": cls.category,
            "requires_root": cls.requires_root, "group": cls.has_submenu,
            "install_version": cls.has_install_version_selection and not wizard and not cls.has_submenu,
            "upgrade": cls.has_upgrade, "switch": cls.has_switch,
            "diagnose": cls.has_diagnose, "manage": cls.has_manage,
            "install_mode": "group" if cls.has_submenu else "token" if cls.key == "wg_client" else "config" if config_install else "interactive_wizard" if wizard else "version" if cls.has_install_version_selection else "system",
            "scriptable_install": not cls.has_submenu and (not wizard or cls.key == "wg_client" or config_install)}


def inventory(query="", installed_only=False, category="", platform=""):
    platform = platform or get_platform().os_type
    keyword = query.strip().lower()
    rows = []
    for cls in sorted(all_recipes(), key=lambda item: item.key):
        if platform not in cls.platforms or category and cls.category != category:
            continue
        if cls.hidden and not installed_only:
            continue
        if keyword and keyword not in (cls.key + " " + cls.description).lower():
            continue
        info = capabilities(cls)
        # Cross-platform listing must not run target-platform detectors on this host.
        if platform != get_platform().os_type:
            info.update(installed=None, version=None, installed_versions=[], detection_error=None)
        else:
            try:
                instance = cls()
                version = instance.detect()
                versions = instance.installed_versions() if hasattr(instance, "installed_versions") else ([version] if version else [])
                info.update(installed=bool(version), version=version, installed_versions=versions, detection_error=None)
            except Exception as exc:
                info.update(installed=None, version=None, installed_versions=[], detection_error=type(exc).__name__)
        if not installed_only or info["installed"]:
            rows.append(info)
    return rows


def register(app):
    from core.cli import emit, fail, guarded

    @app.command("capabilities")
    @guarded
    def show_capabilities(name: str):
        cls = get(name.lower())
        if cls is None:
            fail("unknown official software", 2)
        emit(capabilities(cls))
