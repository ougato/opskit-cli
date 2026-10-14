"""UV bootstrap never executes remote scripts or extracts into the user bin directory."""
import stat
import subprocess
import zipfile
import hashlib

import pytest

from software.base import InstallError
from software.recipes.python.windows import WindowsDriver


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    from software.recipes.python import common
    from software.recipes.python import uv_bootstrap
    from core import platform
    dest = tmp_path / "bin/uv.exe"
    monkeypatch.setattr(common, "uv_bin_path", lambda: dest)
    monkeypatch.setattr(uv_bootstrap.shutil, "which", lambda name: "curl.exe" if name == "curl.exe" else None)
    from types import SimpleNamespace
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="windows", arch="x86_64"))
    return WindowsDriver(), dest


def download(monkeypatch, entries, *, after=None, code=0, valid_digest=True):
    from core import process
    calls = []

    def capture(command, **kwargs):
        calls.append((command, kwargs))
        path = command[command.index("--output") + 1]
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries:
                archive.writestr(name, content)
        if valid_digest:
            from software.recipes.python.uv_release import UV_ASSET_SHA256
            from pathlib import Path
            monkeypatch.setitem(UV_ASSET_SHA256, "uv-x86_64-pc-windows-msvc.zip", hashlib.sha256(Path(path).read_bytes()).hexdigest())
        if after:
            after()
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(process, "capture", capture)
    return calls


@pytest.mark.parametrize("name", ["uv.exe", "uv-windows/uv.exe"])
def test_safe_layout_and_owned_cleanup(bootstrap, monkeypatch, name):
    driver, dest = bootstrap
    calls = download(monkeypatch, [(name, b"inert uv"), ("uvx.exe", b"inert unused")])
    assert driver.ensure_uv() == str(dest)
    assert dest.read_bytes() == b"inert uv"
    assert list(dest.parent.iterdir()) == [dest]
    command, kwargs = calls[0]
    assert command[-1].startswith("https://github.com/astral-sh/uv/")
    assert "/download/0.13.0/" in command[-1]
    assert command[command.index("--proto-redir") + 1] == "=https"
    assert "--fail" in command and "--max-filesize" in command
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["max_bytes"] == 16384


@pytest.mark.parametrize("extra", ["../escape", "a/../../escape", "..\\escape", "C:/escape", "other/uv.exe"])
def test_bad_members_reject_and_cleanup(bootstrap, monkeypatch, extra):
    driver, dest = bootstrap
    download(monkeypatch, [("uv.exe", b"inert"), (extra, b"bad")])
    with pytest.raises(InstallError):
        driver.ensure_uv()
    assert not dest.exists()
    assert not list(dest.parent.iterdir())
    assert not (dest.parent.parent / "escape").exists()


def test_link_member_rejected(bootstrap, monkeypatch):
    driver, dest = bootstrap
    member = zipfile.ZipInfo("uv.exe")
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    download(monkeypatch, [(member, b"../outside")])
    with pytest.raises(InstallError):
        driver.ensure_uv()
    assert not list(dest.parent.iterdir())


@pytest.mark.parametrize("entries,code", [([], 0), ([("not-uv.exe", b"bad")], 0), ([("uv.exe", b"partial")], 22)])
def test_missing_exact_entry_or_http_failure(bootstrap, monkeypatch, entries, code):
    driver, dest = bootstrap
    download(monkeypatch, entries, code=code)
    with pytest.raises(InstallError):
        driver.ensure_uv()
    assert not dest.exists() and not list(dest.parent.iterdir())


def test_competing_destination_is_preserved(bootstrap, monkeypatch):
    driver, dest = bootstrap
    download(monkeypatch, [("uv.exe", b"inert")], after=lambda: dest.write_bytes(b"other owner"))
    with pytest.raises(InstallError, match="refusing to overwrite"):
        driver.ensure_uv()
    assert dest.read_bytes() == b"other owner"
    assert list(dest.parent.iterdir()) == [dest]


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired, KeyboardInterrupt])
def test_timeout_or_cancel_cleans_download(bootstrap, monkeypatch, failure):
    from core import process
    driver, dest = bootstrap

    def capture(command, **kwargs):
        if failure is subprocess.TimeoutExpired:
            raise failure(command, kwargs["timeout"])
        raise failure()

    monkeypatch.setattr(process, "capture", capture)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        driver.ensure_uv()
    assert not dest.exists() and not list(dest.parent.iterdir())


def test_existing_private_uv_needs_no_download(bootstrap, monkeypatch):
    driver, dest = bootstrap
    dest.parent.mkdir()
    dest.write_bytes(b"existing")
    dest.chmod(0o755)
    monkeypatch.setattr("core.process.capture", lambda *a, **k: pytest.fail("unexpected download"))
    assert driver.ensure_uv() == str(dest)


def test_unusable_private_path_is_not_overwritten(bootstrap):
    driver, dest = bootstrap
    dest.mkdir(parents=True)
    with pytest.raises(InstallError, match="refusing to overwrite"):
        driver.ensure_uv()
    assert dest.is_dir()


def test_digest_mismatch_refuses_publication(bootstrap, monkeypatch):
    driver, dest = bootstrap
    download(monkeypatch, [("uv.exe", b"untrusted")], valid_digest=False)
    with pytest.raises(InstallError, match="integrity check failed"):
        driver.ensure_uv()
    assert not dest.exists() and not list(dest.parent.iterdir())
