"""
Tally CLI - Command-line interface.

Usage:
    tally /path/to/config/dir               # Analyze using config directory
    tally /path/to/config/dir --summary     # Summary only (no HTML)
    tally /path/to/config/dir --settings settings-2024.yaml
    tally --help-config                     # Show detailed config documentation
"""

import argparse
import os
import shutil
import sys

# Terminal color support
def _supports_color():
    """Check if the terminal supports color output."""
    if not sys.stdout.isatty():
        return False
    if os.environ.get('NO_COLOR'):
        return False
    if os.environ.get('FORCE_COLOR'):
        return True
    # Check for common terminal types
    term = os.environ.get('TERM', '')
    return term != 'dumb'

def _setup_windows_encoding():
    """Set UTF-8 encoding on Windows to support Unicode output."""
    if sys.platform != 'win32':
        return

    import codecs

    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name)
        # Skip if already UTF-8
        if getattr(stream, 'encoding', '').lower().replace('-', '') == 'utf8':
            continue
        try:
            # Method 1: reconfigure (works in normal Python 3.7+)
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError):
            try:
                # Method 2: Use codecs writer (more reliable for PyInstaller)
                if hasattr(stream, 'buffer'):
                    writer = codecs.getwriter('utf-8')(stream.buffer, errors='replace')
                    writer.encoding = 'utf-8'
                    setattr(sys, stream_name, writer)
            except Exception:
                pass

_setup_windows_encoding()

class _Colors:
    """ANSI color codes with automatic detection."""
    def __init__(self):
        if _supports_color():
            self.RESET = '\033[0m'
            self.BOLD = '\033[1m'
            self.DIM = '\033[2m'
            self.GREEN = '\033[32m'
            self.CYAN = '\033[36m'
            self.BLUE = '\033[34m'
            self.YELLOW = '\033[33m'
            self.RED = '\033[31m'
            self.UNDERLINE = '\033[4m'
        else:
            self.RESET = ''
            self.BOLD = ''
            self.DIM = ''
            self.GREEN = ''
            self.CYAN = ''
            self.BLUE = ''
            self.YELLOW = ''
            self.RED = ''
            self.UNDERLINE = ''

C = _Colors()

from ._version import (
    VERSION, GIT_SHA, REPO_URL, check_for_updates,
    get_latest_release_info, perform_update
)
from .config_loader import load_config

BANNER = ''
from .merchant_utils import get_all_rules, diagnose_rules, explain_description, load_merchant_rules, get_tag_only_rules, apply_tag_rules, get_transforms
from .analyzer import (
    parse_amex,
    parse_boa,
    parse_generic_csv,
    auto_detect_csv_format,
    analyze_transactions,
    print_summary,
    print_sections_summary,
    write_summary_file_vue,
)


def _migrate_csv_to_rules(csv_file: str, config_dir: str, backup: bool = True) -> bool:
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
    from .merchant_utils import load_merchant_rules
    import shutil

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


def _check_merchant_migration(config: dict, config_dir: str, quiet: bool = False, migrate: bool = False) -> list:
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

    if not merchants_file:
        # No rules file found
        if not quiet:
            print(f"No merchant rules found - transactions will be categorized as Unknown")
        return get_all_rules()

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
            if _migrate_csv_to_rules(merchants_file, config_dir, backup=True):
                print()
                print(f"{C.GREEN}Migration complete!{C.RESET} Your rules now support expressions.")
                print()
                # Return new rules from migrated file
                new_file = os.path.join(config_dir, 'merchants.rules')
                return get_all_rules(new_file)

        # Continue with CSV format for this run (backwards compatible)
        if not quiet:
            print(f"Loaded {len(csv_rules)} categorization rules from {merchants_file}")
            if len(csv_rules) == 0:
                print()
                print("⚠️  No merchant rules defined - all transactions will be 'Unknown'")
                print("    Run 'tally discover' to find unknown merchants and get suggested rules.")
                print("    Tip: Use an AI agent with 'tally discover' to auto-generate rules!")
                print()

        return get_all_rules(merchants_file)

    # New .rules format
    if merchants_format == 'new':
        rules = get_all_rules(merchants_file)
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
    return get_all_rules()


CONFIG_HELP = '''
BUDGET ANALYZER - CONFIGURATION
================================

QUICK START
-----------
1. Run: tally init ./my-budget
2. Add CSV/TXT statements to my-budget/data/
3. Edit my-budget/config/settings.yaml with your data sources
4. Run: tally up ./my-budget/config

DIRECTORY STRUCTURE
-------------------
my-budget/
├── config/
│   ├── settings.yaml           # Data sources & settings
│   └── merchants.rules     # Merchant categorization rules
├── data/                       # Your statement exports
└── output/                     # Generated reports

SETTINGS.YAML
-------------
year: 2025
merchants_file: config/merchants.rules
data_sources:
  - name: AMEX
    file: data/amex.csv
    format: "{date:%m/%d/%Y},{description},{amount}"
  - name: Chase
    file: data/chase.csv
    format: "{date:%m/%d/%Y},{description},{amount}"
output_dir: output

# Optional: specify home locations (auto-detected if not set)
home_locations:
  - WA
  - OR                          # Nearby state to not count as travel

# Optional: pretty names for travel destinations
travel_labels:
  HI: Hawaii
  GB: United Kingdom

TRAVEL DETECTION
----------------
International transactions are automatically classified as travel.
Domestic out-of-state is NOT auto-travel. To opt-in, add merchant rules:

  .*\\sHI$,Hawaii Trip,Travel,Hawaii
  .*\\sCA$,California Trip,Travel,California

DISCOVERING UNKNOWN MERCHANTS
-----------------------------
Use the discover command to find uncategorized transactions:
  tally discover               # Human-readable output
  tally discover --format csv  # CSV output to copy-paste
  tally discover --format json # JSON for programmatic use

MERCHANT RULES (.rules format)
----------------------------------
Define merchant patterns in config/merchants.rules:

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment, recurring

[Uber Rides]
match: regex("UBER(?!.*EATS)")
category: Transportation
subcategory: Rideshare

Match expressions:
  contains("X")     Case-insensitive substring match
  regex("pattern")  Full regex support
  amount > 100      Amount conditions
  month == 12       Date conditions

Use: tally inspect <file.csv> to see transaction formats.
'''

STARTER_SETTINGS = '''# Tally Settings
year: {year}
title: "Spending Analysis {year}"

# Data sources - add your statement files here
# Run: tally inspect <file> to auto-detect the format string
data_sources:
  # Example credit card CSV (positive amounts = purchases):
  # - name: Credit Card
  #   file: data/card-{year}.csv
  #   format: "{{date:%m/%d/%Y}},{{description}},{{amount}}"
  #
  # Amount modifiers:
  #   {{amount}}   - Keep original sign from CSV
  #   {{-amount}}  - Flip sign (bank statements where negative = expense)
  #   {{+amount}}  - Absolute value (mixed-sign sources like escrow accounts)
  #
  # - name: Checking
  #   file: data/checking-{year}.csv
  #   format: "{{date:%Y-%m-%d}},{{description}},{{-amount}}"

output_dir: output
html_filename: spending_summary.html

# Merchant rules file - expression-based categorization
merchants_file: config/merchants.rules

# Views file (optional) - custom spending views
# Create config/views.rules and uncomment:
# views_file: config/views.rules

# Home locations (auto-detected if not specified)
# Transactions outside these locations are classified as travel
# home_locations:
#   - WA
#   - OR

# Optional: pretty names for travel destinations in reports
# travel_labels:
#   HI: Hawaii
#   GB: United Kingdom
'''

STARTER_MERCHANTS = '''# Tally Merchant Rules
#
# Expression-based rules for categorizing transactions.
# All matching rules are evaluated. Most specific sets category.
# Tags are collected from ALL matching rules.
#
# Match expressions:
#   contains("X")     - Case-insensitive substring match
#   regex("pattern")  - Regex pattern match
#   normalized("X")   - Match ignoring spaces/hyphens/punctuation
#   anyof("A", "B")   - Match any of multiple patterns
#   startswith("X")   - Match only at beginning
#   fuzzy("X")        - Approximate matching (catches typos)
#   fuzzy("X", 0.85)  - Fuzzy with custom threshold (default 0.80)
#   amount > 100      - Amount conditions
#   month == 12       - Date component (month, year, day, weekday)
#   weekday == 0      - Day of week (0=Monday, 1=Tuesday, ... 6=Sunday)
#   date >= "2025-01-01"  - Date range
#
# You can combine conditions with 'and', 'or', 'not'
#
# Run: tally inspect <file> to see your transaction descriptions.
# Run: tally discover to find unknown merchants.

# === Special Tags ===
# These tags control how transactions appear in your spending report:
#
#   income   - Deposits, salary, interest (excluded from spending)
#   transfer - Account transfers, CC payments (excluded from spending)
#   refund   - Returns and credits (shown in Credits Applied section)
#
# Example:
#   [Paycheck]
#   match: contains("DIRECT DEPOSIT") or contains("PAYROLL")
#   category: Income
#   subcategory: Salary
#   tags: income
#
#   [Credit Card Payment]
#   match: contains("PAYMENT THANK YOU")
#   category: Finance
#   subcategory: Payment
#   tags: transfer

# === Field Transforms (optional) ===
# Strip payment processor prefixes before matching:
# field.description = regex_replace(field.description, "^APLPAY\\\\s+", "")
# field.description = regex_replace(field.description, "^SQ\\\\s*\\\\*", "")

# === Variables (optional) ===
# is_large = amount > 500
# is_holiday = month >= 11 and month <= 12

# === Example Rules ===

# [Netflix]
# match: contains("NETFLIX")
# category: Subscriptions
# subcategory: Streaming
# tags: entertainment

# [Costco Grocery]
# match: contains("COSTCO") and amount <= 200
# category: Food
# subcategory: Grocery

# [Costco Bulk]
# match: contains("COSTCO") and amount > 200
# category: Shopping
# subcategory: Wholesale

# [Uber Rides]
# match: regex("UBER\\s(?!EATS)")  # Uber but not Uber Eats
# category: Transportation
# subcategory: Rideshare

# [Uber Eats]
# match: normalized("UBEREATS")  # Matches "UBER EATS", "UBER-EATS", etc.
# category: Food
# subcategory: Delivery

# [Streaming Services]
# match: anyof("NETFLIX", "HULU", "DISNEY+", "HBO")
# category: Subscriptions
# subcategory: Streaming

# === Weekday-based tagging ===
# Tag weekday vs weekend transactions differently

# [Starbucks - Workdays]
# match: contains("Starbucks") and weekday < 5  # Monday-Friday (0-4)
# category: Food
# subcategory: Coffee
# tags: work

# [Starbucks]
# match: contains("Starbucks") and weekday >= 5  # Saturday-Sunday (5-6)
# category: Food
# subcategory: Coffee

# === Add your rules below ===

'''

# Legacy format (deprecated)
STARTER_MERCHANT_CATEGORIES = '''# Merchant Categorization Rules
#
# Define your merchant categorization rules here.
# Format: Pattern,Merchant,Category,Subcategory
#
# - Pattern: Python regex (case-insensitive) matched against transaction descriptions
# - Use | for alternatives: DELTA|SOUTHWEST matches either
# - Use (?!...) for negative lookahead: UBER\\s(?!EATS) excludes Uber Eats
# - Test patterns at regex101.com (Python flavor)
#
# First match wins.
# Run: tally inspect <file> to see your transaction descriptions.
#
# Examples:
#   MY LOCAL BAKERY,My Favorite Bakery,Food,Restaurant
#   JOHNS PLUMBING,John's Plumbing,Bills,Home Repair
#   ZELLE.*JANE,Jane (Babysitter),Personal,Childcare

Pattern,Merchant,Category,Subcategory

# Add your custom rules below:

'''

STARTER_VIEWS = '''# Tally Views Configuration (.rules format)
#
# Views define groups of merchants for your spending report.
# Each merchant is evaluated against all view filters.
# Views can overlap - the same merchant can appear in multiple views.
#
# SYNTAX:
#   [View Name]
#   description: Human-readable description (optional)
#   filter: <expression>
#
# PRIMITIVES:
#   months      - count of unique months with transactions
#   total       - sum of all payments
#   cv          - coefficient of variation of monthly totals (0 = very consistent)
#   category    - category string (e.g., "Food", "Travel")
#   subcategory - subcategory string (e.g., "Grocery", "Airline")
#   merchant    - merchant name
#   tags        - set of tag strings
#   payments    - list of payment amounts
#
# FUNCTIONS:
#   sum(x), count(x), avg(x), min(x), max(x), stddev(x)
#   abs(x), round(x)
#   by(field) - group payments by: month, year, week, day
#
# GROUPING:
#   by("month")           - list of payment lists per month
#   sum(by("month"))      - list of monthly totals
#   avg(sum(by("month"))) - average monthly spend
#   max(sum(by("month"))) - highest spending month
#
# OPERATORS:
#   Comparison: ==  !=  <  <=  >  >=
#   Boolean:    and  or  not
#   Membership: "tag" in tags
#   Arithmetic: +  -  *  /  %
#
# ============================================================================
# SAMPLE VIEWS (uncomment and customize)
# ============================================================================

# [Every Month]
# description: Consistent recurring expenses (rent, utilities, subscriptions)
# filter: months >= 6 and cv < 0.3

# [Variable Recurring]
# description: Frequent but inconsistent (groceries, shopping, delivery)
# filter: months >= 6 and cv >= 0.3

# [Periodic]
# description: Quarterly or semi-annual (tuition, insurance)
# filter: months >= 2 and months <= 5

# [Travel]
# description: All travel expenses
# filter: category == "Travel"

# [Large Purchases]
# description: Big one-time expenses over $1,000
# filter: total > 1000 and months <= 3

# [Food & Dining]
# description: All food-related spending
# filter: category == "Food"

# [Subscriptions]
# description: Streaming, software, memberships
# filter: category == "Subscriptions"

# [Tagged: Business]
# description: Business expenses for reimbursement
# filter: "business" in tags

'''

_deprecated_parser_warnings = []  # Collect warnings to print at end

def _warn_deprecated_parser(source_name, parser_type, filepath):
    """Record deprecation warning for amex/boa parsers (to print at end)."""
    warning = (source_name, parser_type, filepath)
    if warning not in _deprecated_parser_warnings:
        _deprecated_parser_warnings.append(warning)

def _print_deprecation_warnings(config=None):
    """Print all collected deprecation warnings to stderr (to avoid breaking JSON output)."""
    has_warnings = False

    # Print config-based warnings (more detailed, from config_loader)
    if config and config.get('_warnings'):
        has_warnings = True
        print(file=sys.stderr)
        print(f"{C.YELLOW}{'=' * 70}{C.RESET}", file=sys.stderr)
        print(f"{C.YELLOW}DEPRECATION WARNINGS{C.RESET}", file=sys.stderr)
        print(f"{C.YELLOW}{'=' * 70}{C.RESET}", file=sys.stderr)
        for warning in config['_warnings']:
            print(file=sys.stderr)
            print(f"{C.YELLOW}⚠ {warning['message']}{C.RESET}", file=sys.stderr)
            print(f"  {warning['suggestion']}", file=sys.stderr)
            if 'example' in warning:
                print(file=sys.stderr)
                print(f"  {C.DIM}Suggested config:{C.RESET}", file=sys.stderr)
                for line in warning['example'].split('\n'):
                    print(f"  {C.GREEN}{line}{C.RESET}", file=sys.stderr)
        print(file=sys.stderr)

    # Print legacy parser warnings (if not already covered by config warnings)
    # Skip these if config warnings already exist (they're duplicates)
    if _deprecated_parser_warnings and not has_warnings:
        print(file=sys.stderr)
        for source_name, parser_type, filepath in _deprecated_parser_warnings:
            print(f"{C.YELLOW}Warning:{C.RESET} The '{parser_type}' parser is deprecated and will be removed in a future release.", file=sys.stderr)
            print(f"  Source: {source_name}", file=sys.stderr)
            print(f"  Run: {C.GREEN}tally inspect {filepath}{C.RESET} to get a format string for your CSV.", file=sys.stderr)
            print(f"  Then update settings.yaml to use 'format:' instead of 'type: {parser_type}'", file=sys.stderr)
            print(file=sys.stderr)

    _deprecated_parser_warnings.clear()


def find_config_dir():
    """Find the config directory, checking environment and both layouts.

    Resolution order:
    1. TALLY_CONFIG environment variable (if set and exists)
    2. ./config (old layout - config in current directory)
    3. ./tally/config (new layout - config in tally subdirectory)

    Note: Migration prompts are handled separately by run_migrations()
    during 'tally update', not here.

    Returns None if no config directory is found.
    """
    # Check environment variable first
    env_config = os.environ.get('TALLY_CONFIG')
    if env_config:
        env_path = os.path.abspath(env_config)
        if os.path.isdir(env_path):
            return env_path

    # Check old layout (backwards compatibility)
    # Note: Migration prompts are handled by run_migrations() during 'tally update'
    old_layout = os.path.abspath('config')
    if os.path.isdir(old_layout):
        return old_layout

    # Check new layout
    new_layout = os.path.abspath(os.path.join('tally', 'config'))
    if os.path.isdir(new_layout):
        return new_layout

    return None


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


def init_config(target_dir):
    """Initialize a new config directory with starter files."""
    import datetime

    config_dir = os.path.join(target_dir, 'config')
    data_dir = os.path.join(target_dir, 'data')
    output_dir = os.path.join(target_dir, 'output')

    # Create directories
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    current_year = datetime.datetime.now().year
    files_created = []
    files_skipped = []

    # Write settings.yaml
    settings_path = os.path.join(config_dir, 'settings.yaml')
    if not os.path.exists(settings_path):
        with open(settings_path, 'w', encoding='utf-8') as f:
            f.write(STARTER_SETTINGS.format(year=current_year))
        files_created.append('config/settings.yaml')
    else:
        files_skipped.append('config/settings.yaml')

    # Write merchants.rules (new expression-based format)
    merchants_path = os.path.join(config_dir, 'merchants.rules')
    if not os.path.exists(merchants_path):
        with open(merchants_path, 'w', encoding='utf-8') as f:
            f.write(STARTER_MERCHANTS)
        files_created.append('config/merchants.rules')
    else:
        files_skipped.append('config/merchants.rules')

    # Write views.rules
    sections_path = os.path.join(config_dir, 'views.rules')
    if not os.path.exists(sections_path):
        with open(sections_path, 'w', encoding='utf-8') as f:
            f.write(STARTER_VIEWS)
        files_created.append('config/views.rules')
    else:
        files_skipped.append('config/views.rules')

    # Create .gitignore for data privacy
    gitignore_path = os.path.join(target_dir, '.gitignore')
    if not os.path.exists(gitignore_path):
        with open(gitignore_path, 'w', encoding='utf-8') as f:
            f.write('''# Tally - Ignore sensitive data
data/
output/
''')
        files_created.append('.gitignore')

    return files_created, files_skipped


def _check_deprecated_description_cleaning(config):
    """Check for deprecated description_cleaning setting and fail with migration instructions."""
    if config.get('description_cleaning'):
        patterns = config['description_cleaning']
        print("Error: 'description_cleaning' setting has been removed.", file=sys.stderr)
        print("\nMigrate to field transforms in merchants.rules:", file=sys.stderr)
        print("", file=sys.stderr)
        for pattern in patterns[:3]:  # Show first 3 examples
            # Escape the pattern for the regex_replace function
            escaped = pattern.replace('\\', '\\\\').replace('"', '\\"')
            print(f'  field.description = regex_replace(field.description, "{escaped}", "")', file=sys.stderr)
        if len(patterns) > 3:
            print(f"  # ... and {len(patterns) - 3} more patterns", file=sys.stderr)
        print("\nAdd these lines at the top of your merchants.rules file.", file=sys.stderr)
        sys.exit(1)


def main():
    """Main entry point for tally CLI."""
    parser = argparse.ArgumentParser(
        prog='tally',
        description='A tool to help agents classify your bank transactions.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Run 'tally workflow' to see next steps based on your current state.'''
    )

    subparsers = parser.add_subparsers(dest='command', title='commands', metavar='<command>')

    # init subcommand
    init_parser = subparsers.add_parser(
        'init',
        help='Set up a new budget folder with config files (run once to get started)'
    )
    init_parser.add_argument(
        'dir',
        nargs='?',
        default='tally',
        help='Directory to initialize (default: ./tally)'
    )

    # up subcommand (primary command)
    up_parser = subparsers.add_parser(
        'up',
        help='Parse transactions, categorize them, and generate HTML spending report'
    )
    up_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    up_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    up_parser.add_argument(
        '--summary',
        action='store_true',
        help='Print summary only, do not generate HTML'
    )
    up_parser.add_argument(
        '--output', '-o',
        help='Override output file path'
    )
    up_parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Minimal output'
    )
    up_parser.add_argument(
        '--format', '-f',
        choices=['html', 'json', 'markdown', 'summary'],
        default='html',
        help='Output format: html (default), json (with reasoning), markdown, summary (text)'
    )
    up_parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='Increase output verbosity (use -v for trace, -vv for full details)'
    )
    up_parser.add_argument(
        '--only',
        help='Filter to specific classifications (comma-separated: monthly,variable,travel)'
    )
    up_parser.add_argument(
        '--category',
        help='Filter to specific category'
    )
    up_parser.add_argument(
        '--tags',
        help='Filter by tags (comma-separated, e.g., --tags business,reimbursable)'
    )
    up_parser.add_argument(
        '--no-embedded-html',
        dest='embedded_html',
        action='store_false',
        default=True,
        help='Output CSS/JS as separate files instead of embedding (easier to iterate on styling)'
    )
    up_parser.add_argument(
        '--migrate',
        action='store_true',
        help='Migrate merchant_categories.csv to new .rules format (non-interactive)'
    )
    up_parser.add_argument(
        '--group-by',
        choices=['merchant', 'subcategory'],
        default='merchant',
        help='Group output by merchant (default) or subcategory'
    )

    # run subcommand (deprecated alias for 'up' - hidden from help)
    run_parser = subparsers.add_parser('run')
    run_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    run_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    run_parser.add_argument(
        '--summary',
        action='store_true',
        help='Print summary only, do not generate HTML'
    )
    run_parser.add_argument(
        '--output', '-o',
        help='Override output file path'
    )
    run_parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Minimal output'
    )
    run_parser.add_argument(
        '--format', '-f',
        choices=['html', 'json', 'markdown', 'summary'],
        default='html',
        help='Output format: html (default), json (with reasoning), markdown, summary (text)'
    )
    run_parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='Increase output verbosity (use -v for trace, -vv for full details)'
    )
    run_parser.add_argument(
        '--only',
        help='Filter to specific classifications (comma-separated: monthly,variable,travel)'
    )
    run_parser.add_argument(
        '--category',
        help='Filter to specific category'
    )
    run_parser.add_argument(
        '--tags',
        help='Filter by tags (comma-separated, e.g., --tags business,reimbursable)'
    )
    run_parser.add_argument(
        '--no-embedded-html',
        dest='embedded_html',
        action='store_false',
        default=True,
        help='Output CSS/JS as separate files instead of embedding (easier to iterate on styling)'
    )
    run_parser.add_argument(
        '--migrate',
        action='store_true',
        help='Migrate merchant_categories.csv to new .rules format (non-interactive)'
    )
    # inspect subcommand
    inspect_parser = subparsers.add_parser(
        'inspect',
        help='Show CSV columns and sample data to help build a format string',
        description='Show headers and sample rows from a CSV file, with auto-detection suggestions.'
    )
    inspect_parser.add_argument(
        'file',
        nargs='?',
        help='Path to the CSV file to inspect'
    )
    inspect_parser.add_argument(
        '--rows', '-n',
        type=int,
        default=5,
        help='Number of sample rows to display (default: 5)'
    )

    # discover subcommand
    discover_parser = subparsers.add_parser(
        'discover',
        help='List uncategorized transactions with suggested rules (use --format json for LLMs)',
        description='Analyze transactions to find unknown merchants, sorted by spend. '
                    'Outputs suggested rules for your .rules file.'
    )
    discover_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    discover_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    discover_parser.add_argument(
        '--limit', '-n',
        type=int,
        default=20,
        help='Maximum number of unknown merchants to show (default: 20, 0 for all)'
    )
    discover_parser.add_argument(
        '--format', '-f',
        choices=['text', 'csv', 'json'],
        default='text',
        help='Output format: text (human readable), csv (for import), json (for agents)'
    )

    # diag subcommand
    diag_parser = subparsers.add_parser(
        'diag',
        help='Debug config issues: show loaded rules, data sources, and errors',
        description='Display detailed diagnostic info to help troubleshoot rule loading issues.'
    )
    diag_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    diag_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    diag_parser.add_argument(
        '--format', '-f',
        choices=['text', 'json'],
        default='text',
        help='Output format: text (human readable), json (for agents)'
    )

    # explain subcommand
    explain_parser = subparsers.add_parser(
        'explain',
        help='Explain why merchants are classified the way they are',
        description='Show classification reasoning for merchants or transaction descriptions. '
                    'Pass a merchant name to see its classification, or a raw transaction description '
                    'to see which rule matches. Use --amount to test amount-based rules.'
    )
    explain_parser.add_argument(
        'merchant',
        nargs='*',
        help='Merchant name or raw transaction description to explain (shows summary if omitted)'
    )
    explain_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    explain_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    explain_parser.add_argument(
        '--format', '-f',
        choices=['text', 'json', 'markdown'],
        default='text',
        help='Output format: text (default), json, markdown'
    )
    explain_parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='Increase output verbosity (use -v for trace, -vv for full details)'
    )
    explain_parser.add_argument(
        '--view',
        help='Show all merchants in a specific view (e.g., --view bills)'
    )
    explain_parser.add_argument(
        '--category',
        help='Filter to specific category (e.g., --category Food)'
    )
    explain_parser.add_argument(
        '--tags',
        help='Filter by tags (comma-separated, e.g., --tags business,reimbursable)'
    )
    explain_parser.add_argument(
        '--month',
        help='Filter to specific month (e.g., --month 2024-12 or --month Dec)'
    )
    explain_parser.add_argument(
        '--location',
        help='Filter by transaction location (e.g., --location "New York")'
    )
    explain_parser.add_argument(
        '--amount', '-a',
        type=float,
        help='Transaction amount for testing amount-based rules (e.g., --amount 150.00)'
    )

    # workflow subcommand
    subparsers.add_parser(
        'workflow',
        help='Show context-aware workflow instructions for AI agents',
        description='Detects current state and shows relevant next steps.'
    )

    # reference subcommand
    reference_parser = subparsers.add_parser(
        'reference',
        help='Show complete rule syntax reference for merchants.rules and views.rules',
        description='Display comprehensive documentation for the rule engine syntax.'
    )
    reference_parser.add_argument(
        'topic',
        nargs='?',
        choices=['merchants', 'views'],
        help='Specific topic to show (default: show all)'
    )

    # version subcommand
    subparsers.add_parser(
        'version',
        help='Show version information',
        description='Display tally version and build information.'
    )

    # update subcommand
    update_parser = subparsers.add_parser(
        'update',
        help='Update tally to the latest version',
        description='Download and install the latest tally release.'
    )
    update_parser.add_argument(
        '--check',
        action='store_true',
        help='Check for updates without installing'
    )
    update_parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Skip confirmation prompts'
    )
    update_parser.add_argument(
        '--prerelease',
        action='store_true',
        help='Install latest development build from main branch'
    )

    args = parser.parse_args()

    # If no command specified, show help with banner
    if args.command is None:
        print(BANNER)
        parser.print_help()

        # Check for updates
        update_info = check_for_updates()
        if update_info and update_info.get('update_available'):
            print()
            if update_info.get('is_prerelease'):
                print(f"Dev build available: v{update_info['latest_version']} (current: v{update_info['current_version']})")
                print(f"  Run 'tally update --prerelease' to install")
            else:
                print(f"Update available: v{update_info['latest_version']} (current: v{update_info['current_version']})")
                print(f"  Run 'tally update' to install")

        sys.exit(0)

    # Dispatch to command handler
    # Commands are imported from .commands submodules to reduce file size
    if args.command == 'init':
        from .commands import cmd_init
        cmd_init(args)
    elif args.command == 'up':
        from .commands import cmd_run
        cmd_run(args)
    elif args.command == 'run':
        # Deprecated alias for 'up'
        print(f"{C.YELLOW}Note:{C.RESET} 'tally run' is deprecated. Use 'tally up' instead.", file=sys.stderr)
        from .commands import cmd_run
        cmd_run(args)
    elif args.command == 'inspect':
        from .commands import cmd_inspect
        cmd_inspect(args)
    elif args.command == 'discover':
        from .commands import cmd_discover
        cmd_discover(args)
    elif args.command == 'diag':
        from .commands import cmd_diag
        cmd_diag(args)
    elif args.command == 'explain':
        from .commands import cmd_explain
        cmd_explain(args)
    elif args.command == 'workflow':
        from .commands import cmd_workflow
        cmd_workflow(args)
    elif args.command == 'reference':
        from .commands import cmd_reference
        cmd_reference(args)
    elif args.command == 'version':
        sha_display = GIT_SHA[:8] if GIT_SHA != 'unknown' else 'unknown'
        print(f"tally {VERSION} ({sha_display})")
        print(REPO_URL)

        # Check for updates
        update_info = check_for_updates()
        if update_info and update_info.get('update_available'):
            print()
            if update_info.get('is_prerelease'):
                print(f"Dev build available: v{update_info['latest_version']}")
                print(f"  Run 'tally update --prerelease' to install")
            else:
                print(f"Update available: v{update_info['latest_version']}")
                print(f"  Run 'tally update' to install")
    elif args.command == 'update':
        from .commands import cmd_update
        cmd_update(args)


if __name__ == '__main__':
    main()
