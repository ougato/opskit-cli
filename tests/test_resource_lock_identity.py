"""Lock acquisition cannot trust an unavailable native file identity."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.resource_lock import ResourceIdentityError, ResourceLock


class CustomLockError(RuntimeError):
    pass


@pytest.mark.parametrize("error_type,expected", [
    (RuntimeError, ResourceIdentityError),
    (CustomLockError, CustomLockError),
])
def test_lock_rejects_unavailable_inode(monkeypatch, tmp_path, error_type, expected):
    import core.resource_lock as resource_lock

    path = tmp_path / "resource.lock"
    original_fstat = resource_lock.os.fstat
    original_lstat = Path.lstat

    def no_inode(info):
        return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                               st_dev=info.st_dev, st_ino=0,
                               st_file_attributes=getattr(info, "st_file_attributes", 0))

    def fake_fstat(fd):
        info = original_fstat(fd)
        if path.exists() and (info.st_dev, info.st_ino) == (
                original_lstat(path).st_dev, original_lstat(path).st_ino):
            return no_inode(info)
        return info

    def fake_lstat(candidate, *args, **kwargs):
        info = original_lstat(candidate, *args, **kwargs)
        return no_inode(info) if candidate == path else info

    monkeypatch.setattr(resource_lock.os, "fstat", fake_fstat)
    monkeypatch.setattr(Path, "lstat", fake_lstat)
    lock = ResourceLock(lambda: path, label="test", error_type=error_type)
    with pytest.raises(expected, match="identity"):
        with lock.hold():
            pass


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX directory link race")
def test_lock_does_not_chmod_directory_replaced_by_symlink(tmp_path, monkeypatch):
    import os
    import stat
    import subprocess
    import sys

    parent = tmp_path / "data"
    parent.mkdir()
    victim = tmp_path / "unrelated"
    victim.mkdir()
    victim.chmod(0o755)
    saved = tmp_path / "original-data"
    real_mkdir = Path.mkdir
    real_open = os.open

    def replace_after_mkdir(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        if path == parent:
            subprocess.run([sys.executable, "-c",
                            "import os,sys; os.rename(sys.argv[1],sys.argv[2]); "
                            "os.symlink(sys.argv[3],sys.argv[1])",
                            str(parent), str(saved), str(victim)], check=True, timeout=10)

    def stop_lock_file(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(parent / ".lock"):
            raise OSError("injected stop before lock file creation")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", replace_after_mkdir)
    monkeypatch.setattr(os, "open", stop_lock_file)
    with pytest.raises((OSError, RuntimeError)):
        with ResourceLock(lambda: parent / ".lock", label="test").hold():
            pass
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755


def test_lock_does_not_change_permissions_of_replaced_directory(tmp_path, monkeypatch):
    import os
    import stat
    import subprocess
    import sys

    parent = tmp_path / "data"
    parent.mkdir()
    victim = tmp_path / "unrelated"
    victim.mkdir()
    saved = tmp_path / "original-data"
    if os.name == "nt":
        from core.windows_permissions import _descriptor
        before = _descriptor(victim)[0].raw
    else:
        victim.chmod(0o755)
        before = stat.S_IMODE(victim.stat().st_mode)
    real_mkdir = Path.mkdir

    def replace_after_mkdir(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        if path == parent:
            subprocess.run([sys.executable, "-c",
                            "import os,sys; os.rename(sys.argv[1],sys.argv[2]); "
                            "os.rename(sys.argv[3],sys.argv[1])",
                            str(parent), str(saved), str(victim)], check=True, timeout=10)

    monkeypatch.setattr(Path, "mkdir", replace_after_mkdir)
    with pytest.raises((OSError, RuntimeError)):
        with ResourceLock(lambda: parent / ".lock", label="test").hold():
            pass
    after = (_descriptor(parent)[0].raw if os.name == "nt"
             else stat.S_IMODE(parent.stat().st_mode))
    assert after == before
    assert not (parent / ".lock").exists()


@pytest.mark.parametrize("error_type,expected", [
    (RuntimeError, ResourceIdentityError),
    (CustomLockError, CustomLockError),
])
def test_new_lock_directory_does_not_change_competing_directory(tmp_path, monkeypatch, error_type, expected):
    import os
    import stat
    import subprocess
    import sys

    parent = tmp_path / "data"
    victim = tmp_path / "unrelated"
    victim.mkdir()
    if os.name == "nt":
        from core.windows_permissions import _descriptor
        before = _descriptor(victim)[0].raw
    else:
        victim.chmod(0o755)
        before = stat.S_IMODE(victim.stat().st_mode)
    real_mkdir = Path.mkdir

    def compete_before_mkdir(path, *args, **kwargs):
        if path == parent:
            subprocess.run([sys.executable, "-c", "import os,sys; os.rename(sys.argv[1],sys.argv[2])",
                            str(victim), str(parent)], check=True, timeout=10)
        return real_mkdir(path, *args, **kwargs)

    if os.name == "nt":
        from core import resource_lock_permissions
        create = resource_lock_permissions._create_windows_directory

        def compete_before_create(path):
            subprocess.run([sys.executable, "-c", "import os,sys; os.rename(sys.argv[1],sys.argv[2])",
                            str(victim), str(parent)], check=True, timeout=10)
            return create(path)

        monkeypatch.setattr(resource_lock_permissions, "_create_windows_directory", compete_before_create)
    else:
        monkeypatch.setattr(Path, "mkdir", compete_before_mkdir)
    with pytest.raises(expected, match="appeared"):
        with ResourceLock(lambda: parent / ".lock", label="test", error_type=error_type).hold():
            pass
    after = (_descriptor(parent)[0].raw if os.name == "nt"
             else stat.S_IMODE(parent.stat().st_mode))
    assert after == before
    assert not (parent / ".lock").exists()


def test_new_lock_directory_creates_missing_ancestors(tmp_path):
    path = tmp_path / "nested" / "private" / ".lock"
    with ResourceLock(lambda: path, label="test").hold():
        assert path.is_file()


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows extended path behavior')
def test_long_windows_lock_is_reentrant_and_rejects_hardlinks(tmp_path):
    import os
    from core.paths import absolute_path

    parent = tmp_path / ('p' * 75)
    parent.mkdir()
    path = parent / (('l' * 120) + '.lock')
    assert len(str(path)) >= 260
    lock = ResourceLock(lambda: path, label='long path test')
    with lock.hold():
        with lock.hold():
            assert absolute_path(path).is_file()
    # A second path to the lock must still be rejected before acquisition.
    alias = tmp_path / 'user-data'
    os.link(absolute_path(path), alias)
    before = alias.read_bytes()
    with pytest.raises(RuntimeError, match='hard links'):
        with lock.hold():
            pytest.fail('hard-linked lock accepted')
    assert alias.read_bytes() == before
    assert absolute_path(path).read_bytes() == before


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows extended path behavior')
def test_extended_path_preserves_unc_and_existing_prefix():
    from core.paths import absolute_path
    unc = r'\\server\share\folder\item'
    assert str(absolute_path(unc)) == r'\\?\UNC\server\share\folder\item'
    already = r'\\?\C:\long\item'
    assert str(absolute_path(already)) == already


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows extended path behavior')
@pytest.mark.parametrize('length', [248, 255, 258, 259])
def test_windows_near_limit_lock_creates_ancestors_and_shares_legacy_lock(tmp_path, length):
    import subprocess
    import sys

    # The uncreated ancestor and lock name together cross the directory API's
    # shorter limit while remaining addressable by an older unprefixed caller.
    padding = length - len(str(tmp_path)) - len('/inner/.l') - 1
    assert padding > 0
    path = tmp_path / ('d' * padding) / 'inner' / '.l'
    assert len(str(path)) == length
    code = (
        'import errno,msvcrt,os,sys; '
        'fd=os.open(sys.argv[1],os.O_RDWR); '
        'exec("try:\\n msvcrt.locking(fd,msvcrt.LK_NBLCK,1)\\n'
        'except OSError as e:\\n sys.exit(17 if e.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK) else 18)\\n'
        'else:\\n sys.exit(0)")'
    )
    with ResourceLock(lambda: path, label='legacy compatibility').hold():
        old_process = subprocess.run([sys.executable, '-c', code, str(path)],
                                     capture_output=True, timeout=10)
        assert old_process.returncode == 17, old_process.stderr
    from core.resource_lock import ResourceBusyError
    holder_code = (
        'import msvcrt,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); '
        'msvcrt.locking(fd,msvcrt.LK_NBLCK,1); '
        'print("locked",flush=True); sys.stdin.readline()'
    )
    holder = subprocess.Popen([sys.executable, '-c', holder_code, str(path)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'locked'
        with pytest.raises(ResourceBusyError):
            with ResourceLock(lambda: path, label='legacy compatibility').hold():
                pytest.fail('lock held by an unprefixed caller was acquired')
    finally:
        holder.communicate(input='release\n', timeout=10)


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows extended path behavior')
def test_windows_lock_counts_non_bmp_as_utf16_units(tmp_path):
    from core.paths import absolute_path
    padding = 230 - len(str(tmp_path)) - len('/inner/.l') - 1 - 30
    assert padding > 0
    path = tmp_path / (('d' * padding) + ('😀' * 30)) / 'inner' / '.l'
    assert len(str(path)) < 248
    assert len(str(path).encode('utf-16-le')) // 2 >= 248
    with ResourceLock(lambda: path, label='unicode path').hold():
        assert absolute_path(path).is_file()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows DACL")
def test_new_lock_directory_preserves_existing_private_acl_policy(tmp_path):
    from core.secrets import restrict_permissions
    from core.windows_permissions import _descriptor

    reference = tmp_path / "reference"
    reference.mkdir()
    restrict_permissions(reference)
    parent = tmp_path / "new"
    with ResourceLock(lambda: parent / ".lock", label="test").hold():
        assert _descriptor(parent)[0].raw == _descriptor(reference)[0].raw


@pytest.mark.xfail(__import__("os").name != "nt", strict=True,
                   reason="POSIX new directory can be replaced before it is opened")
def test_new_lock_directory_replaced_after_mkdir_does_not_change_permissions(tmp_path, monkeypatch):
    import os
    import stat
    import subprocess
    import sys

    parent = tmp_path / "data"
    victim = tmp_path / "unrelated"
    saved = tmp_path / "created-data"
    victim.mkdir()
    if os.name == "nt":
        from core.windows_permissions import _descriptor
        before = _descriptor(victim)[0].raw
    else:
        victim.chmod(0o755)
        before = stat.S_IMODE(victim.stat().st_mode)
    real_mkdir = Path.mkdir

    def replace_after_mkdir(path, *args, **kwargs):
        result = real_mkdir(path, *args, **kwargs)
        if path == parent:
            subprocess.run([sys.executable, "-c",
                            "import os,sys; os.rename(sys.argv[1],sys.argv[2]); "
                            "os.rename(sys.argv[3],sys.argv[1])",
                            str(parent), str(saved), str(victim)], check=True, timeout=10)
        return result

    if os.name == "nt":
        from core import resource_lock_permissions
        create = resource_lock_permissions._create_windows_directory

        def replace_after_create(path):
            descriptor = create(path)
            try:
                subprocess.run([sys.executable, "-c",
                                "import os,sys; os.rename(sys.argv[1],sys.argv[2]); "
                                "os.rename(sys.argv[3],sys.argv[1])",
                                str(parent), str(saved), str(victim)], check=True, timeout=10)
            except BaseException:
                os.close(descriptor)
                raise
            return descriptor

        monkeypatch.setattr(resource_lock_permissions, "_create_windows_directory", replace_after_create)
    else:
        monkeypatch.setattr(Path, "mkdir", replace_after_mkdir)
    try:
        with ResourceLock(lambda: parent / ".lock", label="test").hold():
            pass
    except ResourceIdentityError:
        pass
    after = (_descriptor(parent)[0].raw if os.name == "nt"
             else stat.S_IMODE(parent.stat().st_mode))
    assert after == before
    assert not (parent / ".lock").exists()


def test_concurrent_first_lock_directory_creation_fails_closed(tmp_path):
    import subprocess
    import sys

    code = '''
import os, sys, time
from pathlib import Path
from core.resource_lock import ResourceIdentityError, ResourceLock
from core import resource_lock_permissions
root, child = Path(sys.argv[1]), sys.argv[2]
parent = root / 'new' / 'private'
ready = root / ('ready-' + child)
other = root / ('ready-' + ('b' if child == 'a' else 'a'))
original = Path.mkdir
def synchronized_mkdir(path, *args, **kwargs):
    if path == parent:
        ready.write_text('ready')
        deadline = time.monotonic() + 15
        while not other.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('competing process did not reach mkdir')
            time.sleep(.01)
    return original(path, *args, **kwargs)
Path.mkdir = synchronized_mkdir
if os.name == 'nt':
    create = resource_lock_permissions._create_windows_directory
    def synchronized_create(path):
        ready.write_text('ready')
        deadline = time.monotonic() + 15
        while not other.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('competing process did not reach create')
            time.sleep(.01)
        return create(path)
    resource_lock_permissions._create_windows_directory = synchronized_create
try:
    with ResourceLock(lambda: parent / '.lock', label='test').hold():
        print('acquired')
except ResourceIdentityError:
    print('raced')
'''
    processes = []
    try:
        for child in ("a", "b"):
            processes.append(subprocess.Popen(
                [sys.executable, "-c", code, str(tmp_path), child],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ))
        outcomes = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=25)
            assert process.returncode == 0, stderr
            outcomes.append(stdout.strip())
        assert sorted(outcomes) == ["acquired", "raced"]
        assert (tmp_path / "new" / "private" / ".lock").is_file()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
