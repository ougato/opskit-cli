"""Explicit update checks are bounded, read-only and do not imply trust."""
import json
import multiprocessing
import os
from pathlib import Path
import time

import httpx
import pytest
from typer.testing import CliRunner

import main
from core import update_cli, updater


@pytest.mark.parametrize("state", ["scheduled", "applied", "failed", "applied_recovery_incomplete"])
def test_status_reports_whitelisted_job_result(tmp_path, monkeypatch, state):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    job_id = "a" * 32
    cache = updater._get_cache_path()
    updater._write_update_record(cache, {"apply_job": job_id})
    job = cache.parent / "update-jobs" / job_id
    updater._write_update_record(job / "job.json", {"version": updater.APP_VERSION + 1, "state": "scheduled"})
    if state != "scheduled":
        updater._write_update_record(job / "result.json", {"version": updater.APP_VERSION + 1, "state": state,
                                                           "raw_exception": "private-token", "target": "private-path"})
    before = cache.read_bytes()
    result = update_cli.status(timeout=10)
    assert result["apply_job"] == {"id": job_id, "version": updater.APP_VERSION + 1,
                                   "state": state, "health_confirmed": False, "helper_status": "unknown"}
    assert "private-token" not in str(result) and "private-path" not in str(result)
    assert cache.read_bytes() == before


def test_status_rejects_job_id_path_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    updater._write_update_record(updater._get_cache_path(), {"apply_job": "../other-job"})
    with pytest.raises(RuntimeError, match="unavailable or invalid"):
        update_cli.status(timeout=10)


def test_real_status_does_not_create_runtime_data(tmp_path, monkeypatch):
    root = tmp_path / "not-created"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(root))
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("explicit status must not boot"))
    result = CliRunner().invoke(main.app, ["--output", "json", "update", "status", "--timeout", "10"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["pending_present"] is False
    assert payload["data"]["pending_verification"] == "not_checked"
    assert not root.exists()


def test_status_keeps_pending_metadata_private_and_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    cache = updater._get_cache_path()
    cache.parent.mkdir()
    cache.write_text(json.dumps({"pending_version": updater.APP_VERSION + 1, "github_token": "private-token"}))
    updater._get_pending_path().write_bytes(b"unverified artifact")
    before = cache.read_bytes(), cache.stat().st_mtime_ns
    result = update_cli.status(timeout=10)
    assert result["pending_present"]
    assert result["pending_verification"] == "not_checked"
    assert "private-token" not in str(result)
    assert (cache.read_bytes(), cache.stat().st_mtime_ns) == before


def test_check_forwards_budget_without_boot(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("check must not boot"))
    monkeypatch.setattr(update_cli, "check", lambda **kwargs: seen.append(kwargs) or {"available": False})
    result = CliRunner().invoke(main.app, ["--output", "json", "update", "check", "--timeout", "2"])
    assert result.exit_code == 0
    assert seen == [{"timeout": 2}]


@pytest.mark.parametrize("manifest", [
    {"latest_build": updater.APP_VERSION, "rollout": 100},
    {"latest_build": updater.APP_VERSION + 1, "rollout": 100},
    {"latest_build": updater.APP_VERSION + 1, "kill_switch": True},
])
def test_readonly_query_never_saves_cache_or_creates_identity(tmp_path, monkeypatch, manifest):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(updater, "fetch_bootstrap", lambda: manifest)
    monkeypatch.setattr(updater, "_get_machine_id", lambda: pytest.fail("full rollout must not create identity"))
    updater.query_update_target({"repo": "owner/repository"})
    assert not (tmp_path / "absent").exists()
    assert updater._query_readonly.get() is False


def test_uninitialized_partial_rollout_is_error_without_creating_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(updater, "fetch_bootstrap", lambda: {"latest_build": updater.APP_VERSION + 1, "rollout": 50})
    with pytest.raises(RuntimeError, match="identity is not initialized"):
        updater.query_update_target({"repo": "owner/repository"})
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("status,body", [
    (503, b"unavailable"),
    (200, b"x" * 262145),
    (200, b"[]"),
], ids=["http-error", "oversized-body", "invalid-shape"])
def test_failed_query_is_not_no_update_and_does_not_write(tmp_path, monkeypatch, status, body):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(updater, "fetch_bootstrap", lambda: None)
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(
        lambda req: httpx.Response(status, stream=httpx.ByteStream(body))), **kwargs))
    with pytest.raises(RuntimeError, match="release query failed"):
        updater.query_update_target({"repo": "owner/repository", "github_token": "private-token"})
    assert not (tmp_path / "absent").exists()


def _slow_check(connection):
    Path(os.environ["OPSKIT_TEST_UPDATE_MARKER"]).write_text("started")
    time.sleep(30)


def test_check_timeout_reaps_actual_started_worker(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / "started"
    monkeypatch.setenv("OPSKIT_TEST_UPDATE_MARKER", str(marker))
    monkeypatch.setattr(update_cli, "_check_worker", _slow_check)
    before = {child.pid for child in multiprocessing.active_children()}
    budget, observed = expire_worker_after_marker(marker)
    with pytest.raises(TimeoutError, match="update check"):
        update_cli.check(timeout=budget)
    assert marker.read_text() == "started"
    assert observed, "deadline injection must follow the actual started worker"
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_worker_result_never_exposes_error_details(monkeypatch):
    from core import config
    monkeypatch.setattr(config, "load_config", lambda **kwargs: {"update": {}})
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: (_ for _ in ()).throw(RuntimeError("private-token")))
    messages = []
    class Connection:
        def send_bytes(self, message):
            messages.append(json.loads(message))
        def close(self):
            pass
    update_cli._check_worker(Connection())
    assert messages == [{"ok": False, "error": "update check failed"}]


def test_streamed_release_success_has_no_local_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(updater, "fetch_bootstrap", lambda: None)
    build = updater.APP_VERSION + 1
    filename = updater._asset_filename()
    body = json.dumps({"tag_name": f"v{build}", "body": "", "assets": [
        {"name": filename, "browser_download_url": f"https://github.com/owner/repository/releases/download/v{build}/{filename}"}]}).encode()
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(body))
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    target = updater.query_update_target({"repo": "owner/repository", "github_token": "private-token"})
    assert target[0] == build and filename in target[1]
    assert requests[0].headers["Accept-Encoding"] == "identity"
    assert not (tmp_path / "absent").exists()


def test_config_limit_rejects_large_file_before_yaml_decode(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    path = config.get_config_path()
    path.parent.mkdir()
    path.write_bytes(b" " * 65537)
    monkeypatch.setattr(config.yaml, "safe_load", lambda *args: pytest.fail("oversized YAML reached parser"))
    with pytest.raises(ValueError, match="size limit"):
        config.load_config(max_bytes=65536)


def test_invalid_release_version_is_not_no_eligible_update(monkeypatch):
    monkeypatch.setattr(updater, "fetch_bootstrap", lambda: None)
    monkeypatch.setattr(updater, "_fetch_latest", lambda *args: {"tag_name": "not-a-version"})
    with pytest.raises(RuntimeError, match="invalid update release version"):
        updater.query_update_target({"repo": "owner/repository"})


@pytest.mark.parametrize("url", ["http://example.invalid/artifact", "https://user:password@example.invalid/artifact"])
def test_insecure_artifact_url_is_not_a_candidate(monkeypatch, url):
    monkeypatch.setattr(updater, "fetch_bootstrap", lambda: None)
    monkeypatch.setattr(updater, "_fetch_latest", lambda *args: {"tag_name": f"v{updater.APP_VERSION + 1}", "body": "",
        "assets": [{"name": updater._asset_filename(), "browser_download_url": url}]})
    with pytest.raises(RuntimeError, match="invalid update artifact URL"):
        updater.query_update_target({"repo": "owner/repository"})
