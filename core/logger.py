"""统一日志 — 格式化 + 轮转 + print_error 自动同步写 log"""
from __future__ import annotations

import logging
import logging.handlers
import threading
from pathlib import Path

from core.constants import (
    DIR_LOGS,
    LOG_BACKUP_COUNT,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_MAX_BYTES,
)

_logger: logging.Logger | None = None
_initialized: bool = False
_init_lock = threading.RLock()


class _DeferredRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Resolve logging during import, create its directory only on first record."""

    def _open(self):
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()


class _ConsoleHandler(logging.StreamHandler):
    """Identify the console handler owned by this module."""


def _configure_console(logger: logging.Logger, level: str, formatter: logging.Formatter) -> None:
    from core.secrets import RedactionFilter

    parsed_level = logging.getLevelName(level.upper())
    console_level = parsed_level if isinstance(parsed_level, int) else logging.WARNING
    handlers = [handler for handler in logger.handlers if isinstance(handler, _ConsoleHandler)]
    if console_level > logging.INFO:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
        return
    if handlers:
        handlers[0].setLevel(console_level)
        return
    handler = _ConsoleHandler()
    handler.addFilter(RedactionFilter())
    handler.setLevel(console_level)
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _get_log_path() -> Path:
    from core.config import get_data_dir
    return get_data_dir() / DIR_LOGS / "opskit.log"


def init(level: str = "WARNING") -> None:
    """
    初始化日志系统：

    - 文件 handler：DEBUG 级别，RotatingFileHandler
      - 单文件 5MB 上限，保留 3 个备份
    - 控制台 handler：仅当 level 设为 DEBUG/INFO（用户显式开启详细日志）时才挂载。
      默认 WARNING 不挂控制台 handler，避免后台线程（如自动更新）的 WARNING/ERROR
      日志直接打印到交互菜单里，污染 TUI；用户可见提示统一走 print_error/print_warning。
    - 格式：[{timestamp}] [{level}] [{module}] {message}
    """
    global _logger, _initialized
    with _init_lock:
        fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT, style="{")
        if not _initialized:
            file_handler = _DeferredRotatingFileHandler(
                _get_log_path(),
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
                delay=True,
            )
            from core.secrets import RedactionFilter
            file_handler.addFilter(RedactionFilter())
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(fmt)

            _logger = logging.getLogger("opskit")
            _logger.setLevel(logging.DEBUG)
            _logger.addHandler(file_handler)
            _logger.propagate = False
            _initialized = True
        _configure_console(_logger, level, fmt)


def get_logger(name: str = "opskit") -> logging.Logger:
    """获取命名 logger（子 logger 自动继承 opskit 配置）"""
    with _init_lock:
        if not _initialized:
            init()
    return logging.getLogger(name)


def debug(msg: str, *args, **kwargs) -> None:
    get_logger().debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs) -> None:
    get_logger().info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs) -> None:
    get_logger().warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs) -> None:
    get_logger().error(msg, *args, **kwargs)


def exception(msg: str, *args, **kwargs) -> None:
    get_logger().exception(msg, *args, **kwargs)
