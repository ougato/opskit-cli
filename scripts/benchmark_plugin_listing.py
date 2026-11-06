"""Compare menu configuration and trust reads in an isolated data directory.

Inventory, plugin hashing and rendering are stubbed. Configuration filtering
stubs plugin validation; trust-store lookups are measured separately. This
does not measure complete menu startup.
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
from core import plugin_trust
from plugin import commands
import yaml


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
            fingerprint = "a" * 64
            records = {item.name: {"fingerprint": fingerprint, "version": "1.0.0"} for item in items}
            Path(temp, "plugin_trust.yaml").write_text(yaml.safe_dump(records), encoding="utf-8")
            commands.builtin_module_keys = lambda: set()
            commands.load_plugin = lambda item: SimpleNamespace(order=50)

            def reference_config():
                return [(item, commands.load_plugin(item)) for item in items if commands.is_enabled(item.name)]

            def reference_trust():
                return [item.name for item in items if plugin_trust.is_trusted(item.name, fingerprint)]

            def current_trust():
                with plugin_trust.authorization_snapshot():
                    return reference_trust()

            operations = {"reference config": lambda: [item.name for item, _ in reference_config()],
                          "current config": lambda: [item.name for item, _ in commands._loaded_plugins(items)],
                          "reference trust": reference_trust, "current trust": current_trust}
            samples = {name: [] for name in operations}
            for index in range(args.runs):
                order = list(operations) if index % 2 == 0 else list(reversed(operations))
                for name in order:
                    start = time.perf_counter()
                    result = operations[name]()
                    samples[name].append(time.perf_counter() - start)
                    if result != [item.name for item in items]:
                        raise RuntimeError("plugin listing differs")
            print(f"plugins={args.plugins} runs={args.runs}; actual stores, stubbed discovery and hashing")
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
