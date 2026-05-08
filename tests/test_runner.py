"""core/runner.py 单元测试"""
from __future__ import annotations

import sys

import pytest

from core.runner import run, run_lines, cmd_ok, which


def test_run_capture_echo(tmp_path) -> None:
    if sys.platform == "win32":
        result = run(["cmd", "/c", "echo", "hello"], capture=True)
    else:
        result = run(["echo", "hello"], capture=True)
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_run_check_raises(tmp_path) -> None:
    with pytest.raises(Exception):
        if sys.platform == "win32":
            run(["cmd", "/c", "exit", "1"], capture=True, check=True)
        else:
            run(["false"], capture=True, check=True)


def test_run_no_check(tmp_path) -> None:
    if sys.platform == "win32":
        result = run(["cmd", "/c", "exit", "1"], capture=True, check=False)
    else:
        result = run(["false"], capture=True, check=False)
    assert result.returncode != 0


def test_run_lines(tmp_path) -> None:
    if sys.platform == "win32":
        lines = list(run_lines(["cmd", "/c", "echo", "line1"]))
    else:
        lines = list(run_lines(["echo", "line1"]))
    assert any("line1" in l for l in lines)


def test_cmd_ok_true(tmp_path) -> None:
    if sys.platform == "win32":
        assert cmd_ok(["cmd", "/c", "echo", "ok"]) is True
    else:
        assert cmd_ok(["true"]) is True


def test_cmd_ok_false(tmp_path) -> None:
    assert cmd_ok(["nonexistent_command_xyz_123"]) is False


def test_which_python(tmp_path) -> None:
    assert which("python") is not None or which("python3") is not None
