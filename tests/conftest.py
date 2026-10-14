"""pytest 公共 fixtures"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个测试使用独立临时目录，避免污染真实用户数据"""
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    from software._shared import mutation
    monkeypatch.setattr(mutation, "lock_path", lambda: tmp_path / "locks" / "versioned-software.lock")
    monkeypatch.setattr(mutation, "system_lock_path", lambda: tmp_path / "system-locks" / "versioned-software.lock")
    return tmp_path


@pytest.fixture
def expire_worker_after_marker(monkeypatch):
    """Inject deadline expiry only after the real child enters the target stage.

    Startup still has a real, bounded allowance. Only the parent's supervisor
    clock is replaced; child code, process termination and locks remain real.
    """
    import time
    from types import SimpleNamespace
    from network import worker

    def arm(marker: Path, startup_budget=30):
        observed = []
        def monotonic():
            now = time.monotonic()
            if observed or marker.exists():
                if not observed:
                    observed.append(now)
                return now + startup_budget + 1
            return now
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=monotonic))
        return startup_budget, observed
    return arm
