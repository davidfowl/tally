#!/bin/bash
set -e

# Tally installer script
# Usage: curl -fsSL https://raw.githubusercontent.com/davidfowl/tally/main/install.sh | bash

REPO="davidfowl/tally"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.tally/bin}"
TMPDIR="${TMPDIR:-/tmp}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}warning:${NC} $1"; }
error() { echo -e "${RED}error:${NC} $1" >&2; exit 1; }

# Detect OS
detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux" ;;
        Darwin*) echo "macos" ;;
        *)       error "Unsupported OS: $(uname -s)" ;;
    esac
}

# Detect architecture
detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64)  echo "amd64" ;;
        arm64|aarch64) echo "arm64" ;;
        *)             error "Unsupported architecture: $(uname -m)" ;;
    esac
}

# Get latest release version
get_latest_version() {
    curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" |
        grep '"tag_name":' |
        sed -E 's/.*"([^"]+)".*/\1/'
}

main() {
    info "Installing tally..."

    OS=$(detect_os)
    ARCH=$(detect_arch)

    info "Detected: ${OS}-${ARCH}"

    # Get latest version
    VERSION=$(get_latest_version)
    if [ -z "$VERSION" ]; then
        error "Could not determine latest version. Check https://github.com/${REPO}/releases"
    fi
    info "Latest version: ${VERSION}"

    # Download URL
    FILENAME="tally-${OS}-${ARCH}.zip"
    URL="https://github.com/${REPO}/releases/download/${VERSION}/${FILENAME}"

    info "Downloading ${URL}..."

    DOWNLOAD_PATH="${TMPDIR}/tally-download-$$"
    mkdir -p "$DOWNLOAD_PATH"

    if ! curl -fsSL "$URL" -o "${DOWNLOAD_PATH}/${FILENAME}"; then
        error "Failed to download ${URL}"
    fi

    # Extract
    info "Extracting..."
    unzip -q "${DOWNLOAD_PATH}/${FILENAME}" -d "${DOWNLOAD_PATH}"

    # Install
    mkdir -p "$INSTALL_DIR"
    mv "${DOWNLOAD_PATH}/tally" "${INSTALL_DIR}/tally"
    chmod +x "${INSTALL_DIR}/tally"

    # Cleanup
    rm -rf "$DOWNLOAD_PATH"

    # Verify installation
    info "Successfully installed tally!"
    "${INSTALL_DIR}/tally" version

    # Add to PATH if not already there
    if [[ ":$PATH:" != *":${INSTALL_DIR}:"* ]]; then
        add_to_path
    fi
}

# Detect shell and add to appropriate config file
add_to_path() {
    local shell_name
    shell_name=$(basename "${SHELL:-/bin/bash}")

    local config_file=""
    local path_line=""

    case "$shell_name" in
        bash)
            if [[ -f "$HOME/.bashrc" ]]; then
                config_file="$HOME/.bashrc"
            elif [[ -f "$HOME/.bash_profile" ]]; then
                config_file="$HOME/.bash_profile"
            else
                config_file="$HOME/.bashrc"
            fi
            path_line='export PATH="$HOME/.tally/bin:$PATH"'
            ;;
        zsh)
            config_file="${ZDOTDIR:-$HOME}/.zshrc"
            path_line='export PATH="$HOME/.tally/bin:$PATH"'
            ;;
        fish)
            config_file="${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"
            path_line='fish_add_path $HOME/.tally/bin'
            ;;
        *)
            # Fallback to .profile for other POSIX shells
            config_file="$HOME/.profile"
            path_line='export PATH="$HOME/.tally/bin:$PATH"'
            ;;
    esac

    # Create config file directory if needed
    mkdir -p "$(dirname "$config_file")"

    # Check if already added
    if [[ -f "$config_file" ]] && grep -q "/.tally/bin" "$config_file" 2>/dev/null; then
        return
    fi

    # Add to config file
    echo "" >> "$config_file"
    echo "# Added by tally installer" >> "$config_file"
    echo "$path_line" >> "$config_file"

    info "Added tally to PATH in $config_file"
    echo ""
    echo "Restart your terminal or run:"
    echo "  source $config_file"
}

main "$@"
