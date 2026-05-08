# OpsKit 一键安装脚本 — Windows PowerShell
# 用法：irm https://raw.githubusercontent.com/ougato/opskit-cli/main/install.ps1 | iex
#      或在 CMD 中：powershell -c "irm https://raw.githubusercontent.com/ougato/opskit-cli/main/install.ps1 | iex"
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$REPO          = "ougato/opskit-cli"
$BIN_NAME      = "opskit"
$GITHUB_API    = "https://api.github.com/repos/$REPO/releases/latest"
$INSTALL_DIR   = Join-Path $env:LOCALAPPDATA "opskit"

function Write-Info  { param($msg) Write-Host "  [info] $msg" }
function Write-Ok    { param($msg) Write-Host "  [ ok ] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [warn] $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "  [fail] $msg" -ForegroundColor Red }

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

# ── 获取最新版本 ───────────────────────────────────────────────────────────────
function Get-LatestTag {
    try {
        $response = Invoke-RestMethod -Uri $GITHUB_API -UseBasicParsing -TimeoutSec 15
        return $response.tag_name
    } catch {
        Write-Fail "获取最新版本失败：$_"
        Write-Fail "请检查网络，或手动下载：https://github.com/$REPO/releases/latest"
        exit 1
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

    $tag = Get-LatestTag
    Write-Info "最新版本：$tag"

    $filename    = "${BIN_NAME}-${platform}.exe"
    $downloadUrl = "https://github.com/$REPO/releases/download/$tag/$filename"
    $sha256Url   = "$downloadUrl.sha256"

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
    Write-Info "在 PowerShell 或 CMD 中运行 'opskit' 启动程序"
    Write-Warn "若当前终端无法识别 opskit 命令，请重启终端窗口使 PATH 生效"
    Write-Host ""
}

Main
