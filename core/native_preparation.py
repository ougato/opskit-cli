"""Receipt-backed preparation workspaces, recoverable after supervisor death."""
from contextlib import contextmanager, ExitStack
import os
import uuid

from core import native_catalog_cleanup as cleanup
from core import native_catalog_ownership as ownership
from core.native_catalog_leases import reader
from core.plugin_state import _directory
from core.resource_lock import ResourceLock


@contextmanager
def workspace(*, cleanup_timeout=180):
    """Unknown/pre-receipt directories remain unknown; never adopt by name."""
    root = cleanup._root("prepare")
    root.mkdir(parents=True, exist_ok=True)
    from core.plugin_disk_policy import check_space
    check_space(root)
    for ancestor in (root, *root.parents):
        _directory(ancestor)
    lock = ResourceLock(lambda: root / ".catalog.lock", label="native preparation")
    token = uuid.uuid4().hex
    with ExitStack() as lease:
        with lock.hold():
            objects = root / "objects"
            objects.mkdir(exist_ok=True)
            _directory(objects)
            # Bound retained and active preparations; unknown names count too.
            with os.scandir(objects) as entries:
                for index, _ in enumerate(entries):
                    if index >= 7:
                        raise ValueError("native preparation capacity exhausted")
            target = objects / token
            target.mkdir()
            ownership.record_creation(root, token)
            _, digest = ownership.validate(root, token)
            lease.enter_context(reader(root, token))
        failure = None
        try:
            yield target
        except BaseException as error:
            failure = error
            raise
        finally:
            # Stop this reader before the supervised collector acquires the
            # catalog lock and rejects any remaining active reader.
            lease.close()
            try:
                cleanup.collect("prepare", token, digest, timeout=cleanup_timeout)
            except BaseException:
                if failure is None:
                    raise
                from core.cleanup import add_exception_note
                add_exception_note(failure, "native preparation cleanup incomplete; receipt retained")
