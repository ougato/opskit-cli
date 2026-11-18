"""pytest 公共 fixtures"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def symlink_capable(tmp_path):
    """Skip only link-dependent cases when Windows cannot create their input."""
    target = tmp_path / "symlink-capability-target"
    link = tmp_path / "symlink-capability-link"
    target.write_bytes(b"test input only")
    try:
        link.symlink_to(target)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable; link behavior unverified")
        raise
    finally:
        if link.is_symlink():
            link.unlink()
        target.unlink()


@pytest.fixture
def prepared_native_catalog(monkeypatch, tmp_path):
    """State/inspection unit fixtures stop before executing native guest code."""
    from types import SimpleNamespace
    from contextlib import nullcontext
    monkeypatch.setattr("core.native_invocation.prepare_runtime",
                        lambda *args, **kwargs: nullcontext(lambda: None))
    monkeypatch.setattr("core.native_sandbox.get_backend", lambda: SimpleNamespace(probe=lambda: None))
    monkeypatch.setattr("core.native_runtime_catalog.RuntimeCatalog.load", lambda *args, **kwargs: (tmp_path, {}))
    monkeypatch.setattr("core.native_runtime_catalog.RuntimeCatalog.acquire",
                        lambda self, *args, **kwargs: nullcontext(self.load(*args, **kwargs)))


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个测试使用独立临时目录，避免污染真实用户数据"""
    if os.name == "nt":
        from core.secrets import restrict_permissions
        restrict_permissions(tmp_path)
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


@pytest.fixture(scope="session")
def native_lifecycle_tools(tmp_path_factory):
    from tests.native_tool_fixtures import copy_native_tools
    return copy_native_tools(tmp_path_factory)


@pytest.fixture
def prepared_fetch_tools(isolated_data_dir, native_lifecycle_tools, monkeypatch):
    """Real acquisition catalog; SDK preparation has separate invocation coverage."""
    monkeypatch.setattr("core.plugin_bootstrap.ensure_runtime", lambda *args: None)
    from core.native_fetch_catalog import FetchCatalog
    python, _, _ = native_lifecycle_tools
    FetchCatalog().prepare(python)


@pytest.fixture(autouse=True)
def isolated_cli_context():
    """In-process CLI runners must not leak JSON/noninteractive state to other tests."""
    from core import cli
    tokens = [(variable, variable.set(value)) for variable, value in (
        (cli.output_mode, "text"), (cli.non_interactive, False), (cli._result_stream, None))]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


@pytest.fixture
def simulated_windows_launcher(monkeypatch):
    """Cross-platform scheduling units fake OS handles; Windows integration tests remain real."""
    if os.name != "nt":
        from contextlib import nullcontext
        from types import SimpleNamespace
        import subprocess
        monkeypatch.setattr("core.update_windows._inherited_parent_handle", lambda: nullcontext(1234))
        monkeypatch.setattr(subprocess, "STARTUPINFO", SimpleNamespace, raising=False)
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0, raising=False)
