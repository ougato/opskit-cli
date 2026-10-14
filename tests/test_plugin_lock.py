"""Lock grammar and genuine pip hash/transitive validation in private targets."""
import hashlib
import json
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest

from core import plugin_lock, plugin_runtime, plugin_sandbox


def entry(name, version="1.0", digest="a" * 64):
    return name + "==" + version + " --hash=sha256:" + digest + "\n"


@pytest.mark.parametrize("text", ["", "demo>=1 " + "--hash=sha256:" + "a" * 64,
    "demo==1", "demo==1 --hash=md5:abcd", "--index-url https://user:SECRET@example.invalid",
    "-r other.lock", "demo @ https://example.invalid/package.whl", "demo[extra]==1 " + "--hash=sha256:" + "a" * 64,
    entry("demo") + entry("DEMO"), entry("a-b") + entry("a_b"),
    entry("demo") + "demo==2 --trusted-host example.invalid", "demo==1 \\"])
def test_unsafe_or_incomplete_lock_syntax_rejected(text):
    with pytest.raises(ValueError):
        plugin_lock.parse_lock(text)


def test_canonical_lock_stable_across_comments_continuations_and_order():
    canonical, packages = plugin_lock.parse_lock("# reviewed\nPyYAML==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n" + entry("rich"))
    assert canonical == entry("pyyaml") + entry("rich")
    assert set(packages) == {"pyyaml", "rich"}


def test_many_platform_wheel_hashes_preserved_without_broadening_versions():
    hashes = [format(value, "064x") for value in range(80)]
    canonical, packages = plugin_lock.parse_lock("demo==1.0 " + " ".join("--hash=sha256:" + value for value in hashes))
    assert packages["demo"] == ("1.0", hashes)
    assert canonical.count("--hash=") == 80


@pytest.mark.parametrize("name,version", [("pip", "25.0.1"), ("PIP", "26.2")])
def test_plugin_lock_cannot_replace_platform_build_tool(tmp_path, name, version):
    lock = tmp_path / "requirements.lock"
    lock.write_text("".join(entry(package) for package in plugin_lock.RUNTIME_PACKAGES)
                    + entry(name, version), encoding="utf-8")
    with pytest.raises(ValueError, match="platform-owned build tool"):
        plugin_lock.read_lock(lock, [])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO lock safety")
def test_special_lock_rejected_without_blocking(tmp_path):
    import os
    lock = tmp_path / "pipe"
    os.mkfifo(lock)
    with pytest.raises(ValueError, match="ordinary file"):
        plugin_lock.read_lock(lock, [])


@pytest.mark.parametrize("case", ["missing", "unpinned", "wrong_requested", "linked", "oversize"])
def test_invalid_lock_rejected_before_docker(tmp_path, monkeypatch, case):
    lock = tmp_path / "requirements.lock"
    text = "".join(entry(name) for name in plugin_lock.RUNTIME_PACKAGES)
    lock.write_text(text, encoding="utf-8")
    if case == "unpinned":
        lock.write_text(entry("rich"), encoding="utf-8")
    if case == "oversize":
        lock.write_bytes(b"#" * (plugin_lock.LOCK_LIMIT + 1))
    if case == "linked":
        linked = tmp_path / "alias.lock"
        import os
        os.link(lock, linked)
        lock = linked
    def forbidden():
        pytest.fail("invalid lock must not invoke Docker")
    monkeypatch.setattr(plugin_runtime, "docker_command", forbidden)
    with pytest.raises(ValueError):
        plugin_runtime.build(tmp_path, ["rich==9"] if case == "wrong_requested" else [],
                             "python@sha256:" + "b" * 64, lock_file=None if case == "missing" else lock)


def wheel(directory, name, dependency=None):
    filename = directory / (name + "-1.0-py3-none-any.whl")
    metadata = "Metadata-Version: 2.1\nName: " + name + "\nVersion: 1.0\n"
    if dependency:
        metadata += "Requires-Dist: " + dependency + "==1.0\n"
    with zipfile.ZipFile(filename, "w") as archive:
        prefix = name + "-1.0.dist-info/"
        archive.writestr(name + "/__init__.py", "raise RuntimeError('INSTALL_MUST_NOT_IMPORT')\n")
        archive.writestr(prefix + "METADATA", metadata)
        archive.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(prefix + "RECORD", "")
    return hashlib.sha256(filename.read_bytes()).hexdigest()


@pytest.mark.parametrize("case", ["valid", "tampered", "missing_transitive"])
def test_real_pip_requires_exact_wheel_hashes_and_transitive_lock(tmp_path, case):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    child_hash = wheel(wheels, "opskit_lock_child")
    probe_hash = wheel(wheels, "opskit_lock_probe", "opskit_lock_child")
    text = entry("opskit_lock_probe", digest="0" * 64 if case == "tampered" else probe_hash)
    if case != "missing_transitive":
        text += entry("opskit_lock_child", digest=child_hash)
    canonical, _ = plugin_lock.parse_lock(text)
    lock = tmp_path / "requirements.lock"
    lock.write_text(canonical, encoding="utf-8")
    target = tmp_path / "installed"
    result = subprocess.run([sys.executable, "-m", "pip", "--isolated", "install", "--no-index", "--find-links", str(wheels),
        "--target", str(target), "--ignore-installed", "--require-hashes", "--only-binary=:all:",
        "--no-cache-dir", "--disable-pip-version-check", "-r", str(lock)], capture_output=True, text=True, timeout=20)
    assert (result.returncode == 0) is (case == "valid"), result.stdout + result.stderr
    assert "INSTALL_MUST_NOT_IMPORT" not in result.stdout + result.stderr
    assert (target / "opskit_lock_probe").exists() is (case == "valid")


@pytest.mark.parametrize("case", ["valid", "legacy", "schema1", "wrong_code", "wrong_code_label", "wrong_hash", "wrong_base", "wrong_policy", "invalid_image", "wrong_id", "missing_label", "malformed", "object", "short"])
def test_execution_requires_bound_lock_base_and_runtime_policy(tmp_path, monkeypatch, case):
    image = "sha256:" + "b" * 64
    from core.plugin_runtime_inventory import RUNTIME_POLICY, code_digest
    code = code_digest()
    record = {"schema_version": 2, "image": image, "fingerprint": "c" * 64,
              "requirements_sha256": "d" * 64, "base_reference": "python@sha256:" + "e" * 64,
              "code_sha256": "f" * 64 if case == "wrong_code" else code}
    if case == "legacy":
        record.pop("schema_version")
    if case == "schema1":
        record["schema_version"] = 1
    if case == "invalid_image":
        record["image"] = 42
    catalogs = tmp_path / "plugin-runtimes"
    catalogs.mkdir()
    (catalogs / "demo.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(plugin_sandbox, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin_sandbox, "compute_fingerprint", lambda path: "c" * 64)
    calls = []
    def inspect(command, *args):
        calls.append(args)
        assert args[:3] == ("image", "inspect", "--format")
        assert args[3].startswith("[{{json .Id}},") and args[-1] == image
        assert all(label in args[3] for label in ("org.opskit.sandbox", "org.opskit.lock-sha256", "org.opskit.base-digest", "org.opskit.code-sha256"))
        if case in {"malformed", "object", "short"}:
            return {"malformed": "not-json", "object": "{}", "short": "[]"}[case]
        return json.dumps([
            "sha256:" + "a" * 64 if case == "wrong_id" else image,
            str(RUNTIME_POLICY - 1) if case == "wrong_policy" else str(RUNTIME_POLICY),
            None if case == "missing_label" else "f" * 64 if case == "wrong_hash" else "d" * 64,
            "sha256:" + ("f" if case == "wrong_base" else "e") * 64,
            "f" * 64 if case == "wrong_code_label" else code])
    monkeypatch.setattr(plugin_sandbox, "_control", inspect)
    if case == "valid":
        assert plugin_sandbox._image(SimpleNamespace(name="demo", root=tmp_path), ["docker"]) == image
        assert len(calls) == 1
    else:
        with pytest.raises(plugin_sandbox.SandboxError):
            plugin_sandbox._image(SimpleNamespace(name="demo", root=tmp_path), ["docker"])


@pytest.mark.parametrize("case", ["hardlink", "oversize"])
def test_catalog_nonordinary_or_oversize_rejected_without_docker(tmp_path, monkeypatch, case):
    catalogs = tmp_path / "plugin-runtimes"
    catalogs.mkdir()
    catalog = catalogs / "demo.json"
    catalog.write_text("{}", encoding="utf-8")
    if case == "hardlink":
        import os
        os.link(catalog, tmp_path / "alias")
    else:
        catalog.write_bytes(b" " * 16385)
    monkeypatch.setattr(plugin_sandbox, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin_sandbox, "_control", lambda *args: pytest.fail("invalid catalog reached Docker"))
    with pytest.raises(plugin_sandbox.SandboxError):
        plugin_sandbox._image(SimpleNamespace(name="demo", root=tmp_path), ["docker"])
