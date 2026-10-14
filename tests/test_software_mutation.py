"""Real process contention, release and nested version mutations."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from software._shared import mutation
from software.base import InstallError


def _child(source, path):
    return subprocess.Popen([sys.executable, "-u", "-c", source, str(path)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


HOLDER = """
import sys
from pathlib import Path
from software._shared import mutation
mutation.lock_path = lambda: Path(sys.argv[1])
with mutation.mutation_lock():
    print('ready', flush=True)
    sys.stdin.read()
"""


@pytest.mark.parametrize("abrupt", [False, True])
def test_second_process_denied_then_lock_released_on_exit(abrupt):
    path = mutation.lock_path()
    child = _child(HOLDER, path)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(InstallError, match="busy"):
            with mutation.mutation_lock():
                pytest.fail("second process entered shared resource")
        if abrupt:
            child.terminate()
            child.communicate(timeout=5)
        else:
            child.communicate(input="", timeout=5)
            assert child.returncode == 0
        with mutation.mutation_lock():
            assert path.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_nesting_and_exception_release():
    with pytest.raises(KeyboardInterrupt):
        with mutation.mutation_lock(), mutation.mutation_lock():
            raise KeyboardInterrupt()
    with mutation.mutation_lock():
        pass


def test_other_thread_cannot_enter_even_same_process():
    def compete():
        with mutation.mutation_lock():
            return True
    with mutation.mutation_lock(), ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(InstallError, match="busy"):
            pool.submit(compete).result(timeout=3)


def test_all_nine_recipes_reject_competing_process_before_state_read():
    source = """
import sys
from pathlib import Path
from software._shared import mutation
from software.registry import get
from software.base import InstallError
mutation.lock_path = lambda: Path(sys.argv[1])
mutation.system_lock_path = lambda: Path(sys.argv[1]).parent / 'system.lock'
for key in ('python','nodejs','golang','java','mongodb','mysql','postgresql','redis','clickhouse'):
    try:
        get(key)().switch('not-installed')
    except InstallError as error:
        if 'busy' not in str(error):
            raise
    else:
        raise AssertionError(key + ' bypassed mutation lock')
print('all denied')
"""
    with mutation.mutation_lock():
        child = _child(source, mutation.lock_path())
        stdout, stderr = child.communicate(input="", timeout=10)
    assert child.returncode == 0, stderr
    assert stdout.strip() == "all denied"


def test_lock_identity_ignores_data_dir(monkeypatch):
    monkeypatch.undo()
    first = mutation.lock_path()
    monkeypatch.setenv("OPSKIT_DATA_DIR", "other-data-directory")
    assert mutation.lock_path() == first


def test_hard_link_lock_rejected(tmp_path):
    path = mutation.lock_path()
    path.parent.mkdir()
    target = tmp_path / "other-file"
    target.write_bytes(b"preserved")
    os.link(target, path)
    with pytest.raises(InstallError, match="hard links"):
        with mutation.mutation_lock():
            pass
    assert target.read_bytes() == b"preserved"


def test_redis_upgrade_preserves_old_versions(monkeypatch):
    from software.recipes.redis.recipe import RedisRecipe
    calls = []
    monkeypatch.setattr(RedisRecipe, "install", lambda self, version: calls.append(version))
    monkeypatch.setattr(RedisRecipe, "uninstall", lambda *args: pytest.fail("upgrade must not uninstall old versions"))
    RedisRecipe().upgrade("7.4.1")
    assert calls == ["7.4.1"]
