"""Platform-pinned inputs for automatic plugin installation, never repo scripts."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
from urllib.parse import urlsplit

from core.native_sandbox import NativeSandboxError, get_backend
from core.paths import data_dir
from core.plugin_runtime_inventory import _read_file
from core.resource_lock import ResourceLock

_HOSTS = frozenset({"www.python.org", "github.com", "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com", "files.pythonhosted.org"})


def _manifest():
    get_backend().probe()
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise NativeSandboxError("platform_unsupported")
    path = Path(__file__).with_name("plugin-bootstrap.json")
    value = json.loads(_read_file(path, Path(path.anchor), 131072))
    if value.get("schema_version") != 1 or value.get("platform") != "win_amd64":
        raise ValueError("invalid platform input manifest")
    assets = value["assets"]
    names = set()
    for asset in assets:
        name = asset["filename"]
        if (not isinstance(name, str) or Path(name).name != name or any(c in name for c in "\\:/")
                or name in names or asset["kind"] not in {"python", "git", "wheel"}
                or type(asset["size"]) is not int or not 0 < asset["size"] <= 128 * 1024**2
                or len(asset["sha256"]) != 64):
            raise ValueError("invalid platform input asset")
        _url(asset["url"])
        names.add(name)
    if len(assets) > 64 or sum(a["kind"] == "python" for a in assets) != 1 or sum(a["kind"] == "git" for a in assets) != 1:
        raise ValueError("invalid platform input selection")
    return assets


def _url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in _HOSTS or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError("unapproved platform input source")
    return value


def _download(asset, root, deadline):
    """Reuse only hash-verified complete bytes; publish downloads atomically."""
    import httpx
    from core.secrets import atomic_write
    from core.plugin_state import _directory
    _directory(root)
    path = root / asset["filename"]
    try:
        data = _read_file(path, Path(path.anchor), asset["size"])
        if len(data) == asset["size"] and hashlib.sha256(data).hexdigest() == asset["sha256"]:
            return path
    except FileNotFoundError:
        pass
    # A corrupt ordinary cache file may be replaced; unsafe files are refused.
    from core.plugin_disk_policy import check_space
    check_space(root, reserve_bytes=asset["size"])
    url = asset["url"]
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        for _ in range(6):
            if time.monotonic() >= deadline:
                raise NativeSandboxError("execution_timeout")
            with client.stream("GET", _url(url), headers={"Accept-Encoding": "identity"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    from urllib.parse import urljoin
                    url = _url(urljoin(url, response.headers["location"]))
                    continue
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("encoded platform input rejected")
                data = bytearray()
                for block in response.iter_bytes(65536):
                    if time.monotonic() >= deadline:
                        raise NativeSandboxError("execution_timeout")
                    data.extend(block)
                    if len(data) > asset["size"]:
                        raise ValueError("platform input exceeds pinned size")
                if len(data) != asset["size"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
                    raise ValueError("platform input checksum mismatch")
                atomic_write(path, bytes(data))
                return path
    raise ValueError("too many platform input redirects")


@contextmanager
def _inputs(kinds, progress):
    from core.native_preparation import workspace
    assets = _manifest()
    root = data_dir() / "cache" / "plugin-bootstrap"
    lock = ResourceLock(lambda: root / ".inputs.lock", label="plugin installation inputs")
    with lock.hold():
        deadline = time.monotonic() + 600
        with workspace() as temporary:
            selected = []
            for asset in assets:
                if asset["kind"] in kinds:
                    progress("download", asset["filename"])
                    selected.append((asset, _download(asset, root, deadline)))
            yield Path(temporary), selected


def ensure_fetch(progress=lambda phase, name="": None):
    from core.native_fetch_catalog import FetchCatalog
    catalog = FetchCatalog()
    try:
        catalog.load()
        return
    except (FileNotFoundError, ValueError):
        pass
    progress("tools", "")
    with _inputs({"python", "git"}, progress) as (base, selected):
        from core.archive import extract_zip
        from core.archive_reader import open_zip
        for asset, archive in selected:
            target = base / asset["kind"]
            with open_zip(archive) as content:
                extract_zip(content, target)
        # MinGit's object reader needs only git.exe and its adjacent DLLs.
        # Preserve the official layout; snapshot that binary directory intact.
        catalog.prepare(base / "python", base / "git" / "ucrt64" / "bin")
    catalog.load()


def ensure_runtime(manifest, progress=lambda phase, name="": None):
    from core.native_runtime_catalog import RuntimeCatalog
    from core.plugin_runtime_inventory import code_digest
    from core.plugin_trust import compute_fingerprint
    catalog = RuntimeCatalog(data_dir() / "native-plugin-runtimes")
    fingerprint = compute_fingerprint(manifest.root)
    try:
        catalog.load(manifest.name, fingerprint=fingerprint, sdk_sha256=code_digest())
        return
    except (FileNotFoundError, ValueError):
        pass
    ensure_fetch(progress)
    progress("runtime", manifest.name)
    from core.native_fetch_catalog import FetchCatalog
    from core.plugin_runtime import build
    with FetchCatalog().acquire() as (runtime, _):
        with _inputs({"wheel"}, progress) as (base, selected):
            wheels = base / "wheels"
            wheels.mkdir()
            requirements = []
            for asset, path in selected:
                # _download verified a bounded ordinary file under the input lock.
                content = _read_file(path, Path(path.anchor), asset["size"])
                if hashlib.sha256(content).hexdigest() != asset["sha256"]:
                    raise ValueError("wheel cache changed")
                (wheels / asset["filename"]).write_bytes(content)
                requirements.append(asset["package"] + "==" + asset["version"] + " --hash=sha256:" + asset["sha256"])
            lock = base / "requirements.lock"
            lock.write_text("\n".join(requirements) + "\n", encoding="utf-8")
            build(Path(manifest.root), [], runtime / "python", wheels=wheels, lock_file=lock)
    catalog.load(manifest.name, fingerprint=fingerprint, sdk_sha256=code_digest())
