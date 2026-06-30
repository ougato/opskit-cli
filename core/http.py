"""统一 HTTP 客户端 — 重试 / 超时 / 错误处理"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def get_json(url: str, *, timeout: float = 10, retries: int = 2) -> Any | None:
    """GET 请求并返回 JSON，失败返回 None"""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 403:
                    logger.warning("HTTP 403 (rate limited?): %s", url)
                    return None
                logger.debug("HTTP %d from %s (attempt %d)", resp.status_code, url, attempt)
        except Exception as e:
            last_exc = e
            logger.debug("HTTP error from %s (attempt %d): %s", url, attempt, e)
    return None


def get_bytes(url: str, *, timeout: float = 10, retries: int = 2) -> bytes | None:
    """GET 请求并返回原始字节，失败返回 None。

    与 urllib.request.urlopen 不同，httpx 默认使用 certifi 内置 CA 证书，
    在打包二进制 / 缺少系统 CA 的环境下也能完成 TLS 校验。
    """
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return resp.content
                logger.debug("HTTP %d from %s (attempt %d)", resp.status_code, url, attempt)
        except Exception as e:
            logger.debug("HTTP error from %s (attempt %d): %s", url, attempt, e)
    return None


def head_ok(url: str, *, timeout: float = 5) -> bool:
    """HEAD 请求检测 URL 是否可达"""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.head(url)
            return resp.status_code < 500
    except Exception:
        return False
