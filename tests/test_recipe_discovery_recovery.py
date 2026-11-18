"""Recipe discovery must be retryable and serialized across callers."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from software import registry


def test_failed_discovery_keeps_existing_registrations_and_can_retry(monkeypatch):
    class First:
        key = "first"

    class Second:
        key = "second"

    monkeypatch.setattr(registry, "_registry", {})
    monkeypatch.setattr(registry, "_discovered", False)
    monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda _: [(None, "first", True), (None, "second", True)])
    imports = []

    def load(name):
        imports.append(name)
        if name.endswith("first"):
            registry.register(First)
        elif imports.count(name) == 1:
            raise ImportError("temporary failure")
        else:
            registry.register(Second)

    monkeypatch.setattr(registry.importlib, "import_module", load)
    with pytest.raises(ImportError, match="temporary failure"):
        registry.all_recipes()
    assert registry._discovered is False
    assert registry._registry == {"first": First}
    assert registry.get("second") is Second
    assert set(registry.all_recipes()) == {First, Second}
    assert imports.count("software.recipes.second") == 2


def test_discovery_serializes_concurrent_callers(monkeypatch):
    class Recipe:
        key = "example"

    entered = threading.Event()
    release = threading.Event()
    calls = []
    monkeypatch.setattr(registry, "_registry", {})
    monkeypatch.setattr(registry, "_discovered", False)
    monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda _: [(None, "example", True)])

    def load(name):
        calls.append(name)
        entered.set()
        assert release.wait(5)
        registry.register(Recipe)

    monkeypatch.setattr(registry.importlib, "import_module", load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(registry.all_recipes)
        assert entered.wait(5)
        second = executor.submit(registry.get, "example")
        try:
            assert not second.done()
        finally:
            release.set()
        assert first.result(timeout=5) == [Recipe]
        assert second.result(timeout=5) is Recipe
    assert calls == ["software.recipes.example"]


def test_registration_does_not_wait_for_discovery_import(monkeypatch):
    class Recipe:
        key = "parallel"

    entered = threading.Event()
    registered = threading.Event()
    monkeypatch.setattr(registry, "_registry", {})
    monkeypatch.setattr(registry, "_discovered", False)
    monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda _: [(None, "example", True)])

    def load(name):
        entered.set()
        assert registered.wait(5), "registration blocked behind discovery's import"

    def register():
        assert entered.wait(5)
        registry.register(Recipe)
        registered.set()

    monkeypatch.setattr(registry.importlib, "import_module", load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        discovery = executor.submit(registry.all_recipes)
        registration = executor.submit(register)
        registration.result(timeout=5)
        assert discovery.result(timeout=5) == [Recipe]


def test_slow_discovery_waiter_times_out_without_publishing_partial_results(monkeypatch):
    class Recipe:
        key = "slow"

    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(registry, "_registry", {})
    monkeypatch.setattr(registry, "_discovered", False)
    monkeypatch.setattr(registry, "_DISCOVERY_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda _: [(None, "slow", True)])

    def load(_name):
        entered.set()
        assert release.wait(5)
        registry.register(Recipe)

    monkeypatch.setattr(registry.importlib, "import_module", load)
    with ThreadPoolExecutor(max_workers=1) as executor:
        discovery = executor.submit(registry.all_recipes)
        assert entered.wait(5)
        try:
            with pytest.raises(registry.RecipeDiscoveryTimeout, match="Recipe discovery did not finish"):
                registry.all_recipes()
            assert not registry._discovered
            assert not discovery.done()
        finally:
            release.set()
        assert discovery.result(timeout=5) == [Recipe]
    assert registry.all_recipes() == [Recipe]


def test_waiter_retries_after_discovering_thread_fails(monkeypatch):
    class Recipe:
        key = "retried"

    entered = threading.Event()
    release = threading.Event()
    calls = []
    monkeypatch.setattr(registry, "_registry", {})
    monkeypatch.setattr(registry, "_discovered", False)
    monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda _: [(None, "retry", True)])

    def load(name):
        calls.append(name)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            raise ImportError("first discovery failed")
        registry.register(Recipe)

    monkeypatch.setattr(registry.importlib, "import_module", load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(registry.all_recipes)
        assert entered.wait(5)
        second = executor.submit(registry.all_recipes)
        release.set()
        with pytest.raises(ImportError, match="first discovery failed"):
            first.result(timeout=5)
        assert second.result(timeout=5) == [Recipe]
    assert len(calls) == 2


def test_reentrant_query_fails_without_publishing_partial_discovery(monkeypatch):
    class Recipe:
        key = "after_retry"

    calls = []
    monkeypatch.setattr(registry, "_registry", {})
    monkeypatch.setattr(registry, "_discovered", False)
    monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda _: [(None, "example", True)])

    def load(_name):
        calls.append(1)
        if len(calls) == 1:
            registry.all_recipes()
        registry.register(Recipe)

    monkeypatch.setattr(registry.importlib, "import_module", load)
    with pytest.raises(registry.RecipeDiscoveryReentryError, match="cannot be queried"):
        registry.get("after_retry")
    assert not registry._discovered
    assert registry._discovering is None
    assert registry.all_recipes() == [Recipe]


def test_external_import_cycle_fails_without_waiting_in_subprocess():
    # A subprocess keeps a regression from hanging the test runner if a
    # recipe's module lock and the discovery condition form a cycle again.
    script = r'''
import importlib
import sys
import tempfile
import threading
import types
from pathlib import Path

import software.recipes as recipes
import software.registry as registry

sync = types.ModuleType("discovery_cycle_sync")
sync.importing = threading.Event()
sync.discovering = threading.Event()
sys.modules[sync.__name__] = sync
registry._DISCOVERY_WAIT_SECONDS = 0.1
with tempfile.TemporaryDirectory() as temporary:
    Path(temporary, "discovery_cycle_probe.py").write_text("""
import threading
import discovery_cycle_sync as sync
from software import registry
if threading.current_thread().name == 'external_import':
    sync.importing.set()
    assert sync.discovering.wait(3)
    registry.get('probe')
class Recipe:
    key = 'probe'
registry.register(Recipe)
""", encoding="utf-8")
    recipes.__path__.insert(0, temporary)
    original_import = importlib.import_module
    def observe(name, *args, **kwargs):
        if name == 'software.recipes.discovery_cycle_probe' and threading.current_thread().name == 'discovery':
            sync.discovering.set()
        return original_import(name, *args, **kwargs)
    registry.importlib.import_module = observe
    registry.pkgutil.iter_modules = lambda path: [(None, 'discovery_cycle_probe', False)]
    errors = []
    def external():
        try:
            original_import('software.recipes.discovery_cycle_probe')
        except BaseException as exc:
            errors.append(exc)
    discovered = []
    def discover():
        discovered.extend(registry.all_recipes())
    first = threading.Thread(target=external, name='external_import', daemon=True)
    second = threading.Thread(target=discover, name='discovery', daemon=True)
    first.start()
    assert sync.importing.wait(3)
    second.start()
    first.join(3)
    second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], registry.RecipeDiscoveryReentryError), errors
    assert registry._discovered and len(discovered) == 1
    assert registry.get('probe') is discovered[0]
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            timeout=10, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("query", ["registry.get('probe')", "registry.all_recipes()"])
def test_external_recipe_module_query_fails_before_partial_discovery(query):
    # Exercise a real module import on a fresh interpreter. Python's recursive
    # import otherwise returns the still-running module to discovery.
    script = r'''
import importlib
import sys
import tempfile
import types
from pathlib import Path

import software.recipes as recipes
import software.registry as registry

sync = types.ModuleType("discovery_probe_state")
sync.query_on_import = True
sys.modules[sync.__name__] = sync
with tempfile.TemporaryDirectory() as temporary:
    package = Path(temporary, "discovery_probe")
    package.mkdir()
    (package / "__init__.py").write_text("from . import implementation\n", encoding="utf-8")
    (package / "implementation.py").write_text("""
import discovery_probe_state as sync
from software import registry
if sync.query_on_import:
    QUERY
class Recipe:
    key = 'probe'
registry.register(Recipe)
""".replace("QUERY", sys.argv[1]), encoding="utf-8")
    recipes.__path__.insert(0, temporary)
    registry.pkgutil.iter_modules = lambda path: [(None, 'discovery_probe', True)]
    try:
        importlib.import_module('software.recipes.discovery_probe')
    except registry.RecipeDiscoveryReentryError:
        pass
    else:
        raise AssertionError('direct import did not reject incomplete discovery')
    assert not registry._discovered
    assert 'probe' not in registry._registry
    sync.query_on_import = False
    recipe = registry.get('probe')
    assert recipe is not None and recipe.key == 'probe'
    assert registry._discovered and registry.all_recipes() == [recipe]
'''
    result = subprocess.run([sys.executable, "-c", script, query], capture_output=True, text=True,
                            timeout=10, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr
