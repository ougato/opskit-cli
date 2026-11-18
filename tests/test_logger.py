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


def test_explicit_level_reconfigures_early_logger_without_duplicate_handlers(capsys):
    logger = lg.get_logger("opskit.startup")
    lg.init("INFO")
    logger.debug("debug hidden")
    logger.info("info visible")
    assert "info visible" in capsys.readouterr().err
    lg.init("DEBUG")
    lg.init("DEBUG")
    logger.debug("debug visible once")
    assert capsys.readouterr().err.count("debug visible once") == 1
    lg.init("WARNING")
    logger.warning("menu stays clean")
    assert capsys.readouterr().err == ""
    assert sum(isinstance(handler, lg._DeferredRotatingFileHandler) for handler in lg._logger.handlers) == 1
    assert not any(isinstance(handler, lg._ConsoleHandler) for handler in lg._logger.handlers)
    assert "menu stays clean" in lg._get_log_path().read_text(encoding="utf-8")


def test_invalid_level_uses_warning_without_crashing(capsys):
    lg.init("BASIC_FORMAT")
    lg.warning("written only to file")
    assert capsys.readouterr().err == ""


def test_get_logger_returns_logger(tmp_path: Path) -> None:
    logger = lg.get_logger("test")
    assert logger is not None


def test_debug_warning_no_crash(tmp_path: Path) -> None:
    lg.init()
    lg.debug("debug message")
    lg.warning("warning message")
    lg.error("error message")
