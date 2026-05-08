"""Golang 配方包，对外暴露 GoRecipe"""
from .recipe import GoRecipe
from .common import (
    load_snapshot   as _load_snapshot,
    delete_snapshot as _delete_snapshot,
    go_versions_dir as _go_versions_dir,
    go_bin_dir      as _go_bin_dir,
    shim_dir        as _shim_dir,
)

__all__ = [
    "GoRecipe",
    "_load_snapshot", "_delete_snapshot",
    "_go_versions_dir", "_go_bin_dir",
    "_shim_dir",
]
