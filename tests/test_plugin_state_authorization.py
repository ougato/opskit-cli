"""State publication must not cross a completed trust revocation."""
import base64
import io
import tarfile

import pytest

from core import archive, plugin_sandbox as sandbox, plugin_trust as trust


def payload():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as package:
        member = tarfile.TarInfo("value")
        member.size = 3
        package.addfile(member, io.BytesIO(b"new"))
    return base64.b64encode(output.getvalue()).decode()


@pytest.fixture
def authorized_state(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("core.config.load_config", lambda: {})
    trust.grant("owner", "a" * 64, "1")
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("owner", "a" * 64),), raising=False)
    root = tmp_path / "state"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    return root


def revoking_preparation_worker(connection, *args):
    from core.plugin_state_prepare import _prepare_worker
    original = archive.extract_tar
    def extract(*args, **kwargs):
        original(*args, **kwargs)
        trust.revoke("owner")
    archive.extract_tar = extract
    _prepare_worker(connection, *args)


def test_revocation_during_state_extraction_preserves_old_state(authorized_state, monkeypatch):
    monkeypatch.setattr("core.plugin_state_prepare._prepare_worker", revoking_preparation_worker)
    with pytest.raises(sandbox.SandboxError, match="authorization revoked"):
        sandbox._save_state(payload(), authorized_state, plugin="owner", caller="platform")
    assert (authorized_state / "value").read_bytes() == b"old"
    assert not authorized_state.with_name("state.previous").exists()


def test_publication_holds_trust_lock_against_another_thread(authorized_state, monkeypatch):
    import json
    from core import plugin_state_publish
    from state_publication_fixture import publication_worker
    marker = authorized_state.parent / "trust-probe"
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MODE", "trust_probe")
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MARKER", str(marker))
    monkeypatch.setattr(plugin_state_publish, "_publish_worker", publication_worker)
    sandbox._save_state(payload(), authorized_state, plugin="owner", caller="platform")
    attempts = [item for line in marker.read_text().splitlines() for item in json.loads(line)]
    assert attempts and set(attempts) == {"busy"}
    assert (authorized_state / "value").read_bytes() == b"new"
    trust.revoke("owner")
    assert not trust.is_trusted("owner", "a" * 64)


def test_another_process_authorization_transaction_denies_state_commit(authorized_state):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    directory = Path(os.environ["OPSKIT_DATA_DIR"])
    script = '''
import os
from pathlib import Path
import time
from core.plugin_trust import authorization_transaction
root = Path(os.environ["OPSKIT_DATA_DIR"])
with authorization_transaction():
    (root / "ready").write_text("ready")
    until = time.monotonic() + 15
    while not (root / "continue").exists():
        if time.monotonic() >= until:
            raise TimeoutError("parent did not release transaction")
        time.sleep(0.01)
'''
    process = subprocess.Popen([sys.executable, "-c", script],
                               cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        until = time.monotonic() + 15
        while not (directory / "ready").exists():
            if process.poll() is not None or time.monotonic() >= until:
                pytest.fail("child did not acquire authorization transaction")
            time.sleep(0.01)
        with pytest.raises(sandbox.SandboxError, match="state not committed"):
            sandbox._save_state(payload(), authorized_state, plugin="owner", caller="platform")
        assert (authorized_state / "value").read_bytes() == b"old"
        assert not list(authorized_state.parent.glob(".state-*"))
    finally:
        (directory / "continue").write_text("continue")
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise
    assert process.returncode == 0, stderr
    sandbox._save_state(payload(), authorized_state, plugin="owner", caller="platform")
    assert (authorized_state / "value").read_bytes() == b"new"
