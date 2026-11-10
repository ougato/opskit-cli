"""core/config.py 单元测试"""
from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

import pytest
import yaml
import click

from core.config import (
    ensure_config,
    get_config_path,
    get_data_dir,
    load_config,
    save_config,
    set_config_value,
    StartupConfigError,
    StartupConfigPublicationError,
    validate_startup_config,
)
from core.constants import DEFAULT_CONFIG


def test_get_data_dir_uses_env(tmp_path: Path) -> None:
    assert get_data_dir() == tmp_path


def test_ensure_config_creates_file(tmp_path: Path) -> None:
    cfg = ensure_config()
    path = get_config_path()
    assert path.exists()
    assert cfg["language"] == DEFAULT_CONFIG["language"]


def test_ensure_config_idempotent(tmp_path: Path) -> None:
    ensure_config()
    ensure_config()
    path = get_config_path()
    assert path.exists()


@pytest.mark.parametrize("contents,valid", [
    (b"# external\nlanguage: zh\nuser_marker: keep\n", True),
    (b"language: [invalid\n", False),
])
def test_ensure_config_does_not_replace_file_created_during_publication(
    monkeypatch, contents, valid, tmp_path, request,
) -> None:
    import core.config as config

    if os.name == "nt":
        import core.private_export as private_export
        publish = private_export.publish_private_file
    else:
        import core.config_anonymous as anonymous
        supported = anonymous.publish_if_supported(tmp_path / "publication-probe", b"probe")
        if supported:
            publish = anonymous._link_descriptor
        else:
            request.node.add_marker(pytest.mark.xfail(
                strict=True, raises=AssertionError,
                reason="unsupported POSIX filesystem still uses the legacy initial publisher",
            ))
            publish = config.save_config

    def create_before_publish(*args):
        # This writer does not participate in OpsKit's configuration lock.
        path = get_config_path()
        subprocess.run([
            sys.executable, "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(bytes.fromhex(sys.argv[2]))",
            str(path), contents.hex(),
        ], check=True, timeout=10)
        return publish(*args)

    if os.name == "nt":
        monkeypatch.setattr(private_export, "publish_private_file", create_before_publish)
    elif supported:
        monkeypatch.setattr(anonymous, "_link_descriptor", create_before_publish)
    else:
        monkeypatch.setattr(config, "save_config", create_before_publish)
    try:
        result = ensure_config()
    except ValueError:
        assert not valid
    else:
        assert valid
        assert result.get("user_marker") == "keep"
    assert get_config_path().read_bytes() == contents


@pytest.mark.skipif(os.name == "nt", reason="POSIX compatibility fallback")
def test_unsupported_initial_publisher_preserves_existing_startup(monkeypatch):
    import core.config as config
    import core.config_anonymous as anonymous

    monkeypatch.setattr(anonymous, "publish_if_supported", lambda path, raw: False)
    assert config.ensure_config()["language"] == DEFAULT_CONFIG["language"]
    assert get_config_path().is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX publication errors")
def test_initial_publisher_error_does_not_fall_back(monkeypatch):
    import core.config as config
    import core.config_anonymous as anonymous

    def fail(*args):
        raise PermissionError("publication denied")

    def no_fallback(*args):
        pytest.fail("an operational failure must not select the legacy fallback")

    monkeypatch.setattr(anonymous, "publish_if_supported", fail)
    monkeypatch.setattr(config, "save_config", no_fallback)
    with pytest.raises(PermissionError, match="publication denied"):
        config.ensure_config()
    assert not get_config_path().exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-bound publication")
def test_ensure_config_does_not_treat_temporary_collision_as_existing_config(monkeypatch):
    import core.private_export as private_export

    def fail_before_publication(*args):
        raise FileExistsError("temporary name collision")

    monkeypatch.setattr(private_export, "publish_private_file", fail_before_publication)
    with pytest.raises(FileNotFoundError):
        ensure_config()
    assert not get_config_path().exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-bound publication")
def test_ensure_config_rejects_competing_file_that_disappears(monkeypatch):
    import core.config as config
    import core.private_export as private_export

    def competitor(path, payload):
        path.write_text("language: en\n", encoding="utf-8")
        raise FileExistsError("competing configuration")

    read = config._load_config_path

    def disappear_before_read(path, **kwargs):
        path.unlink()
        return read(path, **kwargs)

    monkeypatch.setattr(private_export, "publish_private_file", competitor)
    monkeypatch.setattr(config, "_load_config_path", disappear_before_read)
    with pytest.raises(ValueError, match="disappeared"):
        ensure_config()
    assert not get_config_path().exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-bound publication")
@pytest.mark.parametrize("language,expected", [
    ("zh", "无法安全创建配置"),
    ("en", "Cannot safely create configuration"),
])
def test_boot_reports_unsafe_configuration_directory(monkeypatch, language, expected):
    import core.private_export as private_export
    from core import cli, i18n
    import main
    from typer.testing import CliRunner

    def fail_publication(*args):
        raise ValueError("private directory requirements failed")

    monkeypatch.setattr(private_export, "publish_private_file", fail_publication)
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    monkeypatch.setattr(i18n, "_core_keys", set())
    monkeypatch.setattr(cli, "require_interaction", lambda: None)
    result = CliRunner().invoke(main.app, ["--lang", language])
    assert result.exit_code != 0
    assert expected in result.output
    assert "private directory requirements failed" not in result.output
    assert not get_config_path().exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory DACL")
def test_ensure_config_refuses_shared_writable_directory():
    path = get_config_path()
    path.parent.mkdir(parents=True)
    subprocess.run(["icacls", str(path.parent), "/grant", "*S-1-1-0:M"],
                   check=True, capture_output=True, timeout=10)
    with pytest.raises(StartupConfigPublicationError):
        ensure_config()
    assert not path.exists()
    assert not list(path.parent.iterdir())


def test_save_and_load_config(tmp_path: Path) -> None:
    cfg = {"language": "zh", "theme": "catppuccin"}
    get_config_path().parent.mkdir(parents=True, exist_ok=True)
    save_config(cfg)
    loaded = load_config()
    assert loaded["language"] == "zh"


def test_set_config_value_nested(tmp_path: Path) -> None:
    cfg = ensure_config()
    cfg = set_config_value(cfg, "update.enabled", False)
    assert cfg["update"]["enabled"] is False
    reloaded = load_config()
    assert reloaded["update"]["enabled"] is False


def test_set_config_value_top_level(tmp_path: Path) -> None:
    cfg = ensure_config()
    cfg = set_config_value(cfg, "theme", "nord")
    assert cfg["theme"] == "nord"


def test_load_config_merges_defaults(tmp_path: Path) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump({"language": "zh"}, f)
    cfg = load_config()
    assert cfg["language"] == "zh"
    assert "update" in cfg


def test_ensure_existing_config_keeps_bytes_and_unknown_fields(tmp_path: Path) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"# user comment\nlanguage: zh\ncustom_option: preserved\n"
    path.write_bytes(original)
    config = ensure_config()
    assert config["custom_option"] == "preserved"
    assert config["update"] == DEFAULT_CONFIG["update"]
    assert path.read_bytes() == original


def test_ensure_invalid_config_does_not_overwrite_original(tmp_path: Path) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"language: [invalid\n"
    path.write_bytes(original)
    with pytest.raises(ValueError):
        ensure_config()
    assert path.read_bytes() == original


@pytest.mark.parametrize("bad_config,field", [
    ({"log": []}, "log"),
    ({"log": {"level": False}}, "log.level"),
    ({"modules": {"software": []}}, "modules"),
    ({"modules": {"software": {"enabled": "false"}}}, "modules.*.enabled"),
    ({"update": {"enabled": "false"}}, "update.enabled"),
    ({"telemetry": {"enabled": "false"}}, "telemetry.enabled"),
])
def test_startup_rejects_invalid_field_types_without_disclosing_values(bad_config, field):
    with pytest.raises(StartupConfigError) as error:
        validate_startup_config(bad_config)
    assert error.value.field == field
    assert "false" not in str(error.value)


@pytest.mark.parametrize("language,expected", [
    ("zh", "配置字段 log 无效"),
    ("en", "Invalid configuration field log"),
])
def test_boot_reports_invalid_config_and_preserves_file(tmp_path: Path, monkeypatch, language, expected):
    import main
    from core import i18n
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    monkeypatch.setattr(i18n, "_core_keys", set())
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = f"language: {language}\nlog: []\n".encode()
    path.write_bytes(original)
    with pytest.raises(click.ClickException) as error:
        main._boot()
    assert expected in error.value.message
    assert path.read_bytes() == original


@pytest.mark.parametrize("language,contents,expected", [
    ("zh", b"log: []\nlanguage: en\n", "配置字段 log 无效"),
    ("en", b"log: []\nlanguage: zh\n", "Invalid configuration field log"),
    ("zh", b"log: [broken\n", "配置无效、无法读取或超过大小限制"),
    ("en", b"log: [broken\n", "Configuration is invalid, unreadable"),
])
def test_cli_config_error_honors_language_override_and_preserves_file(
    tmp_path: Path, monkeypatch, language, contents, expected,
):
    import main
    from core import cli, i18n
    from typer.testing import CliRunner

    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    monkeypatch.setattr(i18n, "_core_keys", set())
    monkeypatch.setattr(cli, "require_interaction", lambda: None)
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    result = CliRunner().invoke(main.app, ["--lang", language])
    assert result.exit_code != 0
    assert expected in result.output
    assert path.read_bytes() == contents
