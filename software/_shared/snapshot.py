"""安装快照存储 — 多版本软件共享的 JSON 状态读写

各 recipe 此前在 common.py 中各自实现一份 load/save/delete_snapshot，
逻辑完全一致，仅快照文件名不同。此处抽出 SnapshotStore 统一封装：

    _store = SnapshotStore(SNAPSHOT_SUBDIR, SNAPSHOT_REDIS_FILE)
    _store.load() / _store.save(data) / _store.delete()

快照固定存放于用户私有目录 ``~/<subdir>/<filename>``（与 shim 脚本中
引用的路径契约一致），不随 OPSKIT_DATA_DIR 变化。
"""
from __future__ import annotations

import json
from pathlib import Path

from core.constants import SNAPSHOT_JSON_INDENT


class SnapshotStore:
    """单个软件的安装快照存储。

    Args:
        subdir: 相对 ``~`` 的子目录（如 ``.opskit/snapshots``）
        filename: 快照文件名（如 ``redis.json``）
    """

    __slots__ = ("_subdir", "_filename")

    def __init__(self, subdir: str, filename: str) -> None:
        self._subdir = subdir
        self._filename = filename

    @property
    def path(self) -> Path:
        return Path.home() / self._subdir / self._filename

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict:
        """读取快照，文件不存在或解析失败均返回空 dict。"""
        p = self.path
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, data: dict) -> None:
        """写入快照，自动创建父目录。"""
        p = self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(data, ensure_ascii=False, indent=SNAPSHOT_JSON_INDENT),
            encoding="utf-8",
        )

    def delete(self) -> None:
        """删除快照文件，不存在或删除失败均静默。"""
        p = self.path
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
