"""Prepare native plugin runtimes from reviewed offline inputs."""
from pathlib import Path

from core.native_interpreter import probe
from core.native_runtime import inventory, stage, absolute_path
from core.native_runtime_prepare import assemble
from core.native_runtime_catalog import RuntimeCatalog
from core.paths import data_dir
from core.plugin import load_manifest
from core.plugin_lock import read_lock, parse_lock
from core.plugin_runtime_inventory import snapshot
from core.plugin_trust import compute_fingerprint


def build(path: Path, dependencies: list[str], python_runtime: Path, *,
          wheels: Path, lock_file: Path) -> str:
    """Never execute supplied Python or package setup code on the host."""
    from core.native_sandbox import get_backend
    get_backend()
    manifest = load_manifest(path)
    if manifest is None:
        raise ValueError("invalid plugin manifest")
    fingerprint = compute_fingerprint(path)
    requirements = read_lock(lock_file, dependencies)
    _, packages = parse_lock(requirements)
    if "psutil" not in packages:
        raise ValueError("native Windows SDK requires pinned psutil")
    wheel_files = inventory(wheels)
    if any(not name.endswith(".whl") or "/" in name for name in wheel_files):
        raise ValueError("wheel directory must contain only ordinary wheel files")
    from core.native_preparation import workspace
    with workspace() as temporary:
        base = absolute_path(temporary)
        interpreter = base / "interpreter"
        stage(python_runtime, interpreter, inventory(python_runtime))
        tags, markers = probe(interpreter, staging_parent=base)
        candidate = base / "candidate"
        record = assemble(interpreter, candidate,
                          wheels=[Path(wheels) / name for name in sorted(wheel_files)],
                          lock_text=requirements, sdk_files=snapshot(), fingerprint=fingerprint,
                          compatible_tags=tags, marker_environment=markers)
        def unchanged(name, expected):
            current = load_manifest(path)
            return (current is not None and current.name == name
                    and compute_fingerprint(path) == expected)
        return RuntimeCatalog(data_dir() / "native-plugin-runtimes").publish(
            manifest.name, candidate, record, check_identity=unchanged)
