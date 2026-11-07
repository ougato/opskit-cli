import pytest
import json

from core.native_sandbox import CapabilityRequest, GrantChoice
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied


def request(**overrides):
    args = dict(plugin="game_release", fingerprint="a" * 64,
                operation="tool.execute", resource="creator.build.project-one")
    return CapabilityRequest(**(args | overrides))


def live(*args):
    return True


@pytest.mark.parametrize("language", ["zh", "en"])
def test_tool_scope_consent_shows_path_and_broad_permission(monkeypatch, language):
    from core import i18n
    from core.plugin_capabilities import capability_info_lines
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    resource = json.dumps({"authorization_scope": "tool", "executable": r"D:\git.exe", "sha256": "a" * 64})
    lines = capability_info_lines(request(resource=resource))
    assert r"D:\git.exe" in lines[1]
    assert "sha256" not in lines[1]
    assert lines[2] == i18n.t("plugin_native.tool_scope_warning")


def test_once_is_reused_only_by_same_invocation(tmp_path):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    prompts = []
    def prompt(value):
        prompts.append(value)
        return GrantChoice.ONCE
    authority.require(request(), check_identity=live, prompt=prompt)
    authority.require(request(), check_identity=live, prompt=prompt)
    assert len(prompts) == 1
    assert authority.list_grants() == []
    with pytest.raises(CapabilityDenied):
        CapabilityAuthority(path=authority.path).check(request(), check_identity=live)
    authority.close()
    with pytest.raises(CapabilityDenied):
        authority.check(request(), check_identity=live)


def test_permanent_exact_scope_and_identity(tmp_path):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    authority.require(request(), check_identity=live, prompt=lambda _: GrantChoice.ALWAYS)
    other = CapabilityAuthority(path=authority.path)
    other.check(request(), check_identity=live)
    for changed in [request(fingerprint="b" * 64), request(resource="creator.build.other"),
                    request(operation="directory.write")]:
        with pytest.raises(CapabilityDenied):
            other.check(changed, check_identity=live)
    with pytest.raises(CapabilityDenied):
        other.check(request(), check_identity=lambda *args: False)


@pytest.mark.parametrize("choice", [GrantChoice.ONCE, GrantChoice.ALWAYS])
def test_directory_read_covers_descendants_but_not_other_permissions(tmp_path, choice):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    root = tmp_path / "project"
    parent = request(operation="directory.read", resource=str(root))
    child = request(operation="directory.read", resource=str(root / "build" / "output"))
    prompts = []
    authority.require(parent, check_identity=live, prompt=lambda value: prompts.append(value) or choice)
    authority.require(child, check_identity=live, prompt=lambda _: pytest.fail("child prompted"))
    authority.check(child, check_identity=live)
    assert prompts == [parent]
    for different in [request(operation="directory.read", resource=str(tmp_path / "project-other")),
                      request(operation="directory.read", resource=str(root / ".." / "outside")),
                      request(operation="directory.write", resource=str(root / "build")),
                      request(operation="directory.read", resource=str(root / "build"), fingerprint="b" * 64),
                      request(operation="directory.read", resource=str(root / "build"), plugin="other")]:
        with pytest.raises(CapabilityDenied):
            authority.check(different, check_identity=live)
    if choice is GrantChoice.ALWAYS:
        other = CapabilityAuthority(path=authority.path)
        other.check(child, check_identity=live)
        other.revoke(parent)
    else:
        authority.revoke(parent)
    with pytest.raises(CapabilityDenied):
        authority.check(child, check_identity=live)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows paths")
def test_windows_directory_scope_handles_case_drives_and_component_boundaries():
    from core.plugin_capabilities import _covers
    root = request(operation="directory.read", resource=r"F:\cocos\frame\3.x")
    assert _covers(root, request(operation="directory.read", resource=r"f:\COCOS\frame\3.X\build"))
    for path in [r"F:\cocos\frame\3.x2", r"E:\cocos\frame\3.x\build",
                 r"F:\cocos\frame\3.x\..\outside", r"F:\cocos\frame\3.x\bad.\data"]:
        assert not _covers(root, request(operation="directory.read", resource=path))
    network = request(operation="directory.read", resource=r"\\server\share\project")
    assert _covers(network, request(operation="directory.read", resource=r"\\SERVER\share\project\build"))
    assert not _covers(network, request(operation="directory.read", resource=r"\\server\share-other\project\build"))


@pytest.mark.parametrize("choice", [GrantChoice.DENY, None, "always"])
def test_denied_or_invalid_choice_does_not_persist(tmp_path, choice):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    with pytest.raises(CapabilityDenied):
        authority.require(request(), check_identity=live, prompt=lambda _: choice)
    assert authority.list_grants() == []


def test_back_cancels_without_persisting_and_next_request_prompts(tmp_path, monkeypatch):
    from core import plugin_capabilities, prompt
    from core.cli import non_interactive
    monkeypatch.setattr(plugin_capabilities, "_interactive_input", lambda: True)
    answers = iter([None, "1"])
    seen = []
    def select(*args, **kwargs):
        seen.append(args[2])
        return next(answers)
    monkeypatch.setattr(prompt, "select", select)
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    token = non_interactive.set(False)
    try:
        with pytest.raises(prompt.UserCancel):
            authority.require(request(), check_identity=live)
        assert authority.list_grants() == []
        with pytest.raises(CapabilityDenied):
            authority.check(request(), check_identity=live)
        authority.require(request(), check_identity=live)
        authority.check(request(), check_identity=live)
    finally:
        non_interactive.reset(token)
        authority.close()
    assert len(seen) == 2
    assert [[item["key"] for item in choices] for choices in seen] == [["1", "2"], ["1", "2"]]


@pytest.mark.parametrize("choice", [GrantChoice.ONCE, GrantChoice.ALWAYS])
def test_revoke_invalidates_in_flight_and_persistent_permissions(tmp_path, choice):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    authority.require(request(), check_identity=live, prompt=lambda _: choice)
    CapabilityAuthority(path=authority.path).revoke(request())
    with pytest.raises(CapabilityDenied):
        authority.check(request(), check_identity=live)


def test_revocation_during_prompt_does_not_regrant(tmp_path):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    def prompt(value):
        CapabilityAuthority(path=authority.path).revoke(value)
        return GrantChoice.ALWAYS
    with pytest.raises(CapabilityDenied):
        authority.require(request(), check_identity=live, prompt=prompt)
    assert authority.list_grants() == []


def test_corrupt_store_is_preserved_and_denied(tmp_path):
    path = tmp_path / "grants.yaml"
    path.write_text("not: an-authority", encoding="utf-8")
    authority = CapabilityAuthority(path=path)
    with pytest.raises(CapabilityDenied):
        authority.require(request(), check_identity=live, prompt=lambda _: GrantChoice.ALWAYS)
    assert path.read_text(encoding="utf-8") == "not: an-authority"


def test_noninteractive_permission_cannot_be_approved_by_yes(monkeypatch):
    from core.cli import non_interactive
    from core.plugin_capabilities import prompt_grant
    token = non_interactive.set(True)
    try:
        with pytest.raises(CapabilityDenied):
            prompt_grant(request())
    finally:
        non_interactive.reset(token)


def test_identity_revoked_during_prompt_does_not_persist(tmp_path):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    trusted = [True]
    def prompt(value):
        trusted[0] = False
        return GrantChoice.ALWAYS
    with pytest.raises(CapabilityDenied):
        authority.require(request(), check_identity=lambda *args: trusted[0], prompt=prompt)
    assert authority.list_grants() == []


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows NUL console semantics")
def test_windows_nul_is_not_interactive_even_when_isatty_reports_true(monkeypatch):
    import os
    from core import plugin_capabilities as capabilities
    from core.cli import non_interactive
    with open(os.devnull) as source:
        monkeypatch.setattr(capabilities.sys, "stdin", source)
        # NUL's character-device TTY result must not authorize a console prompt.
        assert not capabilities._interactive_input()
        token = non_interactive.set(False)
        try:
            with pytest.raises(CapabilityDenied) as error:
                capabilities.prompt_grant(request())
            assert error.value.code == "interaction_required"
        finally:
            non_interactive.reset(token)
