"""
Spending Analyzer - Core analysis logic.

Analyzes AMEX and BOA transactions using merchant categorization rules.
"""

import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def get_template_dir():
    """Get the directory containing template files.

    When running as a PyInstaller bundle, files are in sys._MEIPASS/tally/.
    Otherwise, they're in the same directory as this module.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle
        return Path(sys._MEIPASS) / 'tally'
    else:
        # Running as normal Python
        return Path(__file__).parent

from .merchant_utils import normalize_merchant, get_tag_only_rules, apply_tag_rules
from .format_parser import FormatSpec
from . import section_engine

# Try to import sentence_transformers for semantic search
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False


# ============================================================================
# CURRENCY FORMATTING
# ============================================================================

def format_currency(amount: float, currency_format: str = "${amount}") -> str:
    """Format amount with currency symbol/format (no decimals).

    Args:
        amount: The amount to format
        currency_format: Format string with {amount} placeholder, e.g. "${amount}" or "{amount} zł"

    Returns:
        Formatted currency string, e.g. "$1,234" or "1,234 zł"
    """
    formatted_num = f"{amount:,.0f}"
    return currency_format.format(amount=formatted_num)


def format_currency_decimal(amount: float, currency_format: str = "${amount}") -> str:
    """Format amount with currency symbol/format (with 2 decimal places).

    Args:
        amount: The amount to format
        currency_format: Format string with {amount} placeholder

    Returns:
        Formatted currency string with decimals, e.g. "$1,234.56"
    """
    formatted_num = f"{amount:,.2f}"
    return currency_format.format(amount=formatted_num)


# ============================================================================
# DATA PARSING
# ============================================================================

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


def extract_location(description):
    """Extract state/country code from transaction description."""
    # Pattern: ends with 2-letter code (state or country)
    match = re.search(r'\s+([A-Z]{2})\s*$', description)
    if match:
        return match.group(1)
    return None


def is_travel_location(location, home_locations):
    """Determine if a location represents travel (away from home).

    Only international locations (outside US) are automatically considered travel.
    Domestic out-of-state transactions can be marked as travel via merchant rules
    (e.g., add ".*HI$,Hawaii Trip,Travel,Hawaii" to merchant_categories.csv).

    Args:
        location: 2-letter location code (state or country)
        home_locations: Set of location codes considered "home"

    Returns:
        True if this is a travel location, False otherwise
    """
    if not location:
        return False

    # US state codes
    us_states = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
        'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
        'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
        'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
        'DC', 'PR', 'VI', 'GU'
    }

    location = location.upper()

    # International (not a US state) = travel unless explicitly in home_locations
    if location not in us_states:
        return location not in home_locations

    # Domestic US states = NOT travel by default
    # Users can mark specific locations as travel via merchant_categories.csv
    return False


def parse_amex(filepath, rules, home_locations=None):
    """Parse AMEX CSV file and return list of transactions.

    DEPRECATED: Use format strings instead. This parser will be removed in a future release.
    """
    home_locations = home_locations or set()
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
                    'is_travel': is_travel_location(location, home_locations),
                    'match_info': match_info,
                    'tags': match_info.get('tags', []) if match_info else [],
                })
            except (ValueError, KeyError):
                continue

    return transactions


def parse_boa(filepath, rules, home_locations=None):
    """Parse BOA statement file and return list of transactions.

    DEPRECATED: Use format strings instead. This parser will be removed in a future release.
    """
    home_locations = home_locations or set()
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
                    'is_travel': is_travel_location(location, home_locations),
                    'tags': match_info.get('tags', []) if match_info else [],
                })
            except ValueError:
                continue

    return transactions


def _iter_rows_with_delimiter(filepath, delimiter, has_header):
    """Iterate over rows, handling different delimiter types.

    Args:
        filepath: Path to the file
        delimiter: None for CSV, 'tab' for TSV, or 'regex:pattern' for regex
        has_header: Whether to skip the first line

    Yields:
        List of column values for each row
    """
    with open(filepath, 'r', encoding='utf-8') as f:
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
        elif delimiter == 'tab' or delimiter == '\t':
            # Tab-separated
            reader = csv.reader(f, delimiter='\t')
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


def parse_generic_csv(filepath, format_spec, rules, home_locations=None, source_name='CSV',
                      decimal_separator='.', transforms=None):
    """
    Parse a CSV file using a custom format specification.

    Args:
        filepath: Path to the CSV file
        format_spec: FormatSpec defining column mappings (supports delimiter option)
        rules: Merchant categorization rules
        home_locations: Set of location codes considered "home"
        source_name: Name to use for transaction source (default: 'CSV')
        decimal_separator: Character used as decimal separator ('.' or ',')
        transforms: Optional list of (field_path, expression) tuples for field transforms

    Supported delimiters (via format_spec.delimiter):
        - None or ',': Standard CSV (comma-delimited)
        - 'tab' or '\\t': Tab-separated values
        - 'regex:PATTERN': Regex with capture groups for columns

    Returns:
        List of transaction dictionaries
    """
    home_locations = home_locations or set()
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
                'is_travel': is_travel_location(location, home_locations),
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
            transactions.append(txn)

        except (ValueError, IndexError):
            # Skip problematic rows
            continue

    return transactions


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


# ============================================================================


def analyze_transactions(transactions):
    """Analyze transactions and return summary statistics."""
    by_category = defaultdict(lambda: {'count': 0, 'total': 0})
    by_merchant = defaultdict(lambda: {
        'count': 0,
        'total': 0,
        'category': '',
        'subcategory': '',
        'months': set(),  # Track which months this merchant appears
        'monthly_amounts': defaultdict(float),  # Amount per month
        'max_payment': 0,  # Largest single payment
        'payments': [],  # All individual payment amounts
        'transactions': [],  # Individual transactions for drill-down
        'tags': set(),  # Collect all tags from matching rules
        'raw_descriptions': defaultdict(int),  # Track raw description variations
    })
    by_month = defaultdict(float)

    # Track excluded transactions separately (for transparency in UI)
    excluded_transactions = []

    # Special tags that affect spending analysis
    EXCLUDE_TAGS = {'income', 'transfer'}  # Excluded from spending totals

    for txn in transactions:
        # Check for special tags: income, transfer -> exclude from spending
        txn_tags = set(t.lower() for t in txn.get('tags', []))
        excluded_reason = txn.get('excluded')
        if not excluded_reason and (txn_tags & EXCLUDE_TAGS):
            excluded_reason = 'tagged-' + next(iter(txn_tags & EXCLUDE_TAGS))

        if excluded_reason:
            excluded_transactions.append({
                'date': txn['date'].strftime('%m/%d'),
                'month': txn['date'].strftime('%Y-%m'),
                'description': txn.get('raw_description', txn['description']),
                'merchant': txn['merchant'],
                'amount': txn['amount'],
                'category': txn['category'],
                'subcategory': txn['subcategory'],
                'source': txn['source'],
                'location': txn.get('location'),
                'tags': txn.get('tags', []),
                'excluded_reason': excluded_reason,
            })
            continue  # Don't include in spending totals
        key = (txn['category'], txn['subcategory'])
        by_category[key]['count'] += 1
        by_category[key]['total'] += txn['amount']

        month_key = txn['date'].strftime('%Y-%m')

        # Always track by merchant - is_travel flag determines classification
        by_merchant[txn['merchant']]['count'] += 1
        by_merchant[txn['merchant']]['total'] += txn['amount']
        by_merchant[txn['merchant']]['category'] = txn['category']
        by_merchant[txn['merchant']]['subcategory'] = txn['subcategory']
        by_merchant[txn['merchant']]['months'].add(month_key)
        by_merchant[txn['merchant']]['monthly_amounts'][month_key] += txn['amount']
        by_merchant[txn['merchant']]['payments'].append(txn['amount'])
        by_merchant[txn['merchant']]['transactions'].append({
            'date': txn['date'].strftime('%m/%d'),
            'month': month_key,
            'description': txn.get('raw_description', txn['description']),
            'amount': txn['amount'],
            'source': txn['source'],
            'location': txn.get('location'),
            'tags': txn.get('tags', [])
        })
        # Track max payment
        if txn['amount'] > by_merchant[txn['merchant']]['max_payment']:
            by_merchant[txn['merchant']]['max_payment'] = txn['amount']
        # Mark merchant as travel if ANY transaction is travel (location-based)
        if txn.get('is_travel'):
            by_merchant[txn['merchant']]['is_travel'] = True
        # Store match info (pattern that matched) - first transaction sets this
        if 'match_info' not in by_merchant[txn['merchant']] and txn.get('match_info'):
            by_merchant[txn['merchant']]['match_info'] = txn['match_info']
        # Collect tags from all transactions
        by_merchant[txn['merchant']]['tags'].update(txn.get('tags', []))
        # Track raw description variations
        raw_desc = txn.get('raw_description', txn.get('description', ''))
        by_merchant[txn['merchant']]['raw_descriptions'][raw_desc] += 1

        by_month[month_key] += txn['amount']

    # Calculate months active and monthly average for each merchant
    all_months = set(by_month.keys())
    num_months = len(all_months) if all_months else 12

    for merchant, data in by_merchant.items():
        data['months_active'] = len(data['months'])
        data['avg_when_active'] = data['total'] / data['months_active'] if data['months_active'] > 0 else 0

        # Calculate consistency: are monthly amounts similar or lumpy?
        monthly_vals = list(data['monthly_amounts'].values())
        if len(monthly_vals) >= 2:
            avg = sum(monthly_vals) / len(monthly_vals)
            variance = sum((x - avg) ** 2 for x in monthly_vals) / len(monthly_vals)
            std_dev = variance ** 0.5
            # Coefficient of variation: std_dev / mean (0 = perfectly consistent, >0.5 = lumpy)
            data['cv'] = std_dev / avg if avg > 0 else 0
            data['is_consistent'] = data['cv'] < 0.3  # Less than 30% variation = consistent
        else:
            data['cv'] = 0
            data['is_consistent'] = True

        data['months'] = sorted(list(data['months']))

    # =========================================================================
    # CALCULATE MONTHLY VALUES
    # =========================================================================
    # All merchants use YTD/12 for monthly value calculation
    # Custom grouping/views are defined in views.rules
    for merchant, data in by_merchant.items():
        data['classification'] = 'variable'
        data['calc_type'] = '/12'
        monthly_value = data['total'] / 12
        data['monthly_value'] = monthly_value
        data['calc_reasoning'] = 'Spread over 12 months'
        data['calc_formula'] = f"total / 12 = {data['total']:.2f} / 12 = {monthly_value:.2f}"
        data['reasoning'] = {
            'category': data.get('category', ''),
            'subcategory': data.get('subcategory', ''),
            'months_active': data.get('months_active', 1),
            'num_months': num_months,
            'cv': round(data.get('cv', 0), 2),
        }

    # Legacy bucket support - all merchants go into variable
    # Views.rules handles custom grouping
    monthly_merchants = {}
    annual_merchants = {}
    periodic_merchants = {}
    travel_merchants = {}
    one_off_merchants = {}
    variable_merchants = dict(by_merchant)

    # Bucket totals - all spending is variable now (views.rules handles custom grouping)
    monthly_total = 0
    annual_total = 0
    periodic_total = 0
    travel_total = 0
    one_off_total = 0
    variable_total = sum(d['total'] for d in variable_merchants.values())

    # Monthly value averages - all in variable
    monthly_avg = 0
    annual_monthly = 0
    periodic_monthly = 0
    variable_monthly = sum(d.get('monthly_value', 0) for d in variable_merchants.values())

    # Calculate totals only from non-excluded transactions
    included_transactions = [t for t in transactions if not t.get('excluded')]

    return {
        'by_category': dict(by_category),
        'by_merchant': {k: dict(v) for k, v in by_merchant.items()},
        'by_month': dict(by_month),
        'total': sum(t['amount'] for t in included_transactions),
        'count': len(included_transactions),
        'num_months': num_months,
        # Classified merchants
        'monthly_merchants': monthly_merchants,
        'annual_merchants': annual_merchants,
        'periodic_merchants': periodic_merchants,
        'travel_merchants': travel_merchants,
        'one_off_merchants': one_off_merchants,
        'variable_merchants': variable_merchants,
        # Totals (YTD)
        'monthly_total': monthly_total,
        'annual_total': annual_total,
        'periodic_total': periodic_total,
        'travel_total': travel_total,
        'one_off_total': one_off_total,
        'variable_total': variable_total,
        # True monthly averages
        'monthly_avg': monthly_avg,         # Avg when active
        'annual_monthly': annual_monthly,   # Annual / 12
        'periodic_monthly': periodic_monthly, # Periodic / 12
        'variable_monthly': variable_monthly,
        'true_monthly': monthly_avg + annual_monthly + periodic_monthly + variable_monthly,
        # Excluded transactions (for UI transparency)
        'excluded_transactions': excluded_transactions,
        'excluded_count': len(excluded_transactions),
        'excluded_total': sum(t['amount'] for t in excluded_transactions),
    }


def classify_by_sections(by_merchant, sections_config, num_months=12):
    """
    Classify merchants into user-defined sections.

    Args:
        by_merchant: Dict of merchant_name -> merchant data (from analyze_transactions)
        sections_config: SectionConfig from section_engine
        num_months: Number of months in the data period

    Returns:
        Dict mapping section_name -> list of (merchant_name, merchant_data) tuples
    """
    if sections_config is None:
        return {}

    # Collect all unique months across all transactions for period_data
    all_months = set()
    all_years = set()

    # Convert by_merchant to the format expected by section_engine
    merchant_groups = []
    for merchant_name, data in by_merchant.items():
        # Build transactions list for the section filter
        # The 'transactions' key already has the individual transactions
        txns = data.get('transactions', [])

        # Convert transaction format for section_engine
        section_txns = []
        for txn in txns:
            txn_date = datetime.strptime(txn['month'] + '-15', '%Y-%m-%d')
            section_txns.append({
                'amount': txn['amount'],
                'date': txn_date,
                'category': data.get('category', ''),
                'subcategory': data.get('subcategory', ''),
                'merchant': merchant_name,
                'tags': list(data.get('tags', [])),
            })
            # Track global periods
            all_months.add(txn['month'])
            all_years.add(txn_date.year)

        merchant_groups.append({
            'merchant': merchant_name,
            'category': data.get('category', ''),
            'subcategory': data.get('subcategory', ''),
            'transactions': section_txns,
            'data': data,  # Keep reference to original data
        })

    # Compute period_data from all transactions
    period_data = {
        'month': len(all_months) if all_months else num_months,
        'year': len(all_years) if all_years else 1,
    }

    # Classify using section_engine
    section_results = section_engine.classify_merchants(
        sections_config,
        merchant_groups,
        num_months,
        period_data=period_data,
    )

    # Convert results back to (merchant_name, data) tuples
    result = {}
    for section_name, merchants in section_results.items():
        result[section_name] = [
            (m['merchant'], m['data'])
            for m in merchants
        ]

    return result


def compute_section_totals(section_merchants):
    """
    Compute totals for a section.

    Args:
        section_merchants: List of (merchant_name, merchant_data) tuples

    Returns:
        Dict with section totals
    """
    total = sum(data.get('total', 0) for _, data in section_merchants)
    monthly = sum(data.get('monthly_value', 0) for _, data in section_merchants)
    count = len(section_merchants)

    return {
        'total': total,
        'monthly': monthly,
        'count': count,
        'merchants': section_merchants,
    }


# ============================================================================
# EXPORT FUNCTIONS
# ============================================================================

def build_merchant_json(merchant_name, data, verbose=0):
    """Build JSON representation of a merchant with reasoning based on verbosity level.

    Args:
        merchant_name: Name of the merchant
        data: Merchant data dictionary
        verbose: Verbosity level (0=basic, 1=trace, 2=full)

    Returns: dict suitable for JSON serialization
    """
    # Handle tags - could be a set or list
    tags = data.get('tags', [])
    if isinstance(tags, set):
        tags = sorted(tags)

    result = {
        'name': merchant_name,
        'classification': data.get('classification', 'unknown'),
        'category': data.get('category', ''),
        'subcategory': data.get('subcategory', ''),
        'tags': tags,
        'total': round(data.get('total', 0), 2),
        'count': data.get('count', 0),
        'months_active': data.get('months_active', 0),
        'monthly_value': round(data.get('monthly_value', 0), 2),
    }

    # Add reasoning (always include decision)
    reasoning = data.get('reasoning', {})
    result['reasoning'] = {
        'decision': reasoning.get('decision', ''),
    }

    # Add calculation info
    result['calculation'] = {
        'type': data.get('calc_type', ''),
        'reason': data.get('calc_reasoning', ''),
    }

    # Verbose: add decision trace and raw description variations
    if verbose >= 1:
        result['reasoning']['trace'] = reasoning.get('trace', [])
        raw_descs = data.get('raw_descriptions', {})
        if raw_descs:
            # Convert defaultdict to regular dict for JSON
            result['raw_descriptions'] = dict(raw_descs)

    # Very verbose: add thresholds, CV, and calculation formula
    if verbose >= 2:
        result['reasoning']['thresholds'] = reasoning.get('thresholds', {})
        result['reasoning']['cv'] = reasoning.get('cv', 0)
        result['reasoning']['is_consistent'] = reasoning.get('is_consistent', True)
        result['calculation']['formula'] = data.get('calc_formula', '')
        result['months'] = data.get('months', [])

    # Add pattern match info if available
    match_info = data.get('match_info')
    if match_info:
        result['pattern'] = {
            'matched': match_info.get('pattern', ''),
            'source': match_info.get('source', 'unknown'),
            'tags': match_info.get('tags', []),
        }

    return result


def export_json(stats, verbose=0, only=None, category_filter=None, merchant_filter=None):
    """Export analysis results as JSON with reasoning.

    Args:
        stats: Analysis results from analyze_transactions()
        verbose: Verbosity level (0=basic, 1=trace, 2=full)
        only: List of classifications to include (e.g., ['monthly', 'variable'])
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)

    Returns: JSON string
    """
    import json

    output = {
        'summary': {
            'total_spending': round(stats['total'], 2),
            'monthly_budget': round(stats['true_monthly'], 2),
            'num_months': stats['num_months'],
            'breakdown': {
                'monthly_recurring': round(stats['monthly_avg'], 2),
                'annual_monthly': round(stats['annual_monthly'], 2),
                'periodic_monthly': round(stats['periodic_monthly'], 2),
                'variable_monthly': round(stats['variable_monthly'], 2),
            },
            'totals': {
                'monthly': round(stats['monthly_total'], 2),
                'annual': round(stats['annual_total'], 2),
                'periodic': round(stats['periodic_total'], 2),
                'travel': round(stats['travel_total'], 2),
                'one_off': round(stats['one_off_total'], 2),
                'variable': round(stats['variable_total'], 2),
            }
        },
        'classifications': {}
    }

    # Classification sections to process
    all_sections = ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']
    sections = only if only else all_sections

    for section in sections:
        if section not in all_sections:
            continue
        merchants_dict = stats.get(f'{section}_merchants', {})
        merchants = []

        for name, data in merchants_dict.items():
            # Apply filters
            if category_filter and data.get('category') != category_filter:
                continue
            if merchant_filter and name not in merchant_filter:
                continue

            merchants.append(build_merchant_json(name, data, verbose))

        # Sort by monthly value descending
        merchants.sort(key=lambda x: x['monthly_value'], reverse=True)
        output['classifications'][section] = merchants

    return json.dumps(output, indent=2)


def export_markdown(stats, verbose=0, only=None, category_filter=None, merchant_filter=None):
    """Export analysis results as Markdown with reasoning.

    Args:
        stats: Analysis results from analyze_transactions()
        verbose: Verbosity level (0=basic, 1=trace, 2=full)
        only: List of classifications to include (e.g., ['monthly', 'variable'])
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)

    Returns: Markdown string
    """
    lines = ['# Spending Analysis\n']

    # Summary
    lines.append('## Summary\n')
    lines.append(f"- **Monthly Budget:** ${stats['true_monthly']:.2f}/mo")
    lines.append(f"- **Total Spending (YTD):** ${stats['total']:.2f}")
    lines.append(f"- **Data Period:** {stats['num_months']} months\n")

    # Classification sections to process
    all_sections = ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']
    section_names = {
        'monthly': 'Every Month',
        'annual': 'Once a Year',
        'periodic': 'A Few Times/Year',
        'travel': 'Travel Expenses',
        'one_off': 'Large One-Time',
        'variable': 'Varies by Month',
    }
    sections = only if only else all_sections

    for section in sections:
        if section not in all_sections:
            continue
        merchants_dict = stats.get(f'{section}_merchants', {})
        if not merchants_dict:
            continue

        lines.append(f"\n## {section_names.get(section, section)}\n")

        # Sort by monthly value
        sorted_merchants = sorted(
            merchants_dict.items(),
            key=lambda x: x[1].get('monthly_value', 0),
            reverse=True
        )

        for name, data in sorted_merchants:
            # Apply filters
            if category_filter and data.get('category') != category_filter:
                continue
            if merchant_filter and name not in merchant_filter:
                continue

            reasoning = data.get('reasoning', {})

            lines.append(f"### {name}")
            lines.append(f"**Classification:** {section.replace('_', ' ').title()}")
            lines.append(f"**Reason:** {reasoning.get('decision', 'N/A')}")
            lines.append(f"**Category:** {data.get('category', '')} > {data.get('subcategory', '')}")
            lines.append(f"**Monthly Value:** ${data.get('monthly_value', 0):.2f}")
            lines.append(f"**YTD Total:** ${data.get('total', 0):.2f}")
            lines.append(f"**Months Active:** {data.get('months_active', 0)}/{stats['num_months']}")

            # Verbose: add decision trace
            if verbose >= 1:
                trace = reasoning.get('trace', [])
                if trace:
                    lines.append('\n**Decision Trace:**')
                    for i, step in enumerate(trace, 1):
                        lines.append(f"  {i}. {step}")

            # Very verbose: add calculation details
            if verbose >= 2:
                lines.append(f"\n**Calculation:** {data.get('calc_type', '')} ({data.get('calc_reasoning', '')})")
                lines.append(f"  Formula: {data.get('calc_formula', '')}")
                lines.append(f"  CV: {reasoning.get('cv', 0):.2f}")
                thresholds = reasoning.get('thresholds', {})
                if thresholds:
                    lines.append(f"  Thresholds: bill={thresholds.get('bill_threshold')}, general={thresholds.get('general_threshold')}")

            lines.append('')  # Empty line between merchants

    return '\n'.join(lines)


def print_summary(stats, year=2025, filter_category=None, currency_format="${amount}"):
    """Print analysis summary."""
    # Local helper for currency formatting
    def fmt(amount):
        return format_currency(amount, currency_format)

    by_category = stats['by_category']
    monthly_merchants = stats['monthly_merchants']
    annual_merchants = stats['annual_merchants']
    periodic_merchants = stats['periodic_merchants']
    travel_merchants = stats['travel_merchants']
    one_off_merchants = stats['one_off_merchants']
    variable_merchants = stats['variable_merchants']

    # Calculate actual spending (transactions tagged income/transfer already excluded)
    actual_spending = sum(data['total'] for (cat, sub), data in by_category.items())

    # =========================================================================
    # MONTHLY BUDGET SUMMARY
    # =========================================================================
    print("=" * 80)
    print(f"{year} SPENDING ANALYSIS (Occurrence-Based)")
    print("=" * 80)

    print("\nMONTHLY BUDGET")
    print("-" * 50)
    print(f"Every Month (6+ mo):         {fmt(stats['monthly_avg']):>14}/mo")
    print(f"Varies by Month:             {fmt(stats['variable_monthly']):>14}/mo")
    print(f"                             {'-'*14}")
    print(f"TRUE MONTHLY BUDGET:         {fmt(stats['monthly_avg'] + stats['variable_monthly']):>14}/mo")
    print()
    print("NON-RECURRING (YTD)")
    print("-" * 50)
    print(f"Once a Year:                 {fmt(stats['annual_total']):>14}")
    print(f"A Few Times/Year:            {fmt(stats['periodic_total']):>14}")
    print(f"Travel Expenses:             {fmt(stats['travel_total']):>14}")
    print(f"Large One-Time:              {fmt(stats['one_off_total']):>14}")
    print(f"                             {'-'*14}")
    print(f"Total Non-Recurring:         {fmt(stats['annual_total'] + stats['periodic_total'] + stats['travel_total'] + stats['one_off_total']):>14}")
    print()
    print(f"TOTAL SPENDING (YTD):        {fmt(actual_spending):>14}")

    # Show excluded transactions info
    excluded_count = stats.get('excluded_count', 0)
    excluded_total = stats.get('excluded_total', 0)
    if excluded_count > 0:
        print()
        print(f"Excluded (income/transfer):  {fmt(excluded_total):>14}  ({excluded_count} transactions)")
    else:
        # Hint about special tags when none are used
        print()
        print("TIP: Use special tags to exclude non-spending transactions:")
        print("     income   - salary, deposits    (excluded from totals)")
        print("     transfer - CC payments, moves  (excluded from totals)")
        print("     refund   - returns, credits    (shown in Credits section)")

    # =========================================================================
    # EVERY MONTH (6+ months)
    # =========================================================================
    print("\n" + "=" * 80)
    print("EVERY MONTH (Appears 6+ Months)")
    print("=" * 80)
    print(f"\n{'Merchant':<26} {'Mo':>3} {'Type':<6} {'Monthly':>10} {'YTD':>12}")
    print("-" * 62)

    sorted_monthly = sorted(monthly_merchants.items(),
        key=lambda x: x[1]['avg_when_active'] if x[1]['is_consistent'] else x[1]['total']/12,
        reverse=True)
    for merchant, data in sorted_monthly[:25]:
        if data['is_consistent']:
            calc_type = "avg"
            monthly = data['avg_when_active']
        else:
            calc_type = "/12"
            monthly = data['total'] / 12
        print(f"{merchant:<26} {data['months_active']:>3} {calc_type:<6} {fmt(monthly):>12} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL':<26} {'':<3} {'':<6} {fmt(stats['monthly_avg']):>12}/mo {fmt(stats['monthly_total']):>14}")

    # =========================================================================
    # ONCE A YEAR
    # =========================================================================
    print("\n" + "=" * 80)
    print("ONCE A YEAR")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Total':>12}")
    print("-" * 58)

    sorted_annual = sorted(annual_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_annual:
        print(f"{merchant:<28} {data['subcategory']:<15} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL':<28} {'':<15} {fmt(stats['annual_total']):>14}")

    # =========================================================================
    # A FEW TIMES/YEAR
    # =========================================================================
    print("\n" + "=" * 80)
    print("A FEW TIMES/YEAR")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Count':>6} {'Total':>12}")
    print("-" * 65)

    sorted_periodic = sorted(periodic_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_periodic:
        print(f"{merchant:<28} {data['subcategory']:<15} {data['count']:>6} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL':<28} {'':<15} {'':<6} {fmt(stats['periodic_total']):>14}")

    # =========================================================================
    # TRAVEL EXPENSES
    # =========================================================================
    print("\n" + "=" * 80)
    print("TRAVEL EXPENSES")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Count':>6} {'Total':>12}")
    print("-" * 65)

    sorted_travel = sorted(travel_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_travel[:15]:
        print(f"{merchant:<28} {data['category']:<15} {data['count']:>6} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL TRAVEL':<28} {'':<15} {'':<6} {fmt(stats['travel_total']):>14}")

    # =========================================================================
    # LARGE ONE-TIME
    # =========================================================================
    print("\n" + "=" * 80)
    print("LARGE ONE-TIME")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Total':>12}")
    print("-" * 58)

    sorted_oneoff = sorted(one_off_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_oneoff[:15]:
        print(f"{merchant:<28} {data['category']:<15} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL ONE-OFF':<28} {'':<15} {fmt(stats['one_off_total']):>14}")

    # =========================================================================
    # VARIES BY MONTH
    # =========================================================================
    print("\n" + "=" * 80)
    print("VARIES BY MONTH")
    print("=" * 80)
    print(f"\n{'Category':<18} {'Subcategory':<15} {'Months':>6} {'Avg/Mo':>10} {'YTD':>12}")
    print("-" * 70)

    # Group variable merchants by category
    variable_by_cat = defaultdict(lambda: {'total': 0, 'months': set()})
    for merchant, data in variable_merchants.items():
        key = (data['category'], data['subcategory'])
        variable_by_cat[key]['total'] += data['total']
        variable_by_cat[key]['months'].update(data['months'])

    sorted_var_cats = sorted(variable_by_cat.items(), key=lambda x: x[1]['total'], reverse=True)
    for (cat, subcat), info in sorted_var_cats[:20]:
        if filter_category and cat.lower() != filter_category.lower():
            continue
        months_active = len(info['months'])
        avg = info['total'] / months_active if months_active > 0 else 0
        print(f"{cat:<18} {subcat:<15} {months_active:>6} {fmt(avg):>12} {fmt(info['total']):>14}")

    print(f"\n{'TOTAL VARIABLE':<18} {'':<15} {'':<6} {fmt(stats['variable_monthly']):>12}/mo {fmt(stats['variable_total']):>14}")


def print_sections_summary(stats, year=2025, currency_format="${amount}", only_filter=None):
    """Print sections-based analysis summary.

    Args:
        stats: Analysis statistics dict
        year: Year for display
        currency_format: Format string for currency
        only_filter: Optional list of section names (lowercase) to show
    """
    def fmt(amount):
        return format_currency(amount, currency_format)

    sections = stats.get('sections', {})
    sections_config = stats.get('_sections_config')

    if not sections:
        print("No views defined. Add views to config/views.rules")
        return

    # Get the order of sections from config
    section_order = [s.name for s in sections_config.sections] if sections_config else list(sections.keys())

    # Filter sections if only_filter is specified
    if only_filter:
        section_order = [s for s in section_order if s.lower() in only_filter]

    num_months = stats.get('num_months', 12)

    print("=" * 80)
    print(f"{year} SPENDING ANALYSIS")
    print("=" * 80)

    # Print each section
    for section_name in section_order:
        if section_name not in sections:
            continue

        section_data = sections[section_name]
        section_total = section_data.get('total', 0)
        section_monthly = section_data.get('monthly', 0)
        merchants = section_data.get('merchants', [])

        if not merchants:
            continue

        # Section header with totals
        print()
        print(f"{section_name.upper()} ({fmt(section_total)}/yr · {fmt(section_monthly)}/mo)")
        print("-" * 70)

        # Print merchants in section
        print(f"{'Merchant':<28} {'Mo':>3} {'Type':<6} {'Monthly':>12} {'YTD':>14}")
        print("-" * 70)

        # Sort merchants by total (descending)
        sorted_merchants = sorted(merchants, key=lambda x: x[1].get('total', 0), reverse=True)

        for merchant_name, data in sorted_merchants[:20]:
            months_active = data.get('months_active', 0)
            total = data.get('total', 0)
            is_consistent = data.get('is_consistent', False)

            if is_consistent and months_active > 0:
                calc_type = "avg"
                monthly = data.get('avg_when_active', total / months_active)
            else:
                calc_type = "/12"
                monthly = total / num_months

            print(f"{merchant_name:<28} {months_active:>3} {calc_type:<6} {fmt(monthly):>12} {fmt(total):>14}")

        if len(sorted_merchants) > 20:
            print(f"  ... and {len(sorted_merchants) - 20} more merchants")

    print()
    print("=" * 80)


def generate_embeddings(items):
    """Generate embeddings for a list of text items using sentence-transformers."""
    if not EMBEDDINGS_AVAILABLE:
        return None

    print("Generating semantic embeddings...")
    # Use a small, fast model optimized for semantic similarity
    model = SentenceTransformer('all-MiniLM-L6-v2')
    embeddings = model.encode(items, show_progress_bar=False)
    return embeddings.tolist()


def write_summary_file_vue(stats, filepath, year=2025, home_locations=None, currency_format="${amount}", sources=None, embedded_html=True):
    """Write summary to HTML file using Vue 3 for client-side rendering.

    Args:
        stats: Analysis statistics dict
        filepath: Output file path
        year: Year for display in title
        home_locations: Set of home location codes for location badge coloring
        currency_format: Format string for currency display, e.g. "${amount}" or "{amount} zł"
        sources: List of data source names (e.g., ['Amex', 'Chase'])
        embedded_html: If True (default), embed CSS/JS inline. If False, output separate files.
    """
    home_locations = home_locations or set()
    sources = sources or []

    # Load template files
    template_dir = get_template_dir()
    html_template = (template_dir / 'spending_report.html').read_text(encoding='utf-8')
    css_content = (template_dir / 'spending_report.css').read_text(encoding='utf-8')
    js_content = (template_dir / 'spending_report.js').read_text(encoding='utf-8')

    # Extract merchant dicts
    monthly_merchants = stats['monthly_merchants']
    annual_merchants = stats['annual_merchants']
    periodic_merchants = stats['periodic_merchants']
    travel_merchants = stats['travel_merchants']
    one_off_merchants = stats['one_off_merchants']
    variable_merchants = stats['variable_merchants']

    # Get number of months for averaging
    num_months = stats['num_months']

    # Helper to explain a pattern in human-readable form
    def _explain_pattern(pattern):
        """Convert a regex/expression pattern to human-readable explanation."""
        if not pattern:
            return ''

        # Handle expression-style patterns (contains, startswith, etc.)
        if 'contains(' in pattern.lower():
            import re
            match = re.search(r'contains\(["\']([^"\']+)["\']\)', pattern, re.IGNORECASE)
            if match:
                return f'Description contains "{match.group(1)}"'

        if 'startswith(' in pattern.lower():
            import re
            match = re.search(r'startswith\(["\']([^"\']+)["\']\)', pattern, re.IGNORECASE)
            if match:
                return f'Description starts with "{match.group(1)}"'

        if 'anyof(' in pattern.lower():
            import re
            match = re.search(r'anyof\(([^)]+)\)', pattern, re.IGNORECASE)
            if match:
                terms = [t.strip().strip('"\'') for t in match.group(1).split(',')]
                if len(terms) <= 3:
                    return f'Description contains any of: {", ".join(terms)}'
                return f'Description contains any of: {", ".join(terms[:3])}...'

        # Handle regex patterns
        parts = []

        # Check for alternation (OR)
        if '|' in pattern and not pattern.startswith('('):
            alternatives = pattern.split('|')
            if len(alternatives) <= 3:
                terms = [a.replace('.*', ' ... ').replace('\\s', ' ').strip() for a in alternatives]
                return f'Matches: {" OR ".join(terms)}'
            else:
                terms = [a.replace('.*', ' ... ').replace('\\s', ' ').strip() for a in alternatives[:3]]
                return f'Matches: {" OR ".join(terms)} (+ {len(alternatives) - 3} more)'

        # Check for start anchor
        if pattern.startswith('^'):
            pattern = pattern[1:]
            parts.append('Starts with')
        else:
            parts.append('Contains')

        # Check for end anchor
        end_anchor = pattern.endswith('$')
        if end_anchor:
            pattern = pattern[:-1]

        # Clean up common regex syntax for display
        display = pattern
        display = display.replace('.*', ' ... ')
        display = display.replace('.+', ' ... ')
        display = display.replace('\\s+', ' ')
        display = display.replace('\\s', ' ')
        display = display.replace('\\d+', '#')
        display = display.replace('\\d', '#')
        display = display.replace('(?!', ' (not followed by ')
        display = display.replace('(?:', '(')
        display = display.replace(')', ')')

        parts.append(f'"{display.strip()}"')

        if end_anchor:
            parts.append('at end')

        return ' '.join(parts)

    # Helper to explain a view filter expression
    def _explain_view_filter(filter_expr):
        """Convert a view filter expression to human-readable explanation."""
        if not filter_expr:
            return ''

        explanations = []

        # Parse common conditions
        if 'category ==' in filter_expr.lower() or "category='" in filter_expr.lower():
            import re
            match = re.search(r'category\s*==?\s*["\']([^"\']+)["\']', filter_expr, re.IGNORECASE)
            if match:
                explanations.append(f'Category is "{match.group(1)}"')

        if 'subcategory ==' in filter_expr.lower() or "subcategory='" in filter_expr.lower():
            import re
            match = re.search(r'subcategory\s*==?\s*["\']([^"\']+)["\']', filter_expr, re.IGNORECASE)
            if match:
                explanations.append(f'Subcategory is "{match.group(1)}"')

        if 'tag(' in filter_expr.lower() or 'has_tag(' in filter_expr.lower():
            import re
            matches = re.findall(r'(?:tag|has_tag)\(["\']([^"\']+)["\']\)', filter_expr, re.IGNORECASE)
            for tag in matches:
                explanations.append(f'Has tag "{tag}"')

        if 'months >' in filter_expr or 'months>=' in filter_expr:
            import re
            match = re.search(r'months\s*>=?\s*(\d+)', filter_expr)
            if match:
                explanations.append(f'Active {match.group(1)}+ months')

        if 'total >' in filter_expr or 'total>=' in filter_expr:
            import re
            match = re.search(r'total\s*>=?\s*(\d+)', filter_expr)
            if match:
                explanations.append(f'Total ≥ ${match.group(1)}')

        if 'cv <' in filter_expr or 'cv<=' in filter_expr:
            import re
            match = re.search(r'cv\s*<=?\s*([\d.]+)', filter_expr)
            if match:
                explanations.append(f'Coefficient of variation ≤ {match.group(1)}')

        if explanations:
            return ' AND '.join(explanations)

        # Fallback: return cleaned up version of expression
        return filter_expr.replace('==', '=').replace('&&', ' and ').replace('||', ' or ')

    # Helper function to create merchant IDs
    def make_merchant_id(name):
        return name.replace("'", "").replace('"', '').replace(' ', '_')

    # Build section merchants data
    def build_section_merchants(merchant_dict):
        merchants = {}
        for merchant_name, data in merchant_dict.items():
            merchant_id = make_merchant_id(merchant_name)

            # Build transactions array with unique IDs
            txns = []
            for i, txn in enumerate(data.get('transactions', [])):
                txns.append({
                    'id': f"{merchant_id}_{i}",
                    'date': txn.get('date', ''),
                    'month': txn.get('month', ''),
                    'description': txn.get('raw_description', txn.get('description', '')),
                    'amount': txn.get('amount', 0),
                    'source': txn.get('source', ''),
                    'location': txn.get('location'),
                    'tags': txn.get('tags', [])
                })

            # Build match info for tooltip
            match_info = data.get('match_info')
            match_info_json = None
            if match_info:
                pattern = match_info.get('pattern', '')
                match_info_json = {
                    'pattern': pattern,
                    'source': match_info.get('source', ''),
                    'explanation': _explain_pattern(pattern),
                    'assignedMerchant': merchant_name,
                    'assignedCategory': data.get('category', ''),
                    'assignedSubcategory': data.get('subcategory', ''),
                    'assignedTags': sorted(match_info.get('tags', [])),
                    'tagSources': match_info.get('tag_sources', {}),
                }

            merchants[merchant_id] = {
                'id': merchant_id,
                'displayName': merchant_name,
                'category': data.get('category', 'Other'),
                'subcategory': data.get('subcategory', 'Uncategorized'),
                'categoryPath': f"{data.get('category', 'Other')}/{data.get('subcategory', 'Uncategorized')}".lower(),
                'calcType': data.get('calc_type', '/12'),
                'monthsActive': data.get('months_active', 0),
                'isConsistent': data.get('is_consistent', False),
                'ytd': data.get('total', 0),
                'monthly': data.get('avg_when_active') or (data.get('total', 0) / num_months if num_months > 0 else 0),
                'count': data.get('count', len(txns)),
                'transactions': txns,
                'tags': sorted(data.get('tags', set())),  # Convert set to sorted list
                'matchInfo': match_info_json,  # Pattern/source for explain tooltip
            }
        return merchants

    sections = {}

    # Use user-defined views from views.rules
    user_sections = stats.get('sections')
    sections_config = stats.get('_sections_config')

    # Build lookups for section descriptions and filters from config
    section_descriptions = {}
    section_filters = {}
    if sections_config:
        for section in sections_config.sections:
            section_descriptions[section.name] = section.description
            section_filters[section.name] = section.filter_expr

    if user_sections:
        for section_name, section_data in user_sections.items():
            section_id = section_name.lower().replace(' ', '_')
            merchants_list = section_data.get('merchants', [])

            if not merchants_list:
                continue

            # Convert list of (name, data) tuples to dict format
            merchant_dict = {name: data for name, data in merchants_list}
            merchants = build_section_merchants(merchant_dict)

            # Add view info to each merchant
            view_filter = section_filters.get(section_name, '')
            for merchant_id, merchant in merchants.items():
                merchant['viewInfo'] = {
                    'viewName': section_name,
                    'filterExpr': view_filter,
                    'explanation': _explain_view_filter(view_filter) if view_filter else '',
                }

            if merchants:
                # Use description from config, or empty string if not set
                description = section_descriptions.get(section_name, '')
                sections[section_id] = {
                    'title': section_name,
                    'hasMonthlyColumn': True,  # All sections show monthly
                    'description': description,
                    'merchants': merchants
                }

    # Get home state for location coloring
    home_state = list(home_locations)[0] if home_locations else 'WA'

    # Calculate data through date (latest transaction date)
    latest_date = ''
    for merchant_dict in [monthly_merchants, annual_merchants, periodic_merchants,
                          travel_merchants, one_off_merchants, variable_merchants]:
        for data in merchant_dict.values():
            for txn in data.get('transactions', []):
                if txn.get('date', '') > latest_date:
                    latest_date = txn.get('date', '')

    # Build category view - group all merchants by category -> subcategory
    # This uses by_merchant (all merchants) so it's not filtered by views.rules
    def build_category_view():
        # Build from by_merchant which contains ALL merchants (not filtered by sections)
        all_merchants = {}
        by_merchant = stats.get('by_merchant', {})
        for merchant_name, data in by_merchant.items():
            merchant_id = make_merchant_id(merchant_name)
            all_merchants[merchant_id] = build_section_merchants({merchant_name: data})[merchant_id]

        # Group by category -> subcategory
        categories = {}
        for merchant_id, merchant in all_merchants.items():
            cat = merchant.get('category', 'Uncategorized') or 'Uncategorized'
            subcat = merchant.get('subcategory', 'Other') or 'Other'

            # Handle unknown merchants
            if cat == 'Unknown':
                cat = 'Uncategorized'
                subcat = 'Unknown'

            if cat not in categories:
                categories[cat] = {
                    'total': 0,
                    'monthly': 0,
                    'count': 0,
                    'subcategories': {}
                }

            if subcat not in categories[cat]['subcategories']:
                categories[cat]['subcategories'][subcat] = {
                    'total': 0,
                    'monthly': 0,
                    'count': 0,
                    'merchants': {}
                }

            # Add merchant to subcategory
            categories[cat]['subcategories'][subcat]['merchants'][merchant_id] = merchant
            categories[cat]['subcategories'][subcat]['total'] += merchant.get('ytd', 0)
            categories[cat]['subcategories'][subcat]['monthly'] += merchant.get('monthly', 0)
            categories[cat]['subcategories'][subcat]['count'] += merchant.get('count', 0)

            # Update category totals
            categories[cat]['total'] += merchant.get('ytd', 0)
            categories[cat]['monthly'] += merchant.get('monthly', 0)
            categories[cat]['count'] += merchant.get('count', 0)

        return categories

    category_view = build_category_view()

    # Build final spending data object
    spending_data = {
        'year': year,
        'numMonths': num_months,
        'homeState': home_state,
        'sources': sources,
        'dataThrough': latest_date,
        'sections': sections,
        'categoryView': category_view,
        # Excluded transactions for transparency
        'excludedTransactions': stats.get('excluded_transactions', []),
        'excludedCount': stats.get('excluded_count', 0),
        'excludedTotal': stats.get('excluded_total', 0),
    }

    # Assemble final HTML
    data_script = f'window.spendingData = {json.dumps(spending_data)};'

    if not embedded_html:
        # Write separate files for easier development
        output_path = Path(filepath)
        output_dir = output_path.parent

        # Write CSS file
        css_path = output_dir / 'spending_report.css'
        css_path.write_text(css_content, encoding='utf-8')

        # Write JS file
        js_path = output_dir / 'spending_report.js'
        js_path.write_text(js_content, encoding='utf-8')

        # Write data file
        data_path = output_dir / 'spending_data.js'
        data_path.write_text(data_script, encoding='utf-8')

        # Create HTML with external references
        final_html = html_template.replace(
            '<style>/* CSS_PLACEHOLDER */</style>',
            '<link rel="stylesheet" href="spending_report.css">'
        ).replace(
            '<script>/* DATA_PLACEHOLDER */</script>',
            '<script src="spending_data.js"></script>'
        ).replace(
            '<script>/* JS_PLACEHOLDER */</script>',
            '<script src="spending_report.js"></script>'
        )
    else:
        # Embed everything inline (default)
        final_html = html_template.replace(
            '/* CSS_PLACEHOLDER */', css_content
        ).replace(
            '/* DATA_PLACEHOLDER */', data_script
        ).replace(
            '/* JS_PLACEHOLDER */', js_content
        )

    # Write output file
    Path(filepath).write_text(final_html, encoding='utf-8')


def write_summary_file(stats, filepath, year=2025, home_locations=None, currency_format="${amount}"):
    """Write summary to HTML file (legacy version with server-side rendering).

    Args:
        stats: Analysis statistics dict
        filepath: Output file path
        year: Year for display in title
        home_locations: Set of home location codes for location badge coloring
        currency_format: Format string for currency display, e.g. "${amount}" or "{amount} zł"
    """
    home_locations = home_locations or set()

    # Load external CSS and JavaScript files for embedding
    template_dir = get_template_dir()
    css_file_path = template_dir / 'spending_report.css'
    with open(css_file_path, 'r', encoding='utf-8') as f:
        spending_report_css = f.read()

    js_file_path = template_dir / 'spending_report.js'
    with open(js_file_path, 'r', encoding='utf-8') as f:
        spending_report_js = f.read()

    # Local helpers for currency formatting
    def fmt(amount):
        return format_currency(amount, currency_format)
    def fmt_dec(amount):
        return format_currency_decimal(amount, currency_format)
    by_category = stats['by_category']
    monthly_merchants = stats['monthly_merchants']
    annual_merchants = stats['annual_merchants']
    periodic_merchants = stats['periodic_merchants']
    travel_merchants = stats['travel_merchants']
    one_off_merchants = stats['one_off_merchants']
    variable_merchants = stats['variable_merchants']

    # Calculate actual spending (transactions tagged income/transfer already excluded)
    actual = sum(d['total'] for (c, s), d in by_category.items())
    uncat = by_category.get(('Other', 'Uncategorized'), {'total': 0})['total']

    # Group variable by category
    variable_by_cat = defaultdict(lambda: {'total': 0, 'months': set()})
    for merchant, data in variable_merchants.items():
        key = (data['category'], data['subcategory'])
        variable_by_cat[key]['total'] += data['total']
        variable_by_cat[key]['months'].update(data['months'])

    # Collect all unique categories and subcategories for dropdown
    all_categories = set()
    for cat, sub in by_category.keys():
        all_categories.add(cat)
        if sub:
            all_categories.add(sub)
    for data in monthly_merchants.values():
        if data.get('category'):
            all_categories.add(data['category'])
        if data.get('subcategory'):
            all_categories.add(data['subcategory'])
    for data in annual_merchants.values():
        if data.get('category'):
            all_categories.add(data['category'])
        if data.get('subcategory'):
            all_categories.add(data['subcategory'])
    for data in periodic_merchants.values():
        if data.get('category'):
            all_categories.add(data['category'])
        if data.get('subcategory'):
            all_categories.add(data['subcategory'])
    for data in variable_merchants.values():
        if data.get('category'):
            all_categories.add(data['category'])
        if data.get('subcategory'):
            all_categories.add(data['subcategory'])
    for data in travel_merchants.values():
        if data.get('category'):
            all_categories.add(data['category'])
        if data.get('subcategory'):
            all_categories.add(data['subcategory'])
    for data in one_off_merchants.values():
        if data.get('category'):
            all_categories.add(data['category'])
        if data.get('subcategory'):
            all_categories.add(data['subcategory'])
    sorted_categories = sorted(all_categories)

    # Collect all unique merchants for autocomplete
    all_merchants = set()
    for merchant in monthly_merchants.keys():
        all_merchants.add(merchant)
    for merchant in annual_merchants.keys():
        all_merchants.add(merchant)
    for merchant in periodic_merchants.keys():
        all_merchants.add(merchant)
    for merchant in travel_merchants.keys():
        all_merchants.add(merchant)
    for merchant in one_off_merchants.keys():
        all_merchants.add(merchant)
    for merchant in variable_merchants.keys():
        all_merchants.add(merchant)
    sorted_merchants = sorted(all_merchants)

    # Helper functions to create consistent IDs for filtering
    def make_merchant_id(name):
        """Create a unique ID for merchant filtering (URL-safe, no quotes/spaces)."""
        return name.replace("'", "").replace('"', '').replace(' ', '_')

    def make_category_id(name):
        """Create a unique ID for category filtering (lowercase)."""
        return name.lower() if name else ''

    def make_location_id(code):
        """Create a unique ID for location filtering (lowercase)."""
        return code.lower() if code else ''

    # Collect all unique locations for autocomplete
    all_locations = set()
    for data in monthly_merchants.values():
        for txn in data.get('transactions', []):
            if txn.get('location'):
                all_locations.add(txn['location'])
    for data in annual_merchants.values():
        for txn in data.get('transactions', []):
            if txn.get('location'):
                all_locations.add(txn['location'])
    for data in travel_merchants.values():
        for txn in data.get('transactions', []):
            if txn.get('location'):
                all_locations.add(txn['location'])
    for data in one_off_merchants.values():
        for txn in data.get('transactions', []):
            if txn.get('location'):
                all_locations.add(txn['location'])
    for data in variable_merchants.values():
        for txn in data.get('transactions', []):
            if txn.get('location'):
                all_locations.add(txn['location'])
    sorted_locations = sorted(all_locations)

    # Generate embeddings for semantic search
    all_searchable = list(sorted_categories) + list(sorted_merchants)
    embeddings = generate_embeddings(all_searchable)
    embeddings_json = json.dumps({
        'items': all_searchable,
        'vectors': embeddings
    }) if embeddings else 'null'

    true_monthly = stats['monthly_avg'] + stats['variable_monthly']
    non_recurring_total = stats['annual_total'] + stats['periodic_total'] + stats['travel_total'] + stats['one_off_total']

    # US states set for location classification
    us_states = {'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC'}

    def location_badge(loc):
        """Generate HTML for location badge."""
        if not loc:
            return ''
        onclick = f"addFilterFromCell(event, this, 'location')"
        if loc in home_locations:
            return f'<span class="txn-location home clickable" onclick="{onclick}">{loc}</span>'
        elif loc not in us_states:
            return f'<span class="txn-location intl clickable" onclick="{onclick}">{loc}</span>'
        else:
            return f'<span class="txn-location clickable" onclick="{onclick}">{loc}</span>'

    # Generate embedded JSON for LLM tools (full verbosity for programmatic access)
    import json
    embedded_json = export_json(stats, verbose=2)

    # Load Chart.js library for offline use
    assets_dir = Path(__file__).parent / 'assets'
    chart_js_path = assets_dir / 'chart.min.js'
    if chart_js_path.exists():
        chart_js_content = chart_js_path.read_text(encoding='utf-8')
    else:
        chart_js_content = '// Chart.js not found - charts will not render'

    # Prepare chart data
    # 1. Monthly spending trend (transactions tagged income/transfer already excluded)
    # Calculate from classified merchants to match YTD totals
    spending_by_month = defaultdict(float)
    all_merchant_dicts = [
        monthly_merchants, annual_merchants, periodic_merchants,
        travel_merchants, one_off_merchants, variable_merchants
    ]
    for merchants in all_merchant_dicts:
        for merchant, data in merchants.items():
            for month, amount in data.get('monthly_amounts', {}).items():
                spending_by_month[month] += amount

    sorted_months = sorted(spending_by_month.keys())
    monthly_labels = [datetime.strptime(m, '%Y-%m').strftime('%b %Y') for m in sorted_months]
    monthly_totals = [spending_by_month[m] for m in sorted_months]

    # Calculate min/max months for date picker (e.g., "2025-01", "2025-12")
    min_month = sorted_months[0] if sorted_months else f"{year}-01"
    max_month = sorted_months[-1] if sorted_months else f"{year}-12"

    # Generate date picker options from available months
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    def generate_date_options(months_list):
        """Generate HTML options for months and quarters from available data."""
        if not months_list:
            return ""

        options = []

        # Get unique years from the months
        years = sorted(set(m.split('-')[0] for m in months_list), reverse=True)

        # Generate quarter options
        quarter_options = []
        for yr in years:
            quarters = [
                (f"{yr}-10..{yr}-12", f"Q4 {yr}"),
                (f"{yr}-07..{yr}-09", f"Q3 {yr}"),
                (f"{yr}-04..{yr}-06", f"Q2 {yr}"),
                (f"{yr}-01..{yr}-03", f"Q1 {yr}"),
            ]
            for value, label in quarters:
                # Check if any months in this quarter exist in data
                start, end = value.split('..')
                quarter_months = [m for m in months_list if start <= m <= end]
                if quarter_months:
                    quarter_options.append(f'                    <option value="{value}">{label}</option>')

        if quarter_options:
            options.append('                <optgroup label="Quarters">')
            options.extend(quarter_options)
            options.append('                </optgroup>')

        # Generate individual month options
        month_options = []
        for m in reversed(months_list):
            yr, mo = m.split('-')
            month_label = f"{month_names[int(mo)-1]} {yr}"
            month_options.append(f'                    <option value="{m}">{month_label}</option>')

        if month_options:
            options.append('                <optgroup label="Months">')
            options.extend(month_options)
            options.append('                </optgroup>')

        return '\n'.join(options)

    date_picker_options = generate_date_options(sorted_months)
    
    # 2. Category breakdown by month - build spending per category per month
    # Build category breakdown from merchant data using monthly_amounts dict
    category_monthly_totals = defaultdict(lambda: defaultdict(float))
    for merchant_dict in [monthly_merchants, annual_merchants, periodic_merchants,
                          travel_merchants, one_off_merchants, variable_merchants]:
        for merchant, data in merchant_dict.items():
            category = data.get('category', 'Other')
            for month, amount in data.get('monthly_amounts', {}).items():
                category_monthly_totals[category][month] += amount
    
    # Prepare data for category breakdown chart
    top_categories = ['Food', 'Shopping', 'Transport', 'Bills', 'Subscriptions', 
                      'Health', 'Travel', 'Home', 'Personal']
    category_datasets = []
    
    for cat in top_categories:
        if cat in category_monthly_totals:
            cat_data = [category_monthly_totals[cat].get(m, 0) for m in sorted_months]
            if sum(cat_data) > 0:  # Only include if has data
                category_datasets.append({
                    'label': cat,
                    'data': cat_data
                })
    
    # 3. Category pie chart data
    category_totals = {}
    for (cat, subcat), data in by_category.items():
        if cat not in excluded:
            category_totals[cat] = category_totals.get(cat, 0) + data['total']
    
    # Sort by total and take top 8 categories
    sorted_categories_by_total = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    pie_labels = [cat for cat, _ in sorted_categories_by_total[:8]]
    pie_data = [total for _, total in sorted_categories_by_total[:8]]
    
    # Convert chart data to JSON
    chart_data_json = json.dumps({
        'monthly': {
            'labels': monthly_labels,
            'data': monthly_totals
        },
        'categoryByMonth': {
            'labels': monthly_labels,
            'datasets': category_datasets
        },
        'categoryPie': {
            'labels': pie_labels,
            'data': pie_data
        }
    })

    # Generate structured section data for JavaScript filtering
    # This is the single source of truth - filtering operates on this, not DOM
    def build_section_merchants(merchant_dict, section_type):
        """Build merchants object for a section."""
        merchants = {}
        section_total = 0
        for merchant_name, data in merchant_dict.items():
            merchant_id = make_merchant_id(merchant_name)
            section_total += data['total']

            # Build transactions array
            txns = []
            for txn in data.get('transactions', []):
                txns.append({
                    'date': txn.get('date', ''),
                    'month': txn.get('month', ''),
                    'description': txn.get('raw_description', txn.get('description', '')),
                    'amount': txn.get('amount', 0),
                    'source': txn.get('source', ''),
                    'location': txn.get('location'),
                    'tags': txn.get('tags', [])
                })

            merchants[merchant_id] = {
                'id': merchant_id,
                'displayName': merchant_name,
                'category': data.get('category', ''),
                'subcategory': data.get('subcategory', ''),
                'categoryPath': f"{data.get('category', '')}/{data.get('subcategory', '')}".lower(),
                'monthsActive': data.get('months_active'),
                'isConsistent': data.get('is_consistent'),
                'calcType': data.get('calc_type'),
                'ytd': data.get('total', 0),
                'monthly': data.get('avg_when_active') or (data.get('total', 0) / num_months if num_months > 0 else 0),
                'count': data.get('count', 0),
                'transactions': txns,
                'tags': sorted(data.get('tags', set())),  # Convert set to sorted list
            }
        return merchants, section_total

    num_months = stats['num_months']
    section_data = {
        'year': year,
        'numMonths': num_months,
        'sections': {}
    }

    # Section configurations
    section_configs = [
        ('monthly-table', monthly_merchants, True, 'ytd'),
        ('annual-table', annual_merchants, False, 'total'),
        ('periodic-table', periodic_merchants, False, 'total'),
        ('travel-table', travel_merchants, False, 'total'),
        ('oneoff-table', one_off_merchants, False, 'total'),
        ('variable-table', variable_merchants, True, 'ytd'),
    ]

    for section_id, merchant_dict, has_monthly, total_col in section_configs:
        merchants, section_total = build_section_merchants(merchant_dict, section_id)
        section_data['sections'][section_id] = {
            'id': section_id,
            'hasMonthlyColumn': has_monthly,
            'totalColumn': total_col,
            'merchants': merchants,
            'totals': {
                total_col: section_total
            }
        }

    # Add original totals for percentage calculations
    section_data['originalTotals'] = {
        'monthlyYtd': stats['monthly_total'],
        'monthlyAvg': stats['monthly_avg'],
        'annualTotal': stats['annual_total'],
        'periodicTotal': stats['periodic_total'],
        'travelTotal': stats['travel_total'],
        'oneoffTotal': stats['one_off_total'],
        'variableYtd': stats['variable_total'],
        'variableAvg': stats['variable_monthly'],
        'totalYtd': actual
    }

    section_data_json = json.dumps(section_data)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{year} Spending Analysis</title>
    <script>
        // Theme toggle - runs immediately to prevent flash
        (function() {{
            const saved = localStorage.getItem('tally-theme');
            const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            const theme = saved || (prefersDark ? 'dark' : 'light');
            document.documentElement.dataset.theme = theme;
        }})();

        function toggleTheme() {{
            const html = document.documentElement;
            const current = html.dataset.theme || 'dark';
            const next = current === 'dark' ? 'light' : 'dark';
            html.dataset.theme = next;
            localStorage.setItem('tally-theme', next);
            updateThemeIcon(next);
        }}

        function updateThemeIcon(theme) {{
            const icon = document.querySelector('.theme-icon');
            if (icon) icon.textContent = theme === 'dark' ? '🌙' : '☀️';
        }}

        // Update icon when DOM is ready
        document.addEventListener('DOMContentLoaded', function() {{
            updateThemeIcon(document.documentElement.dataset.theme || 'dark');
        }});
    </script>
    <script type="module">
        // Load Transformers.js for semantic search
        import {{ pipeline }} from 'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2';

        window.initSemanticSearch = async function() {{
            if (!window.embeddingsData || !window.embeddingsData.vectors) {{
                console.log('Semantic search disabled - no embeddings');
                return;
            }}
            try {{
                console.log('Loading semantic search model...');
                window.semanticModel = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2');
                window.semanticReady = true;
                console.log('Semantic search ready!');
                // Update placeholder to indicate semantic search is available
                const input = document.getElementById('searchInput');
                if (input) input.placeholder = 'Semantic search ready... try "groceries" or "workout"';
            }} catch (e) {{
                console.error('Failed to load semantic model:', e);
            }}
        }};

        // Initialize when DOM is ready
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', window.initSemanticSearch);
        }} else {{
            window.initSemanticSearch();
        }}
    </script>
    <script>{chart_js_content}</script>
    <style>
{spending_report_css}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{year} Spending Analysis</h1>
            <p class="subtitle">Generated {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
            <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">
                <span class="theme-icon"></span>
            </button>
        </header>
        <div class="search-box">
            <div class="autocomplete-container">
                <input type="text" id="searchInput" placeholder="Search merchants, categories, locations..." autocomplete="off">
                <div id="autocompleteList" class="autocomplete-list"></div>
            </div>
            <select id="dateRangeSelect" class="date-range-select" onchange="applyDateRange(this.value)">
                <option value="">All Dates</option>
{date_picker_options}
            </select>
            <div id="filterChips" class="filter-chips"></div>
        </div>

        <div class="help-section collapsed" id="helpSection">
            <div class="help-section-header" onclick="document.getElementById('helpSection').classList.toggle('collapsed')">
                <h3>📊 How to Read This Report</h3>
                <span class="toggle">▼</span>
            </div>
            <div class="help-section-content">
                <span class="label">Monthly Recurring:</span>
                <span class="value"><span class="badge avg">avg</span> consistent payments → avg when active (e.g., Netflix $15×6mo = $90 YTD → $15/mo avg) · <span class="badge div">/12</span> irregular amounts → YTD÷12 (e.g., $1200 once → $100/mo)</span>
                <span class="label">Variable Avg/Mo:</span>
                <span class="value">YTD ÷ months active (e.g., Groceries $600 over 4 months → $150/mo). Section total = sum of all Avg/Mo values.</span>
                <span class="label">Terms:</span>
                <span class="value"><code>YTD</code> year-to-date total · <code>/mo</code> monthly cost · <code>Months</code> months with transactions</span>
                <span class="label">Categories:</span>
                <span class="value"><strong>Monthly Recurring</strong> (6+ months) · <strong>Annual</strong> (once-a-year) · <strong>Periodic</strong> (quarterly) · <strong>Travel</strong> · <strong>One-Off</strong> · <strong>Variable</strong> (discretionary)</span>
                <span class="label">Charts:</span>
                <span class="value"><strong>Monthly Trend</strong> total spending per month · <strong>Category Breakdown</strong> top 8 categories by total spend · <strong>Spending by Month</strong> category breakdown over time. Charts update when filters are applied.</span>
            </div>
        </div>

        <div class="summary-grid">
            <div class="card monthly">
                <h2>Monthly Budget</h2>
                <div class="amount">{fmt(true_monthly)}<span style="font-size: 1rem; color: #888;">/mo</span></div>
                <div class="breakdown">
                    <div class="breakdown-item">
                        <span class="name">Monthly Recurring</span>
                        <span class="value">{fmt(stats['monthly_avg'])} <span class="breakdown-pct">({stats['monthly_total']/actual*100:.1f}%)</span></span>
                    </div>
                    <div class="breakdown-item">
                        <span class="name" data-tooltip="Sum of Avg/Mo values from variable spending">Variable/Discretionary</span>
                        <span class="value">{fmt(stats['variable_monthly'])} <span class="breakdown-pct">({stats['variable_total']/actual*100:.1f}%)</span></span>
                    </div>
                </div>
            </div>

            <div class="card non-recurring">
                <h2>Non-Recurring (YTD)</h2>
                <div class="amount">{fmt(non_recurring_total)} <span class="breakdown-pct">({non_recurring_total/actual*100:.1f}%)</span></div>
                <div class="breakdown">
                    <div class="breakdown-item">
                        <span class="name">Annual Bills</span>
                        <span class="value">{fmt(stats['annual_total'])} <span class="breakdown-pct">({stats['annual_total']/actual*100:.1f}%)</span></span>
                    </div>
                    <div class="breakdown-item">
                        <span class="name">Periodic Recurring</span>
                        <span class="value">{fmt(stats['periodic_total'])} <span class="breakdown-pct">({stats['periodic_total']/actual*100:.1f}%)</span></span>
                    </div>
                    <div class="breakdown-item">
                        <span class="name">Travel/Trips</span>
                        <span class="value">{fmt(stats['travel_total'])} <span class="breakdown-pct">({stats['travel_total']/actual*100:.1f}%)</span></span>
                    </div>
                    <div class="breakdown-item">
                        <span class="name">One-Off Purchases</span>
                        <span class="value">{fmt(stats['one_off_total'])} <span class="breakdown-pct">({stats['one_off_total']/actual*100:.1f}%)</span></span>
                    </div>
                </div>
            </div>

            <div class="card total">
                <h2 id="totalSpendingLabel">Total Spending (YTD)</h2>
                <div class="amount" id="totalSpending" data-original="{actual:.0f}">{fmt(actual)}</div>
                <div class="breakdown">
                    <div class="breakdown-item">
                        <span class="name">Uncategorized</span>
                        <span class="value">{fmt(uncat)} ({uncat/actual*100:.1f}%)</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Charts Section -->
        <section class="chart-section">
            <div class="section-header" onclick="toggleSection(this)" data-tooltip="Monthly Trend shows spending per month. Category Breakdown shows top 8 categories by total. Charts update when filters are applied.">
                <h2><span class="toggle">▼</span> Spending Charts & Trends</h2>
            </div>
            <div class="section-content">
            <div class="charts-grid">
                <div class="chart-container">
                    <h3>Monthly Spending Trend</h3>
                    <div class="chart-wrapper">
                        <canvas id="monthlyTrendChart"></canvas>
                    </div>
                </div>
                <div class="chart-container">
                    <h3>Category Breakdown</h3>
                    <div class="chart-wrapper">
                        <canvas id="categoryPieChart"></canvas>
                    </div>
                </div>
            </div>
            <div class="chart-container" style="margin-top: 2rem;">
                <h3>Category Spending by Month</h3>
                <div class="chart-wrapper">
                    <canvas id="categoryByMonthChart"></canvas>
                </div>
            </div>
            </div>
        </section>

        <section class="monthly-section">
            <div class="section-header" onclick="toggleSection(this)">
                <h2><span class="toggle">▼</span> <span data-tooltip="Expenses appearing 6+ months with consistent amounts">Monthly Recurring</span></h2>
                <span class="section-total"><span class="section-monthly">{fmt(stats['monthly_avg'])}/mo</span> · <span class="section-ytd">{fmt(stats['monthly_total'])}</span> <span class="section-pct">({stats['monthly_total']/actual*100:.1f}%)</span></span>
            </div>
            <div class="section-content">
            <div class="table-wrapper">
            <table id="monthly-table">
                <thead>
                    <tr>
                        <th onclick="sortTable('monthly-table', 0, 'string')">Merchant</th>
                        <th onclick="sortTable('monthly-table', 1, 'number')" data-tooltip="Number of months with transactions">Months</th>
                        <th onclick="sortTable('monthly-table', 2, 'number')" data-tooltip="Total transaction count">Count</th>
                        <th data-tooltip="avg = average when active, /12 = YTD divided by 12">Type</th>
                        <th onclick="sortTable('monthly-table', 4, 'money')" data-tooltip="Monthly cost based on Type calculation">Monthly</th>
                        <th onclick="sortTable('monthly-table', 5, 'money')" data-tooltip="Year-to-date total">YTD</th>
                        <th onclick="sortTable('monthly-table', 6, 'number')" data-tooltip="Percentage of section total">%</th>
                    </tr>
                </thead>
                <tbody>'''

    # Monthly recurring rows
    sorted_monthly = sorted(monthly_merchants.items(),
        key=lambda x: x[1]['avg_when_active'] if x[1]['is_consistent'] else x[1]['total']/12,
        reverse=True)
    for merchant, data in sorted_monthly:
        if data['is_consistent']:
            calc_type = '<span class="badge avg" data-tooltip="Average when active — consistent monthly payments">avg</span>'
            monthly = data['avg_when_active']
        else:
            calc_type = '<span class="badge div" data-tooltip="YTD ÷ 12 — irregular payment amounts">/12</span>'
            monthly = data['total'] / 12
        section_total = stats['monthly_total']
        pct = (data['total'] / section_total * 100) if section_total > 0 else 0
        merchant_id = make_merchant_id(merchant)
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        category_id = make_category_id(data.get('category', ''))
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-category-id="{category_id}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td>{data['months_active']}</td>
                        <td>{data['count']}</td>
                        <td>{calc_type}</td>
                        <td class="money">{fmt(monthly)}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows (hidden by default)
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}" data-month="{txn['month']}" data-category="{cat_data}" data-category-id="{category_id}">
                        <td colspan="7"><div class="txn-detail"><span class="txn-date">{txn['date']}</span><span class="txn-desc">{txn['description']}</span><span class="txn-amount">{fmt_dec(txn['amount'])}</span><span class="txn-source {txn['source'].lower()}">{txn['source']}</span>{location_badge(txn.get('location'))}</div></td>
                    </tr>'''

    html += f'''
                    <tr class="total-row">
                        <td>Total</td>
                        <td></td>
                        <td></td>
                        <td></td>
                        <td class="money">{fmt(stats['monthly_avg'])}/mo</td>
                        <td class="money">{fmt(stats['monthly_total'])}</td>
                        <td></td>
                    </tr>
                </tbody>
            </table>
            </div>
            </div>
        </section>

        <section class="annual-section">
            <div class="section-header" onclick="toggleSection(this)">
                <h2><span class="toggle">▼</span> <span data-tooltip="Once-a-year expenses like insurance or annual subscriptions">Annual Bills</span></h2>
                <span class="section-total">{fmt(stats['annual_total'])} <span class="section-pct">({stats['annual_total']/actual*100:.1f}%)</span></span>
            </div>
            <div class="section-content">
            <div class="table-wrapper">
            <table id="annual-table">
                <thead>
                    <tr>
                        <th onclick="sortTable('annual-table', 0, 'string')">Merchant</th>
                        <th onclick="sortTable('annual-table', 1, 'string')">Category</th>
                        <th onclick="sortTable('annual-table', 2, 'number')">Count</th>
                        <th onclick="sortTable('annual-table', 3, 'money')">Total</th>
                        <th onclick="sortTable('annual-table', 4, 'number')">%</th>
                    </tr>
                </thead>
                <tbody>'''

    # Annual bills rows
    sorted_annual = sorted(annual_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_annual:
        section_total = stats['annual_total']
        pct = (data['total'] / section_total * 100) if section_total > 0 else 0
        merchant_id = make_merchant_id(merchant)
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        category_id = make_category_id(data.get('category', ''))
        subcategory_id = make_category_id(data.get('subcategory', ''))
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-category-id="{category_id}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="category clickable" data-category-id="{subcategory_id}" onclick="addFilterFromCell(event, this, 'category')">{data['subcategory']}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}" data-month="{txn['month']}" data-category="{cat_data}" data-category-id="{category_id}">
                        <td colspan="5"><div class="txn-detail"><span class="txn-date">{txn['date']}</span><span class="txn-desc">{txn['description']}</span><span class="txn-amount">{fmt_dec(txn['amount'])}</span><span class="txn-source {txn['source'].lower()}">{txn['source']}</span>{location_badge(txn.get('location'))}</div></td>
                    </tr>'''

    html += f'''
                    <tr class="total-row">
                        <td>Total</td>
                        <td></td>
                        <td></td>
                        <td class="money">{fmt(stats['annual_total'])}</td>
                        <td></td>
                    </tr>
                </tbody>
            </table>
            </div>
            </div>
        </section>

        <section class="periodic-section">
            <div class="section-header" onclick="toggleSection(this)">
                <h2><span class="toggle">▼</span> <span data-tooltip="Regular but not monthly expenses (quarterly, bi-annual)">Periodic Recurring</span></h2>
                <span class="section-total">{fmt(stats['periodic_total'])} <span class="section-pct">({stats['periodic_total']/actual*100:.1f}%)</span></span>
            </div>
            <div class="section-content">
            <div class="table-wrapper">
            <table id="periodic-table">
                <thead>
                    <tr>
                        <th onclick="sortTable('periodic-table', 0, 'string')">Merchant</th>
                        <th onclick="sortTable('periodic-table', 1, 'string')">Category</th>
                        <th onclick="sortTable('periodic-table', 2, 'number')">Count</th>
                        <th onclick="sortTable('periodic-table', 3, 'money')">Total</th>
                        <th onclick="sortTable('periodic-table', 4, 'number')">%</th>
                    </tr>
                </thead>
                <tbody>'''

    # Periodic bills rows
    sorted_periodic = sorted(periodic_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_periodic:
        section_total = stats['periodic_total']
        pct = (data['total'] / section_total * 100) if section_total > 0 else 0
        merchant_id = make_merchant_id(merchant)
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        category_id = make_category_id(data.get('category', ''))
        subcategory_id = make_category_id(data.get('subcategory', ''))
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-category-id="{category_id}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="category clickable" data-category-id="{subcategory_id}" onclick="addFilterFromCell(event, this, 'category')">{data['subcategory']}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}" data-month="{txn['month']}" data-category="{cat_data}" data-category-id="{category_id}">
                        <td colspan="5"><div class="txn-detail"><span class="txn-date">{txn['date']}</span><span class="txn-desc">{txn['description']}</span><span class="txn-amount">{fmt_dec(txn['amount'])}</span><span class="txn-source {txn['source'].lower()}">{txn['source']}</span>{location_badge(txn.get('location'))}</div></td>
                    </tr>'''

    html += f'''
                    <tr class="total-row">
                        <td>Total</td>
                        <td></td>
                        <td></td>
                        <td class="money">{fmt(stats['periodic_total'])}</td>
                        <td></td>
                    </tr>
                </tbody>
            </table>
            </div>
            </div>
        </section>

        <section class="travel-section">
            <div class="section-header" onclick="toggleSection(this)">
                <h2><span class="toggle">▼</span> <span data-tooltip="Spending outside your home location(s)">Travel / Trips</span></h2>
                <span class="section-total">{fmt(stats['travel_total'])} <span class="section-pct">({stats['travel_total']/actual*100:.1f}%)</span></span>
            </div>
            <div class="section-content">
            <div class="table-wrapper">
            <table id="travel-table">
                <thead>
                    <tr>
                        <th onclick="sortTable('travel-table', 0, 'string')">Merchant</th>
                        <th onclick="sortTable('travel-table', 1, 'string')">Category</th>
                        <th onclick="sortTable('travel-table', 2, 'number')">Count</th>
                        <th onclick="sortTable('travel-table', 3, 'money')">Total</th>
                        <th onclick="sortTable('travel-table', 4, 'number')">%</th>
                    </tr>
                </thead>
                <tbody>'''

    # Travel rows
    sorted_travel = sorted(travel_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_travel:
        section_total = stats['travel_total']
        pct = (data['total'] / section_total * 100) if section_total > 0 else 0
        merchant_id = make_merchant_id(merchant)
        cat_data = f"{data.get('category', 'travel')}/{data.get('subcategory', '')}".lower()
        category_id = make_category_id(data.get('category', 'travel'))
        category_display = data.get('category', 'Travel')
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-category-id="{category_id}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="clickable" data-category-id="{category_id}" onclick="addFilterFromCell(event, this, 'category')">{category_display}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}" data-month="{txn['month']}" data-category="{cat_data}" data-category-id="{category_id}">
                        <td colspan="5"><div class="txn-detail"><span class="txn-date">{txn['date']}</span><span class="txn-desc">{txn['description']}</span><span class="txn-amount">{fmt_dec(txn['amount'])}</span><span class="txn-source {txn['source'].lower()}">{txn['source']}</span>{location_badge(txn.get('location'))}</div></td>
                    </tr>'''

    html += f'''
                    <tr class="total-row">
                        <td>Total</td>
                        <td></td>
                        <td></td>
                        <td class="money">{fmt(stats['travel_total'])}</td>
                        <td></td>
                    </tr>
                </tbody>
            </table>
            </div>
            </div>
        </section>

        <section class="oneoff-section">
            <div class="section-header" onclick="toggleSection(this)">
                <h2><span class="toggle">▼</span> <span data-tooltip="Single large purchases that don't recur">One-Off Purchases</span></h2>
                <span class="section-total">{fmt(stats['one_off_total'])} <span class="section-pct">({stats['one_off_total']/actual*100:.1f}%)</span></span>
            </div>
            <div class="section-content">
            <div class="table-wrapper">
            <table id="oneoff-table">
                <thead>
                    <tr>
                        <th onclick="sortTable('oneoff-table', 0, 'string')">Merchant</th>
                        <th onclick="sortTable('oneoff-table', 1, 'string')">Category</th>
                        <th onclick="sortTable('oneoff-table', 2, 'number')">Count</th>
                        <th onclick="sortTable('oneoff-table', 3, 'money')">Total</th>
                        <th onclick="sortTable('oneoff-table', 4, 'number')">%</th>
                    </tr>
                </thead>
                <tbody>'''

    # One-off rows
    sorted_oneoff = sorted(one_off_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_oneoff:
        section_total = stats['one_off_total']
        pct = (data['total'] / section_total * 100) if section_total > 0 else 0
        merchant_id = make_merchant_id(merchant)
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        category_id = make_category_id(data.get('category', ''))
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-category-id="{category_id}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="category clickable" data-category-id="{category_id}" onclick="addFilterFromCell(event, this, 'category')">{data['category']}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}" data-month="{txn['month']}" data-category="{cat_data}" data-category-id="{category_id}">
                        <td colspan="5"><div class="txn-detail"><span class="txn-date">{txn['date']}</span><span class="txn-desc">{txn['description']}</span><span class="txn-amount">{fmt_dec(txn['amount'])}</span><span class="txn-source {txn['source'].lower()}">{txn['source']}</span>{location_badge(txn.get('location'))}</div></td>
                    </tr>'''

    html += f'''
                    <tr class="total-row">
                        <td>Total</td>
                        <td></td>
                        <td></td>
                        <td class="money">{fmt(stats['one_off_total'])}</td>
                        <td></td>
                    </tr>
                </tbody>
            </table>
            </div>
            </div>
        </section>

        <section class="variable-section">
            <div class="section-header" onclick="toggleSection(this)">
                <h2><span class="toggle">▼</span> <span data-tooltip="Day-to-day spending. Monthly total = sum of Avg/Mo values.">Variable / Discretionary</span></h2>
                <span class="section-total"><span class="section-monthly">{fmt(stats['variable_monthly'])}/mo</span> · <span class="section-ytd">{fmt(stats['variable_total'])}</span> <span class="section-pct">({stats['variable_total']/actual*100:.1f}%)</span></span>
            </div>
            <div class="section-content">
            <div class="table-wrapper">
            <table id="variable-table">
                <thead>
                    <tr>
                        <th onclick="sortTable('variable-table', 0, 'string')">Merchant</th>
                        <th onclick="sortTable('variable-table', 1, 'string')">Category</th>
                        <th onclick="sortTable('variable-table', 2, 'number')" data-tooltip="Number of months with transactions">Months</th>
                        <th onclick="sortTable('variable-table', 3, 'number')" data-tooltip="Total transaction count">Count</th>
                        <th onclick="sortTable('variable-table', 4, 'money')" data-tooltip="YTD ÷ months active — average spend per active month">Avg/Mo</th>
                        <th onclick="sortTable('variable-table', 5, 'money')" data-tooltip="Year-to-date total">YTD</th>
                        <th onclick="sortTable('variable-table', 6, 'number')" data-tooltip="Percentage of section total">%</th>
                    </tr>
                </thead>
                <tbody>'''

    # Variable rows - show individual merchants
    sorted_var = sorted(variable_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_var:
        months = data['months_active']
        avg = data['total'] / months if months > 0 else 0
        section_total = stats['variable_total']
        pct = (data['total'] / section_total * 100) if section_total > 0 else 0
        merchant_id = make_merchant_id(merchant)
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        category_id = make_category_id(data.get('category', ''))
        subcategory_id = make_category_id(data.get('subcategory', ''))
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-category-id="{category_id}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="category"><span class="clickable" data-category-id="{category_id}" onclick="addFilterFromCell(event, this, 'category')">{data['category']}</span>/<span class="clickable" data-category-id="{subcategory_id}" onclick="addFilterFromCell(event, this, 'category')">{data['subcategory']}</span></td>
                        <td>{months}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(avg)}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}" data-month="{txn['month']}" data-category="{cat_data}" data-category-id="{category_id}">
                        <td colspan="7"><div class="txn-detail"><span class="txn-date">{txn['date']}</span><span class="txn-desc">{txn['description']}</span><span class="txn-amount">{fmt_dec(txn['amount'])}</span><span class="txn-source {txn['source'].lower()}">{txn['source']}</span>{location_badge(txn.get('location'))}</div></td>
                    </tr>'''

    html += f'''
                    <tr class="total-row">
                        <td>Total</td>
                        <td></td>
                        <td></td>
                        <td></td>
                        <td class="money">{fmt(stats['variable_monthly'])}/mo</td>
                        <td class="money">{fmt(stats['variable_total'])}</td>
                        <td></td>
                    </tr>
                </tbody>
            </table>
            </div>
            </div>
        </section>

        <footer>
            <p>Analysis based on {stats['count']:,} transactions across {stats['num_months']} months</p>
        </footer>
    </div>

    <!-- Data injection for JavaScript -->
    <script>
        // Injected data from Python - accessed by spending_report.js via window.*
        window.currencyFormat = '{currency_format}';
        window.originalTotals = {{
            monthly: {stats['monthly_avg']},
            monthlyYtd: {stats['monthly_total']},
            annual: {stats['annual_total']},
            periodic: {stats['periodic_total']},
            travel: {stats['travel_total']},
            oneoff: {stats['one_off_total']},
            variable: {stats['variable_monthly']},
            variableYtd: {stats['variable_total']},
            totalYtd: {actual}
        }};
        window.displayNames = {json.dumps({
            'category': {make_category_id(cat): cat for cat in sorted_categories},
            'merchant': {make_merchant_id(m): m for m in sorted_merchants},
            'location': {make_location_id(loc): loc for loc in sorted_locations}
        })};
        window.autocompleteData = [
            {','.join(f'{{"text": "{cat}", "type": "category", "id": "{make_category_id(cat)}"}}' for cat in sorted_categories)},
            {','.join(f'{{"text": "{merchant.replace(chr(34), chr(92)+chr(34))}", "type": "merchant", "id": "{make_merchant_id(merchant)}"}}' for merchant in sorted_merchants)},
            {','.join(f'{{"text": "{loc}", "type": "location", "id": "{make_location_id(loc)}"}}' for loc in sorted_locations)}
        ];
        window.embeddingsData = {embeddings_json};
        window.sectionData = {section_data_json};
        window.activeFilters = [];
    </script>

    <!-- Spending Report JavaScript (embedded from spending_report.js) -->
    <script>
{spending_report_js}
    </script>


    <!-- Chart rendering -->
    <script>
        // Chart data from Python (global for filter updates)
        window.chartData = {chart_data_json};
        const currencySymbol = '{currency_format}'.split('{{')[0] || '$';
        
        // Chart.js default configuration for theme support
        Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif";
        Chart.defaults.color = getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim();
        Chart.defaults.borderColor = getComputedStyle(document.documentElement).getPropertyValue('--border-table').trim();
        
        // Color palette for categories
        const categoryColors = {{
            'Food': 'rgba(79, 172, 254, 0.8)',        // Blue
            'Shopping': 'rgba(240, 147, 251, 0.8)',   // Pink
            'Transport': 'rgba(74, 222, 128, 0.8)',   // Green
            'Bills': 'rgba(251, 191, 36, 0.8)',       // Yellow
            'Subscriptions': 'rgba(147, 197, 253, 0.8)', // Light blue
            'Health': 'rgba(251, 113, 133, 0.8)',     // Rose
            'Travel': 'rgba(196, 181, 253, 0.8)',     // Purple
            'Home': 'rgba(253, 186, 116, 0.8)',       // Orange
            'Personal': 'rgba(163, 230, 53, 0.8)',    // Lime
            'Entertainment': 'rgba(251, 146, 60, 0.8)', // Orange
            'Education': 'rgba(139, 92, 246, 0.8)',   // Violet
            'Other': 'rgba(156, 163, 175, 0.8)'       // Gray
        }};
        
        // Update charts on theme change
        function updateChartsForTheme() {{
            Chart.defaults.color = getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim();
            Chart.defaults.borderColor = getComputedStyle(document.documentElement).getPropertyValue('--border-table').trim();
            
            // Recreate all charts
            if (window.monthlyTrendChart) {{
                window.monthlyTrendChart.destroy();
                createMonthlyTrendChart();
            }}
            if (window.categoryPieChart) {{
                window.categoryPieChart.destroy();
                createCategoryPieChart();
            }}
            if (window.categoryByMonthChart) {{
                window.categoryByMonthChart.destroy();
                createCategoryByMonthChart();
            }}
        }}
        
        // 1. Monthly Spending Trend (Line Chart)
        function createMonthlyTrendChart() {{
            const ctx = document.getElementById('monthlyTrendChart');
            if (!ctx) return;
            
            window.monthlyTrendChart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: window.chartData.monthly.labels,
                    datasets: [{{
                        label: 'Total Spending',
                        data: window.chartData.monthly.data,
                        borderColor: 'rgba(79, 172, 254, 1)',
                        backgroundColor: 'rgba(79, 172, 254, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.3,
                        pointRadius: 4,
                        pointHoverRadius: 6
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    onClick: (event, elements) => {{
                        if (elements.length > 0) {{
                            const monthLabel = window.monthlyTrendChart.data.labels[elements[0].index];
                            const monthKey = monthLabelToKey(monthLabel);
                            addFilter(monthKey, 'month');
                        }}
                    }},
                    plugins: {{
                        legend: {{
                            display: false
                        }},
                        tooltip: {{
                            callbacks: {{
                                label: function(context) {{
                                    return 'Total: ' + currencySymbol + context.parsed.y.toLocaleString('en-US', {{ maximumFractionDigits: 0 }});
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        y: {{
                            beginAtZero: true,
                            ticks: {{
                                callback: function(value) {{
                                    return currencySymbol + value.toLocaleString('en-US', {{ maximumFractionDigits: 0 }});
                                }}
                            }},
                            grid: {{
                                color: getComputedStyle(document.documentElement).getPropertyValue('--border-table').trim()
                            }}
                        }},
                        x: {{
                            grid: {{
                                display: false
                            }}
                        }}
                    }}
                }}
            }});
        }}

        // 2. Category Pie Chart
        function createCategoryPieChart() {{
            const ctx = document.getElementById('categoryPieChart');
            if (!ctx) return;
            
            const colors = window.chartData.categoryPie.labels.map(label => 
                categoryColors[label] || 'rgba(156, 163, 175, 0.8)'
            );
            
            window.categoryPieChart = new Chart(ctx, {{
                type: 'doughnut',
                data: {{
                    labels: window.chartData.categoryPie.labels,
                    datasets: [{{
                        data: window.chartData.categoryPie.data,
                        backgroundColor: colors,
                        borderWidth: 2,
                        borderColor: getComputedStyle(document.documentElement).getPropertyValue('--bg-table').trim()
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    onClick: (event, elements) => {{
                        if (elements.length > 0) {{
                            const category = window.categoryPieChart.data.labels[elements[0].index];
                            addFilter(category.toLowerCase(), 'category', category);
                        }}
                    }},
                    plugins: {{
                        legend: {{
                            position: 'bottom',
                            labels: {{
                                padding: 15,
                                font: {{
                                    size: 11
                                }}
                            }}
                        }},
                        tooltip: {{
                            callbacks: {{
                                label: function(context) {{
                                    const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                    const percentage = ((context.parsed / total) * 100).toFixed(1);
                                    return context.label + ': ' + currencySymbol + context.parsed.toLocaleString('en-US', {{ maximumFractionDigits: 0 }}) + ' (' + percentage + '%)';
                                }}
                            }}
                        }}
                    }}
                }}
            }});
        }}

        // 3. Category Spending by Month (Stacked Bar Chart)
        function createCategoryByMonthChart() {{
            const ctx = document.getElementById('categoryByMonthChart');
            if (!ctx) return;
            
            const datasets = window.chartData.categoryByMonth.datasets.map(ds => ({{
                label: ds.label,
                data: ds.data,
                backgroundColor: categoryColors[ds.label] || 'rgba(156, 163, 175, 0.8)',
                borderWidth: 0
            }}));
            
            window.categoryByMonthChart = new Chart(ctx, {{
                type: 'bar',
                data: {{
                    labels: window.chartData.categoryByMonth.labels,
                    datasets: datasets
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    onClick: (event, elements) => {{
                        if (elements.length > 0) {{
                            const datasetIndex = elements[0].datasetIndex;
                            const dataIndex = elements[0].index;
                            const category = window.categoryByMonthChart.data.datasets[datasetIndex].label;
                            const monthLabel = window.categoryByMonthChart.data.labels[dataIndex];
                            const monthKey = monthLabelToKey(monthLabel);
                            addFilter(category.toLowerCase(), 'category', category);
                            addFilter(monthKey, 'month');
                        }}
                    }},
                    plugins: {{
                        legend: {{
                            position: 'bottom',
                            labels: {{
                                padding: 10,
                                font: {{
                                    size: 10
                                }},
                                boxWidth: 12
                            }}
                        }},
                        tooltip: {{
                            callbacks: {{
                                label: function(context) {{
                                    return context.dataset.label + ': ' + currencySymbol + context.parsed.y.toLocaleString('en-US', {{ maximumFractionDigits: 0 }});
                                }},
                                footer: function(tooltipItems) {{
                                    let total = 0;
                                    tooltipItems.forEach(item => {{
                                        total += item.parsed.y;
                                    }});
                                    return 'Total: ' + currencySymbol + total.toLocaleString('en-US', {{ maximumFractionDigits: 0 }});
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{
                            stacked: true,
                            grid: {{
                                display: false
                            }}
                        }},
                        y: {{
                            stacked: true,
                            beginAtZero: true,
                            ticks: {{
                                callback: function(value) {{
                                    return currencySymbol + value.toLocaleString('en-US', {{ maximumFractionDigits: 0 }});
                                }}
                            }},
                            grid: {{
                                color: getComputedStyle(document.documentElement).getPropertyValue('--border-table').trim()
                            }}
                        }}
                    }}
                }}
            }});
        }}
        
        // Initialize charts when page loads
        document.addEventListener('DOMContentLoaded', function() {{
            createMonthlyTrendChart();
            createCategoryPieChart();
            createCategoryByMonthChart();

            // Load filters from URL hash after charts are ready
            hashToFilters();
        }});
        
        // Re-create charts on theme toggle
        const originalToggleTheme = toggleTheme;
        toggleTheme = function() {{
            originalToggleTheme();
            setTimeout(updateChartsForTheme, 50);
        }};

    </script>

    <!-- Embedded JSON data for LLM analysis tools -->
    <script id="report-data" type="application/json">
{embedded_json}
    </script>
</body>
</html>'''

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)
