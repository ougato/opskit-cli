"""网络工具命令 — ping / traceroute / DNS / 测速 / 端口扫描"""
from __future__ import annotations

import socket
import ipaddress
import math
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Iterator

COMMON_TCP_PORTS = (21, 22, 23, 25, 53, 80, 110, 143, 443, 445,
                    3306, 5432, 6379, 8080, 8443, 27017)


@dataclass
class PingResult:
    host: str
    ip: str
    packets_sent: int
    packets_recv: int
    avg_ms: float
    min_ms: float
    max_ms: float
    reachable: bool


@dataclass
class DnsResult:
    hostname: str
    addresses: list[str]
    ttl: int | None = None


@dataclass
class PortResult:
    host: str
    port: int
    protocol: str
    open: bool
    latency_ms: float = 0.0


# ─── Ping ─────────────────────────────────────────────────────────────────────

def ping(host: str, count: int = 4, kill_ref: "list | None" = None, timeout: float = 30, *, strict: bool = False) -> PingResult:
    """
    执行 ping 并返回结果。

    kill_ref：外部传入的空列表，函数将 Popen 对象写入 kill_ref[0]，
    调用方可随时调用 kill_ref[0].kill() 立即中止。

    跨平台：
    - Linux/macOS: ping -c {count}
    - Windows:     ping -n {count}
    """
    if count < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("count and timeout must be positive")
    deadline = time.monotonic() + timeout
    ip = host
    try:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            from network.resolver import resolve
            addresses = resolve(host, timeout=max(0, deadline - time.monotonic()))
            if not addresses:
                raise RuntimeError("ping DNS lookup failed")
            address = ipaddress.ip_address(addresses[0])
        ip = str(address)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ping deadline exceeded")
        if sys.platform == "win32":
            cmd = ["ping", "-n", str(count), ip]
        else:
            executable = "ping6" if sys.platform == "darwin" and address.version == 6 else "ping"
            cmd = [executable, "-c", str(count), ip]
        from core.process import stream
        from collections import deque
        lines = deque(maxlen=256)
        iterator = stream(cmd, timeout=remaining, process_ref=kill_ref)
        try:
            for line in iterator:
                lines.append(line)
        finally:
            iterator.close()
        return _parse_ping(host, ip, "\n".join(lines), count)
    except subprocess.TimeoutExpired:
        if strict:
            raise
        if kill_ref:
            try:
                kill_ref[0].kill()
            except Exception:
                pass
        return PingResult(host=host, ip=ip, packets_sent=count, packets_recv=0,
                          avg_ms=0, min_ms=0, max_ms=0, reachable=False)
    except Exception:
        if strict:
            raise
        return PingResult(host=host, ip=ip, packets_sent=count, packets_recv=0,
                          avg_ms=0, min_ms=0, max_ms=0, reachable=False)


def _parse_ping(host: str, ip: str, output: str, count: int) -> PingResult:
    import re
    recv = 0
    avg_ms = min_ms = max_ms = 0.0

    # Linux/macOS: "3 packets transmitted, 3 received"
    m = re.search(r"(\d+) received", output)
    if m:
        recv = int(m.group(1))

    # Windows: "Received = 3"
    m2 = re.search(r"Received\s*=\s*(\d+)", output)
    if m2:
        recv = int(m2.group(1))

    # Linux/macOS rtt: "rtt min/avg/max/mdev = 1.2/2.3/3.4/0.5 ms"
    m3 = re.search(r"min/avg/max.*?=\s*([\d.]+)/([\d.]+)/([\d.]+)", output)
    if m3:
        min_ms, avg_ms, max_ms = float(m3.group(1)), float(m3.group(2)), float(m3.group(3))

    # Windows: "Minimum = 1ms, Maximum = 3ms, Average = 2ms"
    m4 = re.search(r"Minimum\s*=\s*(\d+)ms.*?Maximum\s*=\s*(\d+)ms.*?Average\s*=\s*(\d+)ms", output)
    if m4:
        min_ms, max_ms, avg_ms = float(m4.group(1)), float(m4.group(2)), float(m4.group(3))

    return PingResult(host=host, ip=ip, packets_sent=count, packets_recv=recv,
                      avg_ms=avg_ms, min_ms=min_ms, max_ms=max_ms, reachable=recv > 0)


# ─── Traceroute ───────────────────────────────────────────────────────────────

def traceroute(host: str, max_hops: int = 20, kill_ref: "list | None" = None, timeout: float = 120, *, strict: bool = False) -> Iterator[str]:
    """流式输出 traceroute 每一跳（yield 每行字符串）。

    kill_ref：外部传入的单元素列表 []，函数会将 process 对象写入 kill_ref[0]，
    调用方可在任意时刻调用 kill_ref[0].kill() 立即终止子进程。
    """
    if not 1 <= max_hops <= 255 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid hops or timeout")
    deadline = time.monotonic() + timeout
    try:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            from network.resolver import resolve
            addresses = resolve(host, timeout=max(0, deadline - time.monotonic()))
            if not addresses:
                raise RuntimeError("traceroute DNS lookup failed")
            address = ipaddress.ip_address(addresses[0])
        target = str(address)
        if sys.platform == "win32":
            cmd = ["tracert", "-d", "-h", str(max_hops)]
        else:
            executable = "traceroute6" if sys.platform == "darwin" and address.version == 6 else "traceroute"
            cmd = [executable, "-n", "-m", str(max_hops)]
        if address.version == 6 and sys.platform != "darwin":
            cmd.append("-6")
        cmd.append(target)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("traceroute deadline exceeded")
        from core.process import stream
        iterator = stream(cmd, timeout=remaining, process_ref=kill_ref)
        reached = False
        try:
            while True:
                try:
                    line = next(iterator)
                except StopIteration as done:
                    code = done.value
                    break
                parts = line.split()
                if parts and parts[0].isdigit() and not any(part.startswith("!") for part in parts):
                    for part in parts[1:]:
                        try:
                            reached = reached or ipaddress.ip_address(part.strip("[](),")) == address
                        except ValueError:
                            pass
                yield line
        finally:
            iterator.close()
        if code:
            raise subprocess.CalledProcessError(code, cmd)
        return {"host": host, "target": target, "status": "reached" if reached else "not_reached",
                "reached": reached, "return_code": code}
    except FileNotFoundError:
        if strict:
            raise
        yield "[traceroute not found on this system]"
    except Exception as e:
        if strict:
            raise
        yield f"[error: {e}]"


# ─── DNS 查询 ─────────────────────────────────────────────────────────────────

def dns_lookup(hostname: str, family: int = socket.AF_UNSPEC, *, timeout: float = 10) -> DnsResult:
    """DNS 正向解析"""
    from network.resolver import resolve
    return DnsResult(hostname=hostname, addresses=resolve(hostname, family=family, timeout=timeout) or [])


def dns_reverse(ip: str, *, timeout: float = 10) -> str | None:
    """DNS 反向解析（PTR 记录）"""
    from network.resolver import resolve
    return resolve(ip, reverse=True, timeout=timeout)


# ─── 端口扫描 ─────────────────────────────────────────────────────────────────

def scan_port(host: str, port: int, protocol: str = "tcp", timeout: float = 1.0) -> PortResult:
    """扫描单个端口"""
    ip = _resolve_host(host) or host
    t0 = time.monotonic()
    open_ = False
    try:
        if protocol == "tcp":
            with socket.create_connection((ip, port), timeout=timeout):
                open_ = True
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            s.sendto(b"", (ip, port))
            s.recvfrom(64)
            s.close()
            open_ = True
    except Exception:
        pass
    latency = (time.monotonic() - t0) * 1000
    return PortResult(host=host, port=port, protocol=protocol,
                      open=open_, latency_ms=latency)


def scan_ports(host: str, ports: list[int], timeout: float = 0.5, concurrency: int = 50,
               *, total_timeout: float = 30) -> list[PortResult]:
    """批量 TCP 扫描：单连接期限和包含 DNS 的总期限分别限制。"""
    from network.scanner import scan
    return scan(host, ports, timeout=timeout, concurrency=concurrency, total_timeout=total_timeout)


# ─── 网络测速 ─────────────────────────────────────────────────────────────────

def speed_test_download(url: str, timeout: int = 10, cancel_event: "threading.Event | None" = None) -> float:
    """
    简单下载测速（bytes/s）。

    cancel_event：set() 后立即中止下载并返回已下载部分的速率。
    """
    from network.http_measure import download
    try:
        return download(url, timeout, cancel_event)["bytes_per_second"]
    except (OSError, RuntimeError, ValueError):
        return 0.0


# ─── 辅助 ─────────────────────────────────────────────────────────────────────

def _resolve_host(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return None


def get_local_ip() -> str:
    """获取本机出口 IP（连接外部地址但不发送数据）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_public_ip(cancel_event: "threading.Event | None" = None, timeout: float = 10) -> str | None:
    """Bounded synchronous lookup; no abandoned fetch threads on cancellation."""
    from core.constants import PUBLIC_IP_APIS
    from network.http_measure import public_ip
    try:
        return public_ip(PUBLIC_IP_APIS, timeout, cancel_event)
    except (OSError, RuntimeError):
        return None


def download_measurement(url: str, duration: float) -> dict:
    """Measure downloaded bytes with a bounded read and total measurement window."""
    from network.http_measure import download
    return download(url, duration)
