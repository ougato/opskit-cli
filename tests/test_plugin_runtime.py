"""Platform-owned build context and diagnostic stream, without running Docker."""
import io
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from core import plugin_runtime
from core.plugin_runtime_inventory import RUNTIME_FILES

BASE_IMAGE = "python:3.12-slim@sha256:" + "d" * 64


@pytest.mark.parametrize("reference", ["--help", "python\n--privileged", "https://user:SECRET@registry/image", "image@sha256:bad",
                                      "a" * 4096 + "@sha256:" + "b" * 64])
def test_image_option_or_credential_injection_rejected_before_docker(reference, monkeypatch, tmp_path):
    def forbidden():
        pytest.fail("invalid reference must not invoke Docker")
    monkeypatch.setattr(plugin_runtime, "docker_command", forbidden)
    with pytest.raises(ValueError, match="invalid base image reference"):
        plugin_runtime.build(tmp_path, [], reference)


@pytest.mark.parametrize("final_image", ["sha256:" + "b" * 64, "invalid-image", "plugin-changed"])
def test_platform_context_only_and_build_output_on_stderr(tmp_path, monkeypatch, capfd, final_image):
    root = tmp_path / "platform"
    core = root / "core"
    core.mkdir(parents=True)
    for name in RUNTIME_FILES:
        source = core / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# fixture", encoding="utf-8")
    (core / "updater.py").write_text("DO_NOT_SHIP", encoding="utf-8")
    (root / "requirements.txt").write_text("PyYAML==6.0.3\npytest==9.0.3\n", encoding="utf-8")
    (root / "private.env").write_text("SECRET", encoding="utf-8")
    lock_file = tmp_path / "requirements.lock"
    lock_file.write_text("".join(name + "==1.0 --hash=sha256:" + "e" * 64 + "\n"
                                for name in ("rich", "pyyaml", "typer", "platformdirs", "demo")), encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setattr(plugin_runtime, "CORE", core)
    monkeypatch.setattr(plugin_runtime, "load_manifest", lambda path: SimpleNamespace(name="demo", permissions=[]))
    fingerprints = iter(["c" * 64, ("f" if final_image == "plugin-changed" else "c") * 64])
    monkeypatch.setattr(plugin_runtime, "compute_fingerprint", lambda path: next(fingerprints))
    monkeypatch.setattr(plugin_runtime, "docker_command", lambda: ["docker"])
    monkeypatch.setattr(plugin_runtime, "data_dir", lambda: state)
    native_capture = plugin_runtime.capture
    contexts = []
    def capture(command, **kwargs):
        if command[1] == "image":
            image = ("sha256:" + "b" * 64 if final_image == "plugin-changed" else final_image) if command[-1].startswith("opskit-runtime:") else "sha256:" + "a" * 64
            return subprocess.CompletedProcess(command, 0, image, "")
        assert command[1] == "build"
        contexts.append(kwargs["stdin"].read())
        kwargs["stdin"].seek(0)
        return native_capture([sys.executable, "-c", "import sys; sys.stdin.buffer.read(); print('password=SECRET')"], **kwargs)
    monkeypatch.setattr(plugin_runtime, "capture", capture)
    if final_image == "invalid-image":
        with pytest.raises(ValueError, match="invalid prepared image identity"):
            plugin_runtime.build(tmp_path, ["demo==1.0"], BASE_IMAGE, lock_file=lock_file)
        assert not state.exists()
    elif final_image == "plugin-changed":
        with pytest.raises(RuntimeError, match="plugin changed"):
            plugin_runtime.build(tmp_path, ["demo==1.0"], BASE_IMAGE, lock_file=lock_file)
        assert not state.exists()
    else:
        assert plugin_runtime.build(tmp_path, ["demo==1.0"] * 10000, BASE_IMAGE, lock_file=lock_file) == final_image
        catalog = state / "plugin-runtimes" / "demo.json"
        import json
        record = json.loads(catalog.read_text())
        assert catalog.stat().st_size < 16384 and "dependencies" not in record
    stdout, stderr = capfd.readouterr()
    assert stdout == "" and "Preparing isolated plugin runtime" in stderr
    assert "SECRET" not in stdout + stderr
    with tarfile.open(fileobj=io.BytesIO(contexts[0])) as archive:
        assert set(archive.getnames()) == {"Dockerfile", "pip-bootstrap.txt", "requirements.txt", *("core/" + name for name in RUNTIME_FILES)}
        assert archive.extractfile("pip-bootstrap.txt").read() == plugin_runtime.PIP_BOOTSTRAP.encode()
        assert b"pytest" not in archive.extractfile("requirements.txt").read()
        dockerfile = archive.extractfile("Dockerfile").read()
        assert ("FROM sha256:" + "a" * 64).encode() in dockerfile
        assert b"--require-hashes --only-binary=:all:" in dockerfile and b"--ignore-installed" in dockerfile
        from core.plugin_runtime_inventory import RUNTIME_POLICY
        assert f"org.opskit.sandbox={RUNTIME_POLICY}".encode() in dockerfile and b"org.opskit.code-sha256=" in dockerfile
        assert dockerfile.index(b"-r pip-bootstrap.txt") < dockerfile.index(b"-r requirements.txt")
        assert dockerfile.count(b"--disable-pip-version-check") == 2


def test_bounded_native_client_output_and_nonzero_fail_without_log_leak(capfd):
    command = [sys.executable, "-c", "print('token=SECRET'); raise SystemExit(3)"]
    with pytest.raises(RuntimeError, match="plugin runtime command failed") as failure:
        plugin_runtime._command_output(command, timeout=5)
    assert "SECRET" not in str(failure.value)
    with pytest.raises(RuntimeError, match="output exceeds limit"):
        plugin_runtime._command_output([sys.executable, "-c", "print('x' * 8192)"], timeout=5)
    assert capfd.readouterr() == ("", "")


def test_native_file_input_exact_bytes_and_caller_ownership(tmp_path):
    payload = bytes(range(256)) * 32
    source = tmp_path / "context.tar"
    source.write_bytes(payload)
    with source.open("rb") as input_file:
        command = [sys.executable, "-c", "import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"]
        digest = plugin_runtime._command_output(command, timeout=5, stdin=input_file)
        assert not input_file.closed
    import hashlib
    assert digest == hashlib.sha256(payload).hexdigest() and source.read_bytes() == payload


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "output_limit"])
def test_native_client_failure_reclaims_child_tree(tmp_path, monkeypatch, outcome):
    import json
    import psutil
    record = tmp_path / "child.json"
    program = "\n".join([
        "import sys,subprocess,time,json,psutil",
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])",
        "with open(sys.argv[1],'w') as out: json.dump([child.pid,psutil.Process(child.pid).create_time()],out)",
        "if sys.argv[2]=='cancel': print('ready',flush=True)",
        "if sys.argv[2]=='output_limit': print('x'*8192,flush=True)",
        "time.sleep(60)",
    ])
    if outcome == "cancel":
        from core import process
        native_stream = process.stream
        def cancelled(command, **kwargs):
            iterator = native_stream(command, **kwargs)
            try:
                assert next(iterator) == "ready"
                raise KeyboardInterrupt
                yield  # Keep this an iterator; cancellation occurs after startup.
            finally:
                iterator.close()
        monkeypatch.setattr(process, "stream", cancelled)
    expected = {"timeout": subprocess.TimeoutExpired, "cancel": KeyboardInterrupt,
                "output_limit": RuntimeError}[outcome]
    with pytest.raises(expected):
        plugin_runtime._command_output([sys.executable, "-c", program, str(record), outcome],
                                       timeout=2 if outcome == "timeout" else 5, max_bytes=1024)
    assert record.exists(), "child must have started before timeout"
    pid, created = json.loads(record.read_text())
    try:
        child = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    assert child.create_time() != created or child.status() == psutil.STATUS_ZOMBIE
