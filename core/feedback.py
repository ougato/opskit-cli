"""统一软件操作反馈层 — 成功/失败提示集中分流。

各菜单 install / uninstall / upgrade / switch 此前各自重复同一套
「遥测上报 + 已知错误 vs 未知错误分流 print_error」两行样板（约 9 处），
此处收敛为 report_failure，保证提示文案与上报口径长期一致。

成功提示各处文案 key 不同（install.success / uninstall.success / ...），
直接走 i18n 即可，无需再包一层，故此模块只统一「失败」与「遥测」。
"""
from __future__ import annotations

from core.i18n import t
from core.theme import print_error


def capture(exc: Exception, **ctx) -> None:
    """上报异常到遥测系统，失败静默。"""
    try:
        import core.telemetry as _tel
        _tel.capture_error(exc, **ctx)
    except Exception:
        pass


def report_failure(
    exc: Exception,
    *,
    fail_key: str,
    name: str,
    software: str,
    action: str,
    version: str | None = None,
) -> None:
    """失败统一反馈：遥测上报 + 已知/未知错误分流打印。

    已知错误（InstallError / UninstallError）→ ``fail_key`` 文案（含 name/error）；
    其余异常 → ``error.unknown``（仅含 error）。

    Args:
        fail_key: 已知错误使用的 i18n key（如 ``"install.failed"``）。
        name: 软件显示名，用于已知错误文案。
        software/action/version: 遥测上下文（version 为 None 时不带）。
    """
    from software.base import InstallError, UninstallError

    ctx: dict = {"software": software, "action": action}
    if version is not None:
        ctx["version"] = version
    capture(exc, **ctx)

    if isinstance(exc, (InstallError, UninstallError)):
        print_error(t(fail_key, name=name, error=str(exc)))
    else:
        print_error(t("error.unknown", error=str(exc)))
