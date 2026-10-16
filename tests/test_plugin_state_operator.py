"""Real supervised operator operations use platform-selected partitions."""
import hashlib
import json

import pytest

from core import paths, plugin_state as state
from core.plugin_state_operator import state_operation


def create(caller=None):
    owner = paths.data_dir() / "plugin-data" / "provider"
    root = owner / "self" if caller is None else owner / "callers" / caller
    root.mkdir(parents=True)
    (root / "value").write_bytes(b"old")
    candidate = root.parent / ".state-owned" / "new"
    candidate.mkdir(parents=True)
    (candidate / "value").write_bytes(b"new")
    state.prepare_record(root, candidate, plugin="provider", caller=caller or "platform")
    path = state.record_path(root)
    raw = path.read_bytes() + b'{'
    path.write_bytes(raw)
    return root, raw


def test_inspect_missing_home_does_not_create_it(tmp_path, monkeypatch):
    home = tmp_path / "absent"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(home))
    assert state_operation("provider", timeout=30) == {"plugin": "provider", "caller": None, "status": "clean"}
    assert not home.exists()


@pytest.mark.parametrize("caller", [None, "platform", "consumer"])
def test_real_worker_inspects_and_repairs_only_selected_partition(caller):
    root, raw = create(caller)
    result = state_operation("provider", caller=caller, timeout=30)
    assert result["status"] == "repairable-tail"
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    repaired = state_operation("provider", caller=caller, digest=result["sha256"], timeout=30)
    assert repaired["status"] == "aborted"
    assert (root / "value").read_bytes() == b"old"
    evidence = root.with_name(root.name + ".repair-" + repaired["evidence"] + ".evidence")
    assert evidence.read_bytes() == raw


def test_explicit_platform_caller_does_not_inspect_self():
    root, raw = create()
    assert state_operation("provider", caller="platform", timeout=30)["status"] == "clean"
    assert state.record_path(root).read_bytes() == raw


def invoke(arguments):
    import main
    from core.cli import configure
    from typer.testing import CliRunner
    try:
        return CliRunner().invoke(main.app, ["--output", "json", "plugin", *arguments])
    finally:
        configure("text", False)


def test_real_cli_diagnoses_and_repairs_without_plugin_manifest():
    root, raw = create()
    result = invoke(["inspect-state", "provider"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["status"] == "repairable-tail"
    result = invoke(["repair-state", "provider", "--record-sha256", data["sha256"]])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["status"] == "aborted"
    assert (root / "value").read_bytes() == b"old"
    assert list(root.parent.glob("self.repair-*.evidence"))[0].read_bytes() == raw


@pytest.mark.parametrize("arguments", [["inspect-state", "../bad"],
                                        ["inspect-state", "provider", "--caller", "../bad"],
                                        ["repair-state", "provider", "--record-sha256", "BAD"]])
def test_cli_invalid_bindings_and_hash_are_usage_errors(arguments):
    assert invoke(arguments).exit_code == 2


@pytest.mark.parametrize("error,code", [(OSError("/private/credential"), 1),
                                       (KeyboardInterrupt(), 130)])
def test_cli_errors_are_sanitized(monkeypatch, error, code):
    from core import plugin_state_operator

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(plugin_state_operator, "state_operation", fail)
    result = invoke(["inspect-state", "provider"])
    assert result.exit_code == code
    assert "credential" not in result.output
