"""Guest tree validation is independent of checkout attributes and host paths."""
import pytest

from core import plugin_fetch_worker as worker, plugin_runtime_inventory as inventory


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


def test_environment_does_not_inherit_host_config_credentials_or_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.sshCommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "fixture-private")
    monkeypatch.setenv("LD_PRELOAD", "fixture-private")
    environment = worker._environment(tmp_path)
    assert "fixture-private" not in repr(environment)
    assert "GIT_CONFIG_COUNT" not in environment and "LD_PRELOAD" not in environment
    assert environment["GIT_CONFIG_GLOBAL"] == str(tmp_path / "home" / "gitconfig")


def test_duplicate_request_fields_refused():
    with pytest.raises(ValueError, match="duplicate"):
        worker._object([("branch", None), ("branch", "main")])


def test_worker_bundled_independently_of_sdk_and_gateway():
    source = inventory.fetch_source()
    assert source == inventory.bundle_snapshot()["core/plugin_fetch_worker.py"]
    assert "core/plugin_fetch_worker.py" not in inventory.snapshot()
    assert source != inventory.gateway_source()


def test_host_platform_cannot_be_used_as_guest(monkeypatch, tmp_path):
    monkeypatch.setattr(worker.sys, "platform", "win32")
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("host execution"))
    with pytest.raises(ValueError, match="Linux isolation"):
        worker.fetch_local(tmp_path / "source", tmp_path)
