import hashlib
import importlib.util
import io
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location(
    "install_windows_compiler", Path(__file__).resolve().parents[1] / "scripts/install_windows_compiler.py")
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)


def archive_bytes(name="mingw64/bin/gcc.exe"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, b"known synthetic compiler, never executed")
    return output.getvalue()


def test_digest_failure_never_extracts_or_produces_receipt(tmp_path):
    source = tmp_path / "compiler.zip"
    source.write_bytes(archive_bytes())
    target = tmp_path / "cache"
    with pytest.raises(ValueError, match="SHA256"):
        compiler.install(target, source)
    assert list(target.iterdir()) == []


def test_unreviewed_download_source_does_not_create_cache(tmp_path):
    target = tmp_path / "cache"
    with pytest.raises(ValueError, match="download source"):
        compiler.install(target, source="https://unreviewed.example/compiler")
    assert not target.exists()


def test_api_download_still_requires_pinned_digest_before_extraction(tmp_path, monkeypatch):
    from contextlib import contextmanager
    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def iter_raw(self, **kwargs):
            yield archive_bytes()

    @contextmanager
    def stream(method, url, **kwargs):
        assert method == "GET" and url == compiler.ASSET_API
        assert kwargs["headers"]["Accept"] == "application/octet-stream"
        yield Response()

    monkeypatch.setattr(compiler.httpx, "stream", stream)
    target = tmp_path / "cache"
    with pytest.raises(ValueError, match="SHA256"):
        compiler.install(target, source="api")
    assert not (target / "downloads").exists()
    assert not (target / "compiler-receipt.json").exists()


@pytest.mark.parametrize("name", ["../gcc.exe", "/mingw64/bin/gcc.exe", "mingw64/../gcc.exe",
                                 "mingw64/bin/evil:stream", "mingw64/bin/evil. ", "mingw64\\gcc.exe"])
def test_unsafe_archive_inventory_is_rejected(name):
    with zipfile.ZipFile(io.BytesIO(archive_bytes(name))) as archive:
        with pytest.raises(ValueError):
            compiler.members(archive)


def test_reviewed_archive_installs_only_into_new_private_cache(tmp_path, monkeypatch):
    content = archive_bytes()
    source = tmp_path / "compiler.zip"
    source.write_bytes(content)
    monkeypatch.setattr(compiler, "SIZE", len(content))
    monkeypatch.setattr(compiler, "SHA256", hashlib.sha256(content).hexdigest())
    target = tmp_path / "cache"
    executable = compiler.install(target, source)
    assert executable.read_bytes() == b"known synthetic compiler, never executed"
    assert (target / "compiler-receipt.json").is_file()
    with pytest.raises(ValueError, match="new and canonical"):
        compiler.install(target, source)
