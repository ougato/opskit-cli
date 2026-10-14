"""Build a platform-owned immutable runtime; never execute a plugin Dockerfile."""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

CORE = Path(__file__).resolve().parent

from core.plugin_sandbox import docker_command
from core.plugin import load_manifest
from core.plugin_trust import compute_fingerprint
from core.paths import data_dir
from core.process import capture
from core.plugin_lock import CATALOG_LIMIT, read_lock, valid_base_reference
from core.plugin_runtime_inventory import RUNTIME_POLICY, snapshot, code_digest

BUILD_TIMEOUT = 600
BUILD_OUTPUT_LIMIT = 4 * 1024**2
PIP_BOOTSTRAP = "pip==26.2 --hash=sha256:931c303696af6fa3417112103b1cad26890e5a07eccb5b99783700e33f2b8aad\n"


def _command_output(command, *, timeout, stdin=subprocess.DEVNULL, max_bytes=4096):
    """Supervise the local client; never expose untrusted build diagnostics."""
    result = capture(command, timeout=timeout, stdin=stdin, max_bytes=max_bytes)
    if result.returncode:
        raise RuntimeError("plugin runtime command failed")
    return result.stdout.strip()


def build(path: Path, dependencies: list[str], base_image: str, *, lock_file: Path | None = None) -> str:
    if not valid_base_reference(base_image):
        raise ValueError("invalid base image reference")
    requirements = read_lock(lock_file, dependencies)
    manifest = load_manifest(path)
    if manifest is None:
        raise ValueError("invalid plugin manifest")
    fingerprint = compute_fingerprint(path)
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("invalid plugin content fingerprint")
    sources = snapshot(CORE)
    source_digest = code_digest(sources)
    command = docker_command()
    base_id = _command_output(command + ["image", "inspect", "--format", "{{.Id}}", base_image], timeout=30)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", base_id):
        raise ValueError("invalid base image identity")
    lock_digest = hashlib.sha256(requirements.encode("utf-8")).hexdigest()
    base_digest = base_image.rsplit("@", 1)[1]
    dockerfile = f"""FROM {base_id}
WORKDIR /runtime
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY pip-bootstrap.txt /runtime/pip-bootstrap.txt
RUN python -m pip --isolated install --disable-pip-version-check --no-deps --require-hashes --only-binary=:all: --no-cache-dir --index-url https://pypi.org/simple -r pip-bootstrap.txt
COPY requirements.txt /runtime/requirements.txt
RUN python -m pip --isolated install --disable-pip-version-check --ignore-installed --require-hashes --only-binary=:all: --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt
COPY core /runtime/core
ENV PYTHONPATH=/runtime PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
LABEL org.opskit.sandbox={RUNTIME_POLICY} org.opskit.lock-sha256={lock_digest} org.opskit.base-digest={base_digest} org.opskit.code-sha256={source_digest}
"""
    # Allowlisted build context excludes plugins, caches, secrets and user config.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as context:
        for name, text in [("Dockerfile", dockerfile), ("pip-bootstrap.txt", PIP_BOOTSTRAP), ("requirements.txt", requirements)]:
            body = text.encode("utf-8")
            item = tarfile.TarInfo(name)
            item.size = len(body)
            item.mode = 0o644
            context.addfile(item, io.BytesIO(body))
        for name, body in sorted(sources.items()):
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(body), 0o644
            context.addfile(item, io.BytesIO(body))
    content = buffer.getvalue()
    tag = "opskit-runtime:" + hashlib.sha256(content).hexdigest()[:20]
    # A file-backed input avoids a writer thread blocking on a silent client.
    # Only fixed progress text is public: pip/registry logs may contain secrets.
    print("Preparing isolated plugin runtime...", file=sys.stderr)
    with tempfile.TemporaryFile() as context_input:
        context_input.write(content)
        context_input.seek(0)
        _command_output(command + ["build", "--network=bridge", "--tag", tag, "-"],
                        stdin=context_input, timeout=BUILD_TIMEOUT, max_bytes=BUILD_OUTPUT_LIMIT)
    image = _command_output(command + ["image", "inspect", "--format", "{{.Id}}", tag], timeout=30)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("invalid prepared image identity")
    if compute_fingerprint(path) != fingerprint:
        raise RuntimeError("plugin changed during runtime preparation")
    if "network" in manifest.permissions:
        from core.plugin_gateway import build as build_gateway
        build_gateway(command)
    catalog = data_dir() / "plugin-runtimes" / (manifest.name + ".json")
    catalog.parent.mkdir(parents=True, exist_ok=True)
    from core.secrets import atomic_write
    record = json.dumps({"schema_version": 2, "image": image, "fingerprint": fingerprint,
                         "requirements_sha256": lock_digest, "base_reference": base_image,
                         "code_sha256": source_digest})
    if len(record.encode("utf-8")) > CATALOG_LIMIT:
        raise ValueError("runtime catalog exceeds limit")
    atomic_write(catalog, record)
    return image
