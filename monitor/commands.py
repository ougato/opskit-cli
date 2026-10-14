"""系统监控数据采集命令"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass
class CpuInfo:
    percent: float | None          # 总 CPU 使用率 %；无效计数时不可计算
    per_core: list[float | None]   # 同一采样区间的每核使用率 %
    freq_mhz: float         # 当前频率 MHz
    count_logical: int      # 逻辑核心数
    count_physical: int     # 物理核心数
    rate_status: str = "available"
    window_seconds: float | None = None
    started_at: str | None = None
    sampled_at: str | None = None
    rate_unit: str = "percent"
    rate_schema_version: int = 2


@dataclass
class MemInfo:
    total: int      # bytes
    used: int       # bytes
    free: int       # bytes
    percent: float  # %
    swap_total: int
    swap_used: int
    swap_percent: float


@dataclass
class DiskPartition:
    mountpoint: str
    device: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float


@dataclass
class NetInterface:
    name: str
    bytes_sent: int
    bytes_recv: int
    speed_send: float | None   # bytes/s；首样本或重置时不可计算
    speed_recv: float | None
    rate_status: str = "available"
    window_seconds: float | None = None
    sampled_at: str | None = None
    counter_unit: str = "bytes"
    rate_unit: str = "bytes/s"
    rate_schema_version: int = 2


@dataclass
class ProcessEntry:
    pid: int
    name: str
    cpu_percent: float | None
    mem_percent: float | None
    status: str
    rate_status: str = "available"
    created_at: float | None = None
    window_seconds: float | None = None
    sampled_at: str | None = None
    rate_unit: str = "percent"
    cpu_scope: str = "one_logical_cpu"
    rate_schema_version: int = 2


@dataclass
class SystemSnapshot:
    cpu: CpuInfo
    mem: MemInfo
    disks: list[DiskPartition]
    net: list[NetInterface]
    uptime_seconds: float
    load_avg: tuple[float, float, float] | None  # Linux/macOS only


# ─── 网速差值缓存 ─────────────────────────────────────────────────────────────

_net_session: ContextVar[dict | None] = ContextVar("monitor_net_session", default=None)


@contextmanager
def sampling_session():
    """Own baselines for one synchronous CLI invocation or live menu page."""
    token = _net_session.set({})
    from monitor.process_sampling import sampling_session as process_session
    with process_session():
        try:
            yield
        finally:
            _net_session.reset(token)


def _calc_net_speed(previous, name, sent, recv, now):
    old = previous.get(name)
    previous[name] = (sent, recv, now)
    if old is None:
        return None, None, "initial", None
    p_sent, p_recv, p_ts = old
    dt = now - p_ts
    if sent < p_sent or recv < p_recv:
        return None, None, "counter_reset", None
    if dt <= 0:
        return None, None, "invalid_window", None
    return (sent - p_sent) / dt, (recv - p_recv) / dt, "available", dt


# ─── 数据采集 ─────────────────────────────────────────────────────────────────

def get_cpu() -> CpuInfo:
    import psutil
    from monitor.cpu_sampling import sample
    usage = sample()
    freq = psutil.cpu_freq()
    return CpuInfo(
        **usage,
        freq_mhz=freq.current if freq else 0.0,
        count_logical=len(usage["per_core"]),
        count_physical=psutil.cpu_count(logical=False) or 0,
    )


def get_mem() -> MemInfo:
    import psutil
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return MemInfo(
        total=vm.total,
        used=vm.used,
        free=vm.available,
        percent=vm.percent,
        swap_total=sw.total,
        swap_used=sw.used,
        swap_percent=sw.percent,
    )


def get_disks() -> list[DiskPartition]:
    import psutil
    result: list[DiskPartition] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            result.append(DiskPartition(
                mountpoint=part.mountpoint,
                device=part.device,
                fstype=part.fstype,
                total=usage.total,
                used=usage.used,
                free=usage.free,
                percent=usage.percent,
            ))
        except (PermissionError, OSError):
            continue
    return result


def get_net() -> list[NetInterface]:
    import psutil
    # psutil's global nowrap cache must not conceal resets.
    counters = psutil.net_io_counters(pernic=True, nowrap=False)
    if len(counters) > 4096:
        raise RuntimeError("network interface inventory exceeds quota")
    now = time.monotonic()
    sampled_at = datetime.now(timezone.utc).isoformat()
    previous = _net_session.get()
    if previous is None:
        previous = {}  # Unscoped calls are independent, not an implicit watch.
    for missing in previous.keys() - counters.keys():
        del previous[missing]
    result: list[NetInterface] = []
    for name, stat in counters.items():
        if name.startswith("lo"):
            continue
        speed_send, speed_recv, status, window = _calc_net_speed(
            previous, name, stat.bytes_sent, stat.bytes_recv, now)
        result.append(NetInterface(
            name=name,
            bytes_sent=stat.bytes_sent,
            bytes_recv=stat.bytes_recv,
            speed_send=speed_send,
            speed_recv=speed_recv,
            rate_status=status,
            window_seconds=window,
            sampled_at=sampled_at,
        ))
    return result


def get_uptime() -> float:
    """返回系统运行时长（秒）"""
    import psutil
    return time.time() - psutil.boot_time()


def get_load_avg() -> tuple[float, float, float] | None:
    """返回 1/5/15 分钟负载（仅 Linux/macOS）"""
    import os
    if hasattr(os, "getloadavg"):
        return os.getloadavg()  # type: ignore[return-value]
    return None


def get_process(pid: int) -> ProcessEntry:
    """直接采集目标身份；消失/权限/身份变化有明确失败原因。"""
    from monitor.process_sampling import read
    return ProcessEntry(**read(pid))


def get_top_processes(n: int = 10, sort_by: str = "cpu") -> list[ProcessEntry]:
    """按同一会话的身份计数采样；未知指标排在已测指标之后。"""
    from monitor.process_sampling import top
    return [ProcessEntry(**item) for item in top(n, sort_by)]


def get_snapshot() -> SystemSnapshot:
    """一次性采集所有监控数据"""
    return SystemSnapshot(
        cpu=get_cpu(),
        mem=get_mem(),
        disks=get_disks(),
        net=get_net(),
        uptime_seconds=get_uptime(),
        load_avg=get_load_avg(),
    )


# ─── 格式化工具 ───────────────────────────────────────────────────────────────

from core.utils import fmt_bytes, fmt_uptime, fmt_percent_bar  # noqa: F401
