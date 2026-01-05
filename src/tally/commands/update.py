"""
Tally 'update' command - Check for and install updates.
"""

import sys

from .._version import (
    VERSION,
    get_latest_release_info,
    perform_update,
)

from ..cli_utils import resolve_config_dir
from ..migrations import run_migrations


def cmd_update(args):
    """Handle the update command."""
    if args.prerelease:
        print("Checking for development builds...")
    else:
        print("Checking for updates...")

    # Get release info (may fail if offline or rate-limited)
    release_info = get_latest_release_info(prerelease=args.prerelease)
    has_update = False
    is_prerelease_to_stable = False

    if release_info:
        latest = release_info['version']
        current = VERSION

        # Show version comparison
        from .._version import _version_greater
        has_update = _version_greater(latest, current)

        # Special case: user is on prerelease (-dev) and checking for stable
        # Offer to switch to stable even if stable has lower base version
        if not args.prerelease and '-dev' in current and '-dev' not in latest:
            is_prerelease_to_stable = True
            has_update = True  # Always offer stable when on prerelease

        if has_update:
            if args.prerelease:
                print(f"Development build available: v{latest} (current: v{current})")
            elif is_prerelease_to_stable:
                print(f"Stable release available: v{latest} (current: v{current})")
            else:
                print(f"New version available: v{latest} (current: v{current})")
        else:
            print(f"Already on latest version: v{current}")
    else:
        if args.prerelease:
            print("No development build found. Dev builds are created on each push to main.")
        else:
            print("Could not check for version updates (network issue?)")

    # If --check only, just show status and exit
    if args.check:
        if has_update:
            if args.prerelease:
                print(f"\nRun 'tally update --prerelease' to install the development build.")
            else:
                print(f"\nRun 'tally update' to install the update.")
        sys.exit(0)

    # Check for migrations (layout updates, etc.)
    # This runs even if version check failed
    config_dir = resolve_config_dir(args, required=False)
    did_migrate = False
    if config_dir:
        old_config = config_dir
        new_config = run_migrations(config_dir, skip_confirm=args.yes)
        if new_config and new_config != old_config:
            did_migrate = True

    # Skip binary update if no update available
    if not has_update:
        if not did_migrate:
            print("\nNothing to update.")
        sys.exit(0)

    # Check if running from source (can't self-update)
    import sys as _sys
    if not getattr(_sys, 'frozen', False):
        print(f"\n✗ Cannot self-update when running from source. Use: uv tool upgrade tally")
        sys.exit(1)

    # Perform binary update (force=True when switching from prerelease to stable)
    print()
    success, message = perform_update(release_info, force=is_prerelease_to_stable)

    if success:
        print(f"\n✓ {message}")
    else:
        print(f"\n✗ {message}")
        sys.exit(1)
