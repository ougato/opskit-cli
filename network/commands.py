"""网络工具命令 — ping / traceroute / DNS / 测速 / 端口扫描"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Iterator


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

def ping(host: str, count: int = 4, kill_ref: "list | None" = None) -> PingResult:
    """
    执行 ping 并返回结果。

    kill_ref：外部传入的空列表，函数将 Popen 对象写入 kill_ref[0]，
    调用方可随时调用 kill_ref[0].kill() 立即中止。

    跨平台：
    - Linux/macOS: ping -c {count}
    - Windows:     ping -n {count}
    """
    ip = _resolve_host(host) or host

    if sys.platform == "win32":
        cmd = ["ping", "-n", str(count), host]
    else:
        cmd = ["ping", "-c", str(count), host]

    try:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, **kwargs
        )
        if kill_ref is not None:
            kill_ref.append(process)
        lines = []
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
        process.wait()
        output = "".join(lines)
        return _parse_ping(host, ip, output, count)
    except subprocess.TimeoutExpired:
        if kill_ref:
            try:
                kill_ref[0].kill()
            except Exception:
                pass
        return PingResult(host=host, ip=ip, packets_sent=count, packets_recv=0,
                          avg_ms=0, min_ms=0, max_ms=0, reachable=False)
    except Exception:
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

def traceroute(host: str, max_hops: int = 20, kill_ref: "list | None" = None) -> Iterator[str]:
    """流式输出 traceroute 每一跳（yield 每行字符串）。

    kill_ref：外部传入的单元素列表 []，函数会将 process 对象写入 kill_ref[0]，
    调用方可在任意时刻调用 kill_ref[0].kill() 立即终止子进程。
    """
    if sys.platform == "win32":
        cmd = ["tracert", "-d", "-h", str(max_hops), host]
    else:
        cmd = ["traceroute", "-n", "-m", str(max_hops), host]

    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kwargs)
        if kill_ref is not None:
            kill_ref.append(process)
        assert process.stdout is not None
        for line in process.stdout:
            yield line.rstrip("\n")
        process.wait()
    except FileNotFoundError:
        yield "[traceroute not found on this system]"
    except Exception as e:
        yield f"[error: {e}]"


# ─── DNS 查询 ─────────────────────────────────────────────────────────────────

def dns_lookup(hostname: str) -> DnsResult:
    """DNS 正向解析"""
    try:
        infos = socket.getaddrinfo(hostname, None)
        addresses = list({info[4][0] for info in infos})
        return DnsResult(hostname=hostname, addresses=addresses)
    except socket.gaierror:
        return DnsResult(hostname=hostname, addresses=[])


def dns_reverse(ip: str) -> str | None:
    """DNS 反向解析（PTR 记录）"""
    try:
        return socket.gethostbyaddr(ip)[0]
    except socket.herror:
        return None


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


def scan_ports(host: str, ports: list[int], timeout: float = 0.5) -> list[PortResult]:
    """批量扫描端口（并发）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: list[PortResult] = []
    with ThreadPoolExecutor(max_workers=min(len(ports), 50)) as pool:
        futures = {pool.submit(scan_port, host, p, "tcp", timeout): p for p in ports}
        for f in as_completed(futures):
            results.append(f.result())
    results.sort(key=lambda r: r.port)
    return results


# ─── 网络测速 ─────────────────────────────────────────────────────────────────

def speed_test_download(url: str, timeout: int = 10, cancel_event: "threading.Event | None" = None) -> float:
    """
    简单下载测速（bytes/s）。

    cancel_event：set() 后立即中止下载并返回已下载部分的速率。
    """
    import httpx
    import threading
    from core.constants import DOWNLOAD_CHUNK_SIZE

    total = 0
    t0 = time.monotonic()
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            for chunk in resp.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                if cancel_event is not None and cancel_event.is_set():
                    break
                total += len(chunk)
                if time.monotonic() - t0 > timeout:
                    break
    except Exception:
        pass
    elapsed = time.monotonic() - t0
    return total / elapsed if elapsed > 0 else 0.0


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


def get_public_ip(cancel_event: "threading.Event | None" = None) -> str | None:
    """获取公网 IP。cancel_event set() 后立即放弃并返回 None。

    每个 API 请求在独立线程中执行，cancel_event 可随时中断等待。
    """
    import httpx
    import threading
    from core.constants import TIMEOUT_MIRROR_PROBE, PUBLIC_IP_APIS
    apis = [
        (PUBLIC_IP_APIS[0], lambda t: t.strip()),
        (PUBLIC_IP_APIS[1], lambda t: t.strip()),
    ]
    for url, extractor in apis:
        if cancel_event is not None and cancel_event.is_set():
            return None
        result_box: list = []

        done_event = threading.Event()

        def _fetch_wrapped(u=url, ex=extractor):
            try:
                with httpx.Client(timeout=TIMEOUT_MIRROR_PROBE) as c:
                    resp = c.get(u)
                    if resp.status_code == 200:
                        result_box.append(ex(resp.text))
            except Exception:
                pass
            finally:
                done_event.set()

        threading.Thread(target=_fetch_wrapped, daemon=True).start()

        deadline = TIMEOUT_MIRROR_PROBE + 1
        step = 0.05
        elapsed = 0.0
        while elapsed < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return None
            if done_event.wait(timeout=step):
                break
            elapsed += step

        if cancel_event is not None and cancel_event.is_set():
            return None
        if result_box:
            return result_box[0]
    return None
