"""已安装软件状态会话级缓存。

进入「软件管理」时静默探测一次各 recipe 的安装版本，缓存于内存；分类浏览 /
已装列表 / 搜索结果等全部读缓存，不再每次选择都重新探测、不再弹「检测已安装
软件...」阻塞提示。安装 / 卸载 / 升级 / 切换成功后只失效对应 recipe 的缓存项，
下次读取时按需重新探测。
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
# key -> detect() 结果版本字符串；"" 表示已探测且未安装（同样缓存，避免重复探测）。
_cache: dict[str, str] = {}


def get_detected(cls) -> str:
    """返回 recipe 的安装版本（命中缓存直接返回，未命中则探测一次并缓存）。

    探测异常按「未安装」处理并缓存，避免反复触发慢探测。
    """
    key = cls.key
    with _lock:
        if key in _cache:
            return _cache[key]
    try:
        ver = cls().detect() or ""
    except Exception:
        ver = ""
    with _lock:
        _cache[key] = ver
    return ver


def invalidate(key: str) -> None:
    """失效单个 recipe 的安装状态缓存（安装/卸载/升级/切换后调用）。"""
    with _lock:
        _cache.pop(key, None)


def prime(recipes) -> None:
    """静默探测一批 recipe 的安装状态（不弹提示）。"""
    for cls in recipes:
        get_detected(cls)


def prime_async(recipes) -> None:
    """后台线程静默预热缓存，进入软件管理时调用，不阻塞菜单渲染。"""
    snapshot = list(recipes)
    threading.Thread(target=prime, args=(snapshot,), daemon=True).start()


def clear() -> None:
    """清空缓存（测试用 / 完全重新探测）。"""
    with _lock:
        _cache.clear()
