"""子进程执行 + 实时输出 + 耗时统计"""
from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Sequence

from rich.console import Console


console = Console()


def run(cmd: Sequence[str], *, cwd: Path | str | None = None, env: dict | None = None,
        timeout: float | None = None, capture: bool = False, check: bool = True,
        output_limit: int = 1024 * 1024) -> subprocess.CompletedProcess:
    """Run with a real deadline; retain at most output_limit characters of output."""
    from collections import deque
    from core.process import stream
    if output_limit < 1:
        raise ValueError("output_limit must be positive")
    start = time.monotonic()
    chunks = deque()
    size = 0
    iterator = stream(cmd, cwd=cwd, env=env, timeout=timeout)
    try:
        while True:
            try:
                line = next(iterator)
            except StopIteration as done:
                code = done.value
                break
            if not capture:
                console.print(line, markup=False, highlight=False)
            chunks.append(line + "\n")
            size += len(line) + 1
            while size > output_limit and chunks:
                excess = size - output_limit
                chunk = chunks.popleft()
                if len(chunk) > excess:
                    chunks.appendleft(chunk[excess:])
                    size -= excess
                else:
                    size -= len(chunk)
    finally:
        iterator.close()
    result = subprocess.CompletedProcess(list(cmd), code, "".join(chunks).rstrip("\n"), "")
    result.elapsed = time.monotonic() - start
    if check and code:
        raise subprocess.CalledProcessError(code, list(cmd), result.stdout)
    return result


def run_lines(cmd: Sequence[str], *, cwd: Path | str | None = None, env: dict | None = None,
              timeout: float | None = None) -> Iterator[str]:
    from core.process import stream
    yield from stream(cmd, cwd=cwd, env=env, timeout=timeout)


def which(name: str) -> str | None:
    """检查命令是否存在，返回完整路径或 None"""
    import shutil
    return shutil.which(name)


def cmd_ok(cmd: Sequence[str], timeout: int = 5) -> bool:
    """快速检测命令是否可成功执行（退出码 0）"""
    try:
        result = subprocess.run(
            list(cmd),
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except Exception:
        return False
