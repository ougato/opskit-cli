"""Unrelated stale configuration edits must not restore disabled plugins."""
import pytest

from core.config import load_config, set_config_value
from plugin.commands import is_enabled, set_enabled


@pytest.mark.parametrize("setting,value", [("language", "en"), ("theme", "catppuccin")])
def test_stale_unrelated_config_edit_preserves_completed_plugin_disable(tmp_path, monkeypatch, setting, value):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    stale = load_config()
    set_enabled("owner", False)
    assert not is_enabled("owner")
    set_config_value(stale, setting, value)
    assert not is_enabled("owner")
    assert load_config()[setting] == value


def test_stale_plugin_toggle_preserves_another_plugin_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    stale = load_config()
    set_enabled("first", False)
    set_config_value(stale, "modules.second.enabled", False)
    assert not is_enabled("first")
    assert not is_enabled("second")


def test_disable_cannot_complete_during_state_publication(tmp_path, monkeypatch):
    import base64
    import io
    import tarfile
    from core import plugin_sandbox as sandbox, plugin_trust as trust
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    trust.grant("owner", "a" * 64, "1")
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("owner", "a" * 64),), raising=False)
    root = tmp_path / "state"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as package:
        member = tarfile.TarInfo("value")
        member.size = 3
        package.addfile(member, io.BytesIO(b"new"))
    import json
    from core import plugin_state_publish
    from state_publication_fixture import publication_worker
    marker = tmp_path / "config-probe"
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MODE", "config_probe")
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MARKER", str(marker))
    monkeypatch.setattr(plugin_state_publish, "_publish_worker", publication_worker)
    sandbox._save_state(base64.b64encode(buffer.getvalue()).decode(), root, plugin="owner", caller="platform")
    outcomes = [item for line in marker.read_text().splitlines() for item in json.loads(line)]
    assert outcomes and set(outcomes) == {"busy"}
    assert is_enabled("owner")
    set_enabled("owner", False)
    assert not is_enabled("owner")


@pytest.mark.parametrize("operation", ["save", "ensure", "set"])
def test_configuration_writes_reject_real_process_competition(tmp_path, monkeypatch, operation):
    from pathlib import Path
    import subprocess
    import sys
    import time
    from core.config import save_config, ensure_config
    from core.resource_lock import ResourceBusyError
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    script = '''
import os
from pathlib import Path
import time
from core.config import configuration_transaction
root = Path(os.environ["OPSKIT_DATA_DIR"])
with configuration_transaction():
    (root / "ready").write_text("ready")
    until = time.monotonic() + 15
    while not (root / "continue").exists():
        if time.monotonic() >= until:
            raise TimeoutError("parent did not release configuration lock")
        time.sleep(0.01)
'''
    process = subprocess.Popen([sys.executable, "-c", script],
                               cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    mutate = {"save": lambda: save_config({"language": "en"}),
              "ensure": ensure_config,
              "set": lambda: set_config_value({}, "language", "en")}[operation]
    try:
        until = time.monotonic() + 15
        while not (tmp_path / "ready").exists():
            if process.poll() is not None or time.monotonic() >= until:
                pytest.fail("child did not acquire configuration lock")
            time.sleep(0.01)
        with pytest.raises(ResourceBusyError):
            mutate()
    finally:
        (tmp_path / "continue").write_text("continue")
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise
    assert process.returncode == 0, stderr
    mutate()
