"""Invalid initial envelopes cannot enter state, SDK or plugin operations."""
import json

import pytest

from core import plugin_worker as worker


def request():
    return {"rpc_version": 1, "action": "cli", "name": "demo", "kind": "python",
            "entry": "demo_pkg", "args": ["--help", "中文"], "kwargs": {},
            "service": "", "method": "", "caller": "platform", "service_contract": None}


@pytest.mark.parametrize("changes", [
    {"extra": "untrusted"}, {"action": "unknown"}, {"kind": "shell"},
    {"name": "../demo"}, {"caller": "../other"}, {"args": {}}, {"kwargs": []},
    {"entry": "core"}, {"entry": ["demo_pkg"]}, {"args": [1]},
    {"action": "entry"}, {"service": "counter"}, {"method": "read"},
    {"service_contract": {}}, {"kwargs": {"silent": True}},
    {"kind": "exec", "entry": "../run"}, {"kind": "exec", "entry": "/bin/sh"},
    {"kind": "exec", "entry": {"windows": "run.exe"}},
    {"action": "service.call", "service": "counter", "service_contract": {"api_version": True, "methods": ["read"]}},
    {"action": "service.call", "service": "counter", "method": "delete", "service_contract": {"api_version": 7, "methods": ["read"]}},
    {"action": "service.describe", "service": "counter", "service_contract": {"api_version": 7, "methods": ["read"]}},
])
def test_invalid_request_rejected_before_all_operations(tmp_path, monkeypatch, changes):
    payload = {**request(), **changes}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("OPSKIT_SANDBOX", "1")
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(path)])
    def refused(*args, **kwargs):
        pytest.fail("invalid initial request reached side effects")
    monkeypatch.setattr(worker, "_decode", refused)
    monkeypatch.setattr(worker.shutil, "copytree", refused)
    monkeypatch.setattr(worker, "_adapt_sdk", refused)
    monkeypatch.setattr(worker.importlib, "import_module", refused)
    with pytest.raises(ValueError):
        worker.main()


@pytest.mark.parametrize("changes", [
    {}, {"action": "entry", "args": []}, {"kind": "exec", "entry": "bin/run"},
    {"kind": "exec", "entry": {"linux": "bin/run", "windows": "run.exe"}},
    {"action": "service.call", "service": "counter", "method": "read", "args": [1],
     "kwargs": {"option": True}, "service_contract": {"api_version": 7, "methods": ["read"]}},
    {"action": "service.describe", "service": "counter", "args": [],
     "service_contract": {"api_version": 7, "methods": ["read"]}},
])
def test_valid_request_preserves_exact_values(changes):
    payload = {**request(), **changes}
    assert worker._request(payload) is payload
