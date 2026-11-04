"""Receipts retain host authority across build, commit, inspection and collection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from core import plugin_artifacts as artifacts
from core.native_sandbox import GrantChoice
from core.plugin_capabilities import CapabilityAuthority


@pytest.fixture
def context(tmp_path, monkeypatch):
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    require = authority.require
    monkeypatch.setattr(authority, "require", lambda scope, **kw: require(scope, prompt=lambda _: GrantChoice.ONCE, **kw))
    return dict(authority=authority, check_identity=lambda *a: True,
                deadline=time.monotonic() + 60, checkpoint=lambda: None)


def manage(action, params, context, owner="demo"):
    return artifacts.manage(owner, "a" * 64, action, params, **context)


def output(project, context, *, commit=True, owner="demo"):
    token = uuid.uuid4().hex
    path = project / ("opskit-" + token)
    path.mkdir()
    artifacts.pending(owner, token, project, path, lambda: None)
    (path / "index.html").write_bytes(b"new artifact")
    if commit:
        manage("commit", {"id": token, "required_files": ["index.html"]}, context, owner)
    return token, path


def test_retention_unknown_modified_pending_and_other_owner_preserved(tmp_path, context):
    old, old_path = output(tmp_path, context)
    newest, newest_path = output(tmp_path, context)
    pending, pending_path = output(tmp_path, context, commit=False)
    other, other_path = output(tmp_path, context, owner="other")
    unknown = tmp_path / "unknown"; unknown.mkdir(); (unknown / "keep").write_bytes(b"safe")
    rows = manage("list", {"resource": str(tmp_path), "keep": 1}, context)
    assert {r["id"] for r in rows} == {old, newest}
    assert next(r for r in rows if r["id"] == newest)["cleanup_candidate"] is False
    row = next(r for r in rows if r["id"] == old)
    (old_path / "index.html").write_bytes(b"changed")
    with pytest.raises(ValueError):
        manage("clean", {"id": old, "record_sha256": row["record_sha256"], "keep": 1}, context)
    assert all(p.exists() for p in (old_path, newest_path, pending_path, other_path, unknown))
    with pytest.raises(FileNotFoundError):
        manage("clean", {"id": other, "record_sha256": "a"*64, "keep": 1}, context)


def test_exact_receipt_clean_and_digest_rejection(tmp_path, context):
    old, path = output(tmp_path, context); output(tmp_path, context)
    row = next(r for r in manage("list", {"resource":str(tmp_path),"keep":1},context) if r["id"]==old)
    with pytest.raises(ValueError):
        manage("clean", {"id":old,"record_sha256":"0"*64,"keep":1},context)
    assert path.exists()
    assert manage("clean", {"id":old,"record_sha256":row["record_sha256"],"keep":1},context)=={"removed":old}
    assert not path.exists()


def test_changed_after_candidate_inspection_is_not_deleted(tmp_path, context, monkeypatch):
    old, path=output(tmp_path,context);output(tmp_path,context)
    row=next(r for r in manage("list",{"resource":str(tmp_path),"keep":1},context) if r["id"]==old)
    inspect=artifacts._inspect
    def changed(*args):
        rows=inspect(*args);(path/"index.html").write_bytes(b"independent edit");return rows
    monkeypatch.setattr(artifacts,"_inspect",changed)
    # clean must recheck the content after candidate enumeration too.
    with pytest.raises(ValueError):
        manage("clean",{"id":old,"record_sha256":row["record_sha256"],"keep":1},context)
    assert path.exists()


def test_pending_requires_fresh_nonempty_required_files(tmp_path, context):
    token,path=output(tmp_path,context,commit=False)
    with pytest.raises((OSError,ValueError)):
        manage("commit",{"id":token,"required_files":["missing.html"]},context)
    assert artifacts._load("demo",token)[0]["phase"]=="pending"
    with pytest.raises(ValueError):
        manage("commit",{"id":token,"required_files":["../outside"]},context)


def test_active_cross_process_resource_lock_blocks_cleanup(tmp_path, context):
    old,path=output(tmp_path,context);output(tmp_path,context)
    row=next(r for r in manage("list",{"resource":str(tmp_path),"keep":1},context) if r["id"]==old)
    code="from core.plugin_artifacts import resource_lock; from pathlib import Path\nwith resource_lock(Path("+repr(str(tmp_path))+")): pass"
    with artifacts.resource_lock(tmp_path):
        child=subprocess.run([sys.executable,"-c",code],capture_output=True,timeout=10)
    assert child.returncode!=0 and path.exists()


def test_adoption_recomputes_legacy_snapshot_and_is_idempotent(tmp_path, context):
    path=tmp_path/"legacy";path.mkdir();(path/"index.html").write_bytes(b"old")
    snapshot=artifacts._snapshot(path,lambda:None)
    params={"resource":str(tmp_path),"directory":str(path),"snapshot":snapshot,
            "required_files":[],"completed_ns":time.time_ns()-1000}
    bad=dict(params,snapshot=dict(snapshot,inventory_sha256="0"*64))
    with pytest.raises(ValueError): manage("adopt",bad,context)
    first=manage("adopt",params,context);second=manage("adopt",params,context)
    assert first["id"]==second["id"]
    assert len(manage("list",{"resource":str(tmp_path),"keep":1},context))==1


def test_receipt_and_directory_replacement_are_rejected(tmp_path,context):
    token,path=output(tmp_path,context)
    path.rename(tmp_path/"preserved");path.mkdir();(path/"index.html").write_bytes(b"new")
    rows=manage("list",{"resource":str(tmp_path),"keep":1},context)
    assert rows[0]["valid"] is False


def test_symlink_outputs_never_committed(tmp_path,context,symlink_capable):
    token,path=output(tmp_path,context,commit=False)
    (path/"link").symlink_to(path/"index.html")
    with pytest.raises(ValueError):manage("commit",{"id":token,"required_files":[]},context)
