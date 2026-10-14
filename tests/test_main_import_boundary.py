"""Importing the packaged CLI must not bootstrap or replace its interpreter."""
import os
import subprocess
import sys
from pathlib import Path

from core.process import capture


def test_import_without_virtual_env_never_bootstraps(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, OPSKIT_DATA_DIR=str(tmp_path / "data"), PYTHONDONTWRITEBYTECODE="1")
    env.pop("VIRTUAL_ENV", None)
    program = "\n".join([
        "from core import venv_bootstrap",
        "def forbidden(*args, **kwargs): raise RuntimeError('import attempted environment bootstrap')",
        "venv_bootstrap.ensure_venv = forbidden",
        "import main",
        "assert callable(main.app)",
        "print('CLI_IMPORT_OK')",
    ])
    result = capture([sys.executable, "-c", program], cwd=root, env=env,
                     timeout=30, max_bytes=4096, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "CLI_IMPORT_OK"
    assert not (tmp_path / "data").exists()


def test_active_external_venv_does_not_reenter_project_environment(tmp_path, monkeypatch):
    from core import venv_bootstrap
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "external-environment"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "system-python"))
    def forbidden(*args, **kwargs):
        raise AssertionError("active external venv attempted initialization or exec")
    monkeypatch.setattr(venv_bootstrap, "_exec_reenter", forbidden)
    monkeypatch.setattr(venv_bootstrap, "_create_venv", forbidden)
    monkeypatch.setattr(venv_bootstrap, "_find_python", forbidden)
    venv_bootstrap.ensure_venv(tmp_path / "project")
    assert not (tmp_path / "project").exists()
