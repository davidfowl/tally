"""
Migration utilities for tally configuration.

Handles:
- Schema version tracking
- Layout migrations (v0 to v1)
- CSV to .rules format migration
"""

import os
import shutil
import sys

from .colors import C
from .merchant_utils import get_all_rules, load_merchant_rules


# Schema version for asset migrations
SCHEMA_VERSION = 1


def get_schema_version(config_dir):
    """Get current schema version from config directory.

    Returns:
        int: Schema version (0 if no marker file exists - legacy layout)
    """
    schema_file = os.path.join(config_dir, '.tally-schema')
    if os.path.exists(schema_file):
        try:
            with open(schema_file, encoding='utf-8') as f:
                return int(f.read().strip())
        except (ValueError, IOError):
            return 0
    return 0


def run_migrations(config_dir, skip_confirm=False):
    """Run any pending migrations on the config directory.

    Args:
        config_dir: Path to current config directory
        skip_confirm: If True, skip confirmation prompts (--yes flag)

    Returns:
        str: Path to config directory (may change if layout migrated)
    """
    current = get_schema_version(config_dir)

    if current >= SCHEMA_VERSION:
        return config_dir  # Already up to date

    # Run migrations in order
    if current < 1:
        result = migrate_v0_to_v1(config_dir, skip_confirm)
        if result:
            config_dir = result

    return config_dir


def migrate_v0_to_v1(old_config_dir, skip_confirm=False):
    """Migrate from legacy layout (./config) to new layout (./tally/config).

    Args:
        old_config_dir: Path to the old config directory
        skip_confirm: If True, skip confirmation prompt

    Returns:
        str: Path to new config directory, or None if user declined
    """
    # Only migrate if we're in the old layout (./config at working directory root)
    if os.path.basename(old_config_dir) != 'config':
        return None
    if os.path.dirname(old_config_dir) != os.getcwd():
        return None

    # Prompt user (skip if non-interactive or --yes flag)
    if not skip_confirm:
        # In non-interactive mode (e.g., LLM/CI), skip migration silently
        if not sys.stdin.isatty():
            return None

        print()
        print("Migration available: Layout update")
        print("  Current: ./config (legacy layout)")
        print("  New: ./tally/config")
        print()
        try:
            response = input("Migrate to new layout? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nSkipped.")
            return None
        if response == 'n':
            return None

    # Perform migration
    tally_dir = os.path.abspath('tally')
    try:
        os.makedirs(tally_dir, exist_ok=True)

        # Move config directory
        new_config = os.path.join(tally_dir, 'config')
        print(f"  Moving config/ -> tally/config/")
        shutil.move(old_config_dir, new_config)

        # Move data and output directories if they exist
        for subdir in ['data', 'output']:
            old_path = os.path.abspath(subdir)
            if os.path.isdir(old_path):
                new_path = os.path.join(tally_dir, subdir)
                print(f"  Moving {subdir}/ -> tally/{subdir}/")
                shutil.move(old_path, new_path)

        # Write schema version marker
        schema_file = os.path.join(new_config, '.tally-schema')
        with open(schema_file, 'w', encoding='utf-8') as f:
            f.write('1\n')

        print("✓ Migrated to ./tally/")
        return new_config

    except (OSError, shutil.Error) as e:
        print(f"Error during migration: {e}", file=sys.stderr)
        return None


def migrate_csv_to_rules(csv_file: str, config_dir: str, backup: bool = True) -> bool:
    """
    Migrate merchant_categories.csv to merchants.rules format.

    Args:
        csv_file: Path to the CSV file
        config_dir: Path to config directory
        backup: Whether to rename old CSV to .bak

    Returns:
        True if migration was successful
    """
    from .merchant_engine import csv_to_merchants_content

    try:
        # Load and convert
        csv_rules = load_merchant_rules(csv_file)
        content = csv_to_merchants_content(csv_rules)

        # Write new file
        new_file = os.path.join(config_dir, 'merchants.rules')
        with open(new_file, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  {C.GREEN}✓{C.RESET} Created: config/merchants.rules")
        print(f"      Converted {len(csv_rules)} merchant rules to new format")

        # Backup old file
        if backup and os.path.exists(csv_file):
            shutil.move(csv_file, csv_file + '.bak')
            print(f"  {C.GREEN}✓{C.RESET} Backed up: merchant_categories.csv → .bak")

        # Update settings.yaml to reference new file
        settings_path = os.path.join(config_dir, 'settings.yaml')
        if os.path.exists(settings_path):
            with open(settings_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'merchants_file:' not in content:
                with open(settings_path, 'a', encoding='utf-8') as f:
                    f.write('\n# Merchant rules file (migrated from CSV)\n')
                    f.write('merchants_file: config/merchants.rules\n')
                print(f"  {C.GREEN}✓{C.RESET} Updated: config/settings.yaml")
                print(f"      Added merchants_file: config/merchants.rules")

        return True
    except Exception as e:
        print(f"  {C.RED}✗{C.RESET} Migration failed: {e}")
        return False


def check_merchant_migration(config: dict, config_dir: str, quiet: bool = False, migrate: bool = False) -> list:
    """
    Check if merchant rules should be migrated from CSV to .rules format.

    Args:
        config: Loaded config dict with _merchants_file and _merchants_format
        config_dir: Path to config directory
        quiet: Suppress output
        migrate: Force migration without prompting (for non-interactive use)

    Returns:
        List of merchant rules (in the format expected by existing code)
    """
    merchants_file = config.get('_merchants_file')
    merchants_format = config.get('_merchants_format')
    rule_mode = config.get('rule_mode', 'first_match')

    if not merchants_file:
        # No rules file found
        if not quiet:
            print(f"No merchant rules found - transactions will be categorized as Unknown")
        return get_all_rules(match_mode=rule_mode)

    if merchants_format == 'csv':
        # CSV format - show deprecation warning and offer migration
        csv_rules = load_merchant_rules(merchants_file)

        # Determine if we should migrate
        should_migrate = migrate  # --migrate flag forces it
        is_interactive = sys.stdout.isatty() and not migrate

        if not quiet:
            print()
            print(f"{C.YELLOW}╭─ Upgrade Available ─────────────────────────────────────────────────╮{C.RESET}")
            print(f"{C.YELLOW}│{C.RESET} Found: merchant_categories.csv (legacy CSV format)                  {C.YELLOW}│{C.RESET}")
            print(f"{C.YELLOW}│{C.RESET}                                                                      {C.YELLOW}│{C.RESET}")
            print(f"{C.YELLOW}│{C.RESET} The new .rules format supports powerful expressions:                 {C.YELLOW}│{C.RESET}")
            print(f"{C.YELLOW}│{C.RESET}   match: contains(\"COSTCO\") and amount > 200                        {C.YELLOW}│{C.RESET}")
            print(f"{C.YELLOW}│{C.RESET}   match: regex(\"UBER.*EATS\") and month == 12                        {C.YELLOW}│{C.RESET}")
            print(f"{C.YELLOW}╰──────────────────────────────────────────────────────────────────────╯{C.RESET}")
            print()

        if is_interactive:
            # Only prompt if interactive and not using --migrate
            try:
                response = input(f"   Migrate to new format? [y/N] ").strip().lower()
                should_migrate = (response == 'y')
            except (EOFError, KeyboardInterrupt):
                should_migrate = False

            if not should_migrate:
                print(f"   {C.DIM}Skipped - continuing with CSV format for this run{C.RESET}")
                print()
        elif not migrate and not quiet:
            # Non-interactive without --migrate flag
            print(f"   {C.DIM}Tip: Run with --migrate to convert automatically{C.RESET}")
            print()

        if should_migrate:
            # Perform migration using shared helper
            print(f"{C.CYAN}Migrating to new format...{C.RESET}")
            print()
            if migrate_csv_to_rules(merchants_file, config_dir, backup=True):
                print()
                print(f"{C.GREEN}Migration complete!{C.RESET} Your rules now support expressions.")
                print()
                # Return new rules from migrated file
                new_file = os.path.join(config_dir, 'merchants.rules')
                return get_all_rules(new_file, match_mode=rule_mode)

        # Continue with CSV format for this run (backwards compatible)
        if not quiet:
            print(f"Loaded {len(csv_rules)} categorization rules from {merchants_file}")
            if len(csv_rules) == 0:
                print()
                print("⚠️  No merchant rules defined - all transactions will be 'Unknown'")
                print("    Run 'tally discover' to find unknown merchants and get suggested rules.")
                print("    Tip: Use an AI agent with 'tally discover' to auto-generate rules!")
                print()

        return get_all_rules(merchants_file, match_mode=rule_mode)

    # New .rules format
    if merchants_format == 'new':
        rules = get_all_rules(merchants_file, match_mode=rule_mode)
        if not quiet:
            print(f"Loaded {len(rules)} categorization rules from {merchants_file}")
            if len(rules) == 0:
                print()
                print("⚠️  No merchant rules defined - all transactions will be 'Unknown'")
                print("    Run 'tally discover' to find unknown merchants and get suggested rules.")
                print("    Tip: Use an AI agent with 'tally discover' to auto-generate rules!")
                print()
        return rules

    # No rules file found
    if not quiet:
        print(f"No merchant rules found - transactions will be categorized as Unknown")
    return get_all_rules(match_mode=rule_mode)
