"""Verify frozen SDK upload bytes against a disposable local TLS receiver."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify_upload_native import receiver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "output", "offline"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    args = parser.parse_args()
    binary, offline = args.binary.resolve(strict=True), args.offline.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    data, plugins = output / "data", output / "plugins"
    os.environ["OPSKIT_DATA_DIR"] = str(data)
    from core.config import set_config_value
    from core.native_sandbox import CapabilityRequest, GrantChoice
    from core.plugin_capabilities import CapabilityAuthority, _key
    from core.plugin_trust import compute_fingerprint
    set_config_value({}, "language", args.language)
    set_config_value({}, "plugin.sandbox.private_targets.frozen_upload_probe", ["127.0.0.1:443"])
    environment = dict(os.environ, OPSKIT_PLUGINS_DIR=str(plugins), PYTHONIOENCODING="utf-8")
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    project = output / "synthetic-project"
    project.mkdir()
    content = b"OpsKit frozen synthetic artifact\0" * 80000
    (project / "artifact.bin").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    plugin = plugins / "frozen_upload_probe"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "name: frozen_upload_probe\nversion: 1.0.0\napi_version: 4\nkind: python\n"
        "entry: frozen_upload_probe\npermissions: [network]\n", encoding="utf-8")
    (plugin / "frozen_upload_probe.py").write_text(
        "from core.sdk import upload_file,artifact_info,current_lang,plugin_data_dir\n"
        "def cli(argv):\n"
        "    info=artifact_info(argv[0],'artifact.bin')\n"
        "    if info['sha256']!=argv[2]: raise RuntimeError('artifact checksum mismatch')\n"
        "    result=upload_file(argv[0],'artifact.bin',argv[1],info['sha256'])\n"
        "    if result['sha256']!=argv[2] or result['status']!=200: raise RuntimeError('upload failed')\n"
        "    (plugin_data_dir('frozen_upload_probe')/'completed.txt').write_text(current_lang())\n"
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
                                    timeout=600)
        records.append({"step": label, "exit_code": result.returncode})
        if (result.returncode == 0) != success:
            raise RuntimeError("frozen upload acceptance failed at " + label)

    run("prepare", ["--output", "json", "plugin", "prepare", str(plugin),
                    "--python-runtime", str(offline / "python"), "--wheels", str(offline / "wheels"),
                    "--lock-file", str(offline / "requirements.lock")])
    run("trust", ["--output", "json", "plugin", "trust", "frozen_upload_probe", "--fingerprint", fingerprint])
    scope = CapabilityRequest("frozen_upload_probe", fingerprint, "directory.read", str(project))
    authority = CapabilityAuthority()
    server, thread, certificate, received = receiver(output, content)
    environment["SSL_CERT_FILE"] = str(certificate)
    arguments = ["plugin", "run", "frozen_upload_probe", "--", str(project),
                 "https://127.0.0.1/synthetic-object?token=synthetic", digest]
    try:
        run("unapproved", arguments, False)
        if received:
            raise RuntimeError("unapproved upload reached receiver")
        authority.require(scope, check_identity=lambda name, fp: (name, fp) == (
            "frozen_upload_probe", fingerprint), prompt=lambda _: GrantChoice.ALWAYS)
        run("upload", arguments)
        state = data / "plugin-data/frozen_upload_probe/self/state/completed.txt"
        if (state.read_text() != args.language or len(received) != 1
                or received[0] != {"valid": True, "sha256": digest, "bytes": len(content)}):
            raise RuntimeError("frozen upload bytes/state mismatch")
        run("revoke", ["--output", "json", "plugin", "permissions", "revoke", _key(scope)])
        run("revoked", arguments, False)
        if len(received) != 1 or authority.list_grants() or state.read_text() != args.language:
            raise RuntimeError("revoked frozen upload changed receiver/state")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    (output / "verification.json").write_text(json.dumps({
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "language": args.language,
        "frozen_sdk_upload_verified": True, "received": received,
        "artifact_info_verified": True,
        "missing_grant_denied": True, "revocation_verified": True, "state_committed": True,
        "interactive_consent_verified": False, "actual_s3_verified": False, "steps": records,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
