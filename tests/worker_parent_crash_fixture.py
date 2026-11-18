"""Independent parent, deliberately killed without running supervisor finally."""
import json
import os
from pathlib import Path
import sys
from worker_test_markers import process_birth, publish_marker


def blocked(connection, marker):
    read, write = os.pipe()
    publish_marker(marker, json.dumps({"pid": os.getpid(), "created": process_birth(os.getpid())}))
    os.read(read, 1)
    os.close(read)
    os.close(write)


def blocked_session(connection, buffer, length, requested):
    requested.wait()
    request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
    blocked(connection, request["source"])


def blocked_quota_session(connection, buffer, length, requested):
    from core import plugin_inspection as inspection, plugin_state
    plugin_state.reserve_staging = lambda root: blocked(connection, root)
    inspection._inspection_session(connection, buffer, length, requested)


def blocked_preparation(connection, *args):
    from core import archive, secrets
    from core.plugin_state_prepare import _prepare_worker
    def block(*args, **kwargs):
        blocked(connection, os.environ["OPSKIT_TEST_PREPARATION_PARENT_MARKER"])
    if os.environ["OPSKIT_TEST_PREPARATION_PARENT_STAGE"] == "extract":
        archive.extract_tar = block
    else:
        secrets.restrict_permissions = block
    _prepare_worker(connection, *args)


def blocked_transaction(connection, *args):
    from core import plugin_state, plugin_state_io
    stage = os.environ["OPSKIT_TEST_TRANSACTION_PARENT_STAGE"]
    def block(*args, **kwargs):
        blocked(connection, os.environ["OPSKIT_TEST_TRANSACTION_PARENT_MARKER"])
    if stage == "sync":
        plugin_state.os.fsync = block
    elif stage == "scan":
        original = os.scandir
        def scan(path):
            if str(path).endswith(".previous"):
                block()
            return original(path)
        os.scandir = scan
    else:
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path.parent.name.endswith(".previous"):
                block()
            return original(path, *args, **kwargs)
        Path.unlink = unlink
    plugin_state_io._finish_worker(connection, *args)


def blocked_discard(connection, *args):
    from core import plugin_state_io
    original = Path.unlink
    def unlink(path, *arguments, **kwargs):
        if path.parent.name == "new":
            blocked(connection, os.environ["OPSKIT_TEST_DISCARD_PARENT_MARKER"])
        return original(path, *arguments, **kwargs)
    Path.unlink = unlink
    plugin_state_io._discard_worker(connection, *args)


def blocked_repair(connection, *args):
    from core import plugin_state_operator, plugin_state_repair
    original = plugin_state_repair.repair_state_record
    count = 0
    def checkpoint():
        nonlocal count
        count += 1
        if count == int(os.environ["OPSKIT_TEST_REPAIR_STAGE"]):
            blocked(connection, os.environ["OPSKIT_TEST_REPAIR_MARKER"])
    def repair(*arguments, **kwargs):
        return original(*arguments, **kwargs, checkpoint=checkpoint)
    plugin_state_repair.repair_state_record = repair
    plugin_state_operator._worker(connection, *args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["OPSKIT_DATA_DIR"] = str(Path(sys.argv[1]).parent / "state")
    if len(sys.argv) > 2 and sys.argv[2].startswith("repair-"):
        from core import plugin_state, plugin_state_operator
        import main
        os.environ["OPSKIT_TEST_REPAIR_STAGE"] = sys.argv[2].split("-", 1)[1]
        os.environ["OPSKIT_TEST_REPAIR_MARKER"] = sys.argv[1]
        root = Path(os.environ["OPSKIT_DATA_DIR"]) / "plugin-data" / "owner" / "self"
        root.mkdir(parents=True)
        (root / "value").write_bytes(b"old")
        candidate = root.parent / ".state-owned" / "new"
        candidate.mkdir(parents=True)
        (candidate / "value").write_bytes(b"new")
        plugin_state.prepare_record(root, candidate, plugin="owner", caller="platform")
        path = plugin_state.record_path(root)
        raw = path.read_bytes() + b'{'
        path.write_bytes(raw)
        import hashlib
        plugin_state_operator._worker = blocked_repair
        main.app(args=["--output", "json", "plugin", "repair-state", "owner",
                       "--record-sha256", hashlib.sha256(raw).hexdigest()])
    elif len(sys.argv) > 2 and sys.argv[2].startswith("allocation-"):
        from core import plugin_sandbox, plugin_state_prepare
        from state_publication_fixture import state_packet
        from test_plugin_state_allocation import allocation_worker
        os.environ["OPSKIT_TEST_ALLOCATION_STAGE"] = sys.argv[2].split("-", 1)[1]
        os.environ["OPSKIT_TEST_ALLOCATION_MARKER"] = sys.argv[1]
        root = Path(sys.argv[1]).resolve().parent / "self"
        root.mkdir()
        (root / "value").write_bytes(b"old")
        plugin_state_prepare._prepare_worker = allocation_worker
        with plugin_sandbox._state_staging(root, state_packet(), plugin="owner", caller="platform"):
            raise AssertionError("allocation fixture did not block")
    elif len(sys.argv) > 2 and sys.argv[2].startswith("publication-"):
        from core import plugin_state, plugin_state_io, plugin_state_publish
        from core.config import get_config_path
        from core.plugin_trust import grant, _trust_file
        from state_publication_fixture import publication_worker
        os.environ["OPSKIT_TEST_PUBLICATION_MODE"] = "block_" + sys.argv[2].split("-", 1)[1]
        os.environ["OPSKIT_TEST_PUBLICATION_MARKER"] = sys.argv[1]
        root = Path(sys.argv[1]).resolve().parent / "self"
        root.mkdir()
        (root / "value").write_bytes(b"old")
        wrapper = root.parent / ".state-owned"
        (wrapper / "new").mkdir(parents=True)
        (wrapper / "new" / "value").write_bytes(b"new")
        lease = plugin_state_io.OwnedStaging(root, wrapper, plugin_state._directory(root.parent),
                                            plugin_state._directory(wrapper))
        plugin_state.prepare_record(root, lease.candidate, plugin="owner", caller="platform")
        grant("owner", "a" * 64, "1")
        requirements = {"operation": "authorize", "config": str(get_config_path()), "trust": str(_trust_file()),
                        "identities": (("owner", "a" * 64),), "edges": ()}
        plugin_state_publish._publish_worker = publication_worker
        plugin_state_publish.publish_supervised(lease, plugin_state._directory(lease.candidate),
                                                plugin="owner", caller="platform", requirements=requirements, timeout=60)
    elif len(sys.argv) > 2 and sys.argv[2] == "discard":
        from core import plugin_state, plugin_state_io
        os.environ["OPSKIT_TEST_DISCARD_PARENT_MARKER"] = sys.argv[1]
        root = Path(sys.argv[1]).resolve().parent / "self"
        root.mkdir()
        (root / "value").write_bytes(b"old")
        wrapper = root.parent / ".state-owned"
        (wrapper / "new").mkdir(parents=True)
        (wrapper / "new" / "value").write_bytes(b"new")
        lease = plugin_state_io.OwnedStaging(root, wrapper, plugin_state._directory(root.parent),
                                            plugin_state._directory(wrapper))
        plugin_state_io._discard_worker = blocked_discard
        plugin_state_io.discard_supervised(lease, timeout=60)
    elif len(sys.argv) > 2 and sys.argv[2].startswith("transaction-"):
        from core import plugin_state, plugin_state_io
        os.environ["OPSKIT_TEST_TRANSACTION_PARENT_MARKER"] = sys.argv[1]
        os.environ["OPSKIT_TEST_TRANSACTION_PARENT_STAGE"] = sys.argv[2].split("-", 1)[1]
        root = Path(sys.argv[1]).resolve().parent / "self"
        root.mkdir()
        (root / "value").write_bytes(b"old")
        candidate = root.parent / ".state-owned" / "new"
        candidate.mkdir(parents=True)
        (candidate / "value").write_bytes(b"new")
        plugin_state.prepare_record(root, candidate, plugin="owner", caller="platform")
        root.rename(root.with_name("self.previous"))
        candidate.rename(root)
        plugin_state_io._finish_worker = blocked_transaction
        plugin_state_io.finish_supervised(root, plugin="owner", caller="platform", timeout=60)
    elif len(sys.argv) > 2 and sys.argv[2].startswith("preparation-"):
        import base64
        import io
        import tarfile
        from core import plugin_state_prepare as preparation
        os.environ["OPSKIT_TEST_PREPARATION_PARENT_MARKER"] = sys.argv[1]
        os.environ["OPSKIT_TEST_PREPARATION_PARENT_STAGE"] = sys.argv[2].split("-", 1)[1]
        root = Path(sys.argv[1]).resolve().parent / "self"
        root.mkdir()
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            archive.addfile(tarfile.TarInfo("value"))
        preparation._prepare_worker = blocked_preparation
        preparation.prepare_candidate(base64.b64encode(output.getvalue()).decode("ascii"), root, timeout=60, plugin="owner", caller="platform", on_allocate=lambda lease: None)
    elif len(sys.argv) > 2 and sys.argv[2] == "quota":
        from core import plugin_inspection as inspection
        inspection._inspection_session = blocked_quota_session
        reader = inspection.ContentInspector()
        reader.state_quota(Path(sys.argv[1]).resolve(), timeout=60)
    elif len(sys.argv) > 2 and sys.argv[2] == "session":
        from core import plugin_inspection as inspection
        inspection._inspection_session = blocked_session
        reader = inspection.ContentInspector()
        reader.inspect(sys.argv[1], verify_checksums=False, timeout=60)
    else:
        from network.worker import run
        run(blocked, (sys.argv[1],), timeout=60, label="parent crash fixture")
