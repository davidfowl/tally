"""
CLI utility functions for tally commands.

This module contains shared utilities used by command modules,
keeping them separate from the main CLI argument parsing.
"""

import os
import sys

from .colors import C
from .templates import STARTER_SETTINGS, STARTER_MERCHANTS, STARTER_VIEWS


# Collect deprecation warnings to print at end (avoids breaking JSON output)
_deprecated_parser_warnings = []


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
    old_layout = os.path.abspath('config')
    if os.path.isdir(old_layout):
        return old_layout

    # Check new layout
    new_layout = os.path.abspath(os.path.join('tally', 'config'))
    if os.path.isdir(new_layout):
        return new_layout

    return None


def resolve_config_dir(args, required=True):
    """Resolve config directory from args or auto-detect.

    Args:
        args: Parsed argparse namespace with optional 'config' attribute
        required: If True, exit with error when no config found

    Returns:
        Absolute path to config directory, or None if not found and not required
    """
    if hasattr(args, 'config') and args.config:
        config_dir = os.path.abspath(args.config)
    else:
        config_dir = find_config_dir()

    if required and (config_dir is None or not os.path.isdir(config_dir)):
        print("Error: Config directory not found.", file=sys.stderr)
        print("Looked for: ./config and ./tally/config", file=sys.stderr)
        print(f"\nRun '{C.GREEN}tally init{C.RESET}' to create a new budget directory.", file=sys.stderr)
        sys.exit(1)

    return config_dir


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


def warn_deprecated_parser(source_name, parser_type, filepath):
    """Record deprecation warning for amex/boa parsers (to print at end)."""
    warning = (source_name, parser_type, filepath)
    if warning not in _deprecated_parser_warnings:
        _deprecated_parser_warnings.append(warning)


def print_deprecation_warnings(config=None):
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
    if _deprecated_parser_warnings and not has_warnings:
        print(file=sys.stderr)
        for source_name, parser_type, filepath in _deprecated_parser_warnings:
            print(f"{C.YELLOW}Warning:{C.RESET} The '{parser_type}' parser is deprecated and will be removed in a future release.", file=sys.stderr)
            print(f"  Source: {source_name}", file=sys.stderr)
            print(f"  Run: {C.GREEN}tally inspect {filepath}{C.RESET} to get a format string for your CSV.", file=sys.stderr)
            print(f"  Then update settings.yaml to use 'format:' instead of 'type: {parser_type}'", file=sys.stderr)
            print(file=sys.stderr)

    _deprecated_parser_warnings.clear()


def check_deprecated_description_cleaning(config):
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
