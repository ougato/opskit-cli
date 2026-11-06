"""Compare menu enablement reads with synthetic plugins and an isolated config.

Inventory, plugin hashing, trust and rendering are stubbed. This measures the
configuration-dependent filtering stage, not the complete menu startup.
"""
import argparse
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import save_config
from plugin import commands


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugins", type=int, default=50)
    parser.add_argument("--runs", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.plugins <= 256 or not 1 <= args.runs <= 20:
        parser.error("plugins must be 1–256 and runs 1–20")

    with tempfile.TemporaryDirectory(prefix="opskit-menu-listing-") as temp:
        original_data = os.environ.get("OPSKIT_DATA_DIR")
        original_functions = commands.builtin_module_keys, commands.load_plugin
        try:
            os.environ["OPSKIT_DATA_DIR"] = temp
            items = [SimpleNamespace(name=f"plugin_{index}") for index in range(args.plugins)]
            save_config({"modules": {item.name: {"enabled": True, "description": "example" * 8}
                                     for item in items}})
            commands.builtin_module_keys = lambda: set()
            commands.load_plugin = lambda item: SimpleNamespace(order=50)

            def reference():
                return [(item, commands.load_plugin(item)) for item in items if commands.is_enabled(item.name)]

            operations = {"reference": reference, "current": lambda: commands._loaded_plugins(items)}
            samples = {name: [] for name in operations}
            for index in range(args.runs):
                order = ("reference", "current") if index % 2 == 0 else ("current", "reference")
                for name in order:
                    start = time.perf_counter()
                    result = operations[name]()
                    samples[name].append(time.perf_counter() - start)
                    if [item.name for item, _ in result] != [item.name for item in items]:
                        raise RuntimeError("plugin listing differs")
            print(f"plugins={args.plugins} runs={args.runs}; actual config, stubbed discovery and hashing")
            for name, values in samples.items():
                print(f"{name} seconds: {', '.join(f'{value:.4f}' for value in values)}")
                print(f"{name} median: {statistics.median(values):.4f}s")
            print("OS cache and other host load are not controlled.")
        finally:
            commands.builtin_module_keys, commands.load_plugin = original_functions
            if original_data is None:
                os.environ.pop("OPSKIT_DATA_DIR", None)
            else:
                os.environ["OPSKIT_DATA_DIR"] = original_data


if __name__ == "__main__":
    main()
