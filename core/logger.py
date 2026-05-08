"""统一日志 — 格式化 + 轮转 + print_error 自动同步写 log"""
from __future__ import annotations

import logging
import logging.handlers
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


def _get_log_path() -> Path:
    from core.config import get_data_dir
    return get_data_dir() / DIR_LOGS / "opskit.log"


def init(level: str = "WARNING") -> None:
    """
    初始化日志系统：

    - 文件 handler：DEBUG 级别，RotatingFileHandler
      - 单文件 5MB 上限，保留 3 个备份
    - 控制台 handler：由 level 参数控制（默认 WARNING），通过 config 可调
    - 格式：[{timestamp}] [{level}] [{module}] {message}
    """
    global _logger, _initialized
    if _initialized:
        return

    log_path = _get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        style="{",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, level.upper(), logging.WARNING))
    console_handler.setFormatter(fmt)

    _logger = logging.getLogger("opskit")
    _logger.setLevel(logging.DEBUG)
    _logger.addHandler(file_handler)
    _logger.addHandler(console_handler)
    _logger.propagate = False

    _initialized = True


def get_logger(name: str = "opskit") -> logging.Logger:
    """获取命名 logger（子 logger 自动继承 opskit 配置）"""
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
