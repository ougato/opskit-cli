"""Optional smoke: debug_hook_notify against public hook (wrong secret → 401)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEBUG_SCRIPT = REPO / "scripts" / "debug_hook_notify.py"


def test_debug_hook_script_smoke_invalid_secret_exits_nonzero() -> None:
    """Runtime smoke: public endpoint returns 401 for wrong secret; script exits 1."""
    if not DEBUG_SCRIPT.is_file():
        pytest.skip("scripts/debug_hook_notify.py missing")
    log_path = REPO / "debug-ade7f6.log"
    if log_path.is_file():
        log_path.unlink()
    env = {**os.environ, "HOOK_URL": "https://hook.icerror.top", "HOOK_SECRET": "invalid-ci-smoke-not-real"}
    proc = subprocess.run(
        [sys.executable, str(DEBUG_SCRIPT), "--probe-bearer"],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAIL H3" in proc.stdout or "FAIL H3" in proc.stderr
    assert "FAIL H7" in proc.stdout or "FAIL H7" in proc.stderr
    assert log_path.is_file(), "expected NDJSON log"
    raw = log_path.read_text(encoding="utf-8")
    assert "H3_http_error" in raw
    assert "H7_http_error" in raw
    assert '"http_status": 401' in raw
    assert "auth_mode" in raw
