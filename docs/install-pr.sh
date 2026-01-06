#!/bin/bash
set -e

# Tally PR installer script
# Usage: curl -fsSL https://raw.githubusercontent.com/davidfowl/tally/main/docs/install-pr.sh | bash -s -- <PR_NUMBER>
#
# Requires: GitHub CLI (gh) installed and authenticated
#   brew install gh && gh auth login

REPO="davidfowl/tally"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
TMPDIR="${TMPDIR:-/tmp}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}warning:${NC} $1"; }
error() { echo -e "${RED}error:${NC} $1" >&2; exit 1; }

# Check for gh CLI
check_gh() {
    if ! command -v gh &> /dev/null; then
        error "GitHub CLI (gh) is required but not installed.
Install it with:
  macOS:  brew install gh
  Linux:  https://github.com/cli/cli#installation

Then authenticate with: gh auth login"
    fi

    if ! gh auth status &> /dev/null; then
        error "GitHub CLI is not authenticated. Run: gh auth login"
    fi
}

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

# Get the latest successful workflow run for a PR
get_workflow_run_id() {
    local pr_number="$1"

    # Get the head SHA of the PR
    local head_sha
    head_sha=$(gh api "repos/${REPO}/pulls/${pr_number}" --jq '.head.sha')

    if [ -z "$head_sha" ]; then
        error "Could not find PR #${pr_number}"
    fi

    info "PR #${pr_number} head commit: ${head_sha:0:7}"

    # Find the latest successful workflow run for this commit
    local run_id
    run_id=$(gh api "repos/${REPO}/actions/workflows/pr-build.yml/runs?head_sha=${head_sha}&status=success" \
        --jq '.workflow_runs[0].id')

    if [ -z "$run_id" ] || [ "$run_id" = "null" ]; then
        error "No successful build found for PR #${pr_number}.
Check https://github.com/${REPO}/pull/${pr_number}/checks"
    fi

    echo "$run_id"
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
    local pr_number="$1"

    if [ -z "$pr_number" ]; then
        error "Usage: $0 <PR_NUMBER>

Example: $0 42"
    fi

    check_gh

    info "Installing tally from PR #${pr_number}..."
    
    # Migrate from old installation if it exists
    migrate_old_installation

    OS=$(detect_os)
    ARCH=$(detect_arch)
    PLATFORM="${OS}-${ARCH}"

    info "Detected: ${PLATFORM}"

    # Get workflow run ID
    RUN_ID=$(get_workflow_run_id "$pr_number")
    info "Found workflow run: ${RUN_ID}"

    # Download artifact
    DOWNLOAD_PATH="${TMPDIR}/tally-pr-$$"
    mkdir -p "$DOWNLOAD_PATH"

    ARTIFACT_NAME="tally-${PLATFORM}"
    info "Downloading ${ARTIFACT_NAME}..."

    if ! gh run download "$RUN_ID" -R "$REPO" --name "$ARTIFACT_NAME" -D "$DOWNLOAD_PATH"; then
        error "Failed to download artifact. The build may still be in progress.
Check https://github.com/${REPO}/actions/runs/${RUN_ID}"
    fi

    # Extract
    info "Extracting..."
    unzip -q "${DOWNLOAD_PATH}/${ARTIFACT_NAME}.zip" -d "${DOWNLOAD_PATH}"

    # Install
    mkdir -p "$INSTALL_DIR"
    mv "${DOWNLOAD_PATH}/tally" "${INSTALL_DIR}/tally"
    chmod +x "${INSTALL_DIR}/tally"

    # Cleanup
    rm -rf "$DOWNLOAD_PATH"

    # Verify installation
    info "Successfully installed tally from PR #${pr_number}!"
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
