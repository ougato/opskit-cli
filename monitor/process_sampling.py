"""Session-owned process counters; PID reuse cannot inherit another identity's rate."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import math
import time
from heapq import nlargest

MAX_PROCESSES = 16384


def unavailable(pid, reason):
    return dict(pid=pid, name="", cpu_percent=None, mem_percent=None,
                status=reason, rate_status=reason, created_at=None,
                window_seconds=None, sampled_at=datetime.now(timezone.utc).isoformat())


class ProcessSamplingError(RuntimeError):
    def __init__(self, pid, reason):
        super().__init__("process sampling is unavailable: " + reason)
        self.data = unavailable(pid, reason)


class ProcessSampler:
    def __init__(self):
        self.previous = {}

    def read(self, pid):
        import psutil
        if type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF:
            raise ValueError("invalid operating-system PID")
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                created = proc.create_time()
                if not math.isfinite(created) or created < 0:
                    raise ProcessSamplingError(pid, "invalid_counters")
                counters = proc.cpu_times()
                now = time.monotonic()
                sampled_at = datetime.now(timezone.utc).isoformat()
                name, status, memory = proc.name(), proc.status(), proc.memory_percent()
            # create_time on the first object is cached; a fresh object is
            # necessary to revalidate identity after reading other attributes.
            if psutil.Process(pid).create_time() != created:
                raise ProcessSamplingError(pid, "identity_changed")
        except psutil.ZombieProcess:
            self.previous.pop(pid, None)
            raise ProcessSamplingError(pid, "zombie") from None
        except psutil.NoSuchProcess:
            self.previous.pop(pid, None)
            raise ProcessSamplingError(pid, "gone") from None
        except psutil.AccessDenied:
            self.previous.pop(pid, None)
            raise ProcessSamplingError(pid, "access_denied") from None
        except ProcessSamplingError:
            self.previous.pop(pid, None)
            raise

        user, system = counters.user, counters.system
        result = dict(pid=pid, name=name, status=status, mem_percent=memory,
                      cpu_percent=None, created_at=created, window_seconds=None,
                      sampled_at=sampled_at, rate_status="initial")
        if not all(math.isfinite(value) and value >= 0 for value in (created, user, system, memory, now)):
            self.previous.pop(pid, None)
            result.update(mem_percent=None, rate_status="invalid_counters",
                          created_at=created if math.isfinite(created) and created >= 0 else None)
            return result
        old = self.previous.get(pid)
        if old is None and len(self.previous) >= MAX_PROCESSES:
            raise RuntimeError("process baseline inventory exceeds quota")
        self.previous[pid] = (created, user, system, now)
        if old is not None:
            p_created, p_user, p_system, p_time = old
            window = now - p_time
            if created != p_created:
                result["rate_status"] = "identity_changed"
            elif user < p_user or system < p_system:
                result["rate_status"] = "counter_reset"
            elif window <= 0:
                result["rate_status"] = "invalid_window"
            else:
                percent = ((user - p_user) + (system - p_system)) / window * 100
                if math.isfinite(percent):
                    result.update(cpu_percent=round(percent, 1), rate_status="available",
                                  window_seconds=window)
                else:
                    result["rate_status"] = "invalid_counters"
        return result

    def inventory(self):
        import psutil
        seen, pids, rows = set(), [], []
        for proc in psutil.process_iter():
            if proc.pid == 0:
                continue  # Idle pseudo-process; CLI PIDs describe positive process identities.
            if proc.pid in seen:
                continue
            seen.add(proc.pid)
            if len(seen) > MAX_PROCESSES:
                raise RuntimeError("process inventory exceeds quota")
            pids.append(proc.pid)
        for missing in self.previous.keys() - seen:
            del self.previous[missing]
        for pid in pids:
            try:
                rows.append(self.read(pid))
            except ProcessSamplingError as exc:
                rows.append(exc.data)
        return rows


_session = ContextVar("monitor_process_session", default=None)


@contextmanager
def sampling_session():
    token = _session.set(ProcessSampler())
    try:
        yield
    finally:
        _session.reset(token)


def read(pid):
    return (_session.get() or ProcessSampler()).read(pid)


def top(n, sort_by):
    if type(n) is not int or not 1 <= n <= 10000 or sort_by not in {"cpu", "memory"}:
        raise ValueError("invalid process limit or sort")
    sampler = _session.get() or ProcessSampler()
    rows = sampler.inventory()
    if sort_by == "cpu" and rows and not any(row["cpu_percent"] is not None for row in rows):
        if any(row["rate_status"] in {"initial", "identity_changed"} for row in rows):
            time.sleep(.1)
            rows = sampler.inventory()
    field = "cpu_percent" if sort_by == "cpu" else "mem_percent"
    def rank(row):
        return row[field] is not None, row[field] or 0
    if n * 8 < len(rows):
        return nlargest(n, rows, key=rank)
    rows.sort(key=rank, reverse=True)
    return rows[:n]
