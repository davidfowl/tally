"""
Tally 'discover' command - Find unknown merchants for rule creation.
"""

import os
import sys
from collections import defaultdict

from ..cli import C, require_config_dir, _check_deprecated_description_cleaning, _print_deprecation_warnings
from ..config_loader import load_config
from ..merchant_utils import get_all_rules, get_transforms
from ..analyzer import parse_amex, parse_boa, parse_generic_csv


def cmd_discover(args):
    """Handle the 'discover' subcommand - find unknown merchants for rule creation."""
    import re

    config_dir = require_config_dir(args)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Check for deprecated settings
    _check_deprecated_description_cleaning(config)

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

    # Handle --prefixes: detect common prefixes and show statistics
    if getattr(args, 'prefixes', False):
        import json as json_module

        # Get all raw descriptions (not just unknown)
        all_descriptions = [t.get('raw_description', t.get('description', '')) for t in all_txns]
        prefixes = detect_common_prefixes(all_descriptions)

        if not prefixes:
            print("No common prefixes detected in your transaction descriptions.")
            print()
            print("Prefixes are patterns like 'APLPAY', 'SQ *', 'TST*' that appear")
            print("at the start of many transactions and should be stripped.")
            sys.exit(0)

        # JSON output for agents
        if args.format == 'json':
            print(json_module.dumps(prefixes, indent=2))
            sys.exit(0)

        # Human-readable output with statistics
        print("PREFIX ANALYSIS")
        print("=" * 90)
        print(f"Found {len(prefixes)} potential prefixes in {len(all_descriptions)} transactions")
        print()
        print("Interpreting the data:")
        print("  - HIGH diversity (>0.5) + short prefix = likely payment processor (strip it)")
        print("  - LOW diversity (<0.3) = likely merchant name (don't strip)")
        print("  - Special chars (*, -) often indicate payment processors")
        print()
        print(f"{'PREFIX':<12} {'COUNT':>6} {'%':>6} {'UNIQUE':>7} {'DIV':>5}  EXAMPLES")
        print("-" * 90)

        for p in prefixes:
            examples_str = ', '.join(p['examples'][:3])
            if len(examples_str) > 40:
                examples_str = examples_str[:40] + '...'
            print(f"{p['prefix']:<12} {p['count']:>6} {p['percent']:>5.1f}% {p['unique_following']:>7} {p['diversity']:>5.2f}  {examples_str}")

        print()
        print("To strip a prefix, add to TOP of merchants.rules:")
        print()
        # Show example for the most common high-diversity prefix
        high_div = [p for p in prefixes if p['diversity'] > 0.4]
        if high_div:
            example = high_div[0]
            print(f"  # {example['prefix']} ({example['count']} txns, {example['diversity']:.0%} diverse)")
            print(f'  field.description = regex_replace(field.description, "{example["regex_pattern"]}", "")')
        else:
            # Just show the first one as an example
            example = prefixes[0]
            print(f"  # Example: {example['prefix']}")
            print(f'  field.description = regex_replace(field.description, "{example["regex_pattern"]}", "")')

        sys.exit(0)

    # Output format
    if getattr(args, 'pipe', False):
        # Clean CSV output for piping to rule import --stdin
        # Format: PATTERN,MERCHANT,Unknown,Unknown
        # Uses "Unknown" as category so agents can edit and re-run
        for raw_desc, stats in sorted_descs:
            pattern = suggest_pattern(raw_desc)
            merchant = suggest_merchant_name(raw_desc)
            # Escape commas in pattern/merchant for CSV
            if ',' in pattern:
                pattern = f'"{pattern}"'
            if ',' in merchant:
                merchant = f'"{merchant}"'
            print(f"{pattern},{merchant},Unknown,Unknown")

    elif args.format == 'csv':
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
        import shlex
        output = []
        for raw_desc, stats in sorted_descs:
            pattern = suggest_pattern(raw_desc)
            merchant = suggest_merchant_name(raw_desc)
            # Add refund tag suggestion for negative amounts
            suggested_tags = ['refund'] if stats['has_negative'] else []

            # Build CLI command for agent use
            cli_parts = ['tally', 'rule', 'add', shlex.quote(pattern), '-m', shlex.quote(merchant), '-c', 'CATEGORY']
            if suggested_tags:
                cli_parts.extend(['-t', ','.join(suggested_tags)])
            cli_command = ' '.join(cli_parts)

            output.append({
                'raw_description': raw_desc,
                'suggested_pattern': pattern,
                'suggested_merchant': merchant,
                'cli_command': cli_command,
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
    """Generate a suggested regex pattern from a raw description.

    Returns a pattern that will work with field transforms (which strip common prefixes)
    and handles truncated CSV descriptions.
    """
    import re

    desc = description.upper()

    # Remove common payment processor prefixes (case variations handled)
    # These are typically stripped by field transforms, so patterns should match without them
    prefix_patterns = [
        r'^APLPAY\s*',      # Apple Pay (AplPay, APLPAY)
        r'^SQ\s*\*?\s*',    # Square (SQ *, SQ*)
        r'^TST\*?\s*',      # Toast (TST*, TST* )
        r'^SP\s+',          # Stripe/Square Payments
        r'^PP\*?\s*',       # PayPal
        r'^GOOGLE\s*\*?\s*', # Google Pay
        r'^BT\*?\s*',       # BrainTree
        r'^IC\*?\s*',       # Instacart
        r'^DD\s*\*?\s*',    # DoorDash
        r'^CKO\*?\s*',      # Checkout.com
        r'^EB\s*\*?\s*',    # Eventbrite
        r'^LS\s+',          # Lightspeed
        r'^PY\s*\*?\s*',    # Various payment processors
        r'^CLR\*?\s*',      # Clear/Club
        r'^6CRIC\*?\s*',    # Specific payment processor
        r'^AT\s*\*?\s*',    # AT&T or tickets
        r'^SC\*?\s*',       # Various
        r'^WP\*?\s*',       # WordPress/Wix
    ]

    for prefix_pattern in prefix_patterns:
        desc = re.sub(prefix_pattern, '', desc, flags=re.IGNORECASE)

    # Remove URL prefixes
    desc = re.sub(r'^WWW\.', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'^HTTP[S]?://', '', desc, flags=re.IGNORECASE)

    # Remove common suffixes that vary
    desc = re.sub(r'\s+\d{4,}.*$', '', desc)  # Remove trailing numbers (store IDs)
    desc = re.sub(r'\s+[A-Z]{2}$', '', desc)  # Remove trailing state codes
    desc = re.sub(r'\s+\d{5}$', '', desc)  # Remove zip codes
    desc = re.sub(r'\s+#\d+', '', desc)  # Remove store numbers like #1234
    desc = re.sub(r'\s+\d{3}-\d{3}-\d{4}.*$', '', desc)  # Phone numbers
    desc = re.sub(r'\s+\(\d{3}\).*$', '', desc)  # Phone numbers in parens
    desc = re.sub(r'\s+https?://.*$', '', desc, flags=re.IGNORECASE)  # URLs

    # Clean up
    desc = desc.strip()

    # Extract the core merchant name
    words = desc.split()

    # Filter out location words that commonly appear after merchant name
    location_words = {'NEW', 'LOS', 'SAN', 'LAS', 'NORTH', 'SOUTH', 'EAST', 'WEST'}

    if words:
        # Take first word(s) until we hit a location word
        core_words = []
        for word in words[:3]:
            if word in location_words and core_words:
                break  # Stop at location word if we have at least one word
            core_words.append(word)

        # If we have nothing, take first word
        if not core_words:
            core_words = words[:1]

        # Join with flexible whitespace
        pattern = r'\s*'.join(core_words)
    else:
        pattern = desc

    return pattern


def suggest_merchant_name(description):
    """Generate a clean merchant name from a raw description."""
    import re

    desc = description

    # Remove common payment processor prefixes
    prefix_patterns = [
        r'^APLPAY\s*',      # Apple Pay
        r'^SQ\s*\*?\s*',    # Square
        r'^TST\*?\s*',      # Toast
        r'^SP\s+',          # Stripe/Square Payments
        r'^PP\*?\s*',       # PayPal
        r'^GOOGLE\s*\*?\s*', # Google Pay
        r'^BT\*?\s*',       # BrainTree
        r'^IC\*?\s*',       # Instacart
        r'^DD\s*\*?\s*',    # DoorDash
        r'^CKO\*?\s*',      # Checkout.com
        r'^EB\s*\*?\s*',    # Eventbrite
        r'^LS\s+',          # Lightspeed
        r'^PY\s*\*?\s*',    # Various payment processors
        r'^CLR\*?\s*',      # Clear/Club
        r'^6CRIC\*?\s*',    # Specific payment processor
        r'^AT\s*\*?\s*',    # AT&T or tickets
        r'^SC\*?\s*',       # Various
        r'^WP\*?\s*',       # WordPress/Wix
    ]

    for prefix_pattern in prefix_patterns:
        desc = re.sub(prefix_pattern, '', desc, flags=re.IGNORECASE)

    # Remove trailing IDs, numbers, locations
    desc = re.sub(r'\s+\d{4,}.*$', '', desc)
    desc = re.sub(r'\s+[A-Z]{2}$', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+\d{5}$', '', desc)
    desc = re.sub(r'\s+#\d+', '', desc)
    desc = re.sub(r'\s+DES:.*$', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+ID:.*$', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+\d{3}-\d{3}-\d{4}.*$', '', desc)  # Phone numbers
    desc = re.sub(r'\s+\(\d{3}\).*$', '', desc)  # Phone numbers in parens
    desc = re.sub(r'\s+https?://.*$', '', desc, flags=re.IGNORECASE)  # URLs

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


def detect_common_prefixes(descriptions, min_count=3):
    """
    Detect common prefixes in transaction descriptions.

    Returns ALL potential prefixes with statistics so agents/users can
    identify payment processor prefixes (like APLPAY, SQ *, TST*) vs
    merchant names (like AMAZON, STARBUCKS).

    Key insight: Payment processor prefixes are followed by MANY different
    merchants (high diversity), while merchant names are followed by
    locations/store numbers (low diversity).

    Args:
        descriptions: List of raw description strings
        min_count: Minimum occurrences to include in results

    Returns:
        List of dicts with prefix statistics, sorted by count descending:
        {
            'prefix': 'APLPAY ',
            'count': 45,
            'percent': 12.5,
            'unique_following': 38,
            'diversity': 0.84,  # unique_following / count
            'has_special_char': False,
            'prefix_length': 6,
            'examples': ['APLPAY STARBUCKS', 'APLPAY WHOLEFDS', ...],
            'regex_pattern': '^APLPAY\\\\s+'
        }
    """
    import re
    from collections import Counter, defaultdict

    # Extract potential prefixes and track what follows them
    prefix_candidates = Counter()
    prefix_remainders = defaultdict(set)  # prefix -> set of second words
    prefix_examples = defaultdict(list)  # prefix -> sample full descriptions

    for desc in descriptions:
        upper_desc = desc.upper().strip()
        if not upper_desc:
            continue

        # Look for patterns like "WORD " or "WORD*" or "WORD* " at start
        match = re.match(r'^([A-Z0-9]{2,10})(\s*[\*\-]\s*|\s+)', upper_desc)
        if match:
            prefix = match.group(0)
            # Only count if there's something meaningful after the prefix
            remaining = upper_desc[len(prefix):].strip()
            if remaining and len(remaining) > 3:
                prefix_candidates[prefix] += 1
                # Track the first word of the remainder (truncated to 10 chars)
                remainder_words = remaining.split()
                if remainder_words:
                    prefix_remainders[prefix].add(remainder_words[0][:10])
                # Keep examples (original case)
                if len(prefix_examples[prefix]) < 5:
                    prefix_examples[prefix].append(desc)

    total = len(descriptions)
    results = []

    for prefix, count in prefix_candidates.most_common():
        if count < min_count:
            break

        unique_following = len(prefix_remainders[prefix])
        diversity = unique_following / count if count > 0 else 0
        prefix_word = prefix.strip().rstrip('*- ')

        # Generate regex pattern for potential field transform
        escaped = re.escape(prefix.rstrip())
        if prefix.endswith(' '):
            regex_pattern = f"^{escaped}\\\\s+"
        elif '*' in prefix or '-' in prefix:
            regex_pattern = f"^{escaped}\\\\s*"
        else:
            regex_pattern = f"^{escaped}\\\\s+"

        results.append({
            'prefix': prefix.strip(),
            'count': count,
            'percent': round(count / total * 100, 1) if total > 0 else 0,
            'unique_following': unique_following,
            'diversity': round(diversity, 2),
            'has_special_char': '*' in prefix or '-' in prefix,
            'prefix_length': len(prefix_word),
            'examples': prefix_examples[prefix],
            'regex_pattern': regex_pattern,
        })

    return results
