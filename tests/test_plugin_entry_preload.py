"""The speculative owner hands execution back to the interactive thread."""
import threading
import time
from contextlib import contextmanager, nullcontext

import pytest

from core.plugin_entry_preload import EntryPreload, PreloadCancelled, PreloadStale
from core.plugin_sandbox import _invocations


def _session_with_handoff(monkeypatch, *, stale=False):
    from core import plugin_entry_preload
    class Inspector:
        def close(self):
            pass
    monkeypatch.setattr(plugin_entry_preload, "_warm_inspector", Inspector)
    started = threading.Event()
    release = threading.Event()
    activated = threading.Event()
    details = {}
    holder = {}

    class Worker:
        ready_at = 0.0

        def activate(self, *, deadline, checkpoint, dispatch, output, cancel_event, protocol_deadline):
            details["activation_thread"] = threading.get_ident()
            details["remaining"] = deadline - time.monotonic()
            assert protocol_deadline() == _invocations.deadline
            checkpoint()
            activated.set()
            dispatch({"type": "call"})
            details["protocol_deadline_after_input"] = protocol_deadline()
            return {"state": "result"}

    def target():
        release.wait()
        session = holder["session"]
        details["owner_thread"] = threading.get_ident()
        _invocations.deadline = time.monotonic() + 30
        _invocations.authorizations = (("example", "identity"),)
        _invocations.service_edges = ()
        _invocations.network_authorizations = ()
        def suspend():
            details["suspended"] = True
        def resume():
            assert details["suspended"]
            details["resumed_deadline"] = _invocations.deadline
            details["resumed"] = True
        session.set_locks(suspend, resume)
        def checkpoint():
            session.check_cancelled()
        def validate():
            details["validate_thread"] = threading.get_ident()
            if stale:
                raise PreloadStale()
        def dispatch(message):
            _invocations.network_authorizations = (("example", ("target",)),)
            _invocations.deadline += 60
            _invocations.input_wait_total = 60
        started.set()
        value = session.handoff(Worker(), checkpoint=checkpoint, dispatch=dispatch,
                                output=lambda _: None, validate=validate,
                                state=dict(_invocations.__dict__))
        details["returned"] = value
        details["owner_networks"] = _invocations.network_authorizations
        details["owner_deadline"] = _invocations.deadline
        details["owner_input_wait"] = _invocations.input_wait_total

    holder["session"] = EntryPreload(None, target=target)
    release.set()
    assert started.wait(3)
    session = holder["session"]
    ready = session._ready.get(timeout=3)
    session._ready.put(ready)
    return session, activated, details


def test_activation_runs_on_caller_and_returns_authorization_updates(monkeypatch):
    session, activated, details = _session_with_handoff(monkeypatch)
    _invocations.test_previous_value = "preserved"
    try:
        # Preparation time cannot be returned to the execution budget.
        session._started -= 100
        assert not activated.is_set()
        assert session.take() is True
        assert activated.is_set()
        assert details["owner_thread"] != threading.get_ident()
        assert details["activation_thread"] == details["validate_thread"] == threading.get_ident()
        assert 0 < details["remaining"] <= 30
        assert details["resumed"]
        assert details["returned"] == {"state": "result"}
        assert details["owner_networks"] == (("example", ("target",)),)
        assert details["protocol_deadline_after_input"] == details["owner_deadline"]
        assert details["owner_input_wait"] == 60
        assert _invocations.test_previous_value == "preserved"
        with pytest.raises(RuntimeError, match="already consumed"):
            session.take()
    finally:
        del _invocations.test_previous_value
        session.cancel()


def test_caller_inspector_is_reused_for_activation_and_closed(monkeypatch):
    from core import plugin_entry_preload
    session, activated, _ = _session_with_handoff(monkeypatch)
    created = []
    class Inspector:
        def __init__(self):
            self.closed = False
            created.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr(plugin_entry_preload, "_warm_inspector", Inspector)
    try:
        session.prepare_caller_inspector()
        session.prepare_caller_inspector()
        assert len(created) == 1
        assert not activated.is_set()
        assert session.take() is True
        assert activated.is_set()
        assert len(created) == 1 and created[0].closed
    finally:
        session.cancel(wait=True)


def test_cancelling_menu_closes_unused_caller_inspector(monkeypatch):
    from core import plugin_entry_preload
    session, activated, _ = _session_with_handoff(monkeypatch)
    created = []
    class Inspector:
        def __init__(self):
            self.closed = False
            created.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr(plugin_entry_preload, "_warm_inspector", Inspector)
    session.prepare_caller_inspector()
    session.cancel(wait=True)
    assert len(created) == 1 and created[0].closed
    assert not activated.is_set()


def test_failed_preparation_closes_unused_caller_inspector(monkeypatch):
    from core import plugin_entry_preload
    release = threading.Event()
    created = []
    class Inspector:
        def __init__(self):
            self.closed = False
            created.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr(plugin_entry_preload, "_warm_inspector", Inspector)
    def target():
        release.wait()
        raise PreloadStale()
    session = EntryPreload(None, target=target)
    session.prepare_caller_inspector()
    release.set()
    assert session._done.wait(3)
    assert len(created) == 1 and created[0].closed
    assert session.take() is False


def test_caller_inspector_cleanup_failure_cannot_silently_fall_back(monkeypatch):
    from core import plugin_entry_preload
    release = threading.Event()
    class Inspector:
        def close(self):
            raise ValueError("close failed")
    monkeypatch.setattr(plugin_entry_preload, "_warm_inspector", Inspector)
    session = EntryPreload(None, target=release.wait)
    session.prepare_caller_inspector()
    release.set()
    assert session._done.wait(3)
    with pytest.raises(ValueError, match="close failed"):
        session.take()


def test_caller_inspector_is_not_used_from_another_thread(monkeypatch):
    from core import plugin_entry_preload
    session, activated, _ = _session_with_handoff(monkeypatch)
    created = []
    class Inspector:
        def __init__(self):
            self.thread = threading.get_ident()
            self.closed = False
            created.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr(plugin_entry_preload, "_warm_inspector", Inspector)
    session.prepare_caller_inspector()
    results = []
    caller = threading.Thread(target=lambda: results.append(session.take()))
    try:
        caller.start()
        caller.join(3)
        assert not caller.is_alive()
        assert results == [True] and activated.is_set()
        assert len(created) == 2
        assert created[0].thread != created[1].thread
        assert all(inspector.closed for inspector in created)
    finally:
        session.cancel(wait=True)


def test_cancelled_preload_never_activates_worker(monkeypatch):
    session, activated, _ = _session_with_handoff(monkeypatch)
    session.cancel()
    assert session._done.wait(3)
    assert isinstance(session._error, PreloadCancelled)
    assert not activated.is_set()


@pytest.mark.parametrize(("preparation", "idle"), [(20, 0), (0, 15), (20, 15), (0, 3600)])
def test_budget_counts_preparation_but_excludes_user_idle(monkeypatch, preparation, idle):
    session, _, details = _session_with_handoff(monkeypatch)
    try:
        ready = session._ready.get_nowait()
        ready[-1]["deadline"] -= preparation + idle
        session._ready_at -= idle
        session._ready.put(ready)
        session.check_cancelled()
        assert session.take() is True
        assert 28 - preparation < details["remaining"] <= 30 - preparation
        assert 0 <= details["resumed_deadline"] - time.monotonic() <= 30 - preparation
    finally:
        session.cancel(wait=True)


def test_sdk_still_loading_does_not_count_as_user_idle(monkeypatch):
    session, _, details = _session_with_handoff(monkeypatch)
    try:
        ready = session._ready.get_nowait()
        ready[0].ready_at = None
        ready[-1]["deadline"] -= 5
        session._ready_at -= 5
        session._ready.put(ready)
        assert session.take() is True
        assert 23 < details["remaining"] <= 25
    finally:
        session.cancel(wait=True)


def test_failed_preparation_falls_back_without_activation():
    def fail():
        raise ValueError("not eligible")
    session = EntryPreload(None, target=fail)
    assert session._done.wait(3)
    assert session.take() is False


@pytest.mark.parametrize("failed", [True, False])
def test_failed_or_stalled_startup_is_retired_without_selection(failed):
    class Worker:
        ready_at = None
        startup_failed = failed

    holder = {}
    release = threading.Event()
    def target():
        release.wait()
        session = holder["session"]
        session.set_locks(lambda: None, lambda: None)
        session.handoff(Worker(), checkpoint=lambda: None, dispatch=None, output=None,
                        validate=lambda: None, state={"deadline": time.monotonic() + .2})

    session = holder["session"] = EntryPreload(None, target=target)
    release.set()
    assert session._done.wait(3)
    assert isinstance(session._error, PreloadStale)
    assert session.take() is False


def test_cleanup_failure_cannot_silently_fall_back():
    from core.cleanup import add_exception_note
    failure = ValueError("not eligible")
    add_exception_note(failure, "plugin workspace cleanup incomplete; evidence retained")
    def fail():
        raise failure
    session = EntryPreload(None, target=fail)
    assert session._done.wait(3)
    with pytest.raises(ValueError) as observed:
        session.take()
    assert observed.value is failure


def test_cancelled_preload_waits_for_cleanup_before_fallback():
    from core.cleanup import add_exception_note
    cleanup_started = threading.Event()
    allow_cleanup = threading.Event()
    returned = threading.Event()
    failure = ValueError("preparation cancelled")
    add_exception_note(failure, "plugin workspace cleanup incomplete; evidence retained")
    observed = []

    def target():
        cleanup_started.set()
        allow_cleanup.wait(3)
        raise failure

    def take():
        try:
            observed.append(session.take())
        except BaseException as error:
            observed.append(error)
        finally:
            returned.set()

    session = EntryPreload(None, target=target)
    assert cleanup_started.wait(3)
    session.cancel()
    caller = threading.Thread(target=take)
    caller.start()
    try:
        assert not returned.wait(.2)
    finally:
        allow_cleanup.set()
        caller.join(3)
    assert not caller.is_alive()
    assert observed == [failure]


def test_cancelled_session_is_retired_before_retry(monkeypatch):
    from core import plugin_entry_preload
    release = threading.Event()
    session = EntryPreload("manifest", target=release.wait)
    try:
        monkeypatch.setattr(plugin_entry_preload, "_active", session)
        session.cancel()
        assert plugin_entry_preload.start("manifest") is session
        release.set()
        assert session.take() is False
    finally:
        release.set()
        session.cancel(wait=True)


def test_changed_state_falls_back_without_activation(monkeypatch):
    session, activated, _ = _session_with_handoff(monkeypatch, stale=True)
    try:
        assert session.take() is False
        assert not activated.is_set()
    finally:
        session.cancel()


def test_suspended_invocation_does_not_keep_plugin_lock(monkeypatch):
    from core import plugin_sandbox
    lock = threading.RLock()
    held = []

    @contextmanager
    def state_lock(name):
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr(plugin_sandbox, "_state_lock", state_lock)
    monkeypatch.setattr(plugin_sandbox, "_remaining", lambda: 2)
    with plugin_sandbox._invocation_locks("example", lock) as (suspend, resume):
        assert held
        suspend()
        assert not held
        observed = []
        def other_invocation():
            with lock:
                observed.append(True)
        thread = threading.Thread(target=other_invocation)
        thread.start()
        thread.join(timeout=2)
        assert observed == [True]
        resume()
        assert held
    assert not held


def test_preload_state_check_does_not_recover_pending_transaction(tmp_path, monkeypatch):
    from core import paths, plugin_metadata, plugin_state, plugin_state_budget, plugin_state_io
    monkeypatch.setattr(plugin_state_budget, "platform_state_guard", nullcontext)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin_state, "transaction_guard", lambda root: nullcontext())
    monkeypatch.setattr(plugin_state, "record_path", lambda root: tmp_path / "journal")
    monkeypatch.setattr(plugin_metadata, "path_present", lambda path: True)
    monkeypatch.setattr(plugin_state, "finish_transaction", lambda *a, **k: pytest.fail("recovered before selection"))
    root = tmp_path / "plugin-data" / "example" / "self"
    with pytest.raises(ValueError, match="state recovery requires"):
        plugin_state_io._check_preload_state(root, "example", "platform")
