import pytest

from core import plugin_tool_config as tools


def test_named_creator_configuration_preserves_other_settings_and_grants_nothing(tmp_path):
    from core.config import set_config_value, load_config
    set_config_value({}, "unrelated.setting", 42)
    executable = tmp_path / "CocosCreator.exe"
    executable.write_bytes(b"reviewed test input, never executed")
    result = tools.configure_creator("creator-3-8", str(executable))
    assert result["authorized"] is False
    assert tools.resolve_creator("@creator/creator-3-8") == str(executable)
    assert load_config()["unrelated"]["setting"] == 42
    tools.remove_creator("creator-3-8")
    assert tools.creators() == {} and executable.exists()
    with pytest.raises(ValueError, match="missing"):
        tools.resolve_creator("@creator/creator-3-8")


@pytest.mark.parametrize("name", ["../evil", "a.b", "UPPER", "", "a" * 65])
def test_invalid_name_never_changes_configuration(tmp_path, name):
    executable = tmp_path / "Creator.exe"
    executable.write_bytes(b"test")
    with pytest.raises(ValueError):
        tools.configure_creator(name, str(executable))
    assert tools.creators() == {}


def test_missing_and_wrong_executable_rejected_without_writing(tmp_path):
    executable = tmp_path / "unrelated.exe"
    executable.write_bytes(b"test")
    with pytest.raises(ValueError):
        tools.configure_creator("creator", str(executable))
    with pytest.raises(OSError):
        tools.configure_creator("creator", str(tmp_path / "Creator.exe"))
    assert tools.creators() == {}
