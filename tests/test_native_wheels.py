import hashlib
import zipfile

import pytest

from core.native_wheels import install


def wheel(tmp_path, members=None, *, metadata="", tag="py3-none-any"):
    path = tmp_path / f"example-1.0-{tag}.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\n" + metadata)
        for name, content in (members or {"example/__init__.py": "VALUE=7"}).items():
            entry = zipfile.ZipInfo("placeholder")
            entry.filename = name
            archive.writestr(entry, content)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, "example==1.0 --hash=sha256:" + digest + "\n"


def test_offline_install_does_not_import_package(tmp_path):
    path, lock = wheel(tmp_path, {"example/__init__.py": "raise RuntimeError('must not run')"})
    target = tmp_path / "site"
    result = install([path], lock, target)
    assert result["example"]
    assert (target / "example/__init__.py").read_text().startswith("raise RuntimeError")


@pytest.mark.parametrize("name", ["../escape.py", "/absolute.py", "bad\\escape.py",
                                  "evil.pth", "example.data/scripts/run.exe", "NUL.txt",
                                  "package/file:stream", "package/file.", "bad?.py"])
def test_unsafe_members_rejected_before_writing(tmp_path, name):
    path, lock = wheel(tmp_path, {name: "bad"})
    target = tmp_path / "site"
    with pytest.raises(ValueError):
        install([path], lock, target)
    assert not target.exists()


def test_bad_hash_rejected_before_writing(tmp_path):
    path, lock = wheel(tmp_path)
    lock = "example==1.0 --hash=sha256:" + "0" * 64
    with pytest.raises(ValueError):
        install([path], lock, tmp_path / "site")


def test_file_directory_collision_rejected_before_writing(tmp_path):
    path, lock = wheel(tmp_path, {"package": "file", "package/module.py": "nested"})
    with pytest.raises(ValueError):
        install([path], lock, tmp_path / "site")
    assert not (tmp_path / "site").exists()


@pytest.mark.parametrize("metadata", ["Requires-Python: >=99\n", "Requires-Dist: missing>=1\n",
                                     "Requires-Dist: example>=2\n",
                                     "Requires-Dist: remote @ https://example.com/a.whl\n"])
def test_invalid_dependencies_rejected_before_writing(tmp_path, metadata):
    path, lock = wheel(tmp_path, metadata=metadata)
    with pytest.raises(ValueError):
        install([path], lock, tmp_path / "site")
    assert not (tmp_path / "site").exists()


def test_foreign_interpreter_wheel_rejected(tmp_path):
    path, lock = wheel(tmp_path, tag="cp99-cp99-win_arm64")
    with pytest.raises(ValueError):
        install([path], lock, tmp_path / "site")


def test_inactive_dependency_marker_not_required(tmp_path):
    path, lock = wheel(tmp_path, metadata='Requires-Dist: missing; python_version >= "99"\n')
    install([path], lock, tmp_path / "site")


@pytest.mark.parametrize("dependency", [
    'validate-pyproject[all]; extra == "dev"',
    'remote @ https://example.com/a.whl ; extra == "dev"',
])
def test_inactive_optional_dependencies_are_skipped(tmp_path, dependency):
    path, lock = wheel(tmp_path, metadata=f"Requires-Dist: {dependency}\n")
    install([path], lock, tmp_path / "site")


def test_active_extra_dependency_rejected_before_writing(tmp_path):
    path, lock = wheel(tmp_path, metadata="Requires-Dist: missing[all]\n")
    with pytest.raises(ValueError, match="extras dependencies"):
        install([path], lock, tmp_path / "site")
    assert not (tmp_path / "site").exists()
