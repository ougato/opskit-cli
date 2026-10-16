"""Transfer private read descriptors only during an owned multiprocessing spawn."""
import os


def _received_file(value, windows):
    if windows:
        import msvcrt
        descriptor = msvcrt.open_osfhandle(value, os.O_RDONLY | os.O_BINARY)
    else:
        descriptor = value.detach()
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


class _SpawnFile:
    """No named reopening, byte serialization or resource-sharer server."""
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def __reduce__(self):
        from multiprocessing import reduction
        from multiprocessing.context import get_spawning_popen
        spawning = get_spawning_popen()
        if spawning is None:
            raise RuntimeError("private file transfer requires an owned spawn")
        if os.name == "nt":
            import msvcrt
            value = spawning.duplicate_for_child(msvcrt.get_osfhandle(self.descriptor))
        else:
            value = reduction.DupFd(self.descriptor)
        return _received_file, (value, os.name == "nt")
