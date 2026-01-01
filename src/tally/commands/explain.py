"""
Tally 'explain' command - Explain merchant classifications.
"""

import os
import sys

from ..cli import C, find_config_dir, _check_deprecated_description_cleaning, _print_deprecation_warnings
from ..config_loader import load_config
from ..merchant_utils import get_all_rules, get_transforms, explain_description
from ..analyzer import parse_amex, parse_boa, parse_generic_csv
from ..analyzer import analyze_transactions, export_json, export_markdown, build_merchant_json


def cmd_explain(args):
    """Handle the 'explain' subcommand - explain merchant classifications."""
    from difflib import get_close_matches

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
                from ..cli import _warn_deprecated_parser
                _warn_deprecated_parser(source.get('name', 'AMEX'), 'amex', source['file'])
                txns = parse_amex(filepath, rules)
            elif parser_type == 'boa':
                from ..cli import _warn_deprecated_parser
                _warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
                txns = parse_boa(filepath, rules)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
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
            from ..section_engine import load_sections
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
            from ..analyzer import classify_by_sections, compute_section_totals
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
    from ..section_engine import evaluate_section_filter, evaluate_variables

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
