"""Publication gate rejects private extras, stale source and forged RECORD."""
import base64
import csv
import hashlib
import io
import zipfile

import pytest

from scripts.verify_wheel import verify


def make_wheel(tmp_path, change=None):
    source = tmp_path / "source"
    (source / "core" / "locale").mkdir(parents=True)
    (source / "main.py").write_bytes(b"app = None\n")
    (source / "core" / "locale" / "zh.yaml").write_bytes(b"hello: world\n")
    folder = "opskit-1.0.0.dist-info/"
    members = {"main.py": b"app = None\n", "core/locale/zh.yaml": b"hello: world\n",
               folder + "METADATA": b"Name: opskit\nVersion: 1.0.0\n",
               folder + "WHEEL": b"Wheel-Version: 1.0\n",
               folder + "entry_points.txt": b"[console_scripts]\nopskit = main:app\n"}
    if change:
        change(members)
    records = io.StringIO(newline="")
    writer = csv.writer(records)
    for name, data in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, len(data)])
    writer.writerow([folder + "RECORD", "", ""])
    members[folder + "RECORD"] = records.getvalue().encode()
    wheel = tmp_path / "opskit.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return wheel, source


def test_current_runtime_inventory_and_resource_pass(tmp_path):
    wheel, source = make_wheel(tmp_path)
    assert verify(wheel, source)["runtime_files"] == 2


@pytest.mark.parametrize("name", ["plugins/private/main.py", "cache/token.txt", "core_local/private.py",
                                   ".env", "../private.txt", "core/private.txt"])
def test_extra_files_rejected_even_with_valid_record(tmp_path, name):
    wheel, source = make_wheel(tmp_path, lambda members: members.update({name: b"private"}))
    with pytest.raises(ValueError, match="unexpected files"):
        verify(wheel, source)


@pytest.mark.parametrize("change,error", [
    (lambda members: members.pop("core/locale/zh.yaml"), "missing runtime"),
    (lambda members: members.update({"main.py": b"stale"}), "differs from current source"),
    (lambda members: members.update({"opskit-1.0.0.dist-info/entry_points.txt": b"[console_scripts]\nopskit = other:app\n"}), "entry points"),
])
def test_missing_stale_or_wrong_entrypoint_rejected(tmp_path, change, error):
    wheel, source = make_wheel(tmp_path, change)
    with pytest.raises(ValueError, match=error):
        verify(wheel, source)


def test_record_checksum_tampering_rejected(tmp_path):
    wheel, source = make_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["opskit-1.0.0.dist-info/WHEEL"] = b"tampered"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="RECORD digest mismatch"):
        verify(wheel, source)
