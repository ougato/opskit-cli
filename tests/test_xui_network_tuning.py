"""TCP tuning capability, persistence, readback and failure recovery."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from software.base import InstallError
from xui import network_tuning as tuning


@pytest.fixture
def host(tmp_path, monkeypatch):
    sysctl = tmp_path / "sysctl.conf"
    modules = tmp_path / "modules.conf"
    sysctl.write_bytes(b"# previous managed config\r\n# no final newline \xff")
    values = {"net.ipv4.tcp_available_congestion_control": "reno cubic",
              "net.ipv4.tcp_congestion_control": "cubic", "net.ipv4.tcp_mtu_probing": "0",
              "net.core.default_qdisc": "fq_codel"}
    state = SimpleNamespace(values=values, commands=[], writes=[], fail=None, available=True,
                            ignore_readback=False, sysctl=sysctl, modules=modules)

    def run(command, **kwargs):
        state.commands.append(command)
        assert kwargs["timeout"] > 0
        if command[:2] == ["test", "-f"]:
            return SimpleNamespace(returncode=0 if Path(command[2]).is_file() else 1)
        if command[0] == "head":
            assert not kwargs.get("text")
            return SimpleNamespace(stdout=Path(command[-1]).read_bytes()[:int(command[2])])
        if command[0] == "modprobe":
            if state.available:
                values["net.ipv4.tcp_available_congestion_control"] += " bbr"
            return SimpleNamespace(returncode=0 if state.available else 1, stdout="")
        if command[:2] == ["sysctl", "-n"]:
            return SimpleNamespace(returncode=0, stdout=values[command[2]] + "\n")
        if command[:2] == ["sysctl", "-w"]:
            key, value = command[2].split("=", 1)
            if command[2] == state.fail:
                raise subprocess.CalledProcessError(1, command)
            if not (state.ignore_readback and value == "bbr"):
                values[key] = value
        elif command[:2] == ["rm", "-f"]:
            Path(command[2]).unlink(missing_ok=True)
        return SimpleNamespace(returncode=0, stdout="")

    def write(path, content, mode):
        state.writes.append(path)
        assert mode == "0644"
        if state.fail == "persistence" and path == modules:
            path.write_text("partial")
            raise OSError("synthetic persistence failure")
        if state.fail == "silent_write" and path == modules:
            return
        path.write_bytes(content if isinstance(content, bytes) else content.encode())

    monkeypatch.setattr(tuning, "BBR_SYSCTL_FILE", sysctl)
    monkeypatch.setattr(tuning, "BBR_MODULES_FILE", modules)
    monkeypatch.setattr(tuning, "run_as_root", run)
    monkeypatch.setattr(tuning, "write_root_file", write)
    return state


@pytest.mark.parametrize("builtin", [False, True])
def test_verified_persistent_tuning_preserves_queue(host, builtin):
    if builtin:
        host.values["net.ipv4.tcp_available_congestion_control"] += " bbr"
    tuning.enable()
    assert host.values["net.ipv4.tcp_congestion_control"] == "bbr"
    assert host.values["net.ipv4.tcp_mtu_probing"] == "1"
    assert host.values["net.core.default_qdisc"] == "fq_codel"
    assert "tcp_mtu_probing = 1" in host.sysctl.read_text()
    assert "qdisc" not in host.sysctl.read_text()
    assert host.modules.read_text() == "tcp_bbr\n"
    assert any(c[0] == "modprobe" for c in host.commands) is not builtin
    tuning.enable()  # Reapplying is idempotent.


def test_unsupported_kernel_stops_before_changes(host):
    host.available = False
    with pytest.raises(InstallError, match="kernel does not provide BBR"):
        tuning.enable()
    assert not host.writes
    assert not any(c[:2] == ["sysctl", "-w"] for c in host.commands)


@pytest.mark.parametrize("failure", ["net.ipv4.tcp_mtu_probing=1", "persistence", "readback", "silent_write"])
def test_failed_tuning_restores_runtime_and_files(host, failure):
    host.fail = failure
    host.ignore_readback = failure == "readback"
    with pytest.raises(InstallError, match="TCP tuning failed"):
        tuning.enable()
    assert host.values["net.ipv4.tcp_congestion_control"] == "cubic"
    assert host.values["net.ipv4.tcp_mtu_probing"] == "0"
    assert host.sysctl.read_bytes() == b"# previous managed config\r\n# no final newline \xff"
    assert not host.modules.exists()
