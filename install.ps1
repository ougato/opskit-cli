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
$DOWNLOAD_BASE = "https://github.com/ougato/opskit-cli/releases/latest/download"
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
    if ([string]::IsNullOrEmpty($Expected)) {
        Write-Warn "No SHA256 checksum found, skipping verification"
        return $true
    }
    $actual = (Get-FileHash -Path $FilePath -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $Expected.ToLower()) {
        Write-Fail "SHA256 mismatch"
        Write-Fail "  expected: $Expected"
        Write-Fail "  actual:   $actual"
        return $false
    }
    return $true
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
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
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

    $filename = "${BIN_NAME}-${platform}.exe"
    $downloadUrl = "$DOWNLOAD_BASE/$filename"
    $sha256Url = "$downloadUrl.sha256"

    Write-Field "Platform" $platform
    Write-Field "Source"   "GitHub Releases"
    Write-Field "Target"   "%LOCALAPPDATA%\opskit\opskit.exe"
    Write-Host ""

    $tmpDir  = Join-Path $env:TEMP "opskit_install_$([System.IO.Path]::GetRandomFileName())"
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

    $tmpBin = Join-Path $tmpDir $filename
    $tmpSha = Join-Path $tmpDir "$filename.sha256"

    try {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpBin -UseBasicParsing -TimeoutSec 120
        Write-Ok "Downloaded   $filename"

        $expectedSha = ""
        try {
            Invoke-WebRequest -Uri $sha256Url -OutFile $tmpSha -UseBasicParsing -TimeoutSec 10
            $expectedSha = (Get-Content $tmpSha -Raw).Trim().Split()[0]
        } catch {
            Write-Warn "SHA256 file download failed, skipping verification"
        }

        if (-not (Test-Sha256 -FilePath $tmpBin -Expected $expectedSha)) {
            exit 1
        }
        if (-not [string]::IsNullOrEmpty($expectedSha)) {
            Write-Ok "SHA256 verified"
        }

        New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null
        $destBin = Join-Path $INSTALL_DIR "${BIN_NAME}.exe"

        Copy-Item -Path $tmpBin -Destination $destBin -Force
        Write-Ok "Installed"

        Add-ToUserPath -Dir $INSTALL_DIR

    } finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "  Done!  Open a new terminal, then run:  opskit" -ForegroundColor Cyan
    Write-Host ""
}

Main
