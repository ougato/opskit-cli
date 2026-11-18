"""Measure the real plugin menu path without driving the user's terminal.

The first plugin entry is chosen after the requested parent-menu dwell time;
its first rendered submenu and the parent menu are then exited automatically.
"""
import argparse
from pathlib import Path
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--wait", type=float, default=10)
    parser.add_argument("--no-preload", action="store_true", help="measure the ordinary entry path")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--language", choices=("zh", "en"), help="override the interface language")
    parser.add_argument("--expect-label", help="require option 2 to contain this label")
    parser.add_argument("--root-wait", type=float, help="also exercise the main menu, waiting this many seconds")
    args = parser.parse_args()
    if (not 1 <= args.runs <= 20 or not 0 <= args.wait <= 60
            or args.root_wait is not None and not 0 <= args.root_wait <= 60):
        parser.error("runs must be 1–20 and wait must be 0–60 seconds")

    from core import i18n, prompt, theme
    from core import plugin_entry_preload
    from plugin import menu
    i18n.init(language=args.language)
    theme.init()
    original_main_spec = sys.modules["__main__"].__spec__
    sys.modules["__main__"].__file__ = str(ROOT / "main.py")
    from core.worker_bootstrap import configure_main_spawn
    configure_main_spawn()
    original_select = prompt.select
    original_read = prompt._read_key_seq
    original_menu_select = menu.select
    original_start = plugin_entry_preload.start
    original_discovery = plugin_entry_preload.start_discovery
    if args.no_preload:
        plugin_entry_preload.start = lambda manifest: None
        plugin_entry_preload.start_discovery = lambda: None
    samples = []
    index = 0
    selected = 0.0
    menu_times = []
    profiles = []
    profile = None
    host = None
    originals = None
    if args.root_wait is not None:
        import main as host
        from core.module import ModuleInfo
        originals = (host.discover_modules, host.select, host._start_background_workers, host._on_exit)
        host.discover_modules = lambda cfg: [ModuleInfo(key="plugin", description_key="", order=1, entry=menu.entry)]
        host._start_background_workers = lambda cfg: None
        host._on_exit = lambda cfg: None

    def read_key():
        nonlocal index, selected
        index += 1
        if args.root_wait is not None and index == 1:
            time.sleep(args.root_wait)
            return "1"
        if index == (2 if args.root_wait is not None else 1):
            time.sleep(args.wait)
            selected = time.perf_counter()
            if profile is not None:
                profile.enable()
            return "2"
        return "0"

    def select(*select_args, **select_kwargs):
        existing = select_kwargs.get("on_ready")
        breadcrumb = select_kwargs.get("breadcrumb", select_args[0] if select_args else [])
        choices = select_kwargs.get("choices", select_args[2] if len(select_args) > 2 else [])
        parent_menu = len(breadcrumb) == 2 and breadcrumb[1] == i18n.t("menu.plugin")
        if parent_menu and not selected:
            first = next((item for item in choices if item.get("key") == "2"), None)
            if first is None or args.expect_label and args.expect_label not in first.get("label", ""):
                raise RuntimeError("option 2 does not match the expected plugin")
        def ready():
            if selected and not menu_times and not parent_menu and len(breadcrumb) >= 2:
                menu_times.append(time.perf_counter() - selected)
                if profile is not None:
                    profile.disable()
            if existing is not None:
                existing()
        select_kwargs["on_ready"] = ready
        return original_select(*select_args, **select_kwargs)

    prompt._read_key_seq = read_key
    prompt.select = menu.select = select
    if host is not None:
        host.select = select
    try:
        for run in range(args.runs):
            index = 0
            selected = 0
            menu_times.clear()
            if args.profile:
                import cProfile
                profile = cProfile.Profile()
                profiles.append(profile)
            if host is None:
                menu.entry()
            else:
                host._main_menu({})
            if len(menu_times) != 1:
                raise RuntimeError("plugin submenu was not rendered")
            samples.append(menu_times[0])
            print(f"run {run + 1}: {samples[-1]:.3f} s", flush=True)
    finally:
        prompt._read_key_seq = original_read
        prompt.select = original_select
        menu.select = original_menu_select
        plugin_entry_preload.start = original_start
        plugin_entry_preload.start_discovery = original_discovery
        sys.modules["__main__"].__spec__ = original_main_spec
        if host is not None:
            host.discover_modules, host.select, host._start_background_workers, host._on_exit = originals
    print(f"median: {statistics.median(samples):.3f} s")
    if profiles:
        import pstats
        for item in profiles:
            pstats.Stats(item).strip_dirs().sort_stats("cumulative").print_stats(35)


if __name__ == "__main__":
    main()
