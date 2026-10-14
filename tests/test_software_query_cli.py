import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from core.cli import configure
from core.cli_secrets import read_secret_file

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    yield
    configure("text", False)


def invoke(*args):
    import main
    return runner.invoke(main.app, ["--output", "json", "software", *args])


def test_all_official_capabilities_without_install():
    from software.registry import all_recipes
    for cls in all_recipes():
        result = invoke("capabilities", cls.key)
        assert result.exit_code == 0, result.output
        item = json.loads(result.stdout)["data"]
        assert item["name"] == cls.key and item["platforms"] == cls.platforms
        if cls.has_wizard and cls.key not in {"wg_client", "wg_server", "xui", "ohmyzsh", "tailscale"}:
            assert not item["scriptable_install"]


def test_cross_platform_inventory_does_not_detect(monkeypatch):
    from software.registry import all_recipes
    from software import query
    from types import SimpleNamespace
    monkeypatch.setattr(query, "get_platform", lambda: SimpleNamespace(os_type="windows"))
    def forbidden(self):
        raise AssertionError("foreign platform detector must not run")
    for cls in all_recipes():
        monkeypatch.setattr(cls, "detect", forbidden)
    result = invoke("list", "--platform", "linux")
    assert result.exit_code == 0
    items = json.loads(result.stdout)["data"]
    assert items and all(item["installed"] is None and item["detection_error"] is None for item in items)


def test_unknown_capability_json_error():
    result = invoke("capabilities", "no-such-software")
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_search_requires_query_in_json():
    result = invoke("search")
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_token_stdin_and_conflicts(monkeypatch):
    import main
    captured = []
    monkeypatch.setattr(main, "_boot", lambda: {})
    monkeypatch.setattr(main, "_sw_action_by_name", lambda *args, **kwargs: captured.append(kwargs))
    result = runner.invoke(main.app, ["software", "install", "wg_client", "--token-file", "-"], input="synthetic-token\n")
    assert result.exit_code == 0
    assert captured[0]["token"] == "synthetic-token"
    rejected = invoke("install", "wg_client", "--token", "secret", "--token-file", "-")
    assert rejected.exit_code == 2 and "secret" not in rejected.output


def test_secret_size_and_empty_rejected(tmp_path):
    path = tmp_path / "secret"
    path.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError):
        read_secret_file(str(path))
    path.write_text("  \n")
    with pytest.raises(ValueError):
        read_secret_file(str(path))


def test_preference_queries_do_not_write_config(tmp_path):
    import main
    from core.config import get_config_path
    for command in (["preferences", "show"], ["theme", "list"], ["language", "list"]):
        result = runner.invoke(main.app, ["--output", "json", *command])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["ok"]
    assert not get_config_path().exists()


def test_preference_set_persists_and_rejects_unknown():
    import main
    from core.config import load_config
    result = runner.invoke(main.app, ["--output", "json", "language", "set", "en"])
    assert result.exit_code == 0 and load_config()["language"] == "en"
    rejected = runner.invoke(main.app, ["--output", "json", "language", "set", "../bad"])
    assert rejected.exit_code == 2 and load_config()["language"] == "en"
