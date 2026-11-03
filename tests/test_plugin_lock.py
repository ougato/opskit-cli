"""Lock grammar and native offline hash/transitive validation in private targets."""
import hashlib
import sys
import zipfile

import pytest

from core import plugin_lock


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
def test_invalid_lock_rejected_before_preparation(tmp_path, case):
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
    with pytest.raises(ValueError):
        plugin_lock.read_lock(None if case == "missing" else lock,
                              ["rich==9"] if case == "wrong_requested" else [])


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
def test_native_install_requires_exact_wheel_hashes_and_transitive_lock(tmp_path, case):
    from core.native_wheels import install
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    child_hash = wheel(wheels, "opskit_lock_child")
    probe_hash = wheel(wheels, "opskit_lock_probe", "opskit_lock_child")
    text = entry("opskit_lock_probe", digest="0" * 64 if case == "tampered" else probe_hash)
    if case != "missing_transitive":
        text += entry("opskit_lock_child", digest=child_hash)
    canonical, _ = plugin_lock.parse_lock(text)
    target = tmp_path / "installed"
    # Missing transitive case deliberately selects only the requested wheel,
    # forcing metadata dependency validation rather than selection-count failure.
    selected = sorted(wheels.glob("*.whl"))
    if case == "missing_transitive":
        selected = [path for path in selected if "probe" in path.name]
    if case == "valid":
        install(selected, canonical, target)
        assert "INSTALL_MUST_NOT_IMPORT" in (target / "opskit_lock_probe/__init__.py").read_text()
        assert (target / "opskit_lock_child/__init__.py").is_file()
    else:
        message = "dependency lock" if case == "tampered" else "transitive dependency"
        with pytest.raises(ValueError, match=message):
            install(selected, canonical, target)
        assert not target.exists()
