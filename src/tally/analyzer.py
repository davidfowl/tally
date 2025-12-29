"""
Spending Analyzer - Core analysis logic.

Analyzes AMEX and BOA transactions using merchant categorization rules.
"""

import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .merchant_utils import normalize_merchant
from .format_parser import FormatSpec

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

    Handles both positive amounts (expenses) and negative amounts (AMEX exports
    often use negative for charges). Credits/refunds are skipped.
    """
    home_locations = home_locations or set()
    transactions = []

    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                amount = float(row['Amount'])
                # AMEX exports may use negative for charges, positive for credits
                # We want expenses (charges), so:
                # - If negative: it's a charge, use absolute value
                # - If positive and small context suggests it's a charge format: use as-is
                # - If zero: skip
                if amount == 0:
                    continue
                # Use absolute value - we'll treat all non-zero as expenses
                # (credits are typically marked differently or we don't care about them)
                amount = abs(amount)

                date = datetime.strptime(row['Date'], '%m/%d/%Y')
                merchant, category, subcategory, match_info = normalize_merchant(
                    row['Description'], rules, amount=amount, txn_date=date.date()
                )
                location = extract_location(row['Description'])

                transactions.append({
                    'date': date,
                    'description': row['Description'],
                    'amount': amount,
                    'merchant': merchant,
                    'category': category,
                    'subcategory': subcategory,
                    'source': 'AMEX',
                    'location': location,
                    'is_travel': is_travel_location(location, home_locations),
                    'match_info': match_info,
                })
            except (ValueError, KeyError):
                continue

    return transactions


def parse_boa(filepath, rules, home_locations=None):
    """Parse BOA statement file and return list of transactions."""
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

                if amount >= 0:  # Skip credits/income
                    continue

                merchant, category, subcategory, match_info = normalize_merchant(
                    description, rules, amount=abs(amount), txn_date=date.date()
                )
                location = extract_location(description)

                transactions.append({
                    'date': date,
                    'description': description,
                    'amount': abs(amount),
                    'merchant': merchant,
                    'match_info': match_info,
                    'category': category,
                    'subcategory': subcategory,
                    'source': 'BOA',
                    'location': location,
                    'is_travel': is_travel_location(location, home_locations)
                })
            except ValueError:
                continue

    return transactions


def parse_generic_csv(filepath, format_spec, rules, home_locations=None, source_name='CSV', decimal_separator='.'):
    """
    Parse a CSV file using a custom format specification.

    Args:
        filepath: Path to the CSV file
        format_spec: FormatSpec defining column mappings
        rules: Merchant categorization rules
        home_locations: Set of location codes considered "home"
        source_name: Name to use for transaction source (default: 'CSV')
        decimal_separator: Character used as decimal separator ('.' or ',')

    Returns:
        List of transaction dictionaries
    """
    home_locations = home_locations or set()
    transactions = []

    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)

        # Skip header if expected
        if format_spec.has_header:
            next(reader, None)

        for row in reader:
            try:
                # Ensure row has enough columns
                required_cols = [format_spec.date_column, format_spec.amount_column]
                if format_spec.description_column is not None:
                    required_cols.append(format_spec.description_column)
                if format_spec.custom_captures:
                    required_cols.extend(format_spec.custom_captures.values())
                if format_spec.location_column is not None:
                    required_cols.append(format_spec.location_column)
                max_col = max(required_cols)

                if len(row) <= max_col:
                    continue  # Skip malformed rows

                # Extract values
                date_str = row[format_spec.date_column].strip()
                amount_str = row[format_spec.amount_column].strip()

                # Build description from either mode
                if format_spec.description_column is not None:
                    # Mode 1: Simple {description}
                    description = row[format_spec.description_column].strip()
                else:
                    # Mode 2: Custom captures + template
                    captures = {}
                    for name, col_idx in format_spec.custom_captures.items():
                        captures[name] = row[col_idx].strip() if col_idx < len(row) else ''
                    description = format_spec.description_template.format(**captures)

                # Skip empty rows
                if not date_str or not description or not amount_str:
                    continue

                # Parse date - handle optional day suffix (e.g., "01/02/2017  Mon")
                date_str = date_str.split()[0]  # Take just the date part
                date = datetime.strptime(date_str, format_spec.date_format)

                # Parse amount (handle locale-specific formats)
                amount = parse_amount(amount_str, decimal_separator)

                # Apply negation if specified (for credit cards where positive = charge)
                if format_spec.negate_amount:
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
                    description, rules, amount=amount, txn_date=date.date()
                )

                transactions.append({
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
                })

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
# ANALYSIS - OCCURRENCE-BASED CLASSIFICATION
# ============================================================================

def classify_by_occurrence(merchant, data, num_months=12):
    """Classify a merchant based purely on transaction occurrence patterns.

    NO hardcoded category rules - classification is entirely based on:
    - How many months the merchant appears (frequency)
    - How consistent the payment amounts are (CV)
    - The size of individual payments vs average (max/avg ratio)
    - Total annual spend

    Categories:
        - 'excluded': Transfers, cash, payments
        - 'monthly': Consistent payments appearing in 75%+ of available months (min 3)
        - 'annual': High-value lumpy payments (even if frequent) - tuition, insurance
        - 'travel': Travel-related merchants
        - 'one_off': High-value infrequent (home improvement, medical procedures)
        - 'variable': Discretionary spending

    Args:
        merchant: The merchant name
        data: Transaction data dictionary
        num_months: Total months of data available (for proportional thresholds)

    Returns: tuple of (classification_string, reasoning_dict)
    """
    category = data['category']
    subcategory = data['subcategory']
    months_active = data.get('months_active', 1)
    count = data['count']
    total = data['total']
    cv = data.get('cv', 0)  # Coefficient of variation
    max_payment = data.get('max_payment', 0)
    avg_per_txn = total / count if count > 0 else 0
    is_consistent = data.get('is_consistent', True)

    # Build reasoning structure
    reasoning = {
        'trace': [],
        'thresholds': {},
        'decision': '',
        'category': category,
        'subcategory': subcategory,
        'months_active': months_active,
        'num_months': num_months,
        'cv': round(cv, 2),
        'is_consistent': is_consistent,
    }

    # Calculate thresholds upfront for tracing
    bill_threshold = max(2, int(num_months * 0.5))
    general_threshold = max(3, int(num_months * 0.75))
    reasoning['thresholds'] = {
        'bill_threshold': bill_threshold,
        'general_threshold': general_threshold,
    }

    # =========================================================================
    # EXCLUDED: Transfers, payments, cash, income
    # =========================================================================
    if category in ('Transfers', 'Cash', 'Income'):
        reasoning['trace'].append(f"✓ IS excluded: {category} in [Transfers, Cash, Income]")
        reasoning['decision'] = f"Excluded: {category} category"
        return ('excluded', reasoning)
    reasoning['trace'].append(f"✗ NOT excluded: {category} not in [Transfers, Cash, Income]")

    # =========================================================================
    # TRAVEL: Either explicit travel category OR location-based travel flag
    # - category='Travel' from merchant rules (airlines, hotels, etc.)
    # - is_travel=True from international location detection
    # =========================================================================
    is_travel = data.get('is_travel', False)
    if category == 'Travel' or is_travel:
        if category == 'Travel':
            reasoning['trace'].append(f"✓ IS travel: category=Travel")
            reasoning['decision'] = "Travel: category is Travel"
        else:
            reasoning['trace'].append(f"✓ IS travel: is_travel=true (location-based)")
            reasoning['decision'] = "Travel: international location detected"
        return ('travel', reasoning)
    reasoning['trace'].append(f"✗ NOT travel: category={category}, is_travel=false")

    # =========================================================================
    # ANNUAL BILLS: True once-a-year expenses
    # - Insurance premiums paid annually
    # - Tax payments
    # - Annual membership fees
    # - Charity donations (annual)
    #
    # Key: low frequency (1-2 times) AND bill-type category
    # =========================================================================
    annual_categories = {
        ('Bills', 'Insurance'),
        ('Bills', 'Tax'),
        ('Bills', 'Membership'),
        ('Family', 'Charity'),
        ('Charity', 'Donation'),
    }
    if (category, subcategory) in annual_categories:
        if months_active <= 2 and count <= 2:
            reasoning['trace'].append(f"✓ IS annual: ({category}, {subcategory}) in annual categories, {months_active} months, {count} txns")
            reasoning['decision'] = f"Annual: {category}>{subcategory} with {count} transaction(s) in {months_active} month(s)"
            return ('annual', reasoning)
        else:
            reasoning['trace'].append(f"✗ NOT annual: ({category}, {subcategory}) is annual category but {months_active} months > 2 or {count} txns > 2")
    else:
        reasoning['trace'].append(f"✗ NOT annual: ({category}, {subcategory}) not in annual categories")

    # =========================================================================
    # PERIODIC RECURRING: Regular non-monthly bills
    # - School tuition (paid throughout school year)
    # - Quarterly insurance payments
    # - Medical/dental treatment series
    # - Orthodontics payments
    #
    # Key: recurring pattern but less than monthly, OR inherently periodic
    # =========================================================================
    # Education/Tuition is inherently periodic (school year pattern)
    if category == 'Education' and subcategory == 'Tuition':
        reasoning['trace'].append(f"✓ IS periodic: Education>Tuition (inherently periodic)")
        reasoning['decision'] = "Periodic: Tuition follows school year pattern"
        return ('periodic', reasoning)

    # Quarterly insurance (appears 3+ months)
    if category == 'Bills' and subcategory == 'Insurance' and months_active >= 3:
        reasoning['trace'].append(f"✓ IS periodic: Bills>Insurance with {months_active} months (quarterly pattern)")
        reasoning['decision'] = f"Periodic: Insurance with {months_active} months of payments (quarterly)"
        return ('periodic', reasoning)

    # Medical/dental treatments that span multiple visits
    if category == 'Health' and subcategory in ('Medical', 'Dental', 'Orthodontics'):
        if months_active >= 2 or count >= 2:
            reasoning['trace'].append(f"✓ IS periodic: Health>{subcategory} with {months_active} months, {count} visits")
            reasoning['decision'] = f"Periodic: {subcategory} treatment series ({count} visits over {months_active} months)"
            return ('periodic', reasoning)

    # High-value lumpy payments that are bill-like (high CV pattern)
    if total > 5000 and max_payment > 1000:
        if cv > 0.8:
            max_avg_ratio = max_payment / avg_per_txn if avg_per_txn > 0 else 0
            if max_avg_ratio > 3:
                # Only if it's a bill-type category
                if category in ('Bills', 'Education', 'Health'):
                    reasoning['trace'].append(f"✓ IS periodic: high-value lumpy (total>${total:.0f}, CV={cv:.2f}, max/avg={max_avg_ratio:.1f})")
                    reasoning['decision'] = f"Periodic: High-value lumpy payments (CV={cv:.2f} > 0.8)"
                    return ('periodic', reasoning)

    reasoning['trace'].append(f"✗ NOT periodic: no periodic patterns matched")

    # =========================================================================
    # MONTHLY: Appears in 50%+ of available months (min 2) for bills/utilities,
    # or 75%+ (min 3) for other recurring categories
    # Only bills, utilities, subscriptions, and essential services count
    # Shopping and restaurants are ALWAYS variable, no matter how frequent
    # =========================================================================

    # Bills, utilities, subscriptions are inherently recurring - use lenient threshold
    if category in ('Bills', 'Utilities', 'Subscriptions') and months_active >= bill_threshold:
        reasoning['trace'].append(f"✓ IS monthly: {category} with {months_active}/{num_months} months (>= {bill_threshold} bill threshold)")
        reasoning['decision'] = f"Monthly: {category} appears {months_active}/{num_months} months (50% threshold = {bill_threshold})"
        return ('monthly', reasoning)

    if months_active >= general_threshold:
        # These categories are true monthly recurring expenses
        if category in ('Bills', 'Utilities', 'Subscriptions'):
            reasoning['trace'].append(f"✓ IS monthly: {category} with {months_active}/{num_months} months (>= {general_threshold} general threshold)")
            reasoning['decision'] = f"Monthly: {category} appears {months_active}/{num_months} months (75% threshold = {general_threshold})"
            return ('monthly', reasoning)
        # Essential services that recur monthly
        if category == 'Home' and subcategory in ('Lawn', 'Security', 'Cleaning'):
            reasoning['trace'].append(f"✓ IS monthly: Home>{subcategory} service with {months_active}/{num_months} months")
            reasoning['decision'] = f"Monthly: {subcategory} service appears {months_active}/{num_months} months"
            return ('monthly', reasoning)
        if category == 'Health' and subcategory in ('Gym', 'Fitness', 'Pharmacy'):
            reasoning['trace'].append(f"✓ IS monthly: Health>{subcategory} with {months_active}/{num_months} months")
            reasoning['decision'] = f"Monthly: {subcategory} appears {months_active}/{num_months} months"
            return ('monthly', reasoning)
        if category == 'Food' and subcategory in ('Grocery', 'Delivery'):
            reasoning['trace'].append(f"✓ IS monthly: Food>{subcategory} with {months_active}/{num_months} months")
            reasoning['decision'] = f"Monthly: {subcategory} appears {months_active}/{num_months} months"
            return ('monthly', reasoning)
        if category == 'Transport' and subcategory in ('Gas', 'Parking', 'Transit'):
            reasoning['trace'].append(f"✓ IS monthly: Transport>{subcategory} with {months_active}/{num_months} months")
            reasoning['decision'] = f"Monthly: {subcategory} appears {months_active}/{num_months} months"
            return ('monthly', reasoning)
        if category == 'Personal' and subcategory in ('Childcare', 'Services', 'Grooming'):
            reasoning['trace'].append(f"✓ IS monthly: Personal>{subcategory} with {months_active}/{num_months} months")
            reasoning['decision'] = f"Monthly: {subcategory} appears {months_active}/{num_months} months"
            return ('monthly', reasoning)

    reasoning['trace'].append(f"✗ NOT monthly: {months_active}/{num_months} months, category={category}>{subcategory}")

    # =========================================================================
    # ONE-OFF: High-value infrequent purchases
    # - Home improvement projects
    # - Major appliances
    # - Luxury/jewelry purchases
    # - Electronics
    # - Medical procedures (cosmetic, elective surgery, etc.)
    #
    # Detected by: low frequency + high total + purchase category
    # =========================================================================

    # Procedure subcategory is always one-off (regardless of frequency)
    if subcategory == 'Procedure':
        reasoning['trace'].append(f"✓ IS one-off: subcategory=Procedure (always one-off)")
        reasoning['decision'] = "One-off: Medical/cosmetic procedure"
        return ('one_off', reasoning)

    one_off_categories = ('Shopping', 'Home', 'Personal')
    one_off_subcategories = (
        'Improvement', 'Appliance', 'HVAC', 'Repair', 'Furniture',
        'Electronics', 'Jewelry', 'Luxury', 'One-Off',
    )
    if months_active <= 3 and total > 1000:
        # Shopping or home categories are one-off purchases
        if category in one_off_categories:
            reasoning['trace'].append(f"✓ IS one-off: {category} with ${total:.0f} total in {months_active} months")
            reasoning['decision'] = f"One-off: {category} purchase (${total:.0f} in {months_active} months)"
            return ('one_off', reasoning)
        # Specific subcategories that are one-off
        if subcategory in one_off_subcategories:
            reasoning['trace'].append(f"✓ IS one-off: subcategory={subcategory} with ${total:.0f} total")
            reasoning['decision'] = f"One-off: {subcategory} purchase (${total:.0f})"
            return ('one_off', reasoning)

    reasoning['trace'].append(f"✗ NOT one-off: doesn't match one-off criteria")

    # =========================================================================
    # VARIABLE: Everything else (discretionary spending)
    # Shopping, restaurants, entertainment - even if frequent
    # =========================================================================
    reasoning['trace'].append(f"✓ IS variable: default classification (discretionary)")
    reasoning['decision'] = f"Variable: {category}>{subcategory} is discretionary spending"
    return ('variable', reasoning)


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
    })
    by_month = defaultdict(float)

    for txn in transactions:
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
            'description': txn['description'],
            'amount': txn['amount'],
            'source': txn['source'],
            'location': txn.get('location')
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
    # CLASSIFY BY OCCURRENCE PATTERN
    # =========================================================================
    monthly_merchants = {}   # Appears 6+ months
    annual_merchants = {}    # True annual bills (insurance, tax - once a year)
    periodic_merchants = {}  # Periodic recurring (tuition, quarterly payments)
    travel_merchants = {}    # Travel-related
    one_off_merchants = {}   # High-value infrequent
    variable_merchants = {}  # Discretionary

    for merchant, data in by_merchant.items():
        classification, reasoning = classify_by_occurrence(merchant, data, num_months)
        # Store reasoning in merchant data for later access
        data['classification'] = classification
        data['reasoning'] = reasoning
        if classification == 'monthly':
            monthly_merchants[merchant] = data
        elif classification == 'annual':
            annual_merchants[merchant] = data
        elif classification == 'periodic':
            periodic_merchants[merchant] = data
        elif classification == 'travel':
            travel_merchants[merchant] = data
        elif classification == 'one_off':
            one_off_merchants[merchant] = data
        elif classification == 'variable':
            variable_merchants[merchant] = data

    # =========================================================================
    # CALCULATE TOTALS
    # =========================================================================
    monthly_total = sum(d['total'] for d in monthly_merchants.values())
    annual_total = sum(d['total'] for d in annual_merchants.values())
    periodic_total = sum(d['total'] for d in periodic_merchants.values())
    travel_total = sum(d['total'] for d in travel_merchants.values())
    one_off_total = sum(d['total'] for d in one_off_merchants.values())
    variable_total = sum(d['total'] for d in variable_merchants.values())

    # =========================================================================
    # CALCULATE TRUE MONTHLY AVERAGES (with reasoning)
    # =========================================================================

    # Monthly recurring: use avg when active for CONSISTENT payments,
    # use YTD/12 for LUMPY payments (like tuition with irregular amounts)
    monthly_avg = 0
    for data in monthly_merchants.values():
        if data['is_consistent']:
            # Consistent payments: use average when active
            monthly_value = data['avg_when_active']
            data['calc_type'] = 'avg'
            data['calc_reasoning'] = f"CV={data['cv']:.2f} (<0.3), payments are consistent"
            data['calc_formula'] = f"avg_when_active = {data['total']:.2f} / {data['months_active']} months = {monthly_value:.2f}"
        else:
            # Lumpy payments: use YTD/12 for budgeting
            monthly_value = data['total'] / 12
            data['calc_type'] = '/12'
            data['calc_reasoning'] = f"CV={data['cv']:.2f} (>=0.3), payments vary significantly"
            data['calc_formula'] = f"total / 12 = {data['total']:.2f} / 12 = {monthly_value:.2f}"
        data['monthly_value'] = monthly_value
        monthly_avg += monthly_value

    # Annual bills: divide by 12 to get monthly equivalent
    annual_monthly = annual_total / 12
    for data in annual_merchants.values():
        monthly_value = data['total'] / 12
        data['calc_type'] = '/12'
        data['calc_reasoning'] = "Annual bill: spread over 12 months"
        data['calc_formula'] = f"total / 12 = {data['total']:.2f} / 12 = {monthly_value:.2f}"
        data['monthly_value'] = monthly_value

    # Periodic bills: divide by 12 to get monthly equivalent
    periodic_monthly = periodic_total / 12
    for data in periodic_merchants.values():
        monthly_value = data['total'] / 12
        data['calc_type'] = '/12'
        data['calc_reasoning'] = "Periodic bill: spread over 12 months"
        data['calc_formula'] = f"total / 12 = {data['total']:.2f} / 12 = {monthly_value:.2f}"
        data['monthly_value'] = monthly_value

    # Travel: divide by 12 (not budgeted monthly)
    for data in travel_merchants.values():
        monthly_value = data['total'] / 12
        data['calc_type'] = '/12'
        data['calc_reasoning'] = "Travel: spread over 12 months for budgeting"
        data['calc_formula'] = f"total / 12 = {data['total']:.2f} / 12 = {monthly_value:.2f}"
        data['monthly_value'] = monthly_value

    # One-off: divide by 12 (not budgeted monthly)
    for data in one_off_merchants.values():
        monthly_value = data['total'] / 12
        data['calc_type'] = '/12'
        data['calc_reasoning'] = "One-off: spread over 12 months for budgeting"
        data['calc_formula'] = f"total / 12 = {data['total']:.2f} / 12 = {monthly_value:.2f}"
        data['monthly_value'] = monthly_value

    # Variable: use average when active for frequent & consistent, pro-rate otherwise
    variable_monthly = 0
    for data in variable_merchants.values():
        if data['months_active'] >= 6 and data['is_consistent']:
            monthly_value = data['avg_when_active']
            data['calc_type'] = 'avg'
            data['calc_reasoning'] = f"Frequent ({data['months_active']} months) and consistent (CV={data['cv']:.2f})"
            data['calc_formula'] = f"avg_when_active = {data['total']:.2f} / {data['months_active']} months = {monthly_value:.2f}"
        else:
            monthly_value = data['total'] / 12
            data['calc_type'] = '/12'
            if data['months_active'] < 6:
                data['calc_reasoning'] = f"Infrequent ({data['months_active']} months < 6): spread over 12"
            else:
                data['calc_reasoning'] = f"Inconsistent (CV={data['cv']:.2f} >= 0.3): spread over 12"
            data['calc_formula'] = f"total / 12 = {data['total']:.2f} / 12 = {monthly_value:.2f}"
        data['monthly_value'] = monthly_value
        variable_monthly += monthly_value

    return {
        'by_category': dict(by_category),
        'by_merchant': {k: dict(v) for k, v in by_merchant.items()},
        'by_month': dict(by_month),
        'total': sum(t['amount'] for t in transactions),
        'count': len(transactions),
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
    result = {
        'name': merchant_name,
        'classification': data.get('classification', 'unknown'),
        'category': data.get('category', ''),
        'subcategory': data.get('subcategory', ''),
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

    # Verbose: add decision trace
    if verbose >= 1:
        result['reasoning']['trace'] = reasoning.get('trace', [])

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
        'monthly': 'Monthly Recurring',
        'annual': 'Annual Bills',
        'periodic': 'Periodic Recurring',
        'travel': 'Travel',
        'one_off': 'One-Off',
        'variable': 'Variable/Discretionary',
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

    # Exclude transfers and cash for "actual spending"
    excluded_categories = {'Transfers', 'Cash'}
    actual_spending = sum(
        data['total'] for (cat, sub), data in by_category.items()
        if cat not in excluded_categories
    )

    # =========================================================================
    # MONTHLY BUDGET SUMMARY
    # =========================================================================
    print("=" * 80)
    print(f"{year} SPENDING ANALYSIS (Occurrence-Based)")
    print("=" * 80)

    print("\nMONTHLY BUDGET")
    print("-" * 50)
    print(f"Monthly Recurring (6+ mo):   {fmt(stats['monthly_avg']):>14}/mo")
    print(f"Variable/Discretionary:      {fmt(stats['variable_monthly']):>14}/mo")
    print(f"                             {'-'*14}")
    print(f"TRUE MONTHLY BUDGET:         {fmt(stats['monthly_avg'] + stats['variable_monthly']):>14}/mo")
    print()
    print("NON-RECURRING (YTD)")
    print("-" * 50)
    print(f"Annual Bills:                {fmt(stats['annual_total']):>14}")
    print(f"Periodic Recurring:          {fmt(stats['periodic_total']):>14}")
    print(f"Travel/Trips:                {fmt(stats['travel_total']):>14}")
    print(f"One-Off Purchases:           {fmt(stats['one_off_total']):>14}")
    print(f"                             {'-'*14}")
    print(f"Total Non-Recurring:         {fmt(stats['annual_total'] + stats['periodic_total'] + stats['travel_total'] + stats['one_off_total']):>14}")
    print()
    print(f"TOTAL SPENDING (YTD):        {fmt(actual_spending):>14}")

    # =========================================================================
    # MONTHLY RECURRING (6+ months)
    # =========================================================================
    print("\n" + "=" * 80)
    print("MONTHLY RECURRING (Appears 6+ Months)")
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
    # ANNUAL BILLS (once a year)
    # =========================================================================
    print("\n" + "=" * 80)
    print("ANNUAL BILLS (Once a Year)")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Total':>12}")
    print("-" * 58)

    sorted_annual = sorted(annual_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_annual:
        print(f"{merchant:<28} {data['subcategory']:<15} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL':<28} {'':<15} {fmt(stats['annual_total']):>14}")

    # =========================================================================
    # PERIODIC RECURRING (non-monthly recurring)
    # =========================================================================
    print("\n" + "=" * 80)
    print("PERIODIC RECURRING (Non-Monthly)")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Count':>6} {'Total':>12}")
    print("-" * 65)

    sorted_periodic = sorted(periodic_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_periodic:
        print(f"{merchant:<28} {data['subcategory']:<15} {data['count']:>6} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL':<28} {'':<15} {'':<6} {fmt(stats['periodic_total']):>14}")

    # =========================================================================
    # TRAVEL/TRIPS
    # =========================================================================
    print("\n" + "=" * 80)
    print("TRAVEL/TRIPS")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Count':>6} {'Total':>12}")
    print("-" * 65)

    sorted_travel = sorted(travel_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_travel[:15]:
        print(f"{merchant:<28} {data['category']:<15} {data['count']:>6} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL TRAVEL':<28} {'':<15} {'':<6} {fmt(stats['travel_total']):>14}")

    # =========================================================================
    # ONE-OFF PURCHASES
    # =========================================================================
    print("\n" + "=" * 80)
    print("ONE-OFF PURCHASES")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Total':>12}")
    print("-" * 58)

    sorted_oneoff = sorted(one_off_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_oneoff[:15]:
        print(f"{merchant:<28} {data['category']:<15} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL ONE-OFF':<28} {'':<15} {fmt(stats['one_off_total']):>14}")

    # =========================================================================
    # VARIABLE/DISCRETIONARY
    # =========================================================================
    print("\n" + "=" * 80)
    print("VARIABLE/DISCRETIONARY SPENDING")
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


def generate_embeddings(items):
    """Generate embeddings for a list of text items using sentence-transformers."""
    if not EMBEDDINGS_AVAILABLE:
        return None

    print("Generating semantic embeddings...")
    # Use a small, fast model optimized for semantic similarity
    model = SentenceTransformer('all-MiniLM-L6-v2')
    embeddings = model.encode(items, show_progress_bar=False)
    return embeddings.tolist()


def write_summary_file(stats, filepath, year=2025, home_locations=None, currency_format="${amount}"):
    """Write summary to HTML file.

    Args:
        stats: Analysis statistics dict
        filepath: Output file path
        year: Year for display in title
        home_locations: Set of home location codes for location badge coloring
        currency_format: Format string for currency display, e.g. "${amount}" or "{amount} zł"
    """
    home_locations = home_locations or set()

    # Load external CSS and JavaScript files for embedding
    css_file_path = Path(__file__).parent / 'spending_report.css'
    with open(css_file_path, 'r') as f:
        spending_report_css = f.read()

    js_file_path = Path(__file__).parent / 'spending_report.js'
    with open(js_file_path, 'r') as f:
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

    excluded = {'Transfers', 'Cash'}
    actual = sum(d['total'] for (c, s), d in by_category.items() if c not in excluded)
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
        if cat not in ('Transfers', 'Cash'):
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
        chart_js_content = chart_js_path.read_text()
    else:
        chart_js_content = '// Chart.js not found - charts will not render'

    # Prepare chart data
    # 1. Monthly spending trend (excluding Transfers and Cash)
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
                    'description': txn.get('description', ''),
                    'amount': txn.get('amount', 0),
                    'source': txn.get('source', ''),
                    'location': txn.get('location')
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
                'transactions': txns
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
        // Chart data from Python
        const chartData = {chart_data_json};
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
                    labels: chartData.monthly.labels,
                    datasets: [{{
                        label: 'Total Spending',
                        data: chartData.monthly.data,
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
            
            const colors = chartData.categoryPie.labels.map(label => 
                categoryColors[label] || 'rgba(156, 163, 175, 0.8)'
            );
            
            window.categoryPieChart = new Chart(ctx, {{
                type: 'doughnut',
                data: {{
                    labels: chartData.categoryPie.labels,
                    datasets: [{{
                        data: chartData.categoryPie.data,
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
            
            const datasets = chartData.categoryByMonth.datasets.map(ds => ({{
                label: ds.label,
                data: ds.data,
                backgroundColor: categoryColors[ds.label] || 'rgba(156, 163, 175, 0.8)',
                borderWidth: 0
            }}));
            
            window.categoryByMonthChart = new Chart(ctx, {{
                type: 'bar',
                data: {{
                    labels: chartData.categoryByMonth.labels,
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
