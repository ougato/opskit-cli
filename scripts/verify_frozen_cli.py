"""Read-only CLI acceptance for a locally built frozen executable."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def verify(binary, build, output, *, timeout=60):
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file() or binary.stat().st_size == 0:
        raise ValueError("a regular, nonempty built executable is required")
    if type(build) is not int or build < 1 or not 0 < timeout <= 300:
        raise ValueError("positive build and bounded command timeout required")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    outside = output / "outside-source"
    outside.mkdir()
    home = output / "home"
    home.mkdir()
    data = output / "runtime-must-remain-absent"
    env = dict(os.environ, HOME=str(home),
               XDG_CACHE_HOME=str(output / "cache"), XDG_DATA_HOME=str(output / "data"),
               OPSKIT_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    # A new directory is not a registered Windows user profile. Keep the
    # native profile for frozen startup; OPSKIT_DATA_DIR isolates application
    # storage, while the bootstrap may still use the real LocalAppData cache.
    if os.name != "nt":
        env["USERPROFILE"] = str(home)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    roots = ([], ["software"], ["monitor"], ["network"], ["plugin"], ["preferences"],
             ["theme"], ["language"], ["tailscale"], ["ohmyzsh"], ["xui"], ["wireguard"], ["update"])
    cases = [(root + ["--help"], 0, False) for root in roots]
    cases += [
        (["--output", "json", "--version"], 0, True),
        (["--output", "json", "plugin", "inspect-locks"], 0, True),
        (["--output", "json", "plugin", "inspect-workspaces"], 0, True),
        (["--output", "json", "plugin", "inspect-state", "acceptance_missing"], 0, True),
        (["--output", "json", "plugin", "repair-state", "acceptance_missing", "--record-sha256", "BAD"], 2, True),
        (["--output", "json", "monitor", "cpu", "--once"], 0, True),
        (["--output", "json", "update", "reconcile"], 0, True),
    ]
    records = []
    for index, (arguments, expected, structured) in enumerate(cases):
        result = subprocess.run([str(binary), *arguments], cwd=outside, env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
        (output / f"{index:03d}.stdout").write_bytes(result.stdout)
        (output / f"{index:03d}.stderr").write_bytes(result.stderr)
        (output / f"{index:03d}.status.json").write_text(json.dumps({
            "args": arguments, "expected_exit_code": expected,
            "exit_code": result.returncode,
            "exit_code_hex": f"0x{result.returncode & 0xffffffff:08x}",
        }), encoding="utf-8")
        if result.returncode != expected:
            raise RuntimeError(f"frozen CLI exit mismatch ({result.returncode}, expected {expected}): "
                               + " ".join(arguments))
        if structured:
            payload = json.loads(result.stdout)
            if (not isinstance(payload, dict)
                    or set(payload) != {"schema_version", "ok", "data", "error"}
                    or type(payload["schema_version"]) is not int
                    or payload["schema_version"] != 1
                    or payload["ok"] is not (expected == 0)):
                raise ValueError("frozen CLI result envelope mismatch")
            error = payload["error"]
            if expected == 0:
                if error is not None:
                    raise ValueError("successful frozen CLI returned an error")
            elif (not isinstance(error, dict) or set(error) != {"code", "message"}
                  or type(error["code"]) is not int or error["code"] != expected
                  or not isinstance(error["message"], str) or not error["message"].strip()):
                raise ValueError("failed frozen CLI returned an invalid error")
            if arguments[-1] == "--version" and payload.get("data") != {"version": build}:
                raise ValueError("frozen CLI build mismatch")
            if arguments[-2:] == ["update", "reconcile"] and payload.get("data") != {"state": "no_job"}:
                raise ValueError("clean frozen CLI unexpectedly has an update job")
        if data.exists():
            raise ValueError("read-only frozen CLI initialized runtime storage")
        records.append({"args": arguments, "exit_code": result.returncode})
    hasher = hashlib.sha256()
    with binary.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    digest = hasher.hexdigest()
    report = {"binary_sha256": digest, "build": build, "checks": records, "runtime_storage_created": False}
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"checks": len(records), "binary_sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--build", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.binary, args.build, args.output)))
