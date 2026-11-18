"""Exercise actual CLI inventory limits using owned synthetic directories."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def verify(output):
    output.mkdir(parents=True, exist_ok=False)
    plugins = output / "plugins"
    plugins.mkdir()
    env = dict(os.environ, OPSKIT_DATA_DIR=str(output / "state"), OPSKIT_PLUGINS_DIR=str(plugins),
               OPSKIT_DISABLE_AUTO_UPDATE="1", PYTHONIOENCODING="utf-8")
    results = []
    def call(arguments, expected, message=None):
        result = subprocess.run([sys.executable, "-m", "main", "--output", "json", *arguments],
                                cwd=Path(__file__).resolve().parents[1], env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        if result.returncode != expected:
            raise RuntimeError("inventory CLI exit mismatch")
        if message and json.loads(result.stdout)["error"]["message"] != message:
            raise RuntimeError("inventory CLI error mismatch")
        results.append({"arguments": arguments, "exit_code": result.returncode})

    for index in range(4097):
        (plugins / (".entry-" + str(index))).touch()
    call(["plugin", "list"], 1, "plugin inventory entry limit exceeded")
    call(["plugin", "run", "demo", "--", "--help"], 1, "plugin inventory entry limit exceeded")
    call(["software", "--help"], 0)
    for path in plugins.iterdir():
        path.unlink()
    for index in range(257):
        root = plugins / ("plugin-" + str(index))
        root.mkdir()
        (root / "plugin.yaml").write_text("invalid", encoding="utf-8")
    call(["plugin", "list"], 1, "plugin inventory manifest limit exceeded")
    call(["software", "--help"], 0)
    report = {"checks": results, "scope": "source CLI inventory limits and official help isolation; no real plugins or services"}
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.output.resolve())))
