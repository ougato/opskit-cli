"""Verify native prepare/invoke/state using a known generated synthetic plugin.

Only a fresh output directory is used. Supplied Python and wheels are reviewed
operator inputs. This source verifier does not establish frozen CLI coverage.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROBE = '''from pathlib import Path
from core.sdk import current_lang, plugin_data_dir

def cli(argv):
    try:
        Path(argv[0]).read_bytes()
    except PermissionError:
        pass
    else:
        raise RuntimeError("host secret accessible")
    try:
        Path(__file__).write_text("overwrite")
    except PermissionError:
        pass
    else:
        raise RuntimeError("plugin source writable")
    (plugin_data_dir("native_probe") / "verified.txt").write_text("native-ok")
    (plugin_data_dir("native_probe") / "language.txt").write_text(current_lang())
    return 0
'''


def verify(output, *, python_runtime, wheels, lock_file, language="en"):
    output = Path(output).absolute()
    # Refuse reuse so existing data, trust, artifacts and failures are preserved.
    output.mkdir(parents=False, exist_ok=False)
    os.environ["OPSKIT_DATA_DIR"] = str(output / "data")
    os.environ["OPSKIT_PLUGINS_DIR"] = str(output / "plugins")
    from core import i18n
    from core.plugin_runtime import build
    from core.plugin_trust import compute_fingerprint, grant
    from core.plugin import load_manifest
    from core.plugin_sandbox import invoke
    i18n.init(language=language)
    plugin = output / "plugins/native_probe"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "name: native_probe\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: native_probe\n",
        encoding="utf-8")
    (plugin / "native_probe.py").write_text(PROBE, encoding="utf-8")
    secret = output / "host-secret.txt"
    secret.write_text("host-only", encoding="utf-8")
    identity = build(plugin, [], Path(python_runtime), wheels=Path(wheels), lock_file=Path(lock_file))
    fingerprint = compute_fingerprint(plugin)
    # Authorize only code generated above, never an external plugin.
    grant("native_probe", fingerprint, "1.0.0")
    result = invoke(load_manifest(plugin), "cli", args=[str(secret)])
    if result != 0:
        raise RuntimeError("native probe result mismatch")
    state = output / "data/plugin-data/native_probe/self/state/verified.txt"
    if state.read_text() != "native-ok":
        raise RuntimeError("native state publication failed")
    if state.with_name("language.txt").read_text() != language:
        raise RuntimeError("native language state mismatch")
    report = {"backend": "native", "runtime": identity, "fingerprint": fingerprint,
              "language": language, "isolation": True, "state_committed": True,
              "frozen_cli_verified": False}
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-runtime", type=Path, required=True)
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), default="en")
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.output, python_runtime=arguments.python_runtime,
                            wheels=arguments.wheels, lock_file=arguments.lock_file,
                            language=arguments.language)))
