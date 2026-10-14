"""Signed generation publication preserves old candidates on failed downloads."""
import base64
from contextlib import contextmanager
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import httpx
import pytest
from typer.testing import CliRunner

from core import updater, update_download
from core.release_signature import payload


class Capture:
    def send_bytes(self, value):
        self.result = json.loads(value)

    def close(self):
        pass


def configure_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    build = updater.APP_VERSION + 1
    magic = b"MZ" if sys.platform == "win32" else b"\xcf\xfa\xed\xfe" if sys.platform == "darwin" else b"\x7fELF"
    binary = magic + b"N" * (101 * 1024)
    digest = hashlib.sha256(binary).hexdigest()
    envelope = {"build": build, "filename": updater._asset_filename(), "sha256": digest,
        "signature": base64.b64encode(key.sign(payload(build, updater._asset_filename(), digest))).decode()}
    responses = {"/asset": binary, "/asset.sig.json": json.dumps(envelope).encode()}
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
    monkeypatch.setattr("core.mirror.get_sources", lambda name: ["https://github.com"])
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: (build, "https://example.org/asset", digest))
    return {"signing_public_key": public}, build, binary, responses


@pytest.fixture
def signed_transport(tmp_path, monkeypatch):
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


def test_real_signature_pipeline_publishes_one_complete_generation(signed_transport, monkeypatch):
    cfg, build, binary, _ = signed_transport
    old, before = old_candidate()
    result = call_worker(cfg)
    assert result["ok"], result
    assert result["data"]["applied"] is False
    pending = updater._get_pending_path()
    assert pending != old and pending.read_bytes() == binary
    assert old.read_bytes() == b"previous-candidate"
    assert updater._get_cache_path().read_bytes() != before
    monkeypatch.setattr("core.config.load_config", lambda **kw: {"update": cfg})
    assert updater._verify_pending_signature(pending, build)
    assert updater._update_lock_path() == updater._get_cache_path().parent / "locks" / "update.lock"
    assert updater._download_staging.get() is None
    assert updater._query_readonly.get() is False


@pytest.mark.parametrize("failure", ["signature", "digest", "deadline", "publication"])
def test_failed_download_preserves_previous_pointer_and_bytes(signed_transport, monkeypatch, failure):
    cfg, _, _, responses = signed_transport
    old, before = old_candidate()
    if failure == "signature":
        responses["/asset.sig.json"] = b'{"signature":"invalid"}'
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


def test_missing_key_does_not_query_or_create_a_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: pytest.fail("must reject missing key first"))
    monkeypatch.setattr("core.constants.UPDATE_SIGNING_PUBLIC_KEY", "")
    assert not call_worker({})["ok"]
    assert not (tmp_path / "cache" / "update-candidates").exists()


def test_new_candidate_never_overwrites_a_newer_pending_version(signed_transport):
    cfg, build, _, _ = signed_transport
    old, _ = old_candidate()
    updater._write_update_record(updater._get_cache_path(), {"pending_version": build + 1})
    before = updater._get_cache_path().read_bytes()
    assert not call_worker(cfg)["ok"]
    assert old.read_bytes() == b"previous-candidate"
    assert updater._get_cache_path().read_bytes() == before


def test_repeated_verified_downloads_keep_candidate_count_bounded(signed_transport):
    from core import update_cache
    cfg, _, binary, _ = signed_transport
    for _ in range(8):
        result = call_worker(cfg)
        assert result["ok"], result
        assert updater._get_pending_path().read_bytes() == binary
    root = updater._get_cache_path().parent / "update-candidates"
    assert len(list(root.iterdir())) == update_cache.MAX_CANDIDATES


@pytest.mark.skipif(sys.platform != "win32", reason="Windows application scheduling")
def test_published_candidate_schedules_in_fixed_cache_and_keeps_independent_snapshot(signed_transport, tmp_path, monkeypatch):
    import subprocess
    from core.update_cli import status
    cfg, _, binary, _ = signed_transport
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
    for _ in range(5):
        assert call_worker(cfg)["ok"]
    assert not source_generation.exists()
    assert (job / "artifact.exe").read_bytes() == binary
    assert target.read_bytes() == b"old-program"


def test_worker_refuses_root_lock_competition_before_downloading(signed_transport, monkeypatch):
    cfg, _, _, _ = signed_transport
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: pytest.fail("busy lock must precede query"))
    with updater._update_resource.hold():
        result = call_worker(cfg)
    assert result["ok"] is False
    assert "lock" in result["error"]


def test_signature_stage_consumes_the_same_total_deadline(signed_transport, monkeypatch):
    from types import SimpleNamespace
    cfg, _, _, _ = signed_transport
    old, before = old_candidate()
    clock = [0.0]
    monkeypatch.setattr(update_download, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    fetch = updater._fetch_release_signature
    reached = []
    def signature(*args):
        result = fetch(*args)
        reached.append(result)
        clock[0] = 11.0
        return result
    monkeypatch.setattr(updater, "_fetch_release_signature", signature)
    assert call_worker(cfg, deadline=10)["ok"] is False
    assert reached == [True]
    assert old.read_bytes() == b"previous-candidate"
    assert updater._get_cache_path().read_bytes() == before


def signed_process_pipeline(connection, root_cache, deadline, cfg):
    from pathlib import Path
    with pytest.MonkeyPatch.context() as patch:
        actual_cfg, _, _, _ = configure_transport(Path(root_cache).parent.parent, patch)
        update_download._worker(connection, root_cache, deadline, actual_cfg)


def test_real_spawned_pipeline_publishes_candidate_and_status_reads_it(tmp_path, monkeypatch):
    from core.update_cli import status
    from network.worker import run
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    old, _ = old_candidate()
    result = run(signed_process_pipeline, (str(updater._get_cache_path()), time.monotonic() + 10, None),
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
def test_checksum_signature_response_limits(content, headers):
    response = httpx.Response(200, stream=httpx.ByteStream(content), headers=headers,
                              request=httpx.Request("GET", "https://example.org/meta"))
    with pytest.raises(ValueError):
        updater._small_update_body(response)


@pytest.mark.parametrize("tampered", [False, True])
def test_apply_uses_published_generation_and_reverifies_signature(signed_transport, tmp_path, monkeypatch, tampered):
    cfg, _, binary, _ = signed_transport
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
        run(slow_pipeline, (str(cache), started + budget, {"signing_public_key": "test-key"}),
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
            {"signing_public_key": "test-key", "cancel_event": cancelled}),
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
