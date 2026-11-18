"""Secret exports publish complete private files and preserve unrelated entries."""
import json
import os
import sys

import pytest

from core.cli_secrets import export_secret, read_secret_file


def test_private_export_roundtrip_and_no_overwrite(tmp_path):
    target = tmp_path / 'credentials.json'
    data = {'secret': 'synthetic-秘密'}
    export_secret(str(target), data)
    assert json.loads(read_secret_file(str(target))) == data
    with pytest.raises(FileExistsError):
        export_secret(str(target), {'secret': 'replacement'})
    assert json.loads(read_secret_file(str(target))) == data
    assert list(tmp_path.iterdir()) == [target]


def test_serialization_failure_never_creates_output(tmp_path, monkeypatch):
    def unexpected_open(*args, **kwargs):
        pytest.fail('serialization must finish before file creation')
    monkeypatch.setattr(os, 'open', unexpected_open)
    with pytest.raises(TypeError):
        export_secret(str(tmp_path / 'credentials.json'), {'secret': object()})
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory permission boundary')
@pytest.mark.parametrize('mode', [0o777, 0o770, 0o720], ids=['world-write', 'group-write', 'group-write-only'])
def test_export_refuses_shared_writable_parent(tmp_path, mode):
    parent = tmp_path / 'shared'
    parent.mkdir(mode=mode)
    parent.chmod(mode)
    with pytest.raises(ValueError, match='directory'):
        export_secret(str(parent / 'credentials.json'), {'secret': 'synthetic'})
    assert not list(parent.iterdir())


def test_export_sync_failure_keeps_destination_absent(tmp_path, monkeypatch):
    def failed_sync(fd):
        raise OSError('synthetic sync failure')
    monkeypatch.setattr(os, 'fsync', failed_sync)
    with pytest.raises(OSError):
        export_secret(str(tmp_path / 'credentials.json'), {'secret': 'synthetic'})
    assert not list(tmp_path.iterdir())


def test_destination_created_during_sync_is_preserved(tmp_path, monkeypatch):
    target = tmp_path / 'credentials.json'
    real_sync = os.fsync
    def competitor(fd):
        if not target.exists():
            target.write_bytes(b'unrelated-file')
        return real_sync(fd)
    monkeypatch.setattr(os, 'fsync', competitor)
    with pytest.raises(FileExistsError):
        export_secret(str(target), {'secret': 'synthetic'})
    assert target.read_bytes() == b'unrelated-file'
    assert list(tmp_path.iterdir()) == [target]


def test_existing_directory_is_preserved(tmp_path):
    target = tmp_path / 'existing'
    target.mkdir()
    (target / 'keep').write_bytes(b'unrelated')
    with pytest.raises(OSError):
        export_secret(str(target), {'secret': 'synthetic'})
    assert (target / 'keep').read_bytes() == b'unrelated'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != 'nt', reason='native Windows directory DACL')
@pytest.mark.parametrize('rights', ['R', 'M'], ids=['foreign-read', 'foreign-write'])
def test_windows_parent_acl(tmp_path, rights):
    import subprocess
    parent = tmp_path / 'parent'
    parent.mkdir()
    subprocess.run(['icacls', str(parent), '/grant', '*S-1-1-0:' + rights],
                   check=True, capture_output=True, timeout=10)
    target = parent / 'credentials.json'
    if rights == 'M':
        with pytest.raises(ValueError, match='directory'):
            export_secret(str(target), {'secret': 'synthetic'})
        assert not list(parent.iterdir())
    else:
        export_secret(str(target), {'secret': 'synthetic'})
        assert json.loads(read_secret_file(str(target))) == {'secret': 'synthetic'}


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory publication sync')
def test_post_publication_failure_preserves_replacement(tmp_path, monkeypatch):
    target = tmp_path / 'credentials.json'
    real_sync = os.fsync
    def failed_directory_sync(fd):
        import stat
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            target.unlink()
            target.write_bytes(b'unrelated-replacement')
            raise OSError('synthetic directory sync failure')
        return real_sync(fd)
    monkeypatch.setattr(os, 'fsync', failed_directory_sync)
    with pytest.raises(OSError, match='synthetic'):
        export_secret(str(target), {'secret': 'synthetic'})
    assert target.read_bytes() == b'unrelated-replacement'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(sys.platform != 'darwin', reason='native inherited Darwin ACL')
def test_export_refuses_parent_extended_acl(tmp_path):
    import subprocess
    parent = tmp_path / 'acl-parent'
    parent.mkdir(mode=0o700)
    subprocess.run(['/bin/chmod', '+a', 'everyone allow read,write,add_file,file_inherit,directory_inherit', str(parent)],
                   check=True, capture_output=True, timeout=10)
    with pytest.raises(ValueError, match='ACL|directory'):
        export_secret(str(parent / 'credentials.json'), {'secret': 'synthetic'})
    assert not list(parent.iterdir())
