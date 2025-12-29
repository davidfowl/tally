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
    # TRAVEL: Explicit travel category only
    # - category='Travel' from merchant rules (airlines, hotels, etc.)
    # NOTE: Location-based travel detection was removed because it was unreliable
    # (e.g., "FG" in utility company name triggered international detection).
    # Users can mark merchants as travel via merchant_categories.csv.
    # =========================================================================
    if category == 'Travel':
        reasoning['trace'].append(f"✓ IS travel: category=Travel")
        reasoning['decision'] = "Travel: category is Travel"
        return ('travel', reasoning)
    reasoning['trace'].append(f"✗ NOT travel: category={category}")

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
    <style>
        /* Theme CSS Variables */
        :root {{
            /* Backgrounds */
            --bg-gradient-start: #1a1a2e;
            --bg-gradient-end: #16213e;
            --bg-card: rgba(255,255,255,0.05);
            --bg-card-hover: rgba(255,255,255,0.08);
            --bg-input: rgba(255,255,255,0.05);
            --bg-input-focus: rgba(255,255,255,0.08);
            --bg-table: rgba(255,255,255,0.03);
            --bg-table-header: rgba(255,255,255,0.05);
            --bg-table-hover: rgba(255,255,255,0.03);
            --bg-tooltip: rgba(0,0,0,0.9);
            --bg-txn-row: rgba(0,0,0,0.2);
            --bg-scrollbar-track: rgba(255,255,255,0.05);
            --bg-scrollbar-thumb: rgba(255,255,255,0.2);
            --bg-scrollbar-thumb-hover: rgba(255,255,255,0.3);
            --bg-highlight: rgba(255, 230, 0, 0.3);
            --bg-code: rgba(255,255,255,0.08);
            --bg-help: rgba(255,255,255,0.02);
            --bg-location: #444;

            /* Text */
            --text-primary: #e8e8e8;
            --text-secondary: #888;
            --text-muted: #666;
            --text-dim: #555;
            --text-light: #999;
            --text-lighter: #aaa;
            --text-tooltip: #fff;

            /* Borders */
            --border-light: rgba(255,255,255,0.1);
            --border-medium: rgba(255,255,255,0.2);
            --border-strong: rgba(255,255,255,0.3);
            --border-scrolled: #333;
            --border-help: rgba(255,255,255,0.08);
            --border-table: rgba(255,255,255,0.05);

            /* Shadows */
            --shadow-card: rgba(0,0,0,0.3);
            --shadow-scrolled: rgba(0,0,0,0.4);

            /* Accents (same for both themes) */
            --accent-blue: #4facfe;
            --accent-blue-light: #00f2fe;
            --accent-cyan: #4dffd2;
            --accent-orange: #ffa94d;
            --accent-yellow: #f5af19;
            --accent-pink: #f093fb;
            --accent-pink-dark: #fa709a;
            --accent-red: #ff6b6b;

            /* Special */
            --bg-amex: #006fcf;
            --bg-boa: #c41230;
            --bg-home: #2d5016;
            --text-home: #90EE90;
            --bg-intl: #8B4513;
            --text-intl: #FFD700;
        }}

        /* Light Theme */
        [data-theme="light"] {{
            --bg-gradient-start: #f0f4f8;
            --bg-gradient-end: #ffffff;
            --bg-card: rgba(0,0,0,0.03);
            --bg-card-hover: rgba(0,0,0,0.06);
            --bg-input: rgba(0,0,0,0.03);
            --bg-input-focus: rgba(0,0,0,0.05);
            --bg-table: rgba(0,0,0,0.02);
            --bg-table-header: rgba(0,0,0,0.04);
            --bg-table-hover: rgba(0,0,0,0.03);
            --bg-tooltip: rgba(30,30,30,0.95);
            --bg-txn-row: rgba(0,0,0,0.04);
            --bg-scrollbar-track: rgba(0,0,0,0.05);
            --bg-scrollbar-thumb: rgba(0,0,0,0.15);
            --bg-scrollbar-thumb-hover: rgba(0,0,0,0.25);
            --bg-highlight: rgba(255, 200, 0, 0.3);
            --bg-code: rgba(0,0,0,0.06);
            --bg-help: rgba(0,0,0,0.02);
            --bg-location: #666;

            --text-primary: #1a1a2e;
            --text-secondary: #555;
            --text-muted: #777;
            --text-dim: #888;
            --text-light: #666;
            --text-lighter: #555;
            --text-tooltip: #fff;

            --border-light: rgba(0,0,0,0.08);
            --border-medium: rgba(0,0,0,0.12);
            --border-strong: rgba(0,0,0,0.2);
            --border-scrolled: #ddd;
            --border-help: rgba(0,0,0,0.08);
            --border-table: rgba(0,0,0,0.05);

            --shadow-card: rgba(0,0,0,0.1);
            --shadow-scrolled: rgba(0,0,0,0.15);

            /* Darker accent colors for better contrast on light backgrounds */
            --accent-blue: #2563eb;
            --accent-cyan: #0d9488;
            --accent-orange: #ea580c;
            --accent-yellow: #ca8a04;
            --accent-pink: #c026d3;
            --accent-pink-dark: #be185d;
            --accent-red: #dc2626;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, var(--bg-gradient-start) 0%, var(--bg-gradient-end) 100%);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 2rem;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            text-align: center;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border-light);
            position: relative;
        }}
        h1 {{
            font-size: 2.5rem;
            font-weight: 300;
            margin-bottom: 0.5rem;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-blue-light));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        .subtitle {{
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}
        .theme-toggle {{
            position: absolute;
            top: 0;
            right: 0;
            background: var(--bg-card);
            border: 1px solid var(--border-medium);
            border-radius: 50%;
            width: 36px;
            height: 36px;
            cursor: pointer;
            font-size: 1.1rem;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .theme-toggle:hover {{
            background: var(--bg-card-hover);
            transform: scale(1.1);
        }}
        .search-box {{
            margin: 1.5rem 0;
            text-align: center;
            position: sticky;
            top: 0;
            z-index: 100;
            background: var(--bg-gradient-start);
            padding: 12px 20px;
            margin: 0 -20px 1.5rem -20px;
            border-bottom: 1px solid transparent;
            transition: box-shadow 0.2s, border-color 0.2s;
            width: calc(100% + 40px);
        }}
        .search-box.scrolled {{
            box-shadow: 0 2px 12px var(--shadow-scrolled);
            border-bottom-color: var(--border-scrolled);
        }}
        .search-box input {{
            width: 100%;
            max-width: 500px;
            padding: 0.75rem 1rem;
            border-radius: 8px;
            border: 1px solid var(--border-medium);
            background: var(--bg-input);
            color: var(--text-primary);
            font-size: 1rem;
            outline: none;
            transition: all 0.2s;
        }}
        .search-box input:focus {{
            border-color: var(--accent-blue);
            background: var(--bg-input-focus);
        }}
        .search-box input::placeholder {{
            color: var(--text-muted);
        }}
        .autocomplete-container {{
            position: relative;
            display: inline-block;
            width: 100%;
            max-width: 500px;
        }}
        .autocomplete-list {{
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            width: 100%;
            max-width: 500px;
            background: var(--bg-gradient-start);
            border: 1px solid var(--border-medium);
            border-top: none;
            border-radius: 0 0 8px 8px;
            max-height: 300px;
            overflow-y: auto;
            z-index: 1000;
            display: none;
        }}
        .autocomplete-list.show {{
            display: block;
        }}
        .autocomplete-item {{
            padding: 0.6rem 1rem;
            cursor: pointer;
            text-align: left;
            color: var(--text-primary);
            border-bottom: 1px solid var(--border-light);
        }}
        .autocomplete-item:last-child {{
            border-bottom: none;
        }}
        .autocomplete-item:hover, .autocomplete-item.selected {{
            background: rgba(79, 172, 254, 0.2);
        }}
        .autocomplete-item .type {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-left: 0.5rem;
        }}
        .autocomplete-item .type.category {{
            color: var(--accent-blue);
        }}
        .autocomplete-item .type.merchant {{
            color: var(--accent-cyan);
        }}
        .autocomplete-item .type.location {{
            color: var(--accent-orange);
        }}
        .autocomplete-item .score {{
            font-size: 0.7rem;
            color: var(--accent-yellow);
            margin-left: 0.5rem;
            opacity: 0.8;
        }}
        .filter-chips {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.75rem;
            justify-content: center;
        }}
        .filter-chips:empty {{
            display: none;
        }}
        .filter-chip {{
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.35rem 0.6rem;
            border-radius: 20px;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid;
        }}
        .filter-chip.include {{
            background: rgba(79, 172, 254, 0.2);
            border-color: var(--accent-blue);
            color: var(--accent-blue);
        }}
        .filter-chip.exclude {{
            background: rgba(255, 107, 107, 0.2);
            border-color: var(--accent-red);
            color: var(--accent-red);
            text-decoration: line-through;
        }}
        .filter-chip .chip-type {{
            font-size: 0.7rem;
            opacity: 0.7;
            text-transform: uppercase;
        }}
        .filter-chip .chip-remove {{
            margin-left: 0.2rem;
            font-size: 1rem;
            line-height: 1;
            opacity: 0.7;
        }}
        .filter-chip .chip-remove:hover {{
            opacity: 1;
        }}
        .filter-chip.category {{ border-color: var(--accent-blue); }}
        .filter-chip.category.include {{ background: rgba(79, 172, 254, 0.2); color: var(--accent-blue); }}
        .filter-chip.merchant {{ border-color: var(--accent-cyan); }}
        .filter-chip.merchant.include {{ background: rgba(77, 255, 210, 0.2); color: var(--accent-cyan); }}
        .filter-chip.location {{ border-color: var(--accent-orange); }}
        .filter-chip.location.include {{ background: rgba(255, 169, 77, 0.2); color: var(--accent-orange); }}
        .filter-chip.category.exclude, .filter-chip.merchant.exclude, .filter-chip.location.exclude {{
            background: rgba(255, 107, 107, 0.15);
            border-color: var(--accent-red);
            color: var(--accent-red);
        }}
        .clear-all-btn {{
            background: transparent;
            border: 1px solid var(--border-strong);
            color: var(--text-secondary);
            padding: 0.25rem 0.75rem;
            border-radius: 1rem;
            font-size: 0.75rem;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .clear-all-btn:hover {{
            background: rgba(255, 107, 107, 0.2);
            border-color: var(--accent-red);
            color: var(--accent-red);
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: var(--bg-card);
            border-radius: 16px;
            padding: 1.5rem;
            backdrop-filter: blur(10px);
            border: 1px solid var(--border-light);
            transition: transform 0.2s, box-shadow 0.2s;
            cursor: pointer;
        }}
        .card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 30px var(--shadow-card);
        }}
        .card h2 {{
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-secondary);
            margin-bottom: 1rem;
        }}
        .card .amount {{
            font-size: 2.5rem;
            font-weight: 600;
        }}
        .card .label {{
            font-size: 0.9rem;
            color: var(--text-muted);
            margin-top: 0.25rem;
        }}
        .card.monthly .amount {{ color: var(--accent-blue); }}
        .card.non-recurring .amount {{ color: var(--accent-pink); }}
        .card.total .amount {{ color: var(--accent-cyan); }}
        .breakdown {{
            margin-top: 1rem;
            padding-top: 1rem;
            border-top: 1px solid var(--border-light);
        }}
        .breakdown-item {{
            display: flex;
            justify-content: space-between;
            padding: 0.5rem 0;
            font-size: 0.95rem;
        }}
        .breakdown-item .name {{ color: var(--text-lighter); }}
        .breakdown-item .value {{ font-weight: 500; }}
        .breakdown-item .breakdown-pct {{ color: var(--text-muted); font-size: 0.85rem; }}
        section {{
            margin-bottom: 1rem;
        }}
        .section-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            padding: 1rem;
            background: var(--bg-table);
            border-radius: 12px;
            margin-bottom: 0.5rem;
            transition: background 0.2s;
        }}
        .section-header:hover {{
            background: var(--bg-card-hover);
        }}
        .section-header h2 {{
            font-size: 1.25rem;
            font-weight: 500;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}
        .section-header .toggle {{
            font-size: 1.5rem;
            transition: transform 0.3s;
            color: var(--text-muted);
        }}
        .section-header.collapsed .toggle {{
            transform: rotate(-90deg);
        }}
        .section-header .section-total {{
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 1.1rem;
            color: var(--text-secondary);
        }}
        .section-header .section-pct {{
            font-size: 0.9rem;
            color: var(--text-muted);
        }}
        section.monthly-section .section-header h2 {{ color: var(--accent-blue); }}
        section.annual-section .section-header h2 {{ color: var(--accent-yellow); }}
        section.travel-section .section-header h2 {{ color: var(--accent-pink); }}
        section.oneoff-section .section-header h2 {{ color: var(--accent-pink-dark); }}
        section.variable-section .section-header h2 {{ color: var(--accent-cyan); }}
        .section-content {{
            overflow: hidden;
            transition: max-height 0.4s ease-out, opacity 0.3s ease-out;
            max-height: 5000px;
            opacity: 1;
        }}
        .section-content.collapsed {{
            max-height: 0;
            opacity: 0;
        }}
        .table-wrapper {{
            max-height: 500px;
            overflow-y: auto;
            border-radius: 12px;
        }}
        .table-wrapper::-webkit-scrollbar {{
            width: 8px;
        }}
        .table-wrapper::-webkit-scrollbar-track {{
            background: var(--bg-scrollbar-track);
            border-radius: 4px;
        }}
        .table-wrapper::-webkit-scrollbar-thumb {{
            background: var(--bg-scrollbar-thumb);
            border-radius: 4px;
        }}
        .table-wrapper::-webkit-scrollbar-thumb:hover {{
            background: var(--bg-scrollbar-thumb-hover);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-table);
            border-radius: 12px;
            overflow: hidden;
        }}
        th {{
            text-align: left;
            padding: 1rem;
            background: var(--bg-table-header);
            font-weight: 500;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-secondary);
            cursor: pointer;
            user-select: none;
            transition: background 0.2s;
        }}
        th:hover {{
            background: var(--bg-card-hover);
        }}
        th.sorted-asc::after {{ content: ' ↑'; color: var(--accent-blue); }}
        th.sorted-desc::after {{ content: ' ↓'; color: var(--accent-blue); }}
        th:last-child, td:last-child {{ text-align: right; }}
        td {{
            padding: 0.85rem 1rem;
            border-top: 1px solid var(--border-table);
        }}
        tr:hover td {{
            background: var(--bg-table-hover);
        }}
        tr.hidden {{
            display: none;
        }}
        .merchant {{ font-weight: 500; }}
        .category {{ color: var(--text-secondary); font-size: 0.9rem; }}
        .money {{ font-family: 'SF Mono', Monaco, monospace; }}
        .pct {{ font-family: 'SF Mono', Monaco, monospace; color: var(--text-secondary); font-size: 0.85rem; }}
        .filter-pct {{ font-size: 0.5em; color: var(--text-secondary); font-weight: normal; }}
        .total-row td {{
            font-weight: 600;
            background: var(--bg-table-header);
            border-top: 2px solid var(--border-light);
        }}
        .badge {{
            display: inline-block;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 500;
        }}
        .badge.avg {{ background: rgba(79, 172, 254, 0.2); color: var(--accent-blue); }}
        .badge.div {{ background: rgba(240, 147, 251, 0.2); color: var(--accent-pink); }}
        /* Tooltips */
        [data-tooltip] {{
            position: relative;
            cursor: help;
        }}
        [data-tooltip]:hover::after {{
            content: attr(data-tooltip);
            position: absolute;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            background: var(--bg-tooltip);
            color: var(--text-tooltip);
            padding: 0.5rem 0.75rem;
            border-radius: 6px;
            font-size: 0.75rem;
            white-space: nowrap;
            z-index: 1000;
            pointer-events: none;
        }}
        [data-tooltip]:hover::before {{
            content: '';
            position: absolute;
            bottom: 115%;
            left: 50%;
            transform: translateX(-50%);
            border: 6px solid transparent;
            border-top-color: var(--bg-tooltip);
            z-index: 1000;
        }}
        th[data-tooltip] {{ cursor: pointer; }}
        .highlight {{
            background: var(--bg-highlight);
            color: var(--text-primary);
            padding: 0 2px;
            border-radius: 2px;
        }}
        /* Expandable transaction rows */
        .merchant-row {{
            cursor: pointer;
        }}
        .merchant-row:hover {{
            background: var(--bg-card);
        }}
        .merchant-row .chevron {{
            display: inline-block;
            width: 1em;
            transition: transform 0.2s;
            color: var(--text-muted);
        }}
        .merchant-row.expanded .chevron {{
            transform: rotate(90deg);
        }}
        .txn-row {{
            background: var(--bg-txn-row);
        }}
        .txn-row.hidden {{
            display: none;
        }}
        .txn-row td {{
            padding: 0.3rem 0.5rem;
            font-size: 0.85rem;
            color: var(--text-light);
            border-bottom: none;
        }}
        .txn-row td:first-child {{
            padding-left: 2rem;
        }}
        .txn-detail {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}
        .txn-date {{
            color: var(--text-muted);
            min-width: 3rem;
        }}
        .txn-desc {{
            flex: 1;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .txn-amount {{
            color: var(--accent-pink);
            font-family: monospace;
            min-width: 5rem;
            text-align: right;
        }}
        .txn-source {{
            font-size: 0.7rem;
            padding: 0.1rem 0.4rem;
            border-radius: 3px;
            margin-left: 0.5rem;
            font-weight: bold;
        }}
        .txn-source.amex {{
            background: var(--bg-amex);
            color: white;
        }}
        .txn-source.boa {{
            background: var(--bg-boa);
            color: white;
        }}
        .txn-location {{
            font-size: 0.7rem;
            padding: 0.1rem 0.4rem;
            border-radius: 3px;
            margin-left: 0.5rem;
            font-weight: bold;
            background: var(--bg-location);
            color: #fff;
        }}
        .txn-location.home {{
            background: var(--bg-home);
            color: var(--text-home);
        }}
        .txn-location.intl {{
            background: var(--bg-intl);
            color: var(--text-intl);
        }}
        .chart-container {{
            display: flex;
            justify-content: center;
            margin: 2rem 0;
        }}
        .donut-chart {{
            position: relative;
            width: 200px;
            height: 200px;
        }}
        .donut-chart svg {{
            transform: rotate(-90deg);
        }}
        .donut-chart .center-text {{
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
        }}
        .donut-chart .center-text .amount {{
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--text-primary);
        }}
        .donut-chart .center-text .label {{
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        .legend {{
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 1rem;
            margin-top: 1rem;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.85rem;
            color: var(--text-lighter);
            cursor: pointer;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            transition: background 0.2s;
        }}
        .legend-item:hover {{
            background: var(--bg-card);
        }}
        .legend-item .dot {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }}
        footer {{
            text-align: center;
            padding-top: 2rem;
            color: var(--text-dim);
            font-size: 0.85rem;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        section {{
            animation: fadeIn 0.4s ease-out forwards;
        }}
        section:nth-child(2) {{ animation-delay: 0.1s; }}
        section:nth-child(3) {{ animation-delay: 0.2s; }}
        section:nth-child(4) {{ animation-delay: 0.3s; }}
        section:nth-child(5) {{ animation-delay: 0.4s; }}

        /* Click-to-filter on cells */
        .clickable {{ cursor: pointer; }}
        .clickable:hover {{ text-decoration: underline; color: var(--accent-blue); }}
        .clickable .chevron {{ text-decoration: none !important; display: inline-block; }}
        .location-badge.clickable:hover {{ background: var(--accent-blue); }}

        /* Help/Guide section - Compact inline design */
        .help-section {{
            background: var(--bg-help);
            border: 1px solid var(--border-help);
            border-radius: 12px;
            margin-bottom: 1.5rem;
        }}
        .help-section-header {{
            padding: 0.75rem 1rem;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background 0.2s;
        }}
        .help-section-header:hover {{
            background: var(--bg-table);
        }}
        .help-section-header h3 {{
            font-size: 0.8rem;
            font-weight: 500;
            color: var(--text-secondary);
            margin: 0;
        }}
        .help-section-header .toggle {{
            color: var(--text-muted);
            font-size: 0.7rem;
            transition: transform 0.2s;
        }}
        .help-section.collapsed .help-section-header .toggle {{
            transform: rotate(-90deg);
        }}
        .help-section-content {{
            padding: 0.75rem 1rem 1rem;
            border-top: 1px solid var(--border-table);
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 0.4rem 1rem;
            font-size: 0.75rem;
            color: var(--text-secondary);
            align-items: baseline;
        }}
        .help-section.collapsed .help-section-content {{
            display: none;
        }}
        .help-section-content .label {{
            color: var(--text-muted);
            font-weight: 500;
            white-space: nowrap;
        }}
        .help-section-content .value {{
            line-height: 1.5;
        }}
        .help-section-content .badge {{
            margin-right: 0.15rem;
        }}
        .help-section-content code {{
            background: var(--bg-code);
            padding: 0.1rem 0.35rem;
            border-radius: 3px;
            font-size: 0.7rem;
            color: var(--accent-blue);
        }}
        .help-section-content strong {{
            color: var(--text-lighter);
            font-weight: 500;
        }}
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
                <h2>Total Spending (YTD)</h2>
                <div class="amount" id="totalSpending" data-original="{actual:.0f}">{fmt(actual)}</div>
                <div class="breakdown">
                    <div class="breakdown-item">
                        <span class="name">Uncategorized</span>
                        <span class="value">{fmt(uncat)} ({uncat/actual*100:.1f}%)</span>
                    </div>
                </div>
            </div>
        </div>

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
        merchant_id = merchant.replace("'", "").replace('"', '').replace(' ', '_')
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
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
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}">
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
        merchant_id = merchant.replace("'", "").replace('"', '').replace(' ', '_')
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="category clickable" onclick="addFilterFromCell(event, this, 'category')">{data['subcategory']}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}">
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
        merchant_id = merchant.replace("'", "").replace('"', '').replace(' ', '_')
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="category clickable" onclick="addFilterFromCell(event, this, 'category')">{data['subcategory']}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}">
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
        merchant_id = merchant.replace("'", "").replace('"', '').replace(' ', '_')
        cat_data = f"{data.get('category', 'travel')}/{data.get('subcategory', '')}".lower()
        category_display = data.get('category', 'Travel')
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="clickable" onclick="addFilterFromCell(event, this, 'category')">{category_display}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}">
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
        merchant_id = merchant.replace("'", "").replace('"', '').replace(' ', '_')
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="category clickable" onclick="addFilterFromCell(event, this, 'category')">{data['category']}</td>
                        <td>{data['count']}</td>
                        <td class="money">{fmt(data['total'])}</td>
                        <td class="pct">{pct:.1f}%</td>
                    </tr>'''
        # Add transaction detail rows
        sorted_txns = sorted(data.get('transactions', []), key=lambda x: x['date'], reverse=True)
        for txn in sorted_txns:
            html += f'''
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}">
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
        merchant_id = merchant.replace("'", "").replace('"', '').replace(' ', '_')
        cat_data = f"{data.get('category', '')}/{data.get('subcategory', '')}".lower()
        html += f'''
                    <tr class="merchant-row" data-merchant="{merchant_id}" data-category="{cat_data}" data-ytd="{data['total']:.2f}" onclick="toggleTransactions(this)">
                        <td class="merchant"><span class="chevron clickable" onclick="toggleTransactionsFromChevron(event, this)">▶</span> <span class="clickable" onclick="addFilterFromCell(event, this, 'merchant')">{merchant}</span></td>
                        <td class="category"><span class="clickable" onclick="addFilterFromCell(event, this, 'category')">{data['category']}</span>/<span class="clickable" onclick="addFilterFromCell(event, this, 'category')">{data['subcategory']}</span></td>
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
                    <tr class="txn-row hidden" data-merchant="{merchant_id}" data-amount="{txn['amount']:.2f}">
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

    <script>
        // Currency format
        const currencyFormat = '{currency_format}';
        function formatCurrency(amount) {{
            const formatted = amount.toLocaleString('en-US', {{ maximumFractionDigits: 0 }});
            return currencyFormat.replace('{{amount}}', formatted);
        }}

        // Store original totals
        const originalTotals = {{
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

        // Autocomplete data
        const autocompleteData = [
            {','.join(f'{{"text": "{cat}", "type": "category"}}' for cat in sorted_categories)},
            {','.join(f'{{"text": "{merchant.replace(chr(34), chr(92)+chr(34))}", "type": "merchant"}}' for merchant in sorted_merchants)},
            {','.join(f'{{"text": "{loc}", "type": "location"}}' for loc in sorted_locations)}
        ];

        // Pre-computed embeddings for semantic search
        window.embeddingsData = {embeddings_json};

        // Autocomplete state
        let selectedIndex = -1;

        // Active filters state
        let activeFilters = [];  // array of filter objects with text, type, mode

        function addFilter(text, type) {{
            // Don't add duplicate filters
            if (activeFilters.some(f => f.text === text && f.type === type)) return;
            activeFilters.push({{ text, type, mode: 'include' }});
            renderFilters();
            applyFilters();
        }}

        function removeFilter(index) {{
            activeFilters.splice(index, 1);
            renderFilters();
            applyFilters();
        }}

        function toggleFilter(index) {{
            activeFilters[index].mode = activeFilters[index].mode === 'include' ? 'exclude' : 'include';
            renderFilters();
            applyFilters();
        }}

        function renderFilters() {{
            const container = document.getElementById('filterChips');
            let html = activeFilters.map((f, i) => `
                <div class="filter-chip ${{f.type}} ${{f.mode}}" data-index="${{i}}">
                    <span class="chip-type">${{f.type.charAt(0)}}</span>
                    <span class="chip-text">${{f.text}}</span>
                    <span class="chip-remove" data-action="remove">×</span>
                </div>
            `).join('');

            // Add "Clear all" button if there are multiple filters
            if (activeFilters.length > 1) {{
                html += '<button class="clear-all-btn" onclick="clearAllFilters()">Clear all</button>';
            }}

            container.innerHTML = html;

            // Add click handlers
            container.querySelectorAll('.filter-chip').forEach(chip => {{
                chip.addEventListener('click', (e) => {{
                    const idx = parseInt(chip.dataset.index);
                    if (e.target.dataset.action === 'remove') {{
                        removeFilter(idx);
                    }} else {{
                        toggleFilter(idx);
                    }}
                }});
            }});
        }}

        function clearAllFilters() {{
            activeFilters = [];
            renderFilters();
            applyFilters();
        }}

        function applyFilters() {{
            const tables = document.querySelectorAll('table');
            const includeFilters = activeFilters.filter(f => f.mode === 'include');
            const excludeFilters = activeFilters.filter(f => f.mode === 'exclude');

            // If no filters, show all
            if (activeFilters.length === 0) {{
                document.querySelectorAll('section').forEach(section => {{
                    section.style.display = '';
                }});
                tables.forEach(table => {{
                    table.querySelectorAll('tbody tr:not(.total-row)').forEach(row => {{
                        if (!row.classList.contains('txn-row')) {{
                            row.classList.remove('hidden');
                        }}
                        row.querySelectorAll('.highlight').forEach(el => {{
                            el.outerHTML = el.textContent;
                        }});
                    }});
                }});
                document.querySelectorAll('.merchant-row.expanded').forEach(row => {{
                    row.classList.remove('expanded');
                }});
                document.querySelectorAll('.txn-row').forEach(row => {{
                    row.classList.add('hidden');
                }});
                restoreOriginalTotals();
                const totalEl = document.getElementById('totalSpending');
                const original = parseInt(totalEl.dataset.original);
                totalEl.textContent = formatCurrency(original);
                return;
            }}

            // Map location-based subcategories to location codes
            const locationCategoryMap = {{
                'barbados': 'br',
                'hawaii': 'hi',
                'uk': 'gb',
                'las vegas': 'nv'
            }};

            // Check if a merchant row matches a filter
            function merchantMatchesFilter(row, filter, txnRows) {{
                const filterText = filter.text.toLowerCase();
                const merchantId = row.dataset.merchant;
                const tableId = row.closest('table')?.id;

                if (filter.type === 'category') {{
                    // First check the visible category cell
                    const catCell = row.querySelector('.category');
                    if (catCell) {{
                        const catText = catCell.textContent.toLowerCase();
                        // Match category or subcategory (format: "Category/Subcategory")
                        if (catText.includes(filterText)) return true;
                    }}
                    // Also check data-category attribute (for tables without visible category)
                    const dataCat = row.dataset.category;
                    if (dataCat && dataCat.includes(filterText)) {{
                        return true;
                    }}
                    // Travel table = all items are Travel category
                    if (tableId === 'travel-table' && filterText === 'travel') {{
                        return true;
                    }}
                    // Check if this is a location-based category (Barbados, Hawaii, UK)
                    const locationCode = locationCategoryMap[filterText];
                    if (locationCode) {{
                        // Check if any transaction has matching location badge
                        return txnRows.some(txn => {{
                            if (txn.dataset.merchant !== merchantId) return false;
                            const locBadge = txn.querySelector('.txn-location');
                            return locBadge && locBadge.textContent.toLowerCase() === locationCode;
                        }});
                    }}
                    // Fallback: check transaction descriptions
                    return txnRows.some(txn => {{
                        if (txn.dataset.merchant !== merchantId) return false;
                        const desc = txn.querySelector('.txn-desc');
                        return desc && desc.textContent.toLowerCase().includes(filterText);
                    }});
                }} else if (filter.type === 'location') {{
                    // For location, check if any txn-row for this merchant has matching location
                    return txnRows.some(txn => {{
                        if (txn.dataset.merchant !== merchantId) return false;
                        const locBadge = txn.querySelector('.txn-location');
                        return locBadge && locBadge.textContent.toLowerCase() === filterText;
                    }});
                }} else {{
                    // Merchant filter - match merchant name
                    const merchCell = row.querySelector('.merchant');
                    if (merchCell) {{
                        // Get just the merchant name, not the chevron
                        const merchText = merchCell.textContent.replace('▶', '').trim().toLowerCase();
                        return merchText === filterText || merchText.includes(filterText);
                    }}
                    return false;
                }}
            }}

            // Check if we have location filters (requires transaction-level filtering)
            const hasLocationFilter = activeFilters.some(f => f.type === 'location');

            tables.forEach(table => {{
                const merchantRows = table.querySelectorAll('tbody tr.merchant-row');
                const txnRows = Array.from(table.querySelectorAll('tbody tr.txn-row'));
                let hasVisibleRows = false;

                if (hasLocationFilter) {{
                    // Location filter: filter at transaction level
                    const visibleMerchants = new Set();

                    // First, determine which txn-rows match
                    txnRows.forEach(txn => {{
                        const locBadge = txn.querySelector('.txn-location');
                        const txnLoc = locBadge ? locBadge.textContent.toLowerCase() : '';

                        let matchesInclude = includeFilters.length === 0;
                        let matchesExclude = false;

                        for (const f of includeFilters) {{
                            if (f.type === 'location' && txnLoc === f.text.toLowerCase()) {{
                                matchesInclude = true;
                                break;
                            }} else if (f.type !== 'location') {{
                                // Non-location filters check against merchant
                                const merchantId = txn.dataset.merchant;
                                const merchantRow = table.querySelector(`tr.merchant-row[data-merchant="${{merchantId}}"]`);
                                if (merchantRow && merchantMatchesFilter(merchantRow, f, txnRows)) {{
                                    matchesInclude = true;
                                    break;
                                }}
                            }}
                        }}

                        for (const f of excludeFilters) {{
                            if (f.type === 'location' && txnLoc === f.text.toLowerCase()) {{
                                matchesExclude = true;
                                break;
                            }} else if (f.type !== 'location') {{
                                const merchantId = txn.dataset.merchant;
                                const merchantRow = table.querySelector(`tr.merchant-row[data-merchant="${{merchantId}}"]`);
                                if (merchantRow && merchantMatchesFilter(merchantRow, f, txnRows)) {{
                                    matchesExclude = true;
                                    break;
                                }}
                            }}
                        }}

                        if (matchesInclude && !matchesExclude) {{
                            txn.classList.remove('hidden');
                            visibleMerchants.add(txn.dataset.merchant);
                        }} else {{
                            txn.classList.add('hidden');
                        }}
                    }});

                    // Show merchant rows that have visible transactions
                    merchantRows.forEach(row => {{
                        if (visibleMerchants.has(row.dataset.merchant)) {{
                            row.classList.remove('hidden');
                            row.classList.add('expanded');  // Auto-expand to show txns
                            hasVisibleRows = true;
                        }} else {{
                            row.classList.add('hidden');
                            row.classList.remove('expanded');
                        }}
                    }});
                }} else {{
                    // No location filter: filter at merchant level
                    merchantRows.forEach(row => {{
                        let shouldShow = false;

                        if (includeFilters.length > 0) {{
                            shouldShow = includeFilters.some(f => merchantMatchesFilter(row, f, txnRows));
                        }} else {{
                            shouldShow = true;
                        }}

                        if (shouldShow && excludeFilters.length > 0) {{
                            shouldShow = !excludeFilters.some(f => merchantMatchesFilter(row, f, txnRows));
                        }}

                        if (shouldShow) {{
                            row.classList.remove('hidden');
                            hasVisibleRows = true;
                        }} else {{
                            row.classList.add('hidden');
                        }}
                    }});

                    // Hide all txn-rows when not filtering by location
                    txnRows.forEach(row => row.classList.add('hidden'));
                }}

                // Show/hide sections
                const section = table.closest('section');
                if (section) {{
                    const header = section.querySelector('.section-header');
                    const content = section.querySelector('.section-content');
                    if (hasVisibleRows) {{
                        section.style.display = '';
                        header?.classList.remove('collapsed');
                        content?.classList.remove('collapsed');
                    }} else {{
                        section.style.display = 'none';
                    }}
                }}
            }});

            // Update totals - use transaction amounts if location filter active
            if (hasLocationFilter) {{
                updateTotalsFromTransactions();
            }} else {{
                updateAllTotals();
            }}
        }}

        // Cosine similarity function
        function cosineSimilarity(a, b) {{
            let dotProduct = 0, normA = 0, normB = 0;
            for (let i = 0; i < a.length; i++) {{
                dotProduct += a[i] * b[i];
                normA += a[i] * a[i];
                normB += b[i] * b[i];
            }}
            return dotProduct / (Math.sqrt(normA) * Math.sqrt(normB));
        }}

        // Semantic search function
        async function semanticSearch(query) {{
            if (!window.semanticReady || !window.semanticModel || !window.embeddingsData) {{
                return null;
            }}
            try {{
                // Get query embedding
                const output = await window.semanticModel(query, {{ pooling: 'mean', normalize: true }});
                const queryEmbedding = Array.from(output.data);

                // Calculate similarities
                const results = window.embeddingsData.items.map((item, i) => ({{
                    text: item,
                    type: i < {len(sorted_categories)} ? 'category' : 'merchant',
                    score: cosineSimilarity(queryEmbedding, window.embeddingsData.vectors[i])
                }}));

                // Sort by similarity and return top matches
                return results.sort((a, b) => b.score - a.score).filter(r => r.score > 0.3);
            }} catch (e) {{
                console.error('Semantic search error:', e);
                return null;
            }}
        }}

        // Setup autocomplete
        const searchInput = document.getElementById('searchInput');
        const autocompleteList = document.getElementById('autocompleteList');

        // Debounce timer for semantic search
        let semanticDebounce = null;

        searchInput.addEventListener('input', async function() {{
            const query = this.value.toLowerCase().trim();

            if (query.length < 1) {{
                autocompleteList.classList.remove('show');
                return;
            }}

            // First show text matches immediately
            // Sort by match quality: exact > starts-with > contains
            let matches = autocompleteData
                .filter(item => item.text.toLowerCase().includes(query))
                .sort((a, b) => {{
                    const aLower = a.text.toLowerCase();
                    const bLower = b.text.toLowerCase();
                    const aExact = aLower === query;
                    const bExact = bLower === query;
                    const aStarts = aLower.startsWith(query);
                    const bStarts = bLower.startsWith(query);
                    // Exact matches first
                    if (aExact && !bExact) return -1;
                    if (bExact && !aExact) return 1;
                    // Then starts-with
                    if (aStarts && !bStarts) return -1;
                    if (bStarts && !aStarts) return 1;
                    // Then alphabetically
                    return aLower.localeCompare(bLower);
                }})
                .slice(0, 8);

            // Render text matches
            renderAutocomplete(matches, false);

            // Then try semantic search with debounce
            if (window.semanticReady && query.length >= 2) {{
                clearTimeout(semanticDebounce);
                semanticDebounce = setTimeout(async () => {{
                    const semanticResults = await semanticSearch(query);
                    if (semanticResults && semanticResults.length > 0) {{
                        // Merge: semantic results that aren't already in text matches
                        const textMatchTexts = new Set(matches.map(m => m.text.toLowerCase()));
                        const newSemanticMatches = semanticResults
                            .filter(r => !textMatchTexts.has(r.text.toLowerCase()))
                            .slice(0, 5);

                        if (newSemanticMatches.length > 0) {{
                            const combined = [...matches, ...newSemanticMatches].slice(0, 10);
                            renderAutocomplete(combined, true);
                        }}
                    }}
                }}, 300);
            }}
        }});

        function renderAutocomplete(matches, hasSemantic) {{
            if (matches.length === 0) {{
                autocompleteList.classList.remove('show');
                return;
            }}

            autocompleteList.innerHTML = matches.map((item, i) => {{
                const scoreHtml = item.score ? `<span class="score">${{Math.round(item.score * 100)}}%</span>` : '';
                return `<div class="autocomplete-item${{i === selectedIndex ? ' selected' : ''}}" data-value="${{item.text}}" data-type="${{item.type}}">
                    ${{item.text}}<span class="type ${{item.type}}">${{item.type}}</span>${{scoreHtml}}
                </div>`;
            }}).join('');
            autocompleteList.classList.add('show');
            selectedIndex = -1;
        }}

        searchInput.addEventListener('keydown', function(e) {{
            const items = autocompleteList.querySelectorAll('.autocomplete-item');
            if (!items.length) return;

            if (e.key === 'ArrowDown') {{
                e.preventDefault();
                selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
                updateSelection(items);
            }} else if (e.key === 'ArrowUp') {{
                e.preventDefault();
                selectedIndex = Math.max(selectedIndex - 1, -1);
                updateSelection(items);
            }} else if (e.key === 'Enter' && selectedIndex >= 0) {{
                e.preventDefault();
                const item = items[selectedIndex];
                selectItem(item.dataset.value, item.dataset.type);
            }}
        }});

        function updateSelection(items) {{
            items.forEach((item, i) => {{
                item.classList.toggle('selected', i === selectedIndex);
            }});
        }}

        function selectItem(value, type) {{
            addFilter(value, type);
            searchInput.value = '';
            autocompleteList.classList.remove('show');
            searchInput.focus();
        }}

        autocompleteList.addEventListener('click', function(e) {{
            const item = e.target.closest('.autocomplete-item');
            if (item) {{
                selectItem(item.dataset.value, item.dataset.type);
            }}
        }});

        document.addEventListener('click', function(e) {{
            if (!e.target.closest('.autocomplete-container')) {{
                autocompleteList.classList.remove('show');
            }}
        }});

        // Toggle section collapse/expand
        function toggleSection(header) {{
            header.classList.toggle('collapsed');
            const content = header.nextElementSibling;
            content.classList.toggle('collapsed');
        }}

        // Toggle transaction detail rows
        function toggleTransactions(row) {{
            const merchantId = row.dataset.merchant;
            const isExpanded = row.classList.toggle('expanded');

            // Find all transaction rows for this merchant
            let nextRow = row.nextElementSibling;
            while (nextRow && nextRow.classList.contains('txn-row') && nextRow.dataset.merchant === merchantId) {{
                nextRow.classList.toggle('hidden', !isExpanded);
                nextRow = nextRow.nextElementSibling;
            }}
        }}

        // Toggle transactions from chevron click
        function toggleTransactionsFromChevron(event, chevron) {{
            event.stopPropagation();
            const row = chevron.closest('.merchant-row');
            if (row) toggleTransactions(row);
        }}

        // Parse money value from cell
        function parseMoney(text) {{
            // Remove currency symbols, commas, and /mo suffix
            return parseFloat(text.replace(/[^0-9.-]/g, '')) || 0;
        }}

        // Format as money
        function formatMoney(value, perMonth = false) {{
            const formatted = formatCurrency(value);
            return perMonth ? formatted + '/mo' : formatted;
        }}

        // Update totals for a table based on visible rows
        // monthlyFromYtd: if true, calculate monthly as YTD/12 instead of summing Avg/Mo column
        function updateTableTotals(tableId, monthlyColIndex, ytdColIndex, pctColIndex, monthlyFromYtd = false) {{
            const table = document.getElementById(tableId);
            if (!table) return {{ monthly: 0, ytd: 0 }};

            const rows = table.querySelectorAll('tbody tr.merchant-row');
            const totalRow = table.querySelector('.total-row');
            let monthlySum = 0;
            let ytdSum = 0;

            // First pass: calculate totals from visible rows using data-ytd attribute for precision
            rows.forEach(row => {{
                if (!row.classList.contains('hidden')) {{
                    // Use data-ytd attribute for precise calculations
                    const ytdValue = parseFloat(row.dataset.ytd) || 0;
                    ytdSum += ytdValue;
                    if (monthlyColIndex !== null) {{
                        monthlySum += parseMoney(row.cells[monthlyColIndex].textContent);
                    }}
                }}
            }});

            // Second pass: update percentages for visible rows
            if (pctColIndex !== null && ytdSum > 0) {{
                rows.forEach(row => {{
                    if (!row.classList.contains('hidden') && row.cells[pctColIndex]) {{
                        const rowYtd = parseFloat(row.dataset.ytd) || 0;
                        const pct = (rowYtd / ytdSum * 100).toFixed(1);
                        row.cells[pctColIndex].textContent = pct + '%';
                    }}
                }});
            }}

            // Update total row
            if (totalRow) {{
                if (monthlyColIndex !== null && totalRow.cells[monthlyColIndex]) {{
                    totalRow.cells[monthlyColIndex].innerHTML = '<span class="money">' + formatMoney(monthlySum, true) + '</span>';
                }}
                if (ytdColIndex !== null && totalRow.cells[ytdColIndex]) {{
                    totalRow.cells[ytdColIndex].innerHTML = '<span class="money">' + formatMoney(ytdSum) + '</span>';
                }}
            }}

            return {{ monthly: monthlySum, ytd: ytdSum }};
        }}

        // Update section header total and percentage
        // For monthly/variable sections: pass ytdValue to update both monthly and YTD displays
        // filteredTotal is used to calculate the percentage of total spending
        function updateSectionTotal(sectionClass, value, perMonth = false, ytdValue = null, filteredTotal = null) {{
            const section = document.querySelector('.' + sectionClass);
            if (section) {{
                const monthlySpan = section.querySelector('.section-monthly');
                const ytdSpan = section.querySelector('.section-ytd');
                const pctSpan = section.querySelector('.section-pct');

                if (monthlySpan && ytdSpan && ytdValue !== null) {{
                    // Section has both monthly and YTD display
                    monthlySpan.textContent = formatMoney(value, true);
                    ytdSpan.textContent = formatMoney(ytdValue);
                    // Update percentage based on YTD value
                    if (pctSpan && filteredTotal !== null && filteredTotal > 0) {{
                        const pct = (ytdValue / filteredTotal * 100).toFixed(1);
                        pctSpan.textContent = '(' + pct + '%)';
                    }}
                }} else {{
                    // Sections with single total (annual, periodic, travel, oneoff)
                    const totalSpan = section.querySelector('.section-total');
                    if (totalSpan) {{
                        // Update the text content but preserve the pct span
                        const valueText = formatMoney(value, perMonth);
                        if (pctSpan && filteredTotal !== null && filteredTotal > 0) {{
                            const pct = (value / filteredTotal * 100).toFixed(1);
                            pctSpan.textContent = '(' + pct + '%)';
                            // Update the total span text (value + space before pct)
                            totalSpan.childNodes[0].textContent = valueText + ' ';
                        }} else {{
                            totalSpan.textContent = valueText;
                        }}
                    }}
                }}
            }}
        }}

        // Update summary cards
        function updateSummaryCards(monthlyTotal, variableTotal, annualTotal, periodicTotal, travelTotal, oneoffTotal) {{
            const trueMonthly = monthlyTotal + variableTotal;
            const nonRecurring = annualTotal + periodicTotal + travelTotal + oneoffTotal;

            // Calculate filtered total for percentages (convert monthly to YTD)
            const monthlyYtd = monthlyTotal * 12;
            const variableYtd = variableTotal * 12;
            const filteredTotal = monthlyYtd + variableYtd + annualTotal + periodicTotal + travelTotal + oneoffTotal;

            // Helper to format percentage
            const formatPct = (val) => filteredTotal > 0 ? (val / filteredTotal * 100).toFixed(1) + '%' : '0.0%';

            // Update Monthly Budget card
            const monthlyCard = document.querySelector('.card.monthly');
            if (monthlyCard) {{
                monthlyCard.querySelector('.amount').innerHTML = formatMoney(trueMonthly) + '<span style="font-size: 1rem; color: #888;">/mo</span>';
                const breakdownItems = monthlyCard.querySelectorAll('.breakdown-item .value');
                if (breakdownItems[0]) breakdownItems[0].innerHTML = formatMoney(monthlyTotal) + ' <span class="breakdown-pct">(' + formatPct(monthlyYtd) + ')</span>';
                if (breakdownItems[1]) breakdownItems[1].innerHTML = formatMoney(variableTotal) + ' <span class="breakdown-pct">(' + formatPct(variableYtd) + ')</span>';
            }}

            // Update Non-Recurring card
            const nonRecCard = document.querySelector('.card.non-recurring');
            if (nonRecCard) {{
                nonRecCard.querySelector('.amount').innerHTML = formatMoney(nonRecurring) + ' <span class="breakdown-pct">(' + formatPct(nonRecurring) + ')</span>';
                const breakdownItems = nonRecCard.querySelectorAll('.breakdown-item .value');
                if (breakdownItems[0]) breakdownItems[0].innerHTML = formatMoney(annualTotal) + ' <span class="breakdown-pct">(' + formatPct(annualTotal) + ')</span>';
                if (breakdownItems[1]) breakdownItems[1].innerHTML = formatMoney(periodicTotal) + ' <span class="breakdown-pct">(' + formatPct(periodicTotal) + ')</span>';
                if (breakdownItems[2]) breakdownItems[2].innerHTML = formatMoney(travelTotal) + ' <span class="breakdown-pct">(' + formatPct(travelTotal) + ')</span>';
                if (breakdownItems[3]) breakdownItems[3].innerHTML = formatMoney(oneoffTotal) + ' <span class="breakdown-pct">(' + formatPct(oneoffTotal) + ')</span>';
            }}
        }}

        // Filter tables based on search input
        function filterTables() {{
            const query = document.getElementById('searchInput').value.toLowerCase().trim();
            const tables = document.querySelectorAll('table');
            const sections = document.querySelectorAll('section');

            // If no query, show all and reset
            if (!query) {{
                document.querySelectorAll('section').forEach(section => {{
                    section.style.display = '';
                }});
                tables.forEach(table => {{
                    table.querySelectorAll('tbody tr:not(.total-row)').forEach(row => {{
                        row.classList.remove('hidden');
                        // Remove highlights
                        row.querySelectorAll('.highlight').forEach(el => {{
                            el.outerHTML = el.textContent;
                        }});
                    }});
                }});
                // Collapse all expanded transactions
                document.querySelectorAll('.merchant-row.expanded').forEach(row => {{
                    row.classList.remove('expanded');
                }});
                document.querySelectorAll('.txn-row').forEach(row => {{
                    row.classList.add('hidden');
                }});
                // Restore original totals when clearing filters
                restoreOriginalTotals();
                // Restore original total spending
                const totalEl = document.getElementById('totalSpending');
                const original = parseInt(totalEl.dataset.original);
                totalEl.textContent = formatCurrency(original);
                return;
            }}

            // Split query into words for multi-word search
            const queryWords = query ? query.split(/\\s+/).filter(w => w.length > 0) : [];

            tables.forEach(table => {{
                const rows = table.querySelectorAll('tbody tr:not(.total-row)');
                let hasVisibleRows = false;
                const merchantsWithMatchingTxns = new Set();

                // First pass: check all rows for matches
                rows.forEach(row => {{
                    const text = row.textContent.toLowerCase();
                    const matches = queryWords.every(word => text.includes(word));

                    if (matches) {{
                        row.classList.remove('hidden');
                        // If a txn-row matches, remember its merchant so we show the merchant row too
                        if (row.classList.contains('txn-row')) {{
                            merchantsWithMatchingTxns.add(row.dataset.merchant);
                        }}
                    }} else {{
                        row.classList.add('hidden');
                        row.querySelectorAll('.highlight').forEach(el => {{
                            el.outerHTML = el.textContent;
                        }});
                    }}
                }});

                // Second pass: ensure merchant rows are visible if any of their txns matched
                rows.forEach(row => {{
                    if (row.classList.contains('merchant-row')) {{
                        const merchantId = row.dataset.merchant;
                        const isVisible = !row.classList.contains('hidden') || merchantsWithMatchingTxns.has(merchantId);

                        if (isVisible) {{
                            row.classList.remove('hidden');
                            hasVisibleRows = true;
                            // Highlight matching text
                            row.querySelectorAll('td').forEach(cell => {{
                                let html = cell.textContent;
                                const escapedWords = queryWords.map(w => w.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'));
                                const regex = new RegExp(`(${{escapedWords.join('|')}})`, 'gi');
                                html = html.replace(regex, '<span class="highlight">$1</span>');
                                cell.innerHTML = html;
                            }});
                        }}
                    }}
                }});

                // Show/hide sections based on matches
                const section = table.closest('section');
                if (section) {{
                    const header = section.querySelector('.section-header');
                    const content = section.querySelector('.section-content');
                    if (hasVisibleRows) {{
                        section.style.display = '';
                        header?.classList.remove('collapsed');
                        content?.classList.remove('collapsed');
                    }} else {{
                        section.style.display = 'none';
                    }}
                }}
            }});

            updateAllTotals();
        }}

        function updateTotalsFromTransactions() {{
            // Calculate totals from visible transaction rows (for location filtering)
            // Sum amounts per section based on visible txn-rows
            const sectionTotals = {{
                'monthly-table': 0,
                'annual-table': 0,
                'periodic-table': 0,
                'travel-table': 0,
                'oneoff-table': 0,
                'variable-table': 0
            }};

            document.querySelectorAll('tr.txn-row:not(.hidden)').forEach(txn => {{
                // Use data-amount attribute for precise calculations
                const amount = parseFloat(txn.dataset.amount) || 0;
                const tableId = txn.closest('table')?.id;
                if (tableId && sectionTotals.hasOwnProperty(tableId)) {{
                    sectionTotals[tableId] += amount;
                }}
            }});

            const totalAmount = Object.values(sectionTotals).reduce((a, b) => a + b, 0);

            // Update section headers (pass totalAmount for percentage calculation)
            updateSectionTotal('monthly-section', sectionTotals['monthly-table'] / 12, true, sectionTotals['monthly-table'], totalAmount);
            updateSectionTotal('annual-section', sectionTotals['annual-table'], false, null, totalAmount);
            updateSectionTotal('periodic-section', sectionTotals['periodic-table'], false, null, totalAmount);
            updateSectionTotal('travel-section', sectionTotals['travel-table'], false, null, totalAmount);
            updateSectionTotal('oneoff-section', sectionTotals['oneoff-table'], false, null, totalAmount);
            updateSectionTotal('variable-section', sectionTotals['variable-table'] / 12, true, sectionTotals['variable-table'], totalAmount);

            // Update summary cards
            updateSummaryCards(
                sectionTotals['monthly-table'] / 12,
                sectionTotals['variable-table'] / 12,
                sectionTotals['annual-table'],
                sectionTotals['periodic-table'],
                sectionTotals['travel-table'],
                sectionTotals['oneoff-table']
            );

            // Update table total rows
            updateTableTotalRow('monthly-table', 4, sectionTotals['monthly-table'] / 12, 5, sectionTotals['monthly-table']);
            updateTableTotalRow('annual-table', null, null, 3, sectionTotals['annual-table']);
            updateTableTotalRow('periodic-table', null, null, 3, sectionTotals['periodic-table']);
            updateTableTotalRow('travel-table', null, null, 2, sectionTotals['travel-table']);
            updateTableTotalRow('oneoff-table', null, null, 3, sectionTotals['oneoff-table']);
            updateTableTotalRow('variable-table', 4, sectionTotals['variable-table'] / 12, 5, sectionTotals['variable-table']);

            // Update Total Spending card with percentage
            const totalEl = document.getElementById('totalSpending');
            const pct = (totalAmount / originalTotals.totalYtd * 100).toFixed(1);
            totalEl.innerHTML = formatCurrency(totalAmount) +
                '<span class="filter-pct"> (' + pct + '%)</span>';
        }}

        function updateTableTotalRow(tableId, monthlyColIndex, monthlyValue, ytdColIndex, ytdValue) {{
            const table = document.getElementById(tableId);
            if (!table) return;
            const totalRow = table.querySelector('.total-row');
            if (totalRow) {{
                if (monthlyColIndex !== null && totalRow.cells[monthlyColIndex]) {{
                    totalRow.cells[monthlyColIndex].innerHTML = '<span class="money">' + formatMoney(monthlyValue, true) + '</span>';
                }}
                if (ytdColIndex !== null && totalRow.cells[ytdColIndex]) {{
                    totalRow.cells[ytdColIndex].innerHTML = '<span class="money">' + formatMoney(ytdValue) + '</span>';
                }}
            }}
        }}

        function updateAllTotals() {{
            // Update all totals based on visible rows
            // Args: tableId, monthlyColIndex, ytdColIndex, pctColIndex, monthlyFromYtd
            const monthlyTotals = updateTableTotals('monthly-table', 4, 5, 6, true);
            const annualTotals = updateTableTotals('annual-table', null, 3, 4);
            const periodicTotals = updateTableTotals('periodic-table', null, 3, 4);
            const travelTotals = updateTableTotals('travel-table', null, 2, 3);
            const oneoffTotals = updateTableTotals('oneoff-table', null, 3, 4);
            const variableTotals = updateTableTotals('variable-table', 4, 5, 6, true);

            // Calculate filtered total for percentage calculations
            const totalYtd = monthlyTotals.ytd + annualTotals.ytd + periodicTotals.ytd + travelTotals.ytd + oneoffTotals.ytd + variableTotals.ytd;

            // Update section headers (pass totalYtd for percentage calculation)
            updateSectionTotal('monthly-section', monthlyTotals.monthly, true, monthlyTotals.ytd, totalYtd);
            updateSectionTotal('annual-section', annualTotals.ytd, false, null, totalYtd);
            updateSectionTotal('periodic-section', periodicTotals.ytd, false, null, totalYtd);
            updateSectionTotal('travel-section', travelTotals.ytd, false, null, totalYtd);
            updateSectionTotal('oneoff-section', oneoffTotals.ytd, false, null, totalYtd);
            updateSectionTotal('variable-section', variableTotals.monthly, true, variableTotals.ytd, totalYtd);

            // Update summary cards
            updateSummaryCards(
                monthlyTotals.monthly,
                variableTotals.monthly,
                annualTotals.ytd,
                periodicTotals.ytd,
                travelTotals.ytd,
                oneoffTotals.ytd
            );

            // Update Total Spending card with filtered total and percentage
            const totalEl = document.getElementById('totalSpending');
            const pct = (totalYtd / originalTotals.totalYtd * 100).toFixed(1);
            totalEl.innerHTML = formatCurrency(totalYtd) +
                '<span class="filter-pct"> (' + pct + '%)</span>';
        }}

        function restoreOriginalTotals() {{
            // Restore table totals for all visible rows
            updateTableTotals('monthly-table', 4, 5, 6, true);
            updateTableTotals('annual-table', null, 3, 4);
            updateTableTotals('periodic-table', null, 3, 4);
            updateTableTotals('travel-table', null, 2, 3);
            updateTableTotals('oneoff-table', null, 3, 4);
            updateTableTotals('variable-table', 4, 5, 6, true);

            // Restore section headers with original values (pass totalYtd for percentage calculation)
            updateSectionTotal('monthly-section', originalTotals.monthly, true, originalTotals.monthlyYtd, originalTotals.totalYtd);
            updateSectionTotal('annual-section', originalTotals.annual, false, null, originalTotals.totalYtd);
            updateSectionTotal('periodic-section', originalTotals.periodic, false, null, originalTotals.totalYtd);
            updateSectionTotal('travel-section', originalTotals.travel, false, null, originalTotals.totalYtd);
            updateSectionTotal('oneoff-section', originalTotals.oneoff, false, null, originalTotals.totalYtd);
            updateSectionTotal('variable-section', originalTotals.variable, true, originalTotals.variableYtd, originalTotals.totalYtd);

            // Restore summary cards with original values
            updateSummaryCards(
                originalTotals.monthly,
                originalTotals.variable,
                originalTotals.annual,
                originalTotals.periodic,
                originalTotals.travel,
                originalTotals.oneoff
            );
        }}

        // Sort table by column
        function sortTable(tableId, colIndex, type) {{
            const table = document.getElementById(tableId);
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr.merchant-row'));
            const th = table.querySelectorAll('th')[colIndex];

            // Determine sort direction
            const isAsc = th.classList.contains('sorted-asc');

            // Clear all sort indicators
            table.querySelectorAll('th').forEach(h => {{
                h.classList.remove('sorted-asc', 'sorted-desc');
            }});

            // Sort rows
            rows.sort((a, b) => {{
                let aVal = a.cells[colIndex].textContent.trim();
                let bVal = b.cells[colIndex].textContent.trim();

                if (type === 'money') {{
                    aVal = parseFloat(aVal.replace(/[$,]/g, '')) || 0;
                    bVal = parseFloat(bVal.replace(/[$,]/g, '')) || 0;
                }} else if (type === 'number') {{
                    aVal = parseFloat(aVal) || 0;
                    bVal = parseFloat(bVal) || 0;
                }} else {{
                    aVal = aVal.toLowerCase();
                    bVal = bVal.toLowerCase();
                }}

                if (isAsc) {{
                    return aVal < bVal ? 1 : aVal > bVal ? -1 : 0;
                }} else {{
                    return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
                }}
            }});

            // Update sort indicator
            th.classList.add(isAsc ? 'sorted-desc' : 'sorted-asc');

            // Re-append rows in new order (merchant rows + their transaction rows)
            const totalRow = tbody.querySelector('.total-row');
            const allTxnRows = Array.from(tbody.querySelectorAll('.txn-row'));
            rows.forEach(row => {{
                tbody.appendChild(row);
                // Append associated transaction rows right after merchant row
                const merchantId = row.dataset.merchant;
                allTxnRows.filter(txn => txn.dataset.merchant === merchantId)
                    .forEach(txn => tbody.appendChild(txn));
            }});
            if (totalRow) tbody.appendChild(totalRow);
        }}

        // Keyboard shortcut for search
        document.addEventListener('keydown', (e) => {{
            if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {{
                e.preventDefault();
                document.getElementById('searchInput').focus();
            }}
            if (e.key === 'Escape') {{
                document.getElementById('searchInput').blur();
                document.getElementById('searchInput').value = '';
                autocompleteList.classList.remove('show');
                filterTables();
            }}
        }});

        // Card click to scroll to section
        document.querySelectorAll('.card').forEach((card, index) => {{
            card.addEventListener('click', () => {{
                const sections = document.querySelectorAll('section');
                if (index === 0 && sections[0]) {{
                    sections[0].scrollIntoView({{ behavior: 'smooth' }});
                }} else if (index === 1) {{
                    // Non-recurring - scroll to annual
                    sections[1]?.scrollIntoView({{ behavior: 'smooth' }});
                }}
            }});
        }});

        // ============================================
        // Sticky search bar shadow on scroll
        // ============================================
        window.addEventListener('scroll', () => {{
            const searchBox = document.querySelector('.search-box');
            if (window.scrollY > 50) {{
                searchBox.classList.add('scrolled');
            }} else {{
                searchBox.classList.remove('scrolled');
            }}
        }});

        // ============================================
        // Click-to-filter on cells (merchant, category, location)
        // ============================================
        function addFilterFromCell(event, element, filterType) {{
            event.stopPropagation(); // Don't trigger row expand
            let text = element.textContent.trim();

            // For category, extract main category (e.g., "Food" from "Food/Delivery")
            if (filterType === 'category') {{
                text = text.split('/')[0];
            }}
            // For merchant, remove chevron if present
            if (filterType === 'merchant') {{
                text = text.replace(/^[▶▼]\\s*/, '');
            }}

            addFilter(text, filterType);
        }}

        // ============================================
        // Hash-based filter bookmarks
        // ============================================

        function filtersToHash() {{
            if (activeFilters.length === 0) {{
                history.replaceState(null, null, window.location.pathname);
                return;
            }}
            const encoded = activeFilters.map(f => {{
                const mode = f.mode === 'exclude' ? '-' : '+';
                const type = f.type.charAt(0); // c=category, m=merchant, l=location
                return mode + type + ':' + encodeURIComponent(f.text);
            }}).join('&');
            history.replaceState(null, null, '#' + encoded);
        }}

        function hashToFilters() {{
            const hash = window.location.hash.slice(1);
            if (!hash) return;

            const parts = hash.split('&');
            parts.forEach(part => {{
                if (part.length < 3) return;
                const mode = part.charAt(0) === '-' ? 'exclude' : 'include';
                const typeChar = part.charAt(1);
                const type = typeChar === 'c' ? 'category' : typeChar === 'm' ? 'merchant' : 'location';
                const text = decodeURIComponent(part.slice(3)); // skip mode + type + ':'
                if (text && !activeFilters.some(f => f.text === text && f.type === type)) {{
                    activeFilters.push({{ text, type, mode }});
                }}
            }});

            if (activeFilters.length > 0) {{
                renderFilters();
                applyFilters();
            }}
        }}

        // Update hash when filters change (patch applyFilters)
        const originalApplyFilters = applyFilters;
        applyFilters = function() {{
            originalApplyFilters();
            filtersToHash();
        }};

        // Load filters from hash on page load
        window.addEventListener('hashchange', () => {{
            activeFilters = [];
            hashToFilters();
        }});

        // Initial load from hash
        hashToFilters();
    </script>

    <!-- Embedded JSON data for LLM analysis tools -->
    <script id="report-data" type="application/json">
{embedded_json}
    </script>
</body>
</html>'''

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)
