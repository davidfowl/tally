"""
Tally 'run' command - Analyze transactions and generate reports.
"""

import os
import sys

from ..colors import C
from ..cli_utils import (
    resolve_config_dir,
    check_deprecated_description_cleaning,
    warn_deprecated_parser,
    print_deprecation_warnings,
)
from ..path_utils import resolve_data_source_paths
from ..config_loader import load_config, load_supplemental_sources
from ..merchant_utils import get_transforms
from ..migrations import check_merchant_migration
from ..analyzer import (
    parse_amex,
    parse_boa,
    parse_generic_csv,
    analyze_transactions,
    print_summary,
    print_sections_summary,
    write_summary_file_vue,
    export_json,
    export_csv,
    compare_reports,
    has_changes,
    format_diff_summary,
    format_diff_detailed,
)
from ..parsers import ParseResult, SkippedRow
from collections import Counter


def collect_source_names(data_sources):
    """Collect source names for the report subtitle (exclude supplemental).

    Deduplicates while preserving the order sources are declared in settings.yaml.
    """
    return list(dict.fromkeys(
        s.get('name', 'Unknown') for s in data_sources if not s.get('_supplemental', False)
    ))


def cmd_run(args):
    """Handle the 'run' subcommand."""
    config_dir = resolve_config_dir(args)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        # Format validation errors from config_loader
        print(f"Error: {e}", file=sys.stderr)
        print(f"\nRun 'tally diag --config {config_dir}' for more details.", file=sys.stderr)
        sys.exit(1)

    # Check for deprecated settings
    check_deprecated_description_cleaning(config)

    # Get report title (new) or year (deprecated)
    title = config.get('title')
    year = config.get('year')
    if year and not title:
        # Backwards compatibility: generate title from year
        title = f"{year} Financial Report"

    data_sources = config.get('data_sources', [])
    rule_mode = config.get('rule_mode', 'first_match')
    transforms = get_transforms(config.get('_merchants_file'), match_mode=rule_mode)

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

    # Auto-enable quiet mode for machine-readable formats
    output_format = getattr(args, 'format', 'html')
    if output_format in ('json', 'csv', 'markdown'):
        args.quiet = True

    if not args.quiet:
        if title:
            print(f"Tally - {title}")
        else:
            print("Tally")
        print(f"Config: {config_dir}/{args.settings}")
        print()

    # Load merchant rules (with migration check for CSV -> .rules)
    rules = check_merchant_migration(config, config_dir, args.quiet, getattr(args, 'migrate', False))

    # Load supplemental data sources for cross-source queries
    supplemental_data = load_supplemental_sources(config, config_dir)
    if not args.quiet and supplemental_data:
        print(f"  Supplemental sources: {', '.join(supplemental_data.keys())}")

    # Parse transactions from configured data sources (skip supplemental)
    all_txns = []
    all_skipped = []  # Track all skipped rows across sources
    parsed_files = []  # (filepath, source_name) for the review inventory
    verbose = args.verbose if hasattr(args, 'verbose') else 0

    for source in data_sources:
        # Skip supplemental sources - they don't generate transactions
        if source.get('_supplemental', False):
            continue

        source_files, match_kind = resolve_data_source_paths(config_dir, source.get('file'))
        if not source_files:
            if not args.quiet:
                if match_kind == 'glob':
                    print(f"  {source['name']}: No files matched - {source['file']}")
                elif match_kind == 'dir':
                    print(f"  {source['name']}: No CSV files found - {source['file']}")
                else:
                    print(f"  {source['name']}: File not found - {source['file']}")
            continue

        # Get parser type and format spec (set by config_loader.resolve_source_format)
        parser_type = source.get('_parser_type', source.get('type', '')).lower()
        format_spec = source.get('_format_spec')

        source_txns = []
        source_skipped = []
        unknown_parser = False
        for filepath in source_files:
            try:
                if parser_type == 'amex':
                    warn_deprecated_parser(source.get('name', 'AMEX'), 'amex', filepath)
                    txns = parse_amex(filepath, rules)
                    skipped = []
                elif parser_type == 'boa':
                    warn_deprecated_parser(source.get('name', 'BOA'), 'boa', filepath)
                    txns = parse_boa(filepath, rules)
                    skipped = []
                elif parser_type == 'generic' and format_spec:
                    result = parse_generic_csv(filepath, format_spec, rules,
                                               source_name=source.get('name', 'CSV'),
                                               decimal_separator=source.get('decimal_separator', '.'),
                                               transforms=transforms,
                                               data_sources=supplemental_data)
                    txns = result.transactions
                    skipped = result.skipped_rows
                else:
                    if not args.quiet:
                        print(f"  {source['name']}: Unknown parser type '{parser_type}'")
                        print(f"    Use 'tally inspect {source['file']}' to determine format")
                    unknown_parser = True
                    break
            except Exception as e:
                if not args.quiet:
                    print(f"  {source['name']}: Error parsing {filepath} - {e}")
                continue

            source_txns.extend(txns)
            source_skipped.extend(skipped)
            parsed_files.append((filepath, source.get('name', 'CSV')))

        if unknown_parser:
            continue

        if source_txns:
            all_txns.extend(source_txns)
        all_skipped.extend(source_skipped)
        if not args.quiet:
            files_note = f" ({len(source_files)} files)" if len(source_files) > 1 else ""
            skip_note = ""
            if source_skipped:
                skip_note = f" ({C.YELLOW}{len(source_skipped)} rows skipped{C.RESET})"
            print(f"  {source['name']}: {len(source_txns)} transactions{files_note}{skip_note}")

            # Show skip details based on verbosity
            if source_skipped and verbose >= 1:
                # Group by reason
                reason_counts = Counter(s.reason for s in source_skipped)
                reason_labels = {
                    'empty_required_field': 'empty required field',
                    'date_parse_error': 'date parse error',
                    'amount_parse_error': 'amount parse error',
                    'insufficient_columns': 'insufficient columns',
                    'regex_mismatch': 'regex mismatch',
                    'zero_amount': 'zero amount',
                    'parse_exception': 'parse error',
                }
                for reason, count in reason_counts.most_common():
                    label = reason_labels.get(reason, reason)
                    print(f"    {C.YELLOW}⚠{C.RESET} {count} row{'s' if count > 1 else ''}: {label}")

            # Show individual errors at -vv
            if source_skipped and verbose >= 2:
                # Limit to first 10 errors
                shown = source_skipped[:10]
                for skip in shown:
                    # Get just the filename for cleaner output
                    filename = os.path.basename(skip.filepath)
                    print(f"      {filename}:{skip.line_number}: {skip.message}")
                if len(source_skipped) > 10:
                    print(f"      ... and {len(source_skipped) - 10} more")

            # At default verbosity, hint to use -v when rows skipped
            if source_skipped and verbose == 0:
                print(f"    {C.DIM}Run with -v to see why rows were skipped{C.RESET}")

    if not all_txns:
        print("Error: No transactions found", file=sys.stderr)
        if all_skipped:
            # Show why rows were skipped when no transactions parsed
            reason_counts = Counter(s.reason for s in all_skipped)
            reason_labels = {
                'empty_required_field': 'empty required field',
                'date_parse_error': 'date parse error',
                'amount_parse_error': 'amount parse error',
                'insufficient_columns': 'insufficient columns',
                'regex_mismatch': 'regex mismatch',
                'zero_amount': 'zero amount',
                'parse_exception': 'parse error',
            }
            print(f"\n{len(all_skipped)} rows were skipped:", file=sys.stderr)
            for reason, count in reason_counts.most_common():
                label = reason_labels.get(reason, reason)
                print(f"  • {count} row{'s' if count > 1 else ''}: {label}", file=sys.stderr)

            # Show first few specific errors
            print(f"\nFirst errors:", file=sys.stderr)
            for skip in all_skipped[:5]:
                filename = os.path.basename(skip.filepath)
                print(f"  {filename}:{skip.line_number}: {skip.message}", file=sys.stderr)
            if len(all_skipped) > 5:
                print(f"  ... and {len(all_skipped) - 5} more", file=sys.stderr)

            # Provide actionable hints based on most common error
            top_reason = reason_counts.most_common(1)[0][0] if reason_counts else None
            if top_reason == 'date_parse_error':
                print(f"\n{C.CYAN}Hint:{C.RESET} Check your date format. Run 'tally inspect <file>' to detect the correct format.", file=sys.stderr)
            elif top_reason == 'empty_required_field':
                print(f"\n{C.CYAN}Hint:{C.RESET} Some required fields are empty. Check CSV has data in date/amount/description columns.", file=sys.stderr)
            elif top_reason == 'insufficient_columns':
                print(f"\n{C.CYAN}Hint:{C.RESET} CSV has fewer columns than expected. Check your format string matches the file.", file=sys.stderr)
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
    currency_format = config.get('currency_format', '${amount}')

    if output_format == 'json':
        # JSON output with reasoning
        print(export_json(stats, verbose=verbose, category_filter=category_filter))
    elif output_format == 'csv':
        # CSV output (transaction-level)
        print(export_csv(stats, category_filter=category_filter))
    elif output_format == 'markdown':
        # Markdown output with reasoning
        from ..analyzer import export_markdown
        print(export_markdown(stats, verbose=verbose, category_filter=category_filter, currency_format=currency_format))
    elif output_format == 'summary' or args.summary:
        # Text summary only (no HTML)
        group_by = getattr(args, 'group_by', 'merchant')
        if stats.get('sections'):
            print_sections_summary(stats, title=title, currency_format=currency_format, only_filter=only_filter)
        else:
            print_summary(stats, title=title, currency_format=currency_format, group_by=group_by)
    else:
        # HTML output (default)
        # Print summary first
        group_by = getattr(args, 'group_by', 'merchant')
        if not args.quiet:
            if stats.get('sections'):
                print_sections_summary(stats, title=title, currency_format=currency_format, only_filter=only_filter)
            else:
                print_summary(stats, title=title, currency_format=currency_format, group_by=group_by)

        # Determine output path
        if args.output:
            output_path = args.output
        else:
            output_dir = os.path.join(os.path.dirname(config_dir), config.get('output_dir', 'output'))
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, config.get('html_filename', 'spending_summary.html'))

        # Collect source names for the report subtitle (exclude supplemental)
        source_names = collect_source_names(data_sources)
        write_summary_file_vue(stats, output_path, title=title,
                               currency_format=currency_format, sources=source_names,
                               embedded_html=args.embedded_html)
        if not args.quiet:
            # Make the path clickable using OSC 8 hyperlink escape sequence
            abs_path = os.path.abspath(output_path)
            file_url = f"file://{abs_path}"
            # OSC 8 format: \033]8;;URL\033\\text\033]8;;\033\\
            clickable_path = f"\033]8;;{file_url}\033\\{output_path}\033]8;;\033\\"
            print(f"\nHTML report: {clickable_path}")

        # Save JSON report and show diff
        import json
        json_path = output_path.rsplit('.', 1)[0] + '.json'

        # Load previous report if exists
        prev_data = None
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    prev_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                prev_data = None

        # Generate and save current JSON
        curr_json = export_json(stats, verbose=verbose)
        curr_data = json.loads(curr_json)

        with open(json_path, 'w') as f:
            f.write(curr_json)

        # Show diff if previous report exists
        if prev_data and not args.quiet:
            diff = compare_reports(prev_data, curr_data)
            show_detailed = getattr(args, 'diff', False)
            if has_changes(diff):
                if show_detailed:
                    print(format_diff_detailed(diff, currency_format))
                else:
                    print(format_diff_summary(diff, currency_format))
            elif show_detailed:
                # User asked for --diff but there are no changes
                print("\nNo changes since last run.")

        # Data file inventory (config/inventory.yaml) - HTML path only, after the
        # report is written. Registration is deliberately independent of whether
        # the review file is generated: `registered` records when tally first saw
        # a file, so it must keep advancing even while review is switched off.
        # Otherwise re-enabling would backdate every file to that day.
        from ..categorization_common import CategorizationError
        from ..inventory import register_files

        try:
            register_files(config_dir, parsed_files)
        except CategorizationError as e:
            print(f"\nError: {e}", file=sys.stderr)
            sys.exit(1)

        # Categorization review file (config/categorization.yaml). Defaults on
        # when a merchants file is configured; the report above stays written
        # even on failure.
        if not config.get('generate_categorization_file', True):
            # Never delete what the user may have typed into — but a silently
            # stale file can mislead an agent that reads it later, so say so.
            from ..categorization import stale_file_notice

            notice = stale_file_notice(config_dir)
            if notice and not args.quiet:
                print(f"\n{C.YELLOW}{notice}{C.RESET}")
        elif config.get('_merchants_file'):
            from ..categorization import generate_categorization

            try:
                cat_status = generate_categorization(config, config_dir, all_txns, rules)
            except CategorizationError as e:
                print(f"\nError: {e}", file=sys.stderr)
                sys.exit(1)

            if cat_status.written and not args.quiet:
                parts = [f"{cat_status.unknown_count} unknown"]
                if cat_status.unknown_count:
                    parts[0] += (f" ({cat_status.new_count} new, "
                                 f"{cat_status.carried_forward} carried forward)")
                if cat_status.awaiting_review:
                    parts.append(f"{cat_status.awaiting_review} awaiting review "
                                 f"— confirm to close")
                warn = ""
                if cat_status.answered_still_unknown:
                    warn = f"  {C.YELLOW}⚠ {cat_status.answered_still_unknown} still unknown after apply{C.RESET}"
                print(f"\nCategorization: {', '.join(parts)}{warn}")
                abs_yaml = os.path.abspath(cat_status.yaml_path)
                yaml_url = f"file://{abs_yaml}"
                clickable_yaml = f"\033]8;;{yaml_url}\033\\{cat_status.yaml_path}\033]8;;\033\\"
                print(f"  {clickable_yaml}")

    print_deprecation_warnings(config)
