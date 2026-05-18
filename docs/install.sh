#!/bin/bash
set -e

# Tally installer script
# Usage: curl -fsSL https://raw.githubusercontent.com/davidfowl/tally/main/install.sh | bash
# Usage: curl -fsSL https://raw.githubusercontent.com/davidfowl/tally/main/install.sh | bash -s -- --prerelease

REPO="davidfowl/tally"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
TMPDIR="${TMPDIR:-/tmp}"
PRERELEASE=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}warning:${NC} $1"; }
error() { echo -e "${RED}error:${NC} $1" >&2; exit 1; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--prerelease)
            PRERELEASE=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

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

# Get release version (latest stable or dev prerelease)
get_release_version() {
    if [ "$PRERELEASE" = true ]; then
        # Dev prerelease always uses 'dev' tag - no API call needed
        echo "dev"
    else
        # Get latest stable release
        curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" |
            grep '"tag_name":' |
            sed -E 's/.*"([^"]+)".*/\1/'
    fi
}

# Migrate from old installation location
migrate_old_installation() {
    local old_install_dir="$HOME/.tally/bin"
    local old_binary="${old_install_dir}/tally"
    
    if [[ -f "$old_binary" ]]; then
        info "Found existing installation at ${old_install_dir}"
        info "Migrating to ${INSTALL_DIR}..."
        
        # Remove old binary
        rm -f "$old_binary"
        
        # Remove old directory if empty
        if [[ -d "$old_install_dir" ]] && [[ -z "$(ls -A "$old_install_dir")" ]]; then
            rmdir "$old_install_dir"
        fi
        if [[ -d "$HOME/.tally" ]] && [[ -z "$(ls -A "$HOME/.tally")" ]]; then
            rmdir "$HOME/.tally"
        fi
        
        # Clean up old PATH entries from shell config files
        local config_files=(
            "$HOME/.bashrc"
            "$HOME/.bash_profile"
            "$HOME/.zshrc"
            "${ZDOTDIR:-$HOME}/.zshrc"
            "$HOME/.profile"
            "${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"
        )
        
        for config_file in "${config_files[@]}"; do
            if [[ -f "$config_file" ]] && grep -q "/.tally/bin" "$config_file" 2>/dev/null; then
                # Create backup
                cp "$config_file" "${config_file}.bak"
                # Remove lines containing .tally/bin
                sed -i.tmp '/\.tally\/bin/d' "$config_file" 2>/dev/null || sed -i '' '/\.tally\/bin/d' "$config_file" 2>/dev/null
                rm -f "${config_file}.tmp"
                info "Cleaned up old PATH entry in $(basename "$config_file")"
            fi
        done
        
        info "Migration complete!"
    fi
}

main() {
    if [ "$PRERELEASE" = true ]; then
        info "Installing tally (development build)..."
    else
        info "Installing tally..."
    fi
    
    # Migrate from old installation if it exists
    migrate_old_installation

    OS=$(detect_os)
    ARCH=$(detect_arch)

    info "Detected: ${OS}-${ARCH}"

    # Get release version
    VERSION=$(get_release_version)
    if [ -z "$VERSION" ]; then
        if [ "$PRERELEASE" = true ]; then
            error "No development build found. Dev builds are created on each push to main."
        else
            error "Could not determine latest version. Check https://github.com/${REPO}/releases"
        fi
    fi
    if [ "$PRERELEASE" = true ]; then
        info "Development build: ${VERSION}"
    else
        info "Latest version: ${VERSION}"
    fi

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
    # GitHub Actions: add to GITHUB_PATH
    if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
        echo "$INSTALL_DIR" >> "$GITHUB_PATH"
        info "Added to GITHUB_PATH for this workflow"
        return
    fi

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
            path_line='export PATH="$HOME/.local/bin:$PATH"'
            ;;
        zsh)
            config_file="${ZDOTDIR:-$HOME}/.zshrc"
            path_line='export PATH="$HOME/.local/bin:$PATH"'
            ;;
        fish)
            config_file="${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"
            path_line='fish_add_path $HOME/.local/bin'
            ;;
        *)
            # Fallback to .profile for other POSIX shells
            config_file="$HOME/.profile"
            path_line='export PATH="$HOME/.local/bin:$PATH"'
            ;;
    esac

    # Create config file directory if needed
    mkdir -p "$(dirname "$config_file")"

    # Check if already added
    if [[ -f "$config_file" ]] && grep -q "/.local/bin" "$config_file" 2>/dev/null; then
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
