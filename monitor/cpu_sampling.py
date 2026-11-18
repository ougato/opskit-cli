"""CPU utilization from one pair of per-core counters, without global caches."""
from datetime import datetime, timezone
import math
import time


def calculate(before, after, *, linux):
    """Weight aggregate utilization by elapsed CPU ticks, not rounded percentages."""
    unavailable = [None] * len(after)
    if not before or len(before) != len(after):
        return None, unavailable, "topology_changed"
    busy_sum = total_sum = 0.0
    per_core = []
    for old, new in zip(before, after):
        fields = old._fields
        if fields != new._fields or "idle" not in fields or not 1 <= len(fields) <= 32:
            return None, unavailable, "invalid_counters"
        deltas = {}
        for field in fields:
            previous, current = getattr(old, field), getattr(new, field)
            if not all(math.isfinite(value) and value >= 0 for value in (previous, current)):
                return None, unavailable, "invalid_counters"
            if current < previous:
                return None, unavailable, "counter_reset"
            deltas[field] = current - previous
        total = sum(deltas.values())
        if linux:
            # These are already included in user/nice on Linux.
            if (deltas.get("guest", 0) > deltas.get("user", 0) + 1e-9
                    or deltas.get("guest_nice", 0) > deltas.get("nice", 0) + 1e-9):
                return None, unavailable, "invalid_counters"
            total -= deltas.get("guest", 0) + deltas.get("guest_nice", 0)
        busy = total - deltas["idle"] - deltas.get("iowait", 0)
        if not math.isfinite(total) or total < 0 or busy < -1e-9 or busy > total + 1e-9:
            return None, unavailable, "invalid_counters"
        if total == 0:
            return None, unavailable, "no_ticks"
        # Account only for floating point cancellation at zero/total boundaries.
        busy = min(total, max(0.0, busy))
        per_core.append(round(busy / total * 100, 1))
        busy_sum += busy
        total_sum += total
    if not math.isfinite(busy_sum) or not math.isfinite(total_sum):
        return None, unavailable, "invalid_counters"
    return round(busy_sum / total_sum * 100, 1), per_core, "available"


def sample():
    import psutil
    before = psutil.cpu_times(percpu=True)
    if not 1 <= len(before) <= 4096:
        raise RuntimeError("CPU inventory is unavailable or exceeds quota")
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    time.sleep(0.1)
    after = psutil.cpu_times(percpu=True)
    end = time.monotonic()
    sampled_at = datetime.now(timezone.utc).isoformat()
    if not 1 <= len(after) <= 4096:
        raise RuntimeError("CPU inventory is unavailable or exceeds quota")
    window = end - start
    if not math.isfinite(window) or window <= 0:
        percent, per_core, status = None, [None] * len(after), "invalid_window"
        window = None
    else:
        percent, per_core, status = calculate(before, after, linux=psutil.LINUX)
    return dict(percent=percent, per_core=per_core, rate_status=status,
                window_seconds=window, started_at=started_at, sampled_at=sampled_at)
