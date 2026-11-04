"""Generic host process scope, cancellation and raw results."""
import json
import os
import sys
import time

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


@pytest.mark.parametrize("args,options", [("shell command", {}), (["bad\narg"], {}), ([], {"shell": True}), ([1], {})])
def test_invalid_arguments_never_prompt(tmp_path, args, options):
    with pytest.raises(ValueError):
        tools.execute("demo", "a" * 64, "missing", args, str(tmp_path), options,
                      authority=None, check_identity=None, deadline=time.monotonic()+2,
                      checkpoint=lambda: None, output=lambda _: None)


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
