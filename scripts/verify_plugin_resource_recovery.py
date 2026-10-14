"""Validate real Docker recovery using only this script's labeled resources."""
import argparse
import json
import os
import re
from pathlib import Path
import uuid

from core import plugin_resources as resources
from core import plugin_sandbox as sandbox


def verify(image, output):
    output.mkdir(parents=True, exist_ok=False)
    previous = os.environ.get("OPSKIT_DATA_DIR")
    os.environ["OPSKIT_DATA_DIR"] = str(output / "data")
    checks = []
    try:
        command = sandbox.docker_command()
        control = lambda *args: sandbox._control(command, *args)
        control("image", "inspect", "--format", "{{.Id}}", image)
        for kind in ("container", "network"):
            name = "opskit-" + ("net-" if kind == "network" else "") + uuid.uuid4().hex
            item = resources.ResourceLease(command, kind, name, control)
            try:
                if kind == "network":
                    control("network", "create", "--internal", *item.labels, name)
                else:
                    control("run", "-d", "--name", name, *item.labels,
                            "--network=none", "--read-only", "--user=65534:65534",
                            "--cap-drop=ALL", "--security-opt=no-new-privileges",
                            "--pids-limit=16", "--memory=32m", "--cpus=0.25",
                            "--log-driver=none", "--pull=never", image,
                            "python", "-c", "import time; time.sleep(120)")
                identity = resources._find(item.record, control)
                assert identity
                result = resources.reconcile(command, control)
                assert result["active"] == 1 and result["recovered"] == 0
                assert resources._find(item.record, control) == identity
                checks.append(kind + ": active lease protected")
                def failed_delete(*args):
                    if args[0] == "rm" or args[:2] == ("network", "rm"):
                        raise sandbox.SandboxError("injected owned-resource cleanup failure")
                    return control(*args)
                try:
                    item.close(failed_delete)
                except sandbox.SandboxError:
                    pass
                else:
                    raise AssertionError("failure injection was not exercised")
                assert item.path.exists() and resources._find(item.record, control) == identity
                checks.append(kind + ": failed cleanup retained intent and resource")
                result = resources.reconcile(command, control)
                assert result == {"recovered": 1, "active": 0, "pending": 0, "invalid": 0}
                assert not item.path.exists() and resources._find(item.record, control) is None
                checks.append(kind + ": recovery removed owned ID and retired intent")
            finally:
                if not item.closed:
                    item.close(control)
                elif item.path.exists():
                    resources.reconcile(command, control)
        # A matching name is insufficient ownership proof, even on this daemon.
        name = "opskit-net-" + uuid.uuid4().hex
        identity = control("network", "create", "--internal", name)
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise RuntimeError("invalid test network identity")
        try:
            item = resources.ResourceLease(command, "network", name, control)
            item.close(control)
            assert not item.path.exists()
            assert control("network", "inspect", "--format", "{{.Id}}", identity) == identity
            checks.append("network: matching name without ownership label retained")
        finally:
            control("network", "rm", identity)
        (output / "verification.json").write_text(json.dumps({"checks": checks, "passed": len(checks)}, indent=2))
        print(json.dumps({"passed": len(checks), "output": str(output)}))
    finally:
        if previous is None:
            os.environ.pop("OPSKIT_DATA_DIR", None)
        else:
            os.environ["OPSKIT_DATA_DIR"] = previous


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", arguments.image):
        parser.error("image must be an immutable local image ID")
    verify(arguments.image, arguments.output.resolve())
