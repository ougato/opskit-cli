"""Exercise an installed console entry point outside the source checkout."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def verify_windows_python_routes(python, environment, outside, output, env):
    """Exercise installed route code without writing user PATH or profiles."""
    probe = r'''
import json, os, sys
from pathlib import Path
from software.recipes.python import common, windows
home = Path(sys.argv[1])
home.mkdir(exist_ok=False)
Path.home = staticmethod(lambda: home)
os.environ['USERPROFILE'] = str(home)
os.environ['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
common.load_snapshot = lambda: {}
driver = windows.WindowsDriver()
driver._inject_path = lambda *a, **kw: kw['verify_commands']()
driver._remove_path = lambda *a, **kw: kw['verify_commands']()
script = home / 'arguments.py'
script.write_text('import json,sys; print(json.dumps({"args":sys.argv[1:],"prefix":sys.prefix})); sys.exit(37)')
arguments = ['\u4e2d\u6587 \u7a7a\u683c', 'literal & value', 'percent%', 'bang!', 'quote"inside', 'trailing\\']
records = []
import subprocess
try:
    for binary in (Path(sys.base_prefix) / 'python.exe', Path(sys.executable)):
        driver.apply_version_link(str(binary))
        driver.install_shim('')
        command = common.shim_dir() / 'python.cmd'
        line = '"' + str(command) + '" "' + str(script) + '" ' + subprocess.list2cmdline(arguments)
        result = subprocess.run('cmd.exe /d /v:off /s /c "' + line + '"', capture_output=True,
                                text=True, encoding='utf-8', timeout=15)
        if result.returncode != 37:
            raise RuntimeError('installed Python route exit code mismatch')
        payload = json.loads(result.stdout)
        if payload['args'] != arguments or Path(payload['prefix']).resolve() != driver._route_plan(str(binary))[0]:
            raise RuntimeError('installed Python route argument or environment mismatch')
        records.append({'binary': str(binary), 'exit_code': result.returncode})
finally:
    driver.remove_shim()
if os.path.lexists(common.shim_dir() / '.python-current') or list(common.shim_dir().glob('*.cmd')):
    raise RuntimeError('installed Python route resources remain')
print(json.dumps({'module': windows.__file__, 'checks': records, 'resources_removed': True}))
'''
    result = subprocess.run([str(python), "-I", "-c", probe, str(output / "python-route-home")],
                            cwd=outside, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=60, check=True)
    report = json.loads(result.stdout)
    if not Path(report["module"]).resolve().is_relative_to(environment):
        raise ValueError("Python route imported code outside the installed environment")
    return report


def verify(environment, output):
    environment = Path(environment).resolve()
    binaries = environment / ("Scripts" if os.name == "nt" else "bin")
    python = binaries / ("python.exe" if os.name == "nt" else "python")
    entry = binaries / ("opskit.exe" if os.name == "nt" else "opskit")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    outside = output / "outside-source"
    outside.mkdir()
    data = output / "runtime-must-remain-absent"
    env = dict(os.environ, OPSKIT_DATA_DIR=str(data), VIRTUAL_ENV=str(environment), PYTHONIOENCODING="utf-8")
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    probe = """
import json, main, core, typer.main, psutil
root = typer.main.get_command(main.app)
skipped = []
def paths(command, prefix):
    if command.context_settings.get('help_option_names') == []:
        skipped.append({'args': prefix, 'reason': 'help is owned by the routed plugin'})
        result = []
    else:
        result = [prefix]
    if hasattr(command, 'commands'):
        for name, child in command.commands.items():
            result.extend(paths(child, prefix + [name]))
    return result
commands = paths(root, [])
for absent_pid in range(2147483647, 2147483547, -1):
    if not psutil.pid_exists(absent_pid):
        break
else:
    raise ValueError('no absent PID in the bounded fixture range')
print(json.dumps({'main': main.__file__, 'core': core.__file__, 'commands': commands, 'skipped_help': skipped, 'absent_pid': absent_pid}))
"""
    result = subprocess.run([str(python), "-I", "-c", probe], cwd=outside, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    provenance = json.loads(result.stdout)
    for name in ("main", "core"):
        if not Path(provenance[name]).resolve().is_relative_to(environment):
            raise ValueError("CLI imported code outside the verification environment")
    cases = [(path + ["--help"], 0, False) for path in provenance["commands"]]
    cases.extend([
        (["--output", "json", "--version"], 0, True),
        (["--output", "json", "software", "list"], 0, True),
        (["--output", "json", "plugin", "inspect-locks"], 0, True),
        (["--output", "json", "plugin", "inspect-workspaces"], 0, True),
        (["--output", "json", "plugin", "inspect-state", "acceptance_missing"], 0, True),
        (["--output", "json", "plugin", "inspect-state", "acceptance_missing", "--caller", "platform"], 0, True),
        (["--output", "json", "plugin", "repair-state", "acceptance_missing", "--record-sha256", "BAD"], 2, True),
        (["--output", "json", "monitor", "cpu", "--once"], 0, True),
        (["--output", "json", "update", "status", "--timeout", "10"], 0, True),
        (["--output", "json", "update", "reconcile"], 2, True),
        (["--output", "json", "monitor", "processes", "--pid", "1", "--limit", "15"], 2, True),
        (["--output", "json", "monitor", "processes", "--pid", str(os.getpid()), "--once"], 0, True),
        (["--output", "json", "monitor", "processes", "--pid", str(provenance["absent_pid"]), "--once"], 1, True),
        (["--output", "json", "network", "dns", "::1", "--family", "ipv6"], 2, True),
    ])
    records = []
    for index, (arguments, expected, structured) in enumerate(cases):
        result = subprocess.run([str(entry), *arguments], cwd=outside, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        (output / f"{index:03d}.stdout").write_text(result.stdout, encoding="utf-8")
        (output / f"{index:03d}.stderr").write_text(result.stderr, encoding="utf-8")
        if result.returncode != expected:
            raise RuntimeError("installed CLI failed: " + " ".join(arguments))
        if structured:
            payload = json.loads(result.stdout)
            if (not isinstance(payload, dict)
                    or set(payload) != {"schema_version", "ok", "data", "error"}
                    or type(payload["schema_version"]) is not int
                    or payload["schema_version"] != 1
                    or payload["ok"] is not (expected == 0)):
                raise ValueError("installed CLI result envelope mismatch")
            error = payload["error"]
            if expected == 0:
                if error is not None:
                    raise ValueError("successful installed CLI returned an error")
            elif (not isinstance(error, dict) or set(error) != {"code", "message"}
                  or type(error["code"]) is not int or error["code"] != expected
                  or not isinstance(error["message"], str) or not error["message"].strip()):
                raise ValueError("failed installed CLI returned an invalid error")
            if "processes" in arguments and "--once" in arguments:
                row = payload["data"][0]
                if row["rate_schema_version"] != 2 or row["cpu_percent"] is not None:
                    raise ValueError("installed PID first/error sample is not nullable schema2")
                if row["rate_status"] != ("initial" if expected == 0 else "gone"):
                    raise ValueError("installed PID first/error sample has an incorrect reason")
        if data.exists():
            raise ValueError("read-only installed CLI initialized runtime storage")
        records.append({"args": arguments, "exit_code": result.returncode})
    report = {"provenance": provenance, "checks": records, "runtime_storage_created": False}
    if os.name == "nt":
        report["windows_python_routes"] = verify_windows_python_routes(python, environment, outside, output, env)
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"help_commands": len(provenance["commands"]), "checks": len(records), "output": str(output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.environment, arguments.output)))
