import json
import os
import subprocess
import sys
from pathlib import Path

import re

import pytest
from typer.testing import CliRunner

from core.cli import configure
from core.plugin_trust import trusted_record

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


def test_install_preparation_failure_does_not_publish_or_trust(isolated, monkeypatch):
    from core.native_sandbox import NativeSandboxError
    def unavailable(*args, **kwargs):
        raise NativeSandboxError("fetch_setup_failed")
    monkeypatch.setattr("core.plugin_bootstrap.ensure_fetch", unavailable)
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
    assert help_result.returncode == 0 and "lock-file" in re.sub(r"\x1b\[[0-9;]*m", "", help_result.stdout)
    missing = subprocess.run([sys.executable, str(script), str(tmp_path)],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert missing.returncode == 2
    rejected = subprocess.run([sys.executable, str(script), str(tmp_path),
        "--base-image", "https://user:SECRET@example.invalid", "--lock-file", str(tmp_path / "unused.lock")],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert rejected.returncode == 2 and "SECRET" not in rejected.stdout + rejected.stderr
    assert "Traceback" not in rejected.stderr
    assert not (tmp_path / "runtime").exists() and not (tmp_path / "clones").exists()

@pytest.mark.parametrize("args", [(), ("demo", "--all"), ("--all", "--allow-private"),
                                  ("--all", "--fetch-profile", "profile")])
def test_update_rejects_invalid_target_or_bulk_source_options(isolated, monkeypatch, args):
    from plugin import commands
    monkeypatch.setattr(commands, "manifests", lambda: pytest.fail("invalid options reached discovery"))
    result = call("plugin", "update", *args)
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == 2


def test_update_all_reports_each_result_and_continues_after_failures(isolated, monkeypatch):
    from core.plugin import PluginManifest
    from plugin import commands
    statuses = {
        "broken": (False, "private diagnostic"),
        "pending": (True, "unchanged"),
        "latest": (True, "unchanged"),
        "older": (True, "downgrade"),
        "updated": (True, "updated"),
    }
    manifests = [PluginManifest(name=name, version="1.0.0", api_version=4, kind="python", entry=name)
                 for name in statuses]
    calls = []

    def update(manifest, *, fetch_profile, allow_private, automatic):
        calls.append(manifest.name)
        assert fetch_profile is None and not allow_private and automatic
        return statuses[manifest.name]

    monkeypatch.setattr(commands, "manifests", lambda: manifests)
    monkeypatch.setattr(commands, "update", update)
    monkeypatch.setattr(commands, "trust_status", lambda manifest:
                        commands.TRUST_OK if manifest.name == "latest" else commands.TRUST_CHANGED)
    result = call("plugin", "update", "--all")
    assert result.exit_code == 1 and "private diagnostic" not in result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["data"]["failed"] == 2
    assert calls == list(statuses)
    results = payload["data"]["plugins"]
    assert [(item["name"], item["status"], item.get("authorization_required")) for item in results] == [
        ("broken", "failed", None),
        ("pending", "unchanged", True),
        ("latest", "unchanged", False),
        ("older", "downgrade", True),
        ("updated", "updated", True),
    ]
    assert {item["name"] for item in results if "error" in item} == {"broken", "older"}

    single = call("plugin", "update", "pending")
    assert single.exit_code == 0
    assert json.loads(single.stdout)["data"] == results[1]


def test_update_all_empty_installation(isolated):
    result = call("plugin", "update", "--all")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"] == {"plugins": [], "failed": 0}


def test_permissions_cli_filters_inspects_and_revokes_permanent_grant(isolated):
    from core.native_sandbox import CapabilityRequest, GrantChoice
    from core.plugin_capabilities import CapabilityAuthority

    authority = CapabilityAuthority()
    grants = [CapabilityRequest(name, "a" * 64, "directory.read", str(isolated))
              for name in ("demo", "other")]
    try:
        for request in grants:
            authority.require(request, check_identity=lambda *_: True, prompt=lambda _: GrantChoice.ALWAYS)

        listed = call("plugin", "permissions", "list", "--plugin", "demo")
        assert listed.exit_code == 0, listed.output
        [grant] = json.loads(listed.stdout)["data"]
        assert grant["plugin"] == "demo" and grant["resource"] == str(isolated)
        grant_id = grant["id"]
        shown = call("plugin", "permissions", "show", grant_id)
        assert shown.exit_code == 0 and json.loads(shown.stdout)["data"] == grant
        assert call("plugin", "permissions", "revoke", "invalid").exit_code == 2
        assert authority.list_grants() == grants

        revoked = call("plugin", "permissions", "revoke", grant_id)
        assert revoked.exit_code == 0 and json.loads(revoked.stdout)["data"] == {"revoked": grant_id}
        assert call("plugin", "permissions", "show", grant_id).exit_code == 2
        assert authority.list_grants() == [grants[1]]
    finally:
        authority.close()


@pytest.mark.parametrize("args", [("inspect-updates",), ("inspect-native-profiles",),
                                  ("inspect-native-workspaces",), ("inspect-native-cache", "runtime"),
                                  ("inspect-native-cache", "fetch"), ("inspect-native-cache", "prepare")])
def test_maintenance_inspection_cli_in_empty_workspace(isolated, args):
    inspected = call("plugin", *args, "--limit", "1")
    if args == ("inspect-native-profiles",) and sys.platform != "win32":
        assert inspected.exit_code == 1
        assert json.loads(inspected.stdout)["ok"] is False
        return
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.stdout)["data"] == []
    assert call("plugin", *args, "--limit", "0").exit_code == 2


def test_disk_policy_cli_reads_and_persists_in_isolated_config(isolated):
    shown = call("plugin", "disk-policy")
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.stdout)["data"] == {"minimum_free_mib": 2048, "hard_quota": False}
    assert call("plugin", "disk-policy", "--minimum-free-mib", "255").exit_code == 2
    saved = call("plugin", "disk-policy", "--minimum-free-mib", "512")
    assert saved.exit_code == 0, saved.output
    shown = call("plugin", "disk-policy")
    assert json.loads(shown.stdout)["data"] == {"minimum_free_mib": 512, "hard_quota": False}
