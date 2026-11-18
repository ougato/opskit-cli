"""Trust mutations cannot write an old snapshot over a completed revocation."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from core import plugin_trust as trust
from core.resource_lock import ResourceBusyError


@pytest.mark.parametrize("operation", ["grant", "revoke", "invalidate"])
def test_trust_mutation_rejects_real_process_competition(tmp_path, monkeypatch, operation):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    trust.grant("first", "a" * 64, "1", "https://example.invalid/first", "develop")
    script = '''
import os
from pathlib import Path
import time
from core import plugin_trust as trust
root = Path(os.environ["OPSKIT_DATA_DIR"])
original = trust._load
def pause():
    records = original()
    (root / "ready").write_text("ready")
    until = time.monotonic() + 15
    while not (root / "continue").exists():
        if time.monotonic() >= until:
            raise TimeoutError("parent did not release writer")
        time.sleep(0.01)
    return records
trust._load = pause
trust.grant("second", "b" * 64, "1")
'''
    process = subprocess.Popen([sys.executable, "-c", script],
                               cwd=Path(__file__).resolve().parents[1],
                               env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    mutate = {"grant": lambda: trust.grant("third", "c" * 64, "1"),
              "revoke": lambda: trust.revoke("first"),
              "invalidate": lambda: trust.invalidate("first")}[operation]
    try:
        until = time.monotonic() + 15
        while not (tmp_path / "ready").exists():
            if process.poll() is not None or time.monotonic() >= until:
                pytest.fail("writer did not reach its read barrier")
            time.sleep(0.01)
        with pytest.raises(ResourceBusyError):
            mutate()
        assert trust.is_trusted("first", "a" * 64)
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
    assert trust.is_trusted("second", "b" * 64)
    if operation == "grant":
        assert trust.is_trusted("third", "c" * 64)
    else:
        assert not trust.is_trusted("first", "a" * 64)
        if operation == "invalidate":
            record = trust.trusted_record("first")
            assert record["branch"] == "develop"
            assert record["source"] == "https://example.invalid/first"
