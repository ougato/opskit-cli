"""Python 配方包，对外暴露 PythonRecipe 及向后兼容别名"""
from .recipe import PythonRecipe
from .common import (
    load_snapshot   as _load_snapshot,
    delete_snapshot as _delete_snapshot,
    uv_python_dir   as _uv_python_dir,
    find_uv_python  as _find_uv_python,
    shim_dir        as _shim_dir,
)

def _symlink_target():
    """向后兼容：Linux/macOS 返回 (symlink_path, has_root)，Windows 返回 ('', False)"""
    import sys
    if sys.platform == "win32":
        return "", False
    from .linux import LinuxDriver
    return LinuxDriver()._symlink_target()

def _ensure_uv() -> str:
    """向后兼容：调用当前平台驱动的 ensure_uv"""
    from .driver import get_driver
    return get_driver().ensure_uv()

__all__ = [
    "PythonRecipe",
    "_load_snapshot", "_delete_snapshot",
    "_uv_python_dir", "_find_uv_python",
    "_shim_dir", "_symlink_target", "_ensure_uv",
]
