# OpsKit installer - Windows PowerShell
# Usage: irm https://raw.githubusercontent.com/ougato/opskit-cli/master/install.ps1 | iex
#
# NOTE: keep this bootstrap ASCII-only. Windows PowerShell 5.1's
# Invoke-RestMethod decodes a piped script as ISO-8859-1 when the HTTP
# response carries no charset, which corrupts any non-ASCII output
# (Chinese / box-drawing). ASCII guarantees a clean render everywhere.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$BIN_NAME      = "opskit"
$DOWNLOAD_BASE = "https://github.com/ougato/opskit-cli/releases/download"
$BOOTSTRAP_URL = "https://raw.githubusercontent.com/ougato/opskit-cli/master/bootstrap.json"
$INSTALL_DIR   = Join-Path $env:LOCALAPPDATA "opskit"

function Write-Field { param($label, $value) Write-Host ("  {0,-12}{1}" -f $label, $value) }
function Write-Ok    { param($msg) Write-Host "  [OK]    $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [WARN]  $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "  [FAIL]  $msg" -ForegroundColor Red }

# -- platform detection --------------------------------------------------------
function Get-Platform {
    $arch = $env:PROCESSOR_ARCHITECTURE
    switch ($arch) {
        "AMD64"  { return "windows-x64" }
        "x86"    { return "windows-x64" }
        "ARM64"  {
            Write-Fail "Windows ARM64 is not supported yet"
            exit 1
        }
        default  {
            Write-Fail "Unsupported architecture: $arch"
            exit 1
        }
    }
}

# -- SHA256 verification -------------------------------------------------------
function Test-Sha256 {
    param([string]$FilePath, [string]$Expected)
    if ($Expected -notmatch '^[0-9a-fA-F]{64}$') {
        Write-Fail "Missing or invalid SHA256 checksum; refusing installation"
        return $false
    }
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $file = [IO.File]::OpenRead($FilePath)
        try { $actual = [BitConverter]::ToString($sha256.ComputeHash($file)).Replace('-', '').ToLowerInvariant() }
        finally { $file.Dispose() }
    } finally {
        $sha256.Dispose()
    }
    if ($actual -ne $Expected.ToLowerInvariant()) {
        Write-Fail "SHA256 mismatch"
        Write-Fail "  expected: $Expected"
        Write-Fail "  actual:   $actual"
        return $false
    }
    return $true
}

function Get-ReleaseTag {
    param([string]$TempDir)
    $manifestPath = Join-Path $TempDir "bootstrap.json"
    Invoke-WebRequest -Uri $BOOTSTRAP_URL -OutFile $manifestPath -UseBasicParsing -TimeoutSec 10
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $build = $manifest.latest_build
    $minimum = $manifest.min_build
    if ($manifest.schema_version -isnot [int] -or $manifest.schema_version -ne 2 -or
        $manifest.channel -cne 'stable' -or $build -isnot [int] -or $build -lt 1 -or $build -gt 999999999 -or
        $minimum -isnot [int] -or $minimum -lt 0 -or $minimum -gt $build -or
        $manifest.display -cne "v$build" -or $manifest.kill_switch -isnot [bool]) {
        throw "Invalid OpsKit release metadata"
    }
    if ($manifest.kill_switch) {
        throw "OpsKit installation is paused"
    }
    return "v$build"
}

function Get-VerifiedReleaseFile {
    param([string]$Tag, [string]$FileName, [string]$TempDir)
    $url = "$DOWNLOAD_BASE/$Tag/$FileName"
    $filePath = Join-Path $TempDir $FileName
    $checksumPath = Join-Path $TempDir "$FileName.sha256"
    Invoke-WebRequest -Uri $url -OutFile $filePath -UseBasicParsing -TimeoutSec 120
    Write-Ok "Downloaded   $FileName"
    Invoke-WebRequest -Uri "$url.sha256" -OutFile $checksumPath -UseBasicParsing -TimeoutSec 10
    $expected = (Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\s+' | Select-Object -First 1
    if (-not (Test-Sha256 -FilePath $filePath -Expected $expected)) {
        throw "Checksum verification failed for $FileName"
    }
    Write-Ok "SHA256 verified"
    return $filePath
}

# -- add install dir to user PATH ----------------------------------------------
function Add-ToUserPath {
    param([string]$Dir)
    $currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($currentPath -split ";" | Where-Object { $_ -eq $Dir }) {
        Write-Ok "Already in user PATH"
        return
    }
    $newPath = if ($currentPath) { "$currentPath;$Dir" } else { $Dir }
    $markerPath = Join-Path $Dir ".opskit-user-path"
    $createdMarker = $false
    if (-not (Test-Path -LiteralPath $markerPath)) {
        $marker = [IO.File]::Open($markerPath, 'CreateNew', 'Write', 'None')
        try { $marker.Write([byte[]](49, 13, 10), 0, 3) } finally { $marker.Dispose() }
        $createdMarker = $true
    }
    try {
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    } catch {
        if ($createdMarker) { [IO.File]::Delete($markerPath) }
        throw
    }
    # refresh PATH for the current process
    $env:PATH = "$env:PATH;$Dir"
    Write-Ok "Added to user PATH"
}

# -- main ----------------------------------------------------------------------
function Main {
    Write-Host ""
    Write-Host "  OpsKit  Installer" -ForegroundColor Cyan
    Write-Host ""

    $platform = Get-Platform

    Write-Field "Platform" $platform
    Write-Field "Source"   "GitHub Releases"
    Write-Field "Target"   "%LOCALAPPDATA%\opskit\opskit.exe"
    Write-Host ""

    $tmpDir  = Join-Path $env:TEMP "opskit_install_$([System.IO.Path]::GetRandomFileName())"
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

    try {
        $tag = Get-ReleaseTag -TempDir $tmpDir
        $launcher = Get-VerifiedReleaseFile -Tag $tag -FileName "${BIN_NAME}-${platform}-launcher.exe" -TempDir $tmpDir
        $archive = Get-VerifiedReleaseFile -Tag $tag -FileName "${BIN_NAME}-${platform}.tar.xz" -TempDir $tmpDir
        [IO.Directory]::CreateDirectory($INSTALL_DIR) | Out-Null
        $destBin = Join-Path $INSTALL_DIR "${BIN_NAME}.exe"

        & $launcher --opskit-internal-install $archive $destBin $tag
        if ($LASTEXITCODE -ne 0) {
            throw "OpsKit installation failed (exit code $LASTEXITCODE)"
        }
        Write-Ok "Installed"

        Add-ToUserPath -Dir $INSTALL_DIR
    } catch {
        Write-Fail $_.Exception.Message
        exit 1
    } finally {
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "  Done!  Open a new terminal, then run:  opskit" -ForegroundColor Cyan
    Write-Host ""
}

Main
