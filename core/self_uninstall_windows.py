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
function Lock-Exclusive([string]$path) {
    Assert-Ordinary $path
    $lock = [IO.File]::Open($path, 'Open', 'ReadWrite', 'ReadWrite')
    try {
        for ($attempt = 0; ; $attempt++) {
            try { $lock.Lock(0, 1); return $lock }
            catch [IO.IOException] {
                if ($attempt -ge 39) { throw }
                Start-Sleep -Milliseconds 250
            }
        }
    } catch { $lock.Dispose(); throw }
}
function Get-Sha256([string]$path) {
    Assert-Ordinary $path
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.File]::OpenRead($path)
        try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '') }
        finally { $stream.Dispose() }
    } finally { $hash.Dispose() }
}
$managedLock = $null
try {
    if ($job.managed) {
        $managedLock = Lock-Exclusive $job.managed.lock
        if ((Get-Sha256 $job.managed.state) -ne $job.managed.digest) {
            throw 'OpsKit directory installation changed'
        }
    }
    $lock = Lock-Exclusive $job.lock
    try {
        if ((Get-Sha256 $job.target) -ne $job.digest) {
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
} finally {
    if ($managedLock) { $managedLock.Dispose() }
}
"""


def schedule(plan, digest, state, *, managed=None):
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
    if managed:
        from core.directory_install import reset_dll_directory
        from core.file_digest import file_digest

        job["managed"] = {
            "lock": str(managed.lock.path_provider()),
            "state": str(managed.state_path),
            "digest": file_digest(managed.state_path),
        }
        reset_dll_directory()
    payload = base64.b64encode(json.dumps(job).encode()).decode("ascii")
    command = base64.b64encode(_WORKER.replace("__JOB__", payload).encode("utf-16-le")).decode("ascii")
    subprocess.Popen(
        [str(shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=shell.parent,
    )
    return failure
