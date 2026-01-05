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
    workflow_parser = subparsers.add_parser(
        'workflow',
        help='Show context-aware workflow instructions for AI agents',
        description='Detects current state and shows relevant next steps.'
    )
    workflow_parser.add_argument(
        'config',
        nargs='?',
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


if __name__ == '__main__':
    main()
