"""Implicit directories must consume quota before filesystem mutations."""
import base64
import io
import tarfile
import zipfile

import pytest

from core import archive as extraction
from core import archive_reader as reader


def tar_packet(names):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as package:
        for name in names:
            member = tarfile.TarInfo(name)
            member.size = 5
            package.addfile(member, io.BytesIO(b"inert"))
    return output.getvalue()


def zip_packet(names):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        for name in names:
            package.writestr(name, b"inert")
    return output.getvalue()


@pytest.mark.parametrize("kind", ["tar", "zip", "selected"])
@pytest.mark.parametrize("limit", ["nodes", "bytes"])
def test_deep_unique_paths_rejected_before_destination_created(tmp_path, monkeypatch, kind, limit):
    names = [f"branch{i}/" + "nested/" * 20 + "tool" for i in range(4)]
    # All individual paths and the explicit four-member count are valid.
    monkeypatch.setattr(extraction, "MAX_LAYOUT_NODES", 30 if limit == "nodes" else 10000, raising=False)
    monkeypatch.setattr(extraction, "MAX_LAYOUT_BYTES", 1024 if limit == "bytes" else 1024**2, raising=False)
    destination = tmp_path / "must-not-exist"
    opener = reader.open_zip if kind == "zip" else reader.open_tar
    data = zip_packet(names) if kind == "zip" else tar_packet(names)
    with opener(fileobj=io.BytesIO(data)) as package:
        with pytest.raises(ValueError, match="layout.*limits"):
            if kind == "zip":
                extraction.extract_zip(package, destination)
            elif kind == "selected":
                extraction.extract_tar_files(package, destination, lambda name, member: name)
            else:
                extraction.extract_tar(package, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_shared_parent_is_charged_once_and_explicit_directories_reuse_node(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(extraction, "MAX_LAYOUT_NODES", 5, raising=False)
    names = ["root/shared/", "root/shared/one", "root/shared/two", "root/shared/three"]
    destination = tmp_path / "result"
    if kind == "tar":
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as package:
            for name in names:
                member = tarfile.TarInfo(name)
                if name.endswith("/"):
                    member.type = tarfile.DIRTYPE
                    package.addfile(member)
                else:
                    member.size = 5
                    package.addfile(member, io.BytesIO(b"inert"))
        with reader.open_tar(fileobj=io.BytesIO(output.getvalue())) as package:
            extraction.extract_tar(package, destination)
    else:
        with reader.open_zip(fileobj=io.BytesIO(zip_packet(names))) as package:
            extraction.extract_zip(package, destination)
    assert (destination / "root/shared/three").read_bytes() == b"inert"


def test_plugin_parser_limits_also_reduce_implicit_layout_budget(tmp_path):
    data = tar_packet(["a/b/c/d/e/tool"])
    with reader.open_tar(fileobj=io.BytesIO(data), max_files=2) as package:
        with pytest.raises(ValueError, match="layout.*limits"):
            extraction.extract_tar(package, tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_real_plugin_state_keeps_previous_data_on_implicit_directory_excess(tmp_path):
    from core import plugin_sandbox as sandbox
    root = tmp_path / "state"
    root.mkdir()
    (root / "previous").write_bytes(b"keep")
    # 80 explicit members fit the file/payload quotas; 10,240 distinct
    # implicit/explicit nodes exceed this plugin's 8,192-node layout quota.
    names = [f"branch{i}/" + "a/" * 126 + "tool" for i in range(80)]
    data = base64.b64encode(tar_packet(names)).decode("ascii")
    with pytest.raises(ValueError, match="layout.*limits"):
        sandbox._save_state(data, root, plugin="owner", caller="platform")
    assert (root / "previous").read_bytes() == b"keep"
    assert list(root.iterdir()) == [root / "previous"]
    assert not list(tmp_path.glob(".state-*"))
    assert not (tmp_path / "state.previous").exists()
