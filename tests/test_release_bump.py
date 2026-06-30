"""scripts/release_bump.py 单元测试。"""
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "release_bump", Path(__file__).resolve().parent.parent / "scripts" / "release_bump.py")
rb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rb)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    constants = tmp_path / "constants.py"
    constants.write_text(
        "from __future__ import annotations\nAPP_NAME = \"OpsKit\"\nAPP_VERSION = 2\n",
        encoding="utf-8")
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(json.dumps({
        "schema_version": 2, "channel": "stable", "latest_build": 2, "display": "v2",
        "min_build": 1, "rollout": 100, "kill_switch": False, "notes": "",
    }), encoding="utf-8")
    monkeypatch.setattr(rb, "CONSTANTS", constants)
    monkeypatch.setattr(rb, "BOOTSTRAP", bootstrap)
    return constants, bootstrap


@pytest.mark.parametrize("raw,expected", [("v7", 7), ("7", 7), ("v0", 0), (" v42 ", 42)])
def test_parse_build_ok(raw, expected):
    assert rb.parse_build(raw) == expected


@pytest.mark.parametrize("raw", ["v1.2.3", "abc", "", "v", "1.0"])
def test_parse_build_bad(raw):
    with pytest.raises(ValueError):
        rb.parse_build(raw)


def test_write_app_version(sandbox):
    constants, _ = sandbox
    rb.write_app_version(7)
    assert rb.read_app_version() == 7
    assert "APP_VERSION = 7" in constants.read_text(encoding="utf-8")
    # 不破坏其它行
    assert 'APP_NAME = "OpsKit"' in constants.read_text(encoding="utf-8")


def test_update_bootstrap(sandbox):
    _, bootstrap = sandbox
    data = rb.update_bootstrap(7, min_build=5, rollout=20, notes="灰度")
    assert data["latest_build"] == 7
    assert data["display"] == "v7"
    assert data["min_build"] == 5
    assert data["rollout"] == 20
    on_disk = json.loads(bootstrap.read_text(encoding="utf-8"))
    assert on_disk["latest_build"] == 7 and on_disk["notes"] == "灰度"


def test_update_bootstrap_partial_keeps_existing(sandbox):
    _, bootstrap = sandbox
    rb.update_bootstrap(9, min_build=None, rollout=None, notes=None)
    on_disk = json.loads(bootstrap.read_text(encoding="utf-8"))
    assert on_disk["latest_build"] == 9
    assert on_disk["min_build"] == 1   # 未传 → 保留
    assert on_disk["rollout"] == 100


def test_check_consistency_pass(sandbox):
    rb.write_app_version(7)
    rb.update_bootstrap(7, None, None, None)
    assert rb.check_consistency(7) == []


def test_check_consistency_detects_drift(sandbox):
    # constants=2, bootstrap=2, 但目标 build=7 → 两处都漂移
    errs = rb.check_consistency(7)
    assert len(errs) == 2


def test_check_consistency_min_build_too_high(sandbox):
    rb.write_app_version(7)
    rb.update_bootstrap(7, min_build=9, rollout=None, notes=None)
    errs = rb.check_consistency(7)
    assert any("min_build" in e for e in errs)


def test_main_check_pass(sandbox, monkeypatch):
    monkeypatch.setattr("sys.argv", ["release_bump.py", "2", "--check"])
    assert rb.main() == 0


def test_main_check_fail(sandbox, monkeypatch):
    monkeypatch.setattr("sys.argv", ["release_bump.py", "7", "--check"])
    assert rb.main() == 3


def test_main_bad_arg(sandbox, monkeypatch):
    monkeypatch.setattr("sys.argv", ["release_bump.py", "v1.2.3"])
    assert rb.main() == 2


def test_main_write_then_consistent(sandbox, monkeypatch):
    monkeypatch.setattr("sys.argv", ["release_bump.py", "v8", "--rollout", "30"])
    assert rb.main() == 0
    assert rb.read_app_version() == 8
    assert rb.check_consistency(8) == []


def test_main_rollout_out_of_range(sandbox, monkeypatch):
    monkeypatch.setattr("sys.argv", ["release_bump.py", "v8", "--rollout", "150"])
    assert rb.main() == 2
