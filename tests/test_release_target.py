"""Execute trusted release tools against a separate, untrusted data checkout."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), "-c", "user.name=Acceptance",
                             "-c", "user.email=acceptance@example.invalid", *args],
                            capture_output=True, text=True, timeout=15)
    if check:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def target(tmp_path):
    root = tmp_path / "branch"
    (root / "core").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "core/constants.py").write_text(
        'APP_VERSION = 2\nUPDATE_SIGNING_PUBLIC_KEY = "old"\n', encoding="utf-8")
    (root / "bootstrap.json").write_text(json.dumps({
        "latest_build": 2, "min_build": 1, "rollout": 100}), encoding="utf-8")
    (root / "install.sh").write_text('RELEASE_PUBLIC_KEY="old"\n', encoding="utf-8")
    (root / "install.ps1").write_text('$RELEASE_PUBLIC_KEY = "old"\n', encoding="utf-8")
    for name in ("release_bump.py", "pin_release_key.py"):
        (root / "scripts" / name).write_text('raise RuntimeError("branch code executed")\n')
    return root


def run_tool(target, script, *args):
    env = dict(os.environ, OPSKIT_RELEASE_PUBLIC_KEY=base64.b64encode(bytes(range(32))).decode())
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / script), *args,
                           "--root", str(target)], cwd=target, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=15)


def test_separate_checkout_updated_without_executing_branch_code(target):
    original = {name: (SCRIPTS / name).read_bytes() for name in
                ("release_bump.py", "pin_release_key.py")}
    for script, args in (("pin_release_key.py", ()), ("release_bump.py", ("v7",)),
                         ("release_bump.py", ("v7", "--check"))):
        result = run_tool(target, script, *args)
        assert result.returncode == 0, result.stderr
    assert json.loads((target / "bootstrap.json").read_text())["latest_build"] == 7
    key = base64.b64encode(bytes(range(32))).decode()
    for name in ("core/constants.py", "install.sh", "install.ps1"):
        assert key in (target / name).read_text()
    assert "APP_VERSION = 7" in (target / "core/constants.py").read_text()
    for name, content in original.items():
        assert (SCRIPTS / name).read_bytes() == content


@pytest.mark.parametrize("script,args,name", [
    ("release_bump.py", ("v7",), "bootstrap.json"),
    ("pin_release_key.py", (), "install.ps1"),
])
def test_linked_target_rejected_before_any_write(target, tmp_path, script, args, name):
    before = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    outside = tmp_path / "outside"
    outside.write_bytes(before[target / name])
    (target / name).unlink()
    os.link(outside, target / name)
    result = run_tool(target, script, *args)
    assert result.returncode != 0
    assert "unlinked regular file" in result.stderr
    assert outside.read_bytes() == before[target / name]
    for path, content in before.items():
        assert path.read_bytes() == content


def test_bootstrap_commit_preserves_latest_branch_parent(target):
    git(target, "init", "--quiet")
    git(target, "add", ".")
    git(target, "commit", "--quiet", "-m", "tag-era baseline")
    (target / "later-change.txt").write_text("newer default branch change\n")
    git(target, "add", ".")
    git(target, "commit", "--quiet", "-m", "default branch advanced")
    parent = git(target, "rev-parse", "HEAD").stdout.strip()
    result = run_tool(target, "release_bump.py", "v7")
    assert result.returncode == 0, result.stderr
    git(target, "add", "core/constants.py", "bootstrap.json")
    git(target, "commit", "--quiet", "-m", "publish bootstrap")
    assert git(target, "rev-parse", "HEAD^").stdout.strip() == parent
    assert (target / "later-change.txt").read_text() == "newer default branch change\n"
    assert set(git(target, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines()) == {
        "core/constants.py", "bootstrap.json"}


@pytest.mark.parametrize("script,args,name", [
    ("release_bump.py", ("v7",), "bootstrap.json"),
    ("pin_release_key.py", (), "install.ps1"),
])
def test_symlink_target_cannot_escape_checkout(target, tmp_path, script, args, name):
    outside = tmp_path / "outside"
    destination = target / name
    content = destination.read_bytes()
    outside.write_bytes(content)
    destination.unlink()
    try:
        destination.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    result = run_tool(target, script, *args)
    assert result.returncode != 0
    assert "unlinked regular file" in result.stderr
    assert outside.read_bytes() == content


@pytest.mark.parametrize("version,latest", [(8, 8), (8, 2), (2, 8)])
def test_old_tag_cannot_lower_either_version_source(target, version, latest):
    constants = target / "core/constants.py"
    constants.write_text(f'APP_VERSION = {version}\nUPDATE_SIGNING_PUBLIC_KEY = "old"\n')
    bootstrap = target / "bootstrap.json"
    data = json.loads(bootstrap.read_text())
    data["latest_build"] = latest
    bootstrap.write_text(json.dumps(data))
    before = {p: p.read_bytes() for p in (constants, bootstrap)}
    result = run_tool(target, "release_bump.py", "v7")
    assert result.returncode == 2
    assert "refusing release downgrade" in result.stderr
    assert all(p.read_bytes() == content for p, content in before.items())


def test_preflight_is_read_only_and_same_build_retry_allowed(target):
    before = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    result = run_tool(target, "release_bump.py", "v7", "--preflight")
    assert result.returncode == 0, result.stderr
    assert all(p.read_bytes() == content for p, content in before.items())
    for _ in range(2):
        result = run_tool(target, "release_bump.py", "v7")
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field,value", [
    ("latest_build", "8"), ("latest_build", True), ("min_build", 8),
    ("rollout", 101), ("rollout", True),
])
def test_invalid_manifest_refused_before_version_write(target, field, value):
    manifest = target / "bootstrap.json"
    data = json.loads(manifest.read_text())
    data[field] = value
    manifest.write_text(json.dumps(data))
    constants = target / "core/constants.py"
    before = (constants.read_bytes(), manifest.read_bytes())
    result = run_tool(target, "release_bump.py", "v7")
    assert result.returncode == 2
    assert (constants.read_bytes(), manifest.read_bytes()) == before


def test_stale_publisher_cannot_overwrite_newer_remote(target, tmp_path):
    """Local bare remote exercises the workflow's ordinary non-force push."""
    remote = tmp_path / "remote.git"
    stale = tmp_path / "stale"
    refreshed = tmp_path / "refreshed"
    git(target, "init", "--quiet")
    git(target, "add", ".")
    git(target, "commit", "--quiet", "-m", "initial")
    git(target, "init", "--bare", "--quiet", str(remote))
    git(target, "remote", "add", "origin", str(remote))
    git(target, "push", "origin", "HEAD:refs/heads/develop")
    git(target, "clone", "--quiet", "--branch", "develop", str(remote), str(stale))
    for root, tag in ((target, "v8"), (stale, "v7")):
        result = run_tool(root, "release_bump.py", tag)
        assert result.returncode == 0, result.stderr
        git(root, "add", "core/constants.py", "bootstrap.json")
        git(root, "commit", "--quiet", "-m", tag)
    git(target, "push", "origin", "HEAD:refs/heads/develop")
    rejected = git(stale, "push", "origin", "HEAD:refs/heads/develop", check=False)
    assert rejected.returncode != 0
    assert "rejected" in rejected.stderr
    git(target, "clone", "--quiet", "--branch", "develop", str(remote), str(refreshed))
    before = (refreshed / "bootstrap.json").read_bytes()
    result = run_tool(refreshed, "release_bump.py", "v7", "--preflight")
    assert result.returncode == 2
    assert "refusing release downgrade" in result.stderr
    assert (refreshed / "bootstrap.json").read_bytes() == before
    assert json.loads(before)["latest_build"] == 8
