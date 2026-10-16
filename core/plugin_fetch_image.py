"""Offline platform Git/SSH toolchain build; no plugin-supplied build input."""
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import tarfile
import tempfile

from core.plugin_fetch_lock import BASE_IMAGE, BASE_REFERENCE, INSTALLED_PACKAGES_SHA256, LOCK
from core.plugin_image_hardening import HARDENING_SCRIPT
from core.plugin_runtime_inventory import _read_file, code_digest, fetch_snapshot, fetch_source

POLICY = 4
MAX_PACKAGE_BYTES = 32 * 1024**2
MAX_PACKAGES_BYTES = 64 * 1024**2


def _recipe(lock_digest, worker_digest, sources_digest):
    inventory = (
        "import hashlib,subprocess; "
        "body=subprocess.check_output(['dpkg-query','-W','-f=${Package}\\t${Version}\\t${Architecture}\\n']); "
        "actual=hashlib.sha256(b''.join(sorted(body.splitlines(keepends=True)))).hexdigest(); "
        f"assert actual == {INSTALLED_PACKAGES_SHA256!r}, 'unexpected fetch package inventory'"
    )
    harden = shlex.quote("exec(" + repr(HARDENING_SCRIPT) + ")")
    return f"""FROM {BASE_IMAGE}
COPY packages/ /packages/
RUN dpkg -i /packages/*.deb && apt-get -o APT::Sandbox::User=root --no-download check && rm -rf /packages /var/lib/apt/lists/* /var/cache/apt/archives/*
RUN python -I -S -c {shlex.quote(inventory)}
RUN rm -rf /usr/local/lib/python3.12/site-packages && mkdir /usr/local/lib/python3.12/site-packages && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12
RUN python -I -S -c {harden}
RUN mkdir /empty /runtime && chmod 0555 /empty /runtime
COPY fetch.py /runtime/fetch.py
COPY core/ /runtime/core/
RUN chmod 0555 /runtime/core/plugin_fetch_ssh.py
ENV PYTHONDONTWRITEBYTECODE=1 HOME=/tmp/home GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0
WORKDIR /tmp
USER 65534:65534
ENTRYPOINT []
LABEL org.opskit.fetch-toolchain={POLICY} org.opskit.fetch-lock-sha256={lock_digest} org.opskit.fetch-worker-sha256={worker_digest} org.opskit.fetch-sources-sha256={sources_digest}
""".encode()


def _member(archive, name, body):
    member = tarfile.TarInfo(name)
    member.size, member.mode, member.mtime = len(body), 0o644, 0
    archive.addfile(member, io.BytesIO(body))


def build(command, packages: Path):
    """Build from exact reviewed archives and an already present fixed base.

    Acquisition of signed packages and the base is a separate preparation step.
    Missing/changed archives fail before invoking Docker. No network or host-Git
    fallback is available. This image alone does not authorize a fetch operation.
    """
    from core.plugin_runtime import _command_output

    packages = Path(packages).absolute()
    lock_digest = hashlib.sha256(json.dumps(LOCK, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    sources = fetch_snapshot()
    worker = sources["core/plugin_fetch_worker.py"]
    worker_digest = hashlib.sha256(worker).hexdigest()
    with tempfile.TemporaryFile() as context:
        total = 0
        with tarfile.open(fileobj=context, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            _member(archive, "Dockerfile", _recipe(lock_digest, worker_digest, code_digest(sources)))
            _member(archive, "fetch.py", worker)
            for name, body in sorted(sources.items()):
                if name != "core/plugin_fetch_worker.py":
                    _member(archive, name, body)
            for package in LOCK["packages"]:
                total += package["bytes"]
                if total > MAX_PACKAGES_BYTES or package["bytes"] > MAX_PACKAGE_BYTES:
                    raise ValueError("fetch package budget exceeded")
                body = _read_file(packages / package["file"], packages, MAX_PACKAGE_BYTES)
                if len(body) != package["bytes"] or hashlib.sha256(body).hexdigest() != package["sha256"]:
                    raise ValueError("fetch package integrity mismatch")
                _member(archive, "packages/" + package["file"], body)
        context.seek(0)
        context_digest = _digest(context)
        tag = "opskit-fetch-toolchain:" + context_digest[:20]
        fields = '{"id":{{json .Id}},"architecture":{{json .Architecture}}}'
        actual = json.loads(_command_output([*command, "image", "inspect", "--format", fields, BASE_REFERENCE], timeout=30))
        if actual != {"id": BASE_IMAGE, "architecture": "amd64"}:
            raise ValueError("fetch base identity or architecture mismatch")
        context.seek(0)
        _command_output([*command, "build", "--network=none", "--pull=false", "--platform=linux/amd64", "--tag", tag, "-"],
                        stdin=context, timeout=600, max_bytes=4 * 1024**2)
        result = _command_output([*command, "image", "inspect", "--format", "{{.Id}}", tag], timeout=30)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", result):
            raise ValueError("invalid fetch image identity")
        verify(command, result)
        if fetch_snapshot() != sources:
            raise ValueError("fetch worker changed during preparation")
        return result


def verify(command, image, *, control=None):
    """Require a content-addressed image with current platform lock/worker labels."""
    if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("invalid fetch image identity")
    if control is None:
        from core.plugin_runtime import _command_output
        def control(*args):
            return _command_output([*command, *args], timeout=30)
    lock_digest = hashlib.sha256(json.dumps(LOCK, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    worker_digest = hashlib.sha256(fetch_source()).hexdigest()
    fields = ('{"architecture":{{json .Architecture}},'
              '"policy":{{json (index .Config.Labels "org.opskit.fetch-toolchain")}},'
              '"lock":{{json (index .Config.Labels "org.opskit.fetch-lock-sha256")}},'
              '"sources":{{json (index .Config.Labels "org.opskit.fetch-sources-sha256")}},'
              '"worker":{{json (index .Config.Labels "org.opskit.fetch-worker-sha256")}}}')
    actual = json.loads(control("image", "inspect", "--format", fields, image))
    if actual != {"architecture": "amd64", "policy": str(POLICY), "lock": lock_digest, "worker": worker_digest,
                  "sources": code_digest(fetch_snapshot())}:
        raise ValueError("fetch image identity mismatch")


def _digest(stream):
    digest = hashlib.sha256()
    for body in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(body)
    return digest.hexdigest()
