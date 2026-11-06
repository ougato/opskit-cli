"""One speculative plugin-entry preparation with explicit main-thread handoff.

The owner thread retains all filesystem leases and sandbox ownership. It never
executes plugin code until the menu's caller takes the prepared worker over.
"""
import queue
import threading
import time


class PreloadCancelled(Exception):
    """The enclosing menu no longer needs the speculative preparation."""


class PreloadStale(Exception):
    """Prepared state changed while no invocation lock was held."""


def _warm_inspector():
    from core.plugin_inspection import ContentInspector
    inspector = ContentInspector()
    inspector.start()
    return inspector


class EntryPreload:
    def __init__(self, manifest, *, target=None):
        self.manifest = manifest
        self.cancelled = threading.Event()
        self._ready = queue.Queue(maxsize=1)
        self._returned = queue.Queue(maxsize=1)
        self._claim = threading.Event()
        self._acquired = threading.Event()
        self._done = threading.Event()
        self._entered = False
        self._value = None
        self._error = None
        self._started = time.monotonic()
        self._ready_at = None
        self._selected_deadline = None
        self._caller_inspector = None
        self._caller_thread = None
        self._inspector_lock = threading.Lock()
        self._stopping = False
        self._target = target or self._invoke
        if target is None:
            from core.native_preload import warm_platform_assets_async
            warm_platform_assets_async()
        self._thread = threading.Thread(target=self._run, daemon=True, name="plugin-entry-preload")
        self._thread.start()

    def _invoke(self):
        from core.plugin_sandbox import invoke
        return invoke(self.manifest, "entry", _preload=self)

    def _run(self):
        try:
            self._value = self._target()
        except BaseException as error:
            self._error = error
        finally:
            with self._inspector_lock:
                self._stopping = True
                inspector, self._caller_inspector = self._caller_inspector, None
                self._caller_thread = None
            try:
                if inspector is not None:
                    inspector.close()
            except BaseException as cleanup_error:
                from core.cleanup import add_exception_note
                if self._error is None:
                    self._error = cleanup_error
                    add_exception_note(self._error, "caller inspector cleanup failed")
                else:
                    add_exception_note(self._error, "caller inspector cleanup failed: " + str(cleanup_error))
            finally:
                self._done.set()

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise PreloadCancelled()

    def set_locks(self, suspend, resume):
        self._suspend = suspend
        self._resume = resume
        self._locks_suspended = False

    def suspend_locks(self):
        if not self._locks_suspended:
            self._suspend()
            self._locks_suspended = True

    def handoff(self, worker, *, checkpoint, dispatch, output, validate, state):
        """Keep the worker and its owner open until the main thread returns it."""
        self.check_cancelled()
        # Speculation must not make the plugin busy while the user is reading
        # a menu. Reacquire on selection and revalidate the state snapshot.
        self.suspend_locks()
        self._ready_at = time.monotonic()
        self._ready.put((worker, checkpoint, dispatch, output, validate, state))
        # A failed or stalled interpreter must not remain allocated indefinitely
        # while the user is deciding. Once ready, wait without polling.
        while not self._claim.is_set() and worker.ready_at is None:
            if worker.startup_failed:
                raise PreloadStale()
            remaining = state["deadline"] - time.monotonic()
            if remaining <= 0:
                raise PreloadStale()
            self._claim.wait(min(.05, remaining))
        self._claim.wait()
        self.check_cancelled()
        from core.plugin_sandbox import _invocations
        _invocations.deadline = self._selected_deadline
        self._resume()
        self._acquired.set()
        while True:
            self.check_cancelled()
            try:
                result, error, updates = self._returned.get(timeout=.05)
                break
            except queue.Empty:
                pass
        for key, value in updates.items():
            setattr(_invocations, key, value)
        if error is not None:
            raise error
        return result

    def _fallback(self):
        """Allow an ordinary retry only after confirmed safe cleanup."""
        # A cancellation can become visible before the owner has retired the
        # sandbox and released its workspace. Do not hide a cleanup failure by
        # starting a replacement invocation while that work is still running.
        self._done.wait()
        error = self._error
        if error is not None:
            from core.native_sandbox import NativeSandboxError
            if (not isinstance(error, Exception) or getattr(error, "__notes__", ())
                    or isinstance(error, NativeSandboxError) and error.code == "transport_cleanup_failed"
                    or "cleanup" in str(error).lower()):
                raise error
        return False

    def prepare_caller_inspector(self):
        """Start the caller's reader while its menu is visible, without reading data."""
        with self._inspector_lock:
            if (self._caller_inspector is not None or self._entered or self.cancelled.is_set()
                    or self._stopping or self.done):
                return
            inspector = _warm_inspector()
            if self.cancelled.is_set() or self.done:
                inspector.close()
                return
            self._caller_inspector = inspector
            self._caller_thread = threading.get_ident()

    def _take_caller_inspector(self):
        with self._inspector_lock:
            inspector, self._caller_inspector = self._caller_inspector, None
            owner, self._caller_thread = self._caller_thread, None
        if inspector is not None and owner != threading.get_ident():
            # Inspectors may only perform reads on their creating thread.
            inspector.close()
            return None
        return inspector

    def _close_caller_inspector(self):
        with self._inspector_lock:
            inspector, self._caller_inspector = self._caller_inspector, None
            self._caller_thread = None
        if inspector is not None:
            inspector.close()

    def take(self):
        """Run the activated worker on the caller thread, then join its owner.

        Return False if speculation could not prepare anything; the caller can
        run the ordinary entry path. Once activated, failures are propagated.
        """
        if self._entered:
            raise RuntimeError("plugin preload already consumed")
        self._entered = True
        if self.cancelled.is_set() or self._done.is_set():
            self._close_caller_inspector()
            return self._fallback()
        while True:
            try:
                ready = self._ready.get(timeout=.05)
                break
            except queue.Empty:
                if self._done.is_set():
                    self._close_caller_inspector()
                    return self._fallback()
        if self.cancelled.is_set() or self._done.is_set():
            self._close_caller_inspector()
            return self._fallback()
        worker, checkpoint, dispatch, output, validate, state = ready
        # Only time spent waiting for the user after both handoff and SDK
        # readiness is idle; ongoing SDK imports still consume the budget.
        now = time.monotonic()
        ready_at = worker.ready_at
        idle_since = max(self._ready_at, ready_at) if ready_at is not None else now
        deadline = state["deadline"] + max(0, now - idle_since)
        self._selected_deadline = deadline
        self._claim.set()
        inspector = None
        try:
            inspector = self._take_caller_inspector() or _warm_inspector()
            while not self._acquired.wait(.05):
                if self._done.is_set():
                    inspector.close()
                    return self._fallback()
        except BaseException:
            if inspector is not None:
                inspector.close()
            self.cancel(wait=True)
            raise
        from core.plugin_sandbox import _invocations
        old = dict(_invocations.__dict__)
        _invocations.__dict__.clear()
        _invocations.__dict__.update(state)
        _invocations.inspector = inspector  # inspectors are thread-affine
        result = error = None
        try:
            _invocations.deadline = deadline
            validate()
            result = worker.activate(deadline=_invocations.deadline, dispatch=dispatch,
                                     checkpoint=checkpoint, output=output, cancel_event=self.cancelled,
                                     protocol_deadline=lambda: _invocations.deadline)
        except BaseException as exc:
            error = exc
        finally:
            inspector = getattr(_invocations, "inspector", None)
            if inspector is not None:
                try:
                    inspector.close()
                except BaseException as exc:
                    if error is None:
                        error = exc
            keys = ("deadline", "input_wait_total", "authorizations", "service_edges", "network_authorizations")
            updates = {key: getattr(_invocations, key) for key in keys if hasattr(_invocations, key)}
            _invocations.__dict__.clear()
            _invocations.__dict__.update(old)
            self._returned.put((result, error, updates))
        # The owner publishes state and retires the sandbox before returning.
        self._done.wait()
        if isinstance(error, PreloadStale) and self._error is error and not getattr(error, "__notes__", ()):
            return False
        if self._error is not None:
            raise self._error
        if error is not None:
            raise error
        return True

    def cancel(self, *, wait=False):
        self.cancelled.set()
        self._claim.set()
        try:
            self._close_caller_inspector()
        finally:
            if wait and threading.current_thread() is not self._thread:
                self._thread.join()

    @property
    def done(self):
        return self._done.is_set()


_active = None
_active_lock = threading.Lock()
_discovery = None
_generation = 0


def start(manifest):
    """Limit speculative ownership to one active worker for the whole process."""
    global _active
    with _active_lock:
        if _active is not None and not _active.done:
            # A cancelled matching session is handed back only so take() can
            # wait for its cleanup before the caller retries normally.
            return _active if _active.manifest == manifest and not _active._entered else None
        _active = EntryPreload(manifest)
        return _active


def start_discovery():
    """Begin finding a candidate only after the host's main menu is visible."""
    global _discovery, _generation
    with _active_lock:
        if (_active is not None and not _active.done) or (_discovery is not None and _discovery.is_alive()):
            return
        _generation += 1
        generation = _generation

        def discover():
            global _active
            try:
                from plugin.commands import loaded_plugins
                pairs = loaded_plugins()
                candidate = next((manifest for manifest, _ in pairs if not manifest.group), None)
                if candidate is None:
                    return
                with _active_lock:
                    if generation == _generation and (_active is None or _active.done):
                        _active = EntryPreload(candidate)
            except Exception:
                # Speculation is optional; normal menu discovery reports errors.
                return
        _discovery = threading.Thread(target=discover, daemon=True, name="plugin-preload-discovery")
        _discovery.start()


def cancel_pending(*, wait=False):
    global _generation
    with _active_lock:
        _generation += 1
        active, discovery = _active, _discovery
        if active is not None and not active.done:
            active.cancel()
    if wait:
        if discovery is not None and discovery is not threading.current_thread():
            discovery.join()
        if active is not None:
            active.cancel(wait=True)
