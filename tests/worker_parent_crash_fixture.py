"""Independent parent, deliberately killed without running supervisor finally."""
import json
import os
from pathlib import Path
import sys


def blocked(connection, marker):
    import psutil
    read, write = os.pipe()
    Path(marker).write_text(json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time()}))
    os.read(read, 1)
    os.close(read)
    os.close(write)


def blocked_session(connection, buffer, length, requested):
    requested.wait()
    request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
    blocked(connection, request["source"])


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["OPSKIT_DATA_DIR"] = str(Path(sys.argv[1]).parent / "state")
    if len(sys.argv) > 2 and sys.argv[2] == "session":
        from core import plugin_inspection as inspection
        inspection._inspection_session = blocked_session
        reader = inspection.ContentInspector()
        reader.inspect(sys.argv[1], verify_checksums=False, timeout=60)
    else:
        from network.worker import run
        run(blocked, (sys.argv[1],), timeout=60, label="parent crash fixture")
