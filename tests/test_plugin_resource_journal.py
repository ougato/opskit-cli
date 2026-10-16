import json
import os

import pytest

from core import plugin_resources as resources, plugin_resource_io as resource_io


@pytest.mark.parametrize('field', ['schema', 'token', 'kind', 'name', 'backend', 'daemon'])
def test_duplicate_record_fields_are_retained_without_docker_calls(journal, field):
    item = lease(journal)
    original = item.path.read_bytes()
    duplicate = original.decode()[:-1] + ', ' + json.dumps(field) + ': ' + json.dumps(item.record[field]) + '}'
    item.path.write_text(duplicate)
    item.lock.__exit__(None, None, None)
    journal.calls.clear()
    result = resources.reconcile(['docker'], journal)
    assert result == {'recovered': 0, 'active': 0, 'pending': 0, 'invalid': 1}
    assert item.path.read_text() == duplicate and journal.objects and journal.calls == []
    item.path.write_bytes(original)
    assert resources.reconcile(['docker'], journal)['recovered'] == 1


@pytest.mark.parametrize('stage', ['before_open', 'during_read'])
def test_record_modification_during_read_is_refused(journal, monkeypatch, stage):
    item = lease(journal)
    original_bytes = item.path.read_bytes()
    if stage == 'before_open':
        original = resources.os.open
        def changed(path, *args, **kwargs):
            if path == item.path:
                with item.path.open('ab') as stream:
                    stream.write(b' ')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(resources.os, 'open', changed)
    else:
        original = resources.os.fdopen
        class Stream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def fileno(self):
                return self.stream.fileno()
            def read(self, *args):
                result = self.stream.read(*args)
                with item.path.open('ab') as stream:
                    stream.write(b' ')
                return result
        monkeypatch.setattr(resources.os, 'fdopen', lambda *a, **k: Stream(original(*a, **k)))
    try:
        with pytest.raises(ValueError, match='changed during'):
            resources._read(item.path)
    finally:
        monkeypatch.setattr(resources.os, 'open' if stage == 'before_open' else 'fdopen', original)
        item.path.write_bytes(original_bytes)
        item.close(journal)


def test_deep_record_rejected_before_json_object_parser(tmp_path, monkeypatch):
    from core import plugin_json
    path = tmp_path / ('a' * 32 + '.json')
    path.write_bytes(b'[' * 65 + b'0' + b']' * 65)
    monkeypatch.setattr(plugin_json.json, 'loads', lambda *a, **k: pytest.fail('deep journal reached object parser'))
    with pytest.raises(ValueError, match='limit'):
        resources._read(path)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX FIFO replacement')
def test_regular_file_replaced_by_fifo_does_not_block(tmp_path, monkeypatch):
    path = tmp_path / ('a' * 32 + '.json')
    path.write_bytes(b'{}')
    original = resources.os.open
    def changed(target, flags, *args, **kwargs):
        if target == path:
            assert flags & os.O_NONBLOCK
            path.unlink()
            os.mkfifo(path)
        return original(target, flags, *args, **kwargs)
    monkeypatch.setattr(resources.os, 'open', changed)
    with pytest.raises(ValueError, match='invalid resource journal file'):
        resources._read(path)


class Daemon:
    def __init__(self):
        self.identity = "daemon-a"
        self.failed = False
        self.objects = []
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if self.failed:
            raise RuntimeError("daemon unavailable")
        if args[0] == "info":
            return self.identity
        if len(args) > 1 and args[1] == "ls":
            filters = [args[i + 1] for i, value in enumerate(args) if value == "--filter"]
            token = next(value.split("=", 2)[2] for value in filters if value.startswith("label="))
            name = next(value.split("=", 1)[1] for value in filters if value.startswith("name="))
            return "\n".join(json.dumps({"ID": obj["id"], "Names" if args[0] == "container" else "Name": obj["name"]})
                             for obj in self.objects if obj["kind"] == args[0] and obj["token"] == token and obj["name"] == name)
        identity = args[-1]
        self.objects[:] = [obj for obj in self.objects if obj["id"] != identity]
        return ""


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    return Daemon()


def lease(daemon, kind="container"):
    item = resources.ResourceLease(["docker"], kind, "opskit-" + "a" * 32, daemon)
    daemon.objects.append({"id": "b" * 64, "name": item.record["name"], "kind": kind, "token": item.record["token"]})
    return item


@pytest.mark.parametrize("body_failure", [False, True])
def test_sandbox_scope_records_launch_and_retains_failed_cleanup(journal, monkeypatch, body_failure):
    from core import plugin_sandbox as sandbox
    monkeypatch.setattr(sandbox, "_control", lambda command, *args: journal(*args))
    expected = ValueError if body_failure else sandbox.SandboxError
    with pytest.raises(expected):
        with sandbox._resource_scope(["docker"], "container", "opskit-" + "a" * 32) as managed:
            token = managed.labels[1].split("=", 1)[1]
            journal.objects.append({"id": "b" * 64, "name": "opskit-" + "a" * 32,
                                    "kind": "container", "token": token})
            assert resources.reconcile(["docker"], journal)["active"] == 1
            journal.failed = True
            if body_failure:
                raise ValueError("original execution error")
    journal.failed = False
    assert resources.reconcile(["docker"], journal)["recovered"] == 1
    assert not journal.objects


def test_sandbox_scope_refuses_launch_when_intent_cannot_be_saved(journal, monkeypatch):
    from core import plugin_sandbox as sandbox
    monkeypatch.setattr(sandbox, "_control", lambda command, *args: journal(*args))
    def reject(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(resource_io, "create_record", reject)
    with pytest.raises(sandbox.SandboxError, match="execution refused"):
        with sandbox._resource_scope(["docker"], "container", "opskit-" + "a" * 32):
            pytest.fail("resource creation must not be reached")


@pytest.mark.parametrize("proxy_fails", [False, True])
def test_network_lifecycle_uses_real_journal_and_cleans_reverse_order(journal, monkeypatch, proxy_fails):
    from core import plugin_gateway
    monkeypatch.setattr(plugin_gateway, "image", lambda command: "platform-gateway-image")
    from types import SimpleNamespace
    from core import plugin_sandbox as sandbox
    def control(command, *args):
        network_create = args[:2] == ("network", "create")
        proxy_create = args[0] == "run"
        if network_create or proxy_create:
            token = args[args.index("--label") + 1].split("=", 1)[1]
            name = args[-1] if network_create else args[args.index("--name") + 1]
            records = [resources._read(path) for path in resources._root().glob("*.json")]
            assert any(record["token"] == token and record["name"] == name for record in records)
            journal.objects.append({"id": ("c" if network_create else "d") * 64,
                                    "name": name, "kind": "network" if network_create else "container", "token": token})
            if proxy_create and proxy_fails:
                raise sandbox.SandboxError("proxy launch failed after creation")
            return ""
        if args[:2] == ("network", "connect"):
            return ""
        if args[0] == "inspect":
            name = next(obj["name"] for obj in journal.objects if obj["kind"] == "network")
            return json.dumps({name: {"IPAddress": "172.19.0.2"}})
        return journal(*args)
    monkeypatch.setattr(sandbox, "_control", control)
    def execute():
        with sandbox._network(SimpleNamespace(name="demo", permissions=["network"]), ["docker"]):
            assert resources.reconcile(["docker"], journal)["active"] == 2
    if proxy_fails:
        with pytest.raises(sandbox.SandboxError, match="proxy launch failed"):
            execute()
    else:
        execute()
    assert not journal.objects and not list(resources._root().glob("*.json"))
    deletions = [call for call in journal.calls if call[0] == "rm" or call[:2] == ("network", "rm")]
    assert deletions == [("rm", "-f", "d" * 64), ("network", "rm", "c" * 64)]


@pytest.mark.parametrize("kind", ["container", "network"])
def test_cleanup_failure_retains_intent_and_recovery_deletes_only_owned_id(journal, kind):
    item = lease(journal, kind)
    journal.failed = True
    with pytest.raises(RuntimeError):
        item.close(journal)
    assert item.path.exists()
    journal.failed = False
    result = resources.reconcile(["docker"], journal)
    assert result == {"recovered": 1, "active": 0, "pending": 0, "invalid": 0}
    assert not item.path.exists() and not journal.objects
    deletions = [call for call in journal.calls
                 if call[0] == 'rm' or call[:2] == ('network', 'rm')]
    assert deletions == [("rm", "-f", "b" * 64) if kind == "container"
                         else ("network", "rm", "b" * 64)]


def test_live_lease_is_not_recovered(journal):
    item = lease(journal)
    assert item.labels == ["--label", resources.LABEL + "=" + item.record["token"]]
    try:
        result = resources.reconcile(["docker"], journal)
        assert result["active"] == 1
        assert item.path.exists() and journal.objects
    finally:
        item.close(journal)


def test_matching_name_without_ownership_label_is_never_removed(journal):
    item = lease(journal)
    journal.objects[0]["token"] = "c" * 32
    item.close(journal)
    assert len(journal.objects) == 1
    assert not any(args[0] == "rm" for args in journal.calls)


def test_daemon_identity_change_retains_pending_intent(journal):
    item = lease(journal)
    journal.identity = "daemon-b"
    with pytest.raises(RuntimeError, match="backend changed"):
        item.close(journal)
    assert resources.reconcile(["docker"], journal)["pending"] == 1
    assert journal.objects and item.path.exists()
    assert not any(args[0] == "rm" for args in journal.calls)


def test_invalid_stored_command_is_not_executed(journal):
    item = lease(journal)
    journal.failed = True
    with pytest.raises(RuntimeError):
        item.close(journal)
    record = dict(item.record, backend=["powershell", "untrusted"])
    item.path.write_text(json.dumps(record))
    journal.failed = False
    before = len(journal.calls)
    assert resources.reconcile(["docker"], journal)["invalid"] == 1
    assert len(journal.calls) == before
    assert item.path.exists()


def test_absent_resource_retires_intent_without_deletion(journal):
    item = resources.ResourceLease(["docker"], "container", "opskit-" + "a" * 32, journal)
    item.close(journal)
    assert not item.path.exists()
    assert not any(args[0] == "rm" for args in journal.calls)


@pytest.mark.parametrize("kind", ["container", "network"])
@pytest.mark.parametrize("outcome", ["absent", "present", "daemon_changed", "query_failed"])
def test_failed_removal_requires_same_daemon_and_confirmed_absence(journal, kind, outcome):
    item = lease(journal, kind)
    def control(*args):
        removing = args[0] == "rm" or args[:2] == ("network", "rm")
        if removing:
            if outcome != "present":
                journal.objects.clear()
            if outcome == "daemon_changed":
                journal.identity = "daemon-b"
            if outcome == "query_failed":
                journal.failed = True
            raise RuntimeError("deletion failed")
        return journal(*args)
    if outcome == "absent":
        item.close(control)
        assert not item.path.exists()
    else:
        with pytest.raises(RuntimeError):
            item.close(control)
        assert item.path.exists()


def test_launch_intent_failure_does_not_create_resource(journal, monkeypatch):
    def reject(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(resource_io, "create_record", reject)
    with pytest.raises(OSError):
        resources.ResourceLease(["docker"], "container", "opskit-" + "a" * 32, journal)
    assert not journal.objects


def test_journal_capacity_rejects_more_intents(journal, monkeypatch):
    monkeypatch.setattr(resources, "MAX_RECORDS", 1)
    item = lease(journal)
    try:
        with pytest.raises(RuntimeError, match="journal is full"):
            resources.ResourceLease(["docker"], "container", "opskit-" + "c" * 32, journal)
    finally:
        item.close(journal)


def test_ambiguous_listing_is_not_deleted(journal):
    item = lease(journal)
    journal.objects.append(dict(journal.objects[0], id="c" * 64))
    with pytest.raises(ValueError, match="ambiguous"):
        item.close(journal)
    assert item.path.exists() and len(journal.objects) == 2
    assert not any(args[0] == "rm" for args in journal.calls)


@pytest.mark.parametrize("crash", [False, True])
def test_another_process_live_lease_is_not_recovered(journal, crash):
    from pathlib import Path
    import subprocess
    import sys
    import time
    root = resources._root()
    script = '''
import time
from core.plugin_resources import ResourceLease, _root
def control(*args):
    return 'daemon-a' if args[0] == 'info' else ''
lease = ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, control)
root = _root()
(root / 'ready').write_text('ready')
try:
    until = time.monotonic() + 15
    while not (root / 'continue').exists():
        if time.monotonic() >= until:
            raise TimeoutError('parent did not release lease')
        time.sleep(0.01)
finally:
    lease.close(control)
'''
    process = subprocess.Popen([sys.executable, "-c", script],
                               cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        until = time.monotonic() + 15
        while not (root / "ready").exists():
            if process.poll() is not None or time.monotonic() >= until:
                pytest.fail("child did not acquire resource lease")
            time.sleep(0.01)
        assert resources.reconcile(["docker"], journal)["active"] == 1
        assert journal.calls == []
        if crash:
            path = next(root.glob("*.json"))
            record = resources._read(path)
            process.kill()
            process.wait(timeout=15)
    finally:
        (root / "continue").write_text("continue")
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise
    if crash:
        assert process.returncode != 0
        journal.objects.append({"id": "d" * 64, "name": record["name"],
                                "kind": record["kind"], "token": record["token"]})
        assert resources.reconcile(["docker"], journal)["recovered"] == 1
        assert not journal.objects
    else:
        assert process.returncode == 0, stderr
    assert not list(root.glob("*.json"))
