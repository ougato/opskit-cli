"""Stable executable for Windows and Linux directory installations."""

from pathlib import Path
import re
import subprocess
import sys

from core.directory_install import Install, InstallError, bootstrap, reset_dll_directory


def launch(install: Install, arguments: list[str]) -> int:
    state = install.state()
    selected = state.active
    previous = state.previous
    if previous is not None and not install.healthy(selected):
        if not install.healthy(previous):
            raise InstallError("both OpsKit versions failed startup")
        install.revert(selected, previous)
        selected = previous

    reset_dll_directory()
    # Only retry a launch failure, never an application exit code. A real command
    # may already have performed operations by the time it returns an error.
    try:
        child = subprocess.Popen([str(install.executable(selected)), *arguments], env=install.env())
    except OSError:
        if previous is None or selected == previous or not install.healthy(previous):
            raise
        install.revert(selected, previous)
        child = subprocess.Popen([str(install.executable(previous)), *arguments], env=install.env())
    try:
        code = child.wait()
    except KeyboardInterrupt:
        # Both processes share the console / foreground process group and the
        # child receives Ctrl-C directly. Let it finish its cancellation path.
        code = child.wait()
    return 128 - code if sys.platform != "win32" and code < 0 else code


def main(arguments: list[str]) -> int:
    source = Path(sys.executable).resolve(strict=True)
    if arguments and arguments[0] == "--opskit-internal-install":
        if len(arguments) != 4 or not re.fullmatch(r"v[1-9][0-9]{0,8}", arguments[3]):
            return 2
        bootstrap(source, Path(arguments[1]), Path(arguments[2]), int(arguments[3][1:]))
        return 0
    return launch(Install(source), arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, RuntimeError) as error:
        # The launcher has no application locale or config dependency. Report a
        # stable troubleshooting identifier; release transport errors stay in OpsKit.
        print(f"OpsKit [directory-launcher/{type(error).__name__}]: {error}", file=sys.stderr)
        raise SystemExit(1) from None
