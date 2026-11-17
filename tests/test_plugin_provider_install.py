"""Provider requests constrain source, candidate and exact user approval."""
from unittest.mock import Mock

import pytest

from core import plugin_provider_install as guided
from core.plugin import PluginManifest
from core.plugin_capabilities import CapabilityDenied
from plugin import commands, menu


@pytest.mark.parametrize("outcome", ["approved", "declined", "incorrect", "undeclared", "ready"])
def test_provider_install_stays_in_host_control(tmp_path, monkeypatch, outcome):
    from core import prompt, plugin_trust

    caller = PluginManifest(name="cocos", version="1.1.0", kind="python", api_version=4, entry="cocos",
                            uses=[{"service": "storage", "provider": "storage_hub", "api_version": 2,
                                   "methods": ["open_menu"]}])
    candidate = PluginManifest(name="unexpected" if outcome == "incorrect" else "storage_hub",
                               root=tmp_path, version="0.3.1", kind="python", api_version=4, entry="storage",
                               provides=["storage"], service_contracts={"storage": {"api_version": 2,
                                                                                     "methods": ["open_menu"]}})
    selected = [candidate] if outcome == "ready" else []
    monkeypatch.setattr(guided.services, "_selected_manifests", lambda *args: selected)
    monkeypatch.setattr(guided, "_interactive", lambda: None)
    monkeypatch.setattr(menu, "_fetch_options", lambda source: {})
    monkeypatch.setattr(commands, "is_enabled", lambda name: True)
    monkeypatch.setattr(plugin_trust, "compute_fingerprint", lambda path: "b" * 64)
    monkeypatch.setattr(plugin_trust, "is_trusted", lambda *args: False)
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    approve = Mock(return_value=outcome == "approved")
    monkeypatch.setattr(prompt, "confirm", approve)
    trust = Mock()
    authorize = Mock()
    monkeypatch.setattr(commands, "grant_trust", trust)
    monkeypatch.setattr(guided.services, "authorize_service", authorize)
    monkeypatch.setattr(guided.services, "resolve_service_access", lambda *args: True)
    calls = []

    def install(source, branch, *, review_candidate, **kwargs):
        assert kwargs.get("alias") is None
        calls.append((source, branch))
        if not review_candidate(candidate, "b" * 64):
            return None, "declined"
        return candidate, ""

    monkeypatch.setattr(commands, "install", install)
    provider = "arbitrary" if outcome == "undeclared" else "storage_hub"
    if outcome in {"undeclared", "incorrect"}:
        with pytest.raises(CapabilityDenied):
            guided.request_install(caller, "a" * 64, provider, checkpoint=lambda: None)
    else:
        available = guided.request_install(caller, "a" * 64, provider, checkpoint=lambda: None)
        assert available == (outcome != "declined")
    if outcome in {"approved", "declined", "incorrect"}:
        assert calls == [("git@git.icerror.top:mydea/opskit/plugin/storage-hub.git", "develop")]
    else:
        assert not calls
    assert approve.call_count == (1 if outcome in {"approved", "declined"} else 0)
    if outcome == "approved":
        trust.assert_called_once_with(candidate, expected_fingerprint="b" * 64)
        authorize.assert_called_once_with("cocos", "storage", "a" * 64, "b" * 64)
        assert approve.call_args.kwargs["require_explicit"] is True
    else:
        trust.assert_not_called()
        authorize.assert_not_called()
