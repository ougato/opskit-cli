from __future__ import annotations

import subprocess

from software.registry import all_recipes, get


def test_tailscale_recipe_is_registered() -> None:
    keys = {cls.key for cls in all_recipes()}
    assert "tailscale" in keys
    recipe_cls = get("tailscale")
    assert recipe_cls is not None
    recipe = recipe_cls()
    assert recipe.category == "devops"
    assert recipe.has_diagnose is True
    assert recipe.has_manage is True


def test_tailscale_steps() -> None:
    recipe_cls = get("tailscale")
    assert recipe_cls is not None
    install_steps = [step.description_key for step in recipe_cls().steps()]
    uninstall_steps = [step.description_key for step in recipe_cls().steps("uninstall")]
    assert install_steps == [
        "tailscale.step.check_os",
        "tailscale.step.install",
        "tailscale.step.start",
        "tailscale.step.exit_node",
        "tailscale.step.login",
    ]
    assert uninstall_steps == [
        "software.step.stop_service",
        "software.step.remove_files",
        "software.step.cleanup",
    ]


def test_remove_tailscale_artifacts(tmp_path, monkeypatch) -> None:
    from tailscale import server

    state_dir = tmp_path / "state"
    run_dir = tmp_path / "run"
    repo = tmp_path / "tailscale.list"
    keyring = tmp_path / "tailscale.gpg"
    state_dir.mkdir()
    run_dir.mkdir()
    repo.write_text("repo", encoding="utf-8")
    keyring.write_text("key", encoding="utf-8")

    monkeypatch.setattr(server, "TAILSCALE_STATE_DIR", state_dir)
    monkeypatch.setattr(server, "TAILSCALE_RUN_DIR", run_dir)
    monkeypatch.setattr(server, "TAILSCALE_REPO_FILE", repo)
    monkeypatch.setattr(server, "TAILSCALE_KEYRING_FILE", keyring)

    server.remove_tailscale_artifacts()

    assert not state_dir.exists()
    assert not run_dir.exists()
    assert not repo.exists()
    assert not keyring.exists()


def test_configure_exit_node_writes_persistent_rules(tmp_path, monkeypatch) -> None:
    from tailscale import server

    sysctl_file = tmp_path / "sysctl.conf"
    script_file = tmp_path / "tailscale-exit-node-nat"
    service_file = tmp_path / "tailscale-exit-node-nat.service"
    writes: dict[str, str] = {}
    calls: list[list[str]] = []

    def fake_write_root_file(path, content, mode):
        writes[str(path)] = content
        path.write_text(content, encoding="utf-8")

    def fake_run_root(command, check=True, timeout=0):
        calls.append(command)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(server, "TAILSCALE_EXIT_NODE_SYSCTL_FILE", sysctl_file)
    monkeypatch.setattr(server, "TAILSCALE_EXIT_NODE_SCRIPT_FILE", script_file)
    monkeypatch.setattr(server, "TAILSCALE_EXIT_NODE_SERVICE_FILE", service_file)
    monkeypatch.setattr(server, "_write_root_file", fake_write_root_file)
    monkeypatch.setattr(server, "_run_root", fake_run_root)

    server.configure_exit_node()

    assert "net.ipv4.ip_forward = 1" in writes[str(sysctl_file)]
    assert "MASQUERADE" in writes[str(script_file)]
    assert "TCPMSS --clamp-mss-to-pmtu" in writes[str(script_file)]
    assert "ip6tables -I FORWARD 1 -i tailscale0 -j REJECT" in writes[str(script_file)]
    assert "RemainAfterExit=yes" in writes[str(service_file)]
    assert [server.SYSTEMCTL_COMMAND, "enable", "--now", server.TAILSCALE_EXIT_NODE_SERVICE] in calls


def test_cleanup_exit_node_removes_persistent_rules(tmp_path, monkeypatch) -> None:
    from tailscale import server

    script_file = tmp_path / "tailscale-exit-node-nat"
    service_file = tmp_path / "tailscale-exit-node-nat.service"
    sysctl_file = tmp_path / "sysctl.conf"
    script_file.write_text("#!/bin/sh", encoding="utf-8")
    service_file.write_text("service", encoding="utf-8")
    sysctl_file.write_text("sysctl", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_root(command, check=True, timeout=0):
        calls.append(command)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(server, "TAILSCALE_EXIT_NODE_SCRIPT_FILE", script_file)
    monkeypatch.setattr(server, "TAILSCALE_EXIT_NODE_SERVICE_FILE", service_file)
    monkeypatch.setattr(server, "TAILSCALE_EXIT_NODE_SYSCTL_FILE", sysctl_file)
    monkeypatch.setattr(server, "_run_root", fake_run_root)

    server.cleanup_exit_node()

    assert [str(script_file), "clean"] in calls
    assert [server.SYSTEMCTL_COMMAND, "disable", "--now", server.TAILSCALE_EXIT_NODE_SERVICE] in calls
    assert [server.RM_COMMAND, "-f", str(script_file)] in calls
    assert [server.RM_COMMAND, "-f", str(service_file)] in calls
    assert [server.RM_COMMAND, "-f", str(sysctl_file)] in calls


def test_extract_auth_url_filters_noise() -> None:
    from tailscale import server

    output = (
        "Warning: UDP GRO forwarding is suboptimally configured on eth0\n"
        "See https://tailscale.com/s/ethtool-config-udp-gro\n\n"
        "To authenticate, visit:\n\n"
        "        https://login.tailscale.com/a/1dde563401e43d\n"
    )
    assert server._extract_auth_url(output) == "https://login.tailscale.com/a/1dde563401e43d"
    assert server._extract_auth_url("") == ""
    assert server._extract_auth_url("already logged in") == ""


def test_install_script_raises_install_error_with_stderr_tail(monkeypatch) -> None:
    from software.base import InstallError
    from tailscale import server

    class FakeResp:
        def read(self):
            return b"#!/bin/bash\ntrue\n"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *a, **k: FakeResp())

    class Result:
        returncode = 100
        stdout = ""
        stderr = "E: Could not get lock /var/lib/dpkg/lock-frontend"

    monkeypatch.setattr("core.privilege.run_as_root", lambda *a, **k: Result())

    try:
        server._install_script()
        assert False, "expected InstallError"
    except InstallError as exc:
        assert "Could not get lock" in str(exc)


def test_start_login_returns_timeout_output(monkeypatch) -> None:
    from tailscale import server

    def fake_run_root(command, check=True, timeout=0):
        raise subprocess.TimeoutExpired(command, timeout, output="auth url", stderr="")

    monkeypatch.setattr(server, "_run_root", fake_run_root)
    assert server.start_login() == "auth url"
