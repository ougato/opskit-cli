"""Only generic headroom belongs in the framework policy."""
from types import SimpleNamespace
import pytest
from core import plugin_disk_policy as disk
from core.config import set_config_value, load_config


def test_legacy_business_values_are_preserved_but_ignored():
    set_config_value({},"plugin.disk", {"minimum_free_mib":256,"creator_output_directory":123,"keep_builds":"broken"})
    set_config_value({},"other.value",42)
    assert disk.policy()=={"minimum_free_mib":256}
    disk.configure(512)
    value=load_config()
    assert value["plugin"]["disk"]["creator_output_directory"]==123
    assert value["plugin"]["disk"]["keep_builds"]=="broken"
    assert value["other"]["value"]==42


@pytest.mark.parametrize("value", [True,255,1048577,"2048",None])
def test_invalid_headroom(value):
    with pytest.raises(ValueError): disk.configure(value)


def test_destination_space_checked(tmp_path,monkeypatch):
    disk.configure(256)
    monkeypatch.setattr(disk.shutil,"disk_usage",lambda _:SimpleNamespace(free=1))
    with pytest.raises(OSError): disk.check_space(tmp_path)


def test_business_cli_commands_and_options_are_removed():
    import typer
    from typer.testing import CliRunner
    from plugin.lifecycle_cli import register
    app=typer.Typer();register(app);runner=CliRunner()
    assert runner.invoke(app,["disk-policy","--minimum-free-mib","256"]).exit_code==0
    for args in [["disk-policy","--keep-builds","5"],["disk-policy","--output-directory","x"],
                 ["creator-tools"],["configure-creator"],["remove-creator"],["build-history"],["clean-build"]]:
        assert runner.invoke(app,args).exit_code!=0
