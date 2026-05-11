#!/usr/bin/env bash
# OpsKit 一键安装脚本 — Linux / macOS
# 用法：curl -fsSL https://raw.githubusercontent.com/ougato/opskit-cli/main/install.sh | bash
set -euo pipefail

BIN_NAME="opskit"
DOWNLOAD_BASE_CN="https://file.icerror.top/d/mirror/soft"
DOWNLOAD_BASE_GLOBAL="https://github.com/ougato/opskit-cli/releases/latest/download"

# ── 颜色输出 ──────────────────────────────────────────────────────────────────
_green()  { printf '\033[32m%s\033[0m\n' "$*"; }
_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
_red()    { printf '\033[31m%s\033[0m\n' "$*"; }
_info()   { printf '  [info] %s\n' "$*"; }
_ok()     { printf '  [ ok ] %s\n' "$*"; }
_err()    { printf '  [fail] %s\n' "$*" >&2; }

# ── 地区检测 ──────────────────────────────────────────────────────────────────
detect_region() {
    case "${OPSKIT_SOURCE:-auto}" in
        cn)     echo "cn";     return ;;
        global) echo "global"; return ;;
    esac
    local loc
    loc="$(curl -fsSL --max-time 3 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null \
            | awk -F= '/^loc=/{print $2}' | tr -d '\r')"
    [ "$loc" = "CN" ] && echo "cn" || echo "global"
}

# ── 平台检测 ──────────────────────────────────────────────────────────────────
detect_platform() {
    local os arch
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"

    case "$os" in
        linux)  os="linux" ;;
        darwin) os="darwin" ;;
        *)
            _err "不支持的操作系统：$os"
            exit 1
            ;;
    esac

    case "$arch" in
        x86_64|amd64) arch="x64" ;;
        aarch64|arm64) arch="arm64" ;;
        armv7l) arch="armv7" ;;
        *)
            _err "不支持的架构：$arch"
            exit 1
            ;;
    esac

    echo "${os}-${arch}"
}

# ── 下载文件 ──────────────────────────────────────────────────────────────────
download_file() {
    local url="$1" dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$dest" "$url"
    else
        wget -qO "$dest" "$url"
    fi
}

# ── SHA256 校验 ───────────────────────────────────────────────────────────────
verify_sha256() {
    local file="$1" expected="$2"
    if [ -z "$expected" ]; then
        _yellow "  [warn] 未找到 SHA256 校验值，跳过校验"
        return 0
    fi
    local actual
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$file" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$file" | awk '{print $1}')"
    else
        _yellow "  [warn] 未找到 sha256sum / shasum，跳过校验"
        return 0
    fi
    if [ "$actual" != "$expected" ]; then
        _err "SHA256 校验失败"
        _err "  期望：$expected"
        _err "  实际：$actual"
        return 1
    fi
    _ok "SHA256 校验通过"
}

# ── 确定安装路径 ───────────────────────────────────────────────────────────────
get_install_dir() {
    if [ "$(id -u)" = "0" ]; then
        echo "/usr/local/bin"
    else
        echo "${HOME}/.local/bin"
    fi
}

# ── 确保 PATH 包含安装目录 ────────────────────────────────────────────────────
ensure_in_path() {
    local install_dir="$1"
    if echo "$PATH" | grep -q "$install_dir"; then
        return 0
    fi

    local shell_rc=""
    if [ -n "${BASH_VERSION:-}" ] || [ -f "${HOME}/.bashrc" ]; then
        shell_rc="${HOME}/.bashrc"
    fi
    if [ -n "${ZSH_VERSION:-}" ] || [ -f "${HOME}/.zshrc" ]; then
        shell_rc="${HOME}/.zshrc"
    fi

    local export_line="export PATH=\"${install_dir}:\$PATH\""

    if [ -n "$shell_rc" ]; then
        if ! grep -qF "$install_dir" "$shell_rc" 2>/dev/null; then
            echo "" >> "$shell_rc"
            echo "# OpsKit" >> "$shell_rc"
            echo "$export_line" >> "$shell_rc"
            _ok "已将 ${install_dir} 写入 ${shell_rc}"
        fi
    fi

    _yellow "  请执行以下命令使 PATH 立即生效："
    _yellow "    export PATH=\"${install_dir}:\$PATH\""
}

# ── 主流程 ────────────────────────────────────────────────────────────────────
main() {
    _green "=== OpsKit 安装程序 ==="
    echo ""

    local platform os_dir filename download_url sha256_url tmp_dir region
    platform="$(detect_platform)"
    _info "检测到平台：$platform"

    case "$platform" in
        linux-*)  os_dir="linux" ;;
        darwin-*) os_dir="macos" ;;
        *) _err "不支持的平台：$platform"; exit 1 ;;
    esac

    region="$(detect_region)"
    _info "下载源：$region"

    filename="${BIN_NAME}-${platform}"
    if [ "$region" = "cn" ]; then
        download_url="${DOWNLOAD_BASE_CN}/${os_dir}/${filename}"
    else
        download_url="${DOWNLOAD_BASE_GLOBAL}/${filename}"
    fi
    sha256_url="${download_url}.sha256"

    _info "下载地址：$download_url"

    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT

    local tmp_bin="${tmp_dir}/${filename}"
    local tmp_sha="${tmp_dir}/${filename}.sha256"

    _info "正在下载 ${filename}..."
    download_file "$download_url" "$tmp_bin" || {
        _err "下载失败：$download_url"
        exit 1
    }

    local expected_sha=""
    if download_file "$sha256_url" "$tmp_sha" 2>/dev/null; then
        expected_sha="$(awk '{print $1}' "$tmp_sha")"
    fi

    verify_sha256 "$tmp_bin" "$expected_sha" || exit 1

    local install_dir
    install_dir="$(get_install_dir)"
    mkdir -p "$install_dir"

    chmod +x "$tmp_bin"
    mv "$tmp_bin" "${install_dir}/${BIN_NAME}"

    _ok "已安装到：${install_dir}/${BIN_NAME}"

    ensure_in_path "$install_dir"

    echo ""
    _green "=== 安装完成 ==="
    echo ""
    _info "如命令未找到，请执行：export PATH=\"${install_dir}:\$PATH\""
    exec "${install_dir}/${BIN_NAME}"
}

main "$@"
