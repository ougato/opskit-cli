"""Execute the actual SDK worker from private verified native snapshots.

The lifecycle caller supplies authorization checkpoints and owns state commit.
This function never publishes worker state or imports plugin code in the host.
"""
import tempfile

from core import plugin_json
from core.native_process import open_worker
from core.native_protocol import exchange
from core.native_runtime import stage, absolute_path
from core.native_runtime_prepare import validate_candidate
from core.native_sandbox import get_backend


def invoke(runtime, record, plugin, plugin_files, seed, seed_files, request, *,
           fingerprint, sdk_sha256, deadline, dispatch, checkpoint, output,
           staging_parent):
    """Return bounded worker result; no trust or state decision is implicit."""
    checkpoint()
    validate_candidate(runtime, record, fingerprint=fingerprint, sdk_sha256=sdk_sha256)
    envelope = plugin_json.dumps_message(request)
    backend = get_backend()
    with tempfile.TemporaryDirectory(prefix="native-call-", dir=absolute_path(staging_parent)) as temporary:
        base = absolute_path(temporary)
        stage(runtime, base / "runtime", record["files"])
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
        # -I ignores PYTHONIOENCODING. Explicit UTF-8 mode keeps Chinese plugin
        # output consistent with the UTF-8 pipe protocol on Windows.
        with open_worker(backend, str(executable), ["-I", "-X", "utf8", "-S", "-B", "-u", "-c", bootstrap],
                         readonly_trees=(base,),
                         writable_trees=(base / "data",), environment=environment,
                         cwd=str(base / "data")) as child:
            return exchange(child, deadline=deadline, dispatch=dispatch,
                            checkpoint=checkpoint, output=output)
