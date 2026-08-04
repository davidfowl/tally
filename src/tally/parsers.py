"""
CSV/Transaction Parsing - Parse various bank statement formats.

This module handles parsing of CSV files and other transaction formats.
"""

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, NamedTuple

from .merchant_utils import normalize_merchant
from .format_parser import FormatSpec


@dataclass
class SkippedRow:
    """Information about a row that was skipped during CSV parsing."""
    filepath: str           # Source CSV file
    line_number: int        # Line number in file (1-indexed)
    reason: str             # Category: empty_field, date_error, etc.
    message: str            # Human-readable description
    raw_data: Optional[str] = None  # Original line/row content for debugging


class ParseResult(NamedTuple):
    """Result of parsing a CSV file."""
    transactions: List[dict]
    skipped_rows: List[SkippedRow]


def parse_amount(amount_str, decimal_separator='.'):
    """Parse an amount string to float, handling various formats.

    Args:
        amount_str: String like "1,234.56" or "1.234,56" or "(100.00)"
        decimal_separator: Character used as decimal separator ('.' or ',')

    Returns:
        Float value of the amount
    """
    amount_str = amount_str.strip()

    # Handle parentheses notation for negative: (100.00) -> -100.00
    negative = False
    if amount_str.startswith('(') and amount_str.endswith(')'):
        negative = True
        amount_str = amount_str[1:-1]

    # Remove currency symbols
    amount_str = re.sub(r'[$€£¥]', '', amount_str).strip()

    if decimal_separator == ',':
        # European format: 1.234,56 or 1 234,56
        # Remove thousand separators (period or space)
        amount_str = amount_str.replace('.', '').replace(' ', '')
        # Convert decimal comma to period for float()
        amount_str = amount_str.replace(',', '.')
    else:
        # US format: 1,234.56
        # Remove thousand separators (comma)
        amount_str = amount_str.replace(',', '')

    result = float(amount_str)
    return -result if negative else result


def parse_amex(filepath, rules):
    """Parse AMEX CSV file and return list of transactions.

    DEPRECATED: Use format strings instead. This parser will be removed in a future release.
    """
    transactions = []

    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                amount = float(row['Amount'])
                if amount == 0:
                    continue

                date = datetime.strptime(row['Date'], '%m/%d/%Y')
                merchant, category, subcategory, match_info = normalize_merchant(
                    row['Description'], rules, amount=amount, txn_date=date.date(),
                    data_source='AMEX',
                )

                transactions.append({
                    'date': date,
                    'raw_description': row['Description'],
                    'description': row['Description'],
                    'amount': amount,
                    'merchant': merchant,
                    'category': category,
                    'subcategory': subcategory,
                    'source': 'AMEX',
                    'match_info': match_info,
                    'tags': match_info.get('tags', []) if match_info else [],
                })
            except (ValueError, KeyError):
                continue

    return transactions


def parse_boa(filepath, rules):
    """Parse BOA statement file and return list of transactions.

    DEPRECATED: Use format strings instead. This parser will be removed in a future release.
    """
    transactions = []

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            # Format: MM/DD/YYYY  Description  Amount  Balance
            match = re.match(
                r'^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+([-\d,]+\.\d{2})\s+([-\d,]+\.\d{2})$',
                line.strip()
            )
            if not match:
                continue

            try:
                date = datetime.strptime(match.group(1), '%m/%d/%Y')
                description = match.group(2)
                amount = float(match.group(3).replace(',', ''))

                if amount == 0:
                    continue

                merchant, category, subcategory, match_info = normalize_merchant(
                    description, rules, amount=amount, txn_date=date.date(),
                    data_source='BOA',
                )

                transactions.append({
                    'date': date,
                    'raw_description': description,
                    'description': description,
                    'amount': amount,
                    'merchant': merchant,
                    'match_info': match_info,
                    'category': category,
                    'subcategory': subcategory,
                    'source': 'BOA',
                    'tags': match_info.get('tags', []) if match_info else [],
                })
            except ValueError:
                continue

    return transactions


def _iter_rows_with_delimiter(filepath, delimiter, has_header):
    """Iterate over rows, handling different delimiter types.

    Args:
        filepath: Path to the file
        delimiter: None for CSV, 'tab' for TSV, single char (e.g. ';'), or 'regex:pattern'
        has_header: Whether to skip the first line

    Yields:
        Tuple of (line_number, row_data, raw_line) where:
        - line_number: 1-indexed line number in the file
        - row_data: List of column values, or None if line didn't match
        - raw_line: Original line content (for regex mode) or comma-joined row
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        if delimiter and delimiter == 'tab':
            delimiter = '\t'
        if delimiter and delimiter.startswith('regex:'):
            # Regex-based parsing
            pattern = re.compile(delimiter[6:])  # Strip 'regex:' prefix
            for i, line in enumerate(f, start=1):
                if has_header and i == 1:
                    continue
                raw_line = line.rstrip('\n\r')
                line_stripped = line.strip()
                if not line_stripped:
                    yield (i, None, raw_line, 'empty_line')
                    continue
                match = pattern.match(line_stripped)
                if match:
                    yield (i, list(match.groups()), raw_line, None)
                else:
                    yield (i, None, raw_line, 'regex_mismatch')
        elif delimiter and len(delimiter) == 1:
            reader = csv.reader(f, delimiter=delimiter)
            line_num = 0
            for row in reader:
                line_num += 1
                if has_header and line_num == 1:
                    continue
                raw_line = delimiter.join(row)
                yield (line_num, row, raw_line, None)
        else:
            # Standard CSV (comma-delimited)
            reader = csv.reader(f)
            line_num = 0
            for row in reader:
                line_num += 1
                if has_header and line_num == 1:
                    continue
                raw_line = ','.join(row)
                yield (line_num, row, raw_line, None)


def parse_generic_csv(filepath, format_spec, rules, source_name='CSV',
                      decimal_separator='.', transforms=None, data_sources=None):
    """
    Parse a CSV file using a custom format specification.

    Args:
        filepath: Path to the CSV file
        format_spec: FormatSpec defining column mappings (supports delimiter option)
        rules: Merchant categorization rules
        source_name: Name to use for transaction source (default: 'CSV')
        decimal_separator: Character used as decimal separator ('.' or ',')
        transforms: Optional list of (field_path, expression) tuples for field transforms
        data_sources: Optional dict mapping source names to list of row dicts (for cross-source queries)

    Supported delimiters (via format_spec.delimiter):
        - None or ',': Standard CSV (comma-delimited)
        - 'tab' or '\\t': Tab-separated values
        - 'regex:PATTERN': Regex with capture groups for columns

    Returns:
        ParseResult with transactions list and skipped_rows list
    """
    transactions = []
    skipped_rows = []

    # Get delimiter from format spec
    delimiter = getattr(format_spec, 'delimiter', None)

    # Calculate max required column once
    required_cols = [format_spec.date_column, format_spec.amount_column]
    if format_spec.description_column is not None:
        required_cols.append(format_spec.description_column)
    if format_spec.custom_captures:
        required_cols.extend(format_spec.custom_captures.values())
    if format_spec.extra_fields:
        required_cols.extend(format_spec.extra_fields.values())
    max_col = max(required_cols)

    for line_num, row, raw_line, iter_skip_reason in _iter_rows_with_delimiter(
            filepath, delimiter, format_spec.has_header):
        # Handle skips from the iterator (regex mode)
        if iter_skip_reason == 'empty_line':
            # Don't report empty lines as errors - they're expected
            continue
        if iter_skip_reason == 'regex_mismatch':
            skipped_rows.append(SkippedRow(
                filepath=filepath,
                line_number=line_num,
                reason='regex_mismatch',
                message=f"Line doesn't match format pattern",
                raw_data=raw_line,
            ))
            continue

        try:
            # Ensure row has enough columns
            if len(row) <= max_col:
                skipped_rows.append(SkippedRow(
                    filepath=filepath,
                    line_number=line_num,
                    reason='insufficient_columns',
                    message=f"Expected {max_col + 1} columns, got {len(row)}",
                    raw_data=raw_line,
                ))
                continue

            # Extract values
            date_str = row[format_spec.date_column].strip()
            amount_str = row[format_spec.amount_column].strip()

            # Build description from either mode
            # Also capture custom fields for use in rule expressions (field.name)
            captures = {}
            if format_spec.description_column is not None:
                # Mode 1: Simple {description} with optional extra fields
                description = row[format_spec.description_column].strip()
                # Capture extra fields (e.g., {cardholder}) for rule expressions
                if format_spec.extra_fields:
                    for name, col_idx in format_spec.extra_fields.items():
                        captures[name] = row[col_idx].strip() if col_idx < len(row) else ''
            else:
                # Mode 2: Custom captures + template
                for name, col_idx in format_spec.custom_captures.items():
                    captures[name] = row[col_idx].strip() if col_idx < len(row) else ''
                description = format_spec.description_template.format(**captures)

            # Check for empty required fields
            empty_fields = []
            if not date_str:
                empty_fields.append('date')
            if not description:
                empty_fields.append('description')
            if not amount_str:
                empty_fields.append('amount')
            if empty_fields:
                skipped_rows.append(SkippedRow(
                    filepath=filepath,
                    line_number=line_num,
                    reason='empty_required_field',
                    message=f"Empty {', '.join(empty_fields)} field",
                    raw_data=raw_line,
                ))
                continue

            # Parse date - handle optional day suffix (e.g., "01/02/2017  Mon")
            # Only strip trailing text if the date format doesn't contain spaces
            # (formats like "%d %b %y" for "30 Dec 25" need the spaces preserved)
            date_str_to_parse = date_str
            if ' ' not in format_spec.date_format:
                date_str_to_parse = date_str.split()[0]  # Take just the date part
            try:
                date = datetime.strptime(date_str_to_parse, format_spec.date_format)
            except ValueError:
                skipped_rows.append(SkippedRow(
                    filepath=filepath,
                    line_number=line_num,
                    reason='date_parse_error',
                    message=f"Cannot parse date '{date_str}' with format {format_spec.date_format}",
                    raw_data=raw_line,
                ))
                continue

            # Parse amount (handle locale-specific formats)
            try:
                amount = parse_amount(amount_str, decimal_separator)
            except ValueError:
                skipped_rows.append(SkippedRow(
                    filepath=filepath,
                    line_number=line_num,
                    reason='amount_parse_error',
                    message=f"Cannot parse amount '{amount_str}'",
                    raw_data=raw_line,
                ))
                continue

            # Apply amount modifier if specified
            if format_spec.abs_amount:
                # Absolute value: all amounts become positive (for mixed-sign sources)
                amount = abs(amount)
            elif format_spec.negate_amount:
                # Negate: flip sign (for credit cards where positive = charge)
                amount = -amount

            # Apply field transforms before zero-amount check
            # This allows transforms like field.amount = field.amount + field.fee
            # to rescue transactions where amount=0 but fee>0
            transform_raw_values = {}
            if transforms:
                from .merchant_utils import apply_transforms
                pre_txn = {
                    'description': description,
                    'amount': amount,
                    'date': date,
                    'field': captures if captures else None,
                    'source': format_spec.source_name or source_name,
                }
                apply_transforms(pre_txn, transforms)
                amount = pre_txn.get('amount', amount)
                description = pre_txn.get('description', description)
                # Preserve raw values from transforms
                transform_raw_values = {k: v for k, v in pre_txn.items() if k.startswith('_raw_')}

            # Skip zero amounts (after transforms have been applied)
            if amount == 0:
                skipped_rows.append(SkippedRow(
                    filepath=filepath,
                    line_number=line_num,
                    reason='zero_amount',
                    message=f"Zero amount (after transforms)",
                    raw_data=raw_line,
                ))
                continue

            # Track if this is a credit (negative amount = income/refund)
            is_credit = amount < 0

            # Normalize merchant
            merchant, category, subcategory, match_info = normalize_merchant(
                description, rules, amount=amount, txn_date=date.date(),
                field=captures if captures else None,
                data_source=format_spec.source_name or source_name,
                transforms=None,  # Already applied above
                data_sources=data_sources,
            )

            txn = {
                'date': date,
                'raw_description': description,
                'description': merchant,
                'amount': amount,
                'merchant': merchant,
                'category': category,
                'subcategory': subcategory,
                'source': format_spec.source_name or source_name,
                'is_credit': is_credit,
                'match_info': match_info,
                'tags': match_info.get('tags', []) if match_info else [],
                'excluded': None,  # No auto-exclusion; use rules to categorize
                'field': captures if captures else None,  # Custom CSV captures for rule expressions
            }
            # Add _raw_* keys from transforms
            if transform_raw_values:
                for key, value in transform_raw_values.items():
                    txn[key] = value
            # Add _raw_* keys from normalize_merchant (e.g., _raw_description)
            if match_info and match_info.get('raw_values'):
                for key, value in match_info['raw_values'].items():
                    txn[key] = value
            # Surface CSV captures named in report_fields, skipping blanks so a
            # transaction without the field shows no detail badge at all
            extra_fields = {}
            if format_spec.report_fields:
                extra_fields = {
                    name: captures[name]
                    for name in format_spec.report_fields
                    if captures.get(name)
                }
            # Add extra_fields from field: directives in .rules files (these win)
            if match_info and match_info.get('extra_fields'):
                extra_fields.update(match_info['extra_fields'])
            if extra_fields:
                txn['extra_fields'] = extra_fields
            # Apply transform_description if set
            if match_info and match_info.get('transform_description'):
                txn['original_description'] = txn.get('raw_description', txn.get('description', ''))
                txn['description'] = match_info['transform_description']
            transactions.append(txn)

        except (ValueError, IndexError) as e:
            # Catch any remaining parse errors
            skipped_rows.append(SkippedRow(
                filepath=filepath,
                line_number=line_num,
                reason='parse_exception',
                message=str(e) if str(e) else f"{type(e).__name__}",
                raw_data=raw_line,
            ))
            continue

    return ParseResult(transactions=transactions, skipped_rows=skipped_rows)


def _detect_date_format(date_values):
    """
    Detect the date format from sample values.

    Args:
        date_values: List of date strings to analyze

    Returns:
        Tuple of (format_string, description) e.g. ('%m/%d/%y', 'MM/DD/YY')
    """
    # Date format patterns - order matters: more specific patterns first
    date_patterns = [
        (r'^\d{1,2}/\d{1,2}/\d{4}$', '%m/%d/%Y', 'MM/DD/YYYY'),
        (r'^\d{1,2}/\d{1,2}/\d{2}$', '%m/%d/%y', 'MM/DD/YY'),
        (r'^\d{4}-\d{2}-\d{2}$', '%Y-%m-%d', 'YYYY-MM-DD (ISO)'),
        (r'^\d{1,2}-\d{1,2}-\d{4}$', '%m-%d-%Y', 'MM-DD-YYYY'),
        (r'^\d{1,2}-\d{1,2}-\d{2}$', '%m-%d-%y', 'MM-DD-YY'),
        (r'^\d{1,2}\.\d{1,2}\.\d{4}$', '%d.%m.%Y', 'DD.MM.YYYY (European)'),
        (r'^\d{1,2}\.\d{1,2}\.\d{2}$', '%d.%m.%y', 'DD.MM.YY (European)'),
    ]

    # Filter out empty values
    non_empty = [v.strip() for v in date_values if v.strip()]
    if not non_empty:
        return '%m/%d/%Y', 'MM/DD/YYYY (default)'

    # Check each pattern
    for pattern, fmt, desc in date_patterns:
        matches = sum(1 for v in non_empty if re.match(pattern, v))
        if matches >= len(non_empty) * 0.8:  # 80% threshold
            return fmt, desc

    # Fallback to default
    return '%m/%d/%Y', 'MM/DD/YYYY (default)'


def auto_detect_csv_format(filepath):
    """
    Attempt to auto-detect CSV column mapping from headers and date format from data.

    Looks for common header names:
    - Date: 'date', 'trans date', 'transaction date', 'posting date'
    - Description: 'description', 'merchant', 'payee', 'memo', 'name'
    - Amount: 'amount', 'debit', 'charge', 'transaction amount'

    Also analyzes actual date values to detect the correct format (e.g., %y vs %Y).

    Returns:
        FormatSpec with detected mappings

    Raises:
        ValueError: If required columns cannot be detected
    """
    # Common header patterns (case-insensitive, partial match)
    DATE_PATTERNS = ['date', 'trans date', 'transaction date', 'posting date', 'trans_date']
    DESC_PATTERNS = ['description', 'merchant', 'payee', 'memo', 'name', 'merchant name']
    AMOUNT_PATTERNS = ['amount', 'debit', 'charge', 'transaction amount', 'payment']

    def match_header(header, patterns):
        header_lower = header.lower().strip()
        return any(p in header_lower for p in patterns)

    # First pass: detect headers and dialect
    with open(filepath, 'r', encoding='utf-8') as f:
        # Detect CSV dialect (delimiter, quotechar, etc.)
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = None

        reader = csv.reader(f, dialect) if dialect else csv.reader(f)
        headers = next(reader, None)

        if not headers:
            raise ValueError("CSV file is empty or has no headers")

        # Read some data rows for date format detection
        data_rows = []
        for i, row in enumerate(reader):
            if i >= 20:  # Sample first 20 data rows
                break
            data_rows.append(row)

    # Determine delimiter for FormatSpec (None means comma/default)
    detected_delimiter = None
    if dialect and dialect.delimiter != ',':
        detected_delimiter = dialect.delimiter

    # Find column indices
    date_col = desc_col = amount_col = None

    for idx, header in enumerate(headers):
        if date_col is None and match_header(header, DATE_PATTERNS):
            date_col = idx
        elif desc_col is None and match_header(header, DESC_PATTERNS):
            desc_col = idx
        elif amount_col is None and match_header(header, AMOUNT_PATTERNS):
            amount_col = idx

    # Validate required columns found
    missing = []
    if date_col is None:
        missing.append('date')
    if desc_col is None:
        missing.append('description')
    if amount_col is None:
        missing.append('amount')

    if missing:
        raise ValueError(
            f"Could not auto-detect required columns: {missing}. "
            f"Headers found: {headers}"
        )

    # Detect date format from actual data
    date_values = [row[date_col] for row in data_rows if date_col < len(row)]
    date_format, _ = _detect_date_format(date_values)

    return FormatSpec(
        date_column=date_col,
        date_format=date_format,
        description_column=desc_col,
        amount_column=amount_col,
        has_header=True,
        delimiter=detected_delimiter,
    )
