#!/usr/bin/env bash
# OpsKit installer - Linux / macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/ougato/opskit-cli/master/install.sh | bash
set -euo pipefail

BIN_NAME="opskit"
DOWNLOAD_BASE="https://github.com/ougato/opskit-cli/releases/latest/download"

# -- output helpers ------------------------------------------------------------
_title() { printf '\033[36m%s\033[0m\n' "$*"; }
_field() { printf '  %-12s%s\n' "$1" "$2"; }
_ok()    { printf '  \033[32m[OK]\033[0m    %s\n' "$*"; }
_warn()  { printf '  \033[33m[WARN]\033[0m  %s\n' "$*"; }
_err()   { printf '  \033[31m[FAIL]\033[0m  %s\n' "$*" >&2; }

# -- platform detection --------------------------------------------------------
detect_platform() {
    local os arch
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"

    case "$os" in
        linux)  os="linux" ;;
        darwin) os="darwin" ;;
        *)
            _err "Unsupported OS: $os"
            exit 1
            ;;
    esac

    case "$arch" in
        x86_64|amd64) arch="x64" ;;
        aarch64|arm64) arch="arm64" ;;
        armv7l) arch="armv7" ;;
        *)
            _err "Unsupported architecture: $arch"
            exit 1
            ;;
    esac

    echo "${os}-${arch}"
}

# -- download ------------------------------------------------------------------
download_file() {
    local url="$1" dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$dest" "$url"
    else
        wget -qO "$dest" "$url"
    fi
}

# -- SHA256 verification -------------------------------------------------------
verify_sha256() {
    local file="$1" expected="$2"
    if [ "${#expected}" -ne 64 ] || ! printf '%s' "$expected" | LC_ALL=C grep -Eq '^[0-9a-fA-F]{64}$'; then
        _err "Missing or invalid SHA256 checksum; refusing installation"
        return 1
    fi
    expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
    local actual
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$file" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$file" | awk '{print $1}')"
    else
        _err "SHA256 verification tool required; refusing installation"
        return 1
    fi
    if [ "$actual" != "$expected" ]; then
        _err "SHA256 mismatch"
        _err "  expected: $expected"
        _err "  actual:   $actual"
        return 1
    fi
    _ok "SHA256 verified"
}

# Public trust root is embedded by the release owner, never fetched with assets.
RELEASE_PUBLIC_KEY=""
verify_release_signature() {
    local url="$1" filename="$2" digest="$3" temp="$4"
    if [ -z "$RELEASE_PUBLIC_KEY" ] || ! command -v openssl >/dev/null 2>&1; then
        _err "Pinned release key and OpenSSL 3 required; refusing installation"
        return 1
    fi
    download_file "${url}.signed" "$temp/release.signed" || return 1
    download_file "${url}.sig" "$temp/release.sig" || return 1
    [ "$(wc -c < "$temp/release.signed")" -le 4096 ] || return 1
    [ "$(wc -c < "$temp/release.sig")" -eq 64 ] || return 1
    local actual_name actual_digest
    actual_name="$(sed -n 's/.*"filename":"\([^"]*\)".*/\1/p' "$temp/release.signed")"
    actual_digest="$(sed -n 's/.*"sha256":"\([^"]*\)".*/\1/p' "$temp/release.signed")"
    [ "$actual_name" = "$filename" ] && [ "$actual_digest" = "$digest" ] || return 1
    printf '\x30\x2a\x30\x05\x06\x03\x2b\x65\x70\x03\x21\x00' > "$temp/key.der"
    printf '%s' "$RELEASE_PUBLIC_KEY" | openssl base64 -d -A >> "$temp/key.der" || return 1
    [ "$(wc -c < "$temp/key.der")" -eq 44 ] || return 1
    openssl pkeyutl -verify -pubin -keyform DER -inkey "$temp/key.der" -rawin \
        -in "$temp/release.signed" -sigfile "$temp/release.sig" >/dev/null 2>&1 || return 1
    _ok "Release signature verified"
}

# -- resolve install dir -------------------------------------------------------
get_install_dir() {
    if [ "$(id -u)" = "0" ]; then
        echo "/usr/local/bin"
    else
        echo "${HOME}/.local/bin"
    fi
}

# -- ensure install dir is on PATH ---------------------------------------------
ensure_in_path() {
    local install_dir="$1"
    if echo "$PATH" | grep -q "$install_dir"; then
        _ok "Already on PATH"
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
            _ok "Added to PATH (${shell_rc/#$HOME/~})"
        fi
    fi
}

# -- main ----------------------------------------------------------------------
main() {
    local platform filename download_url sha256_url tmp_dir
    local install_dir target_display

    platform="$(detect_platform)"

    install_dir="$(get_install_dir)"
    target_display="${install_dir/#$HOME/~}/${BIN_NAME}"

    echo ""
    _title "  OpsKit  Installer"
    echo ""
    _field "Platform" "$platform"
    _field "Source"   "GitHub Releases"
    _field "Target"   "$target_display"
    echo ""

    filename="${BIN_NAME}-${platform}"
    download_url="${DOWNLOAD_BASE}/${filename}"
    sha256_url="${download_url}.sha256"

    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT

    local tmp_bin="${tmp_dir}/${filename}"
    local tmp_sha="${tmp_dir}/${filename}.sha256"

    download_file "$download_url" "$tmp_bin" || {
        _err "Download failed: $download_url"
        exit 1
    }
    _ok "Downloaded   $filename"

    local expected_sha=""
    if download_file "$sha256_url" "$tmp_sha" 2>/dev/null; then
        expected_sha="$(awk '{print $1}' "$tmp_sha")"
    fi

    verify_sha256 "$tmp_bin" "$expected_sha" || exit 1
    expected_sha="$(printf '%s' "$expected_sha" | tr 'A-F' 'a-f')"
    verify_release_signature "$download_url" "$filename" "$expected_sha" "$tmp_dir" || {
        _err "Missing or invalid release signature; refusing installation"
        exit 1
    }

    mkdir -p "$install_dir"
    chmod +x "$tmp_bin"
    mv "$tmp_bin" "${install_dir}/${BIN_NAME}"
    _ok "Installed"

    ensure_in_path "$install_dir"

    echo ""
    _title "  Done!  Open a new terminal, then run:  opskit"
    echo ""

    # Installed via `curl | bash`: this script's stdin is the curl pipe (non-TTY,
    # already at EOF), so a bare exec would make opskit inherit it and quit the
    # moment the menu reads a key. Reconnect stdin to /dev/tty when a real
    # terminal exists; otherwise just leave the run hint above.
    if [ -t 0 ]; then
        exec "${install_dir}/${BIN_NAME}"
    elif [ -r /dev/tty ]; then
        exec "${install_dir}/${BIN_NAME}" < /dev/tty
    fi
}

main "$@"
