"""Delete a verified Windows executable after its running process exits."""
import base64
import ctypes
import json
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4


_WORKER = r"""
$ErrorActionPreference = 'Stop'
$job = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__JOB__')) | ConvertFrom-Json
function Assert-Ordinary([string]$path) {
    $item = Get-Item -LiteralPath $path -Force
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked uninstall path' }
        if ($item -is [IO.FileInfo]) { $item = $item.Directory }
        else { $item = $item.Parent }
    }
}
try {
    Assert-Ordinary $job.lock
    $lock = [IO.File]::Open($job.lock, 'Open', 'ReadWrite', 'ReadWrite')
    try {
        for ($attempt = 0; ; $attempt++) {
            try { $lock.Lock(0, 1); break }
            catch [IO.IOException] {
                if ($attempt -ge 39) { throw }
                Start-Sleep -Milliseconds 250
            }
        }
        Assert-Ordinary $job.target
        $hash = [Security.Cryptography.SHA256]::Create()
        try {
            $stream = [IO.File]::OpenRead($job.target)
            try { $digest = [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '') }
            finally { $stream.Dispose() }
        } finally { $hash.Dispose() }
        if ($digest -ne $job.digest) {
            throw 'OpsKit executable changed'
        }
        if ($job.marker) {
            Assert-Ordinary $job.marker
            if ([IO.File]::ReadAllText($job.marker) -cne "1`r`n") { throw 'Install marker changed' }
        }
        for ($attempt = 0; ; $attempt++) {
            try { [IO.File]::Delete($job.target); break }
            catch [IO.IOException], [UnauthorizedAccessException] {
                if ($attempt -ge 119) { throw }
                Start-Sleep -Milliseconds 250
            }
        }
        if ($job.marker) {
            $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
            if ($null -ne $key) {
                try {
                    $value = $key.GetValue('Path', $null, 'DoNotExpandEnvironmentNames')
                    if ($null -ne $value) {
                        $directory = [IO.Path]::GetDirectoryName($job.target)
                        $kept = @($value.Split(';') -ine $directory)
                        if ($kept.Count -ne $value.Split(';').Count) {
                            if ($kept.Count -eq 0) { $key.DeleteValue('Path', $false) }
                            else { $key.SetValue('Path', ($kept -join ';'), $key.GetValueKind('Path')) }
                        }
                    }
                } finally { $key.Dispose() }
            }
            [IO.File]::Delete($job.marker)
        }
        [IO.File]::Delete($job.state)
    } finally { $lock.Dispose() }
    if (-not [IO.File]::Exists($job.target)) {
        [IO.File]::Delete($job.lock)
        $lockDir = [IO.Path]::GetDirectoryName($job.lock)
        if ([IO.Directory]::GetFileSystemEntries($lockDir).Length -eq 0) { [IO.Directory]::Delete($lockDir) }
    }
    if ($job.full) {
        $installDir = [IO.Path]::GetDirectoryName($job.target)
        if ([IO.Directory]::GetFileSystemEntries($installDir).Length -eq 0) { [IO.Directory]::Delete($installDir) }
    }
} catch {
    [IO.File]::WriteAllText($job.failure, $_.Exception.Message)
    exit 1
}
"""


def schedule(plan, digest, state):
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise OSError("Windows system directory unavailable")
    shell = Path(buffer.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    failure = Path(tempfile.gettempdir()) / f"opskit-uninstall-{uuid4().hex}.error"
    job = {
        "target": str(plan.executable), "digest": digest,
        "state": str(state), "lock": str(state.with_name(state.name.removesuffix(".state.json") + ".lock")),
        "marker": str(plan.path_marker) if plan.path_marker else None, "failure": str(failure),
        "full": plan.mode == "full",
    }
    payload = base64.b64encode(json.dumps(job).encode()).decode("ascii")
    command = base64.b64encode(_WORKER.replace("__JOB__", payload).encode("utf-16-le")).decode("ascii")
    subprocess.Popen(
        [str(shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=shell.parent,
    )
    return failure
