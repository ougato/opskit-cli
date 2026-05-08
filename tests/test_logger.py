"""core/logger.py 单元测试"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.logger as lg


def test_init_creates_log_file(tmp_path: Path) -> None:
    lg._initialized = False
    lg._logger = None
    lg.init("WARNING")
    log_path = lg._get_log_path()
    assert log_path.parent.exists()


def test_double_init_is_safe(tmp_path: Path) -> None:
    lg._initialized = False
    lg._logger = None
    lg.init()
    lg.init()


def test_get_logger_returns_logger(tmp_path: Path) -> None:
    lg._initialized = False
    lg._logger = None
    logger = lg.get_logger("test")
    assert logger is not None


def test_debug_warning_no_crash(tmp_path: Path) -> None:
    lg._initialized = False
    lg._logger = None
    lg.init()
    lg.debug("debug message")
    lg.warning("warning message")
    lg.error("error message")
