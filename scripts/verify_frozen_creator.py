"""Verify a frozen host builds a disposable official Creator template via SDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "output", "offline", "creator", "template"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    args = parser.parse_args()
    binary, offline, tool, template = (getattr(args, key).resolve(strict=True)
                                       for key in ("binary", "offline", "creator", "template"))
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    data, plugins = output / "data", output / "plugins"
    os.environ["OPSKIT_DATA_DIR"] = str(data)
    from core.config import set_config_value
    from core.native_sandbox import CapabilityRequest, GrantChoice
    from core.plugin_capabilities import CapabilityAuthority, _key
    from core.plugin_trust import compute_fingerprint
    set_config_value({}, "language", args.language)
    set_config_value({}, "plugin.sandbox.timeout", 900)
    environment = dict(os.environ, OPSKIT_PLUGINS_DIR=str(plugins), PYTHONIOENCODING="utf-8")
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    project = output / "project"
    shutil.copytree(template, project, ignore=shutil.ignore_patterns(".git", "library", "temp", "build"))
    plugin = plugins / "frozen_creator_probe"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "name: frozen_creator_probe\nversion: 1.0.0\napi_version: 2\nkind: python\n"
        "entry: frozen_creator_probe\n", encoding="utf-8")
    (plugin / "frozen_creator_probe.py").write_text(
        "from core.sdk import creator_build,current_lang,plugin_data_dir\n"
        "import json\n"
        "def cli(argv):\n"
        "    result=creator_build('@creator/acceptance',argv[0],{'platform':'web-mobile','debug':True})\n"
        "    if not result['success']: raise RuntimeError('synthetic build failed')\n"
        "    (plugin_data_dir('frozen_creator_probe')/'completed.txt').write_text(current_lang())\n"
        "    (plugin_data_dir('frozen_creator_probe')/'result.json').write_text(json.dumps(result))\n"
        "    return 0\n", encoding="utf-8")
    fingerprint = compute_fingerprint(plugin)
    outside = output / "outside-source"
    outside.mkdir()
    records = []

    def run(label, arguments, success=True):
        with (output / (label + ".stdout")).open("wb") as stdout, \
                (output / (label + ".stderr")).open("wb") as stderr:
            result = subprocess.run([str(binary), "--non-interactive", *arguments], cwd=outside, env=environment,
                                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                    timeout=1000)
        records.append({"step": label, "exit_code": result.returncode})
        if (result.returncode == 0) != success:
            raise RuntimeError("frozen Creator acceptance failed at " + label)

    run("configure", ["--output", "json", "plugin", "configure-creator", "acceptance", str(tool)])
    run("prepare", ["--output", "json", "plugin", "prepare", str(plugin),
                    "--python-runtime", str(offline / "python"), "--wheels", str(offline / "wheels"),
                    "--lock-file", str(offline / "requirements.lock")])
    run("trust", ["--output", "json", "plugin", "trust", "frozen_creator_probe", "--fingerprint", fingerprint])
    # First prove the real frozen host rejects a missing, noninteractive grant.
    run("unapproved", ["plugin", "run", "frozen_creator_probe", "--", str(project)], False)
    scope = CapabilityRequest("frozen_creator_probe", fingerprint, "tool.execute", json.dumps({
        "operation": "creator.build", "executable": str(tool),
        "sha256": hashlib.sha256(tool.read_bytes()).hexdigest(), "project": str(project),
        "platform": "web-mobile", "debug": True,
        "output_policy": "project-build-unique-v1",
    }, ensure_ascii=False, sort_keys=True))
    authority = CapabilityAuthority()
    # Test-owned exact scope only; this is not evidence of interactive consent UI.
    authority.require(scope, check_identity=lambda name, digest: (name, digest) == (
        "frozen_creator_probe", fingerprint), prompt=lambda _: GrantChoice.ALWAYS)
    run("build", ["plugin", "run", "frozen_creator_probe", "--", str(project)])
    state = data / "plugin-data/frozen_creator_probe/self/state/completed.txt"
    result = json.loads(state.with_name("result.json").read_text())
    artifact = Path(result["artifact"]["directory"]) / "index.html"
    if artifact.parent.parent != project / "build" or not artifact.parent.name.startswith("opskit-web-mobile-"):
        raise RuntimeError("frozen Creator output path mismatch")
    if state.read_text() != args.language or not artifact.is_file() or not artifact.stat().st_size:
        raise RuntimeError("frozen Creator artifact/language mismatch")
    run("revoke", ["--output", "json", "plugin", "permissions", "revoke", _key(scope)])
    run("revoked", ["plugin", "run", "frozen_creator_probe", "--", str(project)], False)
    if authority.list_grants() or state.read_text() != args.language:
        raise RuntimeError("frozen Creator revocation mismatch")
    (output / "verification.json").write_text(json.dumps({
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "language": args.language,
        "frozen_creator_verified": True, "artifact": str(artifact), "state_committed": True,
        "missing_grant_denied": True, "revocation_verified": True,
        "interactive_consent_verified": False, "steps": records,
        "fresh_output_inspected": True, "output_summary": result["artifact"],
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
