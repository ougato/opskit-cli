"""子进程执行 + 实时输出 + 耗时统计"""
from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Sequence

from rich.console import Console

from core.constants import TIMEOUT_HTTP

console = Console()


def run(
    cmd: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: dict | None = None,
    timeout: int | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """
    执行命令，实时打印输出，返回 CompletedProcess。

    capture=True  → 不打印到终端，结果在 .stdout / .stderr
    check=True    → 非 0 退出码抛出 CalledProcessError
    timeout=None  → 不限时
    """
    start = time.monotonic()
    kwargs: dict = {
        "cwd": str(cwd) if cwd else None,
        "env": env,
    }
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
        result = subprocess.run(list(cmd), timeout=timeout, **kwargs)
    else:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
        kwargs["text"] = True
        process = subprocess.Popen(list(cmd), **kwargs)
        stdout_lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\n")
            stdout_lines.append(line)
            console.print(line)
        process.wait(timeout=timeout)
        result = subprocess.CompletedProcess(
            args=list(cmd),
            returncode=process.returncode,
            stdout="\n".join(stdout_lines),
            stderr="",
        )

    elapsed = time.monotonic() - start
    result.__dict__["elapsed"] = elapsed

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, list(cmd), result.stdout if capture else None
        )
    return result


def run_lines(
    cmd: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: dict | None = None,
    timeout: int | None = None,
) -> Iterator[str]:
    """
    流式执行命令，逐行 yield 输出（生成器）。

    适用于需要处理每一行输出的场景（进度解析等）。
    """
    kwargs: dict = {
        "cwd": str(cwd) if cwd else None,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    process = subprocess.Popen(list(cmd), **kwargs)
    assert process.stdout is not None
    deadline = time.monotonic() + timeout if timeout else None
    for line in process.stdout:
        if deadline and time.monotonic() > deadline:
            process.kill()
            break
        yield line.rstrip("\n")
    process.wait()


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
