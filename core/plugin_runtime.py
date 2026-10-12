"""Build a platform-owned immutable runtime; never execute a plugin Dockerfile."""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

CORE = Path(__file__).resolve().parent
ROOT = CORE.parent

from core.plugin_sandbox import docker_command
from core.plugin import load_manifest
from core.plugin_trust import compute_fingerprint
from core.paths import data_dir


def build(path: Path, dependencies: list[str], base_image: str = "python:3.12-slim") -> str:
    manifest = load_manifest(path)
    if manifest is None:
        raise ValueError("invalid plugin manifest")
    for dependency in dependencies:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9_.+!-]*", dependency):
            raise ValueError("dependencies must be explicitly pinned package==version")
    command = docker_command()
    inspect = subprocess.run(command + ["image", "inspect", "--format", "{{.Id}}", base_image],
                             capture_output=True, text=True, check=True, timeout=30)
    base_id = inspect.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", base_id):
        raise ValueError("invalid base image identity")
    requirements_path = ROOT / "requirements.txt"
    if requirements_path.is_file():
        requirements = requirements_path.read_text(encoding="utf-8")
    else:
        from importlib.metadata import requires
        requirements = "\n".join(r for r in requires("opskit") or [] if ";" not in r)
    if not (CORE / "plugin_worker.py").is_file():
        raise RuntimeError("runtime preparation needs a source or wheel installation")
    requirements = "\n".join(line for line in requirements.splitlines() if not line.startswith("pytest"))
    requirements += "\n" + "\n".join(dependencies) + "\n"
    dockerfile = f"""FROM {base_id}
WORKDIR /runtime
COPY requirements.txt /runtime/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY core /runtime/core
ENV PYTHONPATH=/runtime PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
LABEL org.opskit.sandbox=2
"""
    # Allowlisted build context excludes plugins, caches, secrets and user config.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as context:
        for name, text in [("Dockerfile", dockerfile), ("requirements.txt", requirements)]:
            body = text.encode("utf-8")
            item = tarfile.TarInfo(name)
            item.size = len(body)
            item.mode = 0o644
            context.addfile(item, io.BytesIO(body))
        for source in sorted(CORE.rglob("*")):
            if source.is_file() and source.suffix in {".py", ".yaml"} and "__pycache__" not in source.parts:
                context.add(source, arcname=source.relative_to(ROOT).as_posix())
    content = buffer.getvalue()
    tag = "opskit-runtime:" + hashlib.sha256(content).hexdigest()[:20]
    subprocess.run(command + ["build", "--network=bridge", "--tag", tag, "-"], input=content, check=True, timeout=600)
    image = subprocess.check_output(command + ["image", "inspect", "--format", "{{.Id}}", tag], text=True).strip()
    catalog = data_dir() / "plugin-runtimes" / (manifest.name + ".json")
    catalog.parent.mkdir(parents=True, exist_ok=True)
    from core.secrets import atomic_write
    atomic_write(catalog, json.dumps({"image": image, "fingerprint": compute_fingerprint(path),
                                   "dependencies": dependencies}))
    return image
