"""Executable tool acquisition must reject drift before creating a usable cache."""
from contextlib import nullcontext
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
import pytest
import yaml

from scripts import install_windows_build_tools as tools


@pytest.fixture
def archive(monkeypatch):
    files = {"depends.exe": b"owned executable fixture", "depends.dll": b"owned library fixture",
             "depends.chm": b"owned documentation fixture"}
    def make(entries):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as z:
            for name, content in entries:
                z.writestr(name, content)
        data = output.getvalue()
        monkeypatch.setattr(tools, "ARCHIVE_SIZE", len(data))
        monkeypatch.setattr(tools, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())
        return data
    monkeypatch.setattr(tools, "MEMBERS", {
        name: (len(content), hashlib.sha256(content).hexdigest()) for name, content in files.items()})
    return files, make


def test_wrong_archive_never_creates_cache(tmp_path):
    payload = tmp_path / "bad.zip"
    payload.write_bytes(b"unreviewed")
    cache = tmp_path / "new-cache"
    with pytest.raises(ValueError, match="digest or size"):
        tools.install(cache, payload)
    assert not cache.exists()


def test_existing_cache_never_downloads_or_overwrites(tmp_path, monkeypatch):
    marker = tmp_path / "depends.exe"
    marker.write_bytes(b"existing")
    monkeypatch.setattr(tools, "download", lambda: pytest.fail("existing cache"))
    with pytest.raises(ValueError, match="new and canonical"):
        tools.install(tmp_path)
    assert marker.read_bytes() == b"existing"


def test_offline_install_has_exact_inventory_and_receipt(tmp_path, archive):
    files, make = archive
    payload = tmp_path / "reviewed.zip"
    payload.write_bytes(make(files.items()))
    cache = tools.install(tmp_path / "cache", payload)
    installed = cache / "downloads/depends/x86_64"
    assert {p.name: p.read_bytes() for p in installed.iterdir()} == files
    assert (cache / "opskit-tools.json").is_file()


@pytest.mark.parametrize("kind", ["escape", "extra", "missing", "duplicate", "member-drift"])
def test_invalid_inventory_or_member_never_creates_cache(tmp_path, archive, kind):
    files, make = archive
    entries = list(files.items())
    if kind == "escape":
        entries[0] = ("../depends.exe", entries[0][1])
    elif kind == "extra":
        entries.append(("extra.exe", b"unreviewed"))
    elif kind == "missing":
        entries.pop()
    elif kind == "duplicate":
        entries.append(entries[0])
    else:
        entries[0] = (entries[0][0], b"X" * len(entries[0][1]))
    if kind == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate"):
            data = make(entries)
    else:
        data = make(entries)
    payload = tmp_path / "bad.zip"
    payload.write_bytes(data)
    cache = tmp_path / "cache"
    with pytest.raises(ValueError):
        tools.install(cache, payload)
    assert not cache.exists()
    assert not (tmp_path / "depends.exe").exists()


@pytest.mark.parametrize("kind", ["redirect", "oversize", "deadline"])
def test_download_rejects_redirect_size_and_deadline(monkeypatch, kind):
    status = 302 if kind == "redirect" else 200
    response = httpx.Response(status, stream=httpx.ByteStream(b"0123456789"),
                              request=httpx.Request("GET", tools.URL))
    def stream(*args, **kwargs):
        assert args == ("GET", tools.URL)
        assert kwargs["follow_redirects"] is False
        assert kwargs["headers"] == {"Accept-Encoding": "identity"}
        return nullcontext(response)
    monkeypatch.setattr(tools.httpx, "stream", stream)
    if kind == "oversize":
        monkeypatch.setattr(tools, "ARCHIVE_SIZE", 3)
    elif kind == "deadline":
        clock = iter([1, 32])
        monkeypatch.setattr(tools, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        tools.download()


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "identity, gzip"])
def test_encoded_response_rejected_before_body_is_read(monkeypatch, encoding):
    class UnreadBody(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("encoded body must not be read or decompressed")
            yield b""

    response = httpx.Response(200, headers={"Content-Encoding": encoding},
                              stream=UnreadBody(), request=httpx.Request("GET", tools.URL))
    monkeypatch.setattr(tools.httpx, "stream", lambda *a, **kw: nullcontext(response))
    with pytest.raises(ValueError, match="encoded"):
        tools.download()


def test_download_preserves_exact_unencoded_bytes(monkeypatch):
    content = b"raw archive bytes" * 8192
    response = httpx.Response(200, stream=httpx.ByteStream(content),
                              request=httpx.Request("GET", tools.URL))
    monkeypatch.setattr(tools.httpx, "stream", lambda *a, **kw: nullcontext(response))
    assert tools.download() == content


def test_workflow_provisions_before_compiling_from_locked_environment():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["build"]["steps"]
    provision = next(i for i, step in enumerate(steps) if step.get("name") == "provision reviewed Windows build tool")
    build = next(i for i, step in enumerate(steps) if step.get("name") == "build (non-manylinux)")
    assert provision < build
    step = steps[provision]
    assert step["if"] == "runner.os == 'Windows'"
    assert 'os.environ["OPSKIT_BUILD_PYTHON"]' in step["run"]
    assert "scripts/install_windows_build_tools.py" in step["run"]
    assert "NUITKA_CACHE_DIR_DOWNLOADS=" in step["run"]
