"""Reviewed synthetic Python/Git inputs shared by native integration tests."""
import os
from pathlib import Path
import shutil
import sys

import pytest


def copy_native_tools(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("native Windows acquisition; other backends are pending")
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git for Windows test input not installed")
    binary = Path(git).parents[1] / "mingw64" / "bin"
    if not (binary / "git.exe").is_file():
        pytest.skip("Git for Windows mingw64 test input not installed")
    root = tmp_path_factory.mktemp("native-fetch-tools")
    python = root / "python"
    python.mkdir()
    original = Path(sys.base_prefix)
    for path in original.iterdir():
        if path.is_file() and (path.suffix.lower() == ".dll" or path.name == "python.exe"):
            shutil.copyfile(path, python / path.name)
    shutil.copytree(original / "Lib", python / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip"))
    shutil.copytree(original / "DLLs", python / "DLLs", ignore=shutil.ignore_patterns("*.pdb"))
    private_git = root / "git"
    private_git.mkdir()
    for path in binary.iterdir():
        if path.suffix.lower() == ".dll" or path.name == "git.exe":
            shutil.copyfile(path, private_git / path.name)
    return python, private_git, git



def populate_synthetic(source, destination, **kwargs):
    """Known fixture payload for publication tests; does not test acquisition."""
    from core.native_acquisition import Acquisition, source_identity
    from core.plugin_install_receipt import content_receipt
    from core.plugin_trust import compute_fingerprint
    canonical, local = source_identity(source)
    assert local, "synthetic publication fixture accepts local input only"
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))
    return Acquisition(canonical, "a" * 40, compute_fingerprint(destination), content_receipt(destination))
