"""Tests for merchant utilities - rule loading and matching."""

import pytest
import tempfile
import os
from datetime import date

from tally.merchant_utils import (
    load_merchant_rules,
    get_all_rules,
    normalize_merchant,
    clean_description,
    extract_merchant_name,
)
from tally.modifier_parser import ParsedPattern


class TestLoadMerchantRules:
    """Tests for loading rules from CSV files."""

    def test_load_simple_rules(self):
        """Load basic rules from CSV."""
        csv_content = """Pattern,Merchant,Category,Subcategory
COSTCO,Costco,Food,Grocery
STARBUCKS,Starbucks,Food,Coffee
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert len(rules) == 2
            # Rules are 5-tuples: (pattern, merchant, category, subcategory, parsed)
            assert rules[0][0] == 'COSTCO'
            assert rules[0][1] == 'Costco'
            assert rules[0][2] == 'Food'
            assert rules[0][3] == 'Grocery'
        finally:
            os.unlink(f.name)

    def test_load_rules_with_modifiers(self):
        """Load rules with inline modifiers."""
        csv_content = """Pattern,Merchant,Category,Subcategory
COSTCO[amount>200],Costco Bulk,Shopping,Bulk
BESTBUY[date=2025-01-15],TV Purchase,Shopping,Electronics
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert len(rules) == 2

            # First rule: COSTCO with amount modifier
            assert rules[0][0] == 'COSTCO'  # Regex pattern (modifier stripped)
            assert rules[0][1] == 'Costco Bulk'
            assert len(rules[0][4].amount_conditions) == 1
            assert rules[0][4].amount_conditions[0].operator == '>'
            assert rules[0][4].amount_conditions[0].value == 200.0

            # Second rule: BESTBUY with date modifier
            assert rules[1][0] == 'BESTBUY'
            assert rules[1][1] == 'TV Purchase'
            assert len(rules[1][4].date_conditions) == 1
            assert rules[1][4].date_conditions[0].value == date(2025, 1, 15)
        finally:
            os.unlink(f.name)

    def test_load_rules_with_comments(self):
        """Comments should be ignored."""
        csv_content = """Pattern,Merchant,Category,Subcategory
# This is a comment
COSTCO,Costco,Food,Grocery
# Another comment
STARBUCKS,Starbucks,Food,Coffee
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert len(rules) == 2
            assert rules[0][1] == 'Costco'
            assert rules[1][1] == 'Starbucks'
        finally:
            os.unlink(f.name)

    def test_load_rules_with_empty_lines(self):
        """Empty lines should be ignored."""
        csv_content = """Pattern,Merchant,Category,Subcategory

COSTCO,Costco,Food,Grocery

STARBUCKS,Starbucks,Food,Coffee

"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert len(rules) == 2
        finally:
            os.unlink(f.name)

    def test_load_rules_with_regex_patterns(self):
        """Load rules with complex regex patterns."""
        csv_content = """Pattern,Merchant,Category,Subcategory
UBER\\s(?!EATS),Uber,Transport,Rideshare
COSTCO(?!.*GAS),Costco,Food,Grocery
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert len(rules) == 2
            assert rules[0][0] == 'UBER\\s(?!EATS)'
            assert rules[1][0] == 'COSTCO(?!.*GAS)'
        finally:
            os.unlink(f.name)

    def test_load_nonexistent_file(self):
        """Loading nonexistent file returns empty list."""
        rules = load_merchant_rules('/nonexistent/path/rules.csv')
        assert rules == []

    def test_load_rules_skip_empty_patterns(self):
        """Empty patterns should be skipped."""
        csv_content = """Pattern,Merchant,Category,Subcategory
COSTCO,Costco,Food,Grocery
,Empty Pattern,Food,Other
STARBUCKS,Starbucks,Food,Coffee
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert len(rules) == 2
            assert rules[0][1] == 'Costco'
            assert rules[1][1] == 'Starbucks'
        finally:
            os.unlink(f.name)


class TestNormalizeMerchant:
    """Tests for normalize_merchant function (the rule matching engine)."""

    def test_simple_pattern_match(self):
        """Match simple pattern."""
        rules = [
            ('COSTCO', 'Costco', 'Food', 'Grocery', ParsedPattern(regex_pattern='COSTCO')),
        ]
        result = normalize_merchant('COSTCO WHOLESALE #1234', rules)
        assert result[:3] == ('Costco', 'Food', 'Grocery')

    def test_case_insensitive_match(self):
        """Matching should be case-insensitive."""
        rules = [
            ('COSTCO', 'Costco', 'Food', 'Grocery', ParsedPattern(regex_pattern='COSTCO')),
        ]
        result = normalize_merchant('costco wholesale', rules)
        assert result[:3] == ('Costco', 'Food', 'Grocery')

    def test_first_match_wins(self):
        """First matching rule wins."""
        rules = [
            ('COSTCO GAS', 'Costco Gas', 'Transport', 'Gas', ParsedPattern(regex_pattern='COSTCO GAS')),
            ('COSTCO', 'Costco', 'Food', 'Grocery', ParsedPattern(regex_pattern='COSTCO')),
        ]
        result = normalize_merchant('COSTCO GAS STATION', rules)
        assert result[:3] == ('Costco Gas', 'Transport', 'Gas')

    def test_no_match_returns_unknown(self):
        """No match returns Unknown category."""
        rules = [
            ('COSTCO', 'Costco', 'Food', 'Grocery', ParsedPattern(regex_pattern='COSTCO')),
        ]
        result = normalize_merchant('RANDOM MERCHANT XYZ', rules)
        assert result[1] == 'Unknown'
        assert result[2] == 'Unknown'

    def test_regex_pattern_match(self):
        """Match using regex pattern."""
        rules = [
            ('UBER\\s(?!EATS)', 'Uber', 'Transport', 'Rideshare',
             ParsedPattern(regex_pattern='UBER\\s(?!EATS)')),
            ('UBER\\s*EATS', 'Uber Eats', 'Food', 'Delivery',
             ParsedPattern(regex_pattern='UBER\\s*EATS')),
        ]

        # Should match Uber (not Uber Eats)
        result = normalize_merchant('UBER RIDE 12345', rules)
        assert result[:3] == ('Uber', 'Transport', 'Rideshare')

        # Should match Uber Eats
        result = normalize_merchant('UBER EATS ORDER', rules)
        assert result[:3] == ('Uber Eats', 'Food', 'Delivery')

    def test_amount_modifier_match(self):
        """Amount modifier affects matching."""
        from tally.modifier_parser import parse_pattern_with_modifiers

        rules = [
            ('COSTCO', 'Costco Bulk', 'Shopping', 'Bulk',
             parse_pattern_with_modifiers('COSTCO[amount>200]')),
            ('COSTCO', 'Costco', 'Food', 'Grocery',
             ParsedPattern(regex_pattern='COSTCO')),
        ]

        # Large purchase -> Bulk
        result = normalize_merchant('COSTCO WHOLESALE', rules, amount=250)
        assert result[:3] == ('Costco Bulk', 'Shopping', 'Bulk')

        # Small purchase -> Grocery (skips first rule)
        result = normalize_merchant('COSTCO WHOLESALE', rules, amount=50)
        assert result[:3] == ('Costco', 'Food', 'Grocery')

    def test_date_modifier_match(self):
        """Date modifier affects matching."""
        from tally.modifier_parser import parse_pattern_with_modifiers

        rules = [
            ('BESTBUY', 'TV Purchase', 'Shopping', 'Electronics',
             parse_pattern_with_modifiers('BESTBUY[date=2025-01-15]')),
            ('BESTBUY', 'Best Buy', 'Shopping', 'Retail',
             ParsedPattern(regex_pattern='BESTBUY')),
        ]

        # Matching date -> TV Purchase
        result = normalize_merchant('BESTBUY STORE', rules, txn_date=date(2025, 1, 15))
        assert result[:3] == ('TV Purchase', 'Shopping', 'Electronics')

        # Different date -> Best Buy (skips first rule)
        result = normalize_merchant('BESTBUY STORE', rules, txn_date=date(2025, 1, 16))
        assert result[:3] == ('Best Buy', 'Shopping', 'Retail')

    def test_combined_modifiers(self):
        """Combined amount and date modifiers."""
        from tally.modifier_parser import parse_pattern_with_modifiers

        rules = [
            ('BESTBUY', 'That Specific Purchase', 'Personal', 'Gifts',
             parse_pattern_with_modifiers('BESTBUY[amount=499.99][date=2025-01-15]')),
            ('BESTBUY', 'Best Buy', 'Shopping', 'Electronics',
             ParsedPattern(regex_pattern='BESTBUY')),
        ]

        # Both match -> specific purchase
        result = normalize_merchant('BESTBUY', rules, amount=499.99, txn_date=date(2025, 1, 15))
        assert result[:3] == ('That Specific Purchase', 'Personal', 'Gifts')

        # Wrong amount -> generic
        result = normalize_merchant('BESTBUY', rules, amount=100, txn_date=date(2025, 1, 15))
        assert result[:3] == ('Best Buy', 'Shopping', 'Electronics')

        # Wrong date -> generic
        result = normalize_merchant('BESTBUY', rules, amount=499.99, txn_date=date(2025, 1, 16))
        assert result[:3] == ('Best Buy', 'Shopping', 'Electronics')

    def test_backward_compatible_4tuple(self):
        """Should work with old 4-tuple format."""
        rules = [
            ('COSTCO', 'Costco', 'Food', 'Grocery'),  # 4-tuple, no parsed pattern
        ]
        result = normalize_merchant('COSTCO WHOLESALE', rules)
        assert result[:3] == ('Costco', 'Food', 'Grocery')

    def test_amount_range_modifier(self):
        """Amount range modifier."""
        from tally.modifier_parser import parse_pattern_with_modifiers

        rules = [
            ('RESTAURANT', 'Fine Dining', 'Food', 'Restaurant',
             parse_pattern_with_modifiers('RESTAURANT[amount:100-500]')),
            ('RESTAURANT', 'Casual Dining', 'Food', 'Restaurant',
             ParsedPattern(regex_pattern='RESTAURANT')),
        ]

        # In range -> Fine Dining
        result = normalize_merchant('RESTAURANT XYZ', rules, amount=200)
        assert result[:3] == ('Fine Dining', 'Food', 'Restaurant')

        # Below range -> Casual
        result = normalize_merchant('RESTAURANT XYZ', rules, amount=50)
        assert result[:3] == ('Casual Dining', 'Food', 'Restaurant')

    def test_month_modifier(self):
        """Month modifier for seasonal categorization."""
        from tally.modifier_parser import parse_pattern_with_modifiers

        rules = [
            ('AMAZON', 'Holiday Shopping', 'Shopping', 'Gifts',
             parse_pattern_with_modifiers('AMAZON[month=12]')),
            ('AMAZON', 'Amazon', 'Shopping', 'Online',
             ParsedPattern(regex_pattern='AMAZON')),
        ]

        # December -> Holiday Shopping
        result = normalize_merchant('AMAZON.COM', rules, txn_date=date(2025, 12, 15))
        assert result[:3] == ('Holiday Shopping', 'Shopping', 'Gifts')

        # Other month -> regular Amazon
        result = normalize_merchant('AMAZON.COM', rules, txn_date=date(2025, 6, 15))
        assert result[:3] == ('Amazon', 'Shopping', 'Online')


class TestCleanDescription:
    """Tests for clean_description function."""

    def test_removes_common_prefixes(self):
        """Should remove common transaction prefixes."""
        # These are common payment processor prefixes
        assert 'STARBUCKS' in clean_description('SQ *STARBUCKS COFFEE')
        assert 'RESTAURANT' in clean_description('TST* RESTAURANT')

    def test_handles_normal_description(self):
        """Normal descriptions should pass through."""
        result = clean_description('COSTCO WHOLESALE')
        assert 'COSTCO' in result


class TestExtractMerchantName:
    """Tests for extract_merchant_name function."""

    def test_extracts_merchant_name(self):
        """Should extract clean merchant name from description."""
        # Basic extraction
        result = extract_merchant_name('STARBUCKS STORE 12345 SEATTLE WA')
        assert 'STARBUCKS' in result.upper() or 'Starbucks' in result

    def test_handles_simple_name(self):
        """Simple names should be returned as-is or title-cased."""
        result = extract_merchant_name('NETFLIX')
        assert 'Netflix' in result or 'NETFLIX' in result


class TestGetAllRules:
    """Tests for get_all_rules function."""

    def test_returns_empty_when_no_user_rules(self):
        """Should return empty list when no user file."""
        rules = get_all_rules(None)
        assert len(rules) == 0  # No baseline rules

    def test_user_rules_loaded(self):
        """User rules should be loaded from CSV file."""
        csv_content = """Pattern,Merchant,Category,Subcategory
MYCUSTOM,My Custom Merchant,Custom,Category
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules(f.name)

            # Should have the user rule
            assert len(rules) == 1
            assert rules[0][1] == 'My Custom Merchant'

            # All rules should be 6-tuples (with source)
            assert all(len(r) == 6 for r in rules)
        finally:

            os.unlink(f.name)

    def test_user_rule_matching(self):
        """User rules should match transactions."""
        csv_content = """Pattern,Merchant,Category,Subcategory
NETFLIX,My Netflix,Entertainment,Movies
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules(f.name)

            # When we match NETFLIX, user rule should match
            merchant, category, subcategory, match_info = normalize_merchant('NETFLIX.COM', rules)
            assert (merchant, category, subcategory) == ('My Netflix', 'Entertainment', 'Movies')
            assert match_info['source'] == 'user'
        finally:
            os.unlink(f.name)
