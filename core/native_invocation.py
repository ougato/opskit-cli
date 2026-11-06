"""Execute the actual SDK worker from private verified native snapshots.

The lifecycle caller supplies authorization checkpoints and owns state commit.
This function never publishes worker state or imports plugin code in the host.
"""
from contextlib import contextmanager
import os
import tempfile
import time

from core import plugin_json
from core.native_process import open_worker
from core.native_protocol import exchange
from core.native_runtime import stage, verify, absolute_path
from core.native_runtime_prepare import stage_candidate, validate_record
from core.native_sandbox import get_backend


def _stage_runtime_worker(connection, source, destination, files):
    from network.worker import send
    try:
        try:
            stage(source, destination, files)
        except (OSError, ValueError, RuntimeError):
            send(connection, {"ok": False})
        else:
            send(connection, {"ok": True})
    finally:
        connection.close()


def _verify_runtime_worker(connection, root, files):
    from network.worker import send
    try:
        try:
            verify(root, files)
        except (OSError, ValueError, RuntimeError):
            send(connection, {"ok": False})
        else:
            send(connection, {"ok": True})
    finally:
        connection.close()


@contextmanager
def verify_runtime_snapshot(runtime, files, *, deadline, cancel_event=None):
    """Recheck the private runtime alongside independent authorization checks."""
    from network.worker import owned_process, wait_result
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("native runtime verification cancelled")
    if time.monotonic() >= deadline:
        raise TimeoutError("native runtime verification deadline exceeded")
    with owned_process(_verify_runtime_worker, (runtime, files),
                       label="native runtime verification") as (connection, _):
        waited = False
        def wait():
            nonlocal waited
            if waited:
                raise RuntimeError("native runtime verification already consumed")
            waited = True
            result = wait_result(connection, deadline=deadline, label="native runtime verification",
                                 cancel_event=cancel_event)
            if result != {"ok": True}:
                raise ValueError("native runtime content does not match prepared inventory")
        yield wait


@contextmanager
def prepare_runtime(runtime, record, *, fingerprint, sdk_sha256, deadline, staging_parent, cancel_event=None):
    """Stage a runtime in an owned child while independent setup proceeds.

    The child remains supervised until this context exits, including when setup
    is cancelled. The caller must wait before handing the snapshot to invoke.
    """
    from network.worker import owned_process, wait_result
    validate_record(record, fingerprint=fingerprint, sdk_sha256=sdk_sha256)
    target = absolute_path(staging_parent) / "runtime-stage"
    with owned_process(_stage_runtime_worker, (str(runtime), str(target), record["files"]),
                       label="native runtime snapshot") as (connection, process):
        waited = False
        def wait():
            nonlocal waited
            if waited:
                raise RuntimeError("native runtime snapshot already consumed")
            waited = True
            result = wait_result(connection, deadline=deadline, label="native runtime snapshot",
                                 cancel_event=cancel_event)
            if result != {"ok": True}:
                raise ValueError("native runtime snapshot failed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("native runtime snapshot deadline exceeded")
            process.join(timeout=remaining)
            if process.is_alive():
                raise TimeoutError("native runtime snapshot deadline exceeded")
            if process.exitcode != 0 or time.monotonic() >= deadline:
                raise RuntimeError("native runtime snapshot worker did not complete")
            return target
        yield wait


def invoke(runtime, record, plugin, plugin_files, seed, seed_files, request, *,
           fingerprint, sdk_sha256, deadline, dispatch, checkpoint, output,
           staging_parent, prepared_runtime=None, preload=None, validate_preload=None, preload_state=None):
    """Return bounded worker result; no trust or state decision is implicit."""
    checkpoint()
    # stage() verifies both the complete source inventory and its private copy.
    # Check identity here without hashing the same source yet another time.
    validate_record(record, fingerprint=fingerprint, sdk_sha256=sdk_sha256)
    envelope = plugin_json.dumps_message(request)
    backend = get_backend()
    with tempfile.TemporaryDirectory(prefix="native-call-", dir=absolute_path(staging_parent)) as temporary:
        base = absolute_path(temporary)
        if prepared_runtime is None:
            stage_candidate(runtime, base / "runtime", record, fingerprint=fingerprint, sdk_sha256=sdk_sha256)
        else:
            source = absolute_path(prepared_runtime)
            if source != absolute_path(staging_parent) / "runtime-stage":
                raise ValueError("invalid prepared native runtime location")
            os.rename(source, base / "runtime")
            if preload is None:
                # The privately staged tree may have changed during setup.
                verify(base / "runtime", record["files"])
            # The preload path instead verifies the complete snapshot against
            # pinned platform assets before starting the inactive worker. Its
            # runtime record is checked again below before plugin activation.
        stage(plugin, base / "plugin", plugin_files)
        stage(seed, base / "seed", seed_files, allow_empty=True)
        (base / "data").mkdir()
        inputs = base / "inputs"
        inputs.mkdir()
        request_path = inputs / "request.json"
        request_path.write_text(envelope, encoding="utf-8")
        private_runtime = base / "runtime"
        executable = private_runtime / "python" / "python.exe"
        # Linux/macOS backends define their interpreter layout when implemented.
        bootstrap = ("import sys,runpy; sys.path.insert(0," +
                     repr(str(private_runtime / "site-packages")) +
                     "); sys.argv=['core.plugin_worker'," + repr(str(request_path)) +
                     "]; runpy.run_module('core.plugin_worker',run_name='__main__')")
        environment = {"OPSKIT_SANDBOX": "1", "OPSKIT_DATA_DIR": str(base / "data"),
                       "OPSKIT_PLUGIN_ROOT": str(base / "plugin"),
                       "OPSKIT_SEED_ROOT": str(base / "seed"),
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        checkpoint()
        if preload is not None:
            from core.native_preload import open_preloaded_worker
            startup_deadline = deadline() if callable(deadline) else deadline
            with open_preloaded_worker(backend, base, deadline=startup_deadline, cancel_event=preload.cancelled) as worker:
                def validate():
                    if worker.child.poll() is not None:
                        # A worker can be stopped externally while its menu is
                        # idle. Retire it before retrying the ordinary path.
                        from core.plugin_entry_preload import PreloadStale
                        raise PreloadStale()
                    # User-idle time is excluded from the invocation budget;
                    # take() has installed the selected deadline on this thread.
                    from core.plugin_sandbox import _invocations
                    selected_deadline = getattr(_invocations, "deadline", None) or startup_deadline
                    with verify_runtime_snapshot(base / "runtime", record["files"],
                                                 deadline=selected_deadline,
                                                 cancel_event=preload.cancelled) as wait:
                        verify(base / "plugin", plugin_files)
                        verify(base / "seed", seed_files, allow_empty=True)
                        validate_preload()
                        wait()
                return preload.handoff(worker, checkpoint=checkpoint, dispatch=dispatch,
                                       output=output, validate=validate, state=preload_state)
        # -I ignores PYTHONIOENCODING. Explicit UTF-8 mode keeps Chinese plugin
        # output consistent with the UTF-8 pipe protocol on Windows.
        with open_worker(backend, str(executable), ["-I", "-X", "utf8", "-S", "-B", "-u", "-c", bootstrap],
                         readonly_trees=(base,),
                         writable_trees=(base / "data",), environment=environment,
                         cwd=str(base / "data")) as child:
            return exchange(child, deadline=deadline, dispatch=dispatch,
                            checkpoint=checkpoint, output=output)
