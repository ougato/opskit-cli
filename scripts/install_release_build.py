"""Create a fresh CPython 3.12 build environment from the exported hash locks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]

# Nuitka 4.3's POSIX handler reaps the child, then main reads an undefined
# wait status. Remove this workaround when an upstream fix is pinned and tested.
BOOTSTRAP_SHA256 = "3ed26f09286629c3898452db484c7153827127521309c409395b197b1d8d7b95"
PARENT_MONITOR_SHA256 = "8c6052f2b309924b5686e652c0d4f050dff5c0a370ef3c7f236561627605b88a"


def patch_bootstrap(source: bytes, version: str) -> bytes:
    if version != "4.3" or hashlib.sha256(source).hexdigest() != BOOTSTRAP_SHA256:
        raise ValueError("unreviewed Nuitka bootstrap; revalidate the signal workaround")
    old = b"    cleanupChildProcess(!signal_from_terminal);\n}"
    if source.count(old) != 1:
        raise ValueError("ambiguous Nuitka POSIX signal handler")
    return source.replace(old, b"    cleanupChildProcess(!signal_from_terminal);\n    _exit(128 + sig);\n}")


def patch_parent_monitor(source: bytes, version: str) -> bytes:
    if version != "4.3" or hashlib.sha256(source).hexdigest() != PARENT_MONITOR_SHA256:
        raise ValueError("unreviewed Nuitka parent monitor; revalidate the lifetime workaround")
    old = b"if (kill(onefile_ppid, 0) == -1 && errno == ESRCH)"
    if source.count(old) != 1:
        raise ValueError("ambiguous Nuitka POSIX parent monitor")
    # The inner executable is a direct fork/exec child of the launcher.
    # kill(pid, 0) also succeeds for an unreaped launcher or a reused PID.
    return source.replace(old, b"if (getppid() != (pid_t)onefile_ppid)")


def apply_signal_workaround(python: Path, target: Path) -> None:
    if os.name != "posix":
        return
    metadata = subprocess.check_output([
        str(python), "-I", "-c",
        "import importlib.metadata,json; d=importlib.metadata.distribution('Nuitka'); "
        "print(json.dumps([d.version,str(d.locate_file('nuitka/build/static_src/OnefileBootstrap.c'))]))",
    ], text=True)
    version, filename = json.loads(metadata)
    source = Path(filename).resolve()
    if not source.is_relative_to(target.resolve()):
        raise ValueError("Nuitka bootstrap is outside the fresh build environment")
    original = source.read_bytes()
    patched = patch_bootstrap(original, version)
    monitor = source.with_name("MainProgram.c")
    if not monitor.resolve().is_relative_to(target.resolve()):
        raise ValueError("Nuitka parent monitor is outside the fresh build environment")
    monitor_original = monitor.read_bytes()
    monitor_patched = patch_parent_monitor(monitor_original, version)
    # Validate both upstream files before modifying either one.
    source.write_bytes(patched)
    monitor.write_bytes(monitor_patched)
    (target / "nuitka-signal-workaround.json").write_text(json.dumps({
        "version": version, "original_sha256": BOOTSTRAP_SHA256,
        "patched_sha256": hashlib.sha256(patched).hexdigest(),
        "parent_monitor_original_sha256": PARENT_MONITOR_SHA256,
        "parent_monitor_patched_sha256": hashlib.sha256(monitor_patched).hexdigest(),
        "reason": "preserve 128+signal; detect direct-parent loss including unreaped launchers",
    }, indent=2) + "\n", encoding="utf-8")


def install(target: Path) -> Path:
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("release builds require CPython 3.12")
    # Reusing a cache could retain executable packages outside the lock.
    if os.path.lexists(target):
        raise ValueError("build environment must not already exist")
    target.mkdir(parents=True)
    temporary = target / "tmp"
    temporary.mkdir()
    environment = dict(os.environ, TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary),
                       PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    venv.EnvBuilder(with_pip=False).create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-I", "-X", "utf8", "-m", "ensurepip", "--upgrade"],
                   env=environment, check=True)
    common = [str(python), "-I", "-X", "utf8", "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
              "--index-url", "https://pypi.org/simple", "--require-hashes",
              "--no-deps", "--only-binary=:all:", "--no-cache-dir", "--progress-bar", "off"]
    # Replace ensurepip's bundled installer before processing application or
    # build artifacts. Its sole input is the reviewed, hash-locked pip wheel.
    subprocess.run([*common, "-r", str(ROOT / "requirements-installer.txt")], env=environment, check=True)
    # Install the reviewed wheel backend first; disable PEP 517's independent
    # dependency resolver when building the one explicitly allowed source tool.
    subprocess.run([*common, "-r", str(ROOT / "requirements-release-bootstrap.txt")], env=environment, check=True)
    subprocess.run([*common, "--no-binary=nuitka", "--no-build-isolation",
                    "-r", str(ROOT / "requirements-release-build.txt")], env=environment, check=True)
    subprocess.run([str(python), "-I", "-m", "pip", "check"], env=environment, check=True)
    apply_signal_workaround(python, target)
    return python


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="new environment directory; existing paths are refused")
    args = parser.parse_args()
    print(install(args.target.absolute()))
