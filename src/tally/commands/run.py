"""
Tally 'run' command - Analyze transactions and generate reports.
"""

import os
import sys

from ..config_loader import load_config
from ..merchant_utils import get_transforms
from ..analyzer import (
    parse_amex,
    parse_boa,
    parse_generic_csv,
    analyze_transactions,
    print_summary,
    print_sections_summary,
    write_summary_file_vue,
)

# Import shared utilities from parent cli module
from ..cli import (
    C,
    find_config_dir,
    _check_deprecated_description_cleaning,
    _check_merchant_migration,
    _warn_deprecated_parser,
    _print_deprecation_warnings,
)


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
                txns = parse_amex(filepath, rules)
            elif parser_type == 'boa':
                _warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
                txns = parse_boa(filepath, rules)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
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

    if not args.quiet:
        print(f"\nTotal: {len(all_txns)} transactions")

    # Analyze
    stats = analyze_transactions(all_txns)

    # Classify by user-defined views
    views_config = config.get('sections')
    if views_config:
        from ..analyzer import classify_by_sections, compute_section_totals
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
        from ..analyzer import export_json
        print(export_json(stats, verbose=verbose, only=only_filter, category_filter=category_filter))
    elif output_format == 'markdown':
        # Markdown output with reasoning
        from ..analyzer import export_markdown
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
        write_summary_file_vue(stats, output_path, year=year,
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
