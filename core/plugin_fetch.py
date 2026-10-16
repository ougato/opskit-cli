"""Platform local acquisition boundary; never fall back to host Git."""
from contextlib import ExitStack, contextmanager
import json
import hashlib
import ipaddress
import os
from pathlib import Path
import stat
import uuid

from core.plugin_fetch_candidate import supervised_inventory
from core.plugin_fetch_image import verify
from core.process_binary import capture


def prepare_tool(packages):
    """Explicit offline preparation, separate from fetching or authorizing code."""
    from core import plugin_fetch_image as tool
    from core import plugin_sandbox as sandbox
    with sandbox._execution_budget():
        command = _local_backend(sandbox.docker_command())
        sandbox._require_sandbox_capabilities(command)
    # The existing builder supervises each client, including its 600s build.
    # No claim of daemon-wide cancellation or maintenance admission is made.
    return tool.build(command, Path(packages))


def prepared_tool():
    """Bind selection to one explicit local endpoint; never consult host Git."""
    from core.plugin_fetch_image import prepared_image
    from core import plugin_sandbox as sandbox
    with sandbox._execution_budget():
        command = _local_backend(sandbox.docker_command())
        sandbox._require_sandbox_capabilities(command)
        image = prepared_image(command, control=lambda *args: sandbox._control(command, *args))
        return command, image


@contextmanager
def _remote_network(command, source, allow_private):
    """One fixed target, one internal network, with recoverable resource leases."""
    from core import plugin_sandbox as sandbox
    from core.plugin_gateway import image as gateway_image
    from core.plugin_runtime_inventory import gateway_source
    image = gateway_image(command)
    digest = hashlib.sha256(gateway_source()).hexdigest()
    actual = json.loads(sandbox._control(command, "image", "inspect", "--format",
                        '{{json (index .Config.Labels "org.opskit.gateway-code-sha256")}}', image))
    if actual != digest:
        raise ValueError("fetch gateway source identity mismatch")
    name = "opskit-net-" + uuid.uuid4().hex
    proxy = name + "-proxy"
    token = uuid.uuid4().hex + uuid.uuid4().hex
    private = [f"{source.host}:{source.port}"] if allow_private else []
    with ExitStack() as resources:
        network = resources.enter_context(sandbox._resource_scope(command, "network", name))
        sandbox._control(command, "network", "create", "--driver=bridge", "--internal", "--ipv6=false",
                         "--opt", "com.docker.network.bridge.inhibit_ipv4=true", *network.labels, name)
        lease = resources.enter_context(sandbox._resource_scope(command, "container", proxy))
        sandbox._control(command, "run", "-d", "--name", proxy, *lease.labels, "--pull=never",
                         "--network=bridge", "--read-only", "--user=65534:65534", "--cap-drop=ALL",
                         "--security-opt=no-new-privileges", "--memory=64m", "--memory-swap=64m", "--pids-limit=32",
                         "--cpus=0.5", "--log-driver=none", "--env", "OPSKIT_PROXY_TOKEN=" + token,
                         "--env", "OPSKIT_PRIVATE_TARGETS=" + json.dumps(private),
                         "--env", "OPSKIT_FETCH_TARGET=" + source.authority,
                         "--entrypoint=python", image, "-I", "-S", "/runtime/gateway.py")
        sandbox._control(command, "network", "connect", name, proxy)
        try:
            networks = json.loads(sandbox._control(command, "inspect", "--format", "{{json .NetworkSettings.Networks}}", proxy))
            address = ipaddress.IPv4Address(networks[name]["IPAddress"])
            if (address.is_loopback or address.is_unspecified or address.is_reserved
                    or address.is_multicast or address.is_link_local):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError("invalid fetch gateway address") from None
        yield name, {"address": str(address), "port": 8080, "token": token}


@contextmanager
def remote_candidate(command, image, source, *, profile=None, profile_file=None, branch=None, allow_private=False):
    """Acquire an explicitly authenticated source, without importing or publishing.

    Profile is sensitive, platform-owned material, never plugin metadata. Private
    access is an explicit grant for this exact target only. An explicit profile
    file is read privately under the same execution budget before daemon calls.
    """
    from core import plugin_sandbox as sandbox
    from core.plugin_fetch_source import authentication, remote_source
    from core.plugin_resources import _backend
    source = remote_source(source)
    if (profile is not None and profile_file is not None or type(allow_private) is not bool or branch is not None and
            (not isinstance(branch, str) or not 0 < len(branch.encode()) <= 256
             or branch.startswith("-") or any(ord(c) < 32 for c in branch))):
        raise ValueError("invalid remote fetch request")
    if profile_file is None:
        authentication(source, profile)
    command = _backend(command)
    with ExitStack() as buffers:
        with sandbox._execution_budget():
            if profile_file is not None:
                from core.plugin_fetch_profile import load_profile
                profile = load_profile(source.url, profile_file, timeout=sandbox._remaining(sandbox.CONTROL_TIMEOUT))
            # Snapshot caller material before daemon calls or credential use.
            encoded = json.dumps({"schema_version": 1, "kind": source.kind, "source": source.url,
                                  "profile": profile, "branch": branch}, ensure_ascii=False).encode()
            if len(encoded) > 130000:
                raise ValueError("remote fetch request budget exceeded")
            request = json.loads(encoded)
            command = _local_backend(command)
            sandbox._require_sandbox_capabilities(command)
            verify(command, image, control=lambda *args: sandbox._control(command, *args))
            with _remote_network(command, source, allow_private) as (network, proxy):
                request["proxy"] = proxy
                raw = json.dumps(request, ensure_ascii=False).encode()
                name = "opskit-" + uuid.uuid4().hex
                with sandbox._resource_scope(command, "container", name) as lease:
                    args = [*command, "run", "--name", name, *lease.labels, "--pull=never", "--network", network,
                            "--read-only", "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                            "--memory=512m", "--memory-swap=512m", "--pids-limit=64", "--cpus=1", "--log-driver=none",
                            "--tmpfs", "/work:rw,nosuid,nodev,noexec,size=256m,mode=1777",
                            "--interactive", "--entrypoint=python", image, "-I", "-S", "/runtime/fetch.py"]
                    response = buffers.enter_context(capture(args, raw, timeout=sandbox._remaining(300), request_limit=131072))
                    inventory = supervised_inventory(response, timeout=sandbox._remaining())
                    sandbox._remaining()
        yield inventory, response


def _local_backend(command):
    from core import plugin_sandbox as sandbox
    from core.plugin_resources import _backend
    command = _backend(command)
    if "--host" in command:
        return command
    endpoint = os.environ.get("DOCKER_HOST") if command == ["docker"] else None
    if endpoint is None:
        endpoint = json.loads(sandbox._control(command, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"))
    return _backend([*command, "--host", endpoint])


def _source_worker(connection, source):
    from network.worker import send
    try:
        path = Path(source)
        identities = []
        for entry in (path, *path.parents):
            info = entry.lstat()
            if (not stat.S_ISDIR(info.st_mode) or entry.is_symlink()
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise ValueError("linked or invalid source")
            identities.append([info.st_dev, info.st_ino, info.st_mode])
        send(connection, {"identities": identities})
    except (OSError, ValueError):
        send(connection, {"error": "invalid local fetch source"})
    finally:
        connection.close()


def _source_identity(source, timeout):
    from network.worker import run
    result = run(_source_worker, (str(source),), timeout=timeout, label="local fetch source")
    if (not isinstance(result, dict) or set(result) != {"identities"}
            or not isinstance(result["identities"], list)
            or len(result["identities"]) != len(source.parents) + 1
            or any(not isinstance(item, list) or len(item) != 3
                   or any(type(value) is not int or value < 0 for value in item) for item in result["identities"])):
        raise ValueError("invalid local fetch source")
    return result["identities"]


@contextmanager
def local_candidate(command, image, source, *, branch=None):
    """Yield an inventory and private response after leased container cleanup.

    Does not extract, publish or authorize. Source contents may change while
    fetching; the reported commit identifies the resulting snapshot. Ancestor
    identity checks detect replacement observations, not same-user rename/restore
    races or remote-daemon filesystem equivalence.
    """
    from core import plugin_sandbox as sandbox
    from core.plugin_resources import _backend

    command = _backend(command)
    source = Path(source).absolute()
    if (len(str(source)) > 4096 or branch is not None and
            (not isinstance(branch, str) or not 0 < len(branch.encode()) <= 256
             or branch.startswith("-") or any(ord(c) < 32 for c in branch))):
        raise ValueError("invalid local fetch request")
    request = json.dumps({"schema_version": 1, "kind": "local", "branch": branch}).encode()
    with ExitStack() as buffers:
        with sandbox._execution_budget():
            command = _local_backend(command)
            sandbox._require_sandbox_capabilities(command)
            verify(command, image, control=lambda *args: sandbox._control(command, *args))
            before = _source_identity(source, sandbox._remaining(sandbox.CONTROL_TIMEOUT))
            mounted = sandbox.mount_path(source, command)
            name = "opskit-" + uuid.uuid4().hex
            with sandbox._resource_scope(command, "container", name) as lease:
                args = [*command, "run", "--name", name, *lease.labels, "--pull=never", "--network=none",
                        "--read-only", "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                        "--memory=512m", "--memory-swap=512m", "--pids-limit=64", "--cpus=1", "--log-driver=none",
                        "--tmpfs", "/work:rw,nosuid,nodev,noexec,size=256m,mode=1777",
                        "--mount", "type=bind,src=" + mounted + ",dst=/source,readonly",
                        "--interactive", "--entrypoint=python", image, "-I", "-S", "/runtime/fetch.py"]
                response = buffers.enter_context(capture(args, request, timeout=sandbox._remaining(300)))
                inventory = supervised_inventory(response, timeout=sandbox._remaining())
                if _source_identity(source, sandbox._remaining(sandbox.CONTROL_TIMEOUT)) != before:
                    raise ValueError("local fetch source identity changed")
                sandbox._remaining()
        yield inventory, response
