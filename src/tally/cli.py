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
from .merchant_utils import get_all_rules, diagnose_rules, explain_description, load_merchant_rules
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
            print(f"{C.YELLOW}⚠️  Deprecation Notice:{C.RESET}")
            print(f"   {C.DIM}merchant_categories.csv is deprecated.{C.RESET}")
            print(f"   {C.DIM}The new .rules format supports expression-based rules like:{C.RESET}")
            print(f"   {C.DIM}  match: contains(\"COSTCO\") and amount > 200{C.RESET}")
            print()

        if is_interactive:
            # Only prompt if interactive and not using --migrate
            try:
                response = input(f"   Migrate to new format? [y/N] ").strip().lower()
                should_migrate = (response == 'y')
            except (EOFError, KeyboardInterrupt):
                should_migrate = False

            if not should_migrate:
                print(f"   {C.DIM}Continuing with CSV format (migration skipped){C.RESET}")
                print()
        elif not migrate and not quiet:
            # Non-interactive without --migrate flag
            print(f"   {C.DIM}Use --migrate flag to convert to new format{C.RESET}")
            print()

        if should_migrate:
            # Perform migration
            from .merchant_engine import csv_to_merchants_content

            # Generate .rules content
            content = csv_to_merchants_content(csv_rules)

            # Write new file next to settings.yaml
            new_file = os.path.join(config_dir, 'merchants.rules')
            try:
                with open(new_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"{C.GREEN}✓ Created {new_file}{C.RESET}")
                print()
                print(f"   {C.DIM}Add to settings.yaml:{C.RESET}")
                print(f"   {C.CYAN}merchants_file: config/merchants.rules{C.RESET}")
                print()
                print(f"   {C.DIM}Then you can delete merchant_categories.csv{C.RESET}")
                print()
            except IOError as e:
                print(f"{C.YELLOW}   Could not write file: {e}{C.RESET}", file=sys.stderr)
                print(f"   {C.DIM}Continuing with CSV format...{C.RESET}")

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
    account_type: credit_card
    format: "{date:%m/%d/%Y},{description},{amount}"
  - name: Chase
    file: data/chase.csv
    account_type: credit_card
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
data_sources:
  # Example AMEX CSV export:
  # - name: AMEX
  #   file: data/amex-{year}.csv
  #   type: amex
  #
  # Example Bank of America text statement:
  # - name: BOA Checking
  #   file: data/boa-checking.txt
  #   type: boa

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

# Description cleaning patterns (regex)
# Strip payment processor prefixes/suffixes before matching merchant rules
# This simplifies your merchant patterns - no need to handle every variation
# description_cleaning:
#   - "^APLPAY\\\\s+"           # Apple Pay prefix
#   - "^SQ\\\\s*\\\\*"          # Square prefix
#   - "^TST\\\\*\\\\s*"         # Toast POS prefix
#   - "^PP\\\\s*\\\\*"          # PayPal prefix
#   - "\\\\s+DES:.*$"           # Bank of America DES suffix
#   - "\\\\s+ID:.*$"            # Bank of America ID suffix
'''

STARTER_MERCHANTS = '''# Tally Merchant Rules
#
# Expression-based rules for categorizing transactions.
# First match wins (file order).
#
# Match expressions:
#   contains("X")     - Case-insensitive substring match
#   regex("pattern")  - Regex pattern match
#   amount > 100      - Amount conditions
#   month == 12       - Date component (month, year, day)
#   date >= "2025-01-01"  - Date range
#
# You can combine conditions with 'and', 'or', 'not'
#
# Run: tally inspect <file> to see your transaction descriptions.
# Run: tally discover to find unknown merchants.

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


def cmd_init(args):
    """Handle the 'init' subcommand."""
    target_dir = os.path.abspath(args.dir)
    # Use relative paths for display
    rel_target = os.path.relpath(target_dir)
    if rel_target == '.':
        rel_target = './'

    print(f"Initializing budget directory: {C.BOLD}{rel_target}{C.RESET}")
    print()

    created, skipped = init_config(target_dir)

    # Show each file with its status
    all_files = [(f, True) for f in created] + [(f, False) for f in skipped]
    # Sort by filename for consistent ordering
    all_files.sort(key=lambda x: x[0])

    for f, was_created in all_files:
        if was_created:
            print(f"  {C.GREEN}✓{C.RESET} {f}")
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

    year = config.get('year', 2025)
    home_locations = config.get('home_locations', set())
    travel_labels = config.get('travel_labels', {})
    data_sources = config.get('data_sources', [])
    cleaning_patterns = config.get('description_cleaning', [])

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
                txns = parse_amex(filepath, rules, home_locations, cleaning_patterns)
            elif parser_type == 'boa':
                _warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
                txns = parse_boa(filepath, rules, home_locations, cleaning_patterns)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'),
                                         cleaning_patterns=cleaning_patterns)
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
            print_summary(stats, year=year, currency_format=currency_format)
    else:
        # HTML output (default)
        # Print summary first
        if not args.quiet:
            if stats.get('sections'):
                print_sections_summary(stats, year=year, currency_format=currency_format, only_filter=only_filter)
            else:
                print_summary(stats, year=year, currency_format=currency_format)

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

    home_locations = config.get('home_locations', set())
    data_sources = config.get('data_sources', [])
    cleaning_patterns = config.get('description_cleaning', [])

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
                txns = parse_amex(filepath, rules, home_locations, cleaning_patterns)
            elif parser_type == 'boa':
                _warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
                txns = parse_boa(filepath, rules, home_locations, cleaning_patterns)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'),
                                         cleaning_patterns=cleaning_patterns)
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
    desc_stats = defaultdict(lambda: {'count': 0, 'total': 0.0, 'examples': []})

    for txn in unknown_txns:
        raw = txn.get('raw_description', txn.get('description', ''))
        amount = abs(txn.get('amount', 0))
        desc_stats[raw]['count'] += 1
        desc_stats[raw]['total'] += amount
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
            output.append({
                'raw_description': raw_desc,
                'suggested_merchant': merchant,
                'suggested_rule': suggest_merchants_rule(merchant, pattern),
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
            print(f"   Count: {stats['count']} | Total: ${stats['total']:.2f}")
            print(f"   Suggested merchant: {merchant}")
            print()
            print(f"   {C.DIM}[{merchant}]")
            print(f"   match: contains(\"{pattern}\")")
            print(f"   category: CATEGORY")
            print(f"   subcategory: SUBCATEGORY{C.RESET}")
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


def suggest_merchants_rule(merchant_name, pattern):
    """Generate a suggested rule block in .rules format."""
    # Escape quotes in pattern if needed
    escaped_pattern = pattern.replace('"', '\\"')
    return f"""[{merchant_name}]
match: contains("{escaped_pattern}")
category: CATEGORY
subcategory: SUBCATEGORY"""


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
    Analyze amount column patterns to suggest account_type and categorization.

    Returns dict with:
        - positive_count: number of positive amounts
        - negative_count: number of negative amounts
        - positive_total: sum of positive amounts
        - negative_total: sum of negative amounts (as positive number)
        - suggested_account_type: 'credit_card' or 'bank'
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

    # Determine account type based on sign distribution
    # Credit cards: mostly positive (charges), few negative (payments/refunds)
    # Bank accounts: mostly negative (debits), some positive (deposits)
    positive_pct = positive_count / total_count * 100

    if positive_pct > 70:
        suggested_type = 'credit_card'
        rationale = "mostly positive amounts (charges)"
    elif positive_pct < 30:
        suggested_type = 'bank'
        rationale = "mostly negative amounts (debits)"
    else:
        # Mixed - harder to tell
        if positive_total > negative_total:
            suggested_type = 'credit_card'
            rationale = "total positive exceeds negative"
        else:
            suggested_type = 'bank'
            rationale = "total negative exceeds positive"

    return {
        'positive_count': positive_count,
        'negative_count': negative_count,
        'positive_total': positive_total,
        'negative_total': negative_total,
        'positive_pct': positive_pct,
        'suggested_account_type': suggested_type,
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

            print(f"\n  Suggested account_type: {analysis['suggested_account_type']}")
            print(f"    Rationale: {analysis['rationale']}")

            if analysis['suggested_account_type'] == 'credit_card':
                print("\n  What 'account_type: credit_card' does:")
                print("    - Keeps amounts as-is (charges are positive expenses)")
                print("    - Negative amounts (payments/refunds) are kept for categorization")
                print("\n  For credit cards, negative amounts are usually:")
                print("    - Payments → Category: Transfers/CC Payment")
                print("    - Refunds → Category: same as original purchase")
            else:
                print("\n  What 'account_type: bank' does:")
                print("    - Negates amounts (debits become positive expenses)")
                print("    - Skips credits/deposits (income filtered out)")
                print("\n  For bank accounts, positive amounts (after negation) are usually:")
                print("    - Regular purchases, bills, transfers out")
                print("  Skipped credits are usually:")
                print("    - Deposits/Income, transfers in")

            # Show sample credits as hints
            if analysis['sample_credits']:
                print("\n  Sample credits (may be transfers/income):")
                for desc, amt in analysis['sample_credits'][:5]:
                    truncated = desc[:40] + '...' if len(desc) > 40 else desc
                    print(f"    ${abs(amt):,.2f}  {truncated}")
                print("\n  Hint: Add categorization rules for these if they are transfers/income")

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
    print(f"Settings file: {settings_path}")
    print(f"  Exists: {os.path.exists(settings_path)}")

    config = None
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
            cleaning = config.get('description_cleaning', [])
            if cleaning:
                print(f"  Description cleaning: {len(cleaning)} pattern(s)")
                for pattern in cleaning:
                    print(f"    - {pattern}")
            else:
                print(f"  Description cleaning: none configured")
        except Exception as e:
            print(f"  Loaded successfully: No")
            print(f"  Error: {e}")
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
                if rules_with_tags > 0:
                    print()
                    pct = (rules_with_tags / len(engine.rules) * 100) if engine.rules else 0
                    print(f"  Rules with tags: {rules_with_tags}/{len(engine.rules)} ({pct:.0f}%)")
                    all_tags = set()
                    for r in engine.rules:
                        all_tags.update(r.tags)
                    if all_tags:
                        print(f"  Unique tags: {', '.join(sorted(all_tags))}")

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

    # Classification rules (occurrence-based analysis)
    print("CLASSIFICATION RULES")
    print("-" * 70)
    rules_file = os.path.join(config_dir, 'classification_rules.txt')
    print(f"Classification rules file: {rules_file}")
    print(f"  Exists: {os.path.exists(rules_file)}")
    if os.path.exists(rules_file):
        # Count non-comment lines
        with open(rules_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            rule_lines = [l for l in lines if l.strip() and not l.strip().startswith('#')]
            print(f"  Active rules: {len(rule_lines)}")
        print()
        print("  Rules (determines bucket and calc_type after categorization):")
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                print(f"    {stripped}")
    else:
        print("  Not found - will be created with defaults on first 'tally run'")
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
        print(f"    {C.DIM}1.{C.RESET} Initialize the project:")
        print(f"       {C.GREEN}tally init{C.RESET}")
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
        print(f"    {C.DIM}2.{C.RESET} Add rules to {C.CYAN}{path_merchants}{C.RESET}:")
        print(f"       {C.DIM}[Starbucks]")
        print(f"       match: contains(\"STARBUCKS\")")
        print(f"       category: Food")
        print(f"       subcategory: Coffee{C.RESET}")
        print()
        print(f"    {C.DIM}3.{C.RESET} Check progress:")
        print(f"       {C.GREEN}tally run --summary{C.RESET}")
        print()
        print(f"    {C.DIM}Repeat until all merchants are categorized{C.RESET}")

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

    section("Debugging Rules")
    print(f"    {C.GREEN}tally explain Amazon{C.RESET}           {C.DIM}See how Amazon is classified{C.RESET}")
    print(f"    {C.GREEN}tally explain Amazon -v{C.RESET}        {C.DIM}Show raw description variations{C.RESET}")
    print(f"    {C.GREEN}tally explain \"RAW DESC\"{C.RESET}       {C.DIM}Trace how a description matches{C.RESET}")

    section("Description Cleaning")
    print(f"    {C.DIM}Strip payment processor prefixes before matching rules.{C.RESET}")
    print(f"    {C.DIM}Add to {C.RESET}{C.CYAN}{path_settings}{C.RESET}{C.DIM}:{C.RESET}")
    print()
    print(f"    {C.DIM}description_cleaning:")
    print(f"      - \"^APLPAY\\\\s+\"       # Apple Pay")
    print(f"      - \"^SQ\\\\s*\\\\*\"       # Square")
    print(f"      - \"\\\\s+DES:.*$\"      # BOA suffix{C.RESET}")

    section("Merchant Rules (.rules)")
    print(f"    {C.DIM}Expression-based rules with full power:{C.RESET}")
    print()
    print(f"    {C.DIM}[Netflix]")
    print(f"    match: contains(\"NETFLIX\")")
    print(f"    category: Subscriptions")
    print(f"    subcategory: Streaming")
    print(f"    tags: entertainment, recurring{C.RESET}")
    print()
    print(f"    {C.BOLD}Match functions:{C.RESET}")
    funcs = [
        ('contains("X")', "Case-insensitive substring match"),
        ('regex("pattern")', "Regex pattern match"),
        ('amount > 100', "Amount conditions"),
        ('month == 12', "Date components (month, year, day)"),
    ]
    for func, desc in funcs:
        print(f"      {C.CYAN}{func:<22}{C.RESET} {C.DIM}{desc}{C.RESET}")
    print()
    print(f"    {C.DIM}First match wins — put specific patterns before general ones{C.RESET}")
    print(f"    {C.DIM}Tags are accumulated from ALL matching rules{C.RESET}")

    section("Views (Optional)")
    print(f"    {C.DIM}Create custom report views with {C.RESET}{C.CYAN}config/views.rules{C.RESET}")
    print()
    print(f"    {C.DIM}[Every Month]")
    print(f"    description: Consistent recurring expenses")
    print(f"    filter: months >= 6 and cv < 0.3")
    print()
    print(f"    [Large Purchases]")
    print(f"    filter: total > 1000 and months <= 2{C.RESET}")
    print()
    print(f"    {C.DIM}Primitives: months, total, cv, category, subcategory, tags, payments{C.RESET}")
    print(f"    {C.DIM}Functions: sum(), avg(), count(), min(), max(), stddev(), by(){C.RESET}")
    print(f"    {C.DIM}Grouping: sum(by(\"month\")) for monthly totals{C.RESET}")
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
        print("\nRestart tally to use the new version.")
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

    home_locations = config.get('home_locations', set())
    data_sources = config.get('data_sources', [])
    cleaning_patterns = config.get('description_cleaning', [])

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
                txns = parse_amex(filepath, rules, home_locations, cleaning_patterns)
            elif parser_type == 'boa':
                _warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
                txns = parse_boa(filepath, rules, home_locations, cleaning_patterns)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'),
                                         cleaning_patterns=cleaning_patterns)
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
                    for m, data in sorted(by_merchant.items(), key=lambda x: abs(x[1]['total']), reverse=True):
                        cat = f"{data['category']} > {data['subcategory']}"
                        print(f"  {m:<30} {cat:<25} ({data['count']} txns, ${abs(data['total']):,.0f})")
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
                trace = explain_description(merchant_query, rules, amount=amount, cleaning_patterns=cleaning_patterns)
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


def _print_description_explanation(query, trace, output_format, verbose):
    """Print explanation for how a raw description matches."""
    import json

    if output_format == 'json':
        print(json.dumps(trace, indent=2))
    elif output_format == 'markdown':
        print(f"## Description Trace: `{query}`")
        print()
        if trace['cleaned'] and trace['cleaned'] != trace['original']:
            print(f"**Cleaned:** `{trace['cleaned']}`")
            print()

        if trace['is_unknown']:
            print(f"**Result:** Unknown merchant")
            print(f"**Extracted Name:** {trace['merchant']}")
            print()
            print("No matching rule found. Run `tally discover` to add a rule for this merchant.")
        else:
            rule = trace['matched_rule']
            print(f"**Matched Rule:** `{rule['pattern']}`")
            print(f"**Matched On:** {rule['matched_on']} description")
            print(f"**Merchant:** {trace['merchant']}")
            print(f"**Category:** {trace['category']} > {trace['subcategory']}")
            if rule.get('tags'):
                print(f"**Tags:** {', '.join(rule['tags'])}")
        print()
    else:
        # Text format
        print(f"Description: {query}")
        if trace['cleaned'] and trace['cleaned'] != trace['original']:
            print(f"  Cleaned: {trace['cleaned']}")

        print()
        if trace['is_unknown']:
            print(f"  Result: Unknown merchant")
            print(f"  Extracted name: {trace['merchant']}")
            print()
            print("  No matching rule found.")
            print("  Run 'tally discover' to add a rule for this merchant.")
        else:
            rule = trace['matched_rule']
            print(f"  Matched Rule:")
            print(f"    {C.DIM}[{trace['merchant']}]{C.RESET}")
            print(f"    {C.DIM}match: {_format_match_expr(rule['pattern'])}{C.RESET}")
            print(f"    {C.DIM}category: {trace['category']}{C.RESET}")
            print(f"    {C.DIM}subcategory: {trace['subcategory']}{C.RESET}")
            if rule.get('tags'):
                print(f"    {C.DIM}tags: {', '.join(rule['tags'])}{C.RESET}")
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
