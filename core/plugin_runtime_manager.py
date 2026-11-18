"""Framework-owned runtime validation and targeted maintenance before execution."""
from contextlib import contextmanager, ExitStack

from core.native_runtime_catalog import RuntimeCatalog, RuntimePluginOutdated, RuntimeSdkOutdated
from core.native_sandbox import NativeSandboxError
from core.paths import data_dir
from core.plugin_runtime_inventory import code_digest


def _refresh_sdk(catalog, manifest, fingerprint, *, checkpoint, authorize):
    from core.native_runtime_sdk_refresh import refresh
    refresh(catalog, manifest, fingerprint, checkpoint=checkpoint, authorize=authorize)


def prepare_update(manifest, receipt, *, checkpoint=lambda: None, authorize=lambda: True, allow_bootstrap=True):
    """Bind a verified runtime to the exact tree obtained through native acquisition."""
    from core.native_runtime_sdk_refresh import refresh
    from core.plugin_install_receipt import content_receipt
    from core.plugin_trust import compute_fingerprint

    def verify_installation():
        checkpoint()
        return authorize() and content_receipt(manifest.root) == receipt

    if not verify_installation():
        raise ValueError("native plugin installation receipt changed")
    fingerprint = compute_fingerprint(manifest.root)
    catalog = RuntimeCatalog(data_dir() / "native-plugin-runtimes")
    try:
        catalog.load(manifest.name, fingerprint=fingerprint, sdk_sha256=code_digest())
    except (RuntimePluginOutdated, RuntimeSdkOutdated):
        refresh(catalog, manifest, fingerprint, checkpoint=checkpoint, rebind=True,
                authorize=lambda: verify_installation() and compute_fingerprint(manifest.root) == fingerprint)
    except FileNotFoundError:
        if not allow_bootstrap:
            raise
        from core.plugin_bootstrap import ensure_runtime
        ensure_runtime(manifest)
        if not verify_installation():
            raise ValueError("native plugin installation receipt changed")


def _restore_plugin_runtime(catalog, manifest, fingerprint, *, checkpoint, authorize):
    from core.plugin_trust import trusted_record

    record = trusted_record(manifest.name) or {}
    if record.get("fingerprint") != fingerprint or not record.get("source") or not record.get("installation_receipt"):
        raise ValueError("updated plugin has no verified installation receipt")
    prepare_update(manifest, record["installation_receipt"], checkpoint=checkpoint,
                   authorize=lambda: authorize() and trusted_record(manifest.name) == record,
                   allow_bootstrap=False)


_MAINTENANCE = {RuntimeSdkOutdated: _refresh_sdk, RuntimePluginOutdated: _restore_plugin_runtime}


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
