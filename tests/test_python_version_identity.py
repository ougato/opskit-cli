"""Never publish a patch version that the interpreter does not provide."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import process
from software.base import InstallError
from software.recipes.python import recipe
from software.recipes.python.common import VersionEntry


@pytest.mark.parametrize("output,code,expected", [
    ("Python 3.12.13\n", 0, True),
    ("Python 3.12.9\n", 0, False),
    ("Python 3.12.130\n", 0, False),
    ("Python 3.12.13rc1\n", 0, False),
    ("Python 3.12.13\n", 1, False),
    ("unrelated Python 3.12.13\n", 0, False),
])
def test_exact_version_and_successful_exit_required(monkeypatch, output, code, expected):
    def capture(command, **options):
        assert options == dict(timeout=5, max_bytes=4096, stdin=subprocess.DEVNULL)
        return subprocess.CompletedProcess(command, code, output, "")
    monkeypatch.setattr(process, "capture", capture)
    assert recipe.PythonRecipe()._python_bin_matches("python", "3.12.13") is expected


def test_wrong_patch_rejected_before_any_activation_side_effect(monkeypatch):
    monkeypatch.setattr(process, "capture", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, "Python 3.12.9\n", ""))
    def forbidden(*args):
        pytest.fail("wrong patch version published activation state")
    monkeypatch.setattr(recipe, "save_snapshot", forbidden)
    driver = SimpleNamespace(apply_version_link=forbidden, install_shim=forbidden)
    with pytest.raises(InstallError):
        recipe.PythonRecipe()._activate_install("3.12.13", "python", {}, driver)


@pytest.mark.parametrize("actual,expected", [("3.12.9", None), ("3.12.13", "package-python")])
def test_package_manager_candidate_must_match_requested_patch(monkeypatch, actual, expected):
    from core import pkg_runner
    installer = recipe.PythonRecipe()
    monkeypatch.setattr(installer, "_version_entries", lambda: [VersionEntry("3.12.13", False)])
    monkeypatch.setattr(pkg_runner, "get_runner", lambda: SimpleNamespace(install_python3=lambda ver: "package-python"))
    monkeypatch.setattr(process, "capture", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, f"Python {actual}\n", ""))
    assert installer._install_with_package_manager("3.12.13", "3.12") == expected


def test_real_interpreter_exact_patch_identity():
    installer = recipe.PythonRecipe()
    version = ".".join(map(str, sys.version_info[:3]))
    wrong_patch = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro + 1}"
    assert installer._python_bin_matches(sys.executable, version)
    assert not installer._python_bin_matches(sys.executable, wrong_patch)
