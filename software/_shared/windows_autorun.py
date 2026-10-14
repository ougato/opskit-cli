"""Retire only an exactly bound platform-owned legacy cmd AutoRun."""
from pathlib import Path
from software._shared.shell_path import _profile_text, _publish_profile
from software._shared.mutation import versioned_mutation
from software.base import InstallError

_COMMAND_PROCESSOR = r'Software\Microsoft\Command Processor'


def _query(registry, key):
    try:
        return registry.QueryValueEx(key, 'AutoRun')
    except FileNotFoundError:
        return None


@versioned_mutation
def retire_legacy_autorun(path, known_paths, begin, end):
    import winreg
    path = Path(path)
    path.absolute().relative_to((Path.home() / '.opskit').absolute())
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _COMMAND_PROCESSOR, 0,
                             winreg.KEY_READ | winreg.KEY_SET_VALUE)
    except FileNotFoundError:
        return
    try:
        expected_binding = (str(path), winreg.REG_SZ)
        if _query(winreg, key) != expected_binding:
            return
        previous = _profile_text(path)
        expected = set()
        for directory in known_paths:
            body = f'@echo off\r\n@{begin}\r\n@set PATH={directory};%PATH%\r\n@{end}\r\n'
            expected.update((body, body.replace('\r\n', '\r\r\n')))
        if previous not in expected:
            raise FileExistsError('refusing to retire an unmanaged AutoRun script')
        if _query(winreg, key) != expected_binding or _profile_text(path) != previous:
            raise InstallError('AutoRun changed before retirement')
        winreg.DeleteValue(key, 'AutoRun')
        try:
            path.unlink()
        except BaseException:
            try:
                if _query(winreg, key) is not None:
                    raise InstallError('AutoRun changed during rollback')
                current = _profile_text(path)
                if current is None:
                    _publish_profile(path, previous)
                elif current != previous:
                    raise InstallError('AutoRun script changed during rollback')
                winreg.SetValueEx(key, 'AutoRun', 0, expected_binding[1], expected_binding[0])
            except Exception:
                raise InstallError('AutoRun retirement failed; rollback incomplete') from None
            raise
    finally:
        winreg.CloseKey(key)
