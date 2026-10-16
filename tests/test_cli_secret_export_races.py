"""Controlled native directory changes during export; all files are synthetic."""
import json
import os
import stat
import subprocess
import sys

import pytest

from core.cli_secrets import export_secret, read_secret_file


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory descriptor sync')
@pytest.mark.parametrize('phase', ['file', 'directory'])
def test_parent_replaced_during_sync_is_not_reported_success(tmp_path, monkeypatch, phase):
    parent = tmp_path / 'parent'
    parent.mkdir(mode=0o700)
    moved = tmp_path / 'original'
    target = parent / 'credentials.json'
    real_sync = os.fsync
    changed = False

    def replace_parent(fd):
        nonlocal changed
        result = real_sync(fd)
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if not changed and directory == (phase == 'directory'):
            changed = True
            parent.rename(moved)
            parent.mkdir(mode=0o700)
            target.write_bytes(b'unrelated-replacement')
        return result

    monkeypatch.setattr(os, 'fsync', replace_parent)
    with pytest.raises(ValueError, match='directory'):
        export_secret(str(target), {'secret': 'synthetic'})
    assert changed
    assert target.read_bytes() == b'unrelated-replacement'
    assert not list(moved.iterdir())


def test_parent_write_grant_during_export_refused(tmp_path, monkeypatch):
    parent = tmp_path / 'parent'
    parent.mkdir(mode=0o700)
    target = parent / 'credentials.json'
    real_sync = os.fsync
    changed = False

    def grant_write(fd):
        nonlocal changed
        result = real_sync(fd)
        if not changed:
            changed = True
            if os.name == 'nt':
                subprocess.run(['icacls', str(parent), '/grant', '*S-1-1-0:M'],
                               check=True, capture_output=True, timeout=10)
            else:
                parent.chmod(0o777)
        return result

    monkeypatch.setattr(os, 'fsync', grant_write)
    with pytest.raises(ValueError, match='directory'):
        export_secret(str(target), {'secret': 'synthetic'})
    assert changed
    if os.name == 'nt':
        assert not list(parent.iterdir())
    else:
        # After foreign writers gain access, portable POSIX unlink by name is
        # unsafe. Preserve a private orphan instead of deleting a possible swap.
        temporary, = parent.iterdir()
        assert temporary.name.startswith('.opskit-secret-')
        assert temporary.stat().st_mode & 0o077 == 0
        assert json.loads(read_secret_file(str(temporary))) == {'secret': 'synthetic'}
        assert not target.exists()


def test_ancestor_move_during_export(tmp_path, monkeypatch):
    ancestor = tmp_path / 'ancestor'
    parent = ancestor / 'parent'
    parent.mkdir(parents=True, mode=0o700)
    target = parent / 'credentials.json'
    moved = tmp_path / 'moved'
    real_sync = os.fsync
    changed = False
    blocked = False

    def move_ancestor(fd):
        nonlocal changed, blocked
        result = real_sync(fd)
        if not changed and not blocked:
            try:
                ancestor.rename(moved)
            except PermissionError:
                assert os.name == 'nt'
                blocked = True
            else:
                changed = True
                parent.mkdir(parents=True, mode=0o700)
                target.write_bytes(b'unrelated-replacement')
        return result

    monkeypatch.setattr(os, 'fsync', move_ancestor)
    try:
        export_secret(str(target), {'secret': 'synthetic'})
    except (ValueError, OSError):
        assert changed and not blocked
        assert target.read_bytes() == b'unrelated-replacement'
        assert not list((moved / 'parent').iterdir())
    else:
        assert blocked and not changed
        assert json.loads(read_secret_file(str(target))) == {'secret': 'synthetic'}


@pytest.mark.skipif(sys.platform != 'darwin', reason='native Darwin ACL change')
def test_parent_acl_added_during_export_refused(tmp_path, monkeypatch):
    parent = tmp_path / 'parent'
    parent.mkdir(mode=0o700)
    real_sync = os.fsync
    changed = False

    def grant_acl(fd):
        nonlocal changed
        result = real_sync(fd)
        if not changed:
            changed = True
            subprocess.run(['/bin/chmod', '+a', 'everyone allow add_file', str(parent)],
                           check=True, capture_output=True, timeout=10)
        return result

    monkeypatch.setattr(os, 'fsync', grant_acl)
    with pytest.raises(ValueError, match='ACL|directory'):
        export_secret(str(parent / 'credentials.json'), {'secret': 'synthetic'})
    assert changed
    temporary, = parent.iterdir()
    assert temporary.name.startswith('.opskit-secret-')
    assert json.loads(read_secret_file(str(temporary))) == {'secret': 'synthetic'}
    assert not (parent / 'credentials.json').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows alternate stream and reserved names')
@pytest.mark.parametrize('name', ['credentials.json:stream', 'NUL', 'trailing.', 'trailing '])
def test_windows_ambiguous_output_names_refused(tmp_path, name):
    with pytest.raises(ValueError, match='filename'):
        export_secret(str(tmp_path / name), {'secret': 'synthetic'})
    assert not list(tmp_path.iterdir())
