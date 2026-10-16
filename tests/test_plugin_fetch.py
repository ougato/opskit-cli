"""Acquisition never yields content before validation and lease cleanup."""
from contextlib import contextmanager
import io
import json
from types import SimpleNamespace

import pytest

from core import plugin_fetch as fetch, plugin_sandbox as sandbox


@pytest.mark.parametrize("operation", ["prepare", "select"])
def test_tool_service_keeps_original_local_endpoint_after_environment_changes(monkeypatch, tmp_path, operation):
    from core import plugin_fetch_image as tool
    @contextmanager
    def budget():
        yield
    pinned = ["docker", "--host", "unix:///var/run/docker.sock"]
    identifier = "sha256:" + "a" * 64
    monkeypatch.setattr(sandbox, "_execution_budget", budget)
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])
    monkeypatch.setenv("DOCKER_HOST", pinned[-1])
    def capabilities(command):
        assert command == pinned
        monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid:2375")
    monkeypatch.setattr(sandbox, "_require_sandbox_capabilities", capabilities)
    def build(command, packages):
        assert command == pinned and packages == tmp_path
        return identifier
    def control(command, *args):
        assert command == pinned
        if args[:2] == ("image", "ls"):
            return identifier
        assert args[:2] == ("image", "inspect") and args[-1] == identifier
        return json.dumps(tool._expected_identity())
    monkeypatch.setattr(tool, "build", build)
    monkeypatch.setattr(sandbox, "_control", control)
    if operation == "prepare":
        assert fetch.prepare_tool(tmp_path) == identifier
    else:
        assert fetch.prepared_tool() == (pinned, identifier)


@pytest.mark.parametrize("operation", ["prepare", "select"])
@pytest.mark.parametrize("endpoint", ["tcp://localhost:2375", "ssh://untrusted.invalid"])
def test_tool_services_refuse_remote_daemon_before_capabilities_or_build(monkeypatch, tmp_path, operation, endpoint):
    from core import plugin_fetch_image as tool
    @contextmanager
    def budget():
        yield
    monkeypatch.setattr(sandbox, "_execution_budget", budget)
    monkeypatch.setattr(sandbox, "docker_command", lambda: ["docker"])
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.setattr(sandbox, "_require_sandbox_capabilities", lambda *args: pytest.fail("remote daemon queried"))
    monkeypatch.setattr(tool, "build", lambda *args: pytest.fail("remote daemon built tool"))
    with pytest.raises(ValueError, match="local Docker endpoint"):
        fetch.prepare_tool(tmp_path) if operation == "prepare" else fetch.prepared_tool()


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    events, calls = [], []
    @contextmanager
    def budget():
        yield
    @contextmanager
    def lease(*args):
        events.append("leased")
        try:
            yield SimpleNamespace(labels=["--label", "fixture=owned"])
        finally:
            events.append("cleaned")
    @contextmanager
    def capture(command, request, **kwargs):
        calls.append((command, json.loads(request), kwargs))
        with io.BytesIO(b"fixture response") as response:
            yield response
    monkeypatch.setattr(sandbox, "_execution_budget", budget)
    monkeypatch.setattr(sandbox, "_require_sandbox_capabilities", lambda command: events.append("capabilities"))
    monkeypatch.setattr(sandbox, "_remaining", lambda cap=None: cap or 30)
    monkeypatch.setattr(sandbox, "mount_path", lambda source, command: "/fixture-source")
    monkeypatch.setattr(sandbox, "_resource_scope", lease)
    monkeypatch.setattr(fetch, "verify", lambda *a, **k: events.append("verified"))
    monkeypatch.setattr(fetch, "_local_backend", lambda command: command)
    monkeypatch.setattr(fetch, "_source_identity", lambda *a: [1, 2, 3])
    monkeypatch.setattr(fetch, "capture", capture)
    monkeypatch.setattr(fetch, "supervised_inventory", lambda stream, **kwargs: events.append("validated") or "inventory")
    return tmp_path, events, calls


def test_verified_readonly_execution_is_cleaned_before_yield(boundary):
    source, events, calls = boundary
    with fetch.local_candidate(["docker"], "sha256:" + "a" * 64, source, branch="main") as (inventory, response):
        assert inventory == "inventory" and response.read() == b"fixture response"
        assert events == ["capabilities", "verified", "leased", "validated", "cleaned"]
        held = response
    assert held.closed
    command, request, kwargs = calls[0]
    assert request == {"schema_version": 1, "kind": "local", "branch": "main"}
    assert "--network=none" in command and "--read-only" in command and "--cap-drop=ALL" in command
    assert "--memory-swap=512m" in command and "--log-driver=none" in command
    assert command[command.index("--mount") + 1] == "type=bind,src=/fixture-source,dst=/source,readonly"
    assert "--rm" not in command and kwargs["timeout"] == 300


def test_failed_validation_still_cleans_and_never_yields(boundary, monkeypatch):
    source, events, calls = boundary
    def invalid(stream, **kwargs):
        raise ValueError("invalid archive")
    monkeypatch.setattr(fetch, "supervised_inventory", invalid)
    with pytest.raises(ValueError, match="invalid archive"):
        with fetch.local_candidate(["docker"], "sha256:" + "a" * 64, source):
            pytest.fail("invalid candidate yielded")
    assert events[-1] == "cleaned"


def test_source_replacement_refuses_candidate_after_cleanup(boundary, monkeypatch):
    source, events, calls = boundary
    identities = iter([[1], [2]])
    monkeypatch.setattr(fetch, "_source_identity", lambda *a: next(identities))
    with pytest.raises(ValueError, match="identity changed"):
        with fetch.local_candidate(["docker"], "sha256:" + "a" * 64, source):
            pytest.fail("replaced source yielded")
    assert events[-1] == "cleaned"


def test_bad_image_refused_before_resource_or_client(boundary, monkeypatch):
    source, events, calls = boundary
    def invalid(*a, **k):
        raise ValueError("invalid image")
    monkeypatch.setattr(fetch, "verify", invalid)
    with pytest.raises(ValueError, match="invalid image"):
        with fetch.local_candidate(["docker"], "tag", source):
            pytest.fail("unverified image yielded")
    assert events == ["capabilities"] and calls == []


@pytest.mark.parametrize("branch", ["-option", "x\ny", "x" * 257, 3])
def test_invalid_branch_rejected_before_daemon(boundary, branch):
    source, events, calls = boundary
    with pytest.raises(ValueError):
        with fetch.local_candidate(["docker"], "tag", source, branch=branch):
            pass
    assert events == [] and calls == []


def test_real_source_worker_binds_ordinary_ancestors(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = fetch._source_identity(source, 10)
    source.rename(tmp_path / "original")
    source.mkdir()
    assert fetch._source_identity(source, 10) != first


def test_linked_source_refused(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable")
    with pytest.raises(ValueError, match="invalid local fetch source"):
        fetch._source_identity(alias, 10)


@pytest.mark.parametrize("endpoint", ["tcp://localhost:2375", "ssh://remote", "unix:///a/../socket", "unix:///a\n"])
def test_remote_or_invalid_endpoint_is_not_local_source_authority(monkeypatch, endpoint):
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    with pytest.raises(ValueError, match="local Docker endpoint"):
        fetch._local_backend(["docker"])


def test_local_endpoint_is_pinned_in_recoverable_backend(monkeypatch):
    from core.plugin_resources import _backend
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    pinned = fetch._local_backend(["docker"])
    assert pinned == ["docker", "--host", "unix:///var/run/docker.sock"]
    assert _backend(pinned) == pinned


def test_wsl_endpoint_pinning_is_supported_for_resource_recovery():
    from core.plugin_resources import _backend
    command = ["wsl", "-d", "Debian", "--exec", "docker", "--host", "unix:///var/run/docker.sock"]
    assert _backend(command) == command


def test_remote_private_request_and_cleanup_before_yield(boundary, monkeypatch):
    _, events, calls = boundary
    @contextmanager
    def network(command, source, allow_private):
        assert source.url == "https://example.com:443/org/repo.git" and allow_private is False
        events.append("network")
        try:
            yield "fixture-internal", {"address": "172.20.0.2", "port": 8080, "token": "a" * 64}
        finally:
            events.append("network-cleaned")
    monkeypatch.setattr(fetch, "_remote_network", network)
    profile = {"schema_version": 1, "source": "https://example.com/org/repo.git",
               "authentication": {"kind": "bearer", "token": "fixture-secret"}}
    with fetch.remote_candidate(["docker"], "sha256:" + "a" * 64, profile["source"], profile=profile) as (inventory, response):
        assert inventory == "inventory" and response.read() == b"fixture response"
        assert events[-2:] == ["cleaned", "network-cleaned"]
        held = response
    assert held.closed
    command, request, kwargs = calls[0]
    assert "fixture-secret" not in repr(command) and "--mount" not in command
    assert command[command.index("--network") + 1] == "fixture-internal"
    assert request["profile"] == profile and kwargs["request_limit"] == 131072


@pytest.mark.parametrize("source", ["http://example.com/repo", "https://user:secret@example.com/repo", "git@example.com:repo"])
def test_invalid_or_uncredentialed_remote_refused_before_daemon(boundary, source):
    _, events, calls = boundary
    with pytest.raises(ValueError):
        with fetch.remote_candidate(["docker"], "tag", source):
            pytest.fail("invalid remote yielded")
    assert events == [] and calls == []


def test_profile_file_and_inline_profile_are_mutually_exclusive(boundary):
    _, events, calls = boundary
    with pytest.raises(ValueError, match="request"):
        with fetch.remote_candidate(["docker"], "tag", "https://example.com/repo", profile={}, profile_file="file"):
            pytest.fail("ambiguous credentials yielded")
    assert events == [] and calls == []


def test_profile_file_rejection_occurs_before_daemon_or_resources(boundary, monkeypatch):
    from core import plugin_fetch_profile
    _, events, calls = boundary
    def refuse(*a, **k):raise ValueError("profile refused")
    monkeypatch.setattr(plugin_fetch_profile, "load_profile", refuse)
    with pytest.raises(ValueError, match="profile refused"):
        with fetch.remote_candidate(["docker"], "tag", "https://example.com/repo", profile_file="file"):
            pytest.fail("rejected profile yielded")
    assert events == [] and calls == []


@pytest.mark.parametrize("allow_private", [False, True])
def test_remote_network_scopes_target_and_cleans_in_reverse_order(monkeypatch, allow_private):
    from core import plugin_gateway, plugin_runtime_inventory
    from core.plugin_fetch_source import remote_source
    import hashlib
    calls, events = [], []
    @contextmanager
    def lease(command, kind, name):
        events.append(("open", kind))
        try:
            yield SimpleNamespace(labels=["--label", "fixture=owned"])
        finally:
            events.append(("close", kind))
    def control(command, *args):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps(hashlib.sha256(plugin_runtime_inventory.gateway_source()).hexdigest())
        if args[0] == "inspect":
            network_name = next(c[-1] for c in calls if c[:2] == ("network", "create"))
            return json.dumps({network_name: {"IPAddress": "172.20.0.2"}})
        return ""
    monkeypatch.setattr(plugin_gateway, "image", lambda command: "sha256:" + "b" * 64)
    monkeypatch.setattr(sandbox, "_control", control)
    monkeypatch.setattr(sandbox, "_resource_scope", lease)
    source = remote_source("https://example.com/repo.git")
    with fetch._remote_network(["docker"], source, allow_private) as (network, proxy):
        assert proxy["address"] == "172.20.0.2" and len(proxy["token"]) == 64
        assert events == [("open", "network"), ("open", "container")]
    assert events[-2:] == [("close", "container"), ("close", "network")]
    run = next(c for c in calls if c[0] == "run")
    assert "OPSKIT_FETCH_TARGET=example.com:443" in run
    assert 'OPSKIT_PRIVATE_TARGETS=' + json.dumps(["example.com:443"] if allow_private else []) in run
    assert "--log-driver=none" in run and "--mount" not in run
    assert any(c[:2] == ("network", "create") and "--internal" in c for c in calls)
