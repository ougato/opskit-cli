"""Configuration authority must remain bounded and preserve rejected inputs."""
from copy import deepcopy
import os

import pytest

from core import bounded_yaml, config
from core.constants import DEFAULT_CONFIG


@pytest.mark.parametrize("payload", [
    b"private-marker: [unfinished",
    b"[]",
    b"false",
    b"1: forbidden",
    b"first: &record {enabled: true}\nsecond: *record\n",
    ("key: " + "[" * 65 + "0" + "]" * 65).encode(),
    b"#" + b"x" * (1024 * 1024),
    ("key: [" + "0," * 20001 + "0]").encode(),
], ids=["malformed", "sequence", "boolean", "numeric-key", "alias", "deep", "bytes", "events"])
def test_rejected_config_cannot_be_migrated_or_mutated(tmp_path, payload):
    path = config.get_config_path()
    path.parent.mkdir()
    path.write_bytes(payload)
    for operation in (config.load_config, config.ensure_config,
                      lambda: config.set_config_value({}, "language", "en")):
        with pytest.raises(ValueError) as caught:
            operation()
        assert "private-marker" not in str(caught.value)
        assert path.read_bytes() == payload


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 1024 * 1024 + 1])
def test_invalid_limits_rejected_even_without_config(tmp_path, limit):
    with pytest.raises(ValueError, match="size limit"):
        config.load_config(max_bytes=limit)
    assert not config.get_config_path().exists()


def test_restricted_byte_budget_checked_before_yaml_construction(tmp_path, monkeypatch):
    path = config.get_config_path()
    path.parent.mkdir()
    path.write_bytes(b" " * 65537)
    monkeypatch.setattr(bounded_yaml.yaml, "safe_load", lambda *args: pytest.fail("constructed oversized YAML"))
    with pytest.raises(ValueError, match="size limit"):
        config.load_config(max_bytes=65536)


def test_publication_failure_preserves_file_and_callers_snapshot(tmp_path):
    current = config.ensure_config()
    before = config.get_config_path().read_bytes()
    snapshot = deepcopy(current)
    with pytest.raises(ValueError, match="publication"):
        config.set_config_value(current, "private-marker", "x" * (1024 * 1024))
    assert config.get_config_path().read_bytes() == before
    assert current == snapshot


def test_loaded_configuration_does_not_mutate_defaults_or_other_readers(tmp_path):
    path = config.get_config_path()
    path.parent.mkdir()
    path.write_text("language: en\n", encoding="utf-8")
    before = deepcopy(DEFAULT_CONFIG)
    first = config.load_config()
    first["update"]["enabled"] = not first["update"]["enabled"]
    assert DEFAULT_CONFIG == before
    assert config.load_config()["update"] == before["update"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_nonordinary_configuration_cannot_authorize_or_mutate(tmp_path, kind):
    path = config.get_config_path()
    path.parent.mkdir()
    target = tmp_path / "private.yaml"
    target.write_text("modules: {first: {enabled: true}}\n", encoding="utf-8")
    try:
        if kind == "symlink":
            path.symlink_to(target)
        elif kind == "hardlink":
            os.link(target, path)
        else:
            path.mkdir()
    except OSError as exc:
        pytest.skip(f"link unavailable: {type(exc).__name__}")
    before = target.read_bytes()
    with pytest.raises(ValueError):
        config.ensure_config()
    with pytest.raises(ValueError):
        config.set_config_value({}, "language", "en")
    assert target.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX named pipe")
def test_fifo_configuration_rejected_without_open(tmp_path, monkeypatch):
    path = config.get_config_path()
    path.parent.mkdir()
    os.mkfifo(path)
    monkeypatch.setattr(bounded_yaml.os, "open", lambda *args: pytest.fail("opened configuration FIFO"))
    with pytest.raises(ValueError):
        config.load_config()
