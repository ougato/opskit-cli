import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from core.cli import configure
from core.plugin_trust import is_trusted, trusted_record

runner = CliRunner()

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
    yield tmp_path
    configure("text", False)


def call(*args):
    import main
    return runner.invoke(main.app, ["--output", "json", *args])


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-C", str(root), *args], check=True, capture_output=True)


def test_install_without_prepared_native_tools_does_not_publish_or_trust(isolated):
    origin = isolated / "origin"
    origin.mkdir()
    manifest = origin / "plugin.yaml"
    manifest.write_text("name: demo\nversion: 1.0.0\napi_version: 4\nkind: python\nentry: demo_pkg\n", encoding="utf-8")
    (origin / "demo_pkg").mkdir()
    (origin / "demo_pkg" / "__init__.py").write_text("# do not import\n", encoding="utf-8")
    installed = call("plugin", "install", str(origin), "--branch", "main")
    assert installed.exit_code == 1, installed.output
    assert json.loads(installed.stdout)["ok"] is False
    assert trusted_record("demo") is None
    assert not (isolated / "plugins/origin").exists()


def test_trust_write_failure_is_not_silenced(isolated, monkeypatch):
    from core import secrets, plugin_trust
    def denied(*args, **kwargs):
        raise PermissionError("test denied")
    monkeypatch.setattr(secrets, "atomic_write", denied)
    with pytest.raises(PermissionError):
        plugin_trust.grant("demo", "a" * 64, "1.0.0")


def test_fingerprint_json_generate_check_and_tamper_without_boot(isolated, monkeypatch):
    import main
    def forbidden():
        pytest.fail("fingerprint must not boot platform services")
    monkeypatch.setattr(main, "_boot", forbidden)
    root = isolated / "artifact"
    root.mkdir()
    source = root / "plugin.yaml"
    source.write_text("name: demo\nversion: 1.0.0\n", encoding="utf-8")
    missing = call("plugin", "fingerprint", str(root), "--check")
    assert missing.exit_code == 1 and json.loads(missing.stdout)["data"]["status"] == "missing"
    assert not (root / "CHECKSUMS.yaml").exists()
    created = call("plugin", "fingerprint", str(root))
    assert created.exit_code == 0 and json.loads(created.stdout)["data"]["status"] == "written"
    before = {item.name: item.read_bytes() for item in root.iterdir()}
    checked = call("plugin", "fingerprint", str(root), "-c")
    assert checked.exit_code == 0 and json.loads(checked.stdout)["data"] == {"status": "ok", "checked": True}
    assert before == {item.name: item.read_bytes() for item in root.iterdir()}
    source.write_text("tampered", encoding="utf-8")
    failed = call("plugin", "fingerprint", str(root), "--check")
    assert failed.exit_code == 1 and json.loads(failed.stdout)["ok"] is False
    assert json.loads(failed.stdout)["data"]["status"] == "mismatch"
    assert not (isolated / "data").exists()


@pytest.mark.parametrize("error,code", [(ValueError("SECRET"), 2),
    (RuntimeError("SECRET"), 1), (PermissionError("SECRET"), 1),
    (subprocess.CalledProcessError(1, ["SECRET"]), 1), (KeyboardInterrupt(), 130)])
def test_prepare_errors_do_not_expose_private_details(isolated, monkeypatch, error, code):
    from core import plugin_runtime
    def failed(*args, **kwargs):
        raise error
    monkeypatch.setattr(plugin_runtime, "build", failed)
    result = call("plugin", "prepare", str(isolated), "--python-runtime", str(isolated / "python"),
                  "--wheels", str(isolated / "wheels"),
                  "--lock-file", str(isolated / "requirements.lock"))
    assert result.exit_code == code and "SECRET" not in result.output
    if code != 130:
        assert json.loads(result.stdout)["ok"] is False


def test_prepare_json_preserves_runtime_arguments_without_authorizing(isolated, monkeypatch):
    from core import plugin_runtime
    calls = []
    def build(*args, **kwargs):
        calls.append((args, kwargs))
        return "a" * 32
    monkeypatch.setattr(plugin_runtime, "build", build)
    result = call("plugin", "prepare", str(isolated), "--dependency", "one==1.0",
                  "--dependency", "two==2.0", "--python-runtime", str(isolated / "python"),
                  "--wheels", str(isolated / "wheels"),
                  "--lock-file", str(isolated / "requirements.lock"))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {"runtime": "a" * 32, "backend": "native",
                                                "prepared": True, "authorized": False}
    assert calls == [((isolated.resolve(), ["one==1.0", "two==2.0"], isolated / "python"),
                     {"lock_file": isolated / "requirements.lock", "wheels": isolated / "wheels"})]
    assert not (isolated / "data").exists()


def test_prepare_requires_operator_lock_and_base_before_any_build(isolated, monkeypatch):
    from core import plugin_runtime
    monkeypatch.setattr(plugin_runtime, "build", lambda *args, **kwargs: pytest.fail("missing options built runtime"))
    result = call("plugin", "prepare", str(isolated))
    assert result.exit_code == 2 and json.loads(result.stdout)["ok"] is False


def test_prepare_fetch_uses_explicit_native_tools_without_boot_or_trust(isolated, monkeypatch):
    import main
    from core.native_fetch_catalog import FetchCatalog
    calls = []
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("preparation must not boot"))
    def prepare(self, python):
        calls.append(python)
        return "a" * 32
    monkeypatch.setattr(FetchCatalog, "prepare", prepare)
    result = call("plugin", "prepare-fetch", "--python-runtime", str(isolated / "python"))
    assert result.exit_code == 0, result.output
    assert calls == [isolated / "python"]
    assert json.loads(result.stdout)["data"] == {"toolchain": "a" * 32, "backend": "native",
                                                "prepared": True, "authorized": False}
    assert not (isolated / "data").exists() and not (isolated / "plugins").exists()


@pytest.mark.parametrize("error,code", [(ValueError("SECRET"), 2),
    (RuntimeError("SECRET"), 1), (PermissionError("SECRET"), 1),
    (subprocess.CalledProcessError(1, ["SECRET"]), 1), (KeyboardInterrupt(), 130)])
def test_prepare_fetch_sanitizes_failures(isolated, monkeypatch, error, code):
    from core.native_fetch_catalog import FetchCatalog
    def failed(*args):
        raise error
    monkeypatch.setattr(FetchCatalog, "prepare", failed)
    result = call("plugin", "prepare-fetch", "--python-runtime", str(isolated))
    assert result.exit_code == code and "SECRET" not in result.output
    if code != 130:
        assert json.loads(result.stdout)["ok"] is False


def test_prepare_fetch_requires_native_tool_inputs_before_build(isolated, monkeypatch):
    from core.native_fetch_catalog import FetchCatalog
    monkeypatch.setattr(FetchCatalog, "prepare", lambda *args: pytest.fail("missing inputs built tool"))
    result = call("plugin", "prepare-fetch")
    assert result.exit_code == 2 and json.loads(result.stdout)["ok"] is False


def test_native_fingerprint_process_preserves_platform_and_plugin_code(tmp_path):
    root = tmp_path / "artifact"
    root.mkdir()
    code = root / "plugin.py"
    code.write_text("raise RuntimeError('MUST_NOT_EXECUTE')\n", encoding="utf-8")
    environment = {**os.environ, "OPSKIT_DATA_DIR": str(tmp_path / "runtime"),
                   "OPSKIT_PLUGINS_DIR": str(tmp_path / "clones")}
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"),
               "--output", "json", "--non-interactive", "plugin", "fingerprint", str(root)]
    generated = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=20)
    assert generated.returncode == 0, generated.stderr
    assert json.loads(generated.stdout)["data"]["status"] == "written"
    before = {item.name: item.read_bytes() for item in root.iterdir()}
    checked = subprocess.run(command + ["--check"], env=environment, capture_output=True, text=True, timeout=20)
    assert checked.returncode == 0 and json.loads(checked.stdout)["data"]["status"] == "ok"
    assert before == {item.name: item.read_bytes() for item in root.iterdir()}
    assert not (tmp_path / "runtime").exists() and not (tmp_path / "clones").exists()
    assert "MUST_NOT_EXECUTE" not in generated.stdout + generated.stderr + checked.stdout + checked.stderr


def test_source_builder_reuses_cli_and_rejects_unlocked_or_secret_parameters(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_plugin_runtime.py"
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "OPSKIT_DATA_DIR": str(tmp_path / "runtime"),
                   "OPSKIT_PLUGINS_DIR": str(tmp_path / "clones")}
    help_result = subprocess.run([sys.executable, str(script), "--help"],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert help_result.returncode == 0 and "lock-file" in help_result.stdout
    missing = subprocess.run([sys.executable, str(script), str(tmp_path)],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert missing.returncode == 2
    rejected = subprocess.run([sys.executable, str(script), str(tmp_path),
        "--base-image", "https://user:SECRET@example.invalid", "--lock-file", str(tmp_path / "unused.lock")],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert rejected.returncode == 2 and "SECRET" not in rejected.stdout + rejected.stderr
    assert "Traceback" not in rejected.stderr
    assert not (tmp_path / "runtime").exists() and not (tmp_path / "clones").exists()
