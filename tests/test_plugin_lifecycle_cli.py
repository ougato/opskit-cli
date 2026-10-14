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


def test_real_install_trust_update_revoke_remove(isolated):
    origin = isolated / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    manifest = origin / "plugin.yaml"
    manifest.write_text("name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo_pkg\n", encoding="utf-8")
    (origin / "demo_pkg").mkdir()
    (origin / "demo_pkg" / "__init__.py").write_text("# do not import\n", encoding="utf-8")
    git(origin, "add", "plugin.yaml", "demo_pkg/__init__.py")
    git(origin, "commit", "-m", "initial")
    installed = call("plugin", "install", str(origin), "--branch", "main")
    assert installed.exit_code == 0, installed.output
    payload = json.loads(installed.stdout)["data"]
    digest = payload["fingerprint"]
    assert not is_trusted("demo", digest)
    assert trusted_record("demo")["branch"] == "main"
    rejected = call("plugin", "trust", "demo", "--fingerprint", "0" * 64)
    assert rejected.exit_code == 2
    accepted = call("plugin", "trust", "demo", "--fingerprint", digest)
    assert accepted.exit_code == 0 and is_trusted("demo", digest)
    assert call("plugin", "disable", "demo").exit_code == 0
    assert not json.loads(call("plugin", "show", "demo").stdout)["data"]["enabled"]
    assert call("plugin", "enable", "demo").exit_code == 0
    manifest.write_text(manifest.read_text().replace("1.0.0", "1.1.0"), encoding="utf-8")
    git(origin, "add", "plugin.yaml")
    git(origin, "commit", "-m", "update")
    updated = call("plugin", "update", "demo")
    assert updated.exit_code == 0, updated.output
    assert trusted_record("demo")["fingerprint"] == ""
    assert call("plugin", "revoke", "demo").exit_code == 0
    assert call("--yes", "plugin", "remove", "demo").exit_code == 0
    assert json.loads(call("plugin", "list").stdout)["data"] == []


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
    result = call("plugin", "prepare", str(isolated), "--base-image", "python@sha256:" + "b" * 64,
                  "--lock-file", str(isolated / "requirements.lock"))
    assert result.exit_code == code and "SECRET" not in result.output
    if code != 130:
        assert json.loads(result.stdout)["ok"] is False


def test_prepare_json_preserves_runtime_arguments_without_authorizing(isolated, monkeypatch):
    from core import plugin_runtime
    calls = []
    def build(*args, **kwargs):
        calls.append((args, kwargs))
        return "sha256:" + "a" * 64
    monkeypatch.setattr(plugin_runtime, "build", build)
    result = call("plugin", "prepare", str(isolated), "--dependency", "one==1.0",
                  "--dependency", "two==2.0", "--base-image", "python@sha256:" + "b" * 64,
                  "--lock-file", str(isolated / "requirements.lock"))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {"image": "sha256:" + "a" * 64,
                                                "prepared": True, "authorized": False}
    assert calls == [((isolated.resolve(), ["one==1.0", "two==2.0"], "python@sha256:" + "b" * 64),
                     {"lock_file": isolated / "requirements.lock"})]
    assert not (isolated / "data").exists()


def test_prepare_requires_operator_lock_and_base_before_any_build(isolated, monkeypatch):
    from core import plugin_runtime
    monkeypatch.setattr(plugin_runtime, "build", lambda *args, **kwargs: pytest.fail("missing options built runtime"))
    result = call("plugin", "prepare", str(isolated))
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
