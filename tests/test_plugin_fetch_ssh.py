"""SSH authentication is explicit and cannot turn Git operands into authority."""
import base64
import io
import json
import os
import shlex
import struct
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from core import plugin_fetch_ssh as ssh


PROXY = {"address": "172.20.0.2", "port": 8080, "token": "a" * 64}
SOURCE = "ssh://git@example.com:2222/org/repo.git"
PRIVATE = "-----BEGIN OPENSSH PRIVATE KEY-----\nYQ==\n-----END OPENSSH PRIVATE KEY-----\n"
KEY = b"".join(struct.pack(">I", len(x)) + x for x in [b"ssh-ed25519", b"a" * 32])
PIN = "ssh-ed25519 " + base64.b64encode(KEY).decode()


def profile(source=SOURCE, passphrase="fixture-passphrase"):
    return {"schema_version": 1, "source": source, "authentication": {
        "kind": "ssh_key", "private_key": PRIVATE, "host_keys": [PIN], "passphrase": passphrase}}


@pytest.fixture
def work(tmp_path):
    (tmp_path / "home").mkdir(mode=0o700)
    return tmp_path


@pytest.mark.parametrize("passphrase", ["", "fixture-passphrase", "带空格的 phrase"])
def test_guest_only_private_files_and_secret_free_git_command(work, passphrase):
    assert ssh.configure(SOURCE, profile(passphrase=passphrase), PROXY, work) == SOURCE
    home = work / "home"
    assert (home / "identity").read_text() == PRIVATE
    assert (home / "passphrase").read_text(encoding="utf-8") == passphrase
    assert (home / "known_hosts").read_text().startswith("[example.com]:2222 ssh-ed25519 ")
    config = (home / "gitconfig").read_text()
    assert "fixture-passphrase" not in config and PRIVATE not in config and PROXY["token"] not in config
    assert 'allow = never' in config and '[protocol "ssh"]\n allow = always' in config
    assert 'variant = ssh' in config and 'version = 0' in config and ssh.HELPER in config
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in home.iterdir())


@pytest.mark.parametrize("source", [SOURCE, "git@example.com:org/repo.git",
                                   "ssh://git@[2001:4860::1]:2222/org/repo.git"])
def test_fixed_client_ignores_git_operands_and_host_environment(work, source, monkeypatch):
    ssh.configure(source, profile(source=source), PROXY, work)
    monkeypatch.setenv("SSH_AUTH_SOCK", "fixture-host-agent")
    monkeypatch.setenv("GIT_SSH_COMMAND", "fixture-host-command")
    calls = []
    class Executed(Exception): pass
    def execute(binary, command, environment):
        calls.append((binary, command, environment))
        raise Executed()
    monkeypatch.setattr(ssh.os, "execve", execute)
    monkeypatch.setattr(ssh.sys, "argv", [ssh.HELPER, "client", str(work / "home/binding.json"), "-oProxyCommand=fixture-host-command"])
    with pytest.raises(Executed):
        ssh.main()
    binary, command, environment = calls[0]
    assert binary == "/usr/bin/ssh" and command[:3] == [binary, "-F", "/dev/null"]
    for required in ["IdentityAgent=none", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes", "GlobalKnownHostsFile=/dev/null",
                     "UpdateHostKeys=no", "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "ClearAllForwardings=yes"]:
        assert required in command
    assert "fixture-host" not in repr(calls) and "fixture-passphrase" not in repr(calls)
    assert environment["SSH_ASKPASS_REQUIRE"] == "force" and environment["SSH_ASKPASS"] == ssh.HELPER
    assert "SSH_AUTH_SOCK" not in environment and "GIT_SSH_COMMAND" not in environment
    assert command[-1] in ["git-upload-pack /org/repo.git", "git-upload-pack org/repo.git"]


@pytest.mark.parametrize("path", ["/org/repo.git", "/space repo.git", "/semi;$(touch bad)'quote"])
def test_remote_path_is_one_shell_operand(work, path, monkeypatch):
    source = "ssh://git@example.com:2222" + quote(path, safe="/")
    ssh.configure(source, profile(source=source), PROXY, work)
    commands = []
    monkeypatch.setattr(ssh.os, "execve", lambda binary, command, environment: commands.append(command))
    ssh._client(work / "home/binding.json")
    assert shlex.split(commands[0][-1]) == ["git-upload-pack", path]


def test_askpass_only_returns_bound_phrase_to_key_prompt(work, monkeypatch):
    ssh.configure(SOURCE, profile(), PROXY, work)
    output = io.BytesIO()
    monkeypatch.setattr(ssh.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(ssh.sys, "argv", [ssh.HELPER, "Enter passphrase for key '/work/key': "])
    ssh._askpass(work / "home/binding.json")
    assert output.getvalue() == b"fixture-passphrase\n"


@pytest.mark.parametrize("prompt", ["Password:", "Confirm host key", "", "Enter password: "])
def test_unexpected_askpass_prompt_never_emits_secret(work, monkeypatch, prompt):
    ssh.configure(SOURCE, profile(), PROXY, work)
    output = io.BytesIO()
    monkeypatch.setattr(ssh.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(ssh.sys, "argv", [ssh.HELPER, prompt])
    with pytest.raises(ValueError, match="unexpected"):
        ssh._askpass(work / "home/binding.json")
    assert output.getvalue() == b""


@pytest.mark.parametrize("failure", ["proxy", "source", "missing_profile", "mismatched_profile"])
def test_invalid_policy_refused_before_files(work, failure):
    source, auth, proxy = SOURCE, profile(), PROXY
    if failure == "proxy":proxy = {**PROXY, "address": "127.0.0.1"}
    elif failure == "source":source = "https://example.com/repo.git"
    elif failure == "missing_profile":auth = None
    else:auth = profile(source=SOURCE + "-other")
    with pytest.raises(ValueError):
        ssh.configure(source, auth, proxy, work)
    assert list((work / "home").iterdir()) == []


@pytest.mark.parametrize("value", [{}, {"source": SOURCE, "proxy": PROXY, "extra": True},
                                  {"source": "https://example.com/repo.git", "proxy": PROXY}])
def test_invalid_internal_binding_refused(work, value):
    binding = work / "home/binding.json"
    binding.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        ssh._binding(binding)
