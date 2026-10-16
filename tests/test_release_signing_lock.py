"""The CPython 3.12 signer uses only the reviewed wheel subset of uv.lock."""
from pathlib import Path
import re

from packaging.requirements import Requirement

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def test_signer_lock_matches_project_lock_wheels():
    root = Path(__file__).resolve().parents[1]
    source = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    lock = (root / "requirements-release-signing.txt").read_text(encoding="utf-8")
    entries = [line.strip() for line in lock.replace("\\\n", " ").splitlines()
               if line.strip() and not line.startswith("#")]
    names = set()
    for entry in entries:
        match = re.fullmatch(r"([a-z-]+)==([0-9.]+)\s+((?:--hash=sha256:[0-9a-f]{64}\s*)+)", entry)
        assert match, entry
        name, version, hashes = match.groups()
        assert name not in names
        names.add(name)
        package = next(p for p in source["package"] if p["name"] == name)
        assert version == package["version"]
        assert set(re.findall(r"sha256:[0-9a-f]{64}", hashes)) == {w["hash"] for w in package["wheels"]}
    assert names == {"cryptography", "cffi", "pycparser"}


def test_build_exports_match_lock_and_include_dependency_closure():
    root = Path(__file__).resolve().parents[1]
    source = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = source["package"]
    project = next(p for p in packages if p["name"] == "opskit")
    def dependency_closure(roots):
        pending = [d["name"] for d in roots]
        closure = set()
        while pending:
            name = pending.pop()
            if name in closure:
                continue
            closure.add(name)
            for package in packages:
                if package["name"] == name:
                    pending.extend(d["name"] for d in package.get("dependencies", []))
        return closure

    build_roots = project["dependencies"] + project["dev-dependencies"]["release-build"]
    closure = dependency_closure(build_roots)
    ci_closure = dependency_closure(build_roots + project["optional-dependencies"]["dev"]) - {"nuitka"}
    for filename, expected in (("requirements-release-build.txt", closure),
                               ("requirements-ci.txt", ci_closure),
                               ("requirements-runtime.txt", dependency_closure(project["dependencies"])),
                               ("requirements-release-bootstrap.txt", {"setuptools", "wheel", "packaging"}),
                               ("requirements-installer.txt", dependency_closure(project["dev-dependencies"]["installer"]))):
        text = (root / filename).read_text(encoding="utf-8").replace("\\\n", " ")
        names = set()
        identities = set()
        for line in text.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            requirement = Requirement(line.split("--hash=", 1)[0].strip())
            specifiers = list(requirement.specifier)
            assert len(specifiers) == 1 and specifiers[0].operator == "=="
            identity = (requirement.name, specifiers[0].version)
            assert identity not in identities
            identities.add(identity)
            names.add(requirement.name)
            package = next(p for p in packages if (p["name"], p["version"]) == identity)
            files = package.get("wheels", []) + ([package["sdist"]] if "sdist" in package else [])
            assert set(re.findall(r"sha256:[0-9a-f]{64}", line)) == {f["hash"] for f in files}
        assert names == expected
