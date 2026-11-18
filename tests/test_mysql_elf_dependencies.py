"""Metadata-only loader dependency inspection must remain bounded and owned."""
import struct

import pytest

from core.deadline import Deadline
from scripts.verify_mysql_dependencies_native import elf_fixture
from software.recipes.mysql.elf_dependencies import dependencies, runtime_requirements


def test_x86_and_arm_metadata_without_executing(tmp_path):
    for machine in (62, 183):
        path = tmp_path / str(machine)
        path.write_bytes(elf_fixture(['libaio.so.1', 'libnuma.so.1'], machine=machine))
        actual, names, paths = dependencies(path, [32 * 1024**2])
        assert actual == machine and names == {'libaio.so.1', 'libnuma.so.1'}
        assert paths == ['$ORIGIN/../lib/private']


@pytest.mark.parametrize('change', ['magic', 'class', 'machine', 'count', 'offset', 'dynamic_size',
                                  'termination', 'string_size', 'string_pointer', 'needed_offset', 'truncated'])
def test_malformed_layout_is_rejected(tmp_path, change):
    body = bytearray(elf_fixture(['libaio.so.1']))
    if change == 'magic':
        body[0] = 0
    elif change == 'class':
        body[4] = 1
    elif change == 'machine':
        struct.pack_into('<H', body, 18, 3)
    elif change == 'count':
        struct.pack_into('<H', body, 56, 1025)
    elif change == 'offset':
        struct.pack_into('<Q', body, 32, len(body) + 1)
    elif change == 'dynamic_size':
        struct.pack_into('<Q', body, 64 + 56 + 32, 65552)
    elif change == 'termination':
        struct.pack_into('<q', body, 176 + 4 * 16, 1)
    elif change == 'string_size':
        struct.pack_into('<Q', body, 176 + 2 * 16 + 8, 8 * 1024**2 + 1)
    elif change == 'string_pointer':
        struct.pack_into('<Q', body, 176 + 16 + 8, 0x800000)
    elif change == 'needed_offset':
        struct.pack_into('<Q', body, 176 + 8, len(body) + 100)
    else:
        body = body[:63]
    path = tmp_path / 'inert'
    path.write_bytes(body)
    with pytest.raises((ValueError, struct.error)):
        dependencies(path, [32 * 1024**2])


@pytest.mark.parametrize('name', ['../outside.so', 'lib\x1b[31m.so', 'x' * 256])
def test_dependency_names_cannot_be_paths_or_terminal_control(tmp_path, name):
    path = tmp_path / 'inert'
    path.write_bytes(elf_fixture([name]))
    with pytest.raises(ValueError, match='soname'):
        dependencies(path, [32 * 1024**2])


def test_metadata_total_budget_precedes_read(tmp_path):
    path = tmp_path / 'inert'
    path.write_bytes(elf_fixture(['libaio.so.1']))
    with pytest.raises(ValueError, match='bounds'):
        dependencies(path, [63])


def layout(root, names):
    (root / 'bin').mkdir(parents=True)
    for name in ('mysql', 'mysqld', 'mysqladmin', 'mysqldump'):
        (root / 'bin' / name).write_bytes(elf_fixture(names))


def test_private_library_dependencies_are_inspected_recursively(tmp_path):
    layout(tmp_path, ['libvendor.so', 'libaio.so.1'])
    private = tmp_path / 'lib/private'
    private.mkdir(parents=True)
    (private / 'libvendor.so').write_bytes(elf_fixture(['libnuma.so.1'], runpath='$ORIGIN'))
    assert runtime_requirements(tmp_path, Deadline(5, 'test'), 62) == {'libaio.so.1', 'libnuma.so.1'}


def test_external_origin_path_is_not_inspected(tmp_path):
    root = tmp_path / 'owned'
    layout(root, ['libvendor.so'])
    for path in (root / 'bin').iterdir():
        path.write_bytes(elf_fixture(['libvendor.so'], runpath='$ORIGIN/../../outside'))
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'outside/libvendor.so').write_bytes(b'MUST_NOT_READ')
    assert runtime_requirements(root, Deadline(5, 'test'), 62) == {'libvendor.so'}


def test_runtime_architecture_must_match(tmp_path):
    layout(tmp_path, ['libaio.so.1'])
    with pytest.raises(ValueError, match='architecture'):
        runtime_requirements(tmp_path, Deadline(5, 'test'), 183)


def test_missing_companion_command_is_not_silent(tmp_path):
    layout(tmp_path, ['libaio.so.1'])
    (tmp_path / 'bin/mysqldump').unlink()
    with pytest.raises(FileNotFoundError):
        runtime_requirements(tmp_path, Deadline(5, 'test'), 62)


def test_runpath_takes_precedence_over_rpath(tmp_path):
    path = tmp_path / 'inert'
    path.write_bytes(elf_fixture(['libaio.so.1'], runpath='$ORIGIN/new', rpath='$ORIGIN/old'))
    assert dependencies(path, [32 * 1024**2])[2] == ['$ORIGIN/new']


def test_lookalike_origin_variable_is_not_expanded(tmp_path):
    layout(tmp_path, ['libvendor.so'])
    for path in (tmp_path / 'bin').iterdir():
        path.write_bytes(elf_fixture(['libvendor.so'], runpath='$ORIGINevil'))
    (tmp_path / 'binevil').mkdir()
    (tmp_path / 'binevil/libvendor.so').write_bytes(b'MUST_NOT_READ')
    assert runtime_requirements(tmp_path, Deadline(5, 'test'), 62) == {'libvendor.so'}


@pytest.mark.parametrize('kind', ['names', 'paths'])
def test_dependency_fanout_is_bounded(tmp_path, kind):
    body = elf_fixture(['lib' + str(index) + '.so' for index in range(513)]) if kind == 'names' else elf_fixture(runpath=':'.join(['$ORIGIN'] * 65))
    path = tmp_path / 'inert'
    path.write_bytes(body)
    with pytest.raises(ValueError, match='quota'):
        dependencies(path, [32 * 1024**2])
