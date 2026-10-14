"""Security regressions exercise actual deadlines, archive paths and routing."""
import io
import json
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from core.archive import extract_tar
from core.runner import run, run_lines


@pytest.mark.parametrize("capture", [False, True])
def test_silent_process_deadline(capture):
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2, capture=capture)
    assert time.monotonic() - start < 5


def test_stream_deadline_without_newline():
    with pytest.raises(subprocess.TimeoutExpired):
        list(run_lines([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2))


def test_output_buffer_is_bounded():
    result = run([sys.executable, "-c", "print('x'*100000)"], capture=True, output_limit=1000)
    assert len(result.stdout) <= 1000


@pytest.mark.parametrize("name,kind", [("../outside", tarfile.REGTYPE), ("/outside", tarfile.REGTYPE),
                                     ("C:/outside", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)])
def test_archive_escape_rejected(tmp_path, name, kind):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as out:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "../../outside"
        out.addfile(member)
    data.seek(0)
    with tarfile.open(fileobj=data) as archive, pytest.raises(ValueError):
        extract_tar(archive, tmp_path / "dest")
    assert not (tmp_path / "outside").exists()


def test_platform_never_imports_plugin_on_discovery(tmp_path, monkeypatch):
    from core.plugin import load_manifest, load_plugin
    from core.plugin_trust import grant, compute_fingerprint
    plugin = tmp_path / "plugins" / "evil"
    (plugin / "evil_pkg").mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: evil\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: evil_pkg\n")
    marker = tmp_path / "polluted"
    (plugin / "evil_pkg" / "__init__.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    grant("evil", compute_fingerprint(plugin), "1.0.0")
    assert load_plugin(load_manifest(plugin)) is not None
    assert not marker.exists()
    assert "evil_pkg" not in sys.modules


def test_plugin_cli_parameters_are_opaque(monkeypatch):
    import main
    received = []
    monkeypatch.setattr("plugin.cli.run_plugin", lambda name, argv: received.append((name, argv)) or 7)
    result = CliRunner().invoke(main.app, ["plugin", "run", "demo", "--", "upload", "--Bucket", "a b", "--help"])
    assert result.exit_code == 7, result.output
    assert received == [("demo", ["upload", "--Bucket", "a b", "--help"])]


def test_service_proxy_rechecks_revocation(tmp_path, monkeypatch):
    from core.plugin import load_manifest
    from core.plugin_trust import grant, revoke, compute_fingerprint
    from core.plugin_services import get_service, invalidate_service_cache
    plugin = tmp_path / "plugins" / "provider"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: provider\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: provider_pkg\nprovides: [storage]\n")
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(plugin.parent))
    grant("provider", compute_fingerprint(plugin), "1.0.0")
    invalidate_service_cache()
    assert get_service("storage") is not None
    revoke("provider")
    assert get_service("storage") is None


def test_redaction_removes_secrets():
    from core.secrets import redact, before_send
    assert redact({"password": "private", "url": "https://u:p@example.com?token=abcdef"}) == {
        "password": "[redacted]", "url": "https://[redacted]@example.com?token=[redacted]"}
    event = before_send({"request": {"headers": {"Authorization": "private"}},
                         "exception": {"values": [{"value": "private", "stacktrace": {"frames": [{"vars": {"secret": "private"}}]}}]}}, {})
    assert "private" not in json.dumps(event)


def test_nested_plugin_cycles_are_denied(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import plugin_sandbox as sandbox
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    manifest = SimpleNamespace(name="cycle")
    def recurse(*args, **kwargs):
        return sandbox.invoke(manifest, "entry")
    monkeypatch.setattr(sandbox, "_invoke", recurse)
    with pytest.raises(sandbox.SandboxError, match="cyclic"):
        sandbox.invoke(manifest, "entry")
    assert sandbox._invocations.stack == []


def test_state_lock_rejects_another_process(tmp_path, monkeypatch):
    from core import plugin_sandbox as sandbox
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    script = "from core.plugin_sandbox import _state_lock;\nwith _state_lock('busy'): pass"
    with sandbox._state_lock("busy"):
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode != 0
    assert "plugin is busy" in result.stderr


def test_large_download_rejects_partial_stream(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from core import mirror
    monkeypatch.setattr(mirror, "_probe_concurrent", lambda *a, **k: [("https://a", 0)])
    class Response:
        status_code = 200
        headers = {"content-length": "10000"}
        def iter_bytes(self, size):
            yield b"x" * 2000
    @contextmanager
    def stream(*args, **kwargs):
        assert kwargs["timeout"].read is not None
        yield Response()
    monkeypatch.setattr(mirror.httpx, "stream", stream)
    with pytest.raises(IOError):
        mirror.download_file(["https://a"], tmp_path / "partial")
    assert not (tmp_path / "partial").exists()


def test_timeout_includes_grandchild_startup(tmp_path, monkeypatch):
    from core import process
    import psutil
    marker = tmp_path / "pid"
    child_code = "import time; time.sleep(30)"
    parent_code = ("import subprocess,sys,time; from pathlib import Path; "
                   f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
                   f"target=Path({str(marker)!r}); temporary=target.with_name('pid.tmp'); "
                   "temporary.write_text(str(p.pid)); temporary.replace(target); time.sleep(30)")
    original = process.start
    parents = []

    def tracked(*args, **kwargs):
        parent = original(*args, **kwargs)
        parents.append(parent)
        return parent

    monkeypatch.setattr(process, "start", tracked)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", parent_code], timeout=1, capture=True)
    assert time.monotonic() - started < 5
    assert parents and parents[0].poll() is not None
    # Startup consumes the production budget too; creation is proved separately.
    if marker.exists():
        pid = int(marker.read_text())
        if psutil.pid_exists(pid):
            assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_ready_grandchild_is_reclaimed_after_one_real_second(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import process
    import psutil
    marker = tmp_path / "ready.json"
    child_code = "import time; time.sleep(30)"
    parent_code = ("import subprocess,sys,time,json,os,psutil; from pathlib import Path; "
                   f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
                   "records=[{'pid':pid,'created':psutil.Process(pid).create_time()} "
                   "for pid in (os.getpid(),p.pid)]; "
                   f"Path({str(marker)!r}).write_text(json.dumps(records)); time.sleep(30)")
    origin = time.monotonic()
    ready = []

    def stage_clock():
        now = time.monotonic()
        if not ready:
            if marker.exists():
                # Parent writes the complete JSON before it enters its wait.
                try:
                    records = json.loads(marker.read_text())
                except (ValueError, OSError):
                    records = None
                if records and len(records) == 2:
                    ready.append((now, records))
            if not ready and now - origin >= 30:
                pytest.fail("owned grandchild did not become ready within fixture startup allowance")
        return origin + (now - ready[0][0] if ready else 0)

    # Only this stage test excludes startup; product clock and whole-budget
    # assertions above are unchanged. After readiness use actual wall time.
    monkeypatch.setattr(process, "time", SimpleNamespace(monotonic=stage_clock))
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", parent_code], timeout=1, capture=True)
    assert ready and 1 <= time.monotonic() - ready[0][0] < 5
    for record in ready[0][1]:
        try:
            observed = psutil.Process(record["pid"])
            if observed.create_time() == record["created"]:
                assert observed.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass


def test_download_cache_rejects_tampering(tmp_path):
    from core import mirror
    target = tmp_path / "asset.bin"
    target.write_bytes(b"valid cached download")
    assert not mirror._is_cached_valid(target)
    mirror._record_download_cache(target)
    assert mirror._is_cached_valid(target)
    target.write_bytes(b"different cached data")
    assert not mirror._is_cached_valid(target)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object failure branch")
def test_job_creation_failure_kills_suspended_process(monkeypatch):
    import ctypes
    from unittest.mock import MagicMock
    from core.windows_job import attach_and_resume
    kernel = MagicMock()
    kernel.CreateJobObjectW.return_value = 0
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    process = MagicMock()
    with pytest.raises(OSError):
        attach_and_resume(process)
    process.kill.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
