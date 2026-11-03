"""Real Windows Git object acquisition inside AppContainer, no Docker."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from core.native_fetch import local_candidate
from core.native_runtime import inventory
from core.plugin_fetch_candidate import validated_archive

pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows AppContainer required")


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    from tests.native_tool_fixtures import copy_native_tools
    return copy_native_tools(tmp_path_factory)


def repository(tmp_path, git, object_format="sha1"):
    root = tmp_path / "repository"
    root.mkdir()
    def run(*args):
        return subprocess.check_output([git, "-c", "user.name=Native Test", "-c", "user.email=native@test.invalid",
                                        "-c", "commit.gpgSign=false", "-C", str(root), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()
    run("init", "--initial-branch=main", "--object-format=" + object_format)
    (root / "plugin.yaml").write_bytes(b"name: native_example\n")
    (root / "binary.dat").write_bytes(b"\x00\xff\n")
    run("add", ".")
    run("commit", "-m", "synthetic test input")
    commit = run("rev-parse", "HEAD")
    (root / "plugin.yaml").write_text("uncommitted content must not be imported")
    (root / "untracked.txt").write_text("not in candidate")
    return root, commit


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_real_smart_protocol_pack_is_indexed_only_in_appcontainer(tools, tmp_path, object_format):
    import io
    from core.native_git_protocol import advertisement, packet, receive_pack
    python, git_runtime, host_git = tools
    source, commit = repository(tmp_path, host_git, object_format)
    # Host Git here is only the synthetic server fixture; production transport
    # never invokes it. Add history to exercise shallow-boundary validation.
    subprocess.run([host_git, "-C", str(source), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@test.invalid", "-c", "commit.gpgSign=false",
                    "commit", "--allow-empty", "-m", "second"], check=True, capture_output=True)
    commit = subprocess.check_output([host_git, "-C", str(source), "rev-parse", "HEAD"]).decode().strip()
    refs = subprocess.check_output([host_git, "upload-pack", "--stateless-rpc", "--advertise-refs", str(source)])
    selection, request = advertisement(packet(b"# service=git-upload-pack\n") + b"0000" + refs)
    assert selection["commit"] == commit
    response = subprocess.check_output([host_git, "upload-pack", "--stateless-rpc", str(source)], input=request)
    packed = tmp_path / "downloaded"
    packed.mkdir()
    with (packed / "objects.pack").open("wb") as output:
        metadata = receive_pack(io.BytesIO(response), output, selection)
    assert metadata["shallow"] == [commit]
    with local_candidate(python, git_runtime, packed, staging_parent=tmp_path,
                         deadline=time.monotonic() + 90, checkpoint=lambda: None,
                         pack={"commit": commit, "shallow": metadata["shallow"]}) as (result, _):
        assert result.reported_commit == commit
        assert {item.path for item in result.files} == {"plugin.yaml", "binary.dat"}


def test_real_https_broker_and_isolated_pack_export(tools, tmp_path, monkeypatch):
    import datetime
    import ipaddress
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import ssl
    import threading
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from core.native_git_https import remote_pack
    from core.native_git_protocol import packet
    python, git_runtime, host_git = tools
    source, commit = repository(tmp_path, host_git)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic local Git")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                   .not_valid_after(now + datetime.timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
    cert = certificate.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "cert.pem").write_bytes(cert)
    (tmp_path / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            assert self.path == "/repository/info/refs?service=git-upload-pack"
            seen.append(("GET", self.headers.get("Authorization")))
            body = (packet(b"# service=git-upload-pack\n") + b"0000"
                    + subprocess.check_output([host_git, "upload-pack", "--stateless-rpc", "--advertise-refs", str(source)]))
            self.reply(body, "advertisement")
        def do_POST(self):
            assert self.path == "/repository/git-upload-pack"
            seen.append(("POST", self.headers.get("Authorization")))
            body = self.rfile.read(int(self.headers["Content-Length"]))
            body = subprocess.check_output([host_git, "upload-pack", "--stateless-rpc", str(source)], input=body)
            self.reply(body, "result")
        def reply(self, body, suffix):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-git-upload-pack-" + suffix)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f"https://127.0.0.1:{server.server_port}/repository"
    profile = {"schema_version": 1, "source": url,
               "authentication": {"kind": "bearer", "token": "synthetic-token", "ca_pem": cert.decode()}}
    try:
        from core.native_sandbox import NativeSandboxError
        with pytest.raises(NativeSandboxError) as denied_private:
            with remote_pack(url, profile=profile, staging_parent=tmp_path, deadline=time.monotonic() + 30,
                             checkpoint=lambda: None):
                pytest.fail("private address accepted without authorization")
        assert denied_private.value.code == "fetch_private_denied"
        with remote_pack(url, profile=profile, allow_private=True, staging_parent=tmp_path,
                         deadline=time.monotonic() + 60, checkpoint=lambda: None) as (packed, metadata):
            assert metadata["commit"] == commit
            with local_candidate(python, git_runtime, packed, staging_parent=tmp_path,
                                 deadline=time.monotonic() + 90, checkpoint=lambda: None,
                                 pack=metadata) as (result, _):
                assert result.reported_commit == commit
                assert {item.path for item in result.files} == {"plugin.yaml", "binary.dat"}
        assert seen == [("GET", "Bearer synthetic-token"), ("POST", "Bearer synthetic-token")]
        assert not list(tmp_path.glob("git-pack-*"))
        # Exercise the public CLI and private-profile reader, not just helpers.
        import json
        import main
        from typer.testing import CliRunner
        from core import cli
        from core.cli_secrets import export_secret
        from core.native_fetch_catalog import FetchCatalog
        from core.plugin import load_manifest
        from core.plugin_trust import trusted_record
        monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
        monkeypatch.setattr(main, "_boot", lambda: pytest.fail("acquisition must not boot services"))
        FetchCatalog().prepare(python, git_runtime)
        def version(value):
            (source / "plugin.yaml").write_text(
                f"name: native_example\nversion: {value}\napi_version: 2\nkind: python\nentry: native_example\n")
            (source / "native_example.py").write_text("raise RuntimeError('INSTALL_MUST_NOT_IMPORT')\n")
            for arguments in (["add", "plugin.yaml", "native_example.py"], ["commit", "-m", value]):
                subprocess.run([host_git, "-c", "user.name=Test", "-c", "user.email=native@test.invalid",
                                "-c", "commit.gpgSign=false", "-C", str(source), *arguments],
                               check=True, capture_output=True)
        version("1.0.0")
        profile_file = tmp_path / "private-profile.json"
        export_secret(str(profile_file), profile)
        runner = CliRunner()
        def call(*arguments):
            return runner.invoke(main.app, ["--output", "json", "plugin", *arguments])
        options = ["--fetch-profile", str(profile_file), "--allow-private"]
        installed = call("install", url, "--as", "https-example", *options)
        assert installed.exit_code == 0, installed.output
        root = tmp_path / "plugins/https-example"
        assert load_manifest(root).version == "1.0.0"
        record = trusted_record("native_example")
        assert record["source"] == url and not record["fingerprint"]
        assert "synthetic-token" not in json.dumps(record)
        request_count = len(seen)
        denied = call("update", "native_example")
        assert denied.exit_code == 1 and len(seen) == request_count
        assert load_manifest(root).version == "1.0.0"
        version("1.1.0")
        updated = call("update", "native_example", *options)
        assert updated.exit_code == 0, updated.output
        assert load_manifest(root).version == "1.1.0"
        assert not (root / ".git").exists()
        cli.configure("text", False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("branch", [None, "main", "hostile-config"])
def test_real_isolated_local_candidate(tools, tmp_path, branch):
    python, git_runtime, host_git = tools
    source, commit = repository(tmp_path, host_git)
    sentinel = tmp_path / "must-not-run"
    if branch == "hostile-config":
        # No checkout, external diff, pager, filter or hooks should run while
        # exporting objects, even when the repository supplies their commands.
        with (source / ".git/config").open("a", encoding="utf-8") as config:
            config.write('\n[core]\n pager = "cmd /c echo escaped"\n'
                         '[diff]\n external = "cmd /c echo escaped"\n'
                         '[filter "malicious"]\n smudge = "cmd /c echo escaped"\n')
        hook = source / ".git/hooks/post-checkout"
        hook.write_text('#!/bin/sh\necho escaped > "' + sentinel.as_posix() + '"\n')
        branch = None
    original = inventory(source)
    with local_candidate(python, git_runtime, source, staging_parent=tmp_path,
                         deadline=time.monotonic() + 60, checkpoint=lambda: None, branch=branch) as (result, response):
        assert result.reported_commit == commit
        assert {item.path for item in result.files} == {"plugin.yaml", "binary.dat"}
        response.seek(0)
        with validated_archive(response) as (validated, archive):
            assert validated == result
            import tarfile
            with tarfile.open(fileobj=archive) as tar:
                assert tar.extractfile("binary.dat").read() == b"\x00\xff\n"
                assert tar.extractfile("plugin.yaml").read() == b"name: native_example\n"
    assert inventory(source) == original
    assert not sentinel.exists()
    assert not list(tmp_path.glob("native-fetch-*"))


def test_source_hardlink_rejected_before_launch(tools, tmp_path):
    python, git_runtime, host_git = tools
    source, _ = repository(tmp_path, host_git)
    secret = tmp_path / "secret"
    secret.write_text("host-secret")
    os.link(secret, source / "linked-secret")
    with pytest.raises(ValueError, match="unsafe native runtime"):
        with local_candidate(python, git_runtime, source, staging_parent=tmp_path,
                             deadline=time.monotonic() + 60, checkpoint=lambda: None):
            pytest.fail("unsafe source was accepted")
    assert secret.read_text() == "host-secret"


@pytest.mark.parametrize("branch", ["", "-main", "main\ncommand", 12, "x" * 257])
def test_invalid_branch_rejected_before_tool_or_source_access(tmp_path, monkeypatch, branch):
    from core import native_fetch
    monkeypatch.setattr(native_fetch, "get_backend", lambda: pytest.fail("invalid request reached launcher"))
    with pytest.raises(ValueError, match="invalid native fetch branch"):
        with local_candidate(tmp_path / "missing-python", tmp_path / "missing-git", tmp_path / "missing-source",
                             staging_parent=tmp_path, deadline=time.monotonic() + 5,
                             checkpoint=lambda: None, branch=branch):
            pytest.fail("invalid branch accepted")


def test_real_preparation_and_catalog_acquisition(tools, tmp_path, monkeypatch):
    import json
    import main
    from typer.testing import CliRunner
    from core import cli
    from core.native_fetch_catalog import FetchCatalog
    from core.plugin import load_manifest
    from core.plugin_trust import compute_fingerprint, trusted_record, is_trusted
    from plugin import commands
    python, git_runtime, host_git = tools
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("native preparation must not boot services"))
    from core import plugin_sandbox
    assert not hasattr(plugin_sandbox, "docker_command")
    runner = CliRunner()
    def call(*args):
        return runner.invoke(main.app, ["--output", "json", *args])
    prepared = call("plugin", "prepare-fetch", "--python-runtime", str(python), "--git-runtime", str(git_runtime))
    assert prepared.exit_code == 0, prepared.output
    identity = json.loads(prepared.stdout)["data"]["toolchain"]
    catalog = FetchCatalog()
    runtime, record = catalog.load()
    assert runtime.parent.name == identity
    assert record["platform"] == "win32"
    source, commit = repository(tmp_path, host_git)
    with catalog.local_candidate(source, staging_parent=tmp_path, deadline=time.monotonic() + 90,
                                 checkpoint=lambda: None) as (result, _):
        assert result.reported_commit == commit
        assert {file.path for file in result.files} == {"plugin.yaml", "binary.dat"}
    def commit_version(version):
        (source / "plugin.yaml").write_text(
            f"name: native_example\nversion: {version}\napi_version: 2\nkind: python\nentry: native_example\n")
        (source / "native_example.py").write_text("raise RuntimeError('INSTALL_MUST_NOT_IMPORT')\n")
        for arguments in (["add", "plugin.yaml", "native_example.py"], ["commit", "-m", version]):
            subprocess.run([host_git, "-c", "user.name=Test", "-c", "user.email=native@test.invalid",
                            "-c", "commit.gpgSign=false", "-C", str(source), *arguments],
                           check=True, capture_output=True)
    commit_version("1.0.0")
    installed = call("plugin", "install", str(source), "--branch", "main", "--as", "native-example")
    assert installed.exit_code == 0, installed.output
    root = tmp_path / "plugins/native-example"
    assert root.is_dir() and not (root / ".git").exists() and not (root / "untracked.txt").exists()
    digest = compute_fingerprint(root)
    assert not is_trusted("native_example", digest)
    assert call("plugin", "trust", "native_example", "--fingerprint", "0" * 64).exit_code == 2
    assert call("plugin", "trust", "native_example", "--fingerprint", digest).exit_code == 0
    assert is_trusted("native_example", digest)
    assert call("plugin", "disable", "native_example").exit_code == 0
    assert call("plugin", "enable", "native_example").exit_code == 0
    unchanged = call("plugin", "update", "native_example")
    assert unchanged.exit_code == 0, unchanged.output
    assert json.loads(unchanged.stdout)["data"]["status"] == "unchanged"
    commit_version("1.1.0")
    updated = call("plugin", "update", "native_example")
    assert updated.exit_code == 0, updated.output
    assert load_manifest(root).version == "1.1.0"
    assert trusted_record("native_example")["fingerprint"] == ""
    (root / "user-notes.txt").write_text("keep my local work")
    assert not commands.update(load_manifest(root))[0]
    assert (root / "user-notes.txt").read_text() == "keep my local work"
    assert call("plugin", "revoke", "native_example").exit_code == 0
    assert call("--yes", "plugin", "remove", "native_example").exit_code == 0
    assert not root.exists()
    assert not list(root.parent.glob(".opskit-update-*"))
    cli.configure("text", False)
