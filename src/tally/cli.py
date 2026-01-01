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
            self.UNDERLINE = '\033[4m'
        else:
            self.RESET = ''
            self.BOLD = ''
            self.DIM = ''
            self.GREEN = ''
            self.CYAN = ''
            self.BLUE = ''
            self.YELLOW = ''
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
    write_summary_file,
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
4. Run: tally run ./my-budget/config

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
# First match wins (file order).
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
    """Print all collected deprecation warnings."""
    has_warnings = False

    # Print config-based warnings (more detailed, from config_loader)
    if config and config.get('_warnings'):
        has_warnings = True
        print()
        print(f"{C.YELLOW}{'=' * 70}{C.RESET}")
        print(f"{C.YELLOW}DEPRECATION WARNINGS{C.RESET}")
        print(f"{C.YELLOW}{'=' * 70}{C.RESET}")
        for warning in config['_warnings']:
            print()
            print(f"{C.YELLOW}⚠ {warning['message']}{C.RESET}")
            print(f"  {warning['suggestion']}")
            if 'example' in warning:
                print()
                print(f"  {C.DIM}Suggested config:{C.RESET}")
                for line in warning['example'].split('\n'):
                    print(f"  {C.GREEN}{line}{C.RESET}")
        print()

    # Print legacy parser warnings (if not already covered by config warnings)
    # Skip these if config warnings already exist (they're duplicates)
    if _deprecated_parser_warnings and not has_warnings:
        print()
        for source_name, parser_type, filepath in _deprecated_parser_warnings:
            print(f"{C.YELLOW}Warning:{C.RESET} The '{parser_type}' parser is deprecated and will be removed in a future release.")
            print(f"  Source: {source_name}")
            print(f"  Run: {C.GREEN}tally inspect {filepath}{C.RESET} to get a format string for your CSV.")
            print(f"  Then update settings.yaml to use 'format:' instead of 'type: {parser_type}'")
            print()

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


def cmd_init(args):
    """Handle the 'init' subcommand."""
    import shutil

    # Check if we're already in a tally directory (has config/)
    # If user didn't explicitly specify a directory, use current dir instead of ./tally/
    if args.dir == 'tally' and os.path.isdir('./config'):
        target_dir = os.path.abspath('.')
        print(f"{C.CYAN}Found existing config/ directory{C.RESET}")
        print(f"  Upgrading current directory in place (won't create nested tally/)")
        print()
    else:
        target_dir = os.path.abspath(args.dir)

    # Use relative paths for display
    rel_target = os.path.relpath(target_dir)
    if rel_target == '.':
        rel_target = './'

    print(f"Initializing budget directory: {C.BOLD}{rel_target}{C.RESET}")
    print()

    # Check for old merchant_categories.csv BEFORE init_config creates new files
    config_dir = os.path.join(target_dir, 'config')
    old_csv = os.path.join(config_dir, 'merchant_categories.csv')
    new_rules = os.path.join(config_dir, 'merchants.rules')

    if os.path.exists(old_csv) and not os.path.exists(new_rules):
        # Check if CSV has actual rules (not just header/comments)
        has_rules = False
        try:
            with open(old_csv, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and not line.startswith('Pattern,'):
                        has_rules = True
                        break
        except Exception:
            pass

        if has_rules:
            print()
            print(f"{C.CYAN}Upgrading merchant rules to new format...{C.RESET}")
            print(f"  Found: config/merchant_categories.csv (legacy CSV format)")
            print()
            _migrate_csv_to_rules(old_csv, config_dir, backup=True)
            print()

    created, skipped = init_config(target_dir)

    # Update settings.yaml to add views_file if missing
    settings_path = os.path.join(config_dir, 'settings.yaml')
    views_rules = os.path.join(config_dir, 'views.rules')
    if os.path.exists(settings_path) and os.path.exists(views_rules):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'views_file:' not in content:
                with open(settings_path, 'a', encoding='utf-8') as f:
                    f.write('\n# Views file (custom spending views)\n')
                    f.write('views_file: config/views.rules\n')
                print(f"  {C.GREEN}✓{C.RESET} config/settings.yaml (added views_file)")
        except Exception:
            pass

    # Show each file with its status and description
    all_files = [(f, True) for f in created] + [(f, False) for f in skipped]
    # Sort by filename for consistent ordering
    all_files.sort(key=lambda x: x[0])

    # Brief descriptions for key files
    file_descriptions = {
        'config/merchants.rules': 'match transactions to categories',
        'config/views.rules': 'organize report by spending patterns',
        'config/settings.yaml': 'configure data sources',
    }

    for f, was_created in all_files:
        desc = file_descriptions.get(f, '')
        desc_str = f" {C.DIM}— {desc}{C.RESET}" if desc else ""
        if was_created:
            print(f"  {C.GREEN}✓{C.RESET} {f}{desc_str}")
        else:
            print(f"  {C.YELLOW}→{C.RESET} {C.DIM}{f} (exists){C.RESET}")

    # Check if data sources are configured in settings.yaml
    import yaml
    has_data_sources = False
    settings_path = os.path.join(target_dir, 'config', 'settings.yaml')
    # Use native path separators, with ./ prefix on Unix only
    rel_settings = os.path.relpath(settings_path)
    rel_data = os.path.relpath(os.path.join(target_dir, 'data')) + os.sep
    if os.sep == '/':
        rel_settings = './' + rel_settings
        rel_data = './' + rel_data
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                settings = yaml.safe_load(f) or {}
                has_data_sources = bool(settings.get('data_sources'))
        except Exception:
            pass

    # Show next steps with agent detection
    print()

    # Helper for clickable links (OSC 8 hyperlinks, with fallback)
    def link(url, text=None):
        text = text or url
        if _supports_color():
            return f"\033]8;;{url}\033\\{C.UNDERLINE}{C.BLUE}{text}{C.RESET}\033]8;;\033\\"
        return url

    # Check which agents are installed
    agents = [
        ('claude', 'Claude Code', 'https://claude.com/product/claude-code'),
        ('copilot', 'GitHub Copilot', 'https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli'),
        ('opencode', 'OpenCode', 'https://opencode.ai'),
        ('codex', 'OpenAI Codex', 'https://developers.openai.com/codex/cli'),
    ]

    agent_lines = []
    for cmd, name, url in agents:
        installed = shutil.which(cmd) is not None
        if installed:
            status = f"{C.GREEN}✓ installed{C.RESET}"
        else:
            status = link(url)
        agent_lines.append(f"     {C.CYAN}{cmd:<11}{C.RESET} {name:<16} {status}")

    agents_block = '\n'.join(agent_lines)

    print(f"""{C.BOLD}Next steps:{C.RESET}

  {C.BOLD}1.{C.RESET} Drop your bank/credit card exports into {C.CYAN}{rel_data}{C.RESET}

  {C.BOLD}2.{C.RESET} Open this folder in an AI coding agent:
{agents_block}
     {C.DIM}Or any agent that can run command-line tools.{C.RESET}

  {C.BOLD}3.{C.RESET} Tell the agent what to do:
     {C.DIM}• "Use tally to configure my Chase credit card CSV"{C.RESET}
     {C.DIM}• "Use tally to categorize all my transactions"{C.RESET}
     {C.DIM}• "Use tally to generate my spending report"{C.RESET}

{C.DIM}The agent can run {C.RESET}{C.GREEN}tally workflow{C.RESET}{C.DIM} at any time to see the next steps.{C.RESET}
""")


def cmd_run(args):
    """Handle the 'run' subcommand."""
    # Determine config directory
    if args.config:
        config_dir = os.path.abspath(args.config)
    else:
        # Auto-detect config directory (supports both old and new layouts)
        config_dir = find_config_dir()

    if not config_dir or not os.path.isdir(config_dir):
        print(f"Error: Config directory not found.", file=sys.stderr)
        print(f"Looked for: ./config and ./tally/config", file=sys.stderr)
        print(f"\nRun 'tally init' to create a new budget directory.", file=sys.stderr)
        sys.exit(1)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Check for deprecated settings
    _check_deprecated_description_cleaning(config)

    year = config.get('year', 2025)
    home_locations = config.get('home_locations', set())
    travel_labels = config.get('travel_labels', {})
    data_sources = config.get('data_sources', [])
    transforms = get_transforms(config.get('_merchants_file'))

    # Check for data sources early before printing anything
    if not data_sources:
        print("Error: No data sources configured", file=sys.stderr)
        print(f"\nEdit {config_dir}/{args.settings} to add your data sources.", file=sys.stderr)
        print(f"\nExample:", file=sys.stderr)
        print(f"  data_sources:", file=sys.stderr)
        print(f"    - name: AMEX", file=sys.stderr)
        print(f"      file: data/amex.csv", file=sys.stderr)
        print(f"      type: amex", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"Tally - {year}")
        print(f"Config: {config_dir}/{args.settings}")
        print()

    # Load merchant rules (with migration check for CSV -> .rules)
    rules = _check_merchant_migration(config, config_dir, args.quiet, getattr(args, 'migrate', False))

    # Parse transactions from configured data sources
    all_txns = []

    for source in data_sources:
        filepath = os.path.join(config_dir, '..', source['file'])
        filepath = os.path.normpath(filepath)

        if not os.path.exists(filepath):
            # Try relative to config_dir parent
            filepath = os.path.join(os.path.dirname(config_dir), source['file'])

        if not os.path.exists(filepath):
            if not args.quiet:
                print(f"  {source['name']}: File not found - {source['file']}")
            continue

        # Get parser type and format spec (set by config_loader.resolve_source_format)
        parser_type = source.get('_parser_type', source.get('type', '')).lower()
        format_spec = source.get('_format_spec')

        try:
            if parser_type == 'amex':
                _warn_deprecated_parser(source.get('name', 'AMEX'), 'amex', source['file'])
                txns = parse_amex(filepath, rules, home_locations)
            elif parser_type == 'boa':
                _warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
                txns = parse_boa(filepath, rules, home_locations)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'),
                                         transforms=transforms)
            else:
                if not args.quiet:
                    print(f"  {source['name']}: Unknown parser type '{parser_type}'")
                    print(f"    Use 'tally inspect {source['file']}' to determine format")
                continue
        except Exception as e:
            if not args.quiet:
                print(f"  {source['name']}: Error parsing - {e}")
            continue

        all_txns.extend(txns)
        if not args.quiet:
            print(f"  {source['name']}: {len(txns)} transactions")

    if not all_txns:
        print("Error: No transactions found", file=sys.stderr)
        sys.exit(1)

    # Auto-detect home location if not specified
    if not home_locations:
        from collections import Counter
        # US state codes for filtering
        us_states = {
            'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
            'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
            'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
            'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
            'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
            'DC', 'PR', 'VI', 'GU'
        }
        # Count US state locations
        location_counts = Counter(
            txn['location'] for txn in all_txns
            if txn.get('location') and txn['location'] in us_states
        )
        if location_counts:
            # Most common location is likely home
            detected_home = location_counts.most_common(1)[0][0]
            home_locations = {detected_home}
            if not args.quiet:
                print(f"Auto-detected home location: {detected_home}")
            # Update is_travel on transactions now that we know home
            from .analyzer import is_travel_location
            for txn in all_txns:
                txn['is_travel'] = is_travel_location(txn.get('location'), home_locations)

    if not args.quiet:
        print(f"\nTotal: {len(all_txns)} transactions")
        if home_locations:
            print(f"Home locations: {', '.join(sorted(home_locations))}")

    # Analyze
    stats = analyze_transactions(all_txns)

    # Classify by user-defined views
    views_config = config.get('sections')
    if views_config:
        from .analyzer import classify_by_sections, compute_section_totals
        view_results = classify_by_sections(
            stats['by_merchant'],
            views_config,
            stats['num_months']
        )
        # Compute totals for each view
        stats['sections'] = {
            name: compute_section_totals(merchants)
            for name, merchants in view_results.items()
        }
        stats['_sections_config'] = views_config

    # Parse filter options
    only_filter = None
    if args.only:
        # Get valid view names from views config
        valid_views = set()
        if views_config:
            valid_views = {s.name.lower() for s in views_config.sections}
        only_filter = [c.strip().lower() for c in args.only.split(',')]
        invalid = [c for c in only_filter if c not in valid_views]
        if invalid:
            print(f"Warning: Invalid view(s) ignored: {', '.join(invalid)}", file=sys.stderr)
            if valid_views:
                print(f"  Valid views: {', '.join(sorted(valid_views))}", file=sys.stderr)
            only_filter = [c for c in only_filter if c in valid_views]
            if not only_filter:
                only_filter = None
    category_filter = args.category if hasattr(args, 'category') and args.category else None

    # Handle output format
    output_format = args.format if hasattr(args, 'format') else 'html'
    verbose = args.verbose if hasattr(args, 'verbose') else 0

    currency_format = config.get('currency_format', '${amount}')

    if output_format == 'json':
        # JSON output with reasoning
        from .analyzer import export_json
        print(export_json(stats, verbose=verbose, only=only_filter, category_filter=category_filter))
    elif output_format == 'markdown':
        # Markdown output with reasoning
        from .analyzer import export_markdown
        print(export_markdown(stats, verbose=verbose, only=only_filter, category_filter=category_filter))
    elif output_format == 'summary' or args.summary:
        # Text summary only (no HTML)
        if stats.get('sections'):
            print_sections_summary(stats, year=year, currency_format=currency_format, only_filter=only_filter)
        else:
            print("No views configured. Add 'views_file' to settings.yaml for custom views.", file=sys.stderr)
    else:
        # HTML output (default)
        # Print summary first
        if not args.quiet:
            if stats.get('sections'):
                print_sections_summary(stats, year=year, currency_format=currency_format, only_filter=only_filter)
            else:
                print("No views configured. Add 'views_file' to settings.yaml for custom views.", file=sys.stderr)

        # Determine output path
        if args.output:
            output_path = args.output
        else:
            output_dir = os.path.join(os.path.dirname(config_dir), config.get('output_dir', 'output'))
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, config.get('html_filename', 'spending_summary.html'))

        # Collect source names for the report subtitle
        source_names = [s.get('name', 'Unknown') for s in data_sources]
        write_summary_file_vue(stats, output_path, year=year, home_locations=home_locations,
                               currency_format=currency_format, sources=source_names,
                               embedded_html=args.embedded_html)
        if not args.quiet:
            # Make the path clickable using OSC 8 hyperlink escape sequence
            abs_path = os.path.abspath(output_path)
            file_url = f"file://{abs_path}"
            # OSC 8 format: \033]8;;URL\033\\text\033]8;;\033\\
            clickable_path = f"\033]8;;{file_url}\033\\{output_path}\033]8;;\033\\"
            print(f"\nHTML report: {clickable_path}")

    _print_deprecation_warnings(config)


def cmd_discover(args):
    """Handle the 'discover' subcommand - find unknown merchants for rule creation."""
    from collections import Counter, defaultdict
    import re

    # Determine config directory
    if args.config:
        config_dir = os.path.abspath(args.config)
    else:
        config_dir = find_config_dir()

    if not config_dir or not os.path.isdir(config_dir):
        print(f"Error: Config directory not found.", file=sys.stderr)
        print(f"Looked for: ./config and ./tally/config", file=sys.stderr)
        print(f"\nRun 'tally init' to create a new budget directory.", file=sys.stderr)
        sys.exit(1)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Check for deprecated settings
    _check_deprecated_description_cleaning(config)

    home_locations = config.get('home_locations', set())
    data_sources = config.get('data_sources', [])
    transforms = get_transforms(config.get('_merchants_file'))

    if not data_sources:
        print("Error: No data sources configured", file=sys.stderr)
        print(f"\nEdit {config_dir}/{args.settings} to add your data sources.", file=sys.stderr)
        print(f"\nExample:", file=sys.stderr)
        print(f"  data_sources:", file=sys.stderr)
        print(f"    - name: AMEX", file=sys.stderr)
        print(f"      file: data/amex.csv", file=sys.stderr)
        print(f"      type: amex", file=sys.stderr)
        sys.exit(1)

    # Load merchant rules
    merchants_file = config.get('_merchants_file')
    if merchants_file and os.path.exists(merchants_file):
        rules = get_all_rules(merchants_file)
    else:
        rules = get_all_rules()

    # Parse transactions from configured data sources
    all_txns = []

    for source in data_sources:
        filepath = os.path.join(config_dir, '..', source['file'])
        filepath = os.path.normpath(filepath)

        if not os.path.exists(filepath):
            filepath = os.path.join(os.path.dirname(config_dir), source['file'])

        if not os.path.exists(filepath):
            continue

        parser_type = source.get('_parser_type', source.get('type', '')).lower()
        format_spec = source.get('_format_spec')

        try:
            if parser_type == 'amex':
                _warn_deprecated_parser(source.get('name', 'AMEX'), 'amex', source['file'])
                txns = parse_amex(filepath, rules, home_locations)
            elif parser_type == 'boa':
                _warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
                txns = parse_boa(filepath, rules, home_locations)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'),
                                         transforms=transforms)
            else:
                continue
        except Exception:
            continue

        all_txns.extend(txns)

    if not all_txns:
        print("Error: No transactions found", file=sys.stderr)
        sys.exit(1)

    # Find unknown transactions
    unknown_txns = [t for t in all_txns if t.get('category') == 'Unknown']

    if not unknown_txns:
        print("No unknown transactions found! All merchants are categorized.")
        sys.exit(0)

    # Group by raw description and calculate stats
    desc_stats = defaultdict(lambda: {'count': 0, 'total': 0.0, 'examples': [], 'has_negative': False})

    for txn in unknown_txns:
        raw = txn.get('raw_description', txn.get('description', ''))
        raw_amount = txn.get('amount', 0)
        amount = abs(raw_amount)
        desc_stats[raw]['count'] += 1
        desc_stats[raw]['total'] += amount
        if raw_amount < 0:
            desc_stats[raw]['has_negative'] = True
        if len(desc_stats[raw]['examples']) < 3:
            desc_stats[raw]['examples'].append(txn)

    # Sort by total spend (descending)
    sorted_descs = sorted(desc_stats.items(), key=lambda x: x[1]['total'], reverse=True)

    # Limit output
    limit = args.limit
    if limit > 0:
        sorted_descs = sorted_descs[:limit]

    # Output format
    if args.format == 'csv':
        # Legacy CSV output (deprecated)
        print("# NOTE: CSV format is deprecated. Use .rules format instead.")
        print("# See 'tally workflow' for the new format.")
        print("#")
        print("# Suggested rules for unknown merchants")
        print("Pattern,Merchant,Category,Subcategory")
        print()

        for raw_desc, stats in sorted_descs:
            pattern = suggest_pattern(raw_desc)
            merchant = suggest_merchant_name(raw_desc)
            print(f"{pattern},{merchant},CATEGORY,SUBCATEGORY  # ${stats['total']:.2f} ({stats['count']} txns)")

    elif args.format == 'json':
        import json
        output = []
        for raw_desc, stats in sorted_descs:
            pattern = suggest_pattern(raw_desc)
            merchant = suggest_merchant_name(raw_desc)
            # Add refund tag suggestion for negative amounts
            suggested_tags = ['refund'] if stats['has_negative'] else []
            output.append({
                'raw_description': raw_desc,
                'suggested_merchant': merchant,
                'suggested_rule': suggest_merchants_rule(merchant, pattern, tags=suggested_tags),
                'suggested_tags': suggested_tags,
                'has_negative': stats['has_negative'],
                'count': stats['count'],
                'total_spend': round(stats['total'], 2),
                'examples': [
                    {
                        'date': str(t.get('date', '')),
                        'amount': t.get('amount', 0),
                        'description': t.get('description', '')
                    }
                    for t in stats['examples']
                ]
            })
        print(json.dumps(output, indent=2))

    else:
        # Default: human-readable format
        print(f"UNKNOWN MERCHANTS - Top {len(sorted_descs)} by spend")
        print("=" * 80)
        print(f"Total unknown: {len(unknown_txns)} transactions, ${sum(s['total'] for _, s in desc_stats.items()):.2f}")
        print()

        for i, (raw_desc, stats) in enumerate(sorted_descs, 1):
            pattern = suggest_pattern(raw_desc)
            merchant = suggest_merchant_name(raw_desc)

            print(f"{i}. {raw_desc[:60]}")
            status = f"Count: {stats['count']} | Total: ${stats['total']:.2f}"
            if stats['has_negative']:
                status += f" {C.YELLOW}(has refunds/credits){C.RESET}"
            print(f"   {status}")
            print(f"   Suggested merchant: {merchant}")
            print()
            print(f"   {C.DIM}[{merchant}]")
            print(f"   match: contains(\"{pattern}\")")
            print(f"   category: CATEGORY")
            print(f"   subcategory: SUBCATEGORY")
            if stats['has_negative']:
                print(f"   {C.CYAN}tags: refund{C.RESET}")
            print(f"{C.RESET}")
            print()

    _print_deprecation_warnings(config)


def suggest_pattern(description):
    """Generate a suggested regex pattern from a raw description."""
    import re

    desc = description.upper()

    # Remove common suffixes that vary
    desc = re.sub(r'\s+\d{4,}.*$', '', desc)  # Remove trailing numbers (store IDs)
    desc = re.sub(r'\s+[A-Z]{2}$', '', desc)  # Remove trailing state codes
    desc = re.sub(r'\s+\d{5}$', '', desc)  # Remove zip codes
    desc = re.sub(r'\s+#\d+', '', desc)  # Remove store numbers like #1234

    # Remove common prefixes
    prefixes = ['APLPAY ', 'SQ *', 'TST*', 'SP ', 'PP*', 'GOOGLE *']
    for prefix in prefixes:
        if desc.startswith(prefix):
            desc = desc[len(prefix):]

    # Clean up
    desc = desc.strip()

    # Escape regex special characters but keep it readable
    # Only escape characters that are common in descriptions
    pattern = re.sub(r'([.*+?^${}()|[\]\\])', r'\\\1', desc)

    # Simplify: take first 2-3 significant words
    words = pattern.split()[:3]
    if words:
        pattern = r'\s*'.join(words)

    return pattern


def suggest_merchant_name(description):
    """Generate a clean merchant name from a raw description."""
    import re

    desc = description

    # Remove common prefixes
    prefixes = ['APLPAY ', 'SQ *', 'TST*', 'TST* ', 'SP ', 'PP*', 'GOOGLE *']
    for prefix in prefixes:
        if desc.upper().startswith(prefix.upper()):
            desc = desc[len(prefix):]

    # Remove trailing IDs, numbers, locations
    desc = re.sub(r'\s+\d{4,}.*$', '', desc)
    desc = re.sub(r'\s+[A-Z]{2}$', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+\d{5}$', '', desc)
    desc = re.sub(r'\s+#\d+', '', desc)
    desc = re.sub(r'\s+DES:.*$', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+ID:.*$', '', desc, flags=re.IGNORECASE)

    # Take first few words and title case
    words = desc.split()[:3]
    if words:
        return ' '.join(words).title()

    return 'Unknown'


def suggest_merchants_rule(merchant_name, pattern, tags=None):
    """Generate a suggested rule block in .rules format."""
    # Escape quotes in pattern if needed
    escaped_pattern = pattern.replace('"', '\\"')
    rule = f"""[{merchant_name}]
match: contains("{escaped_pattern}")
category: CATEGORY
subcategory: SUBCATEGORY"""
    if tags:
        rule += f"\ntags: {', '.join(tags)}"
    return rule


def _detect_file_format(filepath):
    """Detect if file is CSV, fixed-width text, or other format.

    Returns dict with:
        - format_type: 'csv', 'fixed_width', 'unknown'
        - delimiter: detected delimiter for CSV
        - has_header: whether file has headers
        - issues: list of potential issues detected
        - suggestions: list of suggestions
    """
    import csv
    import re

    result = {
        'format_type': 'unknown',
        'delimiter': ',',
        'has_header': True,
        'issues': [],
        'suggestions': [],
        'sample_lines': []
    }

    with open(filepath, 'r', encoding='utf-8') as f:
        sample = f.read(8192)
        lines = sample.split('\n')[:20]
        result['sample_lines'] = lines

    # Check for fixed-width format indicators
    fixed_width_indicators = 0

    # Check if lines have consistent length (fixed-width)
    line_lengths = [len(l) for l in lines if l.strip() and not l.startswith('#')]
    if line_lengths:
        avg_len = sum(line_lengths) / len(line_lengths)
        if avg_len > 80 and max(line_lengths) - min(line_lengths) < 20:
            fixed_width_indicators += 1

    # Check for date pattern at start of lines (bank statement format)
    date_pattern = re.compile(r'^\d{2}/\d{2}/\d{4}\s{2,}')
    date_matches = sum(1 for l in lines if date_pattern.match(l))
    if date_matches >= 3:
        fixed_width_indicators += 2

    # Check for amounts with thousands separators at end of lines
    amount_at_end = re.compile(r'\s+-?[\d,]+\.\d{2}\s*$')
    amount_matches = sum(1 for l in lines if amount_at_end.search(l))
    if amount_matches >= 3:
        fixed_width_indicators += 1

    # Check if commas appear in what looks like amounts (thousands separators)
    # This would break CSV parsing
    thousands_pattern = re.compile(r'\d{1,3},\d{3}')
    has_thousands_separators = any(thousands_pattern.search(l) for l in lines)

    # Try CSV sniffing
    csv_dialect = None
    csv_header = True
    try:
        csv_dialect = csv.Sniffer().sniff(sample)
        csv_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        pass

    # Make determination
    if fixed_width_indicators >= 3:
        result['format_type'] = 'fixed_width'
        result['issues'].append("File appears to be fixed-width format (like Bank of America statements)")
        result['suggestions'].append("Use 'delimiter: regex' with a pattern, or convert to CSV")

        # Try to detect the fixed-width pattern
        # BOA format: MM/DD/YYYY  Description...  Amount  Balance
        if date_matches >= 3:
            result['suggestions'].append(
                'Suggested config:\n'
                '  delimiter: "regex:^(\\d{2}/\\d{2}/\\d{4})\\s+(.+?)\\s+([-\\d,]+\\.\\d{2})\\s+([-\\d,]+\\.\\d{2})$"\n'
                '  format: "{date:%m/%d/%Y}, {description}, {-amount}, {_}"\n'
                '  has_header: false'
            )
    elif csv_dialect:
        result['format_type'] = 'csv'
        result['delimiter'] = csv_dialect.delimiter
        result['has_header'] = csv_header

        if has_thousands_separators and csv_dialect.delimiter == ',':
            result['issues'].append("Warning: File contains comma thousands separators which may conflict with CSV delimiter")
            result['suggestions'].append("Ensure amount columns are quoted, or export with different delimiter")
    else:
        result['format_type'] = 'unknown'
        result['issues'].append("Could not determine file format")

    return result


def _analyze_amount_patterns(filepath, amount_col, has_header=True, delimiter=None, max_rows=1000):
    """
    Analyze amount column patterns to help users understand their data's sign convention.

    Returns dict with:
        - positive_count: number of positive amounts
        - negative_count: number of negative amounts
        - positive_total: sum of positive amounts
        - negative_total: sum of negative amounts (as positive number)
        - sign_convention: 'expenses_positive' or 'expenses_negative'
        - suggest_negate: True if user should use {-amount} to normalize
        - sample_credits: list of (description, amount) for likely transfers/income
    """
    import csv
    import re as re_mod

    positive_count = 0
    negative_count = 0
    positive_total = 0.0
    negative_total = 0.0
    sample_credits = []  # (description, amount) tuples

    def parse_amount(val):
        """Parse amount string to float, handling currency symbols and parentheses."""
        if not val:
            return None
        val = val.strip()
        # Remove currency symbols, commas
        val = re_mod.sub(r'[$€£¥,]', '', val)
        # Handle parentheses as negative
        if val.startswith('(') and val.endswith(')'):
            val = '-' + val[1:-1]
        try:
            return float(val)
        except ValueError:
            return None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            if delimiter and delimiter.startswith('regex:'):
                # Regex-based parsing
                pattern = re_mod.compile(delimiter[6:])
                for i, line in enumerate(f):
                    if has_header and i == 0:
                        continue
                    if i >= max_rows:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    match = pattern.match(line)
                    if match:
                        groups = match.groups()
                        if amount_col < len(groups):
                            amount = parse_amount(groups[amount_col])
                            if amount is not None:
                                desc = groups[1] if len(groups) > 1 else ''
                                if amount >= 0:
                                    positive_count += 1
                                    positive_total += amount
                                else:
                                    negative_count += 1
                                    negative_total += abs(amount)
                                    if len(sample_credits) < 10:
                                        sample_credits.append((desc.strip(), amount))
            else:
                # Standard CSV
                reader = csv.reader(f)
                if has_header:
                    headers = next(reader, None)
                    desc_col = 1  # default
                    for idx, h in enumerate(headers or []):
                        hl = h.lower()
                        if 'desc' in hl or 'merchant' in hl or 'payee' in hl or 'name' in hl:
                            desc_col = idx
                            break
                else:
                    desc_col = 1

                for i, row in enumerate(reader):
                    if i >= max_rows:
                        break
                    if amount_col < len(row):
                        amount = parse_amount(row[amount_col])
                        if amount is not None:
                            desc = row[desc_col] if desc_col < len(row) else ''
                            if amount >= 0:
                                positive_count += 1
                                positive_total += amount
                            else:
                                negative_count += 1
                                negative_total += abs(amount)
                                if len(sample_credits) < 10:
                                    sample_credits.append((desc.strip(), amount))
    except Exception:
        return None

    total_count = positive_count + negative_count
    if total_count == 0:
        return None

    # Determine sign convention based on distribution
    # Expenses positive: mostly positive amounts (typical credit card export)
    # Expenses negative: mostly negative amounts (typical bank export)
    positive_pct = positive_count / total_count * 100

    if positive_pct > 70:
        sign_convention = 'expenses_positive'
        suggest_negate = False
        rationale = "mostly positive amounts (expenses are positive)"
    elif positive_pct < 30:
        sign_convention = 'expenses_negative'
        suggest_negate = True
        rationale = "mostly negative amounts (expenses are negative)"
    else:
        # Mixed - harder to tell
        if positive_total > negative_total:
            sign_convention = 'expenses_positive'
            suggest_negate = False
            rationale = "total positive exceeds negative"
        else:
            sign_convention = 'expenses_negative'
            suggest_negate = True
            rationale = "total negative exceeds positive"

    return {
        'positive_count': positive_count,
        'negative_count': negative_count,
        'positive_total': positive_total,
        'negative_total': negative_total,
        'positive_pct': positive_pct,
        'sign_convention': sign_convention,
        'suggest_negate': suggest_negate,
        'rationale': rationale,
        'sample_credits': sample_credits,
    }


def cmd_inspect(args):
    """Handle the 'inspect' subcommand - show CSV structure and sample rows."""
    import csv

    if not args.file:
        print("Error: No file specified", file=sys.stderr)
        print("\nUsage: tally inspect <file.csv>", file=sys.stderr)
        print("\nExample:", file=sys.stderr)
        print("  tally inspect data/transactions.csv", file=sys.stderr)
        sys.exit(1)

    filepath = os.path.abspath(args.file)

    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    num_rows = args.rows

    print(f"Inspecting: {filepath}")
    print("=" * 70)

    # First, detect file format
    format_info = _detect_file_format(filepath)

    print(f"\nFile Format Detection:")
    print("-" * 70)
    print(f"  Detected type: {format_info['format_type']}")

    if format_info['issues']:
        print(f"\n  Issues:")
        for issue in format_info['issues']:
            print(f"    ⚠ {issue}")

    if format_info['format_type'] == 'fixed_width':
        print(f"\n  Sample lines:")
        for i, line in enumerate(format_info['sample_lines'][:5]):
            if line.strip():
                print(f"    {i}: {line[:80]}{'...' if len(line) > 80 else ''}")

        if format_info['suggestions']:
            print(f"\n  Suggestions:")
            for suggestion in format_info['suggestions']:
                for line in suggestion.split('\n'):
                    print(f"    {line}")
        print()
        return  # Don't try to parse as CSV

    with open(filepath, 'r', encoding='utf-8') as f:
        # Detect if it's a valid CSV
        try:
            sample = f.read(4096)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample)
            has_header = csv.Sniffer().has_header(sample)
            f.seek(0)
        except csv.Error:
            print("Warning: Could not detect CSV dialect, using default")
            dialect = None
            has_header = True
            f.seek(0)

        reader = csv.reader(f, dialect) if dialect else csv.reader(f)

        rows = []
        for i, row in enumerate(reader):
            rows.append(row)
            if i >= num_rows:  # Get header + N data rows
                break

        if not rows:
            print("File appears to be empty.")
            return

    # Display header info
    if has_header and rows:
        print("\nDetected Headers:")
        print("-" * 70)
        for idx, col in enumerate(rows[0]):
            print(f"  Column {idx}: {col}")

    # Display sample data
    print(f"\nSample Data (first {min(num_rows, len(rows)-1)} rows):")
    print("-" * 70)

    data_rows = rows[1:] if has_header else rows
    for row_num, row in enumerate(data_rows[:num_rows], start=1):
        print(f"\nRow {row_num}:")
        for idx, val in enumerate(row):
            header = rows[0][idx] if has_header and idx < len(rows[0]) else f"Col {idx}"
            # Truncate long values
            display_val = val[:50] + "..." if len(val) > 50 else val
            print(f"  [{idx}] {header}: {display_val}")

    # Attempt auto-detection
    print("\n" + "=" * 70)
    print("Auto-Detection Results:")
    print("-" * 70)

    try:
        spec = auto_detect_csv_format(filepath)
        print("  Successfully detected format!")
        print(f"  - Date column: {spec.date_column} (format: {spec.date_format})")
        print(f"  - Description column: {spec.description_column}")
        print(f"  - Amount column: {spec.amount_column}")
        if spec.location_column is not None:
            print(f"  - Location column: {spec.location_column}")

        # Build suggested format string
        max_col = max(spec.date_column, spec.description_column, spec.amount_column)
        if spec.location_column is not None:
            max_col = max(max_col, spec.location_column)

        cols = []
        for i in range(max_col + 1):
            if i == spec.date_column:
                cols.append(f'{{date:{spec.date_format}}}')
            elif i == spec.description_column:
                cols.append('{description}')
            elif i == spec.amount_column:
                cols.append('{amount}')
            elif spec.location_column is not None and i == spec.location_column:
                cols.append('{location}')
            else:
                cols.append('{_}')

        format_str = ', '.join(cols)
        print(f"\n  Suggested format string:")
        print(f'    format: "{format_str}"')

        # Analyze amount patterns
        analysis = _analyze_amount_patterns(filepath, spec.amount_column, has_header=True)
        if analysis:
            print("\n" + "=" * 70)
            print("Amount Sign Analysis:")
            print("-" * 70)
            print(f"  Positive amounts: {analysis['positive_count']} (${analysis['positive_total']:,.2f})")
            print(f"  Negative amounts: {analysis['negative_count']} (${analysis['negative_total']:,.2f})")
            print(f"  Distribution: {analysis['positive_pct']:.1f}% positive")

            print(f"\n  Sign convention: {analysis['sign_convention'].replace('_', ' ')}")
            print(f"    Rationale: {analysis['rationale']}")

            if analysis['suggest_negate']:
                print("\n  Recommendation: Use {-amount} to normalize signs")
                print("    Your data has expenses as NEGATIVE values.")
                print("    Using {-amount} will flip signs so expenses become positive.")
                print(f'\n    format: "{format_str.replace("{amount}", "{-amount}")}"')
                print("\n  OR: Keep raw signs and write sign-aware rules:")
                print("    match: contains(\"GROCERY\") and amount < 0  # expenses")
                print("    match: contains(\"REFUND\") and amount > 0   # credits")
            else:
                print("\n  Your data has expenses as POSITIVE values (standard convention).")
                print("  No sign normalization needed.")
                print("\n  To match by sign in rules:")
                print("    match: contains(\"GROCERY\") and amount > 0  # expenses")
                print("    match: contains(\"REFUND\") and amount < 0   # credits/refunds")

            # Always show the +amount option for mixed-sign sources
            print("\n  TIP: For mixed-sign sources (e.g., escrow accounts):")
            print(f'    format: "{format_str.replace("{amount}", "{+amount}")}"')
            print("    {+amount} takes absolute value - all amounts become positive.")

            # Show sample credits as hints
            if analysis['sample_credits']:
                print("\n  Sample negative amounts (may be refunds/credits/income):")
                for desc, amt in analysis['sample_credits'][:5]:
                    truncated = desc[:40] + '...' if len(desc) > 40 else desc
                    print(f"    ${amt:,.2f}  {truncated}")
                print("\n  Use special tags to handle these transactions:")
                print(f"    {C.CYAN}refund{C.RESET}   - Returns/credits (nets against merchant spending)")
                print(f"    {C.CYAN}income{C.RESET}   - Deposits/salary (excluded from spending)")
                print(f"    {C.CYAN}transfer{C.RESET} - Account transfers (excluded from spending)")
                print("\n  Example rule for refunds:")
                print("    [Amazon Refund]")
                print("    match: contains(\"AMAZON\") and amount < 0")
                print("    category: Shopping")
                print("    subcategory: Online")
                print(f"    {C.CYAN}tags: refund{C.RESET}")

    except ValueError as e:
        print(f"  Could not auto-detect: {e}")
        print("\n  Use a manual format string. Example:")
        print('    format: "{date:%m/%d/%Y}, {description}, {amount}"')

    print()


def cmd_diag(args):
    """Handle the 'diag' subcommand - show diagnostic information about config and rules."""
    import json as json_module

    # Determine config directory
    if args.config:
        config_dir = os.path.abspath(args.config)
    else:
        config_dir = find_config_dir() or os.path.abspath('config')

    print("BUDGET ANALYZER DIAGNOSTICS")
    print("=" * 70)
    print()

    # Config directory info
    print("CONFIGURATION")
    print("-" * 70)
    print(f"Config directory: {config_dir}")
    print(f"  Exists: {os.path.isdir(config_dir)}")
    print()

    if not os.path.isdir(config_dir):
        print("ERROR: Config directory not found!")
        print("Run 'tally init' to create a new budget directory.")
        sys.exit(1)

    # Settings file
    settings_path = os.path.join(config_dir, args.settings)
    budget_dir = os.path.dirname(config_dir)
    print(f"Settings file: {settings_path}")
    print(f"  Exists: {os.path.exists(settings_path)}")

    config = None
    config_issues = []

    if os.path.exists(settings_path):
        try:
            config = load_config(config_dir, args.settings)
            print(f"  Loaded successfully: Yes")
            print(f"  Year: {config.get('year', 'not set')}")
            print(f"  Output dir: {config.get('output_dir', 'not set')}")
            home_locs = config.get('home_locations', set())
            print(f"  Home locations: {', '.join(sorted(home_locs)) if home_locs else 'auto-detect'}")
            currency_fmt = config.get('currency_format', '${amount}')
            from .analyzer import format_currency
            print(f"  Currency format: {currency_fmt}")
            print(f"    Example: {format_currency(1234, currency_fmt)}")
            # Show field transforms from merchants.rules
            transforms = get_transforms(config.get('_merchants_file'))
            if transforms:
                print(f"  Field transforms: {len(transforms)} transform(s)")
                for field_path, expr in transforms[:5]:
                    print(f"    - {field_path} = {expr}")
                if len(transforms) > 5:
                    print(f"    ... and {len(transforms) - 5} more")
            else:
                print(f"  Field transforms: none configured")
        except Exception as e:
            print(f"  Loaded successfully: No")
            print(f"  Error: {e}")
            config_issues.append(f"settings.yaml error: {e}")
    else:
        config_issues.append("settings.yaml not found")
    print()

    # CONFIG HEALTH CHECK - identify common issues
    print("CONFIG HEALTH CHECK")
    print("-" * 70)

    # Check for legacy CSV file
    legacy_csv = os.path.join(config_dir, 'merchant_categories.csv')
    merchants_rules = os.path.join(config_dir, 'merchants.rules')
    views_rules = os.path.join(config_dir, 'views.rules')

    if os.path.exists(legacy_csv) and not os.path.exists(merchants_rules):
        config_issues.append(f"Legacy CSV format detected: {os.path.basename(legacy_csv)}")
        print(f"  {C.YELLOW}⚠{C.RESET}  Legacy merchant_categories.csv found")
        print(f"       Run 'tally run --migrate' to upgrade to .rules format")

    # Check if merchants_file is set in settings
    if config:
        merchants_file_setting = config.get('merchants_file')
        views_file_setting = config.get('views_file')

        # Check merchants_file reference
        if not merchants_file_setting:
            if os.path.exists(merchants_rules):
                config_issues.append("merchants.rules exists but not configured in settings.yaml")
                print(f"  {C.YELLOW}⚠{C.RESET}  config/merchants.rules exists but not in settings.yaml")
                print(f"       Add: merchants_file: config/merchants.rules")
            elif not os.path.exists(legacy_csv):
                print(f"  {C.YELLOW}⚠{C.RESET}  No merchant rules configured")
                print(f"       All transactions will be categorized as 'Unknown'")
        else:
            resolved_path = os.path.join(budget_dir, merchants_file_setting)
            if not os.path.exists(resolved_path):
                config_issues.append(f"merchants_file points to missing file: {merchants_file_setting}")
                print(f"  {C.RED}✗{C.RESET}  merchants_file: {merchants_file_setting}")
                print(f"       File not found at: {resolved_path}")
            else:
                print(f"  {C.GREEN}✓{C.RESET}  merchants_file: {merchants_file_setting}")

        # Check views_file reference
        if not views_file_setting:
            if os.path.exists(views_rules):
                config_issues.append("views.rules exists but not configured in settings.yaml")
                print(f"  {C.YELLOW}⚠{C.RESET}  config/views.rules exists but not in settings.yaml")
                print(f"       Add: views_file: config/views.rules")
        else:
            resolved_path = os.path.join(budget_dir, views_file_setting)
            if not os.path.exists(resolved_path):
                config_issues.append(f"views_file points to missing file: {views_file_setting}")
                print(f"  {C.RED}✗{C.RESET}  views_file: {views_file_setting}")
                print(f"       File not found at: {resolved_path}")
            else:
                print(f"  {C.GREEN}✓{C.RESET}  views_file: {views_file_setting}")

        # Check data sources
        data_sources = config.get('data_sources', [])
        if not data_sources:
            config_issues.append("No data sources configured")
            print(f"  {C.YELLOW}⚠{C.RESET}  No data sources configured")
            print(f"       Add data_sources to settings.yaml to process transactions")
        else:
            missing_sources = []
            for source in data_sources:
                filepath = os.path.join(budget_dir, source['file'])
                if not os.path.exists(filepath):
                    missing_sources.append(source['file'])
            if missing_sources:
                config_issues.append(f"Missing data files: {', '.join(missing_sources)}")
                for f in missing_sources:
                    print(f"  {C.RED}✗{C.RESET}  data source: {f}")
                    print(f"       File not found")
            else:
                print(f"  {C.GREEN}✓{C.RESET}  data_sources: {len(data_sources)} configured, all files exist")

    if not config_issues:
        print(f"  {C.GREEN}✓{C.RESET}  All configuration files are valid")

    print()

    # FILE PATHS - show how paths are resolved
    print("FILE PATHS")
    print("-" * 70)
    print(f"  Budget directory:  {budget_dir}")
    print(f"  Config directory:  {config_dir}")
    print()
    print("  Path resolution (relative paths in settings.yaml are resolved from budget dir):")
    if config:
        if config.get('merchants_file'):
            mf = config['merchants_file']
            resolved = os.path.join(budget_dir, mf)
            exists = "exists" if os.path.exists(resolved) else "NOT FOUND"
            print(f"    merchants_file: {mf}")
            print(f"      → {resolved} ({exists})")
        if config.get('views_file'):
            vf = config['views_file']
            resolved = os.path.join(budget_dir, vf)
            exists = "exists" if os.path.exists(resolved) else "NOT FOUND"
            print(f"    views_file: {vf}")
            print(f"      → {resolved} ({exists})")
        for source in config.get('data_sources', []):
            sf = source['file']
            resolved = os.path.join(budget_dir, sf)
            exists = "exists" if os.path.exists(resolved) else "NOT FOUND"
            print(f"    data_source: {sf}")
            print(f"      → {resolved} ({exists})")
    print()

    # Data sources
    if config and config.get('data_sources'):
        print("DATA SOURCES")
        print("-" * 70)
        for i, source in enumerate(config['data_sources'], 1):
            filepath = os.path.join(config_dir, '..', source['file'])
            filepath = os.path.normpath(filepath)
            if not os.path.exists(filepath):
                filepath = os.path.join(os.path.dirname(config_dir), source['file'])

            print(f"  {i}. {source.get('name', 'unnamed')}")
            print(f"     File: {source['file']}")
            print(f"     Exists: {os.path.exists(filepath)}")
            if source.get('type'):
                print(f"     Type: {source['type']}")
            if source.get('format'):
                print(f"     Format: {source['format']}")

            # Show format spec details if available
            format_spec = source.get('_format_spec')
            if format_spec:
                print(f"     Columns:")
                print(f"       date: column {format_spec.date_column} (format: {format_spec.date_format})")
                print(f"       amount: column {format_spec.amount_column}")
                if format_spec.description_column is not None:
                    print(f"       description: column {format_spec.description_column}")
                if format_spec.custom_captures:
                    for name, col in format_spec.custom_captures.items():
                        print(f"       {name}: column {col} (custom capture)")
                if format_spec.description_template:
                    print(f"     Description template: {format_spec.description_template}")
                if format_spec.location_column is not None:
                    print(f"       location: column {format_spec.location_column}")
                if format_spec.negate_amount:
                    print(f"     Amount negation: enabled")
            print()

    # Merchant rules diagnostics
    print("MERCHANT RULES")
    print("-" * 70)

    merchants_file = config.get('_merchants_file') if config else None
    merchants_format = config.get('_merchants_format') if config else None

    if merchants_file and os.path.exists(merchants_file):
        print(f"Merchants file: {merchants_file}")
        print(f"  Format: {merchants_format or 'unknown'}")
        print(f"  Exists: True")

        # Get file stats
        file_size = os.path.getsize(merchants_file)
        print(f"  File size: {file_size} bytes")

        if merchants_format == 'new':
            # New .rules format
            try:
                from .merchant_engine import load_merchants_file
                from pathlib import Path
                engine = load_merchants_file(Path(merchants_file))
                print(f"  Rules loaded: {len(engine.rules)}")

                # Tag statistics
                rules_with_tags = sum(1 for r in engine.rules if r.tags)
                all_tags = set()
                for r in engine.rules:
                    all_tags.update(r.tags)

                # Special tags that affect spending analysis
                SPECIAL_TAGS = {'income', 'refund', 'transfer'}
                special_tags_used = all_tags & SPECIAL_TAGS

                print()
                if rules_with_tags > 0:
                    pct = (rules_with_tags / len(engine.rules) * 100) if engine.rules else 0
                    print(f"  Rules with tags: {rules_with_tags}/{len(engine.rules)} ({pct:.0f}%)")
                    if all_tags:
                        # Show special tags in cyan, others normally
                        tag_strs = []
                        for tag in sorted(all_tags):
                            if tag in SPECIAL_TAGS:
                                tag_strs.append(f"{C.CYAN}{tag}{C.RESET}")
                            else:
                                tag_strs.append(tag)
                        print(f"  Unique tags: {', '.join(tag_strs)}")

                # Show special tag usage
                print()
                print(f"  {C.BOLD}Special Tags:{C.RESET} (affect spending analysis)")
                for tag, desc in [('income', 'exclude deposits/salary'), ('refund', 'net against merchant'), ('transfer', 'exclude account transfers')]:
                    if tag in special_tags_used:
                        print(f"    {C.GREEN}✓{C.RESET} {C.CYAN}{tag}{C.RESET}: {C.DIM}{desc}{C.RESET}")
                    else:
                        print(f"    {C.DIM}○ {tag}: {desc}{C.RESET}")

                print()
                print("  MERCHANT RULES (all):")
                for rule in engine.rules:
                    print(f"    [{rule.name}]")
                    print(f"      match: {rule.match_expr}")
                    print(f"      category: {rule.category} > {rule.subcategory}")
                    if rule.tags:
                        print(f"      tags: {', '.join(rule.tags)}")
            except Exception as e:
                print(f"  Error loading rules: {e}")
        else:
            # Legacy CSV format
            rules_path = merchants_file
            diag = diagnose_rules(rules_path)
            print(f"  Rules loaded: {diag['user_rules_count']}")
            print()
            print(f"  {C.YELLOW}NOTE: Using legacy CSV format. Run 'tally run --migrate' to upgrade.{C.RESET}")

            if diag['user_rules_errors']:
                print()
                print("  ERRORS/WARNINGS:")
                for err in diag['user_rules_errors']:
                    print(f"    - {err}")

            if diag.get('rules_with_tags', 0) > 0:
                print()
                pct = (diag['rules_with_tags'] / diag['user_rules_count'] * 100) if diag['user_rules_count'] > 0 else 0
                print(f"  Rules with tags: {diag['rules_with_tags']}/{diag['user_rules_count']} ({pct:.0f}%)")
                if diag.get('unique_tags'):
                    print(f"  Unique tags: {', '.join(sorted(diag['unique_tags']))}")

            if diag['user_rules']:
                print()
                print("  MERCHANT RULES (CSV format):")
                for rule in diag['user_rules']:
                    if len(rule) == 5:
                        pattern, merchant, category, subcategory, tags = rule
                    else:
                        pattern, merchant, category, subcategory = rule
                        tags = []
                    print(f"    {pattern}")
                    tags_str = f" [{', '.join(tags)}]" if tags else ""
                    print(f"      -> {merchant} | {category} > {subcategory}{tags_str}")
    else:
        print("Merchants file: not configured")
        print()
        print("  No merchant rules found.")
        print("  Add 'merchants_file: config/merchants.rules' to settings.yaml")
        print("  Transactions will be categorized as 'Unknown'.")
    print()

    # Views configuration
    print("VIEWS")
    print("-" * 70)
    views_file_setting = config.get('views_file') if config else None
    if views_file_setting:
        # Resolve path relative to budget directory (parent of config dir)
        budget_dir = os.path.dirname(config_dir)
        views_path = os.path.join(budget_dir, views_file_setting)
        print(f"Configured in settings.yaml: {views_file_setting}")
        print(f"  Resolved path: {views_path}")
        print(f"  Exists: {os.path.exists(views_path)}")
        if os.path.exists(views_path):
            try:
                from .section_engine import load_sections
                views_config = load_sections(views_path)
                print(f"  Views defined: {len(views_config.sections)}")
                if views_config.global_variables:
                    print()
                    print("  Global variables:")
                    for name, expr in views_config.global_variables.items():
                        print(f"    {name} = {expr}")
                print()
                print("  Views:")
                for view in views_config.sections:
                    print(f"    [{view.name}]")
                    if view.description:
                        print(f"      description: {view.description}")
                    print(f"      filter: {view.filter_expr}")
            except Exception as e:
                print(f"  Error loading views: {e}")
        else:
            print()
            print("  WARNING: Views file not found!")
            print(f"  Create {views_file_setting} or remove views_file from settings.yaml")
    else:
        print("Not configured (optional)")
        print("  To enable views, add to settings.yaml:")
        print("    views_file: config/views.rules")
        print()
        print("  Then create the file with view definitions. Example:")
        print("    [Every Month]")
        print("    filter: months >= 6 and cv < 0.3")
    print()

    # JSON output option
    if args.format == 'json':
        print("JSON OUTPUT")
        print("-" * 70)
        output = {
            'config_dir': config_dir,
            'config_dir_exists': os.path.isdir(config_dir),
            'settings_file': settings_path,
            'settings_exists': os.path.exists(settings_path),
            'data_sources': [],
            'rules': {
                'user_rules_path': diag['user_rules_path'],
                'user_rules_exists': diag['user_rules_exists'],
                'user_rules_count': diag['user_rules_count'],
                'user_rules': [
                    {'pattern': r[0], 'merchant': r[1], 'category': r[2], 'subcategory': r[3], 'tags': r[4] if len(r) > 4 else []}
                    for r in diag['user_rules']
                ],
                'errors': diag['user_rules_errors'],
                'total_rules': diag['total_rules'],
                'rules_with_tags': diag.get('rules_with_tags', 0),
                'unique_tags': sorted(diag.get('unique_tags', set())),
            }
        }
        if config and config.get('data_sources'):
            for source in config['data_sources']:
                filepath = os.path.join(os.path.dirname(config_dir), source['file'])
                output['data_sources'].append({
                    'name': source.get('name'),
                    'file': source['file'],
                    'exists': os.path.exists(filepath),
                    'type': source.get('type'),
                    'format': source.get('format'),
                })
        print(json_module.dumps(output, indent=2))


def cmd_workflow(args):
    """Show context-aware workflow instructions for AI agents."""
    import subprocess

    # Detect current state
    config_dir = find_config_dir()
    has_config = config_dir is not None
    has_data_sources = False
    unknown_count = 0
    total_unknown_spend = 0

    # Calculate relative paths for display (OS-aware)
    def make_path(relative_to_config_parent, trailing_sep=False):
        """Create display path relative to cwd with correct OS separators."""
        if config_dir:
            parent = os.path.dirname(config_dir)
            full_path = os.path.join(parent, relative_to_config_parent)
        else:
            full_path = relative_to_config_parent
        rel = os.path.relpath(full_path)
        if trailing_sep:
            rel = rel + os.sep
        # Add ./ prefix on Unix only
        if os.sep == '/' and not rel.startswith('.'):
            rel = './' + rel
        return rel

    # Default paths (used when no config exists)
    path_data = make_path('data', trailing_sep=True) if config_dir else './data/'
    path_settings = make_path(os.path.join('config', 'settings.yaml')) if config_dir else './config/settings.yaml'
    path_merchants = make_path(os.path.join('config', 'merchants.rules')) if config_dir else './config/merchants.rules'

    if has_config:
        try:
            config = load_config(config_dir)
            has_data_sources = bool(config.get('data_sources'))

            if has_data_sources:
                # Try to get unknown merchant count
                try:
                    result = subprocess.run(
                        ['tally', 'discover', '--format', 'json'],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        import json as json_module
                        unknowns = json_module.loads(result.stdout)
                        unknown_count = len(unknowns)
                        total_unknown_spend = sum(u.get('total_spend', 0) for u in unknowns)
                except Exception:
                    pass
        except Exception:
            pass

    # Helper for section headers
    def section(title):
        print()
        print(f"{C.BOLD}{C.CYAN}▸ {title}{C.RESET}")

    # Build context-aware output
    print()
    print(f"{C.BOLD}  TALLY WORKFLOW{C.RESET}")
    print(f"{C.DIM}  ─────────────────────────────────────────{C.RESET}")

    # Status bar
    if not has_config:
        print(f"  {C.YELLOW}●{C.RESET} No config found")
        section("Getting Started")
        print(f"    {C.DIM}1.{C.RESET} Initialize: {C.GREEN}tally init{C.RESET}")
        print(f"       {C.DIM}Creates settings.yaml, merchants.rules, views.rules{C.RESET}")
        print()
        print(f"    {C.DIM}2.{C.RESET} Add bank/credit card CSVs to {C.CYAN}./data/{C.RESET}")
        print()
        print(f"    {C.DIM}3.{C.RESET} Configure data sources in {C.CYAN}./config/settings.yaml{C.RESET}")
        print()
        return

    if not has_data_sources:
        print(f"  {C.YELLOW}●{C.RESET} No data sources configured")
        section("Setup Data Sources")
        print(f"    {C.DIM}1.{C.RESET} Add bank/credit card CSVs to {C.CYAN}{path_data}{C.RESET}")
        print()
        print(f"    {C.DIM}2.{C.RESET} Inspect your file to get the format string:")
        print(f"       {C.GREEN}tally inspect {path_data}yourfile.csv{C.RESET}")
        print()
        print(f"    {C.DIM}3.{C.RESET} Add to {C.CYAN}{path_settings}{C.RESET}:")
        print(f"       {C.DIM}data_sources:")
        print(f"         - name: My Card")
        print(f"           file: data/transactions.csv")
        print(f"           format: \"{{date:%m/%d/%Y}},{{description}},{{amount}}\"{C.RESET}")
        print()
        section("Then: Categorize Transactions")
        print(f"    {C.DIM}Use{C.RESET} {C.GREEN}tally discover{C.RESET} {C.DIM}to find merchants, add rules to:{C.RESET}")
        print(f"    {C.CYAN}{path_merchants}{C.RESET} {C.DIM}— match transactions to categories{C.RESET}")
        print()
        return

    # Configured state
    if unknown_count > 0:
        print(f"  {C.GREEN}●{C.RESET} Config ready  {C.DIM}│{C.RESET}  {C.YELLOW}●{C.RESET} {unknown_count} unknown merchants {C.DIM}(${total_unknown_spend:,.0f}){C.RESET}")
    else:
        print(f"  {C.GREEN}●{C.RESET} Config ready  {C.DIM}│{C.RESET}  {C.GREEN}●{C.RESET} All merchants categorized")

    # Show categorization workflow if there are unknowns
    if unknown_count > 0:
        section("Categorization Workflow")
        print(f"    {C.DIM}1.{C.RESET} Get unknown merchants with suggested rules:")
        print(f"       {C.GREEN}tally discover --format json{C.RESET}")
        print()
        print(f"    {C.DIM}2.{C.RESET} Add rules to {C.CYAN}{path_merchants}{C.RESET}")
        print(f"       {C.YELLOW}READ the Best Practices below first!{C.RESET}")
        print()
        print(f"    {C.DIM}3.{C.RESET} Check progress:")
        print(f"       {C.GREEN}tally run --summary{C.RESET}")
        print()
        print(f"    {C.YELLOW}{C.BOLD}KEEP GOING UNTIL ALL UNKNOWNS ARE RESOLVED!{C.RESET}")
        print(f"    {C.DIM}Your report is only as good as your rules. Don't stop at 80%.{C.RESET}")

    section("Commands")
    cmds = [
        ("tally run", "Generate HTML spending report"),
        ("tally run --summary", "Quick text summary"),
        ("tally discover", "Find unknown merchants"),
        ("tally explain <merchant>", "Debug classification"),
        ("tally diag", "Diagnose config issues"),
    ]
    for cmd, desc in cmds:
        print(f"    {C.GREEN}{cmd:<24}{C.RESET} {C.DIM}{desc}{C.RESET}")

    section("Field Transforms")
    print(f"    {C.DIM}Strip payment processor prefixes before matching rules.{C.RESET}")
    print(f"    {C.DIM}Add to the top of {C.RESET}{C.CYAN}{path_merchants}{C.RESET}{C.DIM}:{C.RESET}")
    print()
    print(f"    {C.DIM}field.description = regex_replace(field.description, \"^APLPAY\\\\s+\", \"\")  # Apple Pay")
    print(f"    field.description = regex_replace(field.description, \"^SQ\\\\s*\\\\*\", \"\")   # Square")
    print(f"    field.description = regex_replace(field.description, \"\\\\s+DES:.*$\", \"\")  # BOA suffix{C.RESET}")

    section("Rule Syntax Reference")
    print(f"    Run {C.GREEN}tally reference{C.RESET} for complete syntax documentation:")
    print()
    print(f"    {C.DIM}• Match functions: contains(), regex(), normalized(), fuzzy(), etc.{C.RESET}")
    print(f"    {C.DIM}• Custom fields: field.name, extraction functions{C.RESET}")
    print(f"    {C.DIM}• Dynamic tags: {{field.txn_type}}, {{source}}{C.RESET}")
    print(f"    {C.DIM}• Tag-only rules: add tags without changing category{C.RESET}")
    print(f"    {C.DIM}• Views: group merchants into report sections{C.RESET}")
    print()
    print(f"    {C.GREEN}tally reference merchants{C.RESET}  {C.DIM}Merchant rules only{C.RESET}")
    print(f"    {C.GREEN}tally reference views{C.RESET}      {C.DIM}View definitions only{C.RESET}")

    section("Special Tags")
    print(f"    {C.DIM}These tags affect how transactions appear in your report:{C.RESET}")
    print()
    print(f"    {C.CYAN}income{C.RESET}     {C.DIM}Salary, deposits, interest → excluded from spending{C.RESET}")
    print(f"    {C.CYAN}transfer{C.RESET}   {C.DIM}CC payments, account transfers → excluded from spending{C.RESET}")
    print(f"    {C.CYAN}refund{C.RESET}     {C.DIM}Returns and credits → shown in Credits Applied section{C.RESET}")
    print()
    print(f"    {C.DIM}Example:{C.RESET}")
    print(f"    {C.DIM}  [Paycheck] match: contains(\"PAYROLL\") tags: income{C.RESET}")

    section("Best Practices")
    print(f"    {C.YELLOW}{C.BOLD}RULES ARE ORDERED — FIRST MATCH WINS{C.RESET}")
    print(f"    {C.DIM}Put specific rules before general ones (e.g., \"Uber Eats\" before \"Uber\"){C.RESET}")
    print()
    print(f"    {C.BOLD}1. Start broad, refine later{C.RESET}")
    print(f"       {C.DIM}Write general rules first, then add specific overrides only when needed.{C.RESET}")
    print()
    print(f"    {C.BOLD}2. Consolidate similar merchants{C.RESET}")
    print(f"       {C.DIM}One rule for all airlines is better than one per airline:{C.RESET}")
    print(f"       {C.DIM}  [Airlines]{C.RESET}")
    print(f"       {C.DIM}  match: anyof(\"DELTA\", \"UNITED\", \"AMERICAN\", \"SOUTHWEST\"){C.RESET}")
    print(f"       {C.DIM}  category: Travel{C.RESET}")
    print()
    print(f"    {C.BOLD}3. Specific rules go first{C.RESET}")
    print(f"       {C.DIM}First matching rule wins. Put \"Uber Eats\" before \"Uber\":{C.RESET}")
    print(f"       {C.DIM}  [Uber Eats] match: contains(\"UBER\") and contains(\"EATS\"){C.RESET}")
    print(f"       {C.DIM}  [Uber] match: contains(\"UBER\")  # catches remaining{C.RESET}")
    print()
    print(f"    {C.BOLD}4. Use normalized() for inconsistent names{C.RESET}")
    print(f"       {C.DIM}normalized(\"WHOLEFOODS\") matches \"WHOLE FOODS\", \"WHOLEFDS\", etc.{C.RESET}")
    print()
    print(f"    {C.BOLD}5. Avoid overly generic patterns{C.RESET}")
    print(f"       {C.DIM}contains(\"PHO\") matches \"PHONE\" — use regex(r'\\bPHO\\b') instead{C.RESET}")
    print(f"       {C.DIM}contains(\"AT\") would match everything — be specific!{C.RESET}")
    print()
    print(f"    {C.BOLD}6. Use word boundaries in regex{C.RESET}")
    print(f"       {C.DIM}regex(r'\\bTARGET\\b') won't match \"TARGETED\" or \"STARGET\"{C.RESET}")
    print()
    print(f"    {C.BOLD}7. Use tags for cross-category grouping{C.RESET}")
    print(f"       {C.DIM}Tag rules collect from ALL matching rules (not just first):{C.RESET}")
    print(f"       {C.DIM}  [Recurring Tag] match: anyof(\"NETFLIX\", \"SPOTIFY\") tags: recurring{C.RESET}")
    print()
    print(f"    {C.BOLD}8. Verify with explain{C.RESET}")
    print(f"       {C.DIM}tally explain Amazon              # check by merchant name{C.RESET}")
    print(f"       {C.DIM}tally explain \"WHOLEFDS MKT\"      # test raw description{C.RESET}")
    print(f"       {C.DIM}tally explain -c Food             # list all Food merchants{C.RESET}")
    print(f"       {C.DIM}tally explain --tags business     # list business-tagged{C.RESET}")
    print()
    print(f"    {C.BOLD}9. Strip prefixes, don't catch them{C.RESET}")
    print(f"       {C.DIM}BAD:  [ApplePay] match: startswith(\"APLPAY\")  # hides real merchants{C.RESET}")
    print(f"       {C.DIM}GOOD: Use field transforms at top of merchants.rules:{C.RESET}")
    print(f"       {C.DIM}      field.description = regex_replace(field.description, \"^APLPAY\\\\s+\", \"\"){C.RESET}")
    print(f"       {C.DIM}      \"APLPAY STARBUCKS\" → \"STARBUCKS\" → matches correctly{C.RESET}")
    print()

    section("Getting CSV Format Right")
    print(f"    {C.DIM}Use{C.RESET} {C.GREEN}tally inspect{C.RESET} {C.DIM}to analyze your CSV, but verify amount handling:{C.RESET}")
    print()
    print(f"    {C.CYAN}{{amount}}{C.RESET}      {C.DIM}Use as-is (positive = expense, negative = refund){C.RESET}")
    print(f"    {C.CYAN}{{-amount}}{C.RESET}     {C.DIM}Negate (flip the sign){C.RESET}")
    print(f"    {C.CYAN}{{+amount}}{C.RESET}     {C.DIM}Absolute value (always positive){C.RESET}")
    print()
    print(f"    {C.DIM}Common patterns:{C.RESET}")
    print(f"    {C.DIM}  Chase/Amex:  debits positive, credits negative → {{amount}}{C.RESET}")
    print(f"    {C.DIM}  Some banks:  credits positive, debits negative → {{-amount}}{C.RESET}")
    print(f"    {C.DIM}  Others:      all positive with type column     → {{+amount}}{C.RESET}")
    print()
    print(f"    {C.DIM}Test with:{C.RESET} {C.GREEN}tally run --summary{C.RESET} {C.DIM}(check if totals make sense){C.RESET}")

    section("Common Pitfalls")
    print(f"    {C.DIM}• Amounts inverted? Try {{-amount}} or {{+amount}} in format{C.RESET}")
    print(f"    {C.DIM}• Rule not matching? Use{C.RESET} {C.GREEN}tally explain \"RAW DESC\"{C.RESET}")
    print(f"    {C.DIM}• Too many matches? Use startswith() or regex word boundaries{C.RESET}")
    print(f"    {C.DIM}• Catch-all hiding merchants? Use field transforms instead{C.RESET}")
    print()


def cmd_reference(args):
    """Show complete rule syntax reference."""
    topic = args.topic.lower() if args.topic else None

    def header(title):
        print()
        print(f"{C.BOLD}{C.CYAN}{'═' * 60}{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}  {title}{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'═' * 60}{C.RESET}")
        print()

    def section(title):
        print()
        print(f"{C.BOLD}{title}{C.RESET}")
        print(f"{C.DIM}{'─' * 40}{C.RESET}")

    def show_merchants_reference():
        header("MERCHANTS.RULES REFERENCE")

        print(f"{C.DIM}File: config/merchants.rules{C.RESET}")
        print(f"{C.DIM}Purpose: Categorize transactions by matching descriptions{C.RESET}")

        section("Rule Structure")
        print(f"""
  {C.CYAN}[Rule Name]{C.RESET}              {C.DIM}# Display name for matched transactions{C.RESET}
  {C.CYAN}match:{C.RESET} <expression>      {C.DIM}# Required: when to apply this rule{C.RESET}
  {C.CYAN}category:{C.RESET} <Category>     {C.DIM}# Required: primary grouping{C.RESET}
  {C.CYAN}subcategory:{C.RESET} <Sub>       {C.DIM}# Optional: secondary grouping{C.RESET}
  {C.CYAN}tags:{C.RESET} tag1, tag2         {C.DIM}# Optional: labels for filtering{C.RESET}
""")

        section("Match Functions")
        print(f"""  {C.DIM}All match functions search description by default.{C.RESET}
  {C.DIM}Add an optional first argument to search a custom field instead.{C.RESET}
""")
        funcs = [
            ('contains("text")', 'Case-insensitive substring match',
             'match: contains("NETFLIX")', 'Matches "NETFLIX.COM", "netflix", etc.'),
            ('contains(field.x, "text")', 'Search custom field',
             'match: contains(field.memo, "REF")', 'Searches in field.memo'),
            ('regex("pattern")', 'Perl-compatible regex',
             'match: regex("UBER\\\\s(?!EATS)")', 'Matches "UBER TRIP" but not "UBER EATS"'),
            ('normalized("text")', 'Ignores spaces, hyphens, punctuation',
             'match: normalized("WHOLEFOODS")', 'Matches "WHOLE FOODS", "WHOLE-FOODS", etc.'),
            ('anyof("a", "b", ...)', 'Match any of multiple patterns',
             'match: anyof("NETFLIX", "HULU", "HBO")', 'Matches any streaming service'),
            ('startswith("text")', 'Match only at beginning',
             'match: startswith("AMZN")', 'Matches "AMZN MKTP" but not "PAY AMZN"'),
            ('fuzzy("text")', 'Approximate matching (typos)',
             'match: fuzzy("STARBUCKS")', 'Matches "STARBUKS", "STARBUCK" (80% similar)'),
            ('fuzzy("text", 0.90)', 'Fuzzy with custom threshold',
             'match: fuzzy("COSTCO", 0.90)', 'Requires 90% similarity'),
        ]
        for func, desc, example, note in funcs:
            print(f"  {C.GREEN}{func}{C.RESET}")
            print(f"    {desc}")
            print(f"    {C.DIM}Example: {example}{C.RESET}")
            print(f"    {C.DIM}→ {note}{C.RESET}")
            print()

        section("Amount & Date Conditions")
        conditions = [
            ('amount > 100', 'Transactions over $100'),
            ('amount <= 50', 'Transactions $50 or less'),
            ('amount < 0', 'Credits/refunds (negative amounts)'),
            ('month == 12', 'December transactions only'),
            ('month >= 11', 'November and December'),
            ('year == 2024', 'Specific year'),
            ('day == 1', 'First of the month'),
            ('date >= "2024-01-01"', 'On or after a specific date'),
            ('date < "2024-06-01"', 'Before a specific date'),
        ]
        for cond, desc in conditions:
            print(f"  {C.GREEN}{cond:<28}{C.RESET} {C.DIM}{desc}{C.RESET}")

        section("Combining Conditions")
        print(f"""
  {C.GREEN}and{C.RESET}   Both conditions must be true
        {C.DIM}match: contains("COSTCO") and amount > 200{C.RESET}

  {C.GREEN}or{C.RESET}    Either condition can be true
        {C.DIM}match: contains("SHELL") or contains("CHEVRON"){C.RESET}

  {C.GREEN}not{C.RESET}   Negates a condition
        {C.DIM}match: contains("UBER") and not contains("EATS"){C.RESET}

  {C.GREEN}( ){C.RESET}   Group conditions
        {C.DIM}match: (contains("AMAZON") or contains("AMZN")) and amount > 100{C.RESET}
""")

        section("Custom CSV Fields")
        print(f"""
  Access custom fields captured from CSV format strings using {C.GREEN}field.<name>{C.RESET}:

  {C.DIM}# In settings.yaml:{C.RESET}
  {C.CYAN}format: "{{date}},{{txn_type}},{{memo}},{{vendor}},{{amount}}"{C.RESET}
  {C.CYAN}columns:{C.RESET}
  {C.CYAN}  description: "{{vendor}}"{C.RESET}

  {C.DIM}# In merchants.rules:{C.RESET}
  {C.DIM}[Wire Transfer]{C.RESET}
  {C.DIM}match: field.txn_type == "WIRE"{C.RESET}
  {C.DIM}category: Transfers{C.RESET}

  {C.DIM}[Invoice Payment]{C.RESET}
  {C.DIM}match: contains(field.memo, "Invoice"){C.RESET}
  {C.DIM}category: Bills{C.RESET}

  Use {C.GREEN}exists(field.name){C.RESET} to safely check if a field exists:
  {C.DIM}match: exists(field.memo) and contains(field.memo, "REF"){C.RESET}
""")

        section("Extraction Functions")
        extract_funcs = [
            ('extract("pattern")', 'Extract first regex capture group',
             r'extract("REF:(\\d+)")', 'Captures "12345" from "REF:12345"'),
            ('extract(field.x, "pattern")', 'Extract from custom field',
             r'extract(field.memo, "#(\\d+)")', 'Captures from field.memo'),
            ('split("-", 0)', 'Split by delimiter, get element at index',
             'split("-", 0)', '"ACH-OUT-123" → "ACH"'),
            ('split(field.x, "-", 1)', 'Split custom field',
             'split(field.code, "-", 1)', 'Gets second element'),
            ('substring(0, 4)', 'Extract substring by position',
             'substring(0, 4)', '"AMZN*MARKET" → "AMZN"'),
            ('trim()', 'Remove leading/trailing whitespace',
             'trim()', '"  AMAZON  " → "AMAZON"'),
            ('trim(field.x)', 'Trim custom field',
             'trim(field.memo)', 'Trims field.memo'),
            ('exists(field.x)', 'Check if field exists and is non-empty',
             'exists(field.memo)', 'Returns false if missing or empty'),
        ]
        for func, desc, example, note in extract_funcs:
            print(f"  {C.GREEN}{func}{C.RESET}")
            print(f"    {desc}")
            print(f"    {C.DIM}Example: {example} → {note}{C.RESET}")
            print()

        section("Variables")
        print(f"""
  Define reusable conditions at the top of your file:

  {C.CYAN}is_large = amount > 500{C.RESET}
  {C.CYAN}is_holiday = month >= 11 and month <= 12{C.RESET}
  {C.CYAN}is_coffee = anyof("STARBUCKS", "PEETS", "PHILZ"){C.RESET}

  Then use in rules:
  {C.DIM}[Holiday Splurge]{C.RESET}
  {C.DIM}match: is_large and is_holiday{C.RESET}
  {C.DIM}category: Shopping{C.RESET}
""")

        section("Field Transforms")
        print(f"""
  Mutate field values before matching. Place at the top of your file:

  {C.CYAN}field.description = regex_replace(field.description, "^APLPAY\\\\s+", ""){C.RESET}
  {C.CYAN}field.description = regex_replace(field.description, "^SQ\\\\*\\\\s*", ""){C.RESET}
  {C.CYAN}field.memo = trim(field.memo){C.RESET}

  {C.BOLD}Transform Functions:{C.RESET}
""")
        transform_funcs = [
            ('regex_replace(text, pattern, repl)', 'Regex substitution (replaces all matches)',
             'regex_replace(field.description, "^APLPAY\\\\s+", "")', '"APLPAY STARBUCKS" → "STARBUCKS"'),
            ('uppercase(text)', 'Convert to uppercase',
             'uppercase(field.description)', '"Starbucks" → "STARBUCKS"'),
            ('lowercase(text)', 'Convert to lowercase',
             'lowercase(field.description)', '"STARBUCKS" → "starbucks"'),
            ('strip_prefix(text, prefix)', 'Remove prefix (case-insensitive)',
             'strip_prefix(field.description, "SQ*")', '"SQ*COFFEE" → "COFFEE"'),
            ('strip_suffix(text, suffix)', 'Remove suffix (case-insensitive)',
             'strip_suffix(field.description, " DES:123")', '"STORE DES:123" → "STORE"'),
            ('trim(text)', 'Remove leading/trailing whitespace',
             'trim(field.memo)', '"  text  " → "text"'),
        ]
        for func, desc, example, note in transform_funcs:
            print(f"  {C.GREEN}{func}{C.RESET}")
            print(f"    {desc}")
            print(f"    {C.DIM}Example: {example} → {note}{C.RESET}")
            print()

        print(f"""  {C.BOLD}Built-in fields:{C.RESET} {C.GREEN}field.description{C.RESET}, {C.GREEN}field.amount{C.RESET}, {C.GREEN}field.date{C.RESET}, {C.GREEN}field.source{C.RESET}
  {C.BOLD}Custom fields:{C.RESET} Any field captured from CSV (e.g., {C.GREEN}field.memo{C.RESET})

  {C.DIM}Original values are preserved in _raw_<field> (e.g., _raw_description){C.RESET}
""")

        section("Special Tags")
        print(f"""
  These tags have special meaning in the spending report:

  {C.CYAN}income{C.RESET}     Money coming in (salary, interest, deposits)
             {C.DIM}→ Excluded from spending totals{C.RESET}

  {C.CYAN}transfer{C.RESET}   Moving money between accounts (CC payments, transfers)
             {C.DIM}→ Excluded from spending totals{C.RESET}

  {C.CYAN}refund{C.RESET}     Returns and credits on purchases
             {C.DIM}→ Shown in "Credits Applied" section, nets against spending{C.RESET}
""")

        section("Dynamic Tags")
        print(f"""
  Use {C.GREEN}{{expression}}{C.RESET} to create tags from field values or data source:

  {C.DIM}[Bank Transaction]{C.RESET}
  {C.DIM}match: contains("BANK"){C.RESET}
  {C.DIM}category: Transfers{C.RESET}
  {C.DIM}tags: banking, {{field.txn_type}}{C.RESET}     {C.DIM}# → "banking", "wire" or "ach"{C.RESET}

  {C.DIM}[Project Expense]{C.RESET}
  {C.DIM}match: contains(field.memo, "PROJ:"){C.RESET}
  {C.DIM}category: Business{C.RESET}
  {C.DIM}tags: project, {{extract(field.memo, "PROJ:(\\w+)")}}{C.RESET}  {C.DIM}# → "project", "alpha"{C.RESET}

  Use {C.GREEN}{{source}}{C.RESET} to tag by data source (e.g., card holder):
  {C.DIM}[All Purchases]{C.RESET}
  {C.DIM}match: *{C.RESET}
  {C.DIM}tags: {{source}}{C.RESET}                      {C.DIM}# → "alice-amex", "bob-chase", etc.{C.RESET}

  Use {C.GREEN}source{C.RESET} in match expressions to vary rules by data source:
  {C.DIM}match: contains("AMAZON") and source == "Amex"{C.RESET}

  {C.DIM}Empty or whitespace-only values are automatically skipped.{C.RESET}
  {C.DIM}All tags are lowercased for consistency.{C.RESET}
""")

        section("Tag-Only Rules")
        print(f"""
  Rules without {C.GREEN}category:{C.RESET} add tags without affecting categorization:

  {C.DIM}[Large Purchase]{C.RESET}
  {C.DIM}match: amount > 500{C.RESET}
  {C.DIM}tags: large, review{C.RESET}              {C.DIM}# No category - just adds tags{C.RESET}

  {C.DIM}[Holiday Season]{C.RESET}
  {C.DIM}match: month >= 11 and month <= 12{C.RESET}
  {C.DIM}tags: holiday{C.RESET}

  {C.BOLD}Two-pass matching:{C.RESET}
  1. First rule with {C.GREEN}category:{C.RESET} sets merchant/category/subcategory
  2. Tags are collected from {C.BOLD}ALL{C.RESET} matching rules

  Example: A $600 Netflix charge in December gets:
  • Category from Netflix rule (Subscriptions)
  • Tags: entertainment + large + review + holiday
""")

        section("Rule Priority")
        print(f"""
  {C.BOLD}First categorization rule wins{C.RESET} — put specific patterns before general:

  {C.DIM}[Uber Eats]                    # ← More specific, checked first{C.RESET}
  {C.DIM}match: contains("UBER EATS"){C.RESET}
  {C.DIM}category: Food{C.RESET}

  {C.DIM}[Uber Rides]                   # ← Less specific, checked second{C.RESET}
  {C.DIM}match: contains("UBER"){C.RESET}
  {C.DIM}category: Transportation{C.RESET}

  {C.BOLD}Tags accumulate{C.RESET} from all matching rules. Use 'and not' if needed:
  {C.DIM}match: contains("UBER") and not contains("EATS"){C.RESET}
""")

        section("Complete Example")
        print(f"""{C.DIM}# === Variables ===
is_large = amount > 500

# === Subscriptions ===

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment

[Spotify]
match: contains("SPOTIFY")
category: Subscriptions
subcategory: Music
tags: entertainment

# === Food ===

[Costco Grocery]
match: contains("COSTCO") and amount <= 200
category: Food
subcategory: Grocery

[Costco Bulk]
match: contains("COSTCO") and is_large
category: Shopping
subcategory: Wholesale

# === Special Handling ===

[Salary]
match: contains("PAYROLL") or contains("DIRECT DEP")
category: Income
subcategory: Salary
tags: income

[CC Payment]
match: contains("PAYMENT THANK YOU")
category: Finance
subcategory: Credit Card
tags: transfer

[Amazon Refund]
match: contains("AMAZON") and amount < 0
category: Shopping
subcategory: Online
tags: refund{C.RESET}
""")

    def show_views_reference():
        header("VIEWS.RULES REFERENCE")

        print(f"{C.DIM}File: config/views.rules{C.RESET}")
        print(f"{C.DIM}Purpose: Create custom sections in the spending report{C.RESET}")

        section("View Structure")
        print(f"""
  {C.CYAN}[View Name]{C.RESET}                {C.DIM}# Section header in report{C.RESET}
  {C.CYAN}description:{C.RESET} <text>        {C.DIM}# Optional: subtitle under header{C.RESET}
  {C.CYAN}filter:{C.RESET} <expression>       {C.DIM}# Required: which merchants to include{C.RESET}
""")

        section("Filter Primitives")
        primitives = [
            ('months', 'Number of months with transactions', 'filter: months >= 6'),
            ('payments', 'Total number of transactions', 'filter: payments >= 12'),
            ('total', 'Total spending for this merchant', 'filter: total > 1000'),
            ('cv', 'Coefficient of variation (consistency)', 'filter: cv < 0.3'),
            ('category', 'Merchant category', 'filter: category == "Subscriptions"'),
            ('subcategory', 'Merchant subcategory', 'filter: subcategory == "Streaming"'),
            ('tags', 'Merchant tags (contains check)', 'filter: tags has "business"'),
        ]
        for prim, desc, example in primitives:
            print(f"  {C.GREEN}{prim:<12}{C.RESET} {desc}")
            print(f"             {C.DIM}{example}{C.RESET}")
            print()

        section("Aggregate Functions")
        funcs = [
            ('sum()', 'Total of all values', 'sum(by("month"))'),
            ('avg()', 'Average value', 'avg(by("month"))'),
            ('count()', 'Number of items', 'count(by("month"))'),
            ('min()', 'Minimum value', 'min(by("month"))'),
            ('max()', 'Maximum value', 'max(by("month"))'),
            ('stddev()', 'Standard deviation', 'stddev(by("month"))'),
        ]
        for func, desc, example in funcs:
            print(f"  {C.GREEN}{func:<12}{C.RESET} {desc:<24} {C.DIM}{example}{C.RESET}")

        section("Grouping with by()")
        print(f"""
  {C.GREEN}by("month"){C.RESET}    Group transactions by month
  {C.GREEN}by("year"){C.RESET}     Group transactions by year
  {C.GREEN}by("day"){C.RESET}      Group transactions by day

  Examples:
    {C.DIM}filter: sum(by("month")) > 100     # At least $100/month{C.RESET}
    {C.DIM}filter: count(by("month")) >= 1    # Transaction every month{C.RESET}
    {C.DIM}filter: avg(by("month")) > 50      # Averages over $50/month{C.RESET}
""")

        section("Comparison Operators")
        print(f"""
  {C.GREEN}=={C.RESET}  Equal to            {C.DIM}category == "Food"{C.RESET}
  {C.GREEN}!={C.RESET}  Not equal to        {C.DIM}category != "Transfers"{C.RESET}
  {C.GREEN}>{C.RESET}   Greater than        {C.DIM}total > 500{C.RESET}
  {C.GREEN}>={C.RESET}  Greater or equal    {C.DIM}months >= 6{C.RESET}
  {C.GREEN}<{C.RESET}   Less than           {C.DIM}cv < 0.3{C.RESET}
  {C.GREEN}<={C.RESET}  Less or equal       {C.DIM}payments <= 12{C.RESET}
""")

        section("Logical Operators")
        print(f"""
  {C.GREEN}and{C.RESET}   Both conditions       {C.DIM}months >= 6 and cv < 0.3{C.RESET}
  {C.GREEN}or{C.RESET}    Either condition      {C.DIM}category == "Bills" or tags has "recurring"{C.RESET}
  {C.GREEN}not{C.RESET}   Negation              {C.DIM}not category == "Income"{C.RESET}
  {C.GREEN}has{C.RESET}   Contains (for tags)   {C.DIM}tags has "business"{C.RESET}
""")

        section("View Examples")
        print(f"""{C.DIM}# Consistent monthly expenses
[Every Month]
description: Bills that hit every month
filter: months >= 6 and cv < 0.3

# Large one-time purchases
[Big Purchases]
description: Major one-time expenses
filter: total > 1000 and months <= 2

# Subscriptions by category
[Streaming]
filter: category == "Subscriptions" and subcategory == "Streaming"

# Business expenses for reimbursement
[Business]
description: Expenses to submit for reimbursement
filter: tags has "business"

# Variable recurring (same merchant, different amounts)
[Utilities]
description: Recurring with variable amounts
filter: months >= 6 and cv >= 0.3 and cv < 1.0

# High-frequency spending
[Daily Habits]
description: Places you visit frequently
filter: payments >= 20 and total > 200{C.RESET}
""")

        section("Views vs Categories")
        print(f"""
  {C.BOLD}Categories{C.RESET} (in merchants.rules): Define WHAT a transaction is
    {C.DIM}→ Each transaction has exactly one category{C.RESET}

  {C.BOLD}Views{C.RESET} (in views.rules): Define HOW to group for reporting
    {C.DIM}→ Same merchant can appear in multiple views{C.RESET}
    {C.DIM}→ Views are optional — report works without them{C.RESET}
""")

    # Main output logic
    if topic == 'merchants':
        show_merchants_reference()
    elif topic == 'views':
        show_views_reference()
    else:
        # Show both
        show_merchants_reference()
        show_views_reference()

        # Footer
        print()
        print(f"{C.DIM}{'─' * 60}{C.RESET}")
        print(f"  {C.DIM}For specific topics:{C.RESET}")
        print(f"    {C.GREEN}tally reference merchants{C.RESET}  {C.DIM}Merchant rules only{C.RESET}")
        print(f"    {C.GREEN}tally reference views{C.RESET}      {C.DIM}View definitions only{C.RESET}")
        print()


def cmd_update(args):
    """Handle the update command."""
    if args.prerelease:
        print("Checking for development builds...")
    else:
        print("Checking for updates...")

    # Get release info (may fail if offline or rate-limited)
    release_info = get_latest_release_info(prerelease=args.prerelease)
    has_update = False

    if release_info:
        latest = release_info['version']
        current = VERSION

        # Show version comparison
        from ._version import _version_greater
        has_update = _version_greater(latest, current)

        if has_update:
            if args.prerelease:
                print(f"Development build available: v{latest} (current: v{current})")
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
    config_dir = find_config_dir()
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

    # Perform binary update
    print()
    success, message = perform_update(release_info)

    if success:
        print(f"\n✓ {message}")
    else:
        print(f"\n✗ {message}")
        sys.exit(1)


def cmd_explain(args):
    """Handle the 'explain' subcommand - explain merchant classifications."""
    from difflib import get_close_matches
    from .analyzer import export_json, export_markdown, build_merchant_json

    # Determine config directory
    # Check if first merchant arg looks like a config path
    config_dir = None
    merchant_names = args.merchant if args.merchant else []

    if merchant_names and os.path.isdir(merchant_names[-1]):
        # Last arg is a directory, treat it as config
        config_dir = os.path.abspath(merchant_names[-1])
        merchant_names = merchant_names[:-1]
    elif args.config:
        config_dir = os.path.abspath(args.config)
    else:
        config_dir = find_config_dir()

    if not config_dir or not os.path.isdir(config_dir):
        print(f"Error: Config directory not found.", file=sys.stderr)
        print(f"Looked for: ./config and ./tally/config", file=sys.stderr)
        print(f"\nRun 'tally init' to create a new budget directory.", file=sys.stderr)
        sys.exit(1)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Check for deprecated settings
    _check_deprecated_description_cleaning(config)

    home_locations = config.get('home_locations', set())
    data_sources = config.get('data_sources', [])
    transforms = get_transforms(config.get('_merchants_file'))

    if not data_sources:
        print("Error: No data sources configured", file=sys.stderr)
        sys.exit(1)

    # Load merchant rules
    merchants_file = config.get('_merchants_file')
    if merchants_file and os.path.exists(merchants_file):
        rules = get_all_rules(merchants_file)
    else:
        rules = get_all_rules()

    # Parse transactions (quietly)
    all_txns = []
    for source in data_sources:
        filepath = os.path.join(config_dir, '..', source['file'])
        filepath = os.path.normpath(filepath)
        if not os.path.exists(filepath):
            filepath = os.path.join(os.path.dirname(config_dir), source['file'])
        if not os.path.exists(filepath):
            continue

        parser_type = source.get('_parser_type', source.get('type', '')).lower()
        format_spec = source.get('_format_spec')

        try:
            if parser_type == 'amex':
                _warn_deprecated_parser(source.get('name', 'AMEX'), 'amex', source['file'])
                txns = parse_amex(filepath, rules, home_locations)
            elif parser_type == 'boa':
                _warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
                txns = parse_boa(filepath, rules, home_locations)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'),
                                         transforms=transforms)
            else:
                continue
        except Exception:
            continue

        all_txns.extend(txns)

    if not all_txns:
        print("Error: No transactions found", file=sys.stderr)
        sys.exit(1)

    # Analyze
    stats = analyze_transactions(all_txns)

    # Get all merchants from by_merchant (the unified view)
    all_merchants = stats.get('by_merchant', {})

    # Load views config for view matching
    views_config = None
    views_file = os.path.join(config_dir, 'views.rules')
    if os.path.exists(views_file):
        try:
            from .section_engine import load_sections
            views_config = load_sections(views_file)
        except Exception:
            pass  # Views are optional

    verbose = args.verbose

    # Handle output based on what was requested
    if merchant_names:
        # Explain specific merchants
        found_any = False
        for merchant_query in merchant_names:
            # Try exact match first
            if merchant_query in all_merchants:
                found_any = True
                _print_merchant_explanation(merchant_query, all_merchants[merchant_query], args.format, verbose, stats['num_months'], views_config)
            else:
                # Try case-insensitive match
                matches = [m for m in all_merchants.keys() if m.lower() == merchant_query.lower()]
                if matches:
                    found_any = True
                    _print_merchant_explanation(matches[0], all_merchants[matches[0]], args.format, verbose, stats['num_months'], views_config)
                    continue

                # Try substring match on merchant names (partial search)
                query_lower = merchant_query.lower()
                partial_matches = [m for m in all_merchants.keys() if query_lower in m.lower()]
                if partial_matches:
                    found_any = True
                    print(f"Merchants matching '{merchant_query}':\n")
                    for m in sorted(partial_matches):
                        _print_merchant_explanation(m, all_merchants[m], args.format, verbose, stats['num_months'], views_config)
                    continue

                # Search transactions containing the query
                matching_txns = [t for t in all_txns if query_lower in t.get('description', '').lower()
                                 or query_lower in t.get('raw_description', '').lower()]
                if matching_txns:
                    found_any = True
                    # Group by merchant and show
                    by_merchant = {}
                    for t in matching_txns:
                        m = t['merchant']
                        if m not in by_merchant:
                            by_merchant[m] = {'count': 0, 'total': 0, 'category': t['category'], 'subcategory': t['subcategory'], 'txns': []}
                        by_merchant[m]['count'] += 1
                        by_merchant[m]['total'] += t['amount']
                        by_merchant[m]['txns'].append(t)
                    print(f"Transactions matching '{merchant_query}':\n")
                    # Special categories excluded from spending analysis
                    excluded_categories = {'Transfers', 'Payments', 'Cash'}
                    for m, data in sorted(by_merchant.items(), key=lambda x: abs(x[1]['total']), reverse=True):
                        cat = f"{data['category']} > {data['subcategory']}"
                        excluded_note = ""
                        if data['category'] in excluded_categories:
                            excluded_note = " [excluded from spending]"
                        print(f"  {m:<30} {cat:<25} ({data['count']} txns, ${abs(data['total']):,.0f}){excluded_note}")
                        if verbose >= 2:
                            # Show individual transactions
                            sorted_txns = sorted(data['txns'], key=lambda x: x['date'], reverse=True)
                            for t in sorted_txns[:10]:  # Limit to 10 most recent
                                date_str = t['date'].strftime('%m/%d') if hasattr(t['date'], 'strftime') else str(t['date'])
                                print(f"      {date_str}  ${abs(t['amount']):>10,.2f}  {t.get('raw_description', t['description'])[:50]}")
                            if len(sorted_txns) > 10:
                                print(f"      ... and {len(sorted_txns) - 10} more")
                    print()
                    continue

                # Try treating query as a raw description for rule matching
                amount = getattr(args, 'amount', None)
                trace = explain_description(merchant_query, rules, amount=amount, transforms=transforms)
                if not trace['is_unknown']:
                    # It matched a rule - show the explanation
                    found_any = True
                    _print_description_explanation(merchant_query, trace, args.format, verbose)
                else:
                    # Try fuzzy match on merchant names
                    close_matches = get_close_matches(merchant_query, list(all_merchants.keys()), n=3, cutoff=0.6)
                    if close_matches:
                        print(f"No merchant matching '{merchant_query}'. Did you mean:", file=sys.stderr)
                        for m in close_matches:
                            print(f"  - {m}", file=sys.stderr)
                    else:
                        # Show unknown merchant info
                        _print_description_explanation(merchant_query, trace, args.format, verbose)

        if not found_any:
            sys.exit(1)

    elif hasattr(args, 'view') and args.view:
        # Show all merchants in a specific view
        view_name = args.view
        views_config = config.get('sections')

        # Classify by views
        if views_config:
            from .analyzer import classify_by_sections, compute_section_totals
            view_results = classify_by_sections(
                stats['by_merchant'],
                views_config,
                stats['num_months']
            )

            # Find the matching view (case-insensitive)
            view_match = None
            for name in view_results.keys():
                if name.lower() == view_name.lower():
                    view_match = name
                    break

            if not view_match:
                valid_views = [s.name for s in views_config.sections]
                print(f"No view '{view_name}' found.", file=sys.stderr)
                print(f"Available views: {', '.join(valid_views)}", file=sys.stderr)
                sys.exit(1)

            merchants_list = view_results[view_match]
            if not merchants_list:
                print(f"No merchants in view '{view_match}'")
                sys.exit(0)

            if args.format == 'json':
                import json
                merchants = [build_merchant_json(name, data, verbose) for name, data in merchants_list]
                merchants.sort(key=lambda x: x['monthly_value'], reverse=True)
                print(json.dumps({'view': view_match, 'merchants': merchants}, indent=2))
            else:
                # Text format
                merchants_dict = {name: data for name, data in merchants_list}
                _print_classification_summary(view_match, merchants_dict, verbose, stats['num_months'])
        else:
            print("No views.rules found. Create config/views.rules to define custom views.")
            sys.exit(1)

    elif args.category:
        # Filter by category
        by_merchant = stats.get('by_merchant', {})
        matching_merchants = {k: v for k, v in by_merchant.items() if v.get('category') == args.category}

        if args.format == 'json':
            import json
            merchants = [build_merchant_json(name, data, verbose) for name, data in matching_merchants.items()]
            merchants.sort(key=lambda x: x['monthly_value'], reverse=True)
            print(json.dumps({'category': args.category, 'merchants': merchants}, indent=2))
        else:
            # Text format
            if matching_merchants:
                print(f"Merchants in category: {args.category}\n")
                _print_classification_summary(args.category, matching_merchants, verbose, stats['num_months'])
            else:
                # Suggest categories that do exist
                all_categories = set(v.get('category') for v in by_merchant.values() if v.get('category'))
                print(f"No merchants found in category '{args.category}'")
                if all_categories:
                    print(f"\nAvailable categories: {', '.join(sorted(all_categories))}")

    elif hasattr(args, 'tags') and args.tags:
        # Filter by tags
        filter_tags = set(t.strip().lower() for t in args.tags.split(','))
        by_merchant = stats.get('by_merchant', {})

        matching_merchants = {
            k: v for k, v in by_merchant.items()
            if set(t.lower() for t in v.get('tags', [])) & filter_tags
        }

        if args.format == 'json':
            import json
            merchants = [build_merchant_json(name, data, verbose) for name, data in matching_merchants.items()]
            merchants.sort(key=lambda x: x['monthly_value'], reverse=True)
            print(json.dumps({'tags': list(filter_tags), 'merchants': merchants}, indent=2))
        else:
            # Text format
            if matching_merchants:
                print(f"Merchants with tags: {', '.join(sorted(filter_tags))}\n")
                _print_classification_summary('Tagged', matching_merchants, verbose, stats['num_months'])
            else:
                # Suggest tags that do exist
                all_tags = set()
                for data in by_merchant.values():
                    all_tags.update(data.get('tags', []))
                print(f"No merchants found with tags: {', '.join(sorted(filter_tags))}")
                if all_tags:
                    print(f"\nAvailable tags: {', '.join(sorted(all_tags))}")

    else:
        # No specific merchant - show classification summary
        _print_explain_summary(stats, verbose)

    _print_deprecation_warnings(config)


def _format_match_expr(pattern):
    """Convert a regex pattern to a readable match expression."""
    import re
    # If pattern already uses function syntax, return as-is
    if re.match(r'^(normalized|anyof|startswith|fuzzy|contains|regex)\s*\(', pattern):
        return pattern
    # If it looks like a simple word match, show as contains()
    if re.match(r'^[A-Z0-9\s]+$', pattern):
        # Simple uppercase pattern - convert to contains()
        return f'contains("{pattern}")'
    elif '\\s' in pattern or '(?!' in pattern or '|' in pattern or '[' in pattern:
        # Complex regex - show as regex()
        return f'regex("{pattern}")'
    else:
        # Default to contains() for simple patterns
        return f'contains("{pattern}")'


def _get_function_explanations(pattern):
    """Get contextual explanations for functions used in a match expression."""
    import re
    explanations = []

    # Check for normalized()
    norm_match = re.search(r'normalized\s*\(\s*"([^"]+)"\s*\)', pattern)
    if norm_match:
        arg = norm_match.group(1)
        explanations.append(
            f'normalized("{arg}") - matches ignoring spaces, hyphens, and punctuation '
            f'(e.g., "UBER EATS", "UBER-EATS", "UBEREATS" all match)'
        )

    # Check for anyof()
    anyof_match = re.search(r'anyof\s*\(([^)]+)\)', pattern)
    if anyof_match:
        args = anyof_match.group(1)
        explanations.append(
            f'anyof({args}) - matches if description contains any of these patterns'
        )

    # Check for startswith()
    starts_match = re.search(r'startswith\s*\(\s*"([^"]+)"\s*\)', pattern)
    if starts_match:
        arg = starts_match.group(1)
        explanations.append(
            f'startswith("{arg}") - matches only if description begins with this prefix'
        )

    # Check for fuzzy()
    fuzzy_match = re.search(r'fuzzy\s*\(\s*"([^"]+)"(?:\s*,\s*([0-9.]+))?\s*\)', pattern)
    if fuzzy_match:
        arg = fuzzy_match.group(1)
        threshold = fuzzy_match.group(2) or '0.80'
        explanations.append(
            f'fuzzy("{arg}", {threshold}) - fuzzy matching at {float(threshold)*100:.0f}% similarity '
            f'(catches typos like "MARKEPLACE" vs "MARKETPLACE")'
        )

    return explanations


def _print_description_explanation(query, trace, output_format, verbose):
    """Print explanation for how a raw description matches."""
    import json

    if output_format == 'json':
        print(json.dumps(trace, indent=2))
    elif output_format == 'markdown':
        print(f"## Description Trace: `{query}`")
        print()
        if trace['transformed'] and trace['transformed'] != trace['original']:
            print(f"**Transformed:** `{trace['transformed']}`")
            print()

        if trace['is_unknown']:
            print(f"**Result:** Unknown merchant")
            print(f"**Extracted Name:** {trace['merchant']}")
            print()
            print("No matching rule found. Run `tally discover` to add a rule for this merchant.")
        else:
            rule = trace['matched_rule']
            match_expr = _format_match_expr(rule['pattern'])
            print(f"**Matched Rule:** `{match_expr}`")
            print(f"**Matched On:** {rule['matched_on']} description")
            print(f"**Merchant:** {trace['merchant']}")
            print(f"**Category:** {trace['category']} > {trace['subcategory']}")
            # Show function explanations
            explanations = _get_function_explanations(match_expr)
            if explanations:
                print()
                print("**How it matches:**")
                for expl in explanations:
                    print(f"- {expl}")
            # Note about special categories
            if trace['category'] in ('Transfers', 'Payments', 'Cash'):
                print(f"**Note:** This category is excluded from spending analysis")
            if rule.get('tags'):
                print(f"**Tags:** {', '.join(rule['tags'])}")
        print()
    else:
        # Text format
        print(f"Description: {query}")
        if trace['transformed'] and trace['transformed'] != trace['original']:
            print(f"  Transformed: {trace['transformed']}")

        print()
        if trace['is_unknown']:
            print(f"  Result: Unknown merchant")
            print(f"  Extracted name: {trace['merchant']}")
            print()
            print("  No matching rule found.")
            print("  Run 'tally discover' to add a rule for this merchant.")
        else:
            rule = trace['matched_rule']
            match_expr = _format_match_expr(rule['pattern'])
            print(f"  Matched Rule:")
            print(f"    {C.DIM}[{trace['merchant']}]{C.RESET}")
            print(f"    {C.DIM}match: {match_expr}{C.RESET}")
            print(f"    {C.DIM}category: {trace['category']}{C.RESET}")
            print(f"    {C.DIM}subcategory: {trace['subcategory']}{C.RESET}")
            if rule.get('tags'):
                print(f"    {C.DIM}tags: {', '.join(rule['tags'])}{C.RESET}")
            # Show function explanations
            explanations = _get_function_explanations(match_expr)
            if explanations:
                print()
                print(f"  {C.DIM}How it matches:{C.RESET}")
                for expl in explanations:
                    print(f"    {C.DIM}• {expl}{C.RESET}")
            # Note about special categories
            if trace['category'] in ('Transfers', 'Payments', 'Cash'):
                print(f"  {C.DIM}Note: This category is excluded from spending analysis{C.RESET}")
            if verbose >= 1:
                print(f"  Matched on: {rule['matched_on']} description")
        print()


def _get_matching_views(data, views_config, num_months):
    """Evaluate which views a merchant matches and return details."""
    if not views_config:
        return []

    from datetime import datetime
    from .section_engine import evaluate_section_filter, evaluate_variables

    # Calculate primitives
    months_active = data.get('months_active', 1)
    total = data.get('total', 0)
    cv = data.get('cv', 0)
    category = data.get('category', '')
    subcategory = data.get('subcategory', '')
    tags = list(data.get('tags', []))

    # Use actual transactions if available, otherwise build synthetic ones
    existing_txns = data.get('transactions', [])
    if existing_txns:
        # Use real transactions - they already have proper month info
        transactions = []
        for txn in existing_txns:
            transactions.append({
                'amount': txn['amount'],
                'date': datetime.strptime(txn['month'] + '-15', '%Y-%m-%d'),
                'category': category,
                'subcategory': subcategory,
                'tags': tags,
            })
    else:
        # Build synthetic transactions with dates spread across months_active
        payments = data.get('payments', [])
        transactions = []
        for i, p in enumerate(payments):
            # Spread across different months so get_months() works
            month_offset = i % max(1, months_active)
            transactions.append({
                'amount': p,
                'date': datetime(2025, max(1, min(12, month_offset + 1)), 15),
                'category': category,
                'subcategory': subcategory,
                'tags': tags,
            })

    # Evaluate global variables
    global_vars = evaluate_variables(
        views_config.global_variables,
        transactions,
        num_months
    )

    matches = []
    for view in views_config.sections:
        if evaluate_section_filter(view, transactions, num_months, global_vars):
            # Build context values for display
            context = {
                'months': months_active,
                'total': total,
                'cv': round(cv, 2),
                'category': category,
                'subcategory': subcategory,
                'tags': tags,
            }
            matches.append({
                'name': view.name,
                'filter': view.filter_expr,
                'description': view.description,
                'context': context,
            })

    return matches


def _print_merchant_explanation(name, data, output_format, verbose, num_months, views_config=None):
    """Print explanation for a single merchant."""
    import json
    from .analyzer import build_merchant_json

    # Get matching views
    matching_views = _get_matching_views(data, views_config, num_months)

    if output_format == 'json':
        merchant_json = build_merchant_json(name, data, verbose)
        merchant_json['views'] = matching_views
        print(json.dumps(merchant_json, indent=2))
    elif output_format == 'markdown':
        reasoning = data.get('reasoning', {})
        print(f"## {name}")
        print(f"**Category:** {data.get('category', '')} > {data.get('subcategory', '')}")
        print(f"**Frequency:** {data.get('classification', 'unknown').replace('_', ' ').title()}")
        print(f"**Reason:** {reasoning.get('decision', 'N/A')}")
        print(f"**Monthly Value:** ${data.get('monthly_value', 0):.2f}")
        print(f"**YTD Total:** ${data.get('total', 0):.2f}")
        print(f"**Months Active:** {data.get('months_active', 0)}/{num_months}")

        if matching_views:
            print(f"\n**Views ({len(matching_views)}):**")
            for view in matching_views:
                print(f"  - **{view['name']}**: `{view['filter']}`")

        if verbose >= 1:
            # Show raw description variations
            raw_descs = data.get('raw_descriptions', {})
            if raw_descs and len(raw_descs) > 0:
                sorted_descs = sorted(raw_descs.items(), key=lambda x: -x[1])
                if verbose >= 2:
                    # -vv: show all variations
                    print(f"\n**Description Variations ({len(raw_descs)}):**")
                    for desc, count in sorted_descs:
                        print(f"  - `{desc}` ({count})")
                else:
                    # -v: show top 10 variations
                    print(f"\n**Description Variations ({len(raw_descs)} unique):**")
                    for desc, count in sorted_descs[:10]:
                        print(f"  - `{desc}` ({count})")
                    if len(raw_descs) > 10:
                        print(f"  - ... and {len(raw_descs) - 10} more (use -vv to see all)")

            trace = reasoning.get('trace', [])
            if trace:
                print('\n**Decision Trace:**')
                for i, step in enumerate(trace, 1):
                    print(f"  {i}. {step}")

        if verbose >= 2:
            print(f"\n**Calculation:** {data.get('calc_type', '')} ({data.get('calc_reasoning', '')})")
            print(f"  Formula: {data.get('calc_formula', '')}")

        # Show tags
        tags = data.get('tags', [])
        if tags:
            print(f"**Tags:** {', '.join(sorted(tags))}")

        # Show pattern match info
        match_info = data.get('match_info')
        if match_info:
            pattern = match_info.get('pattern', '')
            source = match_info.get('source', 'unknown')
            print(f"\n**Pattern:** `{pattern}` ({source})")
        print()
    else:
        # Text format - show category first, then frequency classification
        category = data.get('category', 'Unknown')
        subcategory = data.get('subcategory', 'Unknown')
        classification = data.get('classification', 'unknown').replace('_', ' ').title()
        reasoning = data.get('reasoning', {})
        print(f"{name}")
        print(f"  Category: {category} > {subcategory}")
        print(f"  Frequency: {classification}")
        print(f"  Reason: {reasoning.get('decision', 'N/A')}")

        # Show tags
        tags = data.get('tags', [])
        if tags:
            print(f"  Tags: {', '.join(sorted(tags))}")

        # Show matching views
        if matching_views:
            print()
            print(f"  Views:")
            for view in matching_views:
                ctx = view['context']
                print(f"    ✓ {view['name']}")
                print(f"      filter: {view['filter']}")
                print(f"      values: months={ctx['months']}, total=${ctx['total']:,.0f}, cv={ctx['cv']}")

        # Show pattern match info
        match_info = data.get('match_info')
        if match_info:
            pattern = match_info.get('pattern', '')
            source = match_info.get('source', 'unknown')
            print(f"\n  Rule: {pattern} ({source})")

        if verbose >= 1:
            # Show raw description variations
            raw_descs = data.get('raw_descriptions', {})
            if raw_descs and len(raw_descs) > 0:
                sorted_descs = sorted(raw_descs.items(), key=lambda x: -x[1])
                if verbose >= 2:
                    # -vv: show all variations
                    print()
                    print(f"  Description variations ({len(raw_descs)}):")
                    for desc, count in sorted_descs:
                        print(f"    {desc} ({count})")
                else:
                    # -v: show top 10 variations
                    print()
                    print(f"  Description variations ({len(raw_descs)} unique):")
                    for desc, count in sorted_descs[:10]:
                        print(f"    {desc} ({count})")
                    if len(raw_descs) > 10:
                        print(f"    ... and {len(raw_descs) - 10} more (use -vv to see all)")

            # Show transactions with amounts
            transactions = data.get('transactions', [])
            if transactions:
                print()
                print(f"  Transactions ({len(transactions)}):")
                sorted_txns = sorted(transactions, key=lambda x: x.get('date', ''), reverse=True)
                display_txns = sorted_txns if verbose >= 2 else sorted_txns[:10]
                for txn in display_txns:
                    date = txn.get('date', '')
                    amount = txn.get('amount', 0)
                    desc = txn.get('description', txn.get('raw_description', ''))[:40]
                    # Show refunds in green
                    if amount > 0:
                        print(f"    {date}  {C.GREEN}{amount:>10.2f}{C.RESET}  {desc}")
                    else:
                        print(f"    {date}  {amount:>10.2f}  {desc}")
                if len(transactions) > 10 and verbose < 2:
                    print(f"    ... and {len(transactions) - 10} more (use -vv to see all)")

            trace = reasoning.get('trace', [])
            if trace:
                print()
                print("  Decision trace:")
                for step in trace:
                    print(f"    {step}")

        if verbose >= 2:
            print()
            print(f"  Calculation: {data.get('calc_type', '')} ({data.get('calc_reasoning', '')})")
            print(f"    Formula: {data.get('calc_formula', '')}")
            print(f"    CV: {reasoning.get('cv', 0):.2f}")
        print()


def _print_classification_summary(section, merchants_dict, verbose, num_months):
    """Print summary of merchants in a classification."""
    section_name = section.replace('_', ' ').title()
    print(f"{section_name} ({len(merchants_dict)} merchants)")
    print("-" * 50)

    sorted_merchants = sorted(merchants_dict.items(), key=lambda x: x[1].get('monthly_value', 0), reverse=True)
    for name, data in sorted_merchants:
        reasoning = data.get('reasoning', {})
        category = data.get('category', '')
        months = data.get('months_active', 0)

        # Short reason
        decision = reasoning.get('decision', '')
        short_reason = f"{category} ({months}/{num_months} months)"

        print(f"  {name:<24} {short_reason}")

        if verbose >= 1:
            trace = reasoning.get('trace', [])
            if trace:
                for step in trace:
                    print(f"    {step}")
            print()

    print()


def _print_explain_summary(stats, verbose):
    """Print overview summary of all merchants by category."""
    by_merchant = stats.get('by_merchant', {})
    num_months = stats['num_months']

    print("Merchant Summary")
    print("=" * 60)
    print()

    # Group by category
    by_category = {}
    for name, data in by_merchant.items():
        cat = data.get('category', 'Unknown')
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append((name, data))

    # Sort categories by total spend
    sorted_categories = sorted(
        by_category.items(),
        key=lambda x: sum(d.get('total', 0) for _, d in x[1]),
        reverse=True
    )

    for category, merchants in sorted_categories:
        total = sum(d.get('total', 0) for _, d in merchants)
        print(f"{category} ({len(merchants)} merchants, ${total:,.0f} YTD)")

        sorted_merchants = sorted(merchants, key=lambda x: x[1].get('total', 0), reverse=True)

        # Show top 5 or all if verbose
        display_count = len(sorted_merchants) if verbose >= 1 else min(5, len(sorted_merchants))

        for name, data in sorted_merchants[:display_count]:
            subcategory = data.get('subcategory', '')
            months = data.get('months_active', 0)

            print(f"  {name:<26} {subcategory} ({months}/{num_months} months)")

        if len(sorted_merchants) > display_count:
            remaining = len(sorted_merchants) - display_count
            print(f"  ... and {remaining} more")

        print()

    print("Run `tally explain <merchant>` for detailed reasoning.")
    print("Run `tally explain -v` for full details on all merchants.")


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

    # run subcommand
    run_parser = subparsers.add_parser(
        'run',
        help='Parse transactions, categorize them, and generate HTML spending report'
    )
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
        help='Filter to specific category'
    )
    explain_parser.add_argument(
        '--tags',
        help='Filter by tags (comma-separated, e.g., --tags business,reimbursable)'
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
    if args.command == 'init':
        cmd_init(args)
    elif args.command == 'run':
        cmd_run(args)
    elif args.command == 'inspect':
        cmd_inspect(args)
    elif args.command == 'discover':
        cmd_discover(args)
    elif args.command == 'diag':
        cmd_diag(args)
    elif args.command == 'explain':
        cmd_explain(args)
    elif args.command == 'workflow':
        cmd_workflow(args)
    elif args.command == 'reference':
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
        cmd_update(args)


if __name__ == '__main__':
    main()
