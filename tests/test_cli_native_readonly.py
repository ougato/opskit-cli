"""Verify real startup, including imports that CliRunner fixtures can conceal."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("arguments,code", [
    (["--output", "json", "software", "install", "python"], 2),
    (["--output=json", "monitor", "memory", "--count", "invalid"], 2),
    (["--output", "json", "--version"], 0),
    (["software", "--help"], 0),
    (["--output", "json", "software", "diagnose", "definitely-unknown"], 2),
])
def test_real_query_and_rejected_command_have_no_runtime_writes(tmp_path, arguments, code):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, OPSKIT_DATA_DIR=str(tmp_path), VIRTUAL_ENV=sys.prefix)
    result = subprocess.run([sys.executable, "main.py", *arguments], cwd=root, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    assert result.returncode == code, result.stderr
    if "--help" not in arguments:
        payload = json.loads(result.stdout)
        assert payload["ok"] == (code == 0)
        if "software" in arguments and code != 0:
            assert not payload["error"]["message"].startswith("cli.error.")
    assert not list(tmp_path.iterdir())


def test_deferred_log_file_records_and_redacts_after_first_event(tmp_path):
    import logging
    from core.logger import _DeferredRotatingFileHandler
    from core.secrets import RedactionFilter
    path = tmp_path / "logs" / "test.log"
    handler = _DeferredRotatingFileHandler(path, maxBytes=128, backupCount=1, delay=True, encoding="utf-8")
    handler.addFilter(RedactionFilter())
    try:
        assert not path.parent.exists()
        handler.handle(logging.LogRecord("test", logging.ERROR, "", 0, "token=synthetic-secret", (), None))
        assert path.is_file()
        assert "synthetic-secret" not in path.read_text()
        assert "[redacted]" in path.read_text()
    finally:
        handler.close()
