"""
Merchant normalization utilities for spending analysis.

This module provides functions to clean and categorize merchant descriptions
from credit card and bank statements.
"""

import csv
import os
import re
from datetime import date
from typing import Optional, List, Tuple

from .modifier_parser import (
    parse_pattern_with_modifiers,
    check_all_conditions,
    ParsedPattern,
    ModifierParseError,
)



def load_merchant_rules(csv_path):
    """Load user merchant categorization rules from CSV file.

    CSV format: Pattern,Merchant,Category,Subcategory

    Patterns support inline modifiers for amount/date matching:
        COSTCO[amount>200] - Match COSTCO transactions over $200
        BESTBUY[date=2025-01-15] - Match BESTBUY on specific date
        MERCHANT[amount:50-200][date:2025-01-01..2025-12-31] - Combined

    Lines starting with # are treated as comments and skipped.
    Patterns are Python regular expressions matched against transaction descriptions.

    Returns list of tuples: (pattern, merchant_name, category, subcategory, parsed_pattern)
    """
    if not os.path.exists(csv_path):
        return []  # No user rules file

    rules = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        # Filter out comment and empty lines before passing to DictReader
        lines = [line for line in f if line.strip() and not line.strip().startswith('#')]
        reader = csv.DictReader(lines)
        for row in reader:
            # Skip empty patterns
            pattern_str = row.get('Pattern', '').strip()
            if not pattern_str:
                continue

            # Parse pattern with inline modifiers
            try:
                parsed = parse_pattern_with_modifiers(pattern_str)
            except ModifierParseError:
                # Invalid modifier syntax - use pattern as-is without modifiers
                parsed = ParsedPattern(regex_pattern=pattern_str)

            rules.append((
                parsed.regex_pattern,  # Pure regex for matching
                row['Merchant'],
                row['Category'],
                row['Subcategory'],
                parsed  # Full parsed pattern with conditions
            ))
    return rules


def get_all_rules(csv_path=None):
    """Get user-defined merchant rules.

    Args:
        csv_path: Optional path to user's merchant_categories.csv

    Returns:
        List of (pattern, merchant, category, subcategory, parsed_pattern, source) tuples.
        Source is always 'user' for rules from the CSV file.
    """
    user_rules_with_source = []
    if csv_path:
        user_rules = load_merchant_rules(csv_path)
        # Add source='user' to each rule
        for rule in user_rules:
            if len(rule) == 5:
                pattern, merchant, category, subcategory, parsed = rule
                user_rules_with_source.append((pattern, merchant, category, subcategory, parsed, 'user'))
            else:
                pattern, merchant, category, subcategory = rule
                parsed = ParsedPattern(regex_pattern=pattern)
                user_rules_with_source.append((pattern, merchant, category, subcategory, parsed, 'user'))

    return user_rules_with_source


def diagnose_rules(csv_path=None):
    """Get detailed diagnostic information about rule loading.

    Returns a dict with:
        - user_rules_path: Path to user rules file (or None)
        - user_rules_exists: Whether the user rules file exists
        - user_rules_count: Number of user rules loaded
        - user_rules: List of user rules (pattern, merchant, category, subcategory)
        - user_rules_errors: List of any errors encountered while loading
        - total_rules: Total rules count (same as user_rules_count)
    """
    import re

    result = {
        'user_rules_path': csv_path,
        'user_rules_exists': False,
        'user_rules_count': 0,
        'user_rules': [],
        'user_rules_errors': [],
        'total_rules': 0,
    }

    if not csv_path:
        return result

    result['user_rules_exists'] = os.path.exists(csv_path)

    if not result['user_rules_exists']:
        return result

    # Load user rules with detailed error tracking
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()
            result['file_size_bytes'] = len(raw_content)
            result['file_lines'] = raw_content.count('\n') + 1

        with open(csv_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            non_comment_lines = [line for line in lines if not line.strip().startswith('#') and line.strip()]
            result['non_comment_lines'] = len(non_comment_lines)

            # Check for header
            if non_comment_lines:
                first_line = non_comment_lines[0].strip()
                if 'Pattern' in first_line and 'Merchant' in first_line:
                    result['has_header'] = True
                else:
                    result['has_header'] = False
                    result['user_rules_errors'].append(
                        f"Missing or invalid header. Expected 'Pattern,Merchant,Category,Subcategory', got: {first_line[:50]}"
                    )

        # Now load rules with validation
        with open(csv_path, 'r', encoding='utf-8') as f:
            # Filter out comments AND empty lines
            lines = [line for line in f if line.strip() and not line.strip().startswith('#')]
            reader = csv.DictReader(lines)

            row_num = 1  # Start after header
            for row in reader:
                row_num += 1
                pattern = row.get('Pattern', '').strip()

                if not pattern:
                    continue  # Skip empty patterns silently

                # Validate the regex pattern
                try:
                    re.compile(pattern, re.IGNORECASE)
                except re.error as e:
                    result['user_rules_errors'].append(
                        f"Row {row_num}: Invalid regex pattern '{pattern}': {e}"
                    )
                    continue

                merchant = row.get('Merchant', '').strip()
                category = row.get('Category', '').strip()
                subcategory = row.get('Subcategory', '').strip()

                if not merchant:
                    result['user_rules_errors'].append(
                        f"Row {row_num}: Missing merchant name for pattern '{pattern}'"
                    )
                if not category:
                    result['user_rules_errors'].append(
                        f"Row {row_num}: Missing category for pattern '{pattern}'"
                    )

                result['user_rules'].append((pattern, merchant, category, subcategory))

        result['user_rules_count'] = len(result['user_rules'])
        result['total_rules'] = result['user_rules_count']

    except Exception as e:
        result['user_rules_errors'].append(f"Failed to read file: {e}")

    return result


def clean_description(description):
    """Clean and normalize raw transaction descriptions.

    Handles common prefixes, suffixes, and formatting issues that
    can't be represented in simple pattern matching rules.
    """
    cleaned = description

    # Remove common payment processor prefixes
    prefixes = [
        r'^APLPAY\s+',      # Apple Pay
        r'^AplPay\s+',      # Apple Pay (alternate case)
        r'^SQ\s*\*',        # Square
        r'^TST\*\s*',       # Toast POS
        r'^SP\s+',          # Shopify
        r'^PY\s*\*',        # PayPal merchant
        r'^PP\s*\*',        # PayPal
        r'^GOOGLE\s*\*',    # Google Pay (but keep for YouTube matching)
        r'^BT\s*\*?\s*DD\s*\*?',  # DoorDash via various processors
    ]

    for prefix in prefixes:
        cleaned = re.sub(prefix, '', cleaned, flags=re.IGNORECASE)

    # Remove BOA statement suffixes (ID numbers, confirmation codes)
    cleaned = re.sub(r'\s+DES:.*$', '', cleaned)
    cleaned = re.sub(r'\s+ID:.*$', '', cleaned)
    cleaned = re.sub(r'\s+INDN:.*$', '', cleaned)
    cleaned = re.sub(r'\s+CO ID:.*$', '', cleaned)
    cleaned = re.sub(r'\s+Confirmation#.*$', '', cleaned, flags=re.IGNORECASE)

    # Remove trailing location info (City, State format)
    # But be careful not to remove too much
    cleaned = re.sub(r'\s{2,}[A-Z]{2}$', '', cleaned)  # Trailing state code

    # Normalize whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned


def extract_merchant_name(description):
    """Extract a readable merchant name from a cleaned description.

    Used as fallback when no pattern matches.
    """
    cleaned = clean_description(description)

    # Remove non-alphabetic characters for grouping, keep first 2-3 words
    words = re.sub(r'[^A-Za-z\s]', ' ', cleaned).split()[:3]

    if words:
        return ' '.join(words).title()
    return 'Unknown'


def normalize_merchant(
    description: str,
    rules: list,
    amount: Optional[float] = None,
    txn_date: Optional[date] = None
) -> Tuple[str, str, str, Optional[dict]]:
    """Normalize a merchant description to (name, category, subcategory, match_info).

    Args:
        description: Raw transaction description
        rules: List of (pattern, merchant, category, subcategory, parsed_pattern) tuples
              or (pattern, merchant, category, subcategory, parsed_pattern, source) tuples
        amount: Optional transaction amount for modifier matching
        txn_date: Optional transaction date for modifier matching

    Returns:
        Tuple of (merchant_name, category, subcategory, match_info)
        match_info is a dict with 'pattern', 'source', or None if no match
    """
    # Clean the description for better matching
    cleaned = clean_description(description)
    desc_upper = description.upper()
    cleaned_upper = cleaned.upper()

    # Try pattern matching against both original and cleaned
    for rule in rules:
        # Handle various formats: 4-tuple, 5-tuple, 6-tuple (with source)
        if len(rule) == 6:
            pattern, merchant, category, subcategory, parsed, source = rule
        elif len(rule) == 5:
            pattern, merchant, category, subcategory, parsed = rule
            source = 'unknown'
        else:
            pattern, merchant, category, subcategory = rule
            parsed = None
            source = 'unknown'

        try:
            # Check regex pattern first (most common case)
            regex_matches = (
                re.search(pattern, desc_upper, re.IGNORECASE) or
                re.search(pattern, cleaned_upper, re.IGNORECASE)
            )
            if not regex_matches:
                continue

            # If pattern has modifiers, check them
            if parsed and (parsed.amount_conditions or parsed.date_conditions):
                if not check_all_conditions(parsed, amount, txn_date):
                    continue

            return (merchant, category, subcategory, {'pattern': pattern, 'source': source})
        except re.error:
            # Invalid regex pattern, skip
            continue

    # Fallback: extract merchant name from description, categorize as Unknown
    merchant_name = extract_merchant_name(description)
    return (merchant_name, 'Unknown', 'Unknown', None)
