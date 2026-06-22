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


def test_start_login_returns_timeout_output(monkeypatch) -> None:
    from tailscale import server

    def fake_run_root(command, check=True, timeout=0):
        raise subprocess.TimeoutExpired(command, timeout, output="auth url", stderr="")

    monkeypatch.setattr(server, "_run_root", fake_run_root)
    assert server.start_login() == "auth url"
