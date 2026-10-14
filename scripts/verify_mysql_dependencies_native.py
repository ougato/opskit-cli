"""Native Linux driver verification with owned, controlled executable fixtures.

Never runs a real package manager or a downloaded MySQL program. All fixture
commands and installation directories belong to a temporary verification root.
"""
import io
import json
import os
import platform
from pathlib import Path
import sys
import struct
import tarfile
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SONAMES = ('libaio.so.1', 'libncurses.so.5', 'libtinfo.so.5')


def elf_fixture(names=SONAMES, runpath='$ORIGIN/../lib/private', machine=62, rpath=None):
    """Inert ELF metadata fixture: contains no executable instruction payload."""
    strings = bytearray(b'\0')
    entries = []
    for name in names:
        entries.append((1, len(strings)))
        strings.extend(name.encode('ascii') + b'\0')
    path_offset = len(strings)
    strings.extend(runpath.encode('ascii') + b'\0')
    if rpath is not None:
        entries.append((15, len(strings)))
        strings.extend(rpath.encode('ascii') + b'\0')
    string_offset = 176 + (len(entries) + 4) * 16
    entries.extend([(5, 0x400000 + string_offset), (10, len(strings)), (29, path_offset), (0, 0)])
    dynamic = b''.join(struct.pack('<qQ', *entry) for entry in entries)
    size = string_offset + len(strings)
    header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + b'\0' * 9,
                         2, machine, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    loads = struct.pack('<IIQQQQQQ', 1, 4, 0, 0x400000, 0x400000, size, size, 4096)
    table = struct.pack('<IIQQQQQQ', 2, 4, 176, 0x400000 + 176, 0, len(dynamic), len(dynamic), 8)
    return header + loads + table + dynamic + strings
TOOL = '''import json, os, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['MYSQL_VERIFY_ROOT'])
mode = os.environ['MYSQL_VERIFY_MODE']
if Path(sys.argv[0]).name == 'ldconfig':
    if mode == 'check_failure':
        print('SECRET-check-output')
        sys.exit(7)
    print((root / 'cache').read_text())
else:
    (root / 'arguments.json').write_text(json.dumps(sys.argv[1:]))
    if mode == 'stall':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        (root / 'child.json').write_text(json.dumps({'pid': child.pid}))
        (root / 'entered').write_text('ready')
        time.sleep(30)
    elif mode == 'failure':
        print('SECRET-package-output')
        sys.exit(100)
    elif mode == 'output':
        print('SECRET' * 50000)
    elif mode == 'success':
        names = ('libaio.so.1', 'libncurses.so.5', 'libtinfo.so.5')
        architecture = os.environ['MYSQL_VERIFY_ARCH']
        (root / 'cache').write_text('\\n'.join(name + ' (libc6,' + architecture + ') => /fixture/' + name for name in names))
'''


def verify():
    if not sys.platform.startswith('linux'):
        raise RuntimeError('native Linux verification required')
    if os.statvfs('/tmp').f_flag & os.ST_NOEXEC:
        raise RuntimeError('controlled executable fixtures require an executable owned temporary filesystem')
    sys.path.insert(0, str(ROOT))
    results = []
    with tempfile.TemporaryDirectory(prefix='mysql-dependency-verification-') as temporary:
        root = Path(temporary)
        os.environ['OPSKIT_DATA_DIR'] = str(root / 'platform-state')
        from software.recipes.mysql import common, linux
        from software.base import InstallError
        if Path(linux.__file__).resolve() != ROOT / 'software/recipes/mysql/linux.py':
            raise RuntimeError('verification imported a different driver')
        tools = root / 'tools'
        tools.mkdir()
        for name in ('ldconfig', 'apt-get'):
            target = tools / name
            target.write_text('#!' + sys.executable + '\n' + TOOL, encoding='utf-8')
            target.chmod(0o755)
        os.environ['PATH'] = str(tools) + os.pathsep + os.environ.get('PATH', '')
        for mode in ('present', 'present_modern', 'success', 'failure', 'unresolved', 'check_failure', 'stall', 'output'):
            work = root / mode
            work.mkdir()
            os.environ['MYSQL_VERIFY_ROOT'] = str(work)
            os.environ['MYSQL_VERIFY_MODE'] = mode
            architecture = 'AArch64' if platform.machine().lower() in ('aarch64', 'arm64') else 'x86-64'
            os.environ['MYSQL_VERIFY_ARCH'] = architecture
            modern = ('libaio.so.1', 'libnuma.so.1', 'libncurses.so.6', 'libtinfo.so.6')
            names = SONAMES if mode == 'present' else modern if mode == 'present_modern' else ('libaio.so.10', *SONAMES[1:])
            (work / 'cache').write_text('\n'.join(name + ' (libc6,' + architecture + ') => /fixture/' + name for name in names))
            old = work / 'mysql-old'
            old.mkdir()
            (old / 'data').write_bytes(b'owned-existing-database')
            destination = work / 'mysql-new'
            package = work / 'mysql.tar.gz'
            with tarfile.open(package, 'w:gz') as archive:
                machine = 183 if platform.machine().lower() in ('aarch64', 'arm64') else 62
                body = elf_fixture(modern if mode == 'present_modern' else SONAMES, machine=machine)
                for name in ('mysql', 'mysqld', 'mysqladmin', 'mysqldump'):
                    member = tarfile.TarInfo('mysql/bin/' + name)
                    member.size = len(body)
                    archive.addfile(member, io.BytesIO(body))
            started = time.monotonic()
            error = None
            with patch.object(common, 'mysql_version_dir', lambda version: destination), \
                 patch.object(common, 'mysql_bin_dir', lambda version: destination / 'bin'), \
                 patch.object(linux, 'DEPENDENCY_TIMEOUT', 3 if mode == 'stall' else 180):
                try:
                    linux.LinuxDriver().install_tarball('1', package)
                except InstallError as failure:
                    error = str(failure)
            elapsed = time.monotonic() - started
            if mode in ('present', 'present_modern', 'success'):
                if error is not None or (destination / 'bin/mysql').read_bytes() != body:
                    raise RuntimeError('valid fixture did not publish: ' + mode + ' ' + str(error))
            elif error is None or destination.exists() or 'SECRET' in error:
                raise RuntimeError('dependency failure published or leaked output: ' + mode)
            if (old / 'data').read_bytes() != b'owned-existing-database' or list(work.glob('.opskit-mysql-*')):
                raise RuntimeError('old state/staging cleanup mismatch: ' + mode)
            item = {'mode': mode, 'elapsed_seconds': elapsed, 'failure': error,
                    'published': destination.exists(), 'old_data_preserved': True, 'staging_removed': True}
            if mode in ('present', 'present_modern') and (work / 'arguments.json').exists():
                raise RuntimeError('present libraries triggered package installation')
            if mode == 'success':
                if json.loads((work / 'arguments.json').read_text()) != ['install', '-y', 'libaio1']:
                    raise RuntimeError('similar soname bypassed dependency installation')
                item['exact_soname_rechecked'] = True
            if mode == 'stall':
                if not (work / 'entered').exists() or not 2 <= elapsed < 8:
                    raise RuntimeError('blocked stage/deadline was not exercised')
                pid = json.loads((work / 'child.json').read_text())['pid']
                status = Path('/proc') / str(pid) / 'stat'
                state = status.read_text().rpartition(')')[2].split()[0] if status.exists() else 'absent'
                if state not in ('absent', 'Z'):
                    raise RuntimeError('live descendant survived timeout')
                item['descendant_state_after_timeout'] = state
                item['blocked_stage_entered'] = True
            results.append(item)
    return {'platform': sys.platform, 'python': sys.version.split()[0], 'uid': os.getuid(),
            'cases': results, 'scope': 'real Linux driver/process/archive publication; controlled tools and inert tar; no real package install or MySQL execution'}


if __name__ == '__main__':
    print(json.dumps(verify()))
