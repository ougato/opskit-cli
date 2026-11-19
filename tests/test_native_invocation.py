"""Real SDK execution inside AppContainer, with no host plugin import."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import time

import pytest

from core.native_runtime import inventory
from core.plugin_runtime_inventory import snapshot, code_digest


@pytest.fixture(scope="module")
def sdk_runtime(tmp_path_factory):
    root = tmp_path_factory.mktemp("sdk-runtime")
    python = root / "python"
    python.mkdir()
    source = Path(sys.base_prefix)
    for path in source.iterdir():
        if path.is_file() and (path.suffix.lower() == ".dll" or path.name == "python.exe"):
            shutil.copyfile(path, python / path.name)
    shutil.copytree(source / "Lib", python / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip"))
    shutil.copytree(source / "DLLs", python / "DLLs", ignore=shutil.ignore_patterns("*.pdb"))
    site = root / "site-packages"
    site.mkdir()
    # Test-only SDK dependencies; production uses locked offline wheels.
    for name in ("yaml", "rich", "pygments", "markdown_it", "mdurl", "typer", "click",
                 "shellingham", "typing_extensions", "annotated_doc", "platformdirs", "psutil"):
        spec = importlib.util.find_spec(name)
        if spec is None:
            continue
        origin = Path(spec.origin)
        if spec.submodule_search_locations:
            shutil.copytree(origin.parent, site / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copyfile(origin, site / origin.name)
    files = snapshot()
    for name, content in files.items():
        target = site / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    record = {"schema_version": 1, "backend": "native", "fingerprint": "a" * 64,
              "lock_sha256": "b" * 64, "sdk_sha256": code_digest(files),
              "wheels": {"test-sdk": "c" * 64}, "files": inventory(root)}
    return root, record


@pytest.mark.parametrize("failure", ["runtime_changed", "worker_exited"])
def test_preload_rechecks_worker_and_runtime_before_plugin_activation(tmp_path, monkeypatch, failure):
    from contextlib import contextmanager
    from threading import Event
    from types import SimpleNamespace
    from core import native_invocation, native_preload
    from core.native_runtime import stage
    from core.plugin_entry_preload import PreloadStale

    runtime = tmp_path / "source"
    (runtime / "python").mkdir(parents=True)
    (runtime / "python" / "python.exe").write_bytes(b"trusted")
    files = inventory(runtime)
    fingerprint, sdk_sha256 = "a" * 64, "b" * 64
    record = {"schema_version": 1, "backend": "native", "fingerprint": fingerprint,
              "lock_sha256": "c" * 64, "sdk_sha256": sdk_sha256,
              "wheels": {"sdk": "d" * 64}, "files": files}
    prepared = tmp_path / "runtime-stage"
    stage(runtime, prepared, files)
    plugin, seed = tmp_path / "plugin", tmp_path / "seed"
    plugin.mkdir()
    seed.mkdir()
    (plugin / "plugin.py").write_text("pass")
    activated = []

    @contextmanager
    def open_preloaded_worker(backend, base, **kwargs):
        # The platform identity check occurs before this point. Simulate a
        # later change while the inactive worker waits for the user's choice.
        if failure == "runtime_changed":
            (base / "runtime" / "python" / "python.exe").write_bytes(b"changed")
        child = SimpleNamespace(poll=lambda: 1 if failure == "worker_exited" else None)
        yield SimpleNamespace(child=child)

    class Preload:
        cancelled = Event()

        def handoff(self, worker, *, validate, **kwargs):
            validate()
            activated.append(True)

    monkeypatch.setattr(native_invocation, "get_backend", lambda: object())
    monkeypatch.setattr(native_preload, "open_preloaded_worker", open_preloaded_worker)
    expected = ValueError if failure == "runtime_changed" else PreloadStale
    with pytest.raises(expected):
        native_invocation.invoke(
            runtime, record, plugin, inventory(plugin), seed, {}, {"action": "entry"},
            fingerprint=fingerprint, sdk_sha256=sdk_sha256, deadline=time.monotonic() + 10,
            dispatch=None, checkpoint=lambda: None, output=None, staging_parent=tmp_path,
            prepared_runtime=prepared, preload=Preload(), validate_preload=lambda: None,
            preload_state={},
        )
    assert not activated


def test_normal_invocation_rechecks_prepared_runtime_before_launch(tmp_path, monkeypatch):
    from core import native_invocation
    from core.native_runtime import stage

    runtime = tmp_path / "source"
    (runtime / "python").mkdir(parents=True)
    (runtime / "python" / "python.exe").write_bytes(b"trusted")
    files = inventory(runtime)
    fingerprint, sdk_sha256 = "a" * 64, "b" * 64
    record = {"schema_version": 1, "backend": "native", "fingerprint": fingerprint,
              "lock_sha256": "c" * 64, "sdk_sha256": sdk_sha256,
              "wheels": {"sdk": "d" * 64}, "files": files}
    prepared = tmp_path / "runtime-stage"
    stage(runtime, prepared, files)
    (prepared / "python" / "python.exe").write_bytes(b"changed")
    plugin, seed = tmp_path / "plugin", tmp_path / "seed"
    plugin.mkdir()
    seed.mkdir()
    (plugin / "plugin.py").write_text("pass")
    monkeypatch.setattr(native_invocation, "get_backend", lambda: object())
    monkeypatch.setattr(native_invocation, "open_worker", lambda *a, **k: pytest.fail("worker launched"))
    with pytest.raises(ValueError, match="content does not match"):
        native_invocation.invoke(
            runtime, record, plugin, inventory(plugin), seed, {}, {"action": "entry"},
            fingerprint=fingerprint, sdk_sha256=sdk_sha256, deadline=time.monotonic() + 10,
            dispatch=None, checkpoint=lambda: None, output=None, staging_parent=tmp_path,
            prepared_runtime=prepared,
        )


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
def test_interpreter_probe_uses_actual_isolated_target(sdk_runtime, tmp_path):
    from core.native_interpreter import probe
    from packaging.tags import sys_tags
    root, _ = sdk_runtime
    tags, markers = probe(root / "python", staging_parent=tmp_path)
    assert tags == set(sys_tags())
    assert markers["sys_platform"] == "win32"
    assert markers["python_full_version"].startswith(str(sys.version_info.major) + ".")


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
def test_public_invocation_commits_native_worker_state(sdk_runtime, tmp_path, monkeypatch):
    from core import plugin_sandbox as sandbox
    from core.plugin import load_manifest
    from core.plugin_trust import compute_fingerprint, grant
    from core.native_runtime_catalog import RuntimeCatalog
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: public_example\n")
    (plugin / "public_example.py").write_text(
        "from core.sdk import plugin_data_dir, waiting\n"
        "def cli(argv):\n"
        "    with waiting():\n"
        "        (plugin_data_dir('demo') / 'committed.txt').write_text('native-state')\n"
        "    return 23\n")
    fingerprint = compute_fingerprint(plugin)
    grant("demo", fingerprint, "1.0.0")
    source, original = sdk_runtime
    record = {**original, "fingerprint": fingerprint}
    RuntimeCatalog(tmp_path / "data/native-plugin-runtimes").publish(
        "demo", source, record, check_identity=lambda *args: True)
    assert not hasattr(sandbox, "docker_command")
    assert sandbox.invoke(load_manifest(plugin), "cli") == 23
    assert (tmp_path / "data/plugin-data/demo/self/state/committed.txt").read_text() == "native-state"
    assert "public_example" not in sys.modules


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
def test_old_sdk_is_refreshed_before_real_native_invocation(sdk_runtime, tmp_path, monkeypatch):
    from core import plugin_sandbox as sandbox
    from core.native_runtime import stage, inventory
    from core.native_runtime_catalog import RuntimeCatalog
    from core.plugin import load_manifest
    from core.plugin_trust import compute_fingerprint, grant

    data = tmp_path / 'data'
    monkeypatch.setenv('OPSKIT_DATA_DIR', str(data))
    plugin = tmp_path / 'plugin'
    plugin.mkdir()
    (plugin / 'plugin.yaml').write_text(
        'name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: public_example\n')
    (plugin / 'public_example.py').write_text('def cli(argv):\n    return 23\n')
    fingerprint = compute_fingerprint(plugin)
    grant('demo', fingerprint, '1.0.0')
    original, record = sdk_runtime
    old = tmp_path / 'old-runtime'
    stage(original, old, record['files'])
    worker = old / 'site-packages/core/plugin_worker.py'
    worker.write_bytes(worker.read_bytes() + b'\n# old sdk\n')
    record = dict(record, fingerprint=fingerprint, sdk_sha256='0' * 64, files=inventory(old))
    catalog = RuntimeCatalog(data / 'native-plugin-runtimes')
    old_id = catalog.publish('demo', old, record, check_identity=lambda *args: True)
    assert sandbox.invoke(load_manifest(plugin), 'cli') == 23
    from core.plugin_runtime_inventory import code_digest
    runtime, updated = catalog.load('demo', fingerprint=fingerprint, sdk_sha256=code_digest())
    assert runtime.parent.name != old_id and updated['wheels'] == record['wheels']


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
def test_developer_example_cli_runs_in_native_sandbox(sdk_runtime, tmp_path, monkeypatch):
    from core import plugin_sandbox as sandbox
    from core.plugin import load_manifest
    from core.plugin_trust import compute_fingerprint, grant
    from core.native_runtime_catalog import RuntimeCatalog
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    example = Path(__file__).resolve().parents[1] / "docs" / "examples" / "example-plugin"
    plugin = tmp_path / "plugin"
    shutil.copytree(example, plugin, ignore=shutil.ignore_patterns("__pycache__"))
    fingerprint = compute_fingerprint(plugin)
    grant("example", fingerprint, "1.0.0")
    source, original = sdk_runtime
    RuntimeCatalog(tmp_path / "data/native-plugin-runtimes").publish(
        "example", source, {**original, "fingerprint": fingerprint}, check_identity=lambda *args: True)
    assert sandbox.invoke(load_manifest(plugin), "cli", args=["--hello"]) == 0
    assert "example_plugin" not in sys.modules


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
def test_preloaded_sandbox_does_not_import_plugin_before_activation(sdk_runtime, tmp_path, monkeypatch):
    """Plugin code waits for activation; archive verification is tested separately."""
    import psutil
    from core import plugin_json
    from core.native_preload import open_preloaded_worker
    from core import native_preload
    from core.native_runtime import stage
    from core.native_sandbox import get_backend

    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "example.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "(Path(os.environ['OPSKIT_DATA_DIR']) / 'activated').write_text('yes')\n"
        "def cli(argv): return 23\n")
    seed = tmp_path / "seed"
    seed.mkdir()
    request = {"action": "cli", "name": "demo", "kind": "python", "entry": "example",
               "args": [], "kwargs": {}, "service": "", "method": "", "caller": "platform",
               "service_contract": None}
    runtime, record = sdk_runtime
    base = tmp_path / "stage"
    base.mkdir()
    stage(runtime, base / "runtime", record["files"])
    stage(plugin, base / "plugin", inventory(plugin))
    stage(seed, base / "seed", {}, allow_empty=True)
    (base / "data").mkdir()
    (base / "inputs").mkdir()
    (base / "inputs" / "request.json").write_text(plugin_json.dumps_message(request), encoding="utf-8")
    marker = base / "data" / "activated"
    backend = get_backend()
    # This fixture uses the local test Python rather than the pinned archive.
    monkeypatch.setattr(native_preload, "_verify_platform_runtime", lambda base: None)

    # Cancellation without activation kills the child and never imports the plugin.
    with open_preloaded_worker(backend, base, deadline=time.monotonic() + 30) as warmed:
        pid = warmed.child.pid
        time.sleep(.2)
        assert warmed.child.poll() is None
        assert not marker.exists()
    assert not marker.exists()
    assert not psutil.pid_exists(pid)

    # An invalid activation signal exits before the plugin is imported.
    with open_preloaded_worker(backend, base, deadline=time.monotonic() + 30) as warmed:
        warmed.child.stdin.write(b"invalid activation\n")
        warmed.child.stdin.flush()
        assert warmed.child.wait(10) == 3
        assert not marker.exists()

    with open_preloaded_worker(backend, base, deadline=time.monotonic() + 30) as warmed:
        assert not marker.exists()
        result = warmed.activate(deadline=time.monotonic() + 30, dispatch=lambda _: None,
                                 checkpoint=lambda: None, output=lambda _: None)
        assert marker.read_text() == "yes"
        with pytest.raises(RuntimeError, match="no longer available"):
            warmed.activate(deadline=time.monotonic() + 30, dispatch=lambda _: None,
                            checkpoint=lambda: None, output=lambda _: None)
    assert result["value"] == 23
    assert "example" not in sys.modules


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
def test_preloaded_entry_rechecks_state_and_revocation(sdk_runtime, tmp_path, monkeypatch):
    """A ready snapshot cannot execute after state changes or authorization is revoked."""
    from core import native_preload, plugin_sandbox
    from core.native_runtime_catalog import RuntimeCatalog
    from core.plugin import load_manifest
    from core.plugin_entry_preload import EntryPreload
    from core.plugin_trust import compute_fingerprint, grant, revoke

    data = tmp_path / "data"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(data))
    monkeypatch.setattr(native_preload, "_verify_platform_runtime", lambda base: None)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: public_example\n")
    (plugin / "public_example.py").write_text(
        "from types import SimpleNamespace\n"
        "from core.sdk import plugin_data_dir\n"
        "def entry():\n"
        "    (plugin_data_dir('demo') / 'activated.txt').write_text('executed')\n"
        "def register():\n"
        "    return SimpleNamespace(entry=entry)\n")
    state = data / "plugin-data/demo/self/state"
    state.mkdir(parents=True)
    (state / "previous.txt").write_text("before")
    fingerprint = compute_fingerprint(plugin)
    grant("demo", fingerprint, "1.0.0")
    source, original = sdk_runtime
    record = {**original, "fingerprint": fingerprint}
    catalog = RuntimeCatalog(data / "native-plugin-runtimes")
    catalog.publish("demo", source, record, check_identity=lambda *args: True)
    manifest = load_manifest(plugin)

    def ready():
        session = EntryPreload(manifest)
        deadline = time.monotonic() + 90
        while session._ready.empty() and not session.done and time.monotonic() < deadline:
            time.sleep(.05)
        assert not session.done, repr(session._error)
        assert not session._ready.empty()
        return session

    stale = ready()
    try:
        (state / "previous.txt").write_text("changed")
        assert stale.take() is False
        assert not (state / "activated.txt").exists()
    finally:
        stale.cancel(wait=True)

    replaced = ready()
    try:
        catalog.publish("demo", source, record, check_identity=lambda *args: True)
        assert replaced.take() is False
        assert not (state / "activated.txt").exists()
    finally:
        replaced.cancel(wait=True)

    revoked = ready()
    try:
        revoke("demo")
        with pytest.raises(plugin_sandbox.SandboxError):
            revoked.take()
        assert not (state / "activated.txt").exists()
    finally:
        revoked.cancel(wait=True)

    grant("demo", fingerprint, "1.0.0")
    active = ready()
    try:
        assert active.take() is True
        assert (state / "activated.txt").read_text() == "executed"
        assert (state / "previous.txt").read_text() == "changed"
    finally:
        active.cancel(wait=True)


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
@pytest.mark.parametrize("language", ["zh", "en"])
def test_actual_sdk_worker_language_rpc_and_state(sdk_runtime, tmp_path, monkeypatch, language):
    from core import i18n
    from core.native_invocation import invoke
    monkeypatch.setattr(i18n, "_lang", language)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "example.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "from core.sdk import current_lang, load_yaml, register_locale, save_yaml, t, text_input, plugin_data_dir, run\n"
        "def cli(argv):\n"
        "    try:\n"
        "        child = run([sys.executable, '-I', '-S', '-c', \"print('child-ok')\"], capture=True, timeout=5)\n"
        "        assert child.stdout == 'child-ok'\n"
        "    except Exception:\n"
        "        import traceback\n"
        "        traceback.print_exc()\n"
        "        raise\n"
        "    try:\n"
        "        Path(argv[0]).read_text()\n"
        "        raise RuntimeError('host secret was accessible')\n"
        "    except PermissionError:\n"
        "        pass\n"
        "    try:\n"
        "        Path(__file__).write_text('overwrite')\n"
        "        raise RuntimeError('plugin source was writable')\n"
        "    except PermissionError:\n"
        "        pass\n"
        "    register_locale({'zh': {'example': {'title': '你好'}}, 'en': {'example': {'title': 'Hello'}}})\n"
        "    value = text_input(['OpsKit'], t('example.title'))\n"
        "    (plugin_data_dir('demo') / 'result.txt').write_text(value, encoding='utf-8')\n"
        "    config = plugin_data_dir('demo') / 'config.yaml'\n"
        "    save_yaml(config, {'value': 'previous'})\n"
        "    save_yaml(config, {'value': value})\n"
        "    assert load_yaml(config) == {'value': value}\n"
        "    return {'language': current_lang(), 'title': t('example.title'), 'value': value}\n",
        encoding="utf-8")
    seed = tmp_path / "seed"
    seed.mkdir()
    secret = tmp_path / "host-secret.txt"
    secret.write_text("host-only", encoding="utf-8")
    request = {"action": "cli", "name": "demo", "kind": "python", "entry": "example",
               "args": [str(secret)], "kwargs": {}, "service": "", "method": "", "caller": "platform",
               "service_contract": None}
    calls, logs = [], []
    def dispatch(message):
        calls.append(message)
        return {"value": "测试"}
    runtime, record = sdk_runtime
    result = invoke(runtime, record, plugin, inventory(plugin), seed, {}, request,
                    fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"],
                    deadline=time.monotonic() + 60, dispatch=dispatch,
                    checkpoint=lambda: None, output=lambda line: (logs.append(line), print(line)),
                    staging_parent=tmp_path)
    assert result["value"] == {"language": language,
                               "title": "你好" if language == "zh" else "Hello", "value": "测试"}, logs
    assert calls[0]["method"] == "text_input"
    import base64
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(result["state"]))) as archive:
        assert archive.extractfile("state/result.txt").read().decode("utf-8") == "测试"
        assert "测试" in archive.extractfile("state/config.yaml").read().decode("utf-8")
    assert "example" not in sys.modules

@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
def test_long_workspace_interpreter_probe_uses_verified_alias(sdk_runtime, tmp_path, monkeypatch):
    from core.native_interpreter import probe
    from core.native_short_path import _short_path
    from core.native_runtime import absolute_path
    parent = absolute_path(tmp_path)
    while len(str(parent)) < 185:
        parent /= "long-workspace-component-abcdefghij"
        parent.mkdir()
    # The optimization is optional; a volume with disabled 8.3 names cannot
    # provide the precondition for this real-runtime test.
    if len(str(_short_path(parent))) >= len(str(parent)):
        pytest.skip("this volume has no usable 8.3 alias")
    root, _ = sdk_runtime
    tags, markers = probe(root / "python", staging_parent=parent)
    assert tags
    assert markers["sys_platform"] == "win32"

@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
@pytest.mark.timeout(300)
def test_long_workspace_actual_plugin_invocation(sdk_runtime, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from core import native_invocation
    from core.native_invocation import invoke
    from core.native_runtime import absolute_path
    from core.native_short_path import _short_path
    parent = absolute_path(tmp_path)
    while len(str(parent)) < 185:
        parent /= "long-workspace-component-abcdefghij"
        parent.mkdir()
    if len(str(_short_path(parent))) >= len(str(parent)):
        pytest.skip("this volume has no usable 8.3 alias")
    selected = []
    original = native_invocation.short_execution_root
    @contextmanager
    def observe(root, executable):
        with original(root, executable) as short:
            selected.append((root, short))
            yield short
    monkeypatch.setattr(native_invocation, "short_execution_root", observe)
    plugin, seed = tmp_path / "plugin", tmp_path / "seed"
    plugin.mkdir()
    seed.mkdir()
    (plugin / "example.py").write_text("def cli(argv):\n    return 42\n", encoding="utf-8")
    request = {"action": "cli", "name": "demo", "kind": "python", "entry": "example",
               "args": [], "kwargs": {}, "service": "", "method": "", "caller": "platform",
               "service_contract": None}
    runtime, record = sdk_runtime
    result = invoke(runtime, record, plugin, inventory(plugin), seed, {}, request,
                    fingerprint="a" * 64, sdk_sha256=record["sdk_sha256"],
                    deadline=time.monotonic() + 120, dispatch=lambda _: None,
                    checkpoint=lambda: None, output=lambda _: None, staging_parent=parent)
    assert result["value"] == 42
    assert selected and len(str(selected[0][1])) < len(str(selected[0][0]))
