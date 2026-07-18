"""ohmyzsh 默认 Shell 切换逻辑单元测试（非 root 环境不得挂起等待密码）"""
from __future__ import annotations

import subprocess

import pytest

from software.recipes.ohmyzsh import impl


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    monkeypatch.setattr(impl.shutil, "which", lambda cmd: "/usr/bin/zsh")
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("USER", "tester")


def _completed(returncode: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode)


def test_already_zsh_returns_true(monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    assert impl._switch_default_shell() is True


def test_no_zsh_returns_false(monkeypatch):
    monkeypatch.setattr(impl.shutil, "which", lambda cmd: None)
    assert impl._switch_default_shell() is False


def test_chsh_success_detaches_tty(monkeypatch):
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return _completed(0)

    monkeypatch.setattr(impl, "_run", fake_run)
    assert impl._switch_default_shell() is True
    assert calls[0]["detach_tty"] is True


def test_nonroot_without_passwordless_sudo_fails_fast(monkeypatch):
    """chsh 失败且无免密 sudo：立即返回 False，不得调用 run_as_root（会等密码）。"""
    monkeypatch.setattr(impl, "_run", lambda cmd, **kw: _completed(1))
    monkeypatch.setattr(impl, "is_root", lambda: False)
    monkeypatch.setattr(impl, "sudo_passwordless", lambda: False)

    def fail_run_as_root(cmd, **kwargs):
        raise AssertionError("run_as_root must not be called without passwordless sudo")

    monkeypatch.setattr(impl, "run_as_root", fail_run_as_root)
    assert impl._switch_default_shell() is False


def test_root_falls_back_to_run_as_root(monkeypatch):
    monkeypatch.setattr(impl, "_run", lambda cmd, **kw: _completed(1))
    monkeypatch.setattr(impl, "is_root", lambda: True)
    monkeypatch.setattr(impl, "run_as_root", lambda cmd, **kw: _completed(0))
    assert impl._switch_default_shell() is True


def test_passwordless_sudo_falls_back(monkeypatch):
    monkeypatch.setattr(impl, "_run", lambda cmd, **kw: _completed(1))
    monkeypatch.setattr(impl, "is_root", lambda: False)
    monkeypatch.setattr(impl, "sudo_passwordless", lambda: True)
    captured: dict = {}

    def fake_run_as_root(cmd, **kwargs):
        captured.update(kwargs)
        return _completed(0)

    monkeypatch.setattr(impl, "run_as_root", fake_run_as_root)
    assert impl._switch_default_shell() is True
    assert captured["start_new_session"] is True


def test_chsh_timeout_then_no_sudo(monkeypatch):
    def raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(impl, "_run", raise_timeout)
    monkeypatch.setattr(impl, "is_root", lambda: False)
    monkeypatch.setattr(impl, "sudo_passwordless", lambda: False)
    assert impl._switch_default_shell() is False


def test_run_as_root_exception_returns_false(monkeypatch):
    monkeypatch.setattr(impl, "_run", lambda cmd, **kw: _completed(1))
    monkeypatch.setattr(impl, "is_root", lambda: True)

    def boom(cmd, **kwargs):
        raise OSError("sudo missing")

    monkeypatch.setattr(impl, "run_as_root", boom)
    assert impl._switch_default_shell() is False


def test_prime_privilege_skips_when_root(monkeypatch):
    monkeypatch.setattr(impl, "is_root", lambda: True)
    monkeypatch.setattr(impl, "prime_sudo", lambda: pytest.fail("must not prime"))
    impl._prime_privilege_if_needed()


def test_prime_privilege_skips_when_passwordless(monkeypatch):
    monkeypatch.setattr(impl, "is_root", lambda: False)
    monkeypatch.setattr(impl, "command_exists", lambda cmd: True)
    monkeypatch.setattr(impl, "sudo_passwordless", lambda: True)
    monkeypatch.setattr(impl, "prime_sudo", lambda: pytest.fail("must not prime"))
    impl._prime_privilege_if_needed()


def test_prime_privilege_prompts_once(monkeypatch):
    monkeypatch.setattr(impl, "is_root", lambda: False)
    monkeypatch.setattr(impl, "command_exists", lambda cmd: True)
    monkeypatch.setattr(impl, "sudo_passwordless", lambda: False)
    called: list[bool] = []
    monkeypatch.setattr(impl, "prime_sudo", lambda: called.append(True) or True)
    impl._prime_privilege_if_needed()
    assert called == [True]
