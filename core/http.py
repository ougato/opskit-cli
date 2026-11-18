"""统一 HTTP 客户端 — 重试 / 超时 / 错误处理"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("opskit.http")
_request_lock = threading.Lock()
_inflight: dict[str, Future] = {}
_github_retry_at = 0.0


def __getattr__(name: str):
    """Preserve access to the HTTP client without loading it during CLI setup."""
    if name == "httpx":
        import httpx
        return httpx
    raise AttributeError(name)


def _rate_limit_delay(resp: Any) -> float | None:
    """Only classify GitHub throttling using status, headers and API message."""
    if resp.status_code not in (403, 429):
        return None
    try:
        message = str(resp.json().get("message", "")).lower()
    except (ValueError, AttributeError):
        message = ""
    remaining = resp.headers.get("x-ratelimit-remaining")
    retry = resp.headers.get("retry-after")
    if resp.status_code != 429 and remaining != "0" and not retry and "rate limit" not in message:
        return None
    delays = [60.0]
    if retry:
        try:
            delays.append(float(retry))
        except ValueError:
            try:
                delays.append(parsedate_to_datetime(retry).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
    if remaining == "0":
        try:
            delays.append(float(resp.headers.get("x-ratelimit-reset", "0")) - time.time())
        except ValueError:
            pass
    return max(delays)


def get_json(url: str, *, timeout: float = 10, retries: int = 2) -> Any | None:
    """GET 请求并返回 JSON，失败返回 None"""
    with _request_lock:
        pending = _inflight.get(url)
        owner = pending is None
        if owner:
            pending = Future()
            _inflight[url] = pending
    if not owner:
        try:
            return pending.result(timeout=timeout)
        except TimeoutError:
            return None
    try:
        result = _get_json(url, timeout=timeout, retries=retries)
        pending.set_result(result)
        return result
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _request_lock:
            _inflight.pop(url, None)


def _get_json(url: str, *, timeout: float, retries: int) -> Any | None:
    global _github_retry_at
    github = urlsplit(url).hostname == "api.github.com"
    for attempt in range(1, retries + 1):
        with _request_lock:
            if github and time.monotonic() < _github_retry_at:
                return None
        try:
            import httpx
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                delay = _rate_limit_delay(resp) if github else None
                if delay is not None:
                    with _request_lock:
                        _github_retry_at = max(_github_retry_at, time.monotonic() + delay)
                    logger.warning("GitHub API rate limited; cooling down for %.0fs: %s", delay, url)
                    return None
                if resp.status_code == 403:
                    logger.warning("HTTP 403 Forbidden: %s", url)
                    return None
                logger.debug("HTTP %d from %s (attempt %d)", resp.status_code, url, attempt)
        except Exception as e:
            logger.debug("HTTP error from %s (attempt %d): %s", url, attempt, e)
    return None


def get_bytes(url: str, *, timeout: float = 10, retries: int = 2) -> bytes | None:
    """GET 请求并返回原始字节，失败返回 None。

    与 urllib.request.urlopen 不同，httpx 默认使用 certifi 内置 CA 证书，
    在打包二进制 / 缺少系统 CA 的环境下也能完成 TLS 校验。
    """
    for attempt in range(1, retries + 1):
        try:
            import httpx
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
        import httpx
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.head(url)
            return resp.status_code < 500
    except Exception:
        return False
