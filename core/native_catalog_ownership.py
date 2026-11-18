"""Creation receipts for catalog objects; absence never implies ownership."""
import hashlib
import json
import os
from pathlib import Path
import re

from core.plugin_json import loads
from core.plugin_runtime_inventory import _read_file
from core.plugin_state import _directory, _valid_identity
from core.secrets import atomic_write


def _paths(catalog, token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("invalid native catalog object")
    root = Path(catalog).absolute()
    for parent in (root, *root.parents):
        _directory(parent)
    return root / "objects" / token, root / "ownership" / (token + ".json")


def record_creation(catalog, token):
    """Call immediately after successful exclusive mkdir, under catalog lock."""
    target, receipt = _paths(catalog, token)
    for parent in (target, *target.parents):
        _directory(parent)
    try:
        _directory(receipt.parent)
    except FileNotFoundError:
        receipt.parent.mkdir()
        _directory(receipt.parent)
    value = {"schema_version": 1, "object": token, "parent": _directory(target.parent),
             "identity": _directory(target)}
    if os.path.lexists(receipt):
        raise ValueError("native catalog ownership already recorded")
    atomic_write(receipt, json.dumps(value, sort_keys=True))
    validate(catalog, token)


def read(catalog, token):
    target, receipt = _paths(catalog, token)
    raw = _read_file(receipt, Path(receipt.anchor), 4096)
    value = loads(raw.decode("utf-8"))
    if (not isinstance(value, dict) or set(value) != {"schema_version", "object", "parent", "identity"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["object"] != token or not _valid_identity(value["parent"])
            or not _valid_identity(value["identity"])):
        raise ValueError("invalid native catalog ownership receipt")
    return value, hashlib.sha256(raw).hexdigest()


def validate(catalog, token):
    target, _ = _paths(catalog, token)
    value, digest = read(catalog, token)
    for parent in (target, *target.parents):
        _directory(parent)
    if _directory(target) != value["identity"] or _directory(target.parent) != value["parent"]:
        raise ValueError("native catalog object identity changed")
    return value, digest
