"""Local closed TCP ports: compare prior bounded futures and current selector."""
import json
import math
import socket
import statistics
import sys
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from network.commands import scan_port, scan_ports


def previous_scan(ports):
    remaining = iter(ports)
    results = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        pending = {pool.submit(scan_port, "127.0.0.1", next(remaining), "tcp", 0.5)
                   for _ in range(min(len(ports), 16))}
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                results.append(future.result())
                port = next(remaining, None)
                if port is not None:
                    pending.add(pool.submit(scan_port, "127.0.0.1", port, "tcp", 0.5))
    return sorted(results, key=lambda result: result.port)


def measure(operation, ports):
    times, peaks = [], []
    operation(ports)  # Warm imports/caches before measuring steady-state allocations.
    for _ in range(7):
        tracemalloc.start()
        started = time.perf_counter()
        results = operation(ports)
        times.append((time.perf_counter() - started) * 1000)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
        assert [result.port for result in results] == sorted(ports)
        assert all(not result.open for result in results)
    return {"median_ms": statistics.median(times), "p95_ms": sorted(times)[math.ceil(len(times) * .95) - 1],
            "peak_python_bytes": max(peaks), "runs": len(times)}


def main():
    reserved = []
    try:
        for _ in range(64):
            connection = socket.socket()
            connection.bind(("127.0.0.1", 0))
            reserved.append(connection)
        ports = [connection.getsockname()[1] for connection in reserved]
        print(json.dumps({"python": sys.version.split()[0], "platform": sys.platform,
                          "ports": len(ports), "concurrency": 16,
                          "previous_futures": measure(previous_scan, ports),
                          "selector": measure(lambda values: scan_ports("127.0.0.1", values,
                              concurrency=16, total_timeout=30), ports)}, indent=2))
    finally:
        for connection in reserved:
            connection.close()


if __name__ == "__main__":
    main()
