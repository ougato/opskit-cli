"""Caught task failures must not retain finished synchronization resources."""
import multiprocessing
import weakref

import pytest

from core import plugin_invocation_lock, plugin_state_prepare
from network import worker


@pytest.fixture
def tracked_events(monkeypatch):
    context = multiprocessing.get_context('spawn')
    references = []
    class Context:
        def __getattr__(self, name):
            return getattr(context, name)
        def Event(self):
            value = context.Event()
            references.append(weakref.ref(value))
            return value
    monkeypatch.setattr(multiprocessing, 'get_context', lambda *args: Context())
    return references


def response(connection):
    worker.send(connection, {'ok': True})


def test_worker_startup_gate_needs_no_semaphores(tracked_events):
    assert worker.run(response, (), timeout=30, label='resource retirement') == {'ok': True}
    assert not tracked_events


def test_failed_lock_does_not_retain_confirmation_event(tmp_path, monkeypatch, tracked_events):
    def refused(descriptor):
        raise RuntimeError('synthetic parent lock refusal')
    monkeypatch.setattr(plugin_invocation_lock, '_acquire', refused)
    with pytest.raises(RuntimeError, match='parent lock refusal') as caught:
        with plugin_invocation_lock.hold(tmp_path.resolve() / 'demo.lock', timeout=30):
            pytest.fail('refused lock entered business')
    assert caught.value.__traceback__ is not None
    assert all(ref() is None for ref in tracked_events)
    assert len(tracked_events) == 1


def test_failed_allocation_callback_retires_event(tmp_path, tracked_events):
    from test_plugin_state_preparation import packet
    root = tmp_path.resolve() / 'self'
    root.mkdir()
    def refused(lease):
        raise RuntimeError('synthetic callback refusal')
    with pytest.raises(RuntimeError, match='callback refusal') as caught:
        plugin_state_prepare.prepare_candidate(packet(), root, timeout=30, plugin="owner", caller="platform", on_allocate=refused)
    assert caught.value.__traceback__ is not None
    assert all(ref() is None for ref in tracked_events)
    assert len(tracked_events) == 1
