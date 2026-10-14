"""Probe initial worker rejection in an immutable SDK, using synthetic state."""
import argparse
import json
import os
from pathlib import Path
import re
import uuid

from core import plugin_resources, plugin_sandbox as sandbox


PROBE = """
import json,subprocess,sys
from pathlib import Path
result=subprocess.run([sys.executable,'-m','core.plugin_worker','/request.json'],capture_output=True,text=True,timeout=15)
expected=sys.argv[1]=='valid'
copied=Path('/data/state/seed-marker').exists()
imported=Path('/data/state/import-marker').exists()
if expected:
    assert result.returncode==0 and copied and imported
    assert 'OPSKIT_RPC:' in result.stdout
else:
    assert result.returncode!=0 and not copied and not imported
    assert 'ValueError:' in result.stderr
    assert 'invalid worker' in result.stderr or 'service contract' in result.stderr
print(json.dumps({'copied':copied,'imported':imported,'worker_exit':result.returncode}))
"""


def verify(image, output):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("image must be an immutable local SDK ID")
    output.mkdir(parents=True, exist_ok=False)
    previous = os.environ.get("OPSKIT_DATA_DIR")
    os.environ["OPSKIT_DATA_DIR"] = str(output / "platform-state")
    try:
        command = sandbox.docker_command()
        sandbox._control(command, "image", "inspect", image)
        plugin = output / "plugin/demo_pkg"
        plugin.mkdir(parents=True)
        (plugin / "__init__.py").write_text(
            "from pathlib import Path\nPath('/data/state/import-marker').write_text('imported')\ndef cli(argv): return 0\n",
            encoding="utf-8")
        seed = output / "seed/state"
        seed.mkdir(parents=True)
        (seed / "seed-marker").write_text("synthetic state", encoding="utf-8")
        base = {"rpc_version": 1, "action": "cli", "name": "demo", "kind": "python",
                "entry": "demo_pkg", "args": [], "kwargs": {}, "service": "", "method": "",
                "caller": "platform", "service_contract": None}
        cases = {"valid": {}, "extra": {"extra": True}, "caller": {"caller": "../other"},
                 "action": {"action": "unknown"}, "reserved_entry": {"entry": "core"},
                 "absolute_exec": {"kind": "exec", "entry": "/bin/sh"},
                 "mixed_service": {"service": "counter"},
                 "invalid_contract": {"action": "service.call", "service": "counter",
                                      "service_contract": {"api_version": True, "methods": ["read"]}}}
        results, workers = [], []
        for mode, changes in cases.items():
            request = output / (mode + ".json")
            request.write_text(json.dumps({**base, **changes}), encoding="utf-8")
            name = "opskit-" + uuid.uuid4().hex
            with sandbox._resource_scope(command, "container", name) as labels:
                args = ["run", "--rm", "--name", name, "--pull=never", *labels,
                        "--network=none", "--read-only", "--user=65534:65534", "--cap-drop=ALL",
                        "--security-opt=no-new-privileges", "--pids-limit=32", "--memory=128m",
                        "--memory-swap=128m", "--cpus=1", "--log-driver=none", "--env", "OPSKIT_SANDBOX=1",
                        "--tmpfs", "/data:rw,nosuid,nodev,noexec,size=32m,uid=65534,gid=65534",
                        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m,uid=65534,gid=65534"]
                for source, target in ((output / "plugin", "/plugin"), (output / "seed", "/seed"), (request, "/request.json")):
                    args.extend(["--mount", f"type=bind,src={sandbox.mount_path(source, command)},dst={target},readonly"])
                result = json.loads(sandbox._control(command, *args, image, "python", "-c", PROBE, mode))
                results.append({"mode": mode, **result})
            remaining = sandbox._control(command, "ps", "-a", "--filter", "name=" + name, "--format", "{{.Names}}")
            if name in remaining.splitlines():
                raise RuntimeError("owned initial-request worker remains")
            workers.append(name)
        recovery = plugin_resources.reconcile(command, lambda *args: sandbox._cleanup_control(command, *args))
        if any(recovery.values()):
            raise RuntimeError("initial-request verification left resource records")
        report = {"image": image, "cases": results, "removed_workers": workers, "recovery": recovery,
                  "scope": "synthetic initial envelopes and valid control; not complete daemon/network failure acceptance"}
        (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        if previous is None:
            os.environ.pop("OPSKIT_DATA_DIR", None)
        else:
            os.environ["OPSKIT_DATA_DIR"] = previous


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.image, args.output.resolve())))
