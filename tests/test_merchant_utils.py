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
    _expr_to_regex,
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

            # All rules should be 7-tuples (with source and tags)
            assert all(len(r) == 7 for r in rules)
            # Tags should be empty list when not specified
            assert rules[0][6] == []
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


class TestTags:
    """Tests for tag parsing and matching."""

    def test_load_rules_with_tags(self):
        """Load rules with Tags column."""
        csv_content = """Pattern,Merchant,Category,Subcategory,Tags
NETFLIX,Netflix,Subscriptions,Streaming,entertainment|recurring
UBER,Uber,Transport,Rideshare,business|reimbursable
COSTCO,Costco,Food,Grocery,
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert len(rules) == 3
            # Rules are now 6-tuples: (pattern, merchant, category, subcategory, parsed, tags)
            assert rules[0][5] == ['entertainment', 'recurring']
            assert rules[1][5] == ['business', 'reimbursable']
            assert rules[2][5] == []  # Empty tags
        finally:
            os.unlink(f.name)

    def test_normalize_returns_tags_in_match_info(self):
        """normalize_merchant should return tags in match_info."""
        csv_content = """Pattern,Merchant,Category,Subcategory,Tags
NETFLIX,Netflix,Subscriptions,Streaming,entertainment|recurring
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules(f.name)
            merchant, category, subcategory, match_info = normalize_merchant('NETFLIX.COM', rules)

            assert merchant == 'Netflix'
            assert match_info['tags'] == ['entertainment', 'recurring']
        finally:
            os.unlink(f.name)

    def test_normalize_empty_tags_when_no_tags(self):
        """normalize_merchant returns empty tags list when rule has no tags."""
        csv_content = """Pattern,Merchant,Category,Subcategory,Tags
COSTCO,Costco,Food,Grocery,
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules(f.name)
            merchant, category, subcategory, match_info = normalize_merchant('COSTCO WHOLESALE', rules)

            assert merchant == 'Costco'
            assert match_info['tags'] == []
        finally:
            os.unlink(f.name)

    def test_diagnose_rules_includes_tag_stats(self):
        """diagnose_rules should include tag statistics."""
        from tally.merchant_utils import diagnose_rules

        csv_content = """Pattern,Merchant,Category,Subcategory,Tags
NETFLIX,Netflix,Subscriptions,Streaming,entertainment|recurring
UBER,Uber,Transport,Rideshare,business
COSTCO,Costco,Food,Grocery,
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            diag = diagnose_rules(f.name)

            assert diag['rules_with_tags'] == 2  # Netflix and Uber have tags
            assert diag['unique_tags'] == {'entertainment', 'recurring', 'business'}
        finally:
            os.unlink(f.name)

    def test_tags_with_whitespace_are_trimmed(self):
        """Tags with leading/trailing whitespace should be trimmed."""
        csv_content = """Pattern,Merchant,Category,Subcategory,Tags
NETFLIX,Netflix,Subscriptions,Streaming, entertainment | recurring
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert rules[0][5] == ['entertainment', 'recurring']
        finally:
            os.unlink(f.name)

    def test_missing_tags_column_in_row_handled_gracefully(self):
        """Rows with fewer columns than header (Tags=None) should work."""
        # This simulates a CSV where header has Tags but some rows don't have that column
        csv_content = """Pattern,Merchant,Category,Subcategory,Tags
NETFLIX,Netflix,Subscriptions,Streaming,entertainment
COSTCO,Costco,Food,Grocery
UBER,Uber,Transport,Rideshare,business
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = load_merchant_rules(f.name)

            assert len(rules) == 3
            assert rules[0][5] == ['entertainment']
            assert rules[1][5] == []  # Row has no Tags column value
            assert rules[2][5] == ['business']
        finally:
            os.unlink(f.name)


class TestExprToRegex:
    """Tests for _expr_to_regex function - converting match expressions to regex."""

    def test_contains_simple(self):
        """Extract pattern from contains() expression."""
        assert _expr_to_regex('contains("NETFLIX")') == 'NETFLIX'
        assert _expr_to_regex('contains("COSTCO")') == 'COSTCO'

    def test_contains_single_quotes(self):
        """Should work with single quotes."""
        assert _expr_to_regex("contains('NETFLIX')") == 'NETFLIX'

    def test_contains_with_spaces(self):
        """Handles patterns with spaces."""
        assert _expr_to_regex('contains("WHOLE FOODS")') == 'WHOLE FOODS'

    def test_regex_simple(self):
        """Extract pattern from regex() expression."""
        assert _expr_to_regex('regex("UBER.*EATS")') == 'UBER.*EATS'

    def test_regex_negative_lookahead(self):
        """Extract negative lookahead pattern from regex()."""
        assert _expr_to_regex(r'regex("UBER(?!.*EATS)")') == r'UBER(?!.*EATS)'

    def test_contains_with_conditions(self):
        """Extract pattern when expression has additional conditions."""
        # Should extract just the pattern, ignoring 'and amount > 200'
        assert _expr_to_regex('contains("COSTCO") and amount > 200') == 'COSTCO'

    def test_contains_with_complex_conditions(self):
        """Extract pattern from complex expression."""
        expr = 'contains("AMAZON") and month == 12 and amount > 100'
        assert _expr_to_regex(expr) == 'AMAZON'

    def test_quoted_string_fallback(self):
        """Falls back to first quoted string if no function found."""
        assert _expr_to_regex('"NETFLIX"') == 'NETFLIX'

    def test_passthrough_for_plain_pattern(self):
        """Returns expression as-is if no quotes or function."""
        assert _expr_to_regex('NETFLIX') == 'NETFLIX'


class TestGetAllRulesRulesFormat:
    """Tests for get_all_rules loading .rules files."""

    def test_load_simple_rules_file(self):
        """Load rules from .rules file."""
        content = """[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Spotify]
match: contains("SPOTIFY")
category: Subscriptions
subcategory: Music
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            assert len(rules) == 2
            # First rule - full expression preserved for expr matching
            assert rules[0][0] == 'contains("NETFLIX")'
            assert rules[0][1] == 'Netflix'  # Merchant name
            assert rules[0][2] == 'Subscriptions'  # Category
            assert rules[0][3] == 'Streaming'  # Subcategory
            # Second rule
            assert rules[1][0] == 'contains("SPOTIFY")'
            assert rules[1][1] == 'Spotify'
        finally:
            os.unlink(f.name)

    def test_load_rules_with_tags(self):
        """Load .rules file with tags."""
        content = """[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment, recurring
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            assert len(rules) == 1
            # Tags are at index 6
            assert set(rules[0][6]) == {'entertainment', 'recurring'}
        finally:
            os.unlink(f.name)

    def test_load_rules_regex_pattern(self):
        """Load .rules file with regex() match expression."""
        content = r"""[Uber Rides]
match: regex("UBER(?!.*EATS)")
category: Transportation
subcategory: Rideshare
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            assert len(rules) == 1
            # Full expression preserved for expr matching
            assert rules[0][0] == r'regex("UBER(?!.*EATS)")'
            assert rules[0][1] == 'Uber Rides'
        finally:
            os.unlink(f.name)

    def test_rules_can_match_transactions(self):
        """Rules loaded from .rules should work with normalize_merchant."""
        content = """[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)
            merchant, category, subcategory, match_info = normalize_merchant('NETFLIX.COM', rules)

            assert merchant == 'Netflix'
            assert category == 'Subscriptions'
            assert subcategory == 'Streaming'
        finally:
            os.unlink(f.name)

    def test_rules_with_amount_conditions(self):
        """Amount conditions in expressions should work when loaded from .rules files."""
        content = """[Costco Gas]
match: contains("COSTCO") and amount <= 100
category: Transportation
subcategory: Gas

[Costco Groceries]
match: contains("COSTCO") and amount > 100
category: Food
subcategory: Grocery
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # Low amount should match Gas
            merchant, category, subcategory, _ = normalize_merchant(
                'COSTCO FUEL', rules, amount=50.00
            )
            assert merchant == 'Costco Gas'
            assert category == 'Transportation'
            assert subcategory == 'Gas'

            # High amount should match Groceries
            merchant, category, subcategory, _ = normalize_merchant(
                'COSTCO WHOLESALE', rules, amount=200.00
            )
            assert merchant == 'Costco Groceries'
            assert category == 'Food'
            assert subcategory == 'Grocery'
        finally:
            os.unlink(f.name)


class TestNegativeLookaheadMatching:
    """Tests for negative lookahead patterns in .rules format."""

    def test_uber_not_uber_eats_matching(self):
        """Negative lookahead should match Uber but not Uber Eats."""
        content = r"""[Uber Rides]
match: regex("UBER(?!.*EATS)")
category: Transportation
subcategory: Rideshare

[Uber Eats]
match: contains("UBER") and contains("EATS")
category: Food
subcategory: Delivery
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # "UBER TRIP" should match Uber Rides
            merchant, category, subcategory, _ = normalize_merchant('UBER TRIP 12345', rules)
            assert merchant == 'Uber Rides'
            assert category == 'Transportation'

            # "UBER EATS" should NOT match Uber Rides (negative lookahead)
            # It should fall through to Uber Eats rule
            merchant, category, subcategory, _ = normalize_merchant('UBER EATS ORDER', rules)
            assert merchant == 'Uber Eats'
            assert category == 'Food'
        finally:
            os.unlink(f.name)

    def test_negative_lookahead_various_formats(self):
        """Test negative lookahead with different Uber description formats."""
        content = r"""[Uber Rides]
match: regex("UBER(?!.*EATS)")
category: Transportation
subcategory: Rideshare
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # Should match - regular Uber
            for desc in ['UBER', 'UBER TRIP', 'UBER*RIDE', 'UBER BV AMSTERDAM']:
                merchant, _, _, _ = normalize_merchant(desc, rules)
                assert merchant == 'Uber Rides', f"'{desc}' should match Uber Rides"

            # Should NOT match - Uber Eats variations
            for desc in ['UBER EATS', 'UBEREATS', 'UBER* EATS', 'UBER EATS ORDER']:
                merchant, category, _, _ = normalize_merchant(desc, rules)
                assert category == 'Unknown', f"'{desc}' should NOT match Uber Rides (got {merchant})"
        finally:
            os.unlink(f.name)


class TestRulesFormatComplexConditions:
    """Tests for .rules format with conditions (amount, date, etc.)."""

    def test_amount_condition_in_expression(self):
        """Amount conditions in match expression should be preserved."""
        content = """[Costco Bulk]
match: contains("COSTCO") and amount > 200
category: Shopping
subcategory: Wholesale

[Costco Grocery]
match: contains("COSTCO")
category: Food
subcategory: Grocery
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # Both rules should load with full expressions preserved
            assert len(rules) == 2
            # Full expressions preserved for expr matching (amount conditions work)
            assert rules[0][0] == 'contains("COSTCO") and amount > 200'
            assert rules[1][0] == 'contains("COSTCO")'
        finally:
            os.unlink(f.name)


class TestTwoPassTagging:
    """Tests for two-pass tagging in normalize_merchant (collect tags from ALL matching rules)."""

    def test_tags_from_multiple_matching_rules(self):
        """Tags are collected from ALL matching rules, not just the first."""
        content = """[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment

[Large Purchase]
match: amount > 500
tags: large

[Holiday Season]
match: month >= 11 and month <= 12
tags: holiday
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # Match Netflix + Large + Holiday
            merchant, category, subcategory, match_info = normalize_merchant(
                'NETFLIX PREMIUM',
                rules,
                amount=600.00,
                txn_date=date(2025, 12, 15)
            )

            # Category from first categorization rule (Netflix)
            assert merchant == 'Netflix'
            assert category == 'Subscriptions'

            # Tags from ALL matching rules
            assert 'entertainment' in match_info['tags']
            assert 'large' in match_info['tags']
            assert 'holiday' in match_info['tags']
        finally:
            os.unlink(f.name)

    def test_tag_only_rules_dont_set_category(self):
        """Tag-only rules (no category) don't affect categorization."""
        content = """[Large Purchase]
match: amount > 500
tags: large, expensive

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # Large Purchase matches first, but shouldn't set category
            merchant, category, subcategory, match_info = normalize_merchant(
                'NETFLIX PREMIUM',
                rules,
                amount=600.00
            )

            # Category from Netflix rule (first with category)
            assert merchant == 'Netflix'
            assert category == 'Subscriptions'
            assert subcategory == 'Streaming'

            # Tags from both matching rules
            assert 'large' in match_info['tags']
            assert 'expensive' in match_info['tags']
            assert 'entertainment' in match_info['tags']
        finally:
            os.unlink(f.name)

    def test_unknown_merchant_still_gets_tags(self):
        """Unknown merchants can have tags from tag-only rules."""
        content = """[Large Purchase]
match: amount > 500
tags: large

[Holiday Season]
match: month == 12
tags: holiday
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # No categorization rule matches, but tag-only rules do
            merchant, category, subcategory, match_info = normalize_merchant(
                'RANDOM UNKNOWN MERCHANT',
                rules,
                amount=750.00,
                txn_date=date(2025, 12, 25)
            )

            # No categorization - Unknown
            assert category == 'Unknown'
            assert subcategory == 'Unknown'

            # But tags from matching tag-only rules
            assert match_info is not None
            assert 'large' in match_info['tags']
            assert 'holiday' in match_info['tags']
        finally:
            os.unlink(f.name)

    def test_tags_deduplicated_order_preserved(self):
        """Duplicate tags are removed, order preserved."""
        content = """[Netflix]
match: contains("NETFLIX")
category: Subscriptions
tags: recurring, entertainment

[Streaming Service]
match: contains("NETFLIX") or contains("HULU")
tags: entertainment, streaming

[Monthly Bill]
match: amount < 50
tags: recurring, small
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            merchant, category, subcategory, match_info = normalize_merchant(
                'NETFLIX.COM',
                rules,
                amount=15.99
            )

            # Tags should be deduplicated
            tags = match_info['tags']
            assert tags.count('recurring') == 1
            assert tags.count('entertainment') == 1

            # All unique tags present
            assert 'recurring' in tags
            assert 'entertainment' in tags
            assert 'streaming' in tags
            assert 'small' in tags
        finally:
            os.unlink(f.name)

    def test_no_matching_rules_returns_none_match_info(self):
        """When no rules match, match_info is None."""
        content = """[Netflix]
match: contains("NETFLIX")
category: Subscriptions
tags: entertainment
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # No rule matches
            merchant, category, subcategory, match_info = normalize_merchant(
                'RANDOM MERCHANT',
                rules
            )

            assert category == 'Unknown'
            assert match_info is None  # No tags either
        finally:
            os.unlink(f.name)


class TestApplyTagRules:
    """Tests for apply_tag_rules function."""

    def test_apply_tag_rules_basic(self):
        """Apply tag-only rules to a transaction."""
        from tally.merchant_utils import apply_tag_rules, get_tag_only_rules

        content = """[Netflix]
match: contains("NETFLIX")
category: Subscriptions
tags: entertainment

[Large Purchase]
match: amount > 500
tags: large

[Holiday]
match: month == 12
tags: holiday
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            tag_rules = get_tag_only_rules(f.name)

            # Should have 2 tag-only rules (Large Purchase and Holiday)
            assert len(tag_rules) == 2

            txn = {
                'description': 'RANDOM MERCHANT',
                'amount': 750.00,
                'date': date(2025, 12, 25)
            }
            additional_tags = apply_tag_rules(txn, tag_rules)

            assert 'large' in additional_tags
            assert 'holiday' in additional_tags
            # entertainment not included (Netflix has category, not tag-only)
            assert 'entertainment' not in additional_tags
        finally:
            os.unlink(f.name)

    def test_apply_tag_rules_with_dynamic_tags(self):
        """Apply tag-only rules with dynamic tag expressions."""
        from tally.merchant_utils import apply_tag_rules, get_tag_only_rules

        content = """[Tag By Source]
match: source != ""
tags: {source}
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            tag_rules = get_tag_only_rules(f.name)
            assert len(tag_rules) == 1

            txn = {
                'description': 'PURCHASE',
                'amount': 50.00,
                'source': 'AmexGold'
            }
            additional_tags = apply_tag_rules(txn, tag_rules)

            assert 'amexgold' in additional_tags
        finally:
            os.unlink(f.name)

    def test_get_tag_only_rules_empty_for_csv(self):
        """get_tag_only_rules returns empty for non-.rules files."""
        from tally.merchant_utils import get_tag_only_rules

        # CSV file
        result = get_tag_only_rules('/path/to/file.csv')
        assert result == []

        # None
        result = get_tag_only_rules(None)
        assert result == []

        # Empty string
        result = get_tag_only_rules('')
        assert result == []


class TestNormalizeMerchantWithLocation:
    """Tests for normalize_merchant with location parameter."""

    def test_location_passed_to_rule_matching(self):
        """Location is available in rule expressions."""
        content = """[Hawaii Store]
match: contains("STORE") and regex(field.location, "\\\\bHI$")
category: Travel
subcategory: Shopping

[Regular Store]
match: contains("STORE")
category: Shopping
subcategory: Retail
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # Hawaii store should match the Hawaii rule
            merchant, category, subcategory, _ = normalize_merchant(
                "ABC STORE",
                rules,
                amount=50.00,
                location="HONOLULU\nHI"
            )
            assert merchant == "Hawaii Store"
            assert category == "Travel"
            assert subcategory == "Shopping"

            # Non-Hawaii store should match the regular rule
            merchant, category, subcategory, _ = normalize_merchant(
                "ABC STORE",
                rules,
                amount=50.00,
                location="SEATTLE\nWA"
            )
            assert merchant == "Regular Store"
            assert category == "Shopping"
            assert subcategory == "Retail"

        finally:
            os.unlink(f.name)

    def test_location_with_contains(self):
        """contains() works with field.location."""
        content = """[Lahaina Shop]
match: contains(field.location, "LAHAINA")
category: Travel
subcategory: Shopping
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # Lahaina location should match
            merchant, category, subcategory, _ = normalize_merchant(
                "RANDOM SHOP",
                rules,
                amount=50.00,
                location="LAHAINA\nHI"
            )
            assert merchant == "Lahaina Shop"
            assert category == "Travel"

            # Non-Lahaina should not match
            merchant, category, subcategory, _ = normalize_merchant(
                "RANDOM SHOP",
                rules,
                amount=50.00,
                location="SEATTLE\nWA"
            )
            assert category == "Unknown"

        finally:
            os.unlink(f.name)

    def test_location_none_doesnt_break_matching(self):
        """Rules still work when location is None."""
        content = """[Test Store]
match: contains("STORE")
category: Shopping
subcategory: Retail
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.rules', delete=False)
        try:
            f.write(content)
            f.close()

            rules = get_all_rules(f.name)

            # Location None should still match description-based rules
            merchant, category, subcategory, _ = normalize_merchant(
                "ABC STORE",
                rules,
                amount=50.00,
                location=None
            )
            assert merchant == "Test Store"
            assert category == "Shopping"

        finally:
            os.unlink(f.name)
