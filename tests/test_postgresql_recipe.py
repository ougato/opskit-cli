from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


def test_postgresql_installed_versions_ignore_partial_dirs(monkeypatch, tmp_path):
    from software.recipes.postgresql.recipe import PostgreSQLRecipe

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".opskit" / "postgresql"
    (root / "postgresql17.10").mkdir(parents=True)
    complete = root / "postgresql16.13" / "bin"
    complete.mkdir(parents=True)
    (complete / "psql.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.platform", "win32")

    assert PostgreSQLRecipe().installed_versions() == ["16.13"]


def test_postgresql_windows_extract_is_atomic(monkeypatch, tmp_path):
    from software.base import InstallError
    from software.recipes.postgresql.windows import WindowsDriver

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tarball = tmp_path / "broken.zip"
    with zipfile.ZipFile(tarball, "w") as zf:
        zf.writestr("pgsql/README.txt", "missing psql")

    with pytest.raises(InstallError):
        WindowsDriver().install_tarball("17.10", tarball)

    root = tmp_path / ".opskit" / "postgresql"
    assert not (root / "postgresql17.10").exists()
    assert not (root / "postgresql17.10.installing").exists()
