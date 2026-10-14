"""core/logger.py 单元测试"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.logger as lg


@pytest.fixture(autouse=True)
def isolate_logger(monkeypatch):
    import logging
    parent = logging.getLogger("opskit")
    previous = (parent.handlers, parent.level, parent.propagate)
    parent.handlers = []
    monkeypatch.setattr(lg, "_initialized", False)
    monkeypatch.setattr(lg, "_logger", None)
    yield
    for handler in parent.handlers:
        handler.close()
    parent.handlers, parent.level, parent.propagate = previous


def test_init_defers_file_until_first_record(tmp_path: Path) -> None:
    lg.init("WARNING")
    log_path = lg._get_log_path()
    assert not log_path.parent.exists()
    lg.warning("first record")
    assert log_path.is_file()
    assert "first record" in log_path.read_text(encoding="utf-8")


def test_double_init_is_safe(tmp_path: Path) -> None:
    lg.init()
    count = len(lg._logger.handlers)
    lg.init()
    assert len(lg._logger.handlers) == count


def test_get_logger_returns_logger(tmp_path: Path) -> None:
    logger = lg.get_logger("test")
    assert logger is not None


def test_debug_warning_no_crash(tmp_path: Path) -> None:
    lg.init()
    lg.debug("debug message")
    lg.warning("warning message")
    lg.error("error message")
