"""Exercise installer handoff without downloading or changing the host PATH."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_URL = "https://raw.githubusercontent.com/ougato/opskit-cli/master/bootstrap.json"
RELEASE_URL = "https://github.com/ougato/opskit-cli/releases/download/v24/"

_WINDOWS_LAUNCHER = """
using System;
using System.IO;
using System.Reflection;
public static class Launcher {
    public static void Main(string[] args) {
        File.WriteAllLines(Environment.GetEnvironmentVariable("FIXTURE_ARGS"), args);
        File.Copy(Assembly.GetExecutingAssembly().Location, args[2], true);
    }
}
"""

_WINDOWS_INSTALL = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec)
    Add-Content -LiteralPath $env:FIXTURE_DOWNLOADS -Value $Uri
    $name = ([uri]$Uri).Segments[-1]
    Copy-Item -LiteralPath (Join-Path $env:FIXTURE_ASSETS $name) -Destination $OutFile
}
$script = Get-Content -LiteralPath $env:FIXTURE_INSTALL -Raw
$mock = @'
function Add-ToUserPath {
    param([string]$Dir)
    Set-Content -LiteralPath $env:FIXTURE_PATH -Value $Dir
}
Main
'@
Invoke-Expression ($script -replace '(?m)^Main\s*\z', $mock)
"""

_UNIX_DOWNLOAD = """#!/bin/bash
set -eu
previous="${@: -2:1}"
url="${@: -1}"
printf '%s\\n' "$url" >> "$FIXTURE_DOWNLOADS"
cp -- "$FIXTURE_ASSETS/${url##*/}" "$previous"
"""


@pytest.fixture(scope="module")
def windows_launcher(tmp_path_factory):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell unavailable")
    directory = tmp_path_factory.mktemp("windows-launcher")
    source = directory / "fixture.cs"
    source.write_text(_WINDOWS_LAUNCHER, encoding="ascii")
    output = directory / "launcher.exe"
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "Add-Type -TypeDefinition (Get-Content -LiteralPath $env:FIXTURE_SOURCE -Raw) "
        "-OutputAssembly $env:FIXTURE_EXE -OutputType ConsoleApplication"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "FIXTURE_SOURCE": str(source), "FIXTURE_EXE": str(output)},
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return powershell, output


@pytest.mark.parametrize("corrupt", [None, "archive", "launcher"])
def test_directory_installer_verifies_both_assets_before_handoff(corrupt, request, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    temp = tmp_path / "temp space"
    temp.mkdir()
    home = tmp_path / "home space"
    home.mkdir()
    calls = tmp_path / "args"
    downloads = tmp_path / "downloads"
    env = {**os.environ, "FIXTURE_ASSETS": str(assets), "FIXTURE_ARGS": str(calls),
           "FIXTURE_DOWNLOADS": str(downloads), "FIXTURE_PATH": str(tmp_path / "path")}

    if sys.platform == "win32":
        powershell, launcher_exe = request.getfixturevalue("windows_launcher")
        env.update(TEMP=str(temp), LOCALAPPDATA=str(home), PROCESSOR_ARCHITECTURE="AMD64",
                   FIXTURE_INSTALL=str(ROOT / "install.ps1"))
        command = [powershell, "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_INSTALL]
        platform = "windows-x64"
        launcher = assets / f"opskit-{platform}-launcher.exe"
        shutil.copyfile(launcher_exe, launcher)
        target = home / "opskit" / "opskit.exe"
        extra = {}
    elif sys.platform.startswith("linux"):
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("Bash unavailable")
        program = tmp_path / "bin"
        program.mkdir()
        (program / "curl").write_text(_UNIX_DOWNLOAD, encoding="ascii")
        (program / "id").write_text("#!/bin/sh\nprintf '1000\\n'\n", encoding="ascii")
        (program / "uname").write_text("#!/bin/sh\ncase $1 in -s) echo Linux;; -m) echo x86_64;; esac\n",
                                       encoding="ascii")
        for stub in program.iterdir():
            stub.chmod(0o755)
        target = home / ".local/bin/opskit"
        env.update(HOME=str(home), TMPDIR=str(temp), PATH=f"{program}:{target.parent}:{os.environ['PATH']}")
        command = [bash, str(ROOT / "install.sh")]
        platform = "linux-x64"
        launcher = assets / f"opskit-{platform}-launcher"
        launcher.write_text("#!/bin/bash\nset -eu\nprintf '%s\\n' \"$@\" > \"$FIXTURE_ARGS\"\ncp -- \"$0\" \"$3\"\n",
                            encoding="ascii")
        extra = {"start_new_session": True}
    else:
        pytest.skip("Directory installers are supported on Windows and Linux")

    (assets / "bootstrap.json").write_text(json.dumps({
        "schema_version": 2, "channel": "stable", "latest_build": 24,
        "display": "v24", "min_build": 1, "kill_switch": False,
    }, indent=2), encoding="ascii")
    archive = assets / f"opskit-{platform}.tar.xz"
    archive.write_bytes(b"verified directory archive fixture")
    for label, artifact in [("launcher", launcher), ("archive", archive)]:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if label == corrupt:
            digest = "0" * 64
        artifact.with_name(artifact.name + ".sha256").write_text(f"{digest}  {artifact.name}\n", encoding="ascii")

    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous OpsKit entry")
    result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=45, **extra)
    diagnostics = (result.stdout + result.stderr).decode(errors="replace")
    expected_urls = [BOOTSTRAP_URL, RELEASE_URL + launcher.name, RELEASE_URL + launcher.name + ".sha256"]
    if corrupt != "launcher":
        expected_urls += [RELEASE_URL + archive.name, RELEASE_URL + archive.name + ".sha256"]
    assert downloads.read_text().splitlines() == expected_urls, diagnostics
    assert not list(temp.iterdir())
    if corrupt:
        assert result.returncode != 0
        assert "SHA256 mismatch" in diagnostics, diagnostics
        assert target.read_bytes() == b"previous OpsKit entry"
        assert not calls.exists()
        assert not Path(env["FIXTURE_PATH"]).exists()
    else:
        assert result.returncode == 0, diagnostics
        mode, archive_path, destination, version = calls.read_text().splitlines()
        assert (mode, destination, version) == ("--opskit-internal-install", str(target), "v24")
        assert Path(archive_path).is_absolute() and Path(archive_path).name == archive.name
        assert target.read_bytes() == launcher.read_bytes()
