"""Windows delayed replacement from a private, digest-bound job snapshot."""
from pathlib import Path
import os
import re
import shutil
import subprocess
from contextlib import contextmanager, ExitStack
from uuid import uuid4


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _validate_guard_binding(transaction, recovery, target, version, old_digest, new_digest, owner):
    backup_id = recovery.get("backup_id")
    if (type(transaction.get("schema_version")) is not int or transaction["schema_version"] != 1
            or transaction.get("operation") != "upgrade" or transaction.get("phase") != "prepared"
            or transaction.get("target") != str(Path(target).resolve())
            or type(version) is not int or version <= 0 or not isinstance(owner, str) or not os.path.isabs(owner)
            or type(transaction.get("from_build")) is not int or transaction["from_build"] <= 0
            or type(transaction.get("to_build")) is not int or transaction["to_build"] != version
            or transaction["to_build"] <= transaction["from_build"]
            or not isinstance(transaction.get("transaction_id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", transaction["transaction_id"])
            or not isinstance(backup_id, str) or not re.fullmatch(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak", backup_id)
            or ("backup_id" in transaction and transaction["backup_id"] != backup_id)
            or ("owner_data_dir" in transaction and transaction["owner_data_dir"] != owner)
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                   for value in (old_digest, new_digest))
            or transaction.get("from_sha256") != old_digest or transaction.get("to_sha256") != new_digest
            or old_digest == new_digest):
        raise ValueError("update helper source does not match its transaction")



def _pin_source_directories(paths, stack):
    """Pin each lexical ancestor and capture its identity for the helper.

    Pin root first to avoid resolving later components through a swapped parent.
    Existing reparse points and inaccessible directories fail closed.
    """
    from core.native_short_path import _pin_identity
    from core.paths import absolute_path
    seen = set()
    result = []
    for path in paths:
        absolute = absolute_path(path)
        for parent in reversed((absolute, *absolute.parents)):
            key = str(parent)
            if key in seen:
                continue
            identity = _pin_identity(parent, stack, directory=True)
            result.append((key, *identity))
            seen.add(key)
    return result

def current_process_maps_image(path):
    """Return True only when Windows identifies this file as our loaded image.

    ProcessImageFileMapping is an internal information class, so unavailable
    APIs, access errors, and any unfamiliar status leave the caller's normal
    replacement path unchanged. A pathname alone is not image identity: it
    can have been renamed and replaced after this process started.
    """
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        native = ctypes.WinDLL("ntdll", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        kernel.GetModuleFileNameW.restype = wintypes.DWORD
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        native.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG,
                                                       ctypes.c_void_p, wintypes.ULONG,
                                                       ctypes.c_void_p]
        native.NtQueryInformationProcess.restype = ctypes.c_long

        def module_path(module):
            name = ctypes.create_unicode_buffer(32768)
            length = kernel.GetModuleFileNameW(module, name, len(name))
            return name.value if 0 < length < len(name) else None

        def query(candidate):
            if not candidate:
                return None
            # FILE_EXECUTE | SYNCHRONIZE. Do not follow a final reparse point.
            handle = kernel.CreateFileW(str(candidate), 0x00100020, 7, None, 3, 0x00200000, None)
            if handle == ctypes.c_void_p(-1).value:
                return None
            try:
                opened = wintypes.HANDLE(handle)
                # This query reads the supplied handle and compares its
                # section to the file used to create the current process.
                return native.NtQueryInformationProcess(kernel.GetCurrentProcess(), 44,
                    ctypes.byref(opened), ctypes.sizeof(opened), None) & 0xffffffff
            finally:
                kernel.CloseHandle(handle)

        image = module_path(None)
        dll = kernel.GetModuleHandleW("kernel32.dll")
        if not dll:
            return False
        # The internal information class is not a stable public contract.
        # Require both the documented-by-experiment positive result and the
        # exact non-match status against a loaded DLL. Unexpected statuses,
        # including an unavailable/renamed image path, retain old behavior.
        if query(image) != 0 or query(module_path(dll)) != 0xc0000001:
            return False
        return query(path) == 0
    except (AttributeError, OSError):
        return False


@contextmanager
def _inherited_parent_handle():
    """Keep the exact scheduling process alive as a waitable object in the helper."""
    if os.name != "nt":
        raise OSError("Windows update helper requires Windows process handles")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                       ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                       wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    current = kernel.GetCurrentProcess()
    inherited = wintypes.HANDLE()
    if not kernel.DuplicateHandle(current, current, current, ctypes.byref(inherited),
                                  0x00100000, True, 0):  # SYNCHRONIZE
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield inherited.value
    finally:
        kernel.CloseHandle(inherited)


def script(*, target, artifact, result, health, lock, old_digest, new_digest,
           version, parent_pid, recovery=None, timeout=120, target_lock=None, parent_handle=None,
           source_guard=None):
    """All substituted values are literals; file operations never use globs."""
    import json
    recovery = recovery or {}
    transaction = recovery.get("transaction")
    rollback = bool(transaction and transaction.get("operation") == "rollback")
    if rollback:
        from core.constants import MAX_HEALTH_FAILS
        source_job_id = recovery.get("source_job_id")
        if (transaction.get("phase") != "prepared" or not transaction.get("backup_id")
                or type(transaction.get("from_build")) is not int
                or (source_job_id is not None and (not isinstance(source_job_id, str)
                    or not re.fullmatch(r"[a-f0-9]{32}", source_job_id)))):
            raise ValueError("invalid rollback helper transaction")
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
    # A rollback must preserve the upgrade health record, never reset failures.
    if rollback:
        health_record = ""
    guard_types = guard_functions = guard_action = guard_cleanup = guard_directory_action = ""
    if source_guard is not None:
        if (not transaction or transaction.get("operation") != "upgrade"
                or not isinstance(recovery.get("job_id"), str)
                or not re.fullmatch(r"[a-f0-9]{32}", recovery["job_id"]) or not recovery.get("backup_id")):
            raise ValueError("source guard requires a job-bound upgrade and backup")
        _validate_guard_binding(transaction, recovery, target, version, old_digest, new_digest, source_guard["owner"])
        guard_types = '''
    [StructLayout(LayoutKind.Sequential)]
    public struct FileIdentity {
        public uint attributes, creationLow, creationHigh, accessLow, accessHigh, writeLow, writeHigh;
        public uint volume, sizeHigh, sizeLow, links, indexHigh, indexLow;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFile(
        string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetFileInformationByHandle(IntPtr handle, out FileIdentity identity);
'''
        guard_functions = '''
$directoryHandles = [Collections.Generic.List[Microsoft.Win32.SafeHandles.SafeFileHandle]]::new()
function PinDirectory($path, $volume, $high, $low) {
    $handle = [OpsKitReplace]::CreateFile($path, [uint32]2147483648, 3, [IntPtr]::Zero, 3, 0x02200000, [IntPtr]::Zero)
    if ($handle.IsInvalid) { $handle.Dispose(); throw 'directory_open_failed' }
    try {
        $identity = [OpsKitReplace+FileIdentity]::new()
        if (-not [OpsKitReplace]::GetFileInformationByHandle($handle.DangerousGetHandle(), [ref]$identity) -or
            ($identity.attributes -band 0x410) -ne 0x10 -or $identity.volume -ne $volume -or
            $identity.indexHigh -ne $high -or $identity.indexLow -ne $low) { throw 'directory_identity_changed' }
        [void]$directoryHandles.Add($handle)
    } catch { $handle.Dispose(); throw }
}
$sourceHandles = [Collections.Generic.List[IO.FileStream]]::new()
function OpenSource($path, $limit) {
    # The handle disallows write/delete sharing until the helper exits and
    # opens a final reparse point itself rather than following it.
    $handle = [OpsKitReplace]::CreateFile($path, [uint32]2147483648, 1, [IntPtr]::Zero, 3, 0x00200000, [IntPtr]::Zero)
    if ($handle.IsInvalid) { $handle.Dispose(); throw 'source_open_failed' }
    try {
        $identity = [OpsKitReplace+FileIdentity]::new()
        if (-not [OpsKitReplace]::GetFileInformationByHandle($handle.DangerousGetHandle(), [ref]$identity) -or
            ($identity.attributes -band 0x410) -ne 0 -or $identity.links -ne 1 -or
            $identity.sizeHigh -ne 0 -or $identity.sizeLow -gt $limit) { throw 'source_identity_invalid' }
        $stream = [IO.FileStream]::new($handle, [IO.FileAccess]::Read)
        [void]$sourceHandles.Add($stream)
        return $stream
    } catch { $handle.Dispose(); throw }
}
function ReadSourceRecord($path) {
    $stream = OpenSource $path 65536
    $bytes = New-Object byte[] 65537
    $size = 0
    while ($size -lt $bytes.Length) {
        $read = $stream.Read($bytes, $size, $bytes.Length - $size)
        if ($read -eq 0) { break }
        $size += $read
    }
    if ($size -gt 65536) { throw 'source_record_too_large' }
    return ([Text.Encoding]::UTF8.GetString($bytes, 0, $size) | ConvertFrom-Json)
}
function DigestSource($stream) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}
'''
        directories = source_guard.get('directories')
        if not directories:
            raise ValueError('source guard requires pinned directory identities')
        from core.paths import absolute_path
        result_directory = str(absolute_path(Path(result).parent))
        guard_directory_action = "    $phase = 'source_directory_validation'\n"
        for path, volume, high, low in directories:
            guard_directory_action += f"    PinDirectory {_literal(path)} {volume} {high} {low}\n"
            if path == result_directory:
                guard_directory_action += "    $resultDirectorySafe = $true\n"
        if result_directory not in {item[0] for item in directories}:
            raise ValueError("source guard requires the result directory")
        guard_action = f'''
    $phase = 'source_job_validation'
    $sourceJob = ReadSourceRecord {_literal(source_guard['job'])}
    $sourceIndex = ReadSourceRecord {_literal(source_guard['cache'])}
    if (-not ($sourceJob -is [PSCustomObject]) -or -not ($sourceIndex -is [PSCustomObject]) -or
        -not ($sourceJob.schema_version -is [int] -or $sourceJob.schema_version -is [long]) -or
        -not ($sourceJob.job_id -is [string]) -or -not ($sourceIndex.apply_job -is [string]) -or
        -not ($sourceJob.state -is [string]) -or -not ($sourceJob.transaction_id -is [string]) -or
        -not ($sourceJob.backup_id -is [string]) -or -not ($sourceJob.owner_data_dir -is [string]) -or
        -not ($sourceJob.target -is [string]) -or -not ($sourceJob.old_sha256 -is [string]) -or
        -not ($sourceJob.new_sha256 -is [string]) -or -not ($sourceJob.helper_executable -is [string]) -or
        $sourceJob.schema_version -ne 2 -or $sourceJob.job_id -cne $jobId -or
        $sourceIndex.apply_job -cne $jobId -or
        ($sourceJob.state -cne 'launching' -and $sourceJob.state -cne 'scheduled') -or
        $sourceJob.transaction_id -cne {_literal(transaction['transaction_id'])} -or
        $sourceJob.backup_id -cne {_literal(recovery['backup_id'])} -or
        $sourceJob.owner_data_dir -cne {_literal(source_guard['owner'])} -or
        $sourceJob.target -cne {_literal(Path(target).resolve())} -or
        -not ($sourceJob.version -is [int] -or $sourceJob.version -is [long]) -or
        $sourceJob.version -ne $version -or $sourceJob.old_sha256 -cne $oldDigest -or
        $sourceJob.new_sha256 -cne $newDigest) {{ throw 'source_job_changed' }}
    $declaredPid = $sourceJob.PSObject.Properties['helper_pid']
    if (($null -eq $declaredPid -and $sourceJob.state -cne 'launching') -or
        ($null -ne $declaredPid -and
         (-not ($declaredPid.Value -is [int] -or $declaredPid.Value -is [long]) -or $declaredPid.Value -ne $PID))) {{
        throw 'source_helper_changed'
    }}
    $selfProcess = [Diagnostics.Process]::GetCurrentProcess()
    try {{
        if (-not [IO.Path]::IsPathRooted($sourceJob.helper_executable) -or
            -not [String]::Equals([IO.Path]::GetFullPath($sourceJob.helper_executable),
                [IO.Path]::GetFullPath($selfProcess.MainModule.FileName), [StringComparison]::OrdinalIgnoreCase)) {{
            throw 'source_helper_executable_changed'
        }}
    }} finally {{ $selfProcess.Dispose() }}
    $phase = 'backup_validation'
    $receipt = ReadSourceRecord {_literal(source_guard['receipt'])}
    if (-not ($receipt -is [PSCustomObject]) -or
        -not ($receipt.schema_version -is [int] -or $receipt.schema_version -is [long]) -or
        -not ($receipt.backup_id -is [string]) -or -not ($receipt.target -is [string]) -or
        -not ($receipt.sha256 -is [string]) -or -not ($receipt.replacement_sha256 -is [string]) -or
        $receipt.schema_version -ne 1 -or $receipt.backup_id -cne {_literal(recovery['backup_id'])} -or
        $receipt.target -cne {_literal(Path(target).resolve())} -or
        -not ($receipt.from_build -is [int] -or $receipt.from_build -is [long]) -or
        -not ($receipt.to_build -is [int] -or $receipt.to_build -is [long]) -or
        $receipt.from_build -ne {int(transaction['from_build'])} -or
        $receipt.to_build -ne $version -or $receipt.sha256 -cne $oldDigest -or
        $receipt.replacement_sha256 -cne $newDigest) {{ throw 'backup_receipt_changed' }}
    $backupFile = OpenSource {_literal(source_guard['backup'])} 1073741824
    if ((DigestSource $backupFile) -cne $oldDigest) {{ throw 'backup_changed' }}
'''
        guard_cleanup = '''
    foreach ($source in $sourceHandles) { $source.Dispose() }
    foreach ($directory in $directoryHandles) { $directory.Dispose() }
'''
    return f"""$ErrorActionPreference = 'Stop'
$pid_to_wait = {int(parent_pid)}
$parent_handle = {int(parent_handle or 0)}
$target = {_literal(target)}
$artifact = {_literal(artifact)}
$result = {_literal(result)}
$health = {_literal(health)}
$lockPath = {_literal(lock)}
$targetLockPath = {_literal(target_lock or lock)}
$oldDigest = {_literal(old_digest)}
$newDigest = {_literal(new_digest)}
$healthRecord = {_literal(health_record)}
$rollback = {'$true' if rollback else '$false'}
$rollbackBuild = {int(transaction['from_build']) if rollback else 0}
$rollbackBackup = {_literal(transaction['backup_id'] if rollback else '')}
$rollbackThreshold = {int(MAX_HEALTH_FAILS) if rollback else 0}
$rollbackSourceJob = {_literal(source_job_id if rollback and source_job_id is not None else '')}
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
$resultDirectorySafe = {'$false' if source_guard is not None else '$true'}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class OpsKitReplace {{
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool MoveFileEx(string source, string target, int flags);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool CloseHandle(IntPtr handle);
{guard_types}
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
    # The checkpoint already has a long digest filename. Keep its sibling
    # temporary name independent so .NET Framework can open it near MAX_PATH.
    $tmp = [IO.Path]::Combine([IO.Path]::GetDirectoryName($path), [Guid]::NewGuid().ToString('N') + '.tmp')
    $bytes = [Text.Encoding]::UTF8.GetBytes($body)
    $output = [IO.File]::Open($tmp, 'CreateNew', 'Write', 'None')
    try {{ $output.Write($bytes, 0, $bytes.Length); $output.Flush($true) }}
    finally {{ $output.Dispose() }}
    # On failure the path may no longer name our file; preserve it for review.
    if (-not [OpsKitReplace]::MoveFileEx($tmp, $path, 9)) {{ throw 'record_commit_failed' }}
}}
{guard_functions}
try {{
{guard_directory_action}    if ($jobId -ne '') {{
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
    # The inherited handle continues to identify the original parent even if
    # it has already exited and its PID was reused before this script starts.
    if ($parent_handle -ne 0) {{
        $handle = [IntPtr]::new([Int64]$parent_handle)
        try {{
            $remaining = [Math]::Max(1, [int][Math]::Ceiling(($budget - $clock.Elapsed.TotalSeconds) * 1000))
            $waitResult = [OpsKitReplace]::WaitForSingleObject($handle, [uint32]$remaining)
            if ($waitResult -eq 258) {{ throw 'parent_wait_timeout' }}
            if ($waitResult -ne 0) {{ throw 'parent_wait_failed' }}
        }} finally {{ [void][OpsKitReplace]::CloseHandle($handle) }}
    }} elseif ($pid_to_wait -gt 0) {{
        # Compatibility for manually generated scripts; scheduled jobs always
        # use a handle inherited from the scheduling process.
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
    if ($rollback) {{
        # The scheduler's decision can become stale while the parent exits.
        # Recheck under both locks and leave the health record untouched.
        $phase = 'rollback_health_validation'
        $entry = New-Object IO.FileInfo($health)
        if (-not $entry.Exists -or ($entry.Attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint)) -or $entry.Length -gt 65536) {{
            throw 'invalid_rollback_health'
        }}
        $healthFile = [IO.File]::OpenRead($health)
        try {{
            $healthBuffer = New-Object byte[] 65537
            $healthSize = 0
            while ($healthSize -lt 65537) {{
                $count = $healthFile.Read($healthBuffer, $healthSize, 65537 - $healthSize)
                if ($count -eq 0) {{ break }}
                $healthSize += $count
            }}
            if ($healthSize -gt 65536) {{ throw 'invalid_rollback_health' }}
            $currentHealth = [Text.Encoding]::UTF8.GetString($healthBuffer, 0, $healthSize) | ConvertFrom-Json
        }} finally {{ $healthFile.Dispose() }}
        if ($null -eq $currentHealth -or -not ($currentHealth.build -is [int] -or $currentHealth.build -is [long]) -or
            $currentHealth.build -ne $rollbackBuild -or -not ($currentHealth.confirmed -is [bool]) -or
            $currentHealth.confirmed -ne $false -or -not ($currentHealth.fails -is [int] -or $currentHealth.fails -is [long]) -or
            $currentHealth.fails -lt $rollbackThreshold -or -not ($currentHealth.backup_id -is [string]) -or
            $currentHealth.backup_id -cne $rollbackBackup) {{
            throw 'rollback_health_changed'
        }}
        $sourceJobProperty = $currentHealth.PSObject.Properties['job_id']
        if (($rollbackSourceJob -eq '' -and $null -ne $sourceJobProperty) -or
            ($rollbackSourceJob -ne '' -and ($null -eq $sourceJobProperty -or
                -not ($sourceJobProperty.Value -is [string]) -or $sourceJobProperty.Value -cne $rollbackSourceJob))) {{
            throw 'rollback_health_source_job_changed'
        }}
    }}
{guard_action}
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
    if ($resultDirectorySafe) {{
        try {{ AtomicRecord $result (@{{state=$state; version=$version; health_confirmed=$false; phase=$phase}} | ConvertTo-Json -Compress) }} catch {{}}
    }}
    exit 1
}} finally {{
    # Preserve stage paths after failure: the name could have been replaced.
{guard_cleanup}
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
    from core import update_target_state
    from core.config import get_data_dir
    from core.secrets import restrict_permissions
    transaction = (recovery or {}).get("transaction")
    if transaction is not None:
        # Upgrade jobs own cache.apply_job and publish a new health record.
        # A rollback needs a separate durable identity and restart protocol;
        # routing it through this scheduler would overwrite upgrade evidence.
        if transaction.get("operation") == "rollback":
            raise ValueError("rollback requires a separate recovery scheduler")
        update_target_state.require_owner(transaction, get_data_dir())
        if "backup_id" in transaction and transaction["backup_id"] != (recovery or {}).get("backup_id"):
            raise ValueError("update job backup does not match transaction")
        backup_id = (recovery or {}).get("backup_id")
        if (transaction.get("operation") != "upgrade" or not isinstance(backup_id, str)
                or not re.fullmatch(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak", backup_id)):
            raise ValueError("update job needs an owned backup")
    cache_root = updater._get_cache_path().parent
    for path in (pending, target, cache_root / "update-jobs"):
        updater._update_record_path(path)
    digest = (recovery or {}).get("replacement_sha256") or updater._sha256_file(pending)
    target_digest = (recovery or {}).get("sha256") or updater._sha256_file(target)
    if transaction is not None:
        owner = update_target_state.data_directory_identity(get_data_dir())
        _validate_guard_binding(transaction, recovery, target, version, target_digest, digest, owner)
        if update_target_state.read(target) != transaction:
            raise ValueError("update transaction changed before helper scheduling")
    from core.update_job_cache import reserve_job
    reserve_job(updater._get_cache_path(), pending.stat().st_size + 131072)
    job_id = uuid4().hex
    job = cache_root / "update-jobs" / job_id
    job.mkdir(parents=True, mode=0o700)
    restrict_permissions(job.parent)
    restrict_permissions(job)
    metadata = {"schema_version": 2, "job_id": job_id, "version": version,
                "state": "preparing", "target": str(target.resolve()), "parent_pid": os.getpid(),
                "owner_data_dir": update_target_state.data_directory_identity(get_data_dir()),
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
    source_guard = None
    if transaction is not None:
        backup = get_data_dir() / "backups" / backup_id
        receipt = backup.with_name(backup.name + ".json")
        for path in (backup, receipt):
            updater._update_record_path(path)
        source_guard = {"job": job / "job.json", "cache": updater._get_cache_path(),
                        "backup": backup, "receipt": receipt, "owner": metadata["owner_data_dir"]}
    # BOM is required for Windows PowerShell 5.1 to preserve Unicode literals.
    with ExitStack() as directory_handles, _inherited_parent_handle() as parent_handle:
        if source_guard is not None:
            source_guard["directories"] = _pin_source_directories(
                [job, backup.parent, cache_root, lock.parent, target_lock.parent,
                 health.parent, target.parent, artifact.parent], directory_handles)
        content = script(target=target, artifact=artifact, result=job / "result.json", health=health,
                         lock=lock, old_digest=target_digest, new_digest=digest,
                         version=version, parent_pid=os.getpid(), parent_handle=parent_handle,
                         recovery={**(recovery or {}), "job_id": job_id}, target_lock=target_lock,
                         source_guard=source_guard)
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
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [parent_handle]}
        try:
            process = subprocess.Popen([executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", str(helper)],
                             creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup, close_fds=True)
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
