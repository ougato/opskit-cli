# OpsKit 一键安装脚本 — Windows PowerShell
# 用法：irm https://file.icerror.top/d/install/opskit.ps1 | iex
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$BIN_NAME             = "opskit"
$DOWNLOAD_BASE_CN     = "https://file.icerror.top/d/mirror/soft/windows"
$DOWNLOAD_BASE_GLOBAL = "https://github.com/ougato/opskit-cli/releases/latest/download"
$INSTALL_DIR          = Join-Path $env:LOCALAPPDATA "opskit"

function Write-Info  { param($msg) Write-Host "  [info] $msg" }
function Write-Ok    { param($msg) Write-Host "  [ ok ] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [warn] $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "  [fail] $msg" -ForegroundColor Red }

# ── 地区检测 ──────────────────────────────────────────────────────────────────
function Get-Region {
    switch ($env:OPSKIT_SOURCE) {
        'cn'     { return 'cn' }
        'global' { return 'global' }
    }
    try {
        $resp = Invoke-WebRequest -Uri 'https://www.cloudflare.com/cdn-cgi/trace' `
                                   -UseBasicParsing -TimeoutSec 3
        if ($resp.Content -match 'loc=CN') { return 'cn' }
        return 'global'
    } catch {
        return 'global'
    }
}

# ── 平台检测 ──────────────────────────────────────────────────────────────────
function Get-Platform {
    $arch = $env:PROCESSOR_ARCHITECTURE
    switch ($arch) {
        "AMD64"  { return "windows-x64" }
        "x86"    { return "windows-x64" }
        "ARM64"  {
            Write-Fail "暂不支持 Windows ARM64，请关注后续版本"
            exit 1
        }
        default  {
            Write-Fail "不支持的架构：$arch"
            exit 1
        }
    }
}

# ── SHA256 校验 ───────────────────────────────────────────────────────────────
function Test-Sha256 {
    param([string]$FilePath, [string]$Expected)
    if ([string]::IsNullOrEmpty($Expected)) {
        Write-Warn "未找到 SHA256 校验值，跳过校验"
        return $true
    }
    $actual = (Get-FileHash -Path $FilePath -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $Expected.ToLower()) {
        Write-Fail "SHA256 校验失败"
        Write-Fail "  期望：$Expected"
        Write-Fail "  实际：$actual"
        return $false
    }
    Write-Ok "SHA256 校验通过"
    return $true
}

# ── 写入用户 PATH ─────────────────────────────────────────────────────────────
function Add-ToUserPath {
    param([string]$Dir)
    $currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($currentPath -split ";" | Where-Object { $_ -eq $Dir }) {
        Write-Info "$Dir 已在 PATH 中"
        return
    }
    $newPath = if ($currentPath) { "$currentPath;$Dir" } else { $Dir }
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    # 刷新当前进程 PATH
    $env:PATH = "$env:PATH;$Dir"
    Write-Ok "已将 $Dir 添加到用户 PATH"
}

# ── 主流程 ────────────────────────────────────────────────────────────────────
function Main {
    Write-Host ""
    Write-Host "=== OpsKit 安装程序 ===" -ForegroundColor Cyan
    Write-Host ""

    $platform = Get-Platform
    Write-Info "检测到平台：$platform"

    $region = Get-Region
    Write-Info "下载源：$region"

    $filename = "${BIN_NAME}-${platform}.exe"
    if ($region -eq 'cn') {
        $downloadUrl = "$DOWNLOAD_BASE_CN/$filename"
    } else {
        $downloadUrl = "$DOWNLOAD_BASE_GLOBAL/$filename"
    }
    $sha256Url = "$downloadUrl.sha256"

    Write-Info "下载地址：$downloadUrl"

    $tmpDir  = Join-Path $env:TEMP "opskit_install_$([System.IO.Path]::GetRandomFileName())"
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

    $tmpBin = Join-Path $tmpDir $filename
    $tmpSha = Join-Path $tmpDir "$filename.sha256"

    try {
        Write-Info "正在下载 $filename ..."
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpBin -UseBasicParsing -TimeoutSec 120

        $expectedSha = ""
        try {
            Invoke-WebRequest -Uri $sha256Url -OutFile $tmpSha -UseBasicParsing -TimeoutSec 10
            $expectedSha = (Get-Content $tmpSha -Raw).Trim().Split()[0]
        } catch {
            Write-Warn "SHA256 文件下载失败，跳过校验"
        }

        if (-not (Test-Sha256 -FilePath $tmpBin -Expected $expectedSha)) {
            exit 1
        }

        New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null
        $destBin = Join-Path $INSTALL_DIR "${BIN_NAME}.exe"

        Copy-Item -Path $tmpBin -Destination $destBin -Force
        Write-Ok "已安装到：$destBin"

        Add-ToUserPath -Dir $INSTALL_DIR

    } finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "=== 安装完成 ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Warn "若当前终端无法识别 opskit 命令，请重启终端窗口使 PATH 生效"
    Write-Host ""
    & (Join-Path $INSTALL_DIR "${BIN_NAME}.exe")
}

Main
