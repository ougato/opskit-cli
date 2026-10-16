"""Local Docker control supervision and narrow network responses, no daemon."""
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_sandbox as sandbox


@pytest.fixture
def client_resource_scope(monkeypatch):
    """Local Python clients test supervision, not the Docker journal protocol.

    Real journal scopes are exercised in test_plugin_resource_journal.
    """
    monkeypatch.setattr(sandbox, "_require_sandbox_capabilities", lambda command: None)
    from core import plugin_gateway
    monkeypatch.setattr(plugin_gateway, "image", lambda command: "platform-gateway-image")
    @contextmanager
    def scope(command, kind, name):
        from core import plugin_workspace
        lease = SimpleNamespace(labels=[], workspace=None)
        def allocate_workspace(**kwargs):
            lease.workspace = plugin_workspace.allocate(**kwargs)
            return lease.workspace
        lease.allocate_workspace = allocate_workspace
        try:
            yield lease
        finally:
            args = ("rm", "-f", name) if kind == "container" else ("network", "rm", name)
            sandbox._cleanup_control(command, *args)
            if lease.workspace is not None:
                plugin_workspace.cleanup(lease.workspace, timeout=30)
    monkeypatch.setattr(sandbox, "_resource_scope", scope)


def test_real_control_output_success_and_closed_stdin():
    result = sandbox._control([sys.executable, "-c", "import sys; assert sys.stdin.read()==''; print('identity')"])
    assert result == "identity"


@pytest.mark.parametrize("program", ["import sys; print('password=SECRET',file=sys.stderr); sys.exit(1)",
                                    "print('token=SECRET' * 10000)"])
def test_control_failure_or_excess_never_exposes_logs(program, capfd):
    with pytest.raises(sandbox.SandboxError) as failure:
        sandbox._control([sys.executable, "-c", program])
    assert str(failure.value) == "Docker sandbox operation failed"
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "output_limit"])
def test_control_reclaims_actual_local_client_tree(tmp_path, monkeypatch, outcome):
    record = tmp_path / "child.json"
    program = "\n".join([
        "import sys,subprocess,time,json,psutil",
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])",
        "with open(sys.argv[1],'w') as out: json.dump([child.pid,psutil.Process(child.pid).create_time()],out)",
        "if sys.argv[2]=='cancel': print('ready',flush=True)",
        "if sys.argv[2]=='output_limit': print('x'*32768,flush=True)",
        "time.sleep(60)",
    ])
    monkeypatch.setattr(sandbox, "CONTROL_TIMEOUT", 2 if outcome == "timeout" else 5)
    if outcome == "cancel":
        from core import process
        original = process.stream
        def cancelled(command, **kwargs):
            iterator = original(command, **kwargs)
            try:
                assert next(iterator) == "ready"
                raise KeyboardInterrupt
                yield
            finally:
                iterator.close()
        monkeypatch.setattr(process, "stream", cancelled)
    with pytest.raises(KeyboardInterrupt if outcome == "cancel" else sandbox.SandboxError):
        sandbox._control([sys.executable, "-c", program, str(record), outcome])
    assert record.exists()
    pid, created = json.loads(record.read_text())
    try:
        child = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    assert child.create_time() != created or child.status() == psutil.STATUS_ZOMBIE


@pytest.mark.parametrize("address", ["172.19.0.2", "100.64.0.2", "", "127.0.0.1", "169.254.1.1",
                                     "0.0.0.0", "224.0.0.1", "::1", "user:SECRET@host", 2886729729])
def test_network_only_queries_network_fields_and_always_attempts_owned_cleanup(monkeypatch, address, client_resource_scope):
    calls = []
    name = "opskit-net-" + "a" * 32
    proxy = name + "-proxy"
    monkeypatch.setattr(sandbox.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    def control(command, *args):
        calls.append(args)
        if args[0] == "inspect":
            assert args == ("inspect", "--format", "{{json .NetworkSettings.Networks}}", proxy)
            return json.dumps({name: {"IPAddress": address}})
        return ""
    monkeypatch.setattr(sandbox, "_control", control)
    manifest = SimpleNamespace(name="demo", permissions=["network"])
    if address in {"172.19.0.2", "100.64.0.2"}:
        with sandbox._network(manifest, ["docker"]) as (network, env):
            assert network == name and all(address in env[index] for index in (1, 3, 5, 7))
    else:
        with pytest.raises(sandbox.SandboxError, match="invalid sandbox network response") as failure:
            with sandbox._network(manifest, ["docker"]):
                pytest.fail("invalid address reached worker")
        assert "SECRET" not in str(failure.value)
    assert calls[-2:] == [("rm", "-f", proxy), ("network", "rm", name)]
    assert '--log-driver=none' in next(call for call in calls if call[0] == 'run')
    created = next(call for call in calls if call[:2] == ('network', 'create'))
    assert '--internal' in created and '--driver=bridge' in created and '--ipv6=false' in created
    assert ('--opt', 'com.docker.network.bridge.inhibit_ipv4=true') == created[5:7]
    launched = next(call for call in calls if call[0] == 'run')
    assert '--entrypoint=python' in launched
    assert launched[-5:] == ('platform-gateway-image', '-I', '-S', '-u', '/runtime/gateway.py')


@pytest.mark.parametrize("response", ["not-json SECRET", "[]", "{}", '{"other":{"IPAddress":"172.19.0.2"}}'])
def test_malformed_network_response_safe_failure_and_cleanup(monkeypatch, response, client_resource_scope):
    calls = []
    def control(command, *args):
        calls.append(args)
        return response if args[0] == "inspect" else ""
    monkeypatch.setattr(sandbox, "_control", control)
    with pytest.raises(sandbox.SandboxError, match="invalid sandbox network response") as failure:
        with sandbox._network(SimpleNamespace(name="demo", permissions=["network"]), ["docker"]):
            pytest.fail("invalid response reached worker")
    assert "SECRET" not in str(failure.value)
    assert calls[-2][0] == "rm" and calls[-1][0:2] == ("network", "rm")


def test_no_network_grant_creates_no_control_resource(monkeypatch):
    monkeypatch.setattr(sandbox, "_control", lambda *args: pytest.fail("ungranted network created resources"))
    with sandbox._network(SimpleNamespace(permissions=[]), ["docker"]) as value:
        assert value == ("none", [])


@pytest.mark.parametrize("wire", ['{"rpc_version":1,"type":"result","error":"token=SECRET"}', '[]', 'null', 'bad-json SECRET', '\U0001f642' * 40])
def test_actual_client_protocol_error_is_safe_and_own_cleanup_attempted(tmp_path, monkeypatch, wire, client_resource_scope):
    from core.plugin import load_manifest
    from core.plugin_trust import grant, compute_fingerprint
    root = tmp_path / "plugin"
    (root / "demo_pkg").mkdir(parents=True)
    (root / "plugin.yaml").write_text("name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo_pkg\n", encoding="utf-8")
    (root / "demo_pkg" / "__init__.py").write_text("raise RuntimeError('MUST_NOT_IMPORT')\n", encoding="utf-8")
    manifest = load_manifest(root)
    grant("demo", compute_fingerprint(root), "1.0.0")
    client = tmp_path / "client.py"
    client.write_text("import os\nos.write(1, " + repr((sandbox.PREFIX + wire + '\n').encode('utf-8')) + ")\n", encoding="utf-8")
    monkeypatch.setattr(sandbox, 'WIRE_LIMIT', 128)
    monkeypatch.setattr(sandbox, "docker_command", lambda: [sys.executable, str(client)])
    monkeypatch.setattr(sandbox, "_image", lambda *args: "sha256:" + "a" * 64)
    calls = []
    monkeypatch.setattr(sandbox, "_control", lambda command, *args: calls.append(args) or "")
    with pytest.raises(sandbox.SandboxError) as failure:
        sandbox.invoke(manifest, "cli", args=[])
    assert str(failure.value) in {"plugin execution failed", "invalid plugin protocol", "plugin exceeded output quota"}
    if wire.startswith('\U0001f642'):
        assert str(failure.value) == 'plugin exceeded output quota'
    assert "SECRET" not in str(failure.value)
    assert len(calls) == 1 and calls[0][:2] == ("rm", "-f")
    assert calls[0][2].startswith("opskit-")
    assert not (tmp_path / "plugin-data" / "demo" / "self").exists()


@pytest.mark.parametrize("blocked_reply", [False, True])
def test_execution_deadline_reclaims_local_tree_and_blocked_reply(tmp_path, monkeypatch, blocked_reply, client_resource_scope):
    from core.plugin import load_manifest
    from core.plugin_trust import grant, compute_fingerprint
    from core import process, config
    root = tmp_path / "plugin"
    (root / "demo_pkg").mkdir(parents=True)
    (root / "plugin.yaml").write_text("name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo_pkg\n", encoding="utf-8")
    (root / "demo_pkg" / "__init__.py").write_text("raise RuntimeError('MUST_NOT_IMPORT')\n", encoding="utf-8")
    manifest = load_manifest(root)
    grant("demo", compute_fingerprint(root), "1.0.0")
    record = tmp_path / "child.json"
    client = tmp_path / "client.py"
    client.write_text("\n".join([
        "import sys,subprocess,time,json,psutil",
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])",
        "with open(" + repr(str(record)) + ",'w') as out: json.dump([child.pid,psutil.Process(child.pid).create_time()],out)",
        "sys.stdin.buffer.readline()",  # Gate protocol work on the supervisor's real request.
        "print(" + repr(sandbox.PREFIX + '{"rpc_version":1,"type":"call"}') + ",flush=True)" if blocked_reply else "pass",
        "time.sleep(60)",
    ]), encoding="utf-8")
    monkeypatch.setattr(config, "load_config", lambda: {"plugin": {"sandbox": {"timeout": 30}}})
    monkeypatch.setattr(sandbox, "docker_command", lambda: [sys.executable, str(client)])
    monkeypatch.setattr(sandbox, "_image", lambda *args: "sha256:" + "a" * 64)
    monkeypatch.setattr(sandbox, "_control", lambda *args: "")
    monkeypatch.setattr(sandbox, "_broker", lambda *args: "x" * (256 * 1024))
    owned = []
    # Allow bounded snapshot startup, then cap the actual client phase at 2s.
    # Cold-start accounting has separate execution-budget tests; this test
    # must already own a real descendant to verify tree/blocked-write cleanup.
    prepared = process.start([sys.executable, str(client)], stdin=subprocess.PIPE, binary=True)
    def tracked_start(*args, **kwargs):
        assert '--log-driver=none' in args[0]
        assert kwargs == {"stdin": subprocess.PIPE, "binary": True}
        sandbox._invocations.deadline = min(sandbox._invocations.deadline, time.monotonic() + 2)
        owned.append(prepared)
        return prepared
    monkeypatch.setattr(sandbox, "start", tracked_start)
    try:
        ready_deadline = time.monotonic() + 30
        while True:
            try:
                pid, created = json.loads(record.read_text())
                break
            except (FileNotFoundError, json.JSONDecodeError):
                assert prepared.poll() is None, "fixture client exited before readiness"
                assert time.monotonic() < ready_deadline, "fixture descendant was not ready"
                time.sleep(.02)
        with pytest.raises(sandbox.SandboxError, match="plugin exceeded execution deadline"):
            sandbox.invoke(manifest, "cli", args=[])
        assert len(owned) == 1 and owned[0].poll() is not None
        assert owned[0].stdin.closed and owned[0].stdout.closed
        try:
            child = psutil.Process(pid)
        except psutil.NoSuchProcess:
            pass
        else:
            assert child.create_time() != created or child.status() == psutil.STATUS_ZOMBIE
        assert not (tmp_path / "plugin-data" / "demo" / "self").exists()
    finally:
        if prepared.poll() is None:
            process.terminate_tree(prepared)
        prepared.stdin.close()
        prepared.stdout.close()
