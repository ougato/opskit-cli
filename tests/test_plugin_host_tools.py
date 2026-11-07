"""Generic host process scope, cancellation and raw results."""
import json
import os
import sys
import time
import base64
import hashlib

import pytest

from core import plugin_host_tools as tools, process
from core.native_sandbox import GrantChoice
from core.plugin_capabilities import CapabilityAuthority


@pytest.fixture
def context(tmp_path, monkeypatch):
    authority = CapabilityAuthority(path=tmp_path / "authority.yaml")
    require = authority.require
    monkeypatch.setattr(authority, "require", lambda request, **kw: require(request, prompt=lambda _: GrantChoice.ONCE, **kw))
    return dict(authority=authority, check_identity=lambda *a: True,
                deadline=time.monotonic() + 30, checkpoint=lambda: None, output=lambda line: None)


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
@pytest.mark.parametrize("code", [0, 36, 32, 34])
def test_raw_exit_code_no_business_success_policy(tmp_path, monkeypatch, context, code):
    tool = tmp_path / "tool.exe"; tool.write_bytes(b"test")
    captured = []
    def stream(cmd, **kwargs):
        captured.append((cmd, kwargs)); kwargs["checkpoint"]()
        yield "output"
        return code
    monkeypatch.setattr(process, "stream", stream)
    result = tools.execute("demo", "a" * 64, str(tool), ["literal;data", "{output_directory}"], str(tmp_path),
                           {"output_parent": str(tmp_path)}, **context)
    assert result["returncode"] == code and "success" not in result
    assert captured[0][0][1] == "literal;data"
    assert captured[0][0][2] == result["output"]["directory"]
    assert captured[0][1]["stdin"] == tools.subprocess.DEVNULL
    grants = context["authority"].once
    assert grants
    from core import plugin_artifacts as receipts
    record, _ = receipts._load("demo", result["output"]["id"])
    assert record["phase"] == "pending"


@pytest.mark.parametrize("args,options", [("shell command", {}), (["bad\narg"], {}), ([], {"shell": True}), ([1], {}),
                                         ([], {"timeout_seconds": 0}), ([], {"timeout_seconds": 3601}),
                                         ([], {"stdin_base64": "invalid!"}),
                                         (["{config_file}"], {}), ([], {"config_base64": "e30="}),
                                         ([], {"capture_after": "bad\nmarker"}),
                                         ([], {"authorization_scope": "invalid"}),
                                         ([], {"authorization_scope": "tool", "tool_dependencies": ["x"]})])
def test_invalid_arguments_never_prompt(tmp_path, args, options):
    with pytest.raises(ValueError):
        tools.execute("demo", "a" * 64, "missing", args, str(tmp_path), options,
                      authority=None, check_identity=None, deadline=time.monotonic()+2,
                      checkpoint=lambda: None, output=lambda _: None)


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_tool_scope_reuses_permanent_grant_and_rechecks_identity(tmp_path, monkeypatch):
    tool = tmp_path / "git.exe"
    tool.write_bytes(b"git")
    project = tmp_path / "project"
    project.mkdir()
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    prompts = []
    original_require = authority.require
    monkeypatch.setattr(authority, "require", lambda request, **kw: original_require(
        request, prompt=lambda value: prompts.append(value) or GrantChoice.ALWAYS, **kw))
    def stream(*args, **kwargs):
        yield "ok"
        return 0
    monkeypatch.setattr(process, "stream", stream)
    context = dict(authority=authority, check_identity=lambda *a: True,
                   deadline=time.monotonic() + 30, checkpoint=lambda: None, output=lambda _: None)
    for args, directory in [(["status"], tmp_path), (["fetch", "origin"], project)]:
        tools.execute("cocos", "a" * 64, str(tool), args, str(directory),
                      {"authorization_scope": "tool", "capture_output": True}, **context)
    assert len(prompts) == 1
    assert json.loads(prompts[0].resource) == {"authorization_scope": "tool", "executable": str(tool),
                                               "sha256": hashlib.sha256(b"git").hexdigest()}
    other = CapabilityAuthority(path=authority.path)
    other.require(prompts[0], check_identity=lambda *a: True,
                  prompt=lambda _: pytest.fail("persistent grant was not reused"))
    from core.native_sandbox import CapabilityRequest
    for changed in [CapabilityRequest("cocos", "b" * 64, "tool.execute", prompts[0].resource),
                    CapabilityRequest("other", "a" * 64, "tool.execute", prompts[0].resource)]:
        with pytest.raises(PermissionError):
            other.check(changed, check_identity=lambda *a: True)
    tool.write_bytes(b"updated git")
    tools.execute("cocos", "a" * 64, str(tool), ["status"], str(project),
                  {"authorization_scope": "tool", "capture_output": True}, **context)
    assert len(prompts) == 2
    assert prompts[0].resource != prompts[1].resource
    other.revoke(prompts[0])
    with pytest.raises(PermissionError):
        authority.check(prompts[0], check_identity=lambda *a: True)


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_consecutive_host_commands_do_not_print_waiting_text(tmp_path, monkeypatch, context):
    from core.cli import non_interactive
    import msvcrt

    tool = tmp_path / "git.exe"
    tool.write_bytes(b"git")
    logs = []
    context["output"] = logs.append
    monkeypatch.setattr("core.plugin_capabilities._interactive_input", lambda: True)
    monkeypatch.setattr(msvcrt, "kbhit", lambda: False)
    def stream(*args, **kwargs):
        kwargs["checkpoint"]()
        return 0
        yield
    monkeypatch.setattr(process, "stream", stream)
    token = non_interactive.set(False)
    try:
        for command in ("status", "fetch", "reset"):
            tools.execute("demo", "a" * 64, str(tool), [command], str(tmp_path),
                          {"authorization_scope": "tool"}, **context)
        assert logs == []
        tools.execute("demo", "a" * 64, str(tool), ["status"], str(tmp_path),
                      {"authorization_scope": "tool"}, **context)
        assert logs == []
    finally:
        non_interactive.reset(token)


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_changed_executable_during_consent_never_launches(tmp_path, monkeypatch, context):
    tool=tmp_path/"tool.exe"; tool.write_bytes(b"original")
    require=context["authority"].require
    def changed(request, **kwargs):
        result=require(request, **kwargs); tool.write_bytes(b"replacement"); return result
    monkeypatch.setattr(context["authority"], "require", changed)
    monkeypatch.setattr(process,"stream",lambda *a,**k: pytest.fail("changed executable launched"))
    with pytest.raises(PermissionError):
        tools.execute("demo","a"*64,str(tool),[],str(tmp_path),{},**context)


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_denied_tool_never_launches(tmp_path, monkeypatch, context):
    tool=tmp_path/"tool.exe"; tool.write_bytes(b"fixture")
    monkeypatch.setattr(context["authority"],"require",lambda *a,**k: (_ for _ in ()).throw(PermissionError("denied")))
    monkeypatch.setattr(process,"stream",lambda *a,**k: pytest.fail("unapproved tool launched"))
    with pytest.raises(PermissionError):
        tools.execute("demo","a"*64,str(tool),[],str(tmp_path),{},**context)


def test_silent_host_process_revocation_kills_child():
    checks=0; processes=[]
    def checkpoint():
        nonlocal checks
        checks+=1
        if checks>=4: raise PermissionError("revoked")
    iterator=process.stream([sys.executable,"-c","import time; time.sleep(60)"],
                            timeout=10,process_ref=processes,checkpoint=checkpoint)
    with pytest.raises(PermissionError): next(iterator)
    assert processes[0].poll() is not None


def test_process_stream_delivers_exact_bounded_binary_input():
    payload = b"version\r\n\x00\xff"
    iterator = process.stream([sys.executable, "-c", "import sys; print(sys.stdin.buffer.read().hex())"],
                              stdin_data=payload, timeout=10)
    assert next(iterator) == payload.hex()
    with pytest.raises(StopIteration) as completed:
        next(iterator)
    assert completed.value.value == 0


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_host_input_digest_is_part_of_consent(tmp_path, monkeypatch, context):
    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"fixture")
    scopes, inputs = [], []
    require = context["authority"].require
    def record(request, **kwargs):
        scopes.append(json.loads(request.resource))
        return require(request, **kwargs)
    monkeypatch.setattr(context["authority"], "require", record)
    def stream(*args, **kwargs):
        inputs.append(kwargs["stdin_data"])
        yield "done"
        return 0
    monkeypatch.setattr(process, "stream", stream)
    for value in (b"first", b"second"):
        tools.execute("demo", "a" * 64, str(tool), [], str(tmp_path),
                      {"stdin_base64": base64.b64encode(value).decode("ascii")}, **context)
    assert inputs == [b"first", b"second"]
    assert scopes[0]["stdin"] == {"size": 5, "sha256": hashlib.sha256(b"first").hexdigest()}
    assert scopes[0] != scopes[1]


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_host_temporary_configuration_and_capture_are_scoped(tmp_path, monkeypatch, context):
    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"fixture")
    scopes, paths, streamed = [], [], []
    context["output"] = streamed.append
    require = context["authority"].require
    def record(request, **kwargs):
        scopes.append(json.loads(request.resource))
        return require(request, **kwargs)
    monkeypatch.setattr(context["authority"], "require", record)
    def stream(cmd, **kwargs):
        from pathlib import Path
        config = Path(cmd[1].split("=", 1)[1])
        paths.append(config)
        assert config.read_bytes() == b'{"safe":true}'
        yield "log"
        yield "Start options:"
        yield '{"options":true}'
        return 0
    monkeypatch.setattr(process, "stream", stream)
    result = tools.execute("demo", "a" * 64, str(tool), ["config={config_file}"], str(tmp_path),
        {"config_base64": base64.b64encode(b'{"safe":true}').decode(), "capture_after": "Start options:"}, **context)
    assert result["captured_after"] == '{"options":true}'
    assert streamed == ["log", "Start options:", '{"options":true}']
    assert not paths[0].exists()
    assert scopes[0]["configuration"] == {"size": 13, "sha256": hashlib.sha256(b'{"safe":true}').hexdigest()}
    assert scopes[0]["capture_after"] == "Start options:"


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_output_template_scope_is_reusable_but_actual_outputs_are_unique(tmp_path, monkeypatch, context):
    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"fixture")
    scopes = []
    require = context["authority"].require
    def record(request, **kwargs):
        scopes.append(json.loads(request.resource))
        return require(request, **kwargs)
    monkeypatch.setattr(context["authority"], "require", record)
    def stream(*args, **kwargs):
        yield "done"
        return 0
    monkeypatch.setattr(process, "stream", stream)
    first = tools.execute("demo", "a" * 64, str(tool), ["output={output_name}"], str(tmp_path),
                          {"output_parent": str(tmp_path)}, **context)
    second = tools.execute("demo", "a" * 64, str(tool), ["output={output_name}"], str(tmp_path),
                           {"output_parent": str(tmp_path)}, **context)
    assert first["output"]["directory"] != second["output"]["directory"]
    assert scopes[0] == scopes[1]
    assert scopes[0]["arguments"] == ["output={output_name}"]


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_changed_arguments_have_distinct_grants(tmp_path, monkeypatch, context):
    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"fixture")
    scopes = []
    require = context["authority"].require
    def record(request, **kwargs):
        scopes.append(request.resource)
        return require(request, **kwargs)
    monkeypatch.setattr(context["authority"], "require", record)
    def stream(*args, **kwargs):
        yield "done"
        return 0
    monkeypatch.setattr(process, "stream", stream)
    for argument in ("one", "two"):
        tools.execute("demo", "a" * 64, str(tool), [argument], str(tmp_path), {}, **context)
    assert scopes[0] != scopes[1]
    assert [json.loads(scope)["arguments"] for scope in scopes] == [["one"], ["two"]]


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_capture_output_is_bounded_and_has_distinct_authorization(tmp_path, monkeypatch, context):
    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"fixture")
    scopes = []
    require = context["authority"].require
    def record(request, **kwargs):
        scopes.append(json.loads(request.resource))
        return require(request, **kwargs)
    monkeypatch.setattr(context["authority"], "require", record)
    def stream(*args, **kwargs):
        yield "first"
        yield "second"
        return 0
    monkeypatch.setattr(process, "stream", stream)
    plain = tools.execute("demo", "a" * 64, str(tool), [], str(tmp_path), {}, **context)
    captured = tools.execute("demo", "a" * 64, str(tool), [], str(tmp_path), {"capture_output": True}, **context)
    assert "captured_output" not in plain
    assert captured["captured_output"] == "first\nsecond"
    assert "capture_output" not in scopes[0] and scopes[1]["capture_output"] is True

    def oversized(*args, **kwargs):
        yield "x" * 16385
    monkeypatch.setattr(process, "stream", oversized)
    with pytest.raises(RuntimeError, match="output limit"):
        tools.execute("demo", "a" * 64, str(tool), [], str(tmp_path), {"capture_output": True}, **context)


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_tool_dependency_is_scoped_and_in_tool_path(tmp_path, monkeypatch, context):
    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"fixture")
    directory = tmp_path / "dependency"
    directory.mkdir()
    dependency = directory / "git.exe"
    dependency.write_bytes(b"fixture git")
    scopes, environments = [], []
    require = context["authority"].require
    def record(request, **kwargs):
        scopes.append(json.loads(request.resource))
        return require(request, **kwargs)
    monkeypatch.setattr(context["authority"], "require", record)
    def stream(*args, **kwargs):
        environments.append(kwargs["env"])
        yield "done"
        return 0
    monkeypatch.setattr(process, "stream", stream)
    tools.execute("demo", "a" * 64, str(tool), [], str(tmp_path),
                  {"tool_dependencies": [str(dependency)]}, **context)
    assert str(directory) in environments[0]["PATH"].split(os.pathsep)
    assert scopes[0]["tool_dependencies"][0]["executable"] == str(dependency)
    assert scopes[0]["tool_dependencies"][0]["search_directory"] == str(directory)


@pytest.mark.skipif(os.name != "nt", reason="native host tool backend")
def test_changed_tool_dependency_during_consent_never_launches(tmp_path, monkeypatch, context):
    tool, dependency = tmp_path / "tool.exe", tmp_path / "git.exe"
    tool.write_bytes(b"tool")
    dependency.write_bytes(b"original")
    require = context["authority"].require
    def changed(request, **kwargs):
        result = require(request, **kwargs)
        dependency.write_bytes(b"replacement")
        return result
    monkeypatch.setattr(context["authority"], "require", changed)
    monkeypatch.setattr(process, "stream", lambda *a, **k: pytest.fail("changed dependency launched"))
    with pytest.raises(PermissionError, match="dependency changed"):
        tools.execute("demo", "a" * 64, str(tool), [], str(tmp_path),
                      {"tool_dependencies": [str(dependency)]}, **context)


def test_silent_process_timeout_and_cancel_reap_children():
    for checkpoint, expected, timeout in (
        (lambda: None, process.subprocess.TimeoutExpired, .15),
        (lambda: (_ for _ in ()).throw(KeyboardInterrupt()), KeyboardInterrupt, 10),
    ):
        processes = []
        iterator = process.stream([sys.executable, "-c", "import time; time.sleep(60)"],
                                  timeout=timeout, process_ref=processes, checkpoint=checkpoint)
        with pytest.raises(expected):
            next(iterator)
        assert all(child.poll() is not None for child in processes)


@pytest.mark.parametrize("suffix", ["../other", "name.", "name ", "name:stream"])
def test_ambiguous_host_paths_rejected_before_access(tmp_path, suffix):
    with pytest.raises(ValueError):
        tools._ordinary(str(tmp_path / suffix), directory=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows host discovery")
def test_find_executable_prefers_config_then_path_and_default_directories(tmp_path, monkeypatch, context):
    configured = tmp_path / "configured" / "git.exe"
    from_path = tmp_path / "path" / "git.exe"
    conventional = tmp_path / "programs" / "Git" / "cmd" / "git.exe"
    for candidate in (configured, from_path, conventional):
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"fixture")
    monkeypatch.setenv("PATH", os.pathsep.join(["relative", str(from_path.parent)]))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "programs"))
    monkeypatch.setenv("ProgramFiles(x86)", "")
    monkeypatch.setenv("LOCALAPPDATA", "")
    find = lambda preferred="": tools.find_executable("demo", "a" * 64, "git.exe", preferred, **context)
    assert find(str(configured)) == str(configured)
    configured.unlink()
    assert find(str(configured)) == str(from_path)
    from_path.unlink()
    assert os.path.normcase(find(str(configured))) == os.path.normcase(str(conventional))
    conventional.unlink()
    assert find(str(configured)) == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows host discovery")
def test_find_executable_rejection_does_not_try_another_candidate(tmp_path, monkeypatch, context):
    preferred = tmp_path / "preferred" / "git.exe"
    alternate = tmp_path / "alternate" / "git.exe"
    for candidate in (preferred, alternate):
        candidate.parent.mkdir()
        candidate.write_bytes(b"fixture")
    monkeypatch.setenv("PATH", str(alternate.parent))
    scopes = []
    def deny(scope, **kwargs):
        scopes.append(scope)
        raise PermissionError("denied")
    monkeypatch.setattr(context["authority"], "require", deny)
    with pytest.raises(PermissionError, match="denied"):
        tools.find_executable("demo", "a" * 64, "git.exe", str(preferred), **context)
    assert len(scopes) == 1 and scopes[0].resource == str(preferred.parent)


@pytest.mark.parametrize("name,preferred", [("../git.exe", ""), ("git.cmd", ""), ("git.exe", "relative/git.exe"),
                                             ("git.exe", r"C:\wrong.exe")])
def test_find_executable_rejects_ambiguous_requests(name, preferred, context):
    with pytest.raises(ValueError):
        tools.find_executable("demo", "a" * 64, name, preferred, **context)


@pytest.mark.skipif(os.name != "nt", reason="Windows host discovery")
def test_find_executable_skips_hard_link_and_finds_sibling_bin(tmp_path, monkeypatch, context):
    cmd, bin_dir = tmp_path / "Git" / "cmd", tmp_path / "Git" / "bin"
    cmd.mkdir(parents=True)
    bin_dir.mkdir()
    source = tmp_path / "hardlink-source.exe"
    source.write_bytes(b"fixture")
    os.link(source, cmd / "git.exe")
    usable = bin_dir / "git.exe"
    usable.write_bytes(b"fixture")
    monkeypatch.setenv("PATH", str(cmd))
    result = tools.find_executable("demo", "a" * 64, "git.exe", "", **context)
    assert result == str(usable)
    monkeypatch.setenv("PATH", "")
    result = tools.find_executable("demo", "a" * 64, "git.exe", str(cmd / "git.exe"), **context)
    assert result == str(usable)
