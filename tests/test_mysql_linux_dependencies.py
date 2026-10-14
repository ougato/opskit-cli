"""Dependency success must mean exact libraries, bounded work and safe publication."""
import io
import subprocess
import tarfile
import platform
from types import SimpleNamespace

import pytest

from software.base import InstallError
from software.recipes.mysql import linux
from scripts.verify_mysql_dependencies_native import elf_fixture

SONAMES = ('libaio.so.1', 'libncurses.so.5', 'libtinfo.so.5')


def host_elf(names=SONAMES):
    machine = 183 if platform.machine().lower() in ('aarch64', 'arm64') else 62
    return elf_fixture(names, machine=machine)


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / 'layout'
    (root / 'bin').mkdir(parents=True)
    for name in ('mysql', 'mysqld', 'mysqladmin', 'mysqldump'):
        (root / 'bin' / name).write_bytes(host_elf())
    return root


def cache(*names):
    architecture = 'AArch64' if platform.machine().lower() in ('aarch64', 'arm64') else 'x86-64'
    return '\n'.join(name + ' (libc6,' + architecture + ') => /fake/' + name for name in names)


def configure(monkeypatch, manager='apt-get', initial='', final=None, install_code=0):
    calls = []
    monkeypatch.setattr(linux.shutil, 'which', lambda name: '/fake/' + name if name in {'ldconfig', manager} else None)
    snapshots = iter([initial, initial if final is None else final])
    def capture(arguments, **kwargs):
        calls.append((arguments, kwargs))
        assert 0 < kwargs['timeout'] <= linux.DEPENDENCY_TIMEOUT
        assert kwargs['stdin'] == subprocess.DEVNULL
        if arguments[-1] == '-p':
            assert kwargs['timeout'] <= 5
            return SimpleNamespace(returncode=0, stdout=next(snapshots))
        return SimpleNamespace(returncode=install_code, stdout='SECRET-package-output')
    monkeypatch.setattr(linux, 'capture', capture)
    return calls


def test_complete_cache_uses_one_snapshot_without_install(monkeypatch, layout):
    calls = configure(monkeypatch, initial=cache(*SONAMES))
    linux._ensure_linux_deps(layout)
    assert len(calls) == 1


def test_modern_actual_requirements_do_not_install_old_curses(monkeypatch, layout):
    modern = ('libaio.so.1', 'libnuma.so.1', 'libncurses.so.6', 'libtinfo.so.6')
    for path in (layout / 'bin').iterdir():
        path.write_bytes(host_elf(modern))
    calls = configure(monkeypatch, initial=cache('libaio.so.1'), final=cache(*modern))
    linux._ensure_linux_deps(layout)
    assert calls[1][0] == ['/fake/apt-get', 'install', '-y', 'libnuma1', 'libncurses6', 'libtinfo6']


def test_unknown_missing_library_does_not_trigger_arbitrary_install(monkeypatch, layout):
    for path in (layout / 'bin').iterdir():
        path.write_bytes(host_elf(['libcustom.so.99']))
    calls = configure(monkeypatch)
    with pytest.raises(InstallError, match='manual provisioning'):
        linux._ensure_linux_deps(layout)
    assert len(calls) == 1


def test_architecture_marker_in_library_path_does_not_satisfy_abi(monkeypatch, layout):
    wrong = '\n'.join(name + ' (libc6) => /fake/x86-64/AArch64/' + name for name in SONAMES)
    calls = configure(monkeypatch, initial=wrong, final=cache(*SONAMES))
    linux._ensure_linux_deps(layout)
    assert len(calls) == 3 and calls[1][0][0] == '/fake/apt-get'


@pytest.mark.parametrize('similar', ['libaio.so.10', 'prefix-libaio.so.1', 'libaio.so.1t64'])
def test_similar_soname_does_not_satisfy_requirement(monkeypatch, similar, layout):
    calls = configure(monkeypatch, initial=cache(similar, *SONAMES[1:]), final=cache(*SONAMES))
    linux._ensure_linux_deps(layout)
    assert calls[1][0] == ['/fake/apt-get', 'install', '-y', 'libaio1']
    assert len(calls) == 3


@pytest.mark.parametrize(('manager', 'packages'), [
    ('apt-get', ['libaio1', 'libncurses5', 'libtinfo5']),
    ('dnf', ['libaio', 'ncurses-libs']), ('yum', ['libaio', 'ncurses-libs']),
    ('zypper', ['libaio1', 'libncurses5']), ('pacman', ['libaio', 'ncurses']),
    ('apk', ['libaio', 'ncurses-libs']),
])
def test_manager_candidates_are_deduplicated_and_success_is_rechecked(monkeypatch, manager, packages, layout):
    calls = configure(monkeypatch, manager, final=cache(*SONAMES))
    linux._ensure_linux_deps(layout)
    assert calls[1][0][-len(packages):] == packages
    assert calls[1][0][0] == '/fake/' + manager
    assert len(calls) == 3 and calls[-1][0] == ['/fake/ldconfig', '-p']


def test_package_manager_failure_is_not_silent_or_exposed(monkeypatch, layout):
    calls = configure(monkeypatch, install_code=100)
    with pytest.raises(InstallError, match='installation failed via apt-get') as failure:
        linux._ensure_linux_deps(layout)
    assert 'SECRET' not in str(failure.value) and len(calls) == 2


def test_successful_manager_with_missing_libraries_is_failure(monkeypatch, layout):
    configure(monkeypatch, final=cache('libaio.so.10'))
    with pytest.raises(InstallError, match='libraries still missing: libaio.so.1'):
        linux._ensure_linux_deps(layout)


def test_no_manager_is_explicit_failure(monkeypatch, layout):
    calls = configure(monkeypatch, manager=None)
    with pytest.raises(InstallError, match='no supported package manager'):
        linux._ensure_linux_deps(layout)
    assert len(calls) == 1


@pytest.mark.parametrize('error', [OSError('SECRET'), RuntimeError('SECRET'),
                                  subprocess.TimeoutExpired('SECRET', 1), KeyboardInterrupt()])
def test_control_failure_or_cancel_does_not_become_success(monkeypatch, error, layout):
    configure(monkeypatch)
    monkeypatch.setattr(linux, 'capture', lambda *args, **kwargs: (_ for _ in ()).throw(error))
    expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else InstallError
    with pytest.raises(expected) as failure:
        linux._ensure_linux_deps(layout)
    assert 'SECRET' not in str(failure.value)


def test_shared_budget_rejects_late_package_manager_success(monkeypatch, layout):
    from core import deadline
    tick = [0]
    monkeypatch.setattr(deadline, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    calls = configure(monkeypatch, final=cache(*SONAMES))
    original = linux.capture
    def late(arguments, **kwargs):
        result = original(arguments, **kwargs)
        tick[0] = 179 if arguments[-1] == '-p' else 181
        return result
    monkeypatch.setattr(linux, 'capture', late)
    with pytest.raises(InstallError, match='exceeded its deadline'):
        linux._ensure_linux_deps(layout)
    assert calls[1][1]['timeout'] == 1


@pytest.mark.parametrize('cancel', [False, True])
def test_dependency_failure_preserves_other_versions_and_never_publishes(tmp_path, monkeypatch, cancel):
    from software.recipes.mysql import common
    destination = tmp_path / 'mysql-new'
    previous = tmp_path / 'mysql-old'
    previous.mkdir()
    (previous / 'data').write_bytes(b'existing database')
    monkeypatch.setattr(common, 'mysql_version_dir', lambda version: destination)
    monkeypatch.setattr(common, 'mysql_bin_dir', lambda version: destination / 'bin')
    calls = configure(monkeypatch, install_code=100)
    if cancel:
        monkeypatch.setattr(linux, 'capture', lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    source = tmp_path / 'mysql.tar.gz'
    with tarfile.open(source, 'w:gz') as archive:
        body = host_elf()
        for name in ('mysql', 'mysqld', 'mysqladmin', 'mysqldump'):
            member = tarfile.TarInfo('mysql/bin/' + name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    with pytest.raises(KeyboardInterrupt if cancel else InstallError):
        linux.LinuxDriver().install_tarball('1', source)
    assert not destination.exists()
    if not cancel:
        assert len(calls) == 2 and calls[1][0][0] == '/fake/apt-get'
    assert (previous / 'data').read_bytes() == b'existing database'
    assert not list(tmp_path.glob('.opskit-mysql-*'))
