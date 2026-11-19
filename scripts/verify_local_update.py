"""Exercise a packaged Linux update with a loopback HTTPS release fixture."""
from __future__ import annotations

import argparse
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pty
import selectors
import shutil
import signal
import ssl
import subprocess
import tempfile
from threading import Thread
import time


def _start_server(root, port, build, candidate, *, omit_sidecar):
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
        "-keyout", str(key), "-out", str(cert),
    ], check=True, capture_output=True)
    filename = candidate.name
    binary = candidate.read_bytes()
    digest = hashlib.sha256(binary).hexdigest()
    asset_path = f"/ougato/opskit-cli/releases/download/v{build}/{filename}"
    asset_url = f"https://127.0.0.1:{port}{asset_path}"
    release = {"tag_name": f"v{build}", "body": "", "draft": False, "assets": [{
        "name": filename, "browser_download_url": asset_url, "digest": f"sha256:{digest}",
    }]}
    metadata = {
        "/bootstrap.json": {"latest_build": build, "min_build": 1, "rollout": 100},
        "/repos/ougato/opskit-cli/releases/latest": release,
        f"/repos/ougato/opskit-cli/releases/tags/v{build}": release,
    }
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.path == asset_path:
                content = binary
            elif self.path == asset_path + ".sha256":
                if omit_sidecar:
                    self.send_error(503)
                    return
                content = f"{digest}  {filename}\n".encode()
            elif self.path in metadata:
                content = json.dumps(metadata[self.path]).encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, cert, requests, digest, asset_path


def _run_menu(executable, root, env, ready, timeout, *, interrupt=False):
    master, slave = pty.openpty()
    selector = selectors.DefaultSelector()
    output = bytearray()
    with subprocess.Popen([executable], cwd=root, env=env, stdin=slave, stdout=slave,
                          stderr=slave, start_new_session=True) as process:
        os.close(slave)
        try:
            selector.register(master, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            sent_exit = False
            while time.monotonic() < deadline:
                for key, _ in selector.select(timeout=.1):
                    try:
                        output.extend(os.read(key.fd, 4096))
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                if process.poll() is not None:
                    if process.returncode == 0 and sent_exit:
                        return
                    break
                if not sent_exit and ready():
                    if interrupt:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                        return
                    os.write(master, b"0")
                    sent_exit = True
            logs = "\n".join(path.read_text(errors="replace")[-4000:]
                             for path in (root / "data" / "logs").glob("*.log"))
            raise RuntimeError(f"update menu did not finish\n{logs}\n{output[-8000:].decode(errors='replace')}")
        finally:
            selector.close()
            os.close(master)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)


def _published(cache, build, digest):
    try:
        state = json.loads((cache / "update_check.json").read_bytes())
        identity = state.get("candidate_id")
        generation = cache / "update-candidates" / identity if identity else cache
        with (generation / "opskit.pending").open("rb") as handle:
            return (state.get("pending_version") == build
                    and hashlib.file_digest(handle, "sha256").hexdigest() == digest)
    except (FileNotFoundError, json.JSONDecodeError):
        return False  # The legacy client writes its cache record in place.


def _confirmed(cache, build):
    try:
        health = json.loads((cache / "update_health.json").read_bytes())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return health.get("build") == build and health.get("confirmed") is True


def _digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--from-build", type=int, required=True)
    parser.add_argument("--to-build", type=int, required=True)
    parser.add_argument("--port", type=int, default=18443)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--apply-on-startup", action="store_true")
    parser.add_argument("--omit-sidecar", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="opskit-update-run-") as directory:
        root = Path(directory)
        executable = root / "opskit"
        shutil.copy2(args.baseline, executable)
        server, thread, cert, requests, digest, asset_path = _start_server(
            root, args.port, args.to_build, args.candidate, omit_sidecar=args.omit_sidecar)
        env = {**os.environ, "OPSKIT_DATA_DIR": str(root / "data"), "SSL_CERT_FILE": str(cert),
               "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
               "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1",
               "http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1",
               "all_proxy": "http://127.0.0.1:1", "no_proxy": "127.0.0.1", "TERM": "xterm"}
        try:
            def version():
                return subprocess.check_output([executable, "--version"], cwd=root, env=env,
                                               text=True, timeout=20).strip()

            before = version()
            if before != f"v{args.from_build}":
                raise RuntimeError(f"unexpected baseline version {before}")
            baseline_digest = _digest(executable)
            cache = root / "data" / "cache"
            _run_menu(executable, root, env, lambda: _published(cache, args.to_build, digest),
                      args.timeout, interrupt=args.apply_on_startup)
            if args.apply_on_startup:
                if _digest(executable) != baseline_digest:
                    raise RuntimeError("executable changed before startup application")
                _run_menu(executable, root, env, lambda: _confirmed(cache, args.to_build), args.timeout)
            after = version()
            installed_digest = _digest(executable)
            if after != f"v{args.to_build}" or installed_digest != digest or asset_path not in requests:
                raise RuntimeError(f"unexpected upgrade result {before} → {after}, requests={requests}")
            backups = (root / "data" / "backups").glob(f"opskit.v{args.from_build}.*.bak")
            if not any(_digest(backup) == baseline_digest for backup in backups):
                raise RuntimeError("verified baseline backup missing")
            if args.omit_sidecar and not any(path.endswith(f"/releases/tags/v{args.to_build}") for path in requests):
                raise RuntimeError("tag-specific checksum fallback was not requested")
            print(json.dumps({"before": before, "after": after, "sha256": digest, "backup_verified": True,
                              "startup_health_confirmed": args.apply_on_startup, "requests": requests}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
