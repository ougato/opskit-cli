"""Coordinate native links, owned commands and one user PATH operation."""
from pathlib import Path

from software._shared.mutation import versioned_mutation
from software._shared.windows_commands import version_routes
from software._shared.windows_junction import junction_transaction, remove_junction


@versioned_mutation
def activate_route(driver, bin_dir, *, managed_root, feature, commands, bin_subdir=False):
    target = Path(bin_dir).parent if bin_subdir else Path(bin_dir)
    entry = driver._active_junction()
    with junction_transaction(entry, managed_root) as route:
        if driver._set_junction(str(target)):
            route.checkpoint(target)
            active_bin = entry / "bin" if bin_subdir else entry
            driver._inject_path(str(active_bin), verify_commands=route.verify)
        else:
            route.checkpoint(unchanged=True)
            directory = driver._win_bin_dir()
            replacements = version_routes(directory, bin_dir, commands, feature, managed_root)
            with driver._command_files(directory, replacements) as verify_commands:
                def verify():
                    verify_commands()
                    route.verify()
                driver._inject_path(str(directory), verify_commands=verify)


@versioned_mutation
def restore_route(driver, *, managed_root, commands, bin_subdir=False):
    entry, directory = driver._active_junction(), driver._win_bin_dir()
    active_bin = entry / "bin" if bin_subdir else entry
    with junction_transaction(entry, managed_root) as route:
        with driver._command_files(directory, {name + ".cmd": None for name in commands}) as verify_commands:
            remove_junction(entry, managed_root)
            route.checkpoint(removed=True)
            def verify():
                verify_commands()
                route.verify()
            driver._remove_paths((str(active_bin), str(directory)), verify_commands=verify)
