"""
Tally 'inspect' command - Show CSV structure and sample rows.
"""

import os
import sys
import csv

from ..colors import C
from ..analyzer import auto_detect_csv_format


def cmd_inspect(args):
    """Handle the 'inspect' subcommand - show CSV structure and sample rows."""

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

    # Analyze all columns
    column_analysis = _analyze_columns(filepath, has_header=has_header, max_rows=100)

    # Display column analysis
    if column_analysis:
        print("\nColumn Analysis:")
        print("-" * 70)
        for i, col in enumerate(column_analysis):
            header = col['header']
            col_type = col['type']
            fmt = col.get('format') or ''

            # Build type description
            type_desc = col_type
            if fmt:
                type_desc = f"{col_type} ({fmt})"
            if col['empty_pct'] >= 90:
                type_desc = f"empty ({col['empty_pct']:.0f}% blank)"
            elif col.get('distinct_values') and col_type == 'categorical':
                n_distinct = len(col['distinct_values'])
                type_desc = f"categorical ({n_distinct} values)"

            # Add observations inline
            obs_str = ''
            if col.get('observations'):
                obs_str = f" - {'; '.join(col['observations'])}"

            print(f"  [{i:2}] {header:<25} {type_desc}{obs_str}")

    # Show categorical columns with their values
    categorical_cols = [c for c in column_analysis if c.get('distinct_values') and len(c['distinct_values']) <= 15]
    if categorical_cols:
        print("\nCategorical Column Values:")
        print("-" * 70)
        for col in categorical_cols:
            header = col['header']
            values = col['distinct_values']
            # Sort by count descending
            sorted_vals = sorted(values.items(), key=lambda x: -x[1])
            val_strs = [f"{v} ({c})" for v, c in sorted_vals[:8]]
            if len(sorted_vals) > 8:
                val_strs.append(f"...+{len(sorted_vals) - 8} more")
            print(f"  {header}: {', '.join(val_strs)}")

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

        # Analyze amount patterns - detailed analysis with both signs
        analysis = _analyze_amount_column_detailed(filepath, spec.amount_column, has_header=True)
        if analysis:
            print("\n" + "=" * 70)
            print("Amount Distribution:")
            print("-" * 70)
            print(f"  {analysis['positive_count']} positive amounts, totaling ${analysis['positive_total']:,.2f}")
            print(f"  {analysis['negative_count']} negative amounts, totaling ${analysis['negative_total']:,.2f}")

            # Show format observations
            if analysis['format_observations']:
                print(f"\n  Format observations:")
                for obs in analysis['format_observations']:
                    print(f"    - {obs}")

            # Show sample positive transactions
            if analysis['sample_positive']:
                print(f"\n  Sample positive:")
                for desc, amt in analysis['sample_positive']:
                    truncated = desc[:45] + '...' if len(desc) > 45 else desc
                    print(f"    +${amt:,.2f}  {truncated}")

            # Show sample negative transactions
            if analysis['sample_negative']:
                print(f"\n  Sample negative:")
                for desc, amt in analysis['sample_negative']:
                    truncated = desc[:45] + '...' if len(desc) > 45 else desc
                    print(f"    -${abs(amt):,.2f}  {truncated}")

            # Show amount modifier options (not recommendations)
            print("\n  Amount modifiers available:")
            print(f"    {{amount}}   - use values as-is")
            print(f"    {{-amount}}  - negate (flip sign)")
            print(f"    {{+amount}}  - absolute value")

    except ValueError as e:
        print(f"  Could not auto-detect: {e}")
        print("\n  Use a manual format string. Example:")
        print('    format: "{date:%m/%d/%Y}, {description}, {amount}"')

    print()


def _detect_file_format(filepath):
    """Detect if file is CSV, fixed-width text, or other format.

    Returns dict with:
        - format_type: 'csv', 'fixed_width', 'unknown'
        - delimiter: detected delimiter for CSV
        - has_header: whether file has headers
        - issues: list of potential issues detected
        - suggestions: list of suggestions
    """
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


def _analyze_columns(filepath, has_header=True, max_rows=100):
    """
    Analyze all columns in a CSV to detect their types and patterns.

    Returns list of dicts, one per column:
        - header: column header (or "Column N")
        - type: detected type (date, currency, number, text, empty, categorical)
        - format: additional format info (e.g., date format, decimal places)
        - sample_values: list of sample non-empty values
        - empty_pct: percentage of empty values
        - distinct_values: dict of value -> count (for low-cardinality columns)
        - min_val, max_val: for numeric columns
    """
    import re as re_mod

    columns = []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)

            if has_header:
                headers = next(reader, None)
                if not headers:
                    return []
            else:
                # Peek at first row to determine column count
                first_row = next(reader, None)
                if not first_row:
                    return []
                # Generate synthetic headers
                headers = [f'Column {i}' for i in range(len(first_row))]
                # Reset to re-read file including first row as data
                f.seek(0)
                reader = csv.reader(f)

            # Initialize column stats
            num_cols = len(headers)
            col_stats = []
            for i, h in enumerate(headers):
                col_stats.append({
                    'header': h if h else f'Column {i}',
                    'values': [],
                    'empty_count': 0,
                    'total_count': 0,
                })

            # Collect values from rows
            for row_num, row in enumerate(reader):
                if row_num >= max_rows:
                    break
                for i, val in enumerate(row):
                    if i < len(col_stats):
                        col_stats[i]['total_count'] += 1
                        val = val.strip()
                        if not val:
                            col_stats[i]['empty_count'] += 1
                        else:
                            col_stats[i]['values'].append(val)

            # Analyze each column
            for i, stats in enumerate(col_stats):
                col_info = {
                    'header': stats['header'],
                    'type': 'unknown',
                    'format': None,
                    'sample_values': stats['values'][:5],
                    'empty_pct': (stats['empty_count'] / stats['total_count'] * 100)
                                 if stats['total_count'] > 0 else 100,
                    'distinct_values': None,
                    'observations': [],
                }

                values = stats['values']

                # Check for empty column
                if not values:
                    col_info['type'] = 'empty'
                    columns.append(col_info)
                    continue

                # Detect column type
                col_info['type'], col_info['format'], col_info['observations'] = \
                    _detect_column_type(values, stats['header'])

                # For categorical columns, count distinct values
                if col_info['type'] == 'categorical' or len(set(values)) <= 20:
                    value_counts = {}
                    for v in values:
                        value_counts[v] = value_counts.get(v, 0) + 1
                    col_info['distinct_values'] = value_counts

                columns.append(col_info)

    except Exception:
        return []

    return columns


def _detect_column_type(values, header=''):
    """
    Detect the type of a column based on sample values.

    Returns (type, format, observations) tuple.
    """
    import re as re_mod

    if not values:
        return 'empty', None, []

    observations = []
    header_lower = header.lower()

    # Date patterns
    date_patterns = [
        (r'^\d{1,2}/\d{1,2}/\d{4}$', '%m/%d/%Y', 'MM/DD/YYYY'),
        (r'^\d{1,2}/\d{1,2}/\d{2}$', '%m/%d/%y', 'MM/DD/YY'),
        (r'^\d{4}-\d{2}-\d{2}$', '%Y-%m-%d', 'YYYY-MM-DD (ISO)'),
        (r'^\d{1,2}-\d{1,2}-\d{4}$', '%m-%d-%Y', 'MM-DD-YYYY'),
        (r'^\d{1,2}\.\d{1,2}\.\d{4}$', '%d.%m.%Y', 'DD.MM.YYYY (European)'),
    ]

    # Check if it looks like a date column
    for pattern, fmt, desc in date_patterns:
        matches = sum(1 for v in values if re_mod.match(pattern, v))
        if matches >= len(values) * 0.8:
            return 'date', fmt, [f'Format: {desc}']

    # Currency patterns (with symbol)
    currency_with_symbol = re_mod.compile(r'^[$€£¥]\s*-?[\d,]+\.?\d*$|^-?[$€£¥]\s*[\d,]+\.?\d*$')
    currency_matches = sum(1 for v in values if currency_with_symbol.match(v))
    if currency_matches >= len(values) * 0.8:
        # Detect which currency symbol
        symbols = {'$': 0, '€': 0, '£': 0, '¥': 0}
        for v in values:
            for sym in symbols:
                if sym in v:
                    symbols[sym] += 1
        main_symbol = max(symbols, key=symbols.get) if any(symbols.values()) else '$'
        return 'currency', main_symbol, [f'Currency symbol: {main_symbol}']

    # Numeric patterns (including negative, decimals)
    numeric_pattern = re_mod.compile(r'^-?[\d,]+\.?\d*$|^\([\d,]+\.?\d*\)$')
    numeric_matches = sum(1 for v in values if numeric_pattern.match(v))

    if numeric_matches >= len(values) * 0.8:
        # Check for currency hints in header
        if any(s in header_lower for s in ['$', 'amount', 'price', 'cost', 'fee', 'balance', 'total']):
            # Analyze number format
            has_decimals = any('.' in v for v in values)
            has_negatives = any(v.startswith('-') or v.startswith('(') for v in values)
            has_thousands = any(',' in v for v in values)

            obs = []
            if has_decimals:
                # Count decimal places
                decimal_places = set()
                for v in values:
                    if '.' in v:
                        parts = v.replace('(', '').replace(')', '').replace('-', '').split('.')
                        if len(parts) == 2:
                            decimal_places.add(len(parts[1]))
                if decimal_places:
                    obs.append(f'Decimal places: {", ".join(str(d) for d in sorted(decimal_places))}')
            if has_negatives:
                obs.append('Contains negative values')
            if has_thousands:
                obs.append('Uses thousands separator (,)')

            return 'currency', None, obs

        return 'number', None, []

    # Check for categorical (low distinct values)
    distinct = set(values)
    if len(distinct) <= 15 and len(values) >= 5:
        return 'categorical', None, [f'{len(distinct)} distinct values']

    # Check for ticker/symbol (short uppercase alphanumeric)
    ticker_pattern = re_mod.compile(r'^[A-Z]{1,5}$')
    ticker_matches = sum(1 for v in values if ticker_pattern.match(v))
    if ticker_matches >= len(values) * 0.5:
        return 'ticker/symbol', None, ['Short uppercase codes']

    # Default to text
    avg_len = sum(len(v) for v in values) / len(values) if values else 0
    if avg_len > 30:
        return 'text', None, ['Long text values']
    return 'text', None, []


def _analyze_amount_column_detailed(filepath, amount_col, desc_col=1, has_header=True, max_rows=1000):
    """
    Detailed analysis of amount column showing both positive and negative samples.

    Returns dict with:
        - positive_count, positive_total, sample_positive
        - negative_count, negative_total, sample_negative
        - format_observations: list of format-related observations
    """
    import re as re_mod

    positive_count = 0
    negative_count = 0
    positive_total = 0.0
    negative_total = 0.0
    sample_positive = []  # (description, amount) tuples
    sample_negative = []  # (description, amount) tuples
    format_observations = []

    # Track format details
    has_decimals = False
    has_integers = False
    has_currency_symbols = False
    has_parentheses_negative = False
    decimal_places = set()

    def parse_amount(val):
        nonlocal has_decimals, has_integers, has_currency_symbols, has_parentheses_negative, decimal_places
        if not val:
            return None
        original = val
        val = val.strip()

        # Detect currency symbols
        if re_mod.search(r'[$€£¥]', val):
            has_currency_symbols = True

        # Remove currency symbols, commas
        val = re_mod.sub(r'[$€£¥,]', '', val)

        # Handle parentheses as negative
        if val.startswith('(') and val.endswith(')'):
            has_parentheses_negative = True
            val = '-' + val[1:-1]

        try:
            result = float(val)
            # Track decimal places
            if '.' in original:
                has_decimals = True
                parts = val.split('.')
                if len(parts) == 2:
                    decimal_places.add(len(parts[1]))
            else:
                has_integers = True
            return result
        except ValueError:
            return None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            if has_header:
                headers = next(reader, None)
                # Try to find description column
                for idx, h in enumerate(headers or []):
                    hl = h.lower()
                    if any(x in hl for x in ['desc', 'merchant', 'payee', 'name', 'action', 'memo']):
                        desc_col = idx
                        break

            for i, row in enumerate(reader):
                if i >= max_rows:
                    break
                if amount_col < len(row):
                    amount = parse_amount(row[amount_col])
                    if amount is not None:
                        desc = row[desc_col] if desc_col < len(row) else ''
                        if amount > 0:
                            positive_count += 1
                            positive_total += amount
                            if len(sample_positive) < 5:
                                sample_positive.append((desc.strip(), amount))
                        elif amount < 0:
                            negative_count += 1
                            negative_total += abs(amount)
                            if len(sample_negative) < 5:
                                sample_negative.append((desc.strip(), amount))
                        # Skip zeros

    except Exception:
        return None

    total_count = positive_count + negative_count
    if total_count == 0:
        return None

    # Build format observations
    if has_decimals and has_integers:
        format_observations.append('Mixed: some values have decimals, some are integers')
    if has_currency_symbols:
        format_observations.append('Values contain currency symbols')
    if has_parentheses_negative:
        format_observations.append('Negative values use parentheses notation')
    if decimal_places:
        if len(decimal_places) == 1:
            format_observations.append(f'Consistent {list(decimal_places)[0]} decimal places')
        else:
            format_observations.append(f'Decimal places vary: {sorted(decimal_places)}')

    return {
        'positive_count': positive_count,
        'positive_total': positive_total,
        'sample_positive': sample_positive,
        'negative_count': negative_count,
        'negative_total': negative_total,
        'sample_negative': sample_negative,
        'format_observations': format_observations,
    }
