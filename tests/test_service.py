from __future__ import annotations

from types import SimpleNamespace


def test_systemd_unavailable_when_runtime_dir_missing(tmp_path, monkeypatch) -> None:
    from core import service

    monkeypatch.setattr(service, "SYSTEMD_RUNTIME_DIR", tmp_path / "missing")
    monkeypatch.setattr(service.shutil, "which", lambda command: f"/usr/bin/{command}")

    assert service.systemd_is_available() is False


def test_systemd_available_for_degraded_state(tmp_path, monkeypatch) -> None:
    from core import service

    runtime_dir = tmp_path / "systemd"
    runtime_dir.mkdir()
    monkeypatch.setattr(service, "SYSTEMD_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(service.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="degraded\n"),
    )

    assert service.systemd_is_available() is True


def test_enable_now_falls_back_to_sysv_service(tmp_path, monkeypatch) -> None:
    from core import service
    from core import privilege

    calls: list[list[str]] = []
    monkeypatch.setattr(service, "SYSTEMD_RUNTIME_DIR", tmp_path / "missing")
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda command: f"/usr/sbin/{command}" if command in {service.SERVICE_COMMAND, service.UPDATE_RC_COMMAND} else None,
    )
    monkeypatch.setattr(privilege, "run_as_root", lambda command, **kwargs: calls.append(command))

    service.enable_now("nginx")

    assert calls == [
        [service.SERVICE_COMMAND, "nginx", "start"],
        [service.UPDATE_RC_COMMAND, "nginx", "defaults"],
    ]


def test_disable_now_falls_back_to_sysv_service(tmp_path, monkeypatch) -> None:
    from core import service
    from core import privilege

    calls: list[list[str]] = []
    monkeypatch.setattr(service, "SYSTEMD_RUNTIME_DIR", tmp_path / "missing")
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda command: f"/usr/sbin/{command}" if command in {service.SERVICE_COMMAND, service.UPDATE_RC_COMMAND} else None,
    )
    monkeypatch.setattr(privilege, "run_as_root", lambda command, **kwargs: calls.append(command))

    service.disable_now("nginx")

    assert calls == [
        [service.SERVICE_COMMAND, "nginx", "stop"],
        [service.UPDATE_RC_COMMAND, "-f", "nginx", "remove"],
    ]
