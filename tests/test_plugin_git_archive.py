"""Shared Git archive validation is independent of backend and checkout hooks."""
import sys
from types import SimpleNamespace

import pytest

from core import plugin_git_archive as worker, plugin_runtime_inventory as inventory


def record(name, mode=b"100644", kind=b"blob", oid=b"a" * 40, size=b"3"):
    return mode + b" " + kind + b" " + oid + b" " + size + b"\t" + name + b"\0"


@pytest.mark.parametrize("name", [b"/escape", b"../escape", b"a/../b", b"a//b", b"a/./b", b"a\\b",
                                  b".git/config", b".GIT/config", b"x:stream", b"a\nlog", b"a\x00b",
                                  b"con.txt", b"LPT9", b"git~1/config", b"a.", b"a ", b"\xff",
                                  b"a/", "COM¹.txt".encode(), "a\u200c".encode()])
def test_unsafe_cross_host_names_refused(name):
    with pytest.raises((UnicodeError, ValueError)):
        worker._path(name)


@pytest.mark.parametrize("raw", [record(b"link", mode=b"120000"), record(b"module", mode=b"160000", kind=b"commit"),
                                 record(b"x", oid=b"bad"), record(b"x", size=b"-1"), record(b"x")[:-1],
                                 record(b"x") + record(b"X"), record(b"x") + record(b"x/y"),
                                 record(b"x/y") + record(b"x"), record(b"X/a") + record(b"x/b"),
                                 record("é".encode()) + record("e\u0301".encode())])
def test_unsupported_or_colliding_tree_refused(raw):
    with pytest.raises(ValueError):
        worker._records(raw)


def test_raw_tree_accepts_attributes_and_unicode_without_transforming():
    raw = record(b".gitattributes") + record("src/猫.py".encode(), mode=b"100755", oid=b"b" * 64)
    assert worker._records(raw) == [(".gitattributes", 0o644, b"a" * 40, 3), ("src/猫.py", 0o755, b"b" * 64, 3)]


@pytest.mark.parametrize("limit", ["MAX_FILE_BYTES", "MAX_TREE_BYTES", "MAX_ENTRIES", "MAX_INDEX_BYTES"])
def test_guest_budgets_enforced(monkeypatch, limit):
    monkeypatch.setattr(worker, limit, 1)
    with pytest.raises(ValueError):
        worker._records(record(b"a") + record(b"b"))


def test_directory_nodes_consume_entry_budget(monkeypatch):
    monkeypatch.setattr(worker, "MAX_ENTRIES", 2)
    with pytest.raises(ValueError, match="budget"):
        worker._records(record(b"a/b/file"))


def test_fetch_worker_does_not_launch_a_git_subprocess(monkeypatch, tmp_path):
    from core import native_fetch_worker as native
    assert not hasattr(native, "subprocess")
    assert not hasattr(native, "_run")


def test_duplicate_request_fields_refused():
    with pytest.raises(ValueError, match="duplicate"):
        worker._object([("branch", None), ("branch", "main")])


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_dulwich_indexes_real_git_packs_without_git_runtime(tmp_path, object_format, monkeypatch):
    import subprocess
    import shutil
    from core import native_fetch_worker as native
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="win32"))

    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is needed only to produce a pack fixture")
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run([git, "-C", str(repo), "init", "-q", "--object-format=" + object_format], check=True)
    (repo / "plugin.yaml").write_text("name: pack_fixture\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "plugin.yaml"], check=True)
    subprocess.run([git, "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@test.invalid",
                    "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output([git, "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    packed = tmp_path / "packed"
    packed.mkdir()
    (packed / "objects.pack").write_bytes(subprocess.check_output(
        [git, "-C", str(repo), "pack-objects", "--stdout", "--all"]))
    work = tmp_path / "work"
    work.mkdir()
    repository = work / "repository"
    (repository / "objects" / "pack").mkdir(parents=True)
    (repository / "objects" / "info").mkdir()
    (repository / "refs" / "heads").mkdir(parents=True)
    (repository / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
    if object_format == "sha256":
        (repository / "config").write_text(
            "[core]\n bare = true\n repositoryformatversion = 1\n"
            "[extensions]\n objectFormat = sha256\n", encoding="ascii")
    else:
        (repository / "config").write_text(
            "[core]\n bare = true\n repositoryformatversion = 0\n", encoding="ascii")
    metadata, archive = native.fetch_pack(packed, work, {"commit": commit, "shallow": []})
    assert metadata["commit"] == commit
    assert metadata["entries"] == 1
    assert archive.is_file()


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_dulwich_reads_committed_local_repository_without_checkout(tmp_path, object_format, monkeypatch):
    import subprocess
    import shutil
    from core import native_fetch_worker as native
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="win32"))

    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is needed only to produce a repository fixture")
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run([git, "-C", str(repo), "init", "-q", "--object-format=" + object_format], check=True)
    (repo / "file.txt").write_text("committed\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run([git, "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@test.invalid",
                    "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output([git, "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    (repo / "file.txt").write_text("uncommitted\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    metadata, archive = native.fetch_local(repo, work)
    assert metadata["commit"] == commit
    assert archive.is_file()


def test_worker_bundled_independently_of_sdk_and_gateway():
    sources = inventory.native_fetch_snapshot()
    assert {"core/" + name for name in inventory.NATIVE_FETCH_FILES} <= set(sources)
    assert "core/dulwich/__init__.py" in sources
    if sys.version_info < (3, 15):
        assert "core/typing_extensions.py" in sources
    bundled = inventory.bundle_snapshot()
    sdk = inventory.snapshot()
    for name, source in sources.items():
        assert source == bundled[name]
        if name != "core/__init__.py":
            assert name not in sdk
    assert "core/native_git_ssh.py" not in inventory.snapshot()


def test_unsupported_platform_does_not_execute_git(monkeypatch, tmp_path):
    from core import native_fetch_worker as native
    monkeypatch.setattr(native.sys, "platform", "linux")
    with pytest.raises(ValueError, match="platform pending"):
        native.fetch_local(tmp_path / "source", tmp_path)
