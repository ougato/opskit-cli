"""Theme names and inheritance stay inside the selected resource directory."""
import os
import sys

import pytest

from core import config, theme


@pytest.fixture
def theme_dir(tmp_path, monkeypatch):
    root = tmp_path / "themes"
    root.mkdir()
    (root / "catppuccin.yaml").write_text("colors: {success: default}\n", encoding="utf-8")
    monkeypatch.setattr(theme, "_get_themes_dir", lambda: root)
    monkeypatch.setattr(theme, "_theme", {})
    monkeypatch.setattr(theme, "_theme_name", "catppuccin")
    return root


def test_custom_name_and_parent_use_resource_directory(theme_dir):
    (theme_dir / "深色-theme.yaml").write_text("meta: {extends: catppuccin}\nicons: {back: X}\n", encoding="utf-8")
    theme.init("深色-theme")
    assert theme.current_theme() == "深色-theme"
    assert theme.get_color("success") == "default"
    assert theme.get_icon("back") == "X"
    assert "深色-theme" in theme.list_themes()


@pytest.mark.parametrize("name", ["../outside", "..\\outside", "/outside", "C:\\outside", "foo:bar", ".."])
def test_path_like_theme_name_falls_back(theme_dir, name):
    theme.init(name)
    assert theme.current_theme() == "catppuccin"
    assert theme.get_color("success") == "default"


def test_absolute_path_and_parent_escape_are_not_read(theme_dir, tmp_path):
    secret = tmp_path / "secret.yaml"
    secret.write_text("colors: {success: outside}\n", encoding="utf-8")
    theme.init(str(secret.with_suffix("")))
    assert theme.get_color("success") == "default"
    (theme_dir / "child.yaml").write_text("meta: {extends: ../secret}\n", encoding="utf-8")
    theme.init("child")
    assert theme.get_color("success") == "white"
    assert secret.read_text(encoding="utf-8") == "colors: {success: outside}\n"


def test_external_symbolic_link_is_not_listed_or_loaded(theme_dir, tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("colors: {success: outside}\n", encoding="utf-8")
    link = theme_dir / "linked.yaml"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic link creation is not available")
    assert "linked" not in theme.list_themes()
    theme.init("linked")
    assert theme.current_theme() == "catppuccin"
    assert theme.get_color("success") == "default"
    assert outside.is_file() and link.is_symlink()


def test_frozen_resource_path_is_honored(tmp_path, monkeypatch):
    theme_root = tmp_path / "core" / "themes"
    theme_root.mkdir(parents=True)
    (theme_root / "catppuccin.yaml").write_text("colors: {success: frozen}\n", encoding="utf-8")
    monkeypatch.setattr(config, "_is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", os.fspath(tmp_path), raising=False)
    monkeypatch.setattr(theme, "_theme", {})
    monkeypatch.setattr(theme, "_theme_name", "catppuccin")
    theme.init("catppuccin")
    assert theme.get_color("success") == "frozen"


def test_guest_default_does_not_require_normalized_path_queries(monkeypatch):
    from pathlib import Path
    def denied(*args, **kwargs):
        raise PermissionError("AppContainer normalized path query denied")
    monkeypatch.setattr(Path, "resolve", denied)
    monkeypatch.setattr(theme, "_theme", {})
    monkeypatch.setattr(theme, "_theme_name", "other")
    theme.init_bundled_default()
    assert theme.current_theme() == "catppuccin"
    assert theme.get_icon("back") != "•"
