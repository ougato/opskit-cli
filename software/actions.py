"""统一软件操作执行核心。

交互式菜单（``software/menu.py``）与非交互 CLI（``main.py``）共享同一套
install / switch / upgrade / uninstall 执行语义：调用 recipe → 计时 →
安装后 detect → 捕获异常，统一返回 :class:`ActionResult`。

各调用方只负责自身的「反馈出口」差异（菜单走 ``report_failure`` + ``pause``；
CLI 走 ``_direct_fail`` 退出码），不再各自重复执行骨架，避免两套实现漂移。

注意：``KeyboardInterrupt`` 属于 ``BaseException``，不会被 ``except Exception``
捕获，调用方原有的中断处理语义保持不变。
"""
from __future__ import annotations

import time
from dataclasses import dataclass


def _invalidate_installed(instance) -> None:
    """安装/卸载/升级/切换成功后失效该 recipe 的安装状态缓存，下次浏览按需重探。"""
    try:
        from core.installed_cache import invalidate
        invalidate(instance.key)
    except Exception:
        pass


@dataclass(frozen=True)
class ActionResult:
    """一次软件操作的执行结果。

    Attributes:
        ok: 是否成功。
        version: 成功时的有效版本（install 取安装后 detect 结果，回落入参版本）。
        elapsed: 耗时（秒）。
        switched: upgrade 是否解析为「切换已装版本」而非重新安装。
        error: 失败时捕获的异常（成功为 ``None``）。
    """

    ok: bool
    version: str | None = None
    elapsed: float = 0.0
    switched: bool = False
    error: Exception | None = None


def execute_install(instance, version: str) -> ActionResult:
    """执行安装：``install(version)`` → 计时 → 安装后 ``detect()`` 回落版本。"""
    start = time.monotonic()
    try:
        instance.install(version)
        detected = instance.detect() or version
        _invalidate_installed(instance)
        return ActionResult(ok=True, version=detected, elapsed=time.monotonic() - start)
    except Exception as e:  # noqa: BLE001 — 统一交给调用方的反馈出口处理
        return ActionResult(ok=False, version=version, elapsed=time.monotonic() - start, error=e)


def execute_switch(instance, version: str) -> ActionResult:
    """执行版本切换：``switch(version)``。"""
    start = time.monotonic()
    try:
        instance.switch(version)
        _invalidate_installed(instance)
        return ActionResult(ok=True, version=version, elapsed=time.monotonic() - start)
    except Exception as e:  # noqa: BLE001
        return ActionResult(ok=False, version=version, elapsed=time.monotonic() - start, error=e)


def execute_upgrade(instance, version: str, already_installed: bool) -> ActionResult:
    """执行升级。

    ``already_installed=True`` 且 recipe 支持切换时，目标版本已在本地 → 直接
    ``switch``（免重新下载），结果 ``switched=True``；否则走 ``upgrade``。
    """
    start = time.monotonic()
    try:
        if already_installed and hasattr(instance, "switch"):
            instance.switch(version)
            _invalidate_installed(instance)
            return ActionResult(ok=True, version=version, elapsed=time.monotonic() - start, switched=True)
        instance.upgrade(version)
        _invalidate_installed(instance)
        return ActionResult(ok=True, version=version, elapsed=time.monotonic() - start)
    except Exception as e:  # noqa: BLE001
        return ActionResult(ok=False, version=version, elapsed=time.monotonic() - start, error=e)


def execute_uninstall(instance, version: str | None = None) -> ActionResult:
    """执行卸载：``uninstall(version)``（``version=None`` 表示卸载全部）。"""
    start = time.monotonic()
    try:
        instance.uninstall(version)
        _invalidate_installed(instance)
        return ActionResult(ok=True, version=version, elapsed=time.monotonic() - start)
    except Exception as e:  # noqa: BLE001
        return ActionResult(ok=False, version=version, elapsed=time.monotonic() - start, error=e)
