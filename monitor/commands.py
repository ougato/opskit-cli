"""系统监控数据采集命令"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CpuInfo:
    percent: float          # 总 CPU 使用率 %
    per_core: list[float]   # 每核使用率 %
    freq_mhz: float         # 当前频率 MHz
    count_logical: int      # 逻辑核心数
    count_physical: int     # 物理核心数


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
    speed_send: float   # bytes/s（差值计算）
    speed_recv: float   # bytes/s（差值计算）


@dataclass
class ProcessEntry:
    pid: int
    name: str
    cpu_percent: float
    mem_percent: float
    status: str


@dataclass
class SystemSnapshot:
    cpu: CpuInfo
    mem: MemInfo
    disks: list[DiskPartition]
    net: list[NetInterface]
    uptime_seconds: float
    load_avg: tuple[float, float, float] | None  # Linux/macOS only


# ─── 网速差值缓存 ─────────────────────────────────────────────────────────────

_net_prev: dict[str, tuple[int, int, float]] = {}  # name → (sent, recv, timestamp)


def _calc_net_speed(name: str, sent: int, recv: int) -> tuple[float, float]:
    now = time.monotonic()
    if name in _net_prev:
        p_sent, p_recv, p_ts = _net_prev[name]
        dt = now - p_ts
        if dt > 0:
            speed_send = (sent - p_sent) / dt
            speed_recv = (recv - p_recv) / dt
        else:
            speed_send = speed_recv = 0.0
    else:
        speed_send = speed_recv = 0.0
    _net_prev[name] = (sent, recv, now)
    return speed_send, speed_recv


# ─── 数据采集 ─────────────────────────────────────────────────────────────────

def get_cpu() -> CpuInfo:
    import psutil
    freq = psutil.cpu_freq()
    return CpuInfo(
        percent=psutil.cpu_percent(interval=0.1),
        per_core=psutil.cpu_percent(interval=0.1, percpu=True),
        freq_mhz=freq.current if freq else 0.0,
        count_logical=psutil.cpu_count(logical=True) or 0,
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
    counters = psutil.net_io_counters(pernic=True)
    result: list[NetInterface] = []
    for name, stat in counters.items():
        if name.startswith("lo"):
            continue
        speed_send, speed_recv = _calc_net_speed(name, stat.bytes_sent, stat.bytes_recv)
        result.append(NetInterface(
            name=name,
            bytes_sent=stat.bytes_sent,
            bytes_recv=stat.bytes_recv,
            speed_send=speed_send,
            speed_recv=speed_recv,
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


def get_top_processes(n: int = 10, sort_by: str = "cpu") -> list[ProcessEntry]:
    """返回按 CPU 或内存排序的 Top N 进程"""
    import psutil
    procs: list[ProcessEntry] = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
        try:
            info = proc.info
            procs.append(ProcessEntry(
                pid=info["pid"],
                name=info["name"] or "",
                cpu_percent=info["cpu_percent"] or 0.0,
                mem_percent=info["memory_percent"] or 0.0,
                status=info["status"] or "",
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = (lambda p: p.cpu_percent) if sort_by == "cpu" else (lambda p: p.mem_percent)
    procs.sort(key=key, reverse=True)
    return procs[:n]


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
