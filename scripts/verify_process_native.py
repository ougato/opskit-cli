"""Exercise owned PID CPU sampling and top queries without spawning workload children."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(output):
    import psutil
    from monitor.commands import get_process, get_top_processes, sampling_session
    reference = psutil.Process(os.getpid())
    with sampling_session():
        first = get_process(os.getpid())
        reference.cpu_percent(None)
        end = time.monotonic() + .25
        value = 1
        while time.monotonic() < end:
            value = (value * 17 + 3) % 1000003
        measured = get_process(os.getpid())
        reference_percent = reference.cpu_percent(None)
        if first.cpu_percent is not None or first.rate_status != "initial":
            raise ValueError("PID first sample was fabricated")
        if (measured.rate_status != "available" or measured.cpu_percent <= 0
                or reference_percent <= 0 or measured.created_at != first.created_at
                or measured.window_seconds <= 0):
            raise ValueError("owned CPU activity was not measured for its correct identity")
    with sampling_session():
        if get_process(os.getpid()).rate_status != "initial":
            raise ValueError("PID baseline escaped its session")
        memory = get_top_processes(5, "memory")
    with sampling_session():
        cpu = get_top_processes(5, "cpu")
    for rows, field in ((memory, "mem_percent"), (cpu, "cpu_percent")):
        if not rows or len(rows) > 5 or any(row.pid == 0 for row in rows):
            raise ValueError("incorrect top query size or idle pseudo-process")
        keys = [(getattr(row, field) is not None, getattr(row, field) or 0) for row in rows]
        if keys != sorted(keys, reverse=True):
            raise ValueError("top query did not rank known metrics first")
    if not any(row.cpu_percent is not None for row in cpu):
        raise ValueError("native CPU ranking had no measured entry")
    report = {"platform": platform.platform(), "python": platform.python_version(),
              "psutil": psutil.__version__, "first_rate_status": first.rate_status,
              "measured_cpu_percent": measured.cpu_percent,
              "reference_cpu_percent": reference_percent,
              "window_seconds": measured.window_seconds,
              "memory_top_count": len(memory), "cpu_top_count": len(cpu),
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("monitor/commands.py", "monitor/process_sampling.py")},
              "scope": "owned interpreter .25s CPU loop and visible native process inventory; reference uses a different observation window; not a throughput benchmark or PID reuse/permission proof"}
    output = Path(output).resolve()
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.output)))
