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
    return tmp_path
