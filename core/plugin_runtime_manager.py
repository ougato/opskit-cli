"""Framework-owned runtime validation and targeted maintenance before execution."""
from contextlib import contextmanager, ExitStack

from core.native_runtime_catalog import RuntimeCatalog, RuntimeSdkOutdated
from core.native_sandbox import NativeSandboxError
from core.paths import data_dir
from core.plugin_runtime_inventory import code_digest


def _refresh_sdk(catalog, manifest, fingerprint, *, checkpoint, authorize):
    from core.native_runtime_sdk_refresh import refresh
    refresh(catalog, manifest, fingerprint, checkpoint=checkpoint, authorize=authorize)


# Only explicitly supported, verified conditions may trigger maintenance.
_MAINTENANCE = {RuntimeSdkOutdated: _refresh_sdk}


@contextmanager
def acquire(manifest, fingerprint, *, checkpoint, authorize, speculative=False):
    """Lease a validated runtime, applying known maintenance only on selection."""
    catalog = RuntimeCatalog(data_dir() / 'native-plugin-runtimes')
    with ExitStack() as lease:
        try:
            runtime = lease.enter_context(catalog.acquire(
                manifest.name, fingerprint=fingerprint, sdk_sha256=code_digest()))
        except FileNotFoundError:
            raise NativeSandboxError('runtime_missing') from None
        except ValueError as error:
            maintenance = _MAINTENANCE.get(type(error))
            if speculative or maintenance is None:
                raise NativeSandboxError('runtime_invalid') from None
            try:
                maintenance(catalog, manifest, fingerprint, checkpoint=checkpoint, authorize=authorize)
                checkpoint()
                runtime = lease.enter_context(catalog.acquire(
                    manifest.name, fingerprint=fingerprint, sdk_sha256=code_digest()))
            except (FileNotFoundError, ValueError):
                raise NativeSandboxError('runtime_invalid') from None
        yield runtime
