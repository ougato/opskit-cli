"""Installed tools are read as data, never executed or changed on the host."""
from pathlib import Path
import pytest
from core import local_toolchain as tools


def python_tree(root):
    root.mkdir()
    (root / "python.exe").write_bytes(b"fixture executable")
    (root / "python314.dll").write_bytes(b"fixture DLL")
    (root / "Lib/encodings").mkdir(parents=True)
    (root / "Lib/encodings/__init__.py").write_text("# fixture")
    return root


def test_python_snapshot_excludes_user_packages_and_resets_search_paths(tmp_path):
    source = python_tree(tmp_path / "installed")
    (source / "Lib/site-packages").mkdir()
    (source / "Lib/site-packages/private.py").write_text("private = True")
    (source / "python314._pth").write_text("C:/external/packages\nimport site\n")
    (source / "unicodedata.pyd").write_bytes(b"fixture extension")
    result = tools.snapshot(source, tmp_path / "snapshot", "python")
    assert not (result / "Lib/site-packages").exists()
    assert (result / "python314._pth").read_text() == "python314.zip\n.\nLib\nDLLs\n"
    assert (source / "python314._pth").read_text().startswith("C:/external")
    assert (result / "python314.dll").read_bytes() == b"fixture DLL"
    assert (result / "unicodedata.pyd").read_bytes() == b"fixture extension"
    import zipfile
    with zipfile.ZipFile(result / "python314.zip") as library:
        assert library.read("encodings/__init__.py") == b"# fixture"
    assert not (result / "Lib/encodings/__init__.py").exists()


def test_python_snapshot_rejects_an_incompatible_interpreter(tmp_path):
    source = python_tree(tmp_path / "installed")
    (source / "python314.dll").rename(source / "python313.dll")
    with pytest.raises(ValueError, match="incompatible"):
        tools.snapshot(source, tmp_path / "snapshot", "python")


def test_git_snapshot_copies_only_object_reader_and_libraries(tmp_path):
    source = tmp_path / "installed"
    source.mkdir()
    for name in ("git.exe", "dependency.dll", "git-credential.exe", "private.txt"):
        (source / name).write_bytes(b"fixture")
    result = tools.snapshot(source, tmp_path / "snapshot", "git")
    assert {p.name for p in result.iterdir()} == {"git.exe", "dependency.dll"}
    assert (source / "private.txt").exists()


def test_hardlinked_installed_tool_becomes_independent_copy(tmp_path):
    import os
    source = python_tree(tmp_path / "installed")
    original = source / "python.exe"
    os.link(original, tmp_path / "linked.exe")
    result = tools.snapshot(source, tmp_path / "snapshot", "python")
    assert (result / "python.exe").stat().st_nlink == 1
    assert not os.path.samefile(original, result / "python.exe")
    assert original.stat().st_nlink == 2


def test_git_discovery_prefers_real_binary_over_path_launcher(monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda name: "C:/Git/cmd/git.exe")
    roots = tools.git_candidates()
    assert roots[0] == Path("C:/Git/ucrt64/bin").absolute()
    assert roots[1] == Path("C:/Git/mingw64/bin").absolute()
    assert roots[-1] == Path("C:/Git/cmd").absolute()
