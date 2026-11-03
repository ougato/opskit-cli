import os
from pathlib import Path
import time

import pytest

from core.plugin_host_tools import creator_build
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied
from core.native_sandbox import GrantChoice


@pytest.mark.parametrize("filename", ["Creator.exe", "CocosCreator.exe"])
@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("exit_code", [0, 36])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.skipif(os.name != "nt", reason="Windows Creator host tool")
def test_creator_scope_and_fixed_arguments(tmp_path, monkeypatch, filename, language, exit_code, named):
    from core import i18n
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    from core import process
    tool = tmp_path / filename
    tool.write_bytes(b"reviewed executable")
    if named:
        from core.plugin_tool_config import configure_creator
        configure_creator("creator-3-8", str(tool))
    project = tmp_path / "game"
    project.mkdir()
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    require = authority.require
    scopes = []
    def grant(scope, **kwargs):
        scopes.append(scope)
        return require(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs)
    monkeypatch.setattr(authority, "require", grant)
    monkeypatch.setenv("PATH", "UNREVIEWED-PATH")
    monkeypatch.setenv("SECRET_TOKEN", "must-not-inherit")
    def stream(command, **kwargs):
        assert command[:4] == [str(tool), "--project", str(project), "--build"]
        assert command[4].startswith("platform=web-mobile;debug=false;buildPath=project://build;outputName=opskit-web-mobile-")
        output_name = command[4].split("outputName=", 1)[1]
        artifact = project / "build" / output_name
        artifact.mkdir(parents=True)
        (artifact / "index.html").write_bytes(b"new build")
        environment = kwargs["env"]
        assert "UNREVIEWED-PATH" not in environment["PATH"]
        assert "SECRET_TOKEN" not in environment
        assert environment["PATH"].split(os.pathsep)[0] == str(tool.parent)
        assert Path(environment["ComSpec"]).is_file()
        kwargs["checkpoint"]()
        yield "building"
        return exit_code
    monkeypatch.setattr(process, "stream", stream)
    logs = []
    result = creator_build("demo", "a" * 64, "@creator/creator-3-8" if named else str(tool), str(project), {"platform": "web-mobile"},
                           authority=authority, check_identity=lambda *a: True,
                           deadline=time.monotonic() + 10, checkpoint=lambda: None, output=logs.append)
    artifact = result.pop("artifact")
    assert result == {"returncode": exit_code, "success": True, "platform": "web-mobile"}
    assert artifact["files"] == 1 and artifact["bytes"] == 9
    assert Path(artifact["directory"]).joinpath("index.html").read_bytes() == b"new build"
    assert scopes[0].operation == "tool.execute"
    assert '"sha256"' in scopes[0].resource
    assert '"output_policy": "project-build-unique-v1"' in scopes[0].resource
    assert logs == [i18n.t("plugin_native.creator.starting", platform="web-mobile"),
                    "building", i18n.t("plugin_native.creator.completed", platform="web-mobile")]


@pytest.mark.parametrize("options", [{"platform": "web-mobile;evil=true"}, {"platform": "web-mobile", "shell": "cmd"},
                                     {"platform": "windows", "debug": "false"}])
def test_creator_invalid_options_never_prompt(tmp_path, options):
    tool = tmp_path / "Creator.exe"
    tool.write_bytes(b"test")
    with pytest.raises(ValueError):
        creator_build("demo", "a" * 64, str(tool), str(tmp_path), options,
                       authority=None, check_identity=lambda *a: True,
                       deadline=time.monotonic() + 10, checkpoint=lambda: None, output=lambda _: None)


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("options,key", [
    ({"platform": "web-mobile", "shell": "cmd"}, "invalid_options"),
    ({"platform": "web-mobile;evil=true"}, "invalid_platform"),
    ({"platform": "windows", "debug": "false"}, "invalid_debug"),
])
@pytest.mark.skipif(os.name != "nt", reason="Windows Creator host tool")
def test_creator_validation_errors_follow_language_before_authorization(
        tmp_path, monkeypatch, language, options, key):
    from core import i18n, process
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    tool = tmp_path / "Creator.exe"
    tool.write_bytes(b"not executed")
    monkeypatch.setattr(process, "stream", lambda *args, **kwargs: pytest.fail("invalid tool request launched"))
    with pytest.raises(ValueError) as refused:
        creator_build("demo", "a" * 64, str(tool), str(tmp_path), options,
                      authority=None, check_identity=lambda *args: pytest.fail("invalid request authorized"),
                      deadline=time.monotonic() + 10, checkpoint=lambda: None, output=lambda _: None)
    assert str(refused.value) == i18n.t("plugin_native.creator." + key)
    assert "plugin_native." not in str(refused.value)


def test_named_creator_change_during_authorization_prevents_launch(tmp_path, monkeypatch):
    from core.plugin_tool_config import configure_creator
    from core import process
    first = tmp_path / "first" / "Creator.exe"
    second = tmp_path / "second" / "Creator.exe"
    for tool in (first, second):
        tool.parent.mkdir()
        tool.write_bytes(b"test tool, never executed")
    configure_creator("creator", str(first))
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    original = authority.require
    def require(scope, **kwargs):
        result = original(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs)
        configure_creator("creator", str(second))
        return result
    monkeypatch.setattr(authority, "require", require)
    monkeypatch.setattr(process, "stream", lambda *args, **kwargs: pytest.fail("changed tool launched"))
    from core.i18n import t
    with pytest.raises(PermissionError) as refused:
        creator_build("demo", "a" * 64, "@creator/creator", str(tmp_path), {"platform": "web-mobile"},
                      authority=authority, check_identity=lambda *args: True,
                      deadline=time.monotonic() + 10, checkpoint=lambda: None, output=lambda _: None)
    assert str(refused.value) == t("plugin_native.creator.resource_changed")


def test_silent_host_process_revocation_kills_child(tmp_path):
    import sys
    import psutil
    from core.process import stream
    processes = []
    checks = 0
    def checkpoint():
        nonlocal checks
        checks += 1
        if checks >= 4:
            raise PermissionError("revoked")
    iterator = stream([sys.executable, "-c", "import time; time.sleep(60)"],
                      timeout=10, process_ref=processes, checkpoint=checkpoint)
    with pytest.raises(PermissionError):
        next(iterator)
    assert processes[0].poll() is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows Creator host tool")
def test_creator_changed_during_prompt_does_not_launch(tmp_path, monkeypatch):
    from core import process
    tool = tmp_path / "Creator.exe"
    tool.write_bytes(b"original")
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    require = authority.require
    def grant(scope, **kwargs):
        def prompt(_):
            tool.write_bytes(b"replacement")
            return GrantChoice.ONCE
        return require(scope, prompt=prompt, **kwargs)
    monkeypatch.setattr(authority, "require", grant)
    monkeypatch.setattr(process, "stream", lambda *a, **k: pytest.fail("changed tool launched"))
    with pytest.raises(PermissionError):
        creator_build("demo", "a" * 64, str(tool), str(tmp_path), {"platform": "web-mobile"},
                       authority=authority, check_identity=lambda *a: True,
                       deadline=time.monotonic() + 10, checkpoint=lambda: None, output=lambda _: None)


@pytest.mark.skipif(os.name != "nt", reason="Windows Creator host tool")
@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_creator_failure_summary_is_localized_and_never_reports_success(tmp_path, monkeypatch, language, interrupted):
    from core import i18n, process
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    tool = tmp_path / "CocosCreator.exe"
    tool.write_bytes(b"fixture")
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    require = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kwargs:
                        require(scope, prompt=lambda _: GrantChoice.ONCE, **kwargs))
    closed = []
    def stream(*args, **kwargs):
        try:
            yield "raw tool diagnostic"
            if interrupted:
                raise KeyboardInterrupt()
            return 7
        finally:
            closed.append(True)
    monkeypatch.setattr(process, "stream", stream)
    logs = []
    def invoke():
        return creator_build("demo", "a" * 64, str(tool), str(tmp_path), {"platform": "web-mobile"},
                             authority=authority, check_identity=lambda *a: True,
                             deadline=time.monotonic() + 10, checkpoint=lambda: None, output=logs.append)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            invoke()
        assert logs[-1] == i18n.t("plugin_native.creator.interrupted")
    else:
        assert invoke() == {"returncode": 7, "success": False, "platform": "web-mobile"}
        assert logs[-1] == i18n.t("plugin_native.creator.failed", code=7)
    assert closed == [True]
    assert "raw tool diagnostic" in logs
    assert i18n.t("plugin_native.creator.completed", platform="web-mobile") not in logs
