"""Startup diagnostics are bounded, opt-in and safe across initialization."""
import subprocess
import sys
import threading

import pytest

from core import telemetry, updater


@pytest.fixture(autouse=True)
def reset_telemetry(monkeypatch):
    monkeypatch.setattr(telemetry, "_provider", None)
    monkeypatch.setattr(telemetry, "_buffer_startup_updates", False)
    monkeypatch.setattr(telemetry, "_init_generation", 0)
    monkeypatch.setattr(telemetry, "_active_dsn", None)
    monkeypatch.setattr(telemetry, "_prepared_dsn", None)
    telemetry._startup_updates.clear()
    yield
    telemetry._startup_updates.clear()


def enabled():
    return {"telemetry": {"enabled": True, "dsn": "https://example.invalid"}}


def fake_provider(monkeypatch, reports):
    class Provider:
        def __init__(self, **kwargs):
            pass

        def init(self):
            pass

        def capture_message(self, message, **kwargs):
            reports.append((message, kwargs))

    monkeypatch.setattr(telemetry, "SentryProvider", Provider)


def test_startup_report_flushes_event_without_exception_or_context(monkeypatch):
    reports = []
    fake_provider(monkeypatch, reports)
    telemetry.prepare_startup_updates(enabled())
    updater._report("update_rollback_failed", exc=RuntimeError("secret exception"),
                    level="error", path="secret path")
    assert reports == []
    telemetry.init(enabled())
    assert reports == [("update_rollback_failed", {"level": "error", "component": "updater"})]
    telemetry.init(enabled())
    assert len(reports) == 1


def test_disabled_or_failed_initialization_discards_pending(monkeypatch):
    reports = []
    fake_provider(monkeypatch, reports)
    telemetry.prepare_startup_updates(enabled())
    updater._report("update_rollback_failed")
    telemetry.init({"telemetry": {"enabled": False, "dsn": "https://example.invalid"}})
    telemetry.init(enabled())
    assert reports == []
    telemetry.prepare_startup_updates({"telemetry": {"enabled": False, "dsn": "https://example.invalid"}})
    updater._report("update_rollback_failed")
    telemetry.init(enabled())
    assert reports == []

    monkeypatch.setattr(telemetry, "_provider", None)
    telemetry.prepare_startup_updates(enabled())
    updater._report("update_rollback_failed")
    monkeypatch.setattr(telemetry, "SentryProvider", lambda **kwargs: (_ for _ in ()).throw(OSError()))
    telemetry.init(enabled())
    fake_provider(monkeypatch, reports)
    telemetry.init(enabled())
    assert reports == []


def test_buffer_is_bounded_and_event_name_is_sanitized(monkeypatch):
    reports = []
    fake_provider(monkeypatch, reports)
    telemetry.prepare_startup_updates(enabled())
    updater._report("private path /home/user", level="error")
    for index in range(32):
        updater._report(f"update_{index}")
    telemetry.init(enabled())
    assert len(reports) == 32
    assert reports[0][0] == "update_0"
    assert reports[-1][0] == "update_31"
    assert "private path" not in repr(reports)


def test_invalid_event_never_enters_deferred_report(monkeypatch):
    reports = []
    fake_provider(monkeypatch, reports)
    telemetry.prepare_startup_updates(enabled())
    updater._report("private path /home/user", exc=RuntimeError("private"))
    telemetry.init(enabled())
    assert reports == [("invalid_event", {"level": "warning", "component": "updater"})]


def test_boot_prepares_buffer_before_pending_update_check(monkeypatch):
    import main

    reports = []
    fake_provider(monkeypatch, reports)
    monkeypatch.setattr(main, "ensure_config", lambda: {**enabled(), "log": {}, "update": {}})
    monkeypatch.setattr(main, "theme_init", lambda: None)
    monkeypatch.setattr(main, "i18n_init", lambda: None)
    monkeypatch.setattr("core.logger.init", lambda *_: None)
    monkeypatch.setattr("core.cleanup.init", lambda: None)
    monkeypatch.setattr("core.platform.preflight_check", lambda: [])

    def pending(*, post_update):
        updater._report("update_rollback_failed", exc=RuntimeError("private"))
        return False

    monkeypatch.setattr(updater, "check_and_apply_pending", pending)
    main._boot()
    assert reports == []
    telemetry.init(enabled())
    assert reports == [("update_rollback_failed", {"level": "warning", "component": "updater"})]


def test_event_during_provider_initialization_is_delivered_once(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    reports = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def init(self):
            entered.set()
            assert release.wait(5)

        def capture_message(self, message, **kwargs):
            reports.append(message)

    monkeypatch.setattr(telemetry, "SentryProvider", Provider)
    telemetry.prepare_startup_updates(enabled())
    worker = threading.Thread(target=telemetry.init, args=(enabled(),))
    worker.start()
    try:
        assert entered.wait(5)
        updater._report("update_rollback_failed")
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert reports == ["update_rollback_failed"]


def test_disabling_invalidates_older_inflight_initialization(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    reports = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def init(self):
            entered.set()
            assert release.wait(5)

        def capture_message(self, message, **kwargs):
            reports.append(message)

    monkeypatch.setattr(telemetry, "SentryProvider", Provider)
    telemetry.prepare_startup_updates(enabled())
    updater._report("update_rollback_failed")
    worker = threading.Thread(target=telemetry.init, args=(enabled(),))
    worker.start()
    try:
        assert entered.wait(5)
        telemetry.prepare_startup_updates({"telemetry": {"enabled": False}})
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert isinstance(telemetry._provider, telemetry.NullProvider)
    assert reports == []


def test_changed_endpoint_does_not_receive_events_on_old_provider(monkeypatch):
    reports = []

    class Provider:
        def __init__(self, dsn, **kwargs):
            self.dsn = dsn

        def init(self):
            pass

        def capture_message(self, message, **kwargs):
            reports.append((self.dsn, message))

    monkeypatch.setattr(telemetry, "SentryProvider", Provider)
    old = enabled()
    new = {"telemetry": {"enabled": True, "dsn": "https://other.invalid"}}
    telemetry.init(old)
    telemetry.prepare_startup_updates(new)
    updater._report("update_rollback_failed")
    assert reports == []
    telemetry.init(new)
    assert reports == [("https://other.invalid", "update_rollback_failed")]


def test_concurrent_initializations_cannot_revert_global_endpoint(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    initialized, reports = [], []

    class Provider:
        def __init__(self, dsn, **kwargs):
            self.dsn = dsn

        def init(self):
            if self.dsn == "https://example.invalid":
                entered.set()
                assert release.wait(5)
            initialized.append(self.dsn)

        def capture_message(self, message, **kwargs):
            reports.append((self.dsn, message))

    monkeypatch.setattr(telemetry, "SentryProvider", Provider)
    old = enabled()
    new = {"telemetry": {"enabled": True, "dsn": "https://other.invalid"}}
    telemetry.prepare_startup_updates(old)
    first = threading.Thread(target=telemetry.init, args=(old,))
    first.start()
    second = None
    try:
        assert entered.wait(5)
        updater._report("old_event")
        telemetry.prepare_startup_updates(new)
        updater._report("new_event")
        second = threading.Thread(target=telemetry.init, args=(new,))
        second.start()
    finally:
        release.set()
        first.join(5)
        if second:
            second.join(5)
    assert not first.is_alive() and second is not None and not second.is_alive()
    assert initialized == ["https://example.invalid", "https://other.invalid"]
    assert reports == [("https://other.invalid", "new_event")]


def test_flush_cannot_cross_to_reconfigured_global_sdk(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    global_sdk, reports = {}, []

    class Provider:
        def __init__(self, dsn, **kwargs):
            self.dsn = dsn

        def init(self):
            global_sdk["dsn"] = self.dsn

        def capture_message(self, message, **kwargs):
            entered.set()
            assert release.wait(5)
            reports.append((self.dsn, global_sdk["dsn"], message))

    monkeypatch.setattr(telemetry, "SentryProvider", Provider)
    old = enabled()
    new = {"telemetry": {"enabled": True, "dsn": "https://other.invalid"}}
    telemetry.prepare_startup_updates(old)
    updater._report("old_event")
    first = threading.Thread(target=telemetry.init, args=(old,))
    first.start()
    second = None
    try:
        assert entered.wait(5)
        telemetry.prepare_startup_updates(new)
        second = threading.Thread(target=telemetry.init, args=(new,))
        second.start()
    finally:
        release.set()
        first.join(5)
        if second:
            second.join(5)
    assert not first.is_alive() and second is not None and not second.is_alive()
    assert reports == [("https://example.invalid", "https://example.invalid", "old_event")]
    assert global_sdk["dsn"] == "https://other.invalid"


def test_regular_capture_does_not_wait_for_initialization(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Provider:
        def __init__(self, **kwargs):
            pass

        def init(self):
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(telemetry, "SentryProvider", Provider)
    worker = threading.Thread(target=telemetry.init, args=(enabled(),))
    worker.start()
    try:
        assert entered.wait(5)
        telemetry.capture_message("not queued")
        telemetry.capture_error(RuntimeError("not queued"))
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_real_sdk_exception_hook_drops_events_after_opt_out():
    # The SDK installs a process-global exception hook; isolate it from pytest
    # and replace its network transport with a synchronous in-memory sink.
    program = r'''
import sys
import sentry_sdk
from sentry_sdk.transport import Transport
from core import telemetry
from core.telemetry.sentry_provider import SentryProvider

events = []
class Sink(Transport):
    def capture_envelope(self, envelope):
        events.append(envelope)

real_init = sentry_sdk.init
def offline_init(*args, **kwargs):
    kwargs['transport'] = Sink()
    return real_init(*args, **kwargs)
sentry_sdk.init = offline_init
SentryProvider._collect_env = lambda self: None
cfg = {'telemetry': {'enabled': True, 'dsn': 'https://public@example.invalid/1'}}
telemetry.prepare_startup_updates(cfg)
telemetry.init(cfg)
def uncaught():
    try:
        raise ValueError('private')
    except ValueError:
        sys.excepthook(*sys.exc_info())
uncaught()
assert len(events) == 1, len(events)
telemetry.prepare_startup_updates({'telemetry': {'enabled': False}})
telemetry.init({'telemetry': {'enabled': False}})
uncaught()
assert len(events) == 1, len(events)
'''
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_real_sdk_endpoint_switch_drops_stale_capture():
    # Use the real SDK and an in-memory transport in an isolated process.
    program = r'''
import threading
import sentry_sdk
from sentry_sdk.transport import Transport
from core import telemetry
from core.telemetry.sentry_provider import SentryProvider

deliveries = []
class Sink(Transport):
    def capture_envelope(self, envelope):
        deliveries.append(self.options['dsn'])

real_init = sentry_sdk.init
def offline_init(*args, **kwargs):
    kwargs['transport'] = Sink
    return real_init(*args, **kwargs)
sentry_sdk.init = offline_init
SentryProvider._collect_env = lambda self: None
old = {'telemetry': {'enabled': True, 'dsn': 'https://public@old.invalid/1'}}
new = {'telemetry': {'enabled': True, 'dsn': 'https://public@new.invalid/1'}}
telemetry.prepare_startup_updates(old)
telemetry.init(old)
telemetry.capture_message('old_baseline')
assert deliveries == [old['telemetry']['dsn']], deliveries

entered, release = threading.Event(), threading.Event()
real_capture = SentryProvider.capture_message
def gated_capture(self, message, **kwargs):
    if message == 'stale_event':
        entered.set()
        assert release.wait(10)
    return real_capture(self, message, **kwargs)
SentryProvider.capture_message = gated_capture
first = threading.Thread(target=telemetry.capture_message, args=('stale_event',))
first.start()
second = None
try:
    assert entered.wait(5)
    telemetry.prepare_startup_updates(new)
    second = threading.Thread(target=telemetry.init, args=(new,))
    second.start()
finally:
    release.set()
    first.join(10)
    if second is not None:
        second.join(10)
assert second is not None and not first.is_alive() and not second.is_alive()
telemetry.capture_message('new_event')
assert deliveries == [old['telemetry']['dsn'], new['telemetry']['dsn']], deliveries
'''
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("section", [{"dsn": "https://example.invalid"},
                                    {"enabled": "false", "dsn": "https://example.invalid"}])
def test_initialization_requires_explicit_boolean_opt_in(monkeypatch, section):
    reports = []
    fake_provider(monkeypatch, reports)
    telemetry.init({"telemetry": section})
    assert isinstance(telemetry._provider, telemetry.NullProvider)
    assert reports == []
