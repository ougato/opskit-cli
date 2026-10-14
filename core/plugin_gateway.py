"""Platform egress authority; never inherits a plugin base, wheel or hook."""
import hashlib
import io
import json
import re
import shlex
import tarfile
import tempfile
from core.paths import data_dir
from core.plugin_runtime_inventory import gateway_source
from core.resource_lock import ResourceLock

POLICY = 2
BASE_REFERENCE = "docker.io/library/python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
LABELS = ("org.opskit.gateway", "org.opskit.gateway-base-digest", "org.opskit.gateway-code-sha256")
_lock = ResourceLock(lambda: data_dir() / "plugin-runtimes" / ".gateway.lock", label="plugin gateway preparation")

# The gateway runs only platform stdlib code. It needs no privileged utility
# bits or terminal-description analyser; retain the libraries Python uses.
HARDENING_SCRIPT = """import os, stat
for base in ('/usr/bin', '/usr/sbin', '/usr/local/bin'):
    for parent, dirs, files in os.walk(base, followlinks=False):
        for name in files:
            path = os.path.join(parent, name)
            mode = os.lstat(path).st_mode
            if stat.S_ISREG(mode) and mode & 0o6000:
                os.chmod(path, stat.S_IMODE(mode) & ~0o6000)
path = '/usr/bin/infocmp'
if os.path.lexists(path):
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise RuntimeError('unexpected gateway utility path type')
    os.unlink(path)
"""


def _catalog():
    # A reserved dot name cannot collide with a validated plugin identity.
    return data_dir() / "plugin-runtimes" / ".gateway.json"


def image(command):
    """Only a prepared platform image may receive external connectivity."""
    from core.plugin_sandbox import (SandboxError, RuntimeCatalogError, _control, _remaining,
                                     _read_runtime_catalog, _invocations, _execution_budget)
    if not getattr(_invocations, "inspection_scope", False):
        with _execution_budget():
            return image(command)
    try:
        _remaining()
        try:
            record = _read_runtime_catalog(_catalog(), gateway=True)
        except RuntimeCatalogError:
            raise ValueError("invalid gateway identity") from None
        digest = record["code_sha256"]
        fields = ["{{json .Id}}", *("{{json (index .Config.Labels " + json.dumps(label) + ")}}" for label in LABELS)]
        actual = json.loads(_control(command, "image", "inspect", "--format", "[" + ",".join(fields) + "]", record["image"]))
        if actual != [record["image"], str(POLICY), BASE_REFERENCE.rsplit("@", 1)[1], digest]:
            raise ValueError("gateway supply chain identity mismatch")
        _remaining()
        return record["image"]
    except SandboxError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise SandboxError("prepare a platform gateway with plugin prepare before network execution") from None


def build(command):
    from core.plugin_runtime import _command_output
    from core.plugin_sandbox import SandboxError
    from core.secrets import atomic_write
    with _lock.hold():
        try:
            return image(command)
        except SandboxError:
            pass
        source = gateway_source()
        digest = hashlib.sha256(source).hexdigest()
        # The fixed official source is independent of every plugin's base.
        _command_output([*command, "pull", BASE_REFERENCE], timeout=300, max_bytes=128 * 1024)
        base = _command_output([*command, "image", "inspect", "--format", "{{.Id}}", BASE_REFERENCE], timeout=30)
        if re.fullmatch(r"sha256:[0-9a-f]{64}", base) is None:
            raise RuntimeError("invalid platform gateway base identity")
        hardening = shlex.quote("exec(" + repr(HARDENING_SCRIPT) + ")")
        dockerfile = f"""FROM {base}
WORKDIR /runtime
RUN rm -rf /usr/local/lib/python3.12/site-packages && mkdir /usr/local/lib/python3.12/site-packages && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12
RUN python -I -S -c {hardening}
COPY gateway.py /runtime/gateway.py
ENV PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
ENTRYPOINT []
LABEL org.opskit.gateway={POLICY} org.opskit.gateway-base-digest={BASE_REFERENCE.rsplit('@', 1)[1]} org.opskit.gateway-code-sha256={digest}
"""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, body in (("Dockerfile", dockerfile.encode()), ("gateway.py", source)):
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.mtime = len(body), 0o644, 0
                archive.addfile(member, io.BytesIO(body))
        content = buffer.getvalue()
        tag = "opskit-gateway:" + hashlib.sha256(content).hexdigest()[:20]
        with tempfile.TemporaryFile() as context:
            context.write(content)
            context.seek(0)
            _command_output([*command, "build", "--network=none", "--tag", tag, "-"], stdin=context,
                            timeout=300, max_bytes=1024 * 1024)
        prepared = _command_output([*command, "image", "inspect", "--format", "{{.Id}}", tag], timeout=30)
        if re.fullmatch(r"sha256:[0-9a-f]{64}", prepared) is None:
            raise RuntimeError("invalid prepared gateway identity")
        if hashlib.sha256(gateway_source()).hexdigest() != digest:
            raise RuntimeError("gateway source changed during preparation")
        record = {"schema_version": POLICY, "image": prepared, "base_reference": BASE_REFERENCE,
                  "code_sha256": digest}
        atomic_write(_catalog(), json.dumps(record))
        return image(command)
