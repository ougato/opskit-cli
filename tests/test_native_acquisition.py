"""Requests must never fall back to unisolated host Git."""
from types import SimpleNamespace
import subprocess

import pytest

from core import native_acquisition
from core.native_sandbox import NativeSandboxError


@pytest.mark.parametrize("source", ["git@example.invalid:team/repo.git", "https://example.invalid/team/repo.git"])
def test_remote_requires_prepared_native_tools_without_host_fallback(tmp_path, monkeypatch, source):
    monkeypatch.setattr(native_acquisition, "get_backend", lambda: SimpleNamespace(probe=lambda: None))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("host Git fallback"))
    from core.native_fetch_catalog import FetchCatalog
    def missing(self):
        raise FileNotFoundError()
    monkeypatch.setattr(FetchCatalog, "load", missing)
    with pytest.raises(NativeSandboxError) as error:
        native_acquisition.populate(source, tmp_path / "payload")
    assert error.value.code == "fetch_tools_missing"
    assert not (tmp_path / "payload").exists()


@pytest.mark.parametrize("source", ["https://user:secret@example.invalid/repo", "http://example.invalid/repo",
                                   "", "bad\nsource", "https://example.invalid/repo?token=secret"])
def test_invalid_or_credential_bearing_sources_are_rejected(source):
    with pytest.raises(ValueError):
        native_acquisition.source_identity(source)


@pytest.mark.parametrize("accepted", [True, False])
def test_private_retry_is_scoped_and_bounded(tmp_path, monkeypatch, accepted):
    from contextlib import contextmanager
    from core import native_git_https
    from core.native_fetch_catalog import FetchCatalog
    monkeypatch.setattr(native_acquisition, "get_backend", lambda: SimpleNamespace(probe=lambda: None))
    monkeypatch.setattr(FetchCatalog, "load", lambda self: None)
    calls, prompts = [], []
    @contextmanager
    def remote(source, **kwargs):
        calls.append((source, kwargs["allow_private"]))
        raise NativeSandboxError("fetch_private_denied")
        yield
    monkeypatch.setattr(native_git_https, "remote_pack", remote)
    def interaction(source, code):
        prompts.append((source, code))
        return accepted
    with pytest.raises(NativeSandboxError):
        native_acquisition.populate("https://example.com/team/repo", tmp_path / "payload",
                                    fetch_interaction=interaction)
    assert len(prompts) == 1
    assert calls == [("https://example.com:443/team/repo", False)] + (
        [("https://example.com:443/team/repo", True)] if accepted else [])
    assert not (tmp_path / "payload").exists()
