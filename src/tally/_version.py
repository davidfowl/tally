# This file is auto-generated during build. Do not edit manually.
VERSION = "0.1.0"
GIT_SHA = "unknown"
REPO_URL = "https://github.com/davidfowl/tally"


def check_for_updates(timeout: float = 2.0) -> dict | None:
    """Check GitHub for a newer version.

    Returns dict with 'latest_version', 'update_available', and 'is_prerelease' keys,
    or None if check fails or current version is unknown.

    If running a dev version (e.g., 0.1.156-dev), checks for newer dev builds.
    Otherwise checks for newer stable releases.
    """
    import urllib.request
    import json

    # Don't check if we're running an unknown version
    if VERSION in ("unknown", "dev", "0.1.0"):
        return None

    # Detect if we're on a prerelease version
    is_prerelease = "-dev" in VERSION

    try:
        # Extract owner/repo from REPO_URL
        # Expected format: https://github.com/owner/repo
        parts = REPO_URL.rstrip('/').split('/')
        if len(parts) < 2:
            return None
        owner, repo = parts[-2], parts[-1]

        # Check prerelease endpoint if running dev version, otherwise stable
        if is_prerelease:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/dev"
        else:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

        req = urllib.request.Request(
            api_url,
            headers={
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': f'tally/{VERSION}'
            }
        )

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
            latest_tag = data.get('tag_name', '')

            # Remove 'v' prefix if present
            latest_version = latest_tag.lstrip('v')

            # Compare versions
            update_available = _version_greater(latest_version, VERSION)

            return {
                'latest_version': latest_version,
                'current_version': VERSION,
                'update_available': update_available,
                'is_prerelease': is_prerelease,
                'release_url': data.get('html_url', f'{REPO_URL}/releases/latest')
            }
    except Exception:
        # Network error, timeout, or API error - fail silently
        return None


def _version_greater(v1: str, v2: str) -> bool:
    """Return True if v1 > v2 using semantic versioning comparison.

    Handles -dev suffix: 0.1.100-dev < 0.1.100 (prerelease < release)
    """
    try:
        def parse_version(v: str) -> tuple:
            # Split off prerelease suffix (e.g., "0.1.100-dev" -> "0.1.100", "dev")
            base, _, prerelease = v.partition('-')
            parts = base.split('.')
            nums = tuple(int(p) for p in parts[:3])
            # Prerelease versions sort before release (0 = prerelease, 1 = release)
            return nums + (0 if prerelease else 1,)

        return parse_version(v1) > parse_version(v2)
    except (ValueError, IndexError):
        return False


def get_platform_asset_name() -> str:
    """Return the release asset name for the current platform."""
    import platform as plat

    system = plat.system().lower()
    machine = plat.machine().lower()

    if system == 'darwin':
        # Detect arm64 (Apple Silicon) vs x86_64 (Intel)
        if machine in ('arm64', 'aarch64'):
            return 'tally-macos-arm64.zip'
        return 'tally-macos-amd64.zip'
    elif system == 'linux':
        return 'tally-linux-amd64.zip'
    elif system == 'windows':
        return 'tally-windows-amd64.zip'
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def get_latest_release_info(timeout: float = 10.0, prerelease: bool = False) -> dict | None:
    """Get latest release info including download URLs.

    Args:
        timeout: Request timeout in seconds
        prerelease: If True, fetch the 'dev' prerelease instead of latest stable

    Returns dict with 'version', 'assets' (dict of name -> url), 'release_url',
    and 'body' (release notes markdown), or None if request fails.
    """
    import urllib.request
    import json

    try:
        parts = REPO_URL.rstrip('/').split('/')
        if len(parts) < 2:
            return None
        owner, repo = parts[-2], parts[-1]

        if prerelease:
            # Find the prerelease from the releases list
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        else:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

        req = urllib.request.Request(
            api_url,
            headers={
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': f'tally/{VERSION}'
            }
        )

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

            # For prerelease, find the one marked as prerelease
            if prerelease:
                for release in data:
                    if release.get('prerelease'):
                        data = release
                        break
                else:
                    return None  # No prerelease found

            assets = {}
            for asset in data.get('assets', []):
                assets[asset['name']] = asset['browser_download_url']

            # For dev releases, version is in name like "Development Build (0.1.134-dev)"
            # For stable releases, version is in tag_name like "v0.1.130"
            version = data.get('tag_name', '').lstrip('v')
            if version == 'dev':
                # Extract version from name: "Development Build (0.1.134-dev)" -> "0.1.134-dev"
                name = data.get('name', '')
                import re
                match = re.search(r'\(([0-9]+\.[0-9]+\.[0-9]+-dev)\)', name)
                if match:
                    version = match.group(1)

            return {
                'version': version,
                'assets': assets,
                'release_url': data.get('html_url', f'{REPO_URL}/releases/latest'),
                'body': data.get('body', ''),
            }
    except Exception:
        return None


def download_file(url: str, dest_path: str, show_progress: bool = True) -> bool:
    """Download a file from URL to destination path.

    Returns True on success, False on failure.
    """
    import urllib.request
    import shutil

    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': f'tally/{VERSION}'}
        )

        with urllib.request.urlopen(req, timeout=60) as response:
            total_size = response.headers.get('Content-Length')
            if total_size:
                total_size = int(total_size)

            with open(dest_path, 'wb') as f:
                downloaded = 0
                block_size = 8192

                while True:
                    chunk = response.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if show_progress and total_size:
                        pct = (downloaded / total_size) * 100
                        print(f"\rDownloading... {pct:.0f}%", end='', flush=True)

                if show_progress:
                    print()  # newline after progress

        return True
    except Exception as e:
        print(f"Download failed: {e}")
        return False


def get_executable_path():
    """Get the path to the currently running tally executable.

    Returns Path object or None if not running as frozen executable.
    """
    import sys
    from pathlib import Path

    # Check if running as PyInstaller frozen executable
    if getattr(sys, 'frozen', False):
        return Path(sys.executable)

    return None


def get_install_path():
    """Get the expected installation path for tally.

    Returns Path object for the install location based on platform.
    For Unix systems, checks new location (~/.local/bin) first, then falls back to old location (~/.tally/bin).
    """
    import platform as plat
    from pathlib import Path
    import os

    system = plat.system().lower()

    if system == 'windows':
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        if local_app_data:
            return Path(local_app_data) / 'tally' / 'tally.exe'
    else:  # macOS, Linux
        home = Path.home()
        new_path = home / '.local' / 'bin' / 'tally'
        old_path = home / '.tally' / 'bin' / 'tally'
        
        # If running from old location, return old path so update can work
        # Otherwise return new location
        if old_path.exists():
            return old_path
        return new_path

    return None


def perform_update(release_info: dict, force: bool = False) -> tuple[bool, str]:
    """Perform the actual binary update.

    Args:
        release_info: Dict from get_latest_release_info()
        force: If True, update even if already on latest version

    Returns:
        Tuple of (success: bool, message: str)
    """
    import tempfile
    import zipfile
    import shutil
    import stat
    import platform as plat
    from pathlib import Path

    # Check if update is needed
    if not force and not _version_greater(release_info['version'], VERSION):
        return True, f"Already on latest version: {VERSION}"

    # Get platform-specific asset
    try:
        asset_name = get_platform_asset_name()
    except RuntimeError as e:
        return False, str(e)

    if asset_name not in release_info['assets']:
        return False, f"No release asset found for this platform: {asset_name}"

    download_url = release_info['assets'][asset_name]

    # Determine install path
    current_path = get_executable_path()
    install_path = current_path or get_install_path()
    if not install_path:
        return False, "Could not determine installation path"

    install_path = Path(install_path)
    
    # For Unix systems, migrate to new location if currently in old location
    system = plat.system().lower()
    if system != 'windows':
        home = Path.home()
        old_path = home / '.tally' / 'bin' / 'tally'
        new_path = home / '.local' / 'bin' / 'tally'
        
        # If updating from old location, target new location instead
        if current_path and current_path == old_path:
            print(f"Migrating installation from {old_path} to {new_path}...")
            install_path = new_path

    # Check if running from source (not frozen)
    if not getattr(__import__('sys'), 'frozen', False):
        return False, "Cannot self-update when running from source. Use: uv tool upgrade tally"

    binary_name = 'tally.exe' if system == 'windows' else 'tally'

    try:
        # Create temp directory for download
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / asset_name

            # Download
            print(f"Downloading {asset_name}...")
            if not download_file(download_url, str(zip_path)):
                return False, "Download failed"

            # Extract
            print("Extracting...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(temp_path)

            new_binary = temp_path / binary_name
            if not new_binary.exists():
                return False, f"Expected binary not found in archive: {binary_name}"

            # Make executable on Unix
            if system != 'windows':
                new_binary.chmod(new_binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            # Ensure install directory exists
            install_path.parent.mkdir(parents=True, exist_ok=True)

            # Backup existing binary
            backup_path = install_path.with_suffix('.bak' if system != 'windows' else '.exe.bak')
            if install_path.exists():
                print("Backing up current binary...")
                shutil.copy2(install_path, backup_path)

            # Replace binary
            print("Installing new version...")
            if system == 'windows':
                # On Windows, rename current (can't delete running exe) then copy new
                if install_path.exists():
                    old_path = install_path.with_name('tally.old.exe')
                    try:
                        old_path.unlink(missing_ok=True)
                    except:
                        pass
                    install_path.rename(old_path)
                shutil.copy2(new_binary, install_path)
            else:
                # On Unix, atomic rename
                shutil.copy2(new_binary, install_path)
            
            # Clean up old installation on Unix if we migrated
            if system != 'windows' and current_path:
                old_install = home / '.tally' / 'bin' / 'tally'
                if current_path == old_install and install_path != old_install:
                    try:
                        if old_install.exists():
                            old_install.unlink()
                            print(f"Removed old installation at {old_install}")
                        
                        # Remove old directory if empty
                        old_bin_dir = old_install.parent
                        if old_bin_dir.exists() and not list(old_bin_dir.iterdir()):
                            old_bin_dir.rmdir()
                        old_tally_dir = old_bin_dir.parent
                        if old_tally_dir.exists() and not list(old_tally_dir.iterdir()):
                            old_tally_dir.rmdir()
                            print(f"Removed old directory {old_tally_dir}")
                    except Exception as e:
                        # Don't fail the update if cleanup fails
                        print(f"Note: Could not clean up old installation: {e}")

            msg = f"Updated to v{release_info['version']}"
            if system != 'windows' and current_path and current_path != install_path:
                msg += f"\n\nInstalled to new location: {install_path}"
                msg += f"\nTo complete migration, update your PATH to use ~/.local/bin instead of ~/.tally/bin"
            
            return True, msg

    except PermissionError:
        return False, f"Permission denied. Try running with elevated privileges or manually update at: {install_path}"
    except Exception as e:
        return False, f"Update failed: {e}"
