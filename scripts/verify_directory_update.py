"""Exercise packaged directory updates and recovery through the actual CLI over loopback TLS."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import py_compile
import shutil
import ssl
import subprocess
import sys
from threading import Thread
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.directory_archive import MANIFEST_NAME, read_manifest, unpack_package
from scripts.directory_package import create_package

REPO = "ougato/opskit-cli"
PROBE_FILE = "opskit-acceptance-probe"
INSTALL_ARGUMENTS = ["update", "install", "--check-timeout", "30", "--timeout", "180"]


def certificate(root: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpsKit release fixture")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False).sign(key, hashes.SHA256()))
    cert_path = root / "cert.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path = root / "key.pem"
    descriptor = os.open(key_path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    return cert_path, key_path


@contextmanager
def release_server(root: Path, stem: str, extension: str):
    cert, key = certificate(root)
    requests = []
    fixture = {"version": 1, "bad_checksum": False, "assets": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            version = fixture["version"]
            origin = f"/{REPO}/releases/download/v{version}/"
            if self.path == "/bootstrap.json":
                body = {"latest_build": version, "min_build": 1, "rollout": 100}
                content = json.dumps(body).encode()
            elif self.path in {f"/repos/{REPO}/releases/latest", f"/repos/{REPO}/releases/tags/v{version}"}:
                filenames = {stem + extension: "f" * 64, **fixture["assets"]}
                assets = [{"name": name, "browser_download_url": address + origin + name,
                           "digest": "sha256:" + digest} for name, digest in filenames.items()]
                content = json.dumps({"tag_name": f"v{version}", "body": "", "draft": False,
                                      "assets": assets}).encode()
            elif self.path.startswith(origin) and self.path.endswith(".sha256"):
                name = self.path[len(origin):-len(".sha256")]
                if name not in fixture["assets"]:
                    self.send_error(404)
                    return
                checksum = "0" * 64 if fixture["bad_checksum"] else fixture["assets"][name]
                content = f"{checksum}  {name}\n".encode("ascii")
            elif self.path.startswith(origin) and self.path[len(origin):] in fixture["assets"]:
                content = (root / self.path[len(origin):]).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    address = f"https://127.0.0.1:{server.server_port}"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fixture, address, cert, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def release_packages(root: Path, seed: Path, address: str, full_name: str, delta_name: str):
    old, new = root / "old-tree", root / "new-tree"
    old.mkdir()
    unpack_package(seed, old, read_manifest(seed).version)
    (old / MANIFEST_NAME).unlink()
    (old / PROBE_FILE).write_bytes(b"stable fixture file")
    shutil.copytree(old, new)
    constants = (ROOT / "core" / "constants.py").read_text(encoding="utf-8")
    source = root / "constants.py"
    for version, tree in ((1, old), (2, new)):
        source.write_text(constants + f"\nAPP_VERSION = {version}\n"
                          f"BOOTSTRAP_URLS = [{address + '/bootstrap.json'!r}]\n"
                          f"GITHUB_BASE = {address!r}\n"
                          f"GITHUB_API_RELEASES = {address + '/repos/{repo}/releases/latest'!r}\n",
                          encoding="utf-8")
        compiled = tree / "_internal" / "core" / "constants.pyc"
        if not compiled.is_file():
            raise RuntimeError("directory seed must provide external OpsKit bytecode")
        py_compile.compile(str(source), cfile=str(compiled), dfile="core/constants.py", doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    initial = root / "baseline.tar.xz"
    full, delta = root / full_name, root / delta_name
    create_package(old, initial, 1)
    create_package(new, full, 2)
    create_package(new, delta, 2, base=initial)
    if delta.stat().st_size >= full.stat().st_size:
        raise RuntimeError("incremental fixture is not smaller than full")
    return initial, full, delta


class InstalledCli:
    def __init__(self, parent: Path, name: str, launcher: Path, seed: Path, cert: Path):
        self.root = parent / name
        self.root.mkdir(mode=0o700)
        extension = ".exe" if os.name == "nt" else ""
        self.entry = self.root / ("opskit" + extension)
        self.versions = self.entry.with_name(f".{self.entry.name}.versions")
        self.timings = {}
        proxies = {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
                   "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1"}
        self.env = {**os.environ, **proxies, **{key.lower(): value for key, value in proxies.items()},
                    "SSL_CERT_FILE": str(cert), "OPSKIT_DATA_DIR": str(self.root / "data"),
                    "OPSKIT_PLUGINS_DIR": str(self.root / "plugins"), "PYTHONIOENCODING": "utf-8"}
        result = self.run("bootstrap", [str(launcher), "--opskit-internal-install", str(seed), str(self.entry), "v1"])
        if result.returncode:
            raise RuntimeError(f"fixture bootstrap failed, see {self.root / 'bootstrap.stderr'}")

    def run(self, label, argv):
        started = time.monotonic()
        result = subprocess.run(argv, cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=240)
        self.timings[label] = round(time.monotonic() - started, 3)
        (self.root / f"{label}.stdout").write_bytes(result.stdout)
        (self.root / f"{label}.stderr").write_bytes(result.stderr)
        return result

    def cli(self, label, arguments, *, success=True):
        invocation = [str(self.entry), "--non-interactive", "--yes", "--output", "json", *arguments]
        result = self.run(label, invocation)
        payload = json.loads(result.stdout)
        if (result.returncode == 0) is not success or payload["ok"] is not success:
            raise RuntimeError(f"unexpected CLI result at {label}: {result.returncode} {payload}")
        return payload["data"]

    def state(self):
        return json.loads((self.versions / "state.json").read_bytes())

    def active(self) -> Path:
        return self.versions / self.state()["active"]["name"]


def verify(root: Path, launcher: Path, seed: Path, stem: str):
    extension = ".exe" if os.name == "nt" else ""
    full_name = f"{stem}.tar.xz"
    delta_name = f"{stem}-from-v1.tar.xz"
    with release_server(root, stem, extension) as (fixture, address, cert, requests):
        initial, full, delta = release_packages(root, seed, address, full_name, delta_name)
        fixture["assets"] = {package.name: hashlib.sha256(package.read_bytes()).hexdigest()
                             for package in (full, delta)}
        installed = InstalledCli(root, "incremental", launcher, initial, cert)
        assert installed.cli("before", ["--version"]) == {"version": 1}
        assert installed.cli("no-update", INSTALL_ARGUMENTS)["state"] == "no_update"

        fixture.update(version=2, bad_checksum=True)
        before = installed.state()
        installed.cli("bad-checksum", INSTALL_ARGUMENTS, success=False)
        assert installed.state() == before
        assert installed.cli("after-bad-checksum", ["--version"]) == {"version": 1}

        fixture["bad_checksum"] = False
        applied = installed.cli("install-incremental", INSTALL_ARGUMENTS)
        assert applied["transport"] == "incremental" and applied["download_bytes"] == delta.stat().st_size
        assert installed.cli("updated-version", ["--version"]) == {"version": 2}
        status = installed.cli("updated-status", ["update", "status"])
        assert status["current_version"] == 2 and status["previous_version"] == 1

        executable = installed.active() / installed.entry.name
        executable.rename(executable.with_suffix(".broken"))
        assert installed.cli("rolled-back", ["--version"]) == {"version": 1}
        recovery = installed.cli("rolled-back-status", ["update", "status"])
        assert recovery["current_version"] == 1 and recovery["rejected_version"] == 2
        assert installed.cli("reject-broken-update", INSTALL_ARGUMENTS)["state"] == "no_update"
        assert installed.state()["rejected"] == 2

        fallback = InstalledCli(root, "full-fallback", launcher, initial, cert)
        (fallback.active() / PROBE_FILE).write_bytes(b"altered local fixture")
        count = len(requests)
        applied = fallback.cli("install-full-fallback", INSTALL_ARGUMENTS)
        assert applied["transport"] == "full" and applied["download_bytes"] == full.stat().st_size
        assert fallback.cli("full-fallback-version", ["--version"]) == {"version": 2}
        fallback_requests = requests[count:]
        assert any(path.endswith("/" + delta_name) for path in fallback_requests)
        assert any(path.endswith("/" + full_name) for path in fallback_requests)

        staged = InstalledCli(root, "staged-damage", launcher, initial, cert)
        downloaded = staged.cli("download-before-tamper", ["update", "download", "--timeout", "180"])
        assert downloaded["transport"] == "incremental"
        pending = staged.versions / staged.state()["staged"]["name"]
        (pending / PROBE_FILE).write_bytes(b"tampered staged fixture")
        staged.cli("refuse-tampered-apply", ["update", "apply"], success=False)
        assert staged.cli("staged-damage-version", ["--version"]) == {"version": 1}
        assert staged.state()["rejected"] == 2
        return {"incremental_bytes": delta.stat().st_size, "full_bytes": full.stat().st_size,
                "bad_checksum_refused": True, "full_fallback": True, "rollback_persisted": True,
                "tampered_staged_refused": True,
                "command_seconds": {"incremental": installed.timings, "full_fallback": fallback.timings,
                                    "staged_damage": staged.timings},
                "requests": requests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=Path, help="Directory asset from build.py directory")
    parser.add_argument("--output", required=True, type=Path, help="new private directory for evidence")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(mode=0o700)
    seed = args.seed.resolve(strict=True)
    result = verify(root, args.launcher.resolve(strict=True), seed, seed.name.removesuffix(".tar.xz"))
    (root / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
