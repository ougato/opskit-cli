"""Bounded interpreter identity checks shared by Python platform drivers."""
import re
import subprocess


def interpreter_version(executable):
    from core.process import capture
    try:
        result = capture([executable, "--version"], timeout=5,
                         max_bytes=4096, stdin=subprocess.DEVNULL)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip() or result.stderr.strip()
    match = re.fullmatch(r"Python ([0-9]+\.[0-9]+\.[0-9]+)", line)
    return match.group(1) if match else None


def command_version(commands):
    import shutil
    for command in commands:
        executable = shutil.which(command)
        if executable:
            version = interpreter_version(executable)
            if version:
                return version
    return None
