import json
import os

import pytest

from core import native_catalog_ownership as ownership

TOKEN = "a" * 32


def create(tmp_path):
    target = tmp_path / "objects" / TOKEN
    target.mkdir(parents=True)
    ownership.record_creation(tmp_path, TOKEN)
    return target


def test_receipt_binds_exact_created_directory_and_parent(tmp_path):
    target = create(tmp_path)
    value, digest = ownership.validate(tmp_path, TOKEN)
    assert value["object"] == TOKEN and len(digest) == 64
    (target / "partial-snapshot").write_bytes(b"incomplete publication")
    assert ownership.validate(tmp_path, TOKEN) == (value, digest)
    with pytest.raises(ValueError, match="already recorded"):
        ownership.record_creation(tmp_path, TOKEN)


@pytest.mark.parametrize("change", ["replacement", "parent", "record", "hardlink"])
def test_changed_or_linked_receipts_never_authorize_object(change, tmp_path):
    target = create(tmp_path)
    receipt = tmp_path / "ownership" / (TOKEN + ".json")
    if change == "replacement":
        target.rename(target.with_name("original"))
        target.mkdir()
    elif change == "parent":
        value = json.loads(receipt.read_text())
        value["parent"][1] += 1
        receipt.write_text(json.dumps(value))
    elif change == "record":
        receipt.write_text("{}")
    else:
        os.link(receipt, tmp_path / "alias")
    with pytest.raises((OSError, ValueError, RuntimeError)):
        ownership.validate(tmp_path, TOKEN)
    assert target.exists()


def test_unrecorded_legacy_object_is_never_adopted(tmp_path):
    (tmp_path / "objects" / TOKEN).mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        ownership.validate(tmp_path, TOKEN)
    assert not (tmp_path / "ownership").exists()


def test_actual_publication_records_ownership_before_snapshot(tmp_path, monkeypatch):
    from test_native_runtime_prepare import prepare
    from core import native_runtime_catalog as catalog_module
    runtime, record = prepare(tmp_path)
    catalog = catalog_module.RuntimeCatalog(tmp_path / "catalog")
    def fail(source, destination, expected):
        ownership.validate(catalog.root, destination.parent.name)
        raise OSError("interrupted snapshot")
    monkeypatch.setattr(catalog_module, "stage", fail)
    with pytest.raises(OSError, match="interrupted"):
        catalog.publish("demo", runtime, record, check_identity=lambda *args: True)
    tokens = list((catalog.root / "objects").iterdir())
    assert len(tokens) == 1
    ownership.validate(catalog.root, tokens[0].name)
    assert not (catalog.root / "demo.json").exists()
