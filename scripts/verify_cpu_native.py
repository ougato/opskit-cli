"""Check real CPU counters against psutil accounting over the exact same pair."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(output):
    import psutil
    from monitor.commands import get_cpu

    read = psutil.cpu_times
    rows = []

    def observed(*, percpu):
        if percpu is not True:
            raise ValueError("CPU sample used a different total window")
        value = read(percpu=True)
        rows.append(value)
        return value

    psutil.cpu_times = observed
    try:
        sample = get_cpu()
    finally:
        psutil.cpu_times = read
    if len(rows) != 2 or sample.rate_status != "available":
        raise ValueError("real counters did not provide an available two-read sample")
    before, after = rows
    # Reference is test-only: product code never depends on psutil private APIs.
    totals, busy, reference = [], [], []
    for old, new in zip(before, after):
        delta = psutil._cpu_times_deltas(old, new)
        total = psutil._cpu_tot_time(delta)
        used = psutil._cpu_busy_time(delta)
        totals.append(total)
        busy.append(used)
        reference.append(round(used / total * 100, 1))
    aggregate = round(sum(busy) / sum(totals) * 100, 1)
    if sample.per_core != reference or sample.percent != aggregate:
        raise ValueError("sample disagrees with same-counter psutil accounting")
    if sample.window_seconds <= 0 or sample.count_logical != len(after):
        raise ValueError("incorrect window or observed logical CPU count")
    report = {"platform": platform.platform(), "python": platform.python_version(),
              "psutil": psutil.__version__, "reads": len(rows),
              "window_seconds": sample.window_seconds, "count_logical": sample.count_logical,
              "percent": sample.percent, "reference_percent": aggregate,
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("monitor/commands.py", "monitor/cpu_sampling.py")},
              "scope": "native local CPU counters, same-pair psutil reference; not a load benchmark or hotplug/reset proof"}
    output = Path(output).resolve()
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.output)))
