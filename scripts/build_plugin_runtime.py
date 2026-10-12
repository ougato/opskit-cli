"""Source checkout wrapper for the generic isolated runtime builder."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.plugin_runtime import build

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin", type=Path)
    parser.add_argument("--dependency", action="append", default=[])
    parser.add_argument("--base-image", default="python:3.12-slim")
    args = parser.parse_args()
    print(build(args.plugin.resolve(), args.dependency, args.base_image))
