"""NodeJS 配方包，对外暴露 NodeRecipe"""
from .recipe import NodeRecipe
from .common import (
    load_snapshot   as _load_snapshot,
    delete_snapshot as _delete_snapshot,
    node_versions_dir as _node_versions_dir,
    node_bin_dir    as _node_bin_dir,
    shim_dir        as _shim_dir,
)

__all__ = [
    "NodeRecipe",
    "_load_snapshot", "_delete_snapshot",
    "_node_versions_dir", "_node_bin_dir",
    "_shim_dir",
]
