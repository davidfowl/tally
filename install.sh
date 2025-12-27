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
        arm64|aarch64) echo "amd64" ;;  # Use amd64 for now, add arm64 builds later
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

    # Verify and show PATH instructions
    info "Successfully installed tally to ${INSTALL_DIR}/tally"
    "${INSTALL_DIR}/tally" version

    if [[ ":$PATH:" != *":${INSTALL_DIR}:"* ]]; then
        echo ""
        warn "Add tally to your PATH by adding this to your shell profile:"
        echo ""
        echo "  export PATH=\"\$HOME/.tally/bin:\$PATH\""
        echo ""
        echo "Then restart your terminal or run: source ~/.bashrc (or ~/.zshrc)"
    fi
}

main "$@"
