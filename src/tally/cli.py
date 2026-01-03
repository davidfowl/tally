"""
Tally CLI - Command-line interface.

Usage:
    tally /path/to/config/dir               # Analyze using config directory
    tally /path/to/config/dir --summary     # Summary only (no HTML)
    tally /path/to/config/dir --settings settings-2024.yaml
    tally --help-config                     # Show detailed config documentation
"""

import argparse
import sys

from .colors import C
from ._version import (
    VERSION, GIT_SHA, REPO_URL, check_for_updates,
)


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
        help='(deprecated, use --config) Path to config directory'
    )
    up_parser.add_argument(
        '--config', '-c',
        dest='config_dir',
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
        help='Filter to specific views (comma-separated view names from views.rules)'
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
    up_parser.add_argument(
        '--diff',
        action='store_true',
        help='Show detailed diff against previous report'
    )

    # run subcommand (deprecated alias for 'up' - hidden from help)
    run_parser = subparsers.add_parser('run')
    run_parser.add_argument(
        'config',
        nargs='?',
        help='(deprecated, use --config) Path to config directory'
    )
    run_parser.add_argument(
        '--config', '-c',
        dest='config_dir',
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
        help='Filter to specific views (comma-separated view names from views.rules)'
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
        help='(deprecated, use --config) Path to config directory'
    )
    discover_parser.add_argument(
        '--config', '-c',
        dest='config_dir',
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
        help='(deprecated, use --config) Path to config directory'
    )
    diag_parser.add_argument(
        '--config', '-c',
        dest='config_dir',
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
        help='Show how merchants are categorized and which rules match',
        description='Show categorization details for merchants or transaction descriptions. '
                    'Pass a merchant name to see its category and matching views, or a raw transaction '
                    'description to see which rule matches. Use --amount to test amount-based rules.'
    )
    explain_parser.add_argument(
        'merchant',
        nargs='*',
        help='Merchant name or raw transaction description to explain (shows summary if omitted)'
    )
    explain_parser.add_argument(
        '--config', '-c',
        dest='config_dir',
        help='Path to config directory (recommended over positional argument)'
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
        '--amount', '-a',
        type=float,
        help='Transaction amount for testing amount-based rules (e.g., --amount 150.00)'
    )

    # workflow subcommand
    workflow_parser = subparsers.add_parser(
        'workflow',
        help='Show context-aware workflow instructions for AI agents',
        description='Detects current state and shows relevant next steps.'
    )
    workflow_parser.add_argument(
        'config',
        nargs='?',
        help='(deprecated, use --config) Path to config directory'
    )
    workflow_parser.add_argument(
        '--config', '-c',
        dest='config_dir',
        help='Path to config directory (default: ./config)'
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
        'config',
        nargs='?',
        help='(deprecated, use --config) Path to config directory'
    )
    update_parser.add_argument(
        '--config', '-c',
        dest='config_dir',
        help='Path to config directory (default: ./config)'
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

    # rule subcommand with its own subparsers
    rule_parser = subparsers.add_parser(
        'rule',
        help='Manage merchant rules (add, list, update, delete)',
        description='CRUD operations for .rules files. Faster than editing files directly.'
    )
    rule_subparsers = rule_parser.add_subparsers(dest='rule_command', metavar='<action>')

    # rule add
    rule_add = rule_subparsers.add_parser(
        'add',
        help='Add or update a rule'
    )
    rule_add.add_argument(
        'pattern',
        help='Pattern or expression (e.g., "NETFLIX" or "contains(\'UBER\')")'
    )
    rule_add.add_argument(
        '-m', '--merchant',
        help='Display name (defaults to pattern-derived name)'
    )
    rule_add.add_argument(
        '-c', '--category',
        help='Category'
    )
    rule_add.add_argument(
        '-s', '--subcategory',
        help='Subcategory'
    )
    rule_add.add_argument(
        '-t', '--tags',
        help='Comma-separated tags'
    )
    rule_add.add_argument(
        '-p', '--priority',
        type=int,
        default=50,
        help='Priority (higher = checked first, default: 50)'
    )
    rule_add.add_argument(
        '--validate',
        action='store_true',
        dest='validate',
        help='Validate rule matches against transactions'
    )
    rule_add.add_argument(
        '--json',
        action='store_true',
        help='Output as JSON'
    )
    rule_add.add_argument(
        '--config',
        help='Config directory'
    )

    # rule list
    rule_list = rule_subparsers.add_parser(
        'list',
        help='List all rules'
    )
    rule_list.add_argument(
        '--category',
        help='Filter by category'
    )
    rule_list.add_argument(
        '--json',
        action='store_true',
        help='Output as JSON'
    )
    rule_list.add_argument(
        '--config',
        help='Config directory'
    )

    # rule show
    rule_show = rule_subparsers.add_parser(
        'show',
        help='Show details of a rule'
    )
    rule_show.add_argument(
        'name',
        help='Rule name'
    )
    rule_show.add_argument(
        '--config',
        help='Config directory'
    )

    # rule update
    rule_update = rule_subparsers.add_parser(
        'update',
        help='Update an existing rule'
    )
    rule_update.add_argument(
        'name',
        help='Rule name to update'
    )
    rule_update.add_argument(
        '-c', '--category',
        help='New category'
    )
    rule_update.add_argument(
        '-s', '--subcategory',
        help='New subcategory'
    )
    rule_update.add_argument(
        '-t', '--tags',
        help='Tag modifications: tag, +tag (add), -tag (remove)'
    )
    rule_update.add_argument(
        '-p', '--priority',
        type=int,
        help='New priority'
    )
    rule_update.add_argument(
        '--config',
        help='Config directory'
    )

    # rule delete
    rule_delete = rule_subparsers.add_parser(
        'delete',
        help='Delete a rule'
    )
    rule_delete.add_argument(
        'name',
        nargs='?',
        help='Rule name to delete'
    )
    rule_delete.add_argument(
        '--pattern',
        help='Delete by pattern instead of name'
    )
    rule_delete.add_argument(
        '--config',
        help='Config directory'
    )

    # rule import
    rule_import = rule_subparsers.add_parser(
        'import',
        help='Import rules from CSV'
    )
    rule_import.add_argument(
        'file',
        nargs='?',
        help='CSV file to import'
    )
    rule_import.add_argument(
        '--stdin',
        action='store_true',
        help='Read CSV from stdin'
    )
    rule_import.add_argument(
        '--validate',
        action='store_true',
        help='Validate rules against transactions'
    )
    rule_import.add_argument(
        '--json',
        action='store_true',
        help='Output as JSON'
    )
    rule_import.add_argument(
        '--config',
        help='Config directory'
    )

    args = parser.parse_args()

    # If no command specified, show help
    if args.command is None:
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
    elif args.command == 'rule':
        from .commands import cmd_rule
        cmd_rule(args)


if __name__ == '__main__':
    main()
