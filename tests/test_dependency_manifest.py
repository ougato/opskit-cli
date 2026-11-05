"""Source installs must supply the same runtime dependencies as packaged installs."""
from pathlib import Path
import builtins
import tomllib

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def test_requirements_match_project_runtime_dependencies():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [line.strip() for line in (root / "requirements.txt").read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    def normalized(items):
        result = {}
        for item in items:
            requirement = Requirement(item)
            result[canonicalize_name(requirement.name)] = (requirement.specifier, requirement.extras,
                                                         str(requirement.marker), requirement.url)
        assert len(result) == len(items)
        return result
    assert normalized(requirements) == normalized(project["project"]["dependencies"])


def test_missing_object_reader_has_dependency_diagnostic(monkeypatch):
    from core import plugin_runtime_inventory as inventory
    from core.native_sandbox import NativeSandboxError
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "dulwich":
            raise ModuleNotFoundError("synthetic missing dependency")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(NativeSandboxError) as error:
        inventory.native_fetch_snapshot()
    assert error.value.code == "fetch_dependency_missing"
