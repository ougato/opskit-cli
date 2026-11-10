"""Refresh a verified runtime's SDK while retaining its exact dependencies."""
from pathlib import PurePosixPath
import shutil

from core.native_preparation import workspace
from core.native_runtime import absolute_path, inventory, stage
from core.plugin_runtime_inventory import code_digest, snapshot


def refresh(catalog, manifest, fingerprint, *, checkpoint, authorize):
    """Publish only if the plugin, SDK and original runtime remain current."""
    sdk = snapshot()
    sdk_sha256 = code_digest(sdk)
    with catalog.acquire_for_sdk_refresh(manifest.name, fingerprint=fingerprint) as (source, old):
        if old['sdk_sha256'] == sdk_sha256:
            return
        checkpoint()
        with workspace() as work:
            # All subsequent SDK reads, removal and writes must use the same
            # extended Windows path as stage/inventory, including long names.
            candidate = absolute_path(work / 'candidate')
            stage(source, candidate, old['files'])
            checkpoint()
            sdk_root = candidate / 'site-packages' / 'core'
            if not sdk_root.is_dir() or sdk_root.is_symlink():
                raise ValueError('native runtime SDK directory is invalid')
            shutil.rmtree(sdk_root)
            for name, content in sdk.items():
                path = PurePosixPath(name)
                if (not isinstance(content, bytes) or path.is_absolute() or len(path.parts) < 2
                        or path.parts[0] != 'core' or '..' in path.parts or '\\' in name):
                    raise ValueError('invalid platform SDK path')
                destination = candidate / 'site-packages' / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            record = dict(old, sdk_sha256=sdk_sha256, files=inventory(candidate))
            checkpoint()

            def unchanged(name, expected):
                checkpoint()
                return (name == manifest.name and expected == fingerprint
                        and catalog.is_current(name, source, old)
                        and code_digest() == sdk_sha256 and authorize())

            catalog.publish(manifest.name, candidate, record, check_identity=unchanged)
