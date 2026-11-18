"""Faults run in the actual publication process, never only in its supervisor."""
import json
import os
from pathlib import Path
import threading
from worker_test_markers import process_birth, publish_marker


def state_packet():
    import base64
    import io
    import tarfile
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("value")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"new"))
    return base64.b64encode(output.getvalue()).decode("ascii")


def publication_worker(connection, *args):
    from core import plugin_sandbox as sandbox, plugin_state_publish as publication
    mode = os.environ["OPSKIT_TEST_PUBLICATION_MODE"]
    root = Path(args[0])
    def block(*arguments, **kwargs):
        read, write = os.pipe()
        publish_marker(os.environ["OPSKIT_TEST_PUBLICATION_MARKER"],
            json.dumps({"pid": os.getpid(), "created": process_birth(os.getpid())}))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    if mode == "block_read":
        sandbox._read_live_authorizations = block
    elif mode == "block_sync":
        from core import plugin_state
        plugin_state.os.fsync = block
    original = sandbox._rename_state
    def rename(source, target):
        if mode == "fail_candidate" and source.name == "new":
            raise PermissionError("injected candidate rename failure")
        if mode in ("trust_probe", "config_probe"):
            from core.resource_lock import ResourceBusyError
            outcomes = []
            def mutate():
                try:
                    if mode == "trust_probe":
                        from core.plugin_trust import revoke
                        revoke(args[5])
                    else:
                        from plugin.commands import set_enabled
                        set_enabled(args[5], False)
                except ResourceBusyError:
                    outcomes.append("busy")
                else:
                    outcomes.append("mutated")
            thread = threading.Thread(target=mutate)
            thread.start()
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("competing mutation did not return")
            with Path(os.environ["OPSKIT_TEST_PUBLICATION_MARKER"]).open("a") as output:
                output.write(json.dumps(outcomes) + "\n")
        original(source, target)
        if ((mode == "block_old" and source == root)
                or (mode == "block_new" and source.name == "new")):
            block()
        if ((mode == "crash_old" and source == root)
                or (mode == "crash_new" and source.name == "new")):
            publish_marker(os.environ["OPSKIT_TEST_PUBLICATION_MARKER"], str(os.getpid()))
            os._exit(73)
    sandbox._rename_state = rename
    publication._publish_worker(connection, *args)
