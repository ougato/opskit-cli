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


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
def test_interpreter_probe_uses_actual_isolated_target(sdk_runtime, tmp_path):
    from core.native_interpreter import probe
    from packaging.tags import sys_tags
    root, _ = sdk_runtime
    tags, markers = probe(root / "python", staging_parent=tmp_path)
    assert tags == set(sys_tags())
    assert markers["sys_platform"] == "win32"
    assert markers["python_full_version"].startswith(str(sys.version_info.major) + ".")


@pytest.mark.skipif(os.name != "nt", reason="real Windows isolation required")
def test_public_invocation_commits_native_worker_state(sdk_runtime, tmp_path, monkeypatch):
    from core import plugin_sandbox as sandbox
    from core.plugin import load_manifest
    from core.plugin_trust import compute_fingerprint, grant
    from core.native_runtime_catalog import RuntimeCatalog
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "name: demo\nversion: 1.0.0\napi_version: 3\nkind: python\nentry: public_example\n")
    (plugin / "public_example.py").write_text(
        "from core.sdk import plugin_data_dir\n"
        "def cli(argv):\n"
        "    (plugin_data_dir('demo') / 'committed.txt').write_text('native-state')\n"
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
        "from core.sdk import current_lang, register_locale, t, text_input, plugin_data_dir, run\n"
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
        "    value = text_input(t('example.title'))\n"
        "    (plugin_data_dir('demo') / 'result.txt').write_text(value, encoding='utf-8')\n"
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
    assert "example" not in sys.modules
