#!/usr/bin/env bash
# OpsKit installer - Linux / macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/ougato/opskit-cli/master/install.sh | bash
set -euo pipefail

BIN_NAME="opskit"
RELEASE_BASE="https://github.com/ougato/opskit-cli/releases"
BOOTSTRAP_URL="https://raw.githubusercontent.com/ougato/opskit-cli/master/bootstrap.json"

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
        curl -fsSL --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 120 -o "$dest" "$url"
    else
        wget --timeout=120 --tries=1 -qO "$dest" "$url"
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

download_verified_file() {
    local url="$1" file="$2"
    download_file "$url" "$file" || {
        _err "Download failed: $url"
        return 1
    }
    _ok "Downloaded   ${file##*/}"
    download_file "$url.sha256" "$file.sha256" || {
        _err "SHA256 file download failed; installation will be refused"
        return 1
    }
    local expected
    expected="$(awk 'NR == 1 {print $1; exit}' "$file.sha256")"
    verify_sha256 "$file" "$expected"
}

get_release_tag() {
    # bootstrap.json is emitted by release_bump.py with two-space indentation.
    LC_ALL=C awk '
        /^  "(schema_version|channel|latest_build|min_build|display|kill_switch)":/ {
            key = $1
            gsub(/[":]/, "", key)
            if (key in fields) invalid = 1
            sub(/^[^:]*:[[:space:]]*/, "")
            sub(/,?[[:space:]]*$/, "")
            fields[key] = $0
        }
        END {
            build = fields["latest_build"]
            minimum = fields["min_build"]
            tag = "v" build
            if (invalid || fields["schema_version"] != "2" || fields["channel"] != "\"stable\"" ||
                build !~ /^[1-9][0-9]*$/ || length(build) > 9 ||
                minimum !~ /^(0|[1-9][0-9]*)$/ || minimum + 0 > build + 0 ||
                fields["display"] != "\"" tag "\"" || fields["kill_switch"] != "false") exit 1
            print tag
        }
    ' "$1"
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

install_release() (
    local platform="$1" install_dir="$2"
    local stage tag archive launcher file
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf -- "$tmp_dir"' EXIT
    stage="$(cd "$tmp_dir" && pwd -P)"

    if [[ "$platform" == linux-* ]]; then
        download_file "$BOOTSTRAP_URL" "$stage/bootstrap.json" || {
            _err "OpsKit release metadata download failed"
            exit 1
        }
        tag="$(get_release_tag "$stage/bootstrap.json")" || {
            _err "Invalid OpsKit release metadata or installation paused"
            exit 1
        }
        launcher="${BIN_NAME}-${platform}-launcher"
        archive="${BIN_NAME}-${platform}.tar.xz"
        download_verified_file "$RELEASE_BASE/download/$tag/$launcher" "$stage/$launcher" || exit 1
        download_verified_file "$RELEASE_BASE/download/$tag/$archive" "$stage/$archive" || exit 1

        mkdir -p "$install_dir"
        chmod +x "$stage/$launcher"
        "$stage/$launcher" --opskit-internal-install "$stage/$archive" "$install_dir/$BIN_NAME" "$tag" || {
            _err "OpsKit installation failed"
            exit 1
        }
    else
        file="${BIN_NAME}-${platform}"
        download_verified_file "$RELEASE_BASE/latest/download/$file" "$stage/$file" || exit 1
        mkdir -p "$install_dir"
        chmod +x "$stage/$file"
        mv "$stage/$file" "$install_dir/$BIN_NAME"
    fi
)

# -- main ----------------------------------------------------------------------
main() {
    local platform
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

    install_release "$platform" "$install_dir"
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
    elif { exec 3</dev/tty; } 2>/dev/null; then
        exec "${install_dir}/${BIN_NAME}" <&3 3<&-
    fi
}

main "$@"
