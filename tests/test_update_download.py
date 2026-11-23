"""Verified generation publication preserves old candidates on failed downloads."""
from contextlib import contextmanager
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import time

import httpx
import pytest
from typer.testing import CliRunner

from core import updater, update_download


class Capture:
    def send_bytes(self, value):
        self.result = json.loads(value)

    def close(self):
        pass


def configure_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    build = updater.APP_VERSION + 1
    magic = b"MZ" if sys.platform == "win32" else b"\xcf\xfa\xed\xfe" if sys.platform == "darwin" else b"\x7fELF"
    binary = magic + b"N" * (101 * 1024)
    digest = hashlib.sha256(binary).hexdigest()
    responses = {"/asset": binary, "/asset.sha256": digest.encode()}
    def handler(request):
        return httpx.Response(200, content=responses[request.url.path])
    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: original_client(*a, transport=transport, **kw))
    @contextmanager
    def stream(method, url, **kw):
        with original_client(transport=transport) as client:
            with client.stream(method, url, **kw) as response:
                yield response
    monkeypatch.setattr(httpx, "stream", stream)
    monkeypatch.setattr("core.mirror.get_sources", lambda name, **kwargs: ["https://github.com"])
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: (build, "https://example.org/asset", digest))
    return {}, build, binary, responses


@pytest.fixture
def verified_transport(tmp_path, monkeypatch):
    return configure_transport(tmp_path, monkeypatch)


def call_worker(cfg, deadline=None):
    connection = Capture()
    update_download._worker(connection, str(updater._get_cache_path()),
                            deadline if deadline is not None else time.monotonic() + 10, cfg)
    return connection.result


def old_candidate():
    updater._write_update_record(updater._get_cache_path(), {"pending_version": updater.APP_VERSION + 1})
    old = updater._get_pending_path()
    old.write_bytes(b"previous-candidate")
    return old, updater._get_cache_path().read_bytes()


def test_confirmed_version_change_does_not_stage_a_different_update(verified_transport, monkeypatch):
    cfg, build, _, _ = verified_transport
    monkeypatch.setattr(updater, "_download_update", lambda *args: pytest.fail("unconfirmed download"))
    connection = Capture()
    cache = updater._get_cache_path()
    update_download._worker(connection, str(cache), time.monotonic() + 10, cfg, build + 1)
    assert connection.result == {"ok": True, "data": {"available": False, "reason": "target_changed"}}
    assert not cache.exists()


def test_real_checksum_pipeline_publishes_one_complete_generation(verified_transport, monkeypatch):
    cfg, build, binary, _ = verified_transport
    old, before = old_candidate()
    result = call_worker(cfg)
    assert result["ok"], result
    assert result["data"]["applied"] is False
    pending = updater._get_pending_path()
    assert pending != old and pending.read_bytes() == binary
    assert old.read_bytes() == b"previous-candidate"
    assert updater._get_cache_path().read_bytes() != before
    monkeypatch.setattr("core.config.load_config", lambda **kw: {"update": cfg})
    assert updater._verify_pending_checksum(pending, build)
    assert updater._update_lock_path() == updater._get_cache_path().parent / "locks" / "update.lock"
    assert updater._download_staging.get() is None
    assert updater._query_readonly.get() is False


@pytest.mark.parametrize("failure", ["missing-checksum", "digest", "deadline", "publication"])
def test_failed_download_preserves_previous_pointer_and_bytes(verified_transport, monkeypatch, failure):
    cfg, _, _, responses = verified_transport
    old, before = old_candidate()
    if failure == "missing-checksum":
        responses["/asset.sha256"] = b"invalid"
        monkeypatch.setattr(updater, "query_update_target", lambda cfg: (updater.APP_VERSION + 1, "https://example.org/asset", ""))
    elif failure == "digest":
        responses["/asset"] = b"tampered"
        monkeypatch.setattr("core.constants.MAX_RETRY_DOWNLOAD", 1)
    elif failure == "publication":
        write = updater._write_update_record
        root = updater._get_cache_path()
        def refuse_root(path, record):
            if path == root:
                raise OSError("private-disk-error-token")
            write(path, record)
        monkeypatch.setattr(updater, "_write_update_record", refuse_root)
    result = call_worker(cfg, time.monotonic() - 1 if failure == "deadline" else None)
    assert result["ok"] is False
    assert "private-disk-error-token" not in str(result)
    assert updater._get_pending_path() == old
    assert updater._get_cache_path().read_bytes() == before
    assert old.read_bytes() == b"previous-candidate"


def test_new_candidate_never_overwrites_a_newer_pending_version(verified_transport):
    cfg, build, _, _ = verified_transport
    old, _ = old_candidate()
    updater._write_update_record(updater._get_cache_path(), {"pending_version": build + 1})
    before = updater._get_cache_path().read_bytes()
    assert not call_worker(cfg)["ok"]
    assert old.read_bytes() == b"previous-candidate"
    assert updater._get_cache_path().read_bytes() == before


def test_repeated_verified_downloads_reuse_existing_candidate(verified_transport, monkeypatch):
    cfg, _, binary, _ = verified_transport
    first = call_worker(cfg)
    assert first["ok"], first
    monkeypatch.setattr(updater, "_download_update", lambda *args: pytest.fail("unexpected redownload"))
    for _ in range(8):
        result = call_worker(cfg)
        assert result == first
        assert updater._get_pending_path().read_bytes() == binary
    root = updater._get_cache_path().parent / "update-candidates"
    assert len(list(root.iterdir())) == 1


def test_v1_jumps_to_v11_with_github_digest_and_mirror_bytes(tmp_path, monkeypatch):
    from core import mirror, version

    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "APP_VERSION", 1)
    monkeypatch.setattr(version, "APP_VERSION", 1)
    monkeypatch.setattr(updater, "BOOTSTRAP_URLS", ["https://manifest.test/first", "https://manifest.test/second"])
    monkeypatch.setattr(mirror, "init", lambda: pytest.fail("update worker ran unrelated mirror probes"))
    monkeypatch.setattr(mirror, "_initialized", False)
    monkeypatch.setattr(mirror, "_load_sources", lambda: {"github_releases": {
        "global": [{"url": "https://github.com"}], "cn": [{"url": "https://mirror.test"}]}})
    monkeypatch.setattr(updater, "DOWNLOAD_RETRY_BASE_DELAY", 0)
    filename = updater._asset_filename()
    asset = f"https://github.com/ougato/opskit-cli/releases/download/v11/{filename}"
    magic = b"MZ" if sys.platform == "win32" else b"\xcf\xfa\xed\xfe" if sys.platform == "darwin" else b"\x7fELF"
    binary = magic + b"N" * (101 * 1024)
    digest = hashlib.sha256(binary).hexdigest()
    seen = []

    def metadata(data):
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(data).encode()))

    def respond(request):
        url = str(request.url)
        seen.append(url)
        if url.endswith("/first"):
            return metadata({"message": "invalid manifest"})
        if url.endswith("/second"):
            return metadata({"latest_build": 11, "rollout": 99})
        if url.endswith("/releases/tags/v11"):
            return metadata({"tag_name": "v11", "draft": False, "assets": [{
                "name": filename, "browser_download_url": asset, "digest": "sha256:" + digest}]})
        if url in (asset, asset + ".sha256"):
            return httpx.Response(503)
        if url == "https://mirror.test/" + asset:
            return httpx.Response(200, content=binary)
        pytest.fail(f"unexpected update request {url}")

    transport = httpx.MockTransport(respond)
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=transport, **kw))

    @contextmanager
    def stream(method, url, **kw):
        with client(transport=transport) as instance, instance.stream(method, url, **kw) as response:
            yield response

    monkeypatch.setattr(httpx, "stream", stream)
    machine_bucket = updater._machine_bucket

    def bucket():
        machine_bucket()  # Exercise actual first-run identity creation.
        return 0

    monkeypatch.setattr(updater, "_machine_bucket", bucket)
    result = call_worker({})
    assert result["ok"], result
    assert result["data"]["candidate_version"] == 11
    assert updater._get_pending_path().read_bytes() == binary
    assert (tmp_path / "cache" / updater.FILE_MACHINE_ID).is_file()
    assert seen[-1] == "https://mirror.test/" + asset


def test_download_without_inline_digest_uses_checksum_file(verified_transport, monkeypatch):
    cfg, build, _, _ = verified_transport
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: (build, "https://example.org/asset", ""))
    result = call_worker(cfg)
    assert result["ok"], result
    assert result["data"]["checksum_verified"] is True
    assert updater._verify_pending_checksum(updater._get_pending_path(), build)


def test_changed_bytes_after_download_are_refused_before_publication(verified_transport, monkeypatch):
    cfg, _, _, _ = verified_transport
    old, before = old_candidate()
    download = updater._download_update
    def changed(*args):
        pending = download(*args)
        pending.write_bytes(pending.read_bytes() + b"changed")
        return pending
    monkeypatch.setattr(updater, "_download_update", changed)
    assert not call_worker(cfg)["ok"]
    assert updater._get_cache_path().read_bytes() == before
    assert old.read_bytes() == b"previous-candidate"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows application scheduling")
def test_published_candidate_schedules_in_fixed_cache_and_keeps_independent_snapshot(verified_transport, tmp_path, monkeypatch):
    import subprocess
    from core.update_cli import status
    cfg, build, binary, responses = verified_transport
    assert call_worker(cfg)["ok"]
    source_generation = updater._get_pending_path().parent
    target = tmp_path / "current-program.exe"
    target.write_bytes(b"old-program")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    monkeypatch.setattr("core.config.load_config", lambda **kw: {"update": cfg})
    launches = []
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda args, **kw: launches.append(args))
        assert updater.apply_verified_pending()["state"] == "scheduled"
    cache_root = updater._get_cache_path().parent
    job_id = updater._read_update_record(updater._get_cache_path())["apply_job"]
    job = cache_root / "update-jobs" / job_id
    assert Path(launches[0][-1]) == job / "apply.ps1"
    assert (job / "artifact.exe").read_bytes() == binary
    assert not (source_generation / "update-jobs").exists()
    assert str(updater._update_lock_path()) in (job / "apply.ps1").read_text("utf-8-sig")
    report = status(timeout=10)
    assert report["apply_job"]["id"] == job_id
    assert report["apply_job"]["state"] == "scheduled"
    for revision in range(5):
        replacement = binary + bytes([revision])
        digest = hashlib.sha256(replacement).hexdigest()
        responses["/asset"] = replacement
        monkeypatch.setattr(updater, "query_update_target",
                            lambda cfg: (build, "https://example.org/asset", digest))
        result = call_worker(cfg)
        assert result["ok"], result
    assert not source_generation.exists()
    assert (job / "artifact.exe").read_bytes() == binary
    assert target.read_bytes() == b"old-program"


def test_worker_refuses_root_lock_competition_before_downloading(verified_transport, monkeypatch):
    cfg, _, _, _ = verified_transport
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: pytest.fail("busy lock must precede query"))
    with updater._update_resource.hold():
        result = call_worker(cfg)
    assert result["ok"] is False
    assert "lock" in result["error"]


def verified_process_pipeline(connection, root_cache, deadline, cfg):
    from pathlib import Path
    with pytest.MonkeyPatch.context() as patch:
        actual_cfg, _, _, _ = configure_transport(Path(root_cache).parent.parent, patch)
        update_download._worker(connection, root_cache, deadline, actual_cfg)


def test_real_spawned_pipeline_publishes_candidate_and_status_reads_it(tmp_path, monkeypatch):
    from core.update_cli import status
    from network.worker import run
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    old, _ = old_candidate()
    result = run(verified_process_pipeline, (str(updater._get_cache_path()), time.monotonic() + 10, None),
                 timeout=10, label="update download")
    assert result["ok"], result
    pending = updater._get_pending_path()
    assert pending != old and updater._verify_binary(pending)
    report = status(timeout=10)
    assert report["pending_present"] is True
    assert report["candidate_id"] == result["data"]["candidate_id"]
    assert report["pending_verification"] == "not_checked"
    assert old.read_bytes() == b"previous-candidate"
    assert not multiprocessing.active_children()


@pytest.mark.parametrize("location", ["http://example.org/insecure", "https://user:password@example.org/asset"])
def test_release_redirect_is_validated_before_destination_request(monkeypatch, location):
    requested = []
    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": location})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="invalid update artifact URL"):
            with updater._https_update_stream("https://example.org/asset", client=client):
                pytest.fail("unsafe destination must not be reached")
    assert requested == ["https://example.org/asset"]


@pytest.mark.parametrize("content,headers", [(b"a" * 4097, {}), (b"body", {"Content-Encoding": "gzip"})])
def test_checksum_checksum_response_limits(content, headers):
    response = httpx.Response(200, stream=httpx.ByteStream(content), headers=headers,
                              request=httpx.Request("GET", "https://example.org/meta"))
    with pytest.raises(ValueError):
        updater._small_update_body(response)


@pytest.mark.parametrize("tampered", [False, True])
def test_apply_uses_published_generation_and_reverifies_checksum(verified_transport, tmp_path, monkeypatch, tampered):
    cfg, _, binary, _ = verified_transport
    assert call_worker(cfg)["ok"]
    pending = updater._get_pending_path()
    target = tmp_path / "current-program.exe"
    target.write_bytes(b"old-executable")
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr("core.config.load_config", lambda **kw: {"update": cfg})
    calls = []
    def commit(source, destination, version, recovery=None):
        calls.append(source)
        updater._atomic_copy(source, destination)
    monkeypatch.setattr(updater, "_apply_windows", commit)
    monkeypatch.setattr(updater, "_apply_unix", commit)
    if tampered:
        pending.write_bytes(binary + b"tampered")
        with pytest.raises(RuntimeError, match="verification or application failed"):
            updater.apply_verified_pending()
        assert target.read_bytes() == b"old-executable"
        assert calls == []
        assert not (tmp_path / "backups").exists()
    else:
        result = updater.apply_verified_pending()
        assert result["health_confirmed"] is False
        assert target.read_bytes() == binary
        assert calls == [pending]


def slow_pipeline(connection, root_cache, deadline, cfg):
    # Runs in a real spawned process, blocking inside the download stage.
    from core import updater
    from pathlib import Path
    updater.query_update_target = lambda _: (updater.APP_VERSION + 1, "https://example.org/asset", "a" * 64)
    def stalled(*args):
        Path(root_cache).with_suffix(".started").write_text("download started")
        if cfg.get("cancel_event") is not None:
            cfg["cancel_event"].set()
        time.sleep(60)
    updater._download_update = stalled
    update_download._worker(connection, root_cache, deadline, cfg)


def test_real_supervision_kills_stalled_download_and_retains_old_candidate(tmp_path, monkeypatch, expire_worker_after_marker):
    from network.worker import run
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    old, before = old_candidate()
    cache = updater._get_cache_path()
    budget, observed = expire_worker_after_marker(cache.with_suffix(".started"))
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        run(slow_pipeline, (str(cache), started + budget, {}),
            timeout=budget, label="update download")
    assert cache.with_suffix(".started").exists(), "must reach the blocked download before timeout"
    assert observed, "deadline injection must follow the actual blocked stage"
    assert time.monotonic() - started < budget + 4
    assert cache.read_bytes() == before and old.read_bytes() == b"previous-candidate"
    assert not multiprocessing.active_children()


def test_real_cancel_after_download_start_reaps_worker_and_keeps_old_candidate(tmp_path, monkeypatch):
    from network.worker import run
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    old, before = old_candidate()
    cache = updater._get_cache_path()
    cancelled = multiprocessing.get_context("spawn").Event()
    with pytest.raises(InterruptedError, match="cancelled"):
        run(slow_pipeline, (str(cache), time.monotonic() + 10,
            {"cancel_event": cancelled}),
            timeout=10, label="update download", cancel_event=cancelled)
    assert cache.with_suffix(".started").exists()
    assert cache.read_bytes() == before and old.read_bytes() == b"previous-candidate"
    assert not multiprocessing.active_children()


def test_download_cli_forwards_total_budget_and_never_boots(monkeypatch):
    import main
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("explicit download must not boot"))
    seen = []
    monkeypatch.setattr(update_download, "download", lambda **kw: seen.append(kw) or {"state": "downloaded"})
    result = CliRunner().invoke(main.app, ["--output", "json", "update", "download", "--timeout", "2"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"]
    assert seen == [{"timeout": 2}]


def test_source_mode_download_rejects_without_creating_data(tmp_path, monkeypatch):
    import main
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(updater, "_is_frozen", lambda: False)
    result = CliRunner().invoke(main.app, ["--output", "json", "update", "download"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["ok"] is False
    assert not (tmp_path / "absent").exists()
