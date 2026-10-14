"""Windows delayed replacement from a private, digest-bound job snapshot."""
from pathlib import Path
import os
import shutil
import subprocess
from uuid import uuid4


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def script(*, target, artifact, result, health, lock, old_digest, new_digest,
           version, parent_pid, recovery=None, timeout=120, target_lock=None):
    """All substituted values are literals; file operations never use globs."""
    import json
    recovery = recovery or {}
    transaction = recovery.get("transaction")
    checkpoint = ""
    prepared_record = applied_record = ""
    if transaction:
        from core.update_target_state import path
        checkpoint = path(Path(target))
        prepared_record = json.dumps(transaction)
        applied_record = json.dumps({**transaction, "phase": "applied"})
    health_data = {"build": version, "confirmed": False, "fails": 0,
                   "backup_id": recovery.get("backup_id")}
    if recovery.get("job_id"):
        health_data["job_id"] = recovery["job_id"]
    health_record = json.dumps(health_data)
    return f"""$ErrorActionPreference = 'Stop'
$pid_to_wait = {int(parent_pid)}
$target = {_literal(target)}
$artifact = {_literal(artifact)}
$result = {_literal(result)}
$health = {_literal(health)}
$lockPath = {_literal(lock)}
$targetLockPath = {_literal(target_lock or lock)}
$oldDigest = {_literal(old_digest)}
$newDigest = {_literal(new_digest)}
$healthRecord = {_literal(health_record)}
$jobId = {_literal(recovery.get("job_id", ""))}
$runningRecord = {_literal(Path(result).parent / "running.json")}
$checkpoint = {_literal(checkpoint)}
$preparedRecord = {_literal(prepared_record)}
$appliedRecord = {_literal(applied_record)}
$version = {int(version)}
$budget = {float(timeout)}
$clock = [Diagnostics.Stopwatch]::StartNew()
$stage = $null
$lockFile = $null
$lockHandles = @()
$locked = $false
$committed = $false
$phase = 'initialization'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class OpsKitReplace {{
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool MoveFileEx(string source, string target, int flags);
}}
'@
function Deadline {{
    if ($clock.Elapsed.TotalSeconds -ge $budget) {{ throw 'deadline_exceeded' }}
}}
function Digest($path) {{
    $source = [IO.File]::OpenRead($path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {{ return ([BitConverter]::ToString($algorithm.ComputeHash($source))).Replace('-', '').ToLowerInvariant() }}
    finally {{ $source.Dispose(); $algorithm.Dispose() }}
}}
function AtomicRecord($path, $body) {{
    $tmp = $path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {{
        $bytes = [Text.Encoding]::UTF8.GetBytes($body)
        $output = [IO.File]::Open($tmp, 'CreateNew', 'Write', 'None')
        try {{ $output.Write($bytes, 0, $bytes.Length); $output.Flush($true) }}
        finally {{ $output.Dispose() }}
        if (-not [OpsKitReplace]::MoveFileEx($tmp, $path, 9)) {{ throw 'record_commit_failed' }}
    }} finally {{ if ([IO.File]::Exists($tmp)) {{ [IO.File]::Delete($tmp) }} }}
}}
try {{
    if ($jobId -ne '') {{
        $phase = 'launch_acknowledgement'
        $selfProcess = [Diagnostics.Process]::GetCurrentProcess()
        try {{
            $ack = @{{ schema_version = 1; job_id = $jobId; version = $version;
                       pid = $selfProcess.Id;
                       created_ticks = $selfProcess.StartTime.ToUniversalTime().ToFileTimeUtc();
                       script = $PSCommandPath; executable = $selfProcess.MainModule.FileName }}
            AtomicRecord $runningRecord ($ack | ConvertTo-Json -Compress)
        }} finally {{ $selfProcess.Dispose() }}
    }}
    $phase = 'parent_wait'
    # WaitForExit uses the exact parent process handle, not a shell command.
    if ($pid_to_wait -gt 0) {{
        $parent = $null
        try {{ $parent = [Diagnostics.Process]::GetProcessById($pid_to_wait) }} catch [ArgumentException] {{}}
        if ($null -ne $parent) {{
            try {{
                $remaining = [Math]::Max(1, [int][Math]::Ceiling(($budget - $clock.Elapsed.TotalSeconds) * 1000))
                if (-not $parent.WaitForExit($remaining)) {{ throw 'parent_wait_timeout' }}
            }} finally {{ $parent.Dispose() }}
        }}
    }}
    Deadline
    $phase = 'resource_lock'
    $lockPaths = if ($lockPath -eq $targetLockPath) {{ @($lockPath) }} else {{ @($lockPath, $targetLockPath) }}
    foreach ($oneLock in $lockPaths) {{
        $lockFile = [IO.File]::Open($oneLock, 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
        $locked = $false
        while (-not $locked) {{
            Deadline
            try {{ $lockFile.Lock(0, 1); $locked = $true }}
            catch [IO.IOException] {{ Start-Sleep -Milliseconds 100 }}
        }}
        $lockHandles += $lockFile
        $lockFile = $null
    }}
    foreach ($path in @($target, $artifact)) {{
        $entry = New-Object IO.FileInfo($path)
        if (-not $entry.Exists -or ($entry.Attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint))) {{
            throw 'invalid_artifact_path'
        }}
    }}
    $phase = 'transaction_validation'
    if ($checkpoint -ne '') {{
        $entry = New-Object IO.FileInfo($checkpoint)
        if (-not $entry.Exists -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {{ throw 'invalid_checkpoint' }}
        $recordFile = [IO.File]::OpenRead($checkpoint)
        try {{
            $recordBuffer = New-Object byte[] 65537
            $recordSize = 0
            while ($recordSize -lt 65537) {{
                $count = $recordFile.Read($recordBuffer, $recordSize, 65537 - $recordSize)
                if ($count -eq 0) {{ break }}
                $recordSize += $count
            }}
            if ($recordSize -gt 65536 -or [Text.Encoding]::UTF8.GetString($recordBuffer, 0, $recordSize) -cne $preparedRecord) {{
                throw 'transaction_superseded'
            }}
        }} finally {{ $recordFile.Dispose() }}
    }}
    $phase = 'target_validation'
    if ((Digest $target) -ne $oldDigest) {{
        throw 'target_changed'
    }}
    $stage = [IO.Path]::Combine([IO.Path]::GetDirectoryName($target),
                              '.opskit-replace-' + [Guid]::NewGuid().ToString('N'))
    $phase = 'artifact_copy'
    $inputFile = [IO.File]::OpenRead($artifact)
    try {{
        $output = [IO.File]::Open($stage, 'CreateNew', 'Write', 'None')
        try {{
            $buffer = New-Object byte[] 1048576
            while (($count = $inputFile.Read($buffer, 0, $buffer.Length)) -gt 0) {{
                Deadline
                $output.Write($buffer, 0, $count)
            }}
            $output.Flush($true)
        }} finally {{ $output.Dispose() }}
    }} finally {{ $inputFile.Dispose() }}
    $phase = 'artifact_validation'
    if ((Digest $stage) -ne $newDigest) {{
        throw 'artifact_changed'
    }}
    # Preserve the target ACL, rather than granting new permissions to Users.
    $phase = 'permissions'
    [IO.File]::SetAccessControl($stage, [IO.File]::GetAccessControl($target))
    Deadline
    if ((Digest $target) -ne $oldDigest) {{
        throw 'target_changed'
    }}
    # Same-directory atomic replacement; no rename-away or in-place copy fallback.
    $phase = 'commit'
    if (-not [OpsKitReplace]::MoveFileEx($stage, $target, 9)) {{ throw 'replacement_failed' }}
    $committed = $true
    $phase = 'recovery_record'
    if ($checkpoint -ne '') {{ AtomicRecord $checkpoint $appliedRecord }}
    if ($healthRecord -ne '' -and {_literal(recovery.get('backup_id') or '')} -ne '') {{
        AtomicRecord $health $healthRecord
    }}
    AtomicRecord $result (@{{state='applied'; version=$version; health_confirmed=$false}} | ConvertTo-Json -Compress)
    exit 0
}} catch {{
    $state = if ($committed) {{ 'applied_recovery_incomplete' }} else {{ 'failed' }}
    # Never persist exception text: it may include paths or other local data.
    try {{ AtomicRecord $result (@{{state=$state; version=$version; health_confirmed=$false; phase=$phase}} | ConvertTo-Json -Compress) }} catch {{}}
    exit 1
}} finally {{
    if ($null -ne $stage -and [IO.File]::Exists($stage)) {{ [IO.File]::Delete($stage) }}
    if ($null -ne $lockFile) {{
        $lockFile.Dispose()
    }}
    foreach ($held in $lockHandles) {{
        try {{ $held.Unlock(0, 1) }} finally {{ $held.Dispose() }}
    }}
}}
"""


def schedule(pending: Path, target: Path, version: int, recovery=None):
    from core import updater
    with updater._update_resource.hold():
        return _schedule_locked(pending, target, version, recovery)


def _schedule_locked(pending: Path, target: Path, version: int, recovery=None):
    from core import updater
    from core.secrets import restrict_permissions
    cache_root = updater._get_cache_path().parent
    for path in (pending, target, cache_root / "update-jobs"):
        updater._update_record_path(path)
    from core.update_job_cache import reserve_job
    reserve_job(updater._get_cache_path(), pending.stat().st_size + 131072)
    digest = (recovery or {}).get("replacement_sha256") or updater._sha256_file(pending)
    target_digest = (recovery or {}).get("sha256") or updater._sha256_file(target)
    job_id = uuid4().hex
    job = cache_root / "update-jobs" / job_id
    job.mkdir(parents=True, mode=0o700)
    restrict_permissions(job.parent)
    restrict_permissions(job)
    metadata = {"schema_version": 2, "job_id": job_id, "version": version,
                "state": "preparing", "target": str(target.resolve()), "parent_pid": os.getpid(),
                "backup_id": (recovery or {}).get("backup_id"),
                "transaction_id": ((recovery or {}).get("transaction") or {}).get("transaction_id"),
                "old_sha256": target_digest, "new_sha256": digest}
    updater._write_update_record(job / "job.json", metadata)
    artifact = job / "artifact.exe"
    updater._atomic_copy(pending, artifact, expected_digest=digest)
    restrict_permissions(artifact)
    lock = updater._update_lock_path()
    updater._update_record_path(lock)
    lock.parent.mkdir(parents=True, exist_ok=True)
    restrict_permissions(lock.parent)
    target_lock = updater._update_target_lock_path(target)
    updater._update_record_path(target_lock)
    target_lock.parent.mkdir(exist_ok=True)
    restrict_permissions(target_lock.parent)
    health = updater._get_health_path()
    updater._update_record_path(health)
    health.parent.mkdir(parents=True, exist_ok=True)
    restrict_permissions(health.parent)
    helper = job / "apply.ps1"
    # BOM is required for Windows PowerShell 5.1 to preserve Unicode literals.
    content = script(target=target, artifact=artifact, result=job / "result.json", health=health,
                     lock=lock, old_digest=target_digest, new_digest=digest,
                     version=version, parent_pid=os.getpid(), recovery={**(recovery or {}), "job_id": job_id}, target_lock=target_lock)
    from core.secrets import atomic_write
    atomic_write(helper, content.encode("utf-8-sig"))
    executable = shutil.which("powershell") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    metadata["helper_executable"] = str(Path(executable).resolve())
    # Persist launch intent and its index before starting an asynchronous helper.
    # Failure here must not launch an unindexed task or replace the old index.
    metadata["state"] = "launching"
    updater._write_update_record(job / "job.json", metadata)
    try:
        cache = updater._read_update_record(updater._get_cache_path())
    except FileNotFoundError:
        cache = {}
    updater._write_update_record(updater._get_cache_path(), {**cache, "apply_job": job_id})
    try:
        process = subprocess.Popen([executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", str(helper)],
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                         close_fds=True)
    except Exception:
        updater._write_update_record(job / "result.json", {"version": version, "state": "launch_failed", "health_confirmed": False})
        raise
    metadata["state"] = "scheduled"
    if type(getattr(process, "pid", None)) is int and process.pid > 0:
        metadata["helper_pid"] = process.pid
    try:
        updater._write_update_record(job / "job.json", metadata)
    except Exception:
        # Popen succeeded. Keep the indexed launch intent rather than label an
        # already-started helper launch_failed or enqueue a second retry marker.
        updater._log.warning("Windows helper started; launch record completion deferred")
    return job_id
