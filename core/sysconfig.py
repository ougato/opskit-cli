"""系统配置安装快照与智能还原管理器"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


def _snapshot_path() -> Path:
    from core.config import get_data_dir
    from core.constants import DIR_DATA, FILE_INSTALL_SNAPSHOTS
    p = get_data_dir() / DIR_DATA / FILE_INSTALL_SNAPSHOTS
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    p = _snapshot_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def _dump(data: dict) -> None:
    p = _snapshot_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _read_sysparam(param: str) -> str:
    """读取 sysctl 参数当前运行时值"""
    try:
        r = subprocess.run(
            ["sysctl", "-n", param],
            capture_output=True, text=True, check=False,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _write_sysparam(param: str, value: str) -> None:
    """写入 sysctl 参数运行时值"""
    try:
        subprocess.run(
            ["sysctl", "-w", f"{param}={value}"],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        pass


class SysConfigManager:
    """系统配置安装快照与智能还原管理器。

    安装时调用 save() 记录快照，卸载时调用 restore() 智能还原，
    完成后调用 remove() 清除快照。支持多软件引用计数，不会互相干扰。
    """

    @staticmethod
    def save(
        recipe_key: str,
        sysparams: dict[str, str] | None = None,
        pre_install: dict[str, Any] | None = None,
    ) -> None:
        """安装前调用：记录 sysctl 参数原值快照和 pre_install 状态。

        sysparams: {参数名: opskit 要写入的值}
        pre_install: 其他安装前状态（文件是否存在等）
        """
        data = _load()
        entry = data.get(recipe_key, {})

        if entry.get("status") == "installed":
            entry["installed_at"] = datetime.now().isoformat(timespec="seconds")
        else:
            entry = {
                "status": "installing",
                "installed_at": datetime.now().isoformat(timespec="seconds"),
                "sysparams": {},
                "pre_install": {},
            }

        if sysparams:
            for param, opskit_value in sysparams.items():
                if param not in entry["sysparams"]:
                    original = _read_sysparam(param)
                    entry["sysparams"][param] = {
                        "original": original,
                        "opskit_value": opskit_value,
                    }

        if pre_install:
            entry["pre_install"].update(pre_install)

        data[recipe_key] = entry
        _dump(data)

    @staticmethod
    def mark_installed(recipe_key: str) -> None:
        """安装成功后调用：将状态从 installing 改为 installed"""
        data = _load()
        if recipe_key in data:
            data[recipe_key]["status"] = "installed"
            _dump(data)

    @staticmethod
    def restore(recipe_key: str) -> dict[str, Any]:
        """卸载时调用：对 sysctl 参数执行智能还原，返回 pre_install dict。

        还原规则：
        - 当前值 == opskit_value（用户未改）且无其他软件持有 → 还原为 original
        - 当前值 != opskit_value（用户手动改过）→ 不干预
        - 其他软件仍持有该参数 → 不还原
        """
        data = _load()
        entry = data.get(recipe_key, {})
        sysparams = entry.get("sysparams", {})

        for param, info in sysparams.items():
            original = info.get("original", "")
            opskit_value = info.get("opskit_value", "")

            holders = [
                k for k, v in data.items()
                if k != recipe_key
                and v.get("status") == "installed"
                and param in v.get("sysparams", {})
            ]

            if holders:
                continue

            current = _read_sysparam(param)
            if current == opskit_value and original != "":
                _write_sysparam(param, original)

        return entry.get("pre_install", {})

    @staticmethod
    def remove(recipe_key: str) -> None:
        """卸载完成后调用：清除该 recipe 的快照数据"""
        data = _load()
        data.pop(recipe_key, None)
        _dump(data)

    @staticmethod
    def get_pre_install(recipe_key: str) -> dict[str, Any]:
        """读取安装前快照数据"""
        data = _load()
        return data.get(recipe_key, {}).get("pre_install", {})
