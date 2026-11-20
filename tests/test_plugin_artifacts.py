"""Receipts retain host authority across build, commit, inspection and collection."""
import base64
import hashlib
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


def test_read_verified_file_rejects_changed_large_config(tmp_path, context):
    content = b" " * (1024**2 + 1)
    (tmp_path / "settings.json").write_bytes(content)
    params = {"directory": str(tmp_path), "file": "settings.json", "limit": 4 * 1024**2,
              "sha256": hashlib.sha256(content).hexdigest()}
    assert base64.b64decode(manage("read_verified_file", params, context)["content"]) == content
    with pytest.raises(ValueError, match="digest or size differs"):
        manage("read_verified_file", {**params, "sha256": "0" * 64}, context)


def test_sign_update_manifest_uses_domain_and_checks_expected_public_key(tmp_path, context):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    (tmp_path / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = key.public_key()
    body = b'{"schemaVersion":1,"files":[]}'
    params = {"directory": str(tmp_path), "file": "key.pem",
              "manifest": base64.b64encode(body).decode("ascii"),
              "public_key": base64.b64encode(public.public_bytes(
                  serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode("ascii")}
    result = manage("sign_update_manifest", params, context)
    assert set(result) == {"signature"}
    public.verify(base64.b64decode(result["signature"]),
                  b"game-update-manifest-v1\0" + body, ec.ECDSA(hashes.SHA256()))
    assert manage("sign_update_manifest", params, context) == result
    assert manage("sign_update_manifest", {**params, **result}, context) == result
    with pytest.raises(ValueError, match="invalid update manifest signature"):
        manage("sign_update_manifest", {**params, **result, "manifest": base64.b64encode(
            b'{"schemaVersion":1,"files":[{}]}').decode("ascii")}, context)
    with pytest.raises(ValueError, match="does not match"):
        manage("sign_update_manifest", {**params, "public_key": "YWJj"}, context)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows no-overwrite directory rename")
def test_publish_tree_creates_parents_under_requested_root(tmp_path, context):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_bytes(b"web")
    destination = tmp_path / "artifacts" / "build" / "official"
    parameters = {"source": str(source), "destination": str(destination),
                  "create_parents_from": str(tmp_path),
                  "files": {"index.html": {"source": "index.html", "sha256": hashlib.sha256(b"web").hexdigest()}}}
    assert manage("publish_tree", parameters, context) == {"directory": str(destination)}
    assert manage("publish_tree", parameters, context) == {"directory": str(destination)}
    assert (destination / "index.html").read_bytes() == b"web"
    with pytest.raises(ValueError):
        manage("publish_tree", {**parameters, "destination": str(tmp_path / ".." / "escape" / "output")}, context)
    assert not (tmp_path.parent / "escape" / "output").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows no-overwrite directory rename")
def test_publish_tree_combines_baseline_and_changed_bundle_at_final_archive(tmp_path, context):
    project = tmp_path / "client"
    baseline = project / "artifacts" / "baseline"
    bundle = tmp_path / "workspace"
    baseline.mkdir(parents=True)
    bundle.mkdir()
    (baseline / "index.html").write_bytes(b"baseline")
    resource = bundle / "resource.json"
    resource.write_bytes(b"changed")
    destination = project / "artifacts" / "build" / "new"
    files = {
        "index.html": {"source": "client/artifacts/baseline/index.html",
                       "sha256": hashlib.sha256(b"baseline").hexdigest()},
        "assets/shop/resource.json": {"source": "workspace/resource.json",
                                       "sha256": hashlib.sha256(b"changed").hexdigest()},
    }
    parameters = {"source": str(tmp_path), "destination": str(destination),
                  "create_parents_from": str(project), "files": files}
    assert manage("publish_tree", parameters, context) == {"directory": str(destination)}
    assert (destination / "index.html").read_bytes() == b"baseline"
    assert (destination / "assets/shop/resource.json").read_bytes() == b"changed"
    resource.write_bytes(b"modified after inventory")
    rejected = destination.with_name("rejected")
    with pytest.raises(ValueError, match="source changed during copy"):
        manage("publish_tree", {**parameters, "destination": str(rejected)}, context)
    assert not rejected.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows no-overwrite directory rename")
def test_publish_tree_atomically_includes_generated_manifest(tmp_path, context):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.png").write_bytes(b"payload")
    destination = tmp_path / "patch"
    manifest = b'{"schemaVersion":1}'
    params = {
        "source": str(source), "destination": str(destination),
        "files": {
            "payload.png": {"source": "payload.png", "sha256": hashlib.sha256(b"payload").hexdigest()},
            "manifest.json": {"content": base64.b64encode(manifest).decode("ascii"),
                              "sha256": hashlib.sha256(manifest).hexdigest()},
        },
    }
    assert manage("publish_tree", params, context) == {"directory": str(destination)}
    assert (destination / "manifest.json").read_bytes() == manifest
    assert (destination / "payload.png").read_bytes() == b"payload"
    assert not (source / "manifest.json").exists()
    params["destination"] = str(tmp_path / "invalid")
    params["files"]["manifest.json"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="invalid inline tree digest"):
        manage("publish_tree", params, context)
    assert not (tmp_path / "invalid").exists()

    inline = {"source": str(tmp_path), "destination": str(tmp_path / "generated"), "files": {
        ".well-known/assetlinks.json": {"content": base64.b64encode(manifest).decode("ascii"),
                                         "sha256": hashlib.sha256(manifest).hexdigest()},
    }}
    manage("publish_tree", inline, context)
    assert (tmp_path / "generated" / ".well-known" / "assetlinks.json").read_bytes() == manifest
    copied = {**inline, "destination": str(tmp_path / "copied"), "files": {
        "payload.png": {**params["files"]["payload.png"], "source": "source/payload.png"},
    }}
    manage("publish_tree", copied, context)
    assert (tmp_path / "copied" / "payload.png").read_bytes() == b"payload"
    with pytest.raises(ValueError, match="destination overlaps source"):
        manage("publish_tree", {**copied, "files": {
            "payload.png": {**params["files"]["payload.png"], "source": "copied/payload.png"},
        }}, context)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows no-overwrite directory rename")
def test_publish_tree_preserves_existing_archive_and_rejects_changed_source(tmp_path, context):
    source = tmp_path / "build"
    source.mkdir()
    (source / "index.html").write_bytes(b"built")
    destination = tmp_path / "archive"
    digest = hashlib.sha256(b"built").hexdigest()
    params = {"source": str(source), "destination": str(destination),
              "files": {"web/index.html": {"source": "index.html", "sha256": digest}}}
    assert manage("publish_tree", params, context) == {"directory": str(destination)}
    assert manage("publish_tree", params, context) == {"directory": str(destination)}
    assert (destination / "web" / "index.html").read_bytes() == b"built"
    (source / "index.html").write_bytes(b"changed")
    params["destination"] = str(tmp_path / "second")
    with pytest.raises(ValueError, match="changed during copy"):
        manage("publish_tree", params, context)
    assert not (tmp_path / "second").exists()
    assert list(tmp_path.glob(".opskit-tree-*")) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows no-overwrite directory rename")
@pytest.mark.parametrize("replace_file", [False, True])
def test_new_tree_checks_staged_file_identity_without_readback(tmp_path, monkeypatch, context, replace_file):
    from core import plugin_tree

    content = b"built"
    destination = tmp_path / "published"
    params = {
        "source": str(tmp_path),
        "destination": str(destination),
        "files": {
            "index.html": {"content": base64.b64encode(content).decode("ascii"),
                           "sha256": hashlib.sha256(content).hexdigest()},
        },
    }
    walk = plugin_tree.bounded_paths

    def staging_paths(root, **kwargs):
        if replace_file:
            replacement = root.parent / "replacement"
            replacement.write_bytes(b"other")
            replacement.replace(root / "index.html")
        yield from walk(root, **kwargs)

    def unexpected_readback(*args, **kwargs):
        pytest.fail("new publication must not hash the staged tree again")

    monkeypatch.setattr(plugin_tree, "bounded_paths", staging_paths)
    monkeypatch.setattr(artifacts, "_snapshot", unexpected_readback)
    if replace_file:
        with pytest.raises(ValueError, match="staged tree differs"):
            manage("publish_tree", params, context)
        assert not destination.exists()
    else:
        manage("publish_tree", params, context)
        assert (destination / "index.html").read_bytes() == content
    assert list(tmp_path.glob(".opskit-tree-*")) == []


def test_zip_entry_reads_only_requested_member_and_rejects_duplicates(tmp_path, context):
    from zipfile import ZipFile

    package = tmp_path / "sample.zip"
    params = {"directory": str(tmp_path), "file": package.name, "member": "assets/environment.json"}
    with ZipFile(package, "w") as archive:
        archive.writestr(params["member"], b'{"env":{}}')
        archive.writestr("unrelated.txt", b"other")
    assert base64.b64decode(manage("zip_entry", params, context)["content"]) == b'{"env":{}}'
    with ZipFile(package, "a") as archive, pytest.warns(UserWarning, match="Duplicate name"):
        archive.writestr(params["member"], b"duplicate")
    with pytest.raises(ValueError, match="ambiguous"):
        manage("zip_entry", params, context)


def test_zip_entry_pattern_matches_only_one_path_component(tmp_path, context):
    from zipfile import ZipFile

    package = tmp_path / "sample.ipa"
    params = {"directory": str(tmp_path), "file": package.name,
              "member_pattern": "Payload/*.app/environment.json"}
    with ZipFile(package, "w") as archive:
        archive.writestr("Payload/Game.app/environment.json", b'{"env":{}}')
        archive.writestr("Payload/Game.app/Nested/Other.app/environment.json", b"nested")
    assert base64.b64decode(manage("zip_entry", params, context)["content"]) == b'{"env":{}}'
    with ZipFile(package, "a") as archive:
        archive.writestr("Payload/Another.app/environment.json", b"another")
    with pytest.raises(ValueError, match="ambiguous"):
        manage("zip_entry", params, context)


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


def test_generated_commit_uses_parent_grant_but_cleanup_uses_exact_output(tmp_path, context, monkeypatch):
    scopes = []
    require = context["authority"].require
    def record(scope, **kwargs):
        scopes.append(scope)
        return require(scope, **kwargs)
    monkeypatch.setattr(context["authority"], "require", record)
    first, path = output(tmp_path, context)
    output(tmp_path, context)
    rows = manage("list", {"resource": str(tmp_path), "keep": 1}, context)
    row = next(item for item in rows if item["id"] == first)
    manage("clean", {"id": first, "record_sha256": row["record_sha256"], "keep": 1}, context)
    write_scopes = [scope.resource for scope in scopes if scope.operation == "directory.write"]
    assert write_scopes[:2] == [str(tmp_path), str(tmp_path)]
    assert write_scopes[-1] == str(path)


def test_commit_rejects_pending_directory_with_wrong_generated_name(tmp_path, context):
    token = uuid.uuid4().hex
    path = tmp_path / "other"
    path.mkdir()
    (path / "index.html").write_bytes(b"x")
    artifacts.pending("demo", token, tmp_path, path, lambda: None)
    with pytest.raises(ValueError, match="pending output"):
        manage("commit", {"id": token, "required_files": ["index.html"]}, context)
    assert path.exists()


def test_active_cross_process_resource_lock_blocks_cleanup(tmp_path, context):
    old,path=output(tmp_path,context);output(tmp_path,context)
    next(r for r in manage("list",{"resource":str(tmp_path),"keep":1},context) if r["id"]==old)
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
