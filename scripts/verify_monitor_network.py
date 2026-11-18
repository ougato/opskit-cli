"""Verify real installed network sampling outside the source checkout."""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess


def verify(environment, output):
    environment = Path(environment).resolve()
    binaries = environment / ("Scripts" if os.name == "nt" else "bin")
    entry = binaries / ("opskit.exe" if os.name == "nt" else "opskit")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    outside = output / "outside-source"
    outside.mkdir()
    data = output / "runtime-must-remain-absent"
    env = dict(os.environ, OPSKIT_DATA_DIR=str(data), VIRTUAL_ENV=str(environment), PYTHONIOENCODING="utf-8")
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    reports = []
    for count in (2, 1):
        result = subprocess.run([str(entry), "--output", "json", "monitor", "network",
                                 "--count", str(count), "--interval", "0.1"],
                                cwd=outside, env=env, capture_output=True, text=True,
                                encoding="utf-8", timeout=30)
        if result.returncode or result.stderr:
            raise RuntimeError("installed network CLI failed or emitted unexpected diagnostics")
        samples = [json.loads(line) for line in result.stdout.splitlines()]
        if len(samples) != count:
            raise ValueError("incorrect JSONL sample count")
        statuses = []
        for index, sample in enumerate(samples):
            if sample["ok"] is not True or sample["error"] is not None or not sample["data"]:
                raise ValueError("no real visible network interface; native rate proof is unavailable")
            stamps = set()
            current = []
            for interface in sample["data"]:
                stamp = datetime.fromisoformat(interface["sampled_at"])
                if stamp.utcoffset() is None:
                    raise ValueError("sampling time has no timezone")
                stamps.add(interface["sampled_at"])
                status = interface["rate_status"]
                current.append(status)
                if interface["rate_schema_version"] != 2 or interface["rate_unit"] != "bytes/s" or interface["counter_unit"] != "bytes":
                    raise ValueError("incorrect network units/schema")
                if index == 0 and status != "initial":
                    raise ValueError("invocation inherited a baseline")
                if status == "available":
                    window = interface["window_seconds"]
                    if not math.isfinite(window) or window <= 0:
                        raise ValueError("invalid actual observation window")
                    for field in ("speed_send", "speed_recv"):
                        if not math.isfinite(interface[field]) or interface[field] < 0:
                            raise ValueError("invalid actual network rate")
                elif status in ("initial", "counter_reset", "invalid_window"):
                    if any(interface[field] is not None for field in ("speed_send", "speed_recv", "window_seconds")):
                        raise ValueError("unavailable rate was fabricated")
                else:
                    raise ValueError("unknown rate status")
            if len(stamps) != 1:
                raise ValueError("interfaces do not share an observation time")
            statuses.append(current)
        if count == 2 and "available" not in statuses[1]:
            raise ValueError("no continuous interface observed; rate proof is unavailable")
        if data.exists():
            raise ValueError("read-only monitor created runtime storage")
        reports.append({"count": count, "statuses": statuses, "exit_code": result.returncode})
    report = {"cases": reports, "runtime_storage_created": False,
              "scope": "real visible local interfaces; no controlled traffic, reset injection or throughput benchmark"}
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.environment, arguments.output)))
