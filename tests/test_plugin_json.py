"""Both ends of RPC reject ambiguous fields and non-finite JSON numbers."""
import io
import json
from pathlib import Path

import pytest

from core import plugin_json, plugin_worker as worker
from test_plugin_control import client_resource_scope as client_resource_scope


@pytest.mark.parametrize("text", [
    '{"type":"call","type":"result"}',
    '{"args":{"caller":"first","caller":"second"}}',
    '{"a":1,"\\u0061":2}',
    '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}',
    '{"value":1e999}', '{"value":-1e999}',
])
def test_invalid_wire_values_rejected(text):
    with pytest.raises(ValueError):
        plugin_json.loads(text)


@pytest.mark.parametrize("text", [
    pytest.param("[" * 65 + "0" + "]" * 65, id="depth"),
    pytest.param("[" + ",".join("0" for _ in range(131072)) + "]", id="values"),
    pytest.param("1" * 4301, id="numeric-token"),
])
def test_allocation_limits_reject_before_object_parser(monkeypatch, text):
    monkeypatch.setattr(plugin_json.json, "loads", lambda *a, **kw: pytest.fail("over-budget value reached object parser"))
    with pytest.raises(ValueError, match="limit"):
        plugin_json.loads(text)


def test_text_limit_rejects_before_object_parser(monkeypatch):
    monkeypatch.setattr(plugin_json, "MAX_TEXT", 64)
    monkeypatch.setattr(plugin_json.json, "loads", lambda *a, **kw: pytest.fail("oversized text reached object parser"))
    with pytest.raises(ValueError, match="text exceeds limit"):
        plugin_json.loads('"' + "a" * 63 + '"')


def test_strings_do_not_consume_structural_budget():
    value = {"literal": '[}] \\" quoted \n\t 中文', "nested": [True, False, None, 1.25]}
    assert plugin_json.loads(plugin_json.dumps(value)) == value
    text = "[" * 64 + "0" + "]" * 64
    result = plugin_json.loads(text)
    for _ in range(64):
        result = result[0]
    assert result == 0


@pytest.mark.parametrize("value", ["", "\\", "\\\\", '"', '\\"', '\\\\"', '\\\\\\"', "end\\", "\\u0022", "[{}]"])
def test_quote_and_backslash_runs_remain_literal(value):
    assert plugin_json.loads(json.dumps({"value": value})) == {"value": value}


def test_guest_does_not_emit_over_budget_callback(capsys):
    value = 0
    for _ in range(65):
        value = [value]
    with pytest.raises(ValueError, match="nesting exceeds limit"):
        worker.broker("pause", value)
    assert capsys.readouterr().out == ""


def test_guest_reply_uses_shared_limit_instead_of_truncating_at_one_megabyte(monkeypatch, capsys):
    value = "a" * (1024 * 1024 + 1)
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(plugin_json.dumps_message({"value": value}) + "\n"))
    assert worker.broker("pause") == value
    assert json.loads(capsys.readouterr().out.removeprefix(worker.PREFIX))["method"] == "pause"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_invalid_outgoing_values_rejected_recursively(value):
    with pytest.raises(ValueError):
        plugin_json.dumps({"value": [value]})


def test_standard_json_preserves_business_values_and_path_encoder():
    value = {"words": "中文 % $ !", "numbers": [0, -7, 1e308], "options": [True, False, None]}
    assert plugin_json.loads(plugin_json.dumps(value)) == value
    path = Path("ordinary-file")
    assert plugin_json.loads(plugin_json.dumps({"file": path}, default=worker._value)) == {"file": str(path)}


@pytest.mark.parametrize("reply", ['{"value":1,"value":2}', '{"value":1e999}'])
def test_guest_broker_rejects_invalid_host_reply(monkeypatch, capsys, reply):
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(reply + "\n"))
    with pytest.raises(ValueError):
        worker.broker("service.describe", "storage")
    call = json.loads(capsys.readouterr().out.removeprefix(worker.PREFIX))
    assert call["method"] == "service.describe"


def test_guest_rejects_nonfinite_business_argument_before_sending(capsys):
    with pytest.raises(ValueError):
        worker.broker("service.call", "storage", "write", [float("nan")], {})
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("version", [None, True, "1", 1.0, 0, 2])
def test_transport_version_is_required_exact_integer(version):
    payload = {"value": 7}
    if version is not None:
        payload["rpc_version"] = version
    with pytest.raises(ValueError, match="protocol version"):
        plugin_json.loads_message(json.dumps(payload))


def test_transport_version_does_not_replace_business_or_sdk_version():
    payload = {"sdk_api_version": 2, "service_api_version": 7, "value": "literal"}
    assert plugin_json.loads_message(plugin_json.dumps_message(payload)) == {"rpc_version": 1, **payload}
    with pytest.raises(ValueError, match="belongs to the transport"):
        plugin_json.dumps_message({"rpc_version": 7})


@pytest.mark.parametrize("payload", [
    {}, {"value": 1, "error": "failed"}, {"cancel": 1}, {"cancel": False},
    {"error": ""}, {"error": True}, {"value": None, "caller": "other"},
])
def test_guest_rejects_ambiguous_reply_envelopes(monkeypatch, payload):
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(plugin_json.dumps_message(payload) + "\n"))
    with pytest.raises(ValueError, match="reply envelope"):
        worker.broker("pause")


@pytest.mark.parametrize("payload", [
    {"value": None}, {"value": 1, "state": None},
    {"value": 1, "state": "", "error": "failed"},
    {"error": ""}, {"error": False}, {"state": ""},
    {"value": 1, "state": "", "caller": "other"},
])
def test_host_rejects_invalid_result_envelope_before_publication(wire_client, monkeypatch, payload):
    sandbox, clients, launch = wire_client
    monkeypatch.setattr(sandbox, "_save_state", lambda *a, **kwargs: pytest.fail("invalid result published"))
    message = plugin_json.dumps_message({"type": "result", **payload})
    program = "print(" + repr(sandbox.PREFIX + message) + ", flush=True)"
    with pytest.raises(sandbox.SandboxError, match="invalid plugin protocol"):
        launch(program)
    assert clients[0].poll() is not None
    assert clients[0].stdin.closed and clients[0].stdout.closed


def test_host_rejects_call_after_result_before_dispatch(wire_client, monkeypatch):
    sandbox, clients, launch = wire_client
    monkeypatch.setattr(sandbox, "_broker", lambda *a: pytest.fail("terminal result allowed capability call"))
    monkeypatch.setattr(sandbox, "_save_state", lambda *a, **kwargs: pytest.fail("invalid sequence published"))
    messages = [
        {"type": "result", "value": 0, "state": "fixture"},
        {"type": "call", "method": "pause", "args": [], "kwargs": {}},
    ]
    program = "\n".join("print(" + repr(sandbox.PREFIX + plugin_json.dumps_message(message)) + ", flush=True)" for message in messages)
    with pytest.raises(sandbox.SandboxError, match="call after terminal result"):
        launch(program)
    assert clients[0].poll() is not None
    assert clients[0].stdin.closed and clients[0].stdout.closed


@pytest.mark.parametrize("reply", ['{"value":"legacy"}', '{"rpc_version":2,"value":"future"}'])
def test_guest_rejects_missing_or_other_reply_version(monkeypatch, capsys, reply):
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(reply + "\n"))
    with pytest.raises(ValueError, match="protocol version"):
        worker.broker("service.describe", "storage")
    call = json.loads(capsys.readouterr().out.removeprefix(worker.PREFIX))
    assert call["rpc_version"] == 1


def test_worker_rejects_version_before_state_or_plugin_operations(tmp_path, monkeypatch):
    request = tmp_path / "request.json"
    request.write_text('{"rpc_version":2}', encoding="utf-8")
    monkeypatch.setenv("OPSKIT_SANDBOX", "1")
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(request)])
    monkeypatch.setattr(worker.shutil, "copytree", lambda *a, **k: pytest.fail("incompatible request copied state"))
    monkeypatch.setattr(worker, "_adapt_sdk", lambda *a: pytest.fail("incompatible request loaded SDK"))
    with pytest.raises(ValueError, match="protocol version"):
        worker.main()


@pytest.fixture
def wire_client(tmp_path, monkeypatch, client_resource_scope):
    import sys
    from core import process, plugin_sandbox as sandbox
    from core.plugin import load_manifest
    from core.plugin_trust import grant, compute_fingerprint
    root = tmp_path / "plugin"
    (root / "demo_pkg").mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo_pkg\n", encoding="utf-8")
    (root / "demo_pkg/__init__.py").write_text("raise RuntimeError('MUST_NOT_IMPORT')\n", encoding="utf-8")
    manifest = load_manifest(root)
    grant("demo", compute_fingerprint(root), "1.0.0")
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["fixture-client"])
    monkeypatch.setattr(sandbox, "_image", lambda *args: "sha256:" + "a" * 64)
    monkeypatch.setattr(sandbox, "_cleanup_control", lambda *args: None)
    clients = []

    def launch(program):
        def start(*args, **kwargs):
            client = process.start([sys.executable, "-c", program], **kwargs)
            clients.append(client)
            return client
        monkeypatch.setattr(sandbox, "start", start)
        return sandbox.invoke(manifest, "cli", args=[])
    return sandbox, clients, launch


@pytest.mark.parametrize("mode", ["depth", "values"])
def test_host_refuses_actual_over_budget_client_before_dispatch(wire_client, monkeypatch, mode):
    sandbox, clients, launch = wire_client
    monkeypatch.setattr(sandbox, "_broker", lambda *a: pytest.fail("over-budget wire reached broker"))
    monkeypatch.setattr(sandbox, "_save_state", lambda *a, **kwargs: pytest.fail("over-budget wire published state"))
    setup = "value=0\nfor _ in range(65): value=[value]" if mode == "depth" else "value=[0]*131072"
    program = ("import json,sys\n" + setup + "\n"
               + "message={'rpc_version':1,'type':'call','method':'pause','args':value,'kwargs':{}}\n"
               + "sys.stdout.buffer.write(" + repr(sandbox.PREFIX.encode())
               + "+json.dumps(message).encode()+bytes([10])); sys.stdout.buffer.flush()")
    with pytest.raises(sandbox.SandboxError, match="invalid plugin protocol"):
        launch(program)
    assert len(clients) == 1 and clients[0].poll() is not None
    assert clients[0].stdin.closed and clients[0].stdout.closed


def test_platform_owned_snapshot_accepts_system_temp_alias(wire_client, tmp_path, monkeypatch):
    sandbox, clients, launch = wire_client
    actual, alias = tmp_path / "actual-temp", tmp_path / "system-temp-alias"
    actual.mkdir()
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation unavailable")
    for variable in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(variable, str(alias))
    publications = []
    monkeypatch.setattr(sandbox, "_save_state", lambda *args, **kwargs: publications.append(args))
    program = "print('OPSKIT_RPC:{\"rpc_version\":1,\"type\":\"result\",\"value\":\"ok\",\"state\":\"\"}', flush=True)"
    assert launch(program) == "ok"
    assert len(publications) == 1
    assert clients[0].poll() is not None


@pytest.mark.parametrize("message", [
    '{"type":"call","type":"result","state":""}',
    '{"type":"result","value":1e999,"state":""}',
    b'{"type":"call","method":"pause","args":["\xff"],"kwargs":{}}',
    b'{"type":"call","method":"pause","args":["\xc0\xaf"],"kwargs":{}}',
    '{"type":"result","value":7,"state":"legacy-state"}',
    '{"rpc_version":2,"type":"call","method":"pause","args":[],"kwargs":{}}',
])
def test_host_rejects_actual_client_wire_before_state_publish(wire_client, monkeypatch, message):
    sandbox, clients, launch = wire_client
    monkeypatch.setattr(sandbox, "_save_state", lambda *args, **kwargs: pytest.fail("invalid wire published state"))
    monkeypatch.setattr(sandbox, "_broker", lambda *args: pytest.fail("invalid wire reached host broker"))
    wire = sandbox.PREFIX.encode("ascii") + (message if isinstance(message, bytes) else message.encode("utf-8")) + b"\n"
    program = "import sys; sys.stdout.buffer.write(" + repr(wire) + "); sys.stdout.buffer.flush()"
    with pytest.raises(sandbox.SandboxError, match="invalid plugin protocol"):
        launch(program)
    assert len(clients) == 1 and clients[0].poll() is not None
    assert clients[0].stdin.closed and clients[0].stdout.closed


def test_valid_unicode_callback_and_nonprotocol_output_survive(wire_client, monkeypatch):
    from core import prompt
    sandbox, clients, launch = wire_client
    calls, states, displayed = [], [], []
    monkeypatch.setattr(prompt, "pause", lambda text: calls.append(text))
    monkeypatch.setattr(prompt.console, "print", lambda text, **kwargs: displayed.append(text))
    monkeypatch.setattr(sandbox, "_save_state", lambda state, root, **kwargs: states.append(state))
    call = sandbox.PREFIX + json.dumps({"rpc_version": 1, "type": "call", "method": "pause", "args": ["中文参数"], "kwargs": {}}, ensure_ascii=False)
    result = sandbox.PREFIX + json.dumps({"rpc_version": 1, "type": "result", "value": "中文结果", "state": "fixture-state"}, ensure_ascii=False)
    program = "\n".join([
        "import sys,json",
        "sys.stdout.buffer.write(b'ordinary-output\\xff\\n'); sys.stdout.buffer.flush()",
        "sys.stdout.buffer.write(" + repr((call + "\n").encode("utf-8")) + "); sys.stdout.buffer.flush()",
        "assert json.loads(sys.stdin.buffer.readline()) == {'rpc_version': 1, 'value': None}",
        "sys.stdout.buffer.write(" + repr((result + "\n").encode("utf-8")) + "); sys.stdout.buffer.flush()",
    ])
    assert launch(program) == "中文结果"
    assert calls == ["中文参数"] and states == ["fixture-state"]
    assert displayed == ["ordinary-output\ufffd"]
    assert len(clients) == 1 and clients[0].poll() is not None
    assert clients[0].stdin.closed and clients[0].stdout.closed
