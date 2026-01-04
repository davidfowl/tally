"""
CSV/Transaction Parsing - Parse various bank statement formats.

This module handles parsing of CSV files and other transaction formats.
"""

import csv
import re
from datetime import datetime
from collections import Counter

from .merchant_utils import normalize_merchant
from .format_parser import FormatSpec


def detect_currency(amount_str):
    """Detect currency symbol from an amount string.
    
    Args:
        amount_str: String like "$1,234.56" or "€100.00" or "£50"
        
    Returns:
        Currency symbol found (e.g., '$', '€', '£', '¥') or None if none detected
    """
    # Check for common currency symbols
    # Order matters - check multi-char symbols first, then single chars
    currency_patterns = [
        (r'€', '€'),
        (r'\$', '$'),
        (r'£', '£'),
        (r'¥', '¥'),
        (r'\blei\b', 'lei'),  # Romanian Leu (plural, suffix)
        (r'\bleu\b', 'lei'),  # Romanian Leu (singular, suffix)
        (r'\bRON\b', 'RON'),  # Romanian Leu (code)
        (r'kr\b', 'kr'),  # Swedish/Norwegian/Danish krone (suffix)
        (r'\bzł\b', 'zł'),  # Polish złoty (suffix)
        (r'\bPLN\b', 'PLN'),  # Polish złoty (code)
        (r'\bEUR\b', 'EUR'),  # Euro (code)
        (r'\bGBP\b', 'GBP'),  # British Pound (code)
        (r'\bUSD\b', 'USD'),  # US Dollar (code)
        (r'\bJPY\b', 'JPY'),  # Japanese Yen (code)
    ]
    
    amount_str = amount_str.strip()
    
    for pattern, symbol in currency_patterns:
        if re.search(pattern, amount_str, re.IGNORECASE):
            return symbol
    
    return None


def currency_to_format(currency_symbol):
    """Convert a detected currency symbol to a currency format string.
    
    Args:
        currency_symbol: Currency symbol like '$', '€', '£', '¥', 'kr', 'zł', etc.
        
    Returns:
        Currency format string like "${amount}", "€{amount}", "{amount} zł", etc.
    """
    # Map currency symbols to their display formats
    currency_format_map = {
        '$': '${amount}',
        'USD': '${amount}',
        '€': '€{amount}',
        'EUR': '€{amount}',
        '£': '£{amount}',
        'GBP': '£{amount}',
        '¥': '¥{amount}',
        'JPY': '¥{amount}',
        'lei': '{amount} lei',  # Romanian Leu (suffix)
        'RON': '{amount} lei',  # Romanian Leu (code -> suffix format)
        'kr': '{amount} kr',  # Swedish/Norwegian/Danish krone (suffix)
        'zł': '{amount} zł',  # Polish złoty (suffix)
        'PLN': '{amount} zł',  # Polish złoty (code -> suffix format)
    }
    
    return currency_format_map.get(currency_symbol, '${amount}')  # Default to USD if unknown


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
    # Remove currency codes and suffixes
    amount_str = re.sub(r'\b(?:kr|zł|lei|leu|PLN|RON|EUR|GBP|USD|JPY)\b', '', amount_str, flags=re.IGNORECASE).strip()

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


def extract_location(description):
    """Extract state/country code from transaction description."""
    # Pattern: ends with 2-letter code (state or country)
    match = re.search(r'\s+([A-Z]{2})\s*$', description)
    if match:
        return match.group(1)
    return None


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
                location = extract_location(row['Description'])

                transactions.append({
                    'date': date,
                    'raw_description': row['Description'],
                    'description': row['Description'],
                    'amount': amount,
                    'merchant': merchant,
                    'category': category,
                    'subcategory': subcategory,
                    'source': 'AMEX',
                    'location': location,
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
                location = extract_location(description)

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
                    'location': location,
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
        List of column values for each row
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        if delimiter and delimiter == 'tab':
            delimiter = '\t'
        if delimiter and delimiter.startswith('regex:'):
            # Regex-based parsing
            pattern = re.compile(delimiter[6:])  # Strip 'regex:' prefix
            for i, line in enumerate(f):
                if has_header and i == 0:
                    continue
                line = line.strip()
                if not line:
                    continue
                match = pattern.match(line)
                if match:
                    yield list(match.groups())
        elif delimiter and len(delimiter) == 1:
            reader = csv.reader(f, delimiter=delimiter)
            if has_header:
                next(reader, None)
            for row in reader:
                yield row
        else:
            # Standard CSV (comma-delimited)
            reader = csv.reader(f)
            if has_header:
                next(reader, None)
            for row in reader:
                yield row


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
        List of transaction dictionaries
    """
    transactions = []

    # Get delimiter from format spec
    delimiter = getattr(format_spec, 'delimiter', None)

    for row in _iter_rows_with_delimiter(filepath, delimiter, format_spec.has_header):
        try:
            # Ensure row has enough columns
            required_cols = [format_spec.date_column, format_spec.amount_column]
            if format_spec.description_column is not None:
                required_cols.append(format_spec.description_column)
            if format_spec.custom_captures:
                required_cols.extend(format_spec.custom_captures.values())
            if format_spec.extra_fields:
                required_cols.extend(format_spec.extra_fields.values())
            if format_spec.location_column is not None:
                required_cols.append(format_spec.location_column)
            max_col = max(required_cols)

            if len(row) <= max_col:
                continue  # Skip malformed rows

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

            # Skip empty rows
            if not date_str or not description or not amount_str:
                continue

            # Parse date - handle optional day suffix (e.g., "01/02/2017  Mon")
            # Only strip trailing text if the date format doesn't contain spaces
            # (formats like "%d %b %y" for "30 Dec 25" need the spaces preserved)
            if ' ' not in format_spec.date_format:
                date_str = date_str.split()[0]  # Take just the date part
            date = datetime.strptime(date_str, format_spec.date_format)

            # Parse amount (handle locale-specific formats)
            amount = parse_amount(amount_str, decimal_separator)

            # Apply amount modifier if specified
            if format_spec.abs_amount:
                # Absolute value: all amounts become positive (for mixed-sign sources)
                amount = abs(amount)
            elif format_spec.negate_amount:
                # Negate: flip sign (for credit cards where positive = charge)
                amount = -amount

            # Skip zero amounts
            if amount == 0:
                continue

            # Track if this is a credit (negative amount = income/refund)
            is_credit = amount < 0

            # Extract location
            location = None
            if format_spec.location_column is not None:
                location = row[format_spec.location_column].strip()
            if not location:
                location = extract_location(description)

            # Normalize merchant
            merchant, category, subcategory, match_info = normalize_merchant(
                description, rules, amount=amount, txn_date=date.date(),
                field=captures if captures else None,
                data_source=format_spec.source_name or source_name,
                transforms=transforms,
                location=location,
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
                'location': location,
                'is_credit': is_credit,
                'match_info': match_info,
                'tags': match_info.get('tags', []) if match_info else [],
                'excluded': None,  # No auto-exclusion; use rules to categorize
                'field': captures if captures else None,  # Custom CSV captures for rule expressions
            }
            # Add _raw_* keys from transforms (e.g., _raw_description)
            if match_info and match_info.get('raw_values'):
                for key, value in match_info['raw_values'].items():
                    txn[key] = value
            # Add extra_fields from field: directives in .rules files
            if match_info and match_info.get('extra_fields'):
                txn['extra_fields'] = match_info['extra_fields']
            transactions.append(txn)

        except (ValueError, IndexError):
            # Skip problematic rows
            continue

    return transactions


def detect_currencies_from_file(filepath, format_spec=None, parser_type='generic', decimal_separator='.'):
    """Detect currencies from a transaction file by scanning amount columns.
    
    This is a lightweight scan that only reads amount values to detect currency symbols,
    without parsing full transactions. Use this when you only need currency detection.
    
    Args:
        filepath: Path to the CSV/transaction file
        format_spec: FormatSpec for generic CSV parsing (required if parser_type='generic')
        parser_type: Type of parser ('amex', 'boa', or 'generic')
        decimal_separator: Character used as decimal separator ('.' or ',')
        
    Returns:
        List of detected currency symbols (may contain duplicates)
    """
    detected_currencies = []
    
    if parser_type == 'amex':
        # AMEX CSV format
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    amount_str = row.get('Amount', '')
                    currency = detect_currency(amount_str)
                    if currency:
                        detected_currencies.append(currency)
        except (ValueError, KeyError, IOError):
            pass
            
    elif parser_type == 'boa':
        # BOA statement format
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    # Allow currency symbols in amount field for detection
                    match = re.match(
                        r'^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+([$€£¥\s\-\d,]+\.\d{2}|\d+\.\d{2}\s*(?:lei|leu|kr|zł|PLN|RON|EUR|GBP|USD|JPY)?)\s+([-\d,]+\.\d{2})$',
                        line.strip(),
                        re.IGNORECASE
                    )
                    if match:
                        amount_str = match.group(3)
                        currency = detect_currency(amount_str)
                        if currency:
                            detected_currencies.append(currency)
        except (ValueError, IOError):
            pass
            
    elif parser_type == 'generic' and format_spec:
        # Generic CSV format
        try:
            delimiter = getattr(format_spec, 'delimiter', None)
            for row in _iter_rows_with_delimiter(filepath, delimiter, format_spec.has_header):
                try:
                    if len(row) <= format_spec.amount_column:
                        continue
                    amount_str = row[format_spec.amount_column].strip()
                    currency = detect_currency(amount_str)
                    if currency:
                        detected_currencies.append(currency)
                except (ValueError, IndexError):
                    continue
        except (ValueError, IOError):
            pass
    
    return detected_currencies


def auto_detect_csv_format(filepath):
    """
    Attempt to auto-detect CSV column mapping from headers.

    Looks for common header names:
    - Date: 'date', 'trans date', 'transaction date', 'posting date'
    - Description: 'description', 'merchant', 'payee', 'memo', 'name'
    - Amount: 'amount', 'debit', 'charge', 'transaction amount'
    - Location: 'location', 'city', 'state', 'city/state'

    Returns:
        FormatSpec with detected mappings

    Raises:
        ValueError: If required columns cannot be detected
    """
    # Common header patterns (case-insensitive, partial match)
    DATE_PATTERNS = ['date', 'trans date', 'transaction date', 'posting date', 'trans_date']
    DESC_PATTERNS = ['description', 'merchant', 'payee', 'memo', 'name', 'merchant name']
    AMOUNT_PATTERNS = ['amount', 'debit', 'charge', 'transaction amount', 'payment']
    LOCATION_PATTERNS = ['location', 'city', 'state', 'city/state', 'region']

    def match_header(header, patterns):
        header_lower = header.lower().strip()
        return any(p in header_lower for p in patterns)

    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader, None)

        if not headers:
            raise ValueError("CSV file is empty or has no headers")

    # Find column indices
    date_col = desc_col = amount_col = location_col = None

    for idx, header in enumerate(headers):
        if date_col is None and match_header(header, DATE_PATTERNS):
            date_col = idx
        elif desc_col is None and match_header(header, DESC_PATTERNS):
            desc_col = idx
        elif amount_col is None and match_header(header, AMOUNT_PATTERNS):
            amount_col = idx
        elif location_col is None and match_header(header, LOCATION_PATTERNS):
            location_col = idx

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

    return FormatSpec(
        date_column=date_col,
        date_format='%m/%d/%Y',  # Default format
        description_column=desc_col,
        amount_column=amount_col,
        location_column=location_col,
        has_header=True
    )
