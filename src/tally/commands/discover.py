"""
Tally 'discover' command - Find unknown merchants for rule creation.
"""

import os
import sys
from collections import defaultdict

from ..colors import C
from ..cli_utils import (
    resolve_config_dir,
    check_deprecated_description_cleaning,
    warn_deprecated_parser,
    print_deprecation_warnings,
)
from ..config_loader import load_config
from ..merchant_utils import get_all_rules, get_transforms
from ..analyzer import parse_amex, parse_boa, parse_generic_csv


def cmd_discover(args):
    """Handle the 'discover' subcommand - find unknown merchants for rule creation."""
    import re

    config_dir = resolve_config_dir(args)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Check for deprecated settings
    check_deprecated_description_cleaning(config)

    data_sources = config.get('data_sources', [])
    rule_mode = config.get('rule_mode', 'first_match')
    transforms = get_transforms(config.get('_merchants_file'), match_mode=rule_mode)

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
        rules = get_all_rules(merchants_file, match_mode=rule_mode)
    else:
        rules = get_all_rules(match_mode=rule_mode)

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
                warn_deprecated_parser(source.get('name', 'AMEX'), 'amex', source['file'])
                txns = parse_amex(filepath, rules)
            elif parser_type == 'boa':
                warn_deprecated_parser(source.get('name', 'BOA'), 'boa', source['file'])
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

    print_deprecation_warnings(config)


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
