"""Tests for the merchant rule engine."""

import pytest
from datetime import date
from tally.merchant_engine import (
    MerchantEngine,
    MerchantRule,
    MatchResult,
    MerchantParseError,
    parse_merchants,
    csv_to_rules,
    csv_to_merchants_content,
    csv_rule_to_merchant_rule,
)
from tally.modifier_parser import parse_pattern_with_modifiers


class TestMerchantRule:
    """Tests for MerchantRule dataclass."""

    def test_merchant_defaults_to_name(self):
        """Merchant field defaults to rule name."""
        rule = MerchantRule(name="Netflix", match_expr='contains("NETFLIX")')
        assert rule.merchant == "Netflix"

    def test_merchant_can_be_overridden(self):
        """Merchant field can be set explicitly."""
        rule = MerchantRule(
            name="Netflix Streaming",
            match_expr='contains("NETFLIX")',
            merchant="Netflix"
        )
        assert rule.merchant == "Netflix"

    def test_is_categorization_rule(self):
        """is_categorization_rule is True when category is set."""
        cat_rule = MerchantRule(
            name="Test", match_expr="true", category="Shopping"
        )
        tag_rule = MerchantRule(
            name="Test", match_expr="true", tags={"large"}
        )
        assert cat_rule.is_categorization_rule
        assert not tag_rule.is_categorization_rule


class TestParsing:
    """Tests for parsing .rules files."""

    def test_simple_rule(self):
        """Parse a simple rule."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
'''
        engine = parse_merchants(content)
        assert len(engine.rules) == 1
        rule = engine.rules[0]
        assert rule.name == "Netflix"
        assert rule.match_expr == 'contains("NETFLIX")'
        assert rule.category == "Subscriptions"
        assert rule.subcategory == "Streaming"
        assert rule.merchant == "Netflix"

    def test_rule_with_tags(self):
        """Parse a rule with tags."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment, recurring
'''
        engine = parse_merchants(content)
        assert engine.rules[0].tags == {"entertainment", "recurring"}

    def test_rule_with_custom_merchant(self):
        """Parse a rule with custom merchant name."""
        content = '''
[Netflix Streaming Service]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
merchant: Netflix
'''
        engine = parse_merchants(content)
        assert engine.rules[0].name == "Netflix Streaming Service"
        assert engine.rules[0].merchant == "Netflix"

    def test_tag_only_rule(self):
        """Parse a tag-only rule (no category)."""
        content = '''
[Large Purchase]
match: amount > 500
tags: large
'''
        engine = parse_merchants(content)
        assert len(engine.rules) == 1
        rule = engine.rules[0]
        assert rule.category == ""
        assert rule.tags == {"large"}
        assert not rule.is_categorization_rule

    def test_rule_with_priority(self):
        """Parse a rule with priority."""
        content = '''
[Uber Eats]
priority: 100
match: contains("UBER") and contains("EATS")
category: Food
'''
        engine = parse_merchants(content)
        assert engine.rules[0].priority == 100

    def test_priority_default(self):
        """Priority defaults to 50."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
'''
        engine = parse_merchants(content)
        assert engine.rules[0].priority == 50

    def test_priority_sorting(self):
        """Rules are sorted by priority (descending)."""
        content = '''
[Low Priority]
priority: 10
match: contains("LOW")
category: Test

[High Priority]
priority: 100
match: contains("HIGH")
category: Test

[Default Priority]
match: contains("DEFAULT")
category: Test
'''
        engine = parse_merchants(content)
        assert engine.rules[0].name == "High Priority"
        assert engine.rules[0].priority == 100
        assert engine.rules[1].name == "Default Priority"
        assert engine.rules[1].priority == 50
        assert engine.rules[2].name == "Low Priority"
        assert engine.rules[2].priority == 10

    def test_multiple_rules(self):
        """Parse multiple rules."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: Online
'''
        engine = parse_merchants(content)
        assert len(engine.rules) == 2
        assert engine.rules[0].name == "Netflix"
        assert engine.rules[1].name == "Amazon"

    def test_comments_ignored(self):
        """Comments are ignored."""
        content = '''
# This is a comment
[Netflix]
# Another comment
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
'''
        engine = parse_merchants(content)
        assert len(engine.rules) == 1

    def test_variables(self):
        """Parse top-level variables."""
        content = '''
is_large = amount > 500
is_holiday = month >= 11 and month <= 12

[Large Holiday Purchase]
match: is_large and is_holiday
category: Shopping
subcategory: Holiday
'''
        engine = parse_merchants(content)
        assert "is_large" in engine.variables
        assert "is_holiday" in engine.variables
        assert engine.variables["is_large"] == "amount > 500"

    def test_empty_rule_name_error(self):
        """Empty rule name raises error."""
        content = '''
[]
match: true
category: Test
'''
        with pytest.raises(MerchantParseError, match="Empty rule name"):
            parse_merchants(content)

    def test_missing_match_error(self):
        """Missing match expression raises error."""
        content = '''
[Test]
category: Shopping
'''
        with pytest.raises(MerchantParseError, match="missing 'match:'"):
            parse_merchants(content)

    def test_missing_category_and_tags_error(self):
        """Rule must have category or tags."""
        content = '''
[Test]
match: true
'''
        with pytest.raises(MerchantParseError, match="must have 'category:' or 'tags:'"):
            parse_merchants(content)

    def test_invalid_match_expression_error(self):
        """Invalid match expression raises error."""
        content = '''
[Test]
match: invalid syntax here!!
category: Test
'''
        with pytest.raises(MerchantParseError, match="Invalid match expression"):
            parse_merchants(content)

    def test_unknown_property_error(self):
        """Unknown property raises error."""
        content = '''
[Test]
match: true
category: Test
unknown_property: value
'''
        with pytest.raises(MerchantParseError, match="Unknown property"):
            parse_merchants(content)


class TestMatching:
    """Tests for transaction matching."""

    def test_simple_match(self):
        """Match a simple contains rule."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
'''
        engine = parse_merchants(content)
        txn = {'description': 'NETFLIX.COM STREAMING', 'amount': 15.99}
        result = engine.match(txn)

        assert result.matched
        assert result.merchant == "Netflix"
        assert result.category == "Subscriptions"
        assert result.subcategory == "Streaming"

    def test_no_match(self):
        """No match returns empty result."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
'''
        engine = parse_merchants(content)
        txn = {'description': 'AMAZON PURCHASE', 'amount': 45.00}
        result = engine.match(txn)

        assert not result.matched
        assert result.merchant == ""
        assert result.category == ""

    def test_most_specific_wins(self):
        """Most specific matching rule wins when match_mode='most_specific'."""
        content = '''
[Streaming Service]
match: contains("NETFLIX")
category: Entertainment
subcategory: Streaming

[Netflix Specific]
match: contains("NETFLIX") and contains(".COM")
category: Subscriptions
subcategory: Monthly
'''
        # Use most_specific mode (opt-in)
        engine = parse_merchants(content, match_mode='most_specific')
        txn = {'description': 'NETFLIX.COM', 'amount': 15.99}
        result = engine.match(txn)

        # More specific rule wins (2 pattern conditions vs 1)
        assert result.category == "Subscriptions"
        assert result.subcategory == "Monthly"
        assert result.merchant == "Netflix Specific"

    def test_first_match_wins_default(self):
        """First matching rule wins by default (backwards compatible)."""
        content = '''
[Streaming Service]
match: contains("NETFLIX")
category: Entertainment
subcategory: Streaming

[Netflix Specific]
match: contains("NETFLIX") and contains(".COM")
category: Subscriptions
subcategory: Monthly
'''
        # Default mode is first_match
        engine = parse_merchants(content)
        txn = {'description': 'NETFLIX.COM', 'amount': 15.99}
        result = engine.match(txn)

        # First matching rule wins (backwards compatible)
        assert result.category == "Entertainment"
        assert result.subcategory == "Streaming"
        assert result.merchant == "Streaming Service"

    def test_amount_condition(self):
        """Amount condition in match expression."""
        content = '''
[Small Costco]
match: contains("COSTCO") and amount <= 200
category: Food
subcategory: Grocery

[Large Costco]
match: contains("COSTCO") and amount > 200
category: Shopping
subcategory: Wholesale
'''
        engine = parse_merchants(content)

        small = {'description': 'COSTCO #123', 'amount': 75.00}
        large = {'description': 'COSTCO #123', 'amount': 350.00}

        assert engine.match(small).category == "Food"
        assert engine.match(large).category == "Shopping"

    def test_date_condition(self):
        """Date conditions in match expression."""
        content = '''
[Black Friday]
match: contains("BESTBUY") and date >= "2025-11-28" and date <= "2025-11-30"
category: Shopping
subcategory: Holiday

[Regular BestBuy]
match: contains("BESTBUY")
category: Shopping
subcategory: Electronics
'''
        engine = parse_merchants(content)

        bf = {'description': 'BESTBUY', 'amount': 500, 'date': date(2025, 11, 29)}
        regular = {'description': 'BESTBUY', 'amount': 500, 'date': date(2025, 7, 15)}

        assert engine.match(bf).subcategory == "Holiday"
        assert engine.match(regular).subcategory == "Electronics"

    def test_regex_match(self):
        """Regex pattern matching."""
        content = '''
[Uber Rides]
match: regex("UBER(?!.*EATS)")
category: Transportation
subcategory: Rideshare

[Uber Eats]
match: contains("UBER") and contains("EATS")
category: Food
subcategory: Delivery
'''
        engine = parse_merchants(content)

        rides = {'description': 'UBER TRIP', 'amount': 25.00}
        eats = {'description': 'UBER EATS ORDER', 'amount': 30.00}

        assert engine.match(rides).category == "Transportation"
        assert engine.match(eats).category == "Food"


class TestSpecificityResolution:
    """Tests for specificity-based matching (requires match_mode='most_specific')."""

    def test_longer_pattern_more_specific(self):
        """Longer pattern is more specific than shorter."""
        content = '''
[Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: General

[Amazon Prime]
match: contains("AMAZON PRIME")
category: Subscriptions
subcategory: Prime
'''
        engine = parse_merchants(content, match_mode='most_specific')

        # Short pattern matches less specific rule
        regular = {'description': 'AMAZON ORDER', 'amount': 50.0}
        assert engine.match(regular).category == "Shopping"

        # Longer pattern matches more specific rule
        prime = {'description': 'AMAZON PRIME MEMBERSHIP', 'amount': 15.0}
        assert engine.match(prime).category == "Subscriptions"

    def test_more_conditions_more_specific(self):
        """Rule with more conditions is more specific."""
        content = '''
[Any Costco]
match: contains("COSTCO")
category: Shopping
subcategory: General

[Large Costco]
match: contains("COSTCO") and amount > 200
category: Shopping
subcategory: Bulk
'''
        engine = parse_merchants(content, match_mode='most_specific')

        # Small purchase - only general rule matches
        small = {'description': 'COSTCO #123', 'amount': 50.0}
        assert engine.match(small).subcategory == "General"

        # Large purchase - both match, more specific wins
        large = {'description': 'COSTCO #123', 'amount': 500.0}
        assert engine.match(large).subcategory == "Bulk"

    def test_per_field_resolution(self):
        """Merchant, category, subcategory resolved independently."""
        content = '''
[Costco Food]
match: contains("COSTCO") and amount < 100
category: Food
subcategory: Grocery
merchant: Costco

[Large Purchase]
match: amount > 200
category: Shopping
subcategory: Big Ticket

[Holiday Tag]
match: month >= 11 and month <= 12
tags: holiday
'''
        engine = parse_merchants(content, match_mode='most_specific')
        from datetime import date

        # Transaction matches Large Purchase for category/subcategory
        # and Holiday Tag for tags
        txn = {
            'description': 'COSTCO WHOLESALE',
            'amount': 500.0,
            'date': date(2025, 12, 15)
        }
        result = engine.match(txn)

        # category from Large Purchase (amount > 200)
        assert result.category == "Shopping"
        # subcategory from Large Purchase
        assert result.subcategory == "Big Ticket"
        # tags accumulated from Holiday Tag
        assert "holiday" in result.tags

    def test_priority_overrides_specificity(self):
        """Explicit priority beats calculated specificity."""
        content = '''
[General Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: General
priority: 100

[Specific Amazon]
match: contains("AMAZON") and contains(".COM")
category: Subscriptions
subcategory: Online
priority: 10
'''
        engine = parse_merchants(content)
        txn = {'description': 'AMAZON.COM ORDER', 'amount': 50.0}
        result = engine.match(txn)

        # Higher priority (100) wins over higher specificity
        assert result.category == "Shopping"
        assert result.merchant == "General Amazon"

    def test_all_matching_rules_tracked(self):
        """All matching rules are tracked in result."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Large]
match: amount > 50
tags: large

[Entertainment]
match: contains("NETFLIX") or contains("HULU")
tags: entertainment
'''
        engine = parse_merchants(content)
        txn = {'description': 'NETFLIX.COM', 'amount': 100.0}
        result = engine.match(txn)

        # All 3 rules should be in all_matching_rules
        assert len(result.all_matching_rules) == 3
        rule_names = {r.name for r in result.all_matching_rules}
        assert rule_names == {"Netflix", "Large", "Entertainment"}


class TestTwoPassTagging:
    """Tests for two-pass evaluation (categorization + tagging)."""

    def test_tags_from_categorization_rule(self):
        """Tags are collected from the categorization rule."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment, recurring
'''
        engine = parse_merchants(content)
        txn = {'description': 'NETFLIX', 'amount': 15.99}
        result = engine.match(txn)

        assert result.tags == {"entertainment", "recurring"}

    def test_tags_accumulated_from_all_matches(self):
        """Tags accumulate from ALL matching rules."""
        content = '''
[Netflix]
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
'''
        engine = parse_merchants(content)

        # Match Netflix + Large + Holiday
        txn = {
            'description': 'NETFLIX PREMIUM',
            'amount': 600.00,
            'date': date(2025, 12, 15)
        }
        result = engine.match(txn)

        assert result.matched
        assert result.category == "Subscriptions"
        assert result.tags == {"entertainment", "large", "holiday"}
        assert len(result.tag_rules) == 3

    def test_tag_only_rules_dont_categorize(self):
        """Tag-only rules don't set category."""
        content = '''
[Large Purchase]
match: amount > 500
tags: large

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
'''
        engine = parse_merchants(content)

        # Large Purchase matches first, but shouldn't categorize
        txn = {'description': 'NETFLIX', 'amount': 600.00}
        result = engine.match(txn)

        # Category comes from Netflix rule
        assert result.category == "Subscriptions"
        # Tags include both
        assert "large" in result.tags

    def test_uncategorized_but_tagged(self):
        """Transaction can have tags without category."""
        content = '''
[Large Purchase]
match: amount > 500
tags: large

[Holiday]
match: month == 12
tags: holiday
'''
        engine = parse_merchants(content)

        txn = {
            'description': 'UNKNOWN MERCHANT',
            'amount': 750.00,
            'date': date(2025, 12, 25)
        }
        result = engine.match(txn)

        assert not result.matched  # No categorization
        assert result.category == ""
        assert result.tags == {"large", "holiday"}  # But has tags


class TestVariables:
    """Tests for variable support in rules."""

    def test_variable_in_match(self):
        """Variables can be used in match expressions."""
        content = '''
is_large = amount > 500

[Large Purchase]
match: is_large
category: Shopping
subcategory: Big Ticket
'''
        engine = parse_merchants(content)

        small = {'description': 'STORE', 'amount': 100}
        large = {'description': 'STORE', 'amount': 750}

        assert not engine.match(small).matched
        assert engine.match(large).matched

    def test_combined_variable_and_pattern(self):
        """Variables combined with patterns."""
        content = '''
is_holiday = month >= 11 and month <= 12
is_large = amount > 100

[Holiday Gift]
match: contains("AMAZON") and is_holiday and is_large
category: Shopping
subcategory: Gifts
tags: holiday
'''
        engine = parse_merchants(content)

        holiday_gift = {
            'description': 'AMAZON ORDER',
            'amount': 150,
            'date': date(2025, 12, 10)
        }
        regular = {
            'description': 'AMAZON ORDER',
            'amount': 150,
            'date': date(2025, 6, 10)
        }
        small_holiday = {
            'description': 'AMAZON ORDER',
            'amount': 25,
            'date': date(2025, 12, 10)
        }

        assert engine.match(holiday_gift).matched
        assert not engine.match(regular).matched
        assert not engine.match(small_holiday).matched


class TestEngineProperties:
    """Tests for engine utility methods."""

    def test_categorization_rules(self):
        """Get only categorization rules."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Large]
match: amount > 500
tags: large
'''
        engine = parse_merchants(content)

        assert len(engine.categorization_rules) == 1
        assert engine.categorization_rules[0].name == "Netflix"

    def test_tag_only_rules(self):
        """Get only tag-only rules."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Large]
match: amount > 500
tags: large

[Holiday]
match: month == 12
tags: holiday
'''
        engine = parse_merchants(content)

        assert len(engine.tag_only_rules) == 2
        names = {r.name for r in engine.tag_only_rules}
        assert names == {"Large", "Holiday"}

    def test_match_all(self):
        """Match multiple transactions."""
        content = '''
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
'''
        engine = parse_merchants(content)

        txns = [
            {'description': 'NETFLIX', 'amount': 15.99},
            {'description': 'AMAZON', 'amount': 45.00},
            {'description': 'NETFLIX HD', 'amount': 22.99},
        ]
        results = engine.match_all(txns)

        assert len(results) == 3
        assert results[0].matched
        assert not results[1].matched
        assert results[2].matched


class TestCSVConversion:
    """Tests for CSV to MerchantRule conversion."""

    def test_simple_csv_rule(self):
        """Convert simple CSV rule without modifiers."""
        parsed = parse_pattern_with_modifiers("NETFLIX")
        rule = csv_rule_to_merchant_rule(
            pattern="NETFLIX",
            merchant="Netflix",
            category="Subscriptions",
            subcategory="Streaming",
            parsed_pattern=parsed,
            tags=["entertainment"],
        )

        assert rule.name == "Netflix"
        assert rule.merchant == "Netflix"
        assert rule.category == "Subscriptions"
        assert rule.subcategory == "Streaming"
        assert "entertainment" in rule.tags
        assert 'regex("NETFLIX")' in rule.match_expr

    def test_csv_rule_with_amount_modifier(self):
        """Convert CSV rule with amount modifier."""
        parsed = parse_pattern_with_modifiers("COSTCO[amount>200]")
        rule = csv_rule_to_merchant_rule(
            pattern=parsed.regex_pattern,  # "COSTCO"
            merchant="Costco",
            category="Shopping",
            subcategory="Wholesale",
            parsed_pattern=parsed,
        )

        assert 'regex("COSTCO")' in rule.match_expr
        assert "amount > 200" in rule.match_expr

    def test_csv_rule_with_amount_range(self):
        """Convert CSV rule with amount range modifier."""
        parsed = parse_pattern_with_modifiers("STORE[amount:50-200]")
        rule = csv_rule_to_merchant_rule(
            pattern=parsed.regex_pattern,
            merchant="Store",
            category="Shopping",
            subcategory="General",
            parsed_pattern=parsed,
        )

        assert "amount >= 50" in rule.match_expr
        assert "amount <= 200" in rule.match_expr

    def test_csv_rule_with_month_modifier(self):
        """Convert CSV rule with month modifier."""
        parsed = parse_pattern_with_modifiers("STORE[month=12]")
        rule = csv_rule_to_merchant_rule(
            pattern=parsed.regex_pattern,
            merchant="Store",
            category="Shopping",
            subcategory="Holiday",
            parsed_pattern=parsed,
        )

        assert "month == 12" in rule.match_expr

    def test_csv_rule_with_date_modifier(self):
        """Convert CSV rule with exact date modifier."""
        parsed = parse_pattern_with_modifiers("STORE[date=2025-11-29]")
        rule = csv_rule_to_merchant_rule(
            pattern=parsed.regex_pattern,
            merchant="Store",
            category="Shopping",
            subcategory="Black Friday",
            parsed_pattern=parsed,
        )

        assert 'date == "2025-11-29"' in rule.match_expr

    def test_csv_to_rules_list(self):
        """Convert list of CSV rules."""
        csv_rules = [
            ("NETFLIX", "Netflix", "Subscriptions", "Streaming",
             parse_pattern_with_modifiers("NETFLIX"), "user", ["entertainment"]),
            ("AMAZON", "Amazon", "Shopping", "Online",
             parse_pattern_with_modifiers("AMAZON"), "user", []),
        ]
        rules = csv_to_rules(csv_rules)

        assert len(rules) == 2
        assert rules[0].merchant == "Netflix"
        assert rules[1].merchant == "Amazon"
        assert "entertainment" in rules[0].tags

    def test_csv_to_merchants_content(self):
        """Convert CSV rules to .rules file content."""
        csv_rules = [
            ("NETFLIX", "Netflix", "Subscriptions", "Streaming",
             parse_pattern_with_modifiers("NETFLIX"), "user", ["entertainment", "recurring"]),
            ("COSTCO", "Costco", "Shopping", "Wholesale",
             parse_pattern_with_modifiers("COSTCO[amount>200]"), "user", []),
        ]
        content = csv_to_merchants_content(csv_rules)

        # Check Netflix rule
        assert "[Netflix]" in content
        assert 'match: regex("NETFLIX")' in content
        assert "category: Subscriptions" in content
        assert "tags: entertainment, recurring" in content

        # Check Costco rule with modifier
        assert "[Costco]" in content
        assert "amount > 200" in content

    def test_converted_rules_match_correctly(self):
        """Converted CSV rules match transactions correctly."""
        csv_rules = [
            ("NETFLIX", "Netflix", "Subscriptions", "Streaming",
             parse_pattern_with_modifiers("NETFLIX"), "user", []),
            ("COSTCO", "Costco Large", "Shopping", "Wholesale",
             parse_pattern_with_modifiers("COSTCO[amount>200]"), "user", []),
            ("COSTCO", "Costco Grocery", "Food", "Grocery",
             parse_pattern_with_modifiers("COSTCO[amount<=200]"), "user", []),
        ]
        rules = csv_to_rules(csv_rules)

        engine = MerchantEngine()
        engine.rules = rules

        # Test Netflix match
        netflix = {'description': 'NETFLIX.COM', 'amount': 15.99}
        result = engine.match(netflix)
        assert result.merchant == "Netflix"
        assert result.category == "Subscriptions"

        # Test Costco large purchase
        large_costco = {'description': 'COSTCO #123', 'amount': 350.00}
        result = engine.match(large_costco)
        assert result.merchant == "Costco Large"
        assert result.category == "Shopping"

        # Test Costco small purchase
        small_costco = {'description': 'COSTCO #123', 'amount': 75.00}
        result = engine.match(small_costco)
        assert result.merchant == "Costco Grocery"
        assert result.category == "Food"

    def test_regex_pattern_preserved(self):
        """Complex regex patterns are preserved during conversion."""
        # Uber rides vs Uber Eats pattern
        parsed = parse_pattern_with_modifiers(r"UBER\s(?!EATS)")
        rule = csv_rule_to_merchant_rule(
            pattern=parsed.regex_pattern,
            merchant="Uber Rides",
            category="Transportation",
            subcategory="Rideshare",
            parsed_pattern=parsed,
        )

        engine = MerchantEngine()
        engine.rules = [rule]

        uber_rides = {'description': 'UBER TRIP', 'amount': 25.00}
        uber_eats = {'description': 'UBER EATS ORDER', 'amount': 30.00}

        assert engine.match(uber_rides).matched
        assert not engine.match(uber_eats).matched


class TestTagParsingWithExpressions:
    """Tests for parsing tags that contain expressions with commas."""

    def test_tag_with_expression_containing_comma(self):
        """Expression with comma inside parentheses is preserved as single tag."""
        content = '''
[Test Rule]
match: exists(field.name)
category: Test
subcategory: Test
tags: {split(field.name, " ", 0)}
'''
        engine = parse_merchants(content)
        assert len(engine.rules) == 1
        assert engine.rules[0].tags == {'{split(field.name, " ", 0)}'}

    def test_multiple_tags_with_expression(self):
        """Multiple tags where one contains an expression with commas."""
        content = '''
[Test Rule]
match: exists(field.cardholder)
category: Test
subcategory: Test
tags: static_tag, {split(field.cardholder, " ", 0)}, another_tag
'''
        engine = parse_merchants(content)
        assert len(engine.rules) == 1
        expected = {'static_tag', '{split(field.cardholder, " ", 0)}', 'another_tag'}
        assert engine.rules[0].tags == expected

    def test_expression_with_nested_parentheses(self):
        """Expression with nested function calls is preserved."""
        content = '''
[Test Rule]
match: true
category: Test
subcategory: Test
tags: {lower(split(field.name, " ", 0))}
'''
        engine = parse_merchants(content)
        assert engine.rules[0].tags == {'{lower(split(field.name, " ", 0))}'}

    def test_multiple_expressions_as_tags(self):
        """Multiple expression tags each with commas inside."""
        content = '''
[Test Rule]
match: true
category: Test
subcategory: Test
tags: {split(field.first, ",", 0)}, {split(field.second, " ", 1)}
'''
        engine = parse_merchants(content)
        expected = {'{split(field.first, ",", 0)}', '{split(field.second, " ", 1)}'}
        assert engine.rules[0].tags == expected

    def test_tag_only_rule_with_expression(self):
        """Tag-only rule with expression tag."""
        content = '''
[Cardholder Tag]
match: source == "AMEX" and exists(field.cardholder)
tags: {split(field.cardholder, " ", 0)}
'''
        engine = parse_merchants(content)
        assert len(engine.rules) == 1
        assert not engine.rules[0].is_categorization_rule
        assert engine.rules[0].tags == {'{split(field.cardholder, " ", 0)}'}

    def test_simple_tags_still_work(self):
        """Simple comma-separated tags without expressions still work."""
        content = '''
[Simple Tags]
match: true
category: Test
subcategory: Test
tags: tag1, tag2, tag3
'''
        engine = parse_merchants(content)
        assert engine.rules[0].tags == {'tag1', 'tag2', 'tag3'}

    def test_single_tag_no_comma(self):
        """Single tag without commas works."""
        content = '''
[Single Tag]
match: true
category: Test
subcategory: Test
tags: solo
'''
        engine = parse_merchants(content)
        assert engine.rules[0].tags == {'solo'}


class TestFieldTransforms:
    """Tests for field transforms parsing."""

    def test_simple_transform(self):
        """Parse a simple field transform."""
        content = '''
field.description = regex_replace(field.description, "^APLPAY\\s+", "")

[Starbucks]
match: contains("STARBUCKS")
category: Food
subcategory: Coffee
'''
        engine = parse_merchants(content)
        assert len(engine.transforms) == 1
        assert engine.transforms[0][0] == 'field.description'
        assert 'regex_replace' in engine.transforms[0][1]

    def test_multiple_transforms(self):
        """Parse multiple field transforms."""
        content = '''
field.description = regex_replace(field.description, "^APLPAY\\s+", "")
field.description = regex_replace(field.description, "^SQ\\s*\\*", "")
field.memo = trim(field.memo)

[Test]
match: true
category: Test
subcategory: Test
'''
        engine = parse_merchants(content)
        assert len(engine.transforms) == 3
        assert engine.transforms[0][0] == 'field.description'
        assert engine.transforms[1][0] == 'field.description'
        assert engine.transforms[2][0] == 'field.memo'

    def test_transforms_and_variables(self):
        """Transforms and variables can coexist."""
        content = '''
field.description = uppercase(field.description)
is_large = amount > 500

[Large Purchase]
match: is_large
category: Shopping
subcategory: Large
'''
        engine = parse_merchants(content)
        assert len(engine.transforms) == 1
        assert 'is_large' in engine.variables
        assert engine.transforms[0][0] == 'field.description'

    def test_transform_vs_variable_distinction(self):
        """Field transforms start with 'field.', variables don't."""
        content = '''
field.description = lowercase(field.description)
my_var = amount > 100

[Test]
match: my_var
category: Test
subcategory: Test
'''
        engine = parse_merchants(content)
        assert len(engine.transforms) == 1
        assert len(engine.variables) == 1
        assert 'field.description' not in engine.variables
        assert 'my_var' in engine.variables

    def test_transforms_applied_in_order(self):
        """Transforms are stored in order they appear."""
        content = '''
field.description = strip_prefix(field.description, "A")
field.description = strip_prefix(field.description, "B")
field.description = strip_prefix(field.description, "C")

[Test]
match: true
category: Test
subcategory: Test
'''
        engine = parse_merchants(content)
        assert len(engine.transforms) == 3
        assert '"A"' in engine.transforms[0][1]
        assert '"B"' in engine.transforms[1][1]
        assert '"C"' in engine.transforms[2][1]

    def test_transform_with_complex_expression(self):
        """Transforms can have complex expressions."""
        content = '''
field.description = regex_replace(regex_replace(field.description, "^APLPAY\\s+", ""), "^SQ\\*", "")

[Test]
match: true
category: Test
subcategory: Test
'''
        engine = parse_merchants(content)
        assert len(engine.transforms) == 1
        # Should contain nested regex_replace calls
        assert engine.transforms[0][1].count('regex_replace') == 2


class TestApplyTransforms:
    """Tests for apply_transforms function."""

    def test_apply_single_transform(self):
        """Apply a single description transform."""
        from tally.merchant_utils import apply_transforms

        transaction = {'description': 'APLPAY STARBUCKS', 'amount': 5.50}
        transforms = [('field.description', 'regex_replace(field.description, "^APLPAY\\\\s+", "")')]

        result = apply_transforms(transaction, transforms)
        assert result['description'] == 'STARBUCKS'

    def test_apply_transform_preserves_raw(self):
        """Apply transform preserves original in _raw_description."""
        from tally.merchant_utils import apply_transforms

        transaction = {'description': 'APLPAY STARBUCKS', 'amount': 5.50}
        transforms = [('field.description', 'regex_replace(field.description, "^APLPAY\\\\s+", "")')]

        result = apply_transforms(transaction, transforms)
        assert result['_raw_description'] == 'APLPAY STARBUCKS'
        assert result['description'] == 'STARBUCKS'

    def test_apply_chained_transforms(self):
        """Apply multiple transforms in sequence."""
        from tally.merchant_utils import apply_transforms

        transaction = {'description': 'APLPAY SQ* STARBUCKS', 'amount': 5.50}
        transforms = [
            ('field.description', 'regex_replace(field.description, "^APLPAY\\\\s+", "")'),
            ('field.description', 'regex_replace(field.description, "^SQ\\\\*\\\\s*", "")'),
        ]

        result = apply_transforms(transaction, transforms)
        assert result['description'] == 'STARBUCKS'
        # _raw_description should be the original before any transforms
        assert result['_raw_description'] == 'APLPAY SQ* STARBUCKS'

    def test_apply_no_transforms(self):
        """Empty transforms list returns transaction unchanged."""
        from tally.merchant_utils import apply_transforms

        transaction = {'description': 'STARBUCKS', 'amount': 5.50}

        result = apply_transforms(transaction, [])
        assert result['description'] == 'STARBUCKS'
        assert '_raw_description' not in result

    def test_apply_none_transforms(self):
        """None transforms returns transaction unchanged."""
        from tally.merchant_utils import apply_transforms

        transaction = {'description': 'STARBUCKS', 'amount': 5.50}

        result = apply_transforms(transaction, None)
        assert result['description'] == 'STARBUCKS'

    def test_apply_transform_uppercase(self):
        """Apply uppercase transform."""
        from tally.merchant_utils import apply_transforms

        transaction = {'description': 'starbucks coffee', 'amount': 5.50}
        transforms = [('field.description', 'uppercase(field.description)')]

        result = apply_transforms(transaction, transforms)
        assert result['description'] == 'STARBUCKS COFFEE'

    def test_apply_transform_lowercase(self):
        """Apply lowercase transform."""
        from tally.merchant_utils import apply_transforms

        transaction = {'description': 'STARBUCKS COFFEE', 'amount': 5.50}
        transforms = [('field.description', 'lowercase(field.description)')]

        result = apply_transforms(transaction, transforms)
        assert result['description'] == 'starbucks coffee'

    def test_apply_transform_custom_field(self):
        """Apply transform to custom field."""
        from tally.merchant_utils import apply_transforms

        transaction = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'memo': '  trimmed  '}
        }
        transforms = [('field.memo', 'trim(field.memo)')]

        result = apply_transforms(transaction, transforms)
        assert result['field']['memo'] == 'trimmed'
        assert result['_raw_memo'] == '  trimmed  '

    def test_apply_transform_no_match(self):
        """Transform with no match leaves value unchanged."""
        from tally.merchant_utils import apply_transforms

        transaction = {'description': 'STARBUCKS', 'amount': 5.50}
        transforms = [('field.description', 'regex_replace(field.description, "^APLPAY\\\\s+", "")')]

        result = apply_transforms(transaction, transforms)
        assert result['description'] == 'STARBUCKS'  # Unchanged


class TestGetTransforms:
    """Tests for get_transforms function."""

    def test_get_transforms_from_rules_file(self, tmp_path):
        """Get transforms from a .rules file."""
        from tally.merchant_utils import get_transforms

        rules_file = tmp_path / "merchants.rules"
        rules_file.write_text('''
field.description = regex_replace(field.description, "^APLPAY\\s+", "")

[Test]
match: true
category: Test
subcategory: Test
''')

        transforms = get_transforms(str(rules_file))
        assert len(transforms) == 1
        assert transforms[0][0] == 'field.description'

    def test_get_transforms_empty_file(self, tmp_path):
        """Empty rules file returns empty transforms."""
        from tally.merchant_utils import get_transforms

        rules_file = tmp_path / "merchants.rules"
        rules_file.write_text('''
[Test]
match: true
category: Test
subcategory: Test
''')

        transforms = get_transforms(str(rules_file))
        assert transforms == []

    def test_get_transforms_none_path(self):
        """None path returns empty transforms."""
        from tally.merchant_utils import get_transforms

        transforms = get_transforms(None)
        assert transforms == []

    def test_get_transforms_csv_path(self):
        """CSV path (not .rules) returns empty transforms."""
        from tally.merchant_utils import get_transforms

        transforms = get_transforms("/path/to/merchants.csv")
        assert transforms == []


class TestLetBindings:
    """Tests for rule-level let: bindings."""

    def test_parse_let_binding(self):
        """Parse rule with let binding."""
        content = """
[Amazon Verified]
let: matched = amount > 100
match: contains("AMAZON") and matched
category: Shopping
subcategory: Large
"""
        engine = MerchantEngine()
        engine.parse(content)

        assert len(engine.rules) == 1
        rule = engine.rules[0]
        assert len(rule.let_bindings) == 1
        assert rule.let_bindings[0] == ('matched', 'amount > 100')

    def test_parse_multiple_let_bindings(self):
        """Parse rule with multiple let bindings."""
        content = """
[Amazon Verified]
let: is_large = amount > 100
let: is_amazon = contains("AMAZON")
match: is_amazon and is_large
category: Shopping
subcategory: Large
"""
        engine = MerchantEngine()
        engine.parse(content)

        rule = engine.rules[0]
        assert len(rule.let_bindings) == 2
        assert rule.let_bindings[0] == ('is_large', 'amount > 100')
        assert rule.let_bindings[1] == ('is_amazon', 'contains("AMAZON")')

    def test_let_binding_evaluated_in_match(self):
        """Let binding is available in match expression."""
        content = """
[Large Purchase]
let: is_large = amount > 100
match: is_large
category: Shopping
subcategory: Large
"""
        engine = MerchantEngine()
        engine.parse(content)

        # Should match large amounts
        result = engine.match({'description': 'TEST', 'amount': 150.0})
        assert result.matched
        assert result.category == 'Shopping'

        # Should not match small amounts
        result = engine.match({'description': 'TEST', 'amount': 50.0})
        assert not result.matched

    def test_let_binding_chains(self):
        """Later let bindings can reference earlier ones."""
        content = """
[Verified Large]
let: threshold = 100
let: is_large = amount > threshold
match: is_large
category: Shopping
subcategory: Large
"""
        engine = MerchantEngine()
        engine.parse(content)

        result = engine.match({'description': 'TEST', 'amount': 150.0})
        assert result.matched

    def test_let_binding_with_data_sources(self):
        """Let bindings work with supplemental data sources."""
        content = """
[Amazon Verified]
let: orders = [r for r in amazon_orders if r.amount == amount]
let: has_order = len(orders) > 0
match: contains("AMAZON") and has_order
category: Shopping
subcategory: Verified
"""
        engine = MerchantEngine()
        engine.parse(content)

        data_sources = {
            'amazon_orders': [
                {'item': 'Book', 'amount': 45.99},
                {'item': 'Cable', 'amount': 30.00},
            ]
        }

        # Should match when amount matches an order
        result = engine.match(
            {'description': 'AMAZON MARKETPLACE', 'amount': 45.99},
            data_sources=data_sources
        )
        assert result.matched
        assert result.subcategory == 'Verified'

        # Should not match when amount doesn't match
        result = engine.match(
            {'description': 'AMAZON MARKETPLACE', 'amount': 99.99},
            data_sources=data_sources
        )
        assert not result.matched

    def test_invalid_let_syntax_error(self):
        """Invalid let syntax raises error."""
        content = """
[Test]
let: invalid syntax here
match: true
category: Test
subcategory: Test
"""
        engine = MerchantEngine()
        with pytest.raises(MerchantParseError, match="Invalid let syntax"):
            engine.parse(content)

    def test_invalid_let_expression_error(self):
        """Invalid let expression raises error."""
        content = """
[Test]
let: x = import os
match: true
category: Test
subcategory: Test
"""
        engine = MerchantEngine()
        with pytest.raises(MerchantParseError, match="Invalid let expression"):
            engine.parse(content)


class TestFieldDirective:
    """Tests for field: directive to add extra fields to transactions."""

    def test_parse_field_directive(self):
        """Parse rule with field directive."""
        content = """
[Amazon]
match: contains("AMAZON")
category: Shopping
field: item_count = 5
"""
        engine = MerchantEngine()
        engine.parse(content)

        assert len(engine.rules) == 1
        rule = engine.rules[0]
        assert 'item_count' in rule.fields
        assert rule.fields['item_count'] == '5'

    def test_parse_multiple_field_directives(self):
        """Parse rule with multiple field directives."""
        content = """
[Amazon]
match: contains("AMAZON")
category: Shopping
field: items = "test items"
field: order_id = "ABC123"
field: count = 3
"""
        engine = MerchantEngine()
        engine.parse(content)

        rule = engine.rules[0]
        assert len(rule.fields) == 3
        assert 'items' in rule.fields
        assert 'order_id' in rule.fields
        assert 'count' in rule.fields

    def test_field_evaluated_on_match(self):
        """Field expressions are evaluated when rule matches."""
        content = """
[Large Purchase]
match: amount > 100
category: Shopping
field: is_large = true
field: doubled = amount * 2
"""
        engine = MerchantEngine()
        engine.parse(content)

        result = engine.match({'description': 'TEST', 'amount': 150.0})
        assert result.matched
        assert result.extra_fields['is_large'] is True
        assert result.extra_fields['doubled'] == 300.0

    def test_field_with_let_bindings(self):
        """Field can reference let binding variables."""
        content = """
[Amazon]
let: multiplier = 2
match: contains("AMAZON")
category: Shopping
field: calculated = amount * multiplier
"""
        engine = MerchantEngine()
        engine.parse(content)

        result = engine.match({'description': 'AMAZON', 'amount': 50.0})
        assert result.matched
        assert result.extra_fields['calculated'] == 100.0

    def test_field_with_data_sources(self):
        """Field can query supplemental data sources."""
        content = """
[Amazon]
let: orders = [r for r in amazon_orders if r.amount == amount]
match: contains("AMAZON") and len(orders) > 0
category: Shopping
field: items = [r.item for r in orders]
field: order_count = len(orders)
"""
        engine = MerchantEngine()
        engine.parse(content)

        data_sources = {
            'amazon_orders': [
                {'item': 'Book', 'amount': 45.99},
                {'item': 'Cable', 'amount': 45.99},
                {'item': 'Other', 'amount': 30.00},
            ]
        }

        result = engine.match(
            {'description': 'AMAZON MARKETPLACE', 'amount': 45.99},
            data_sources=data_sources
        )
        assert result.matched
        assert result.extra_fields['items'] == ['Book', 'Cable']
        assert result.extra_fields['order_count'] == 2

    def test_field_not_evaluated_when_no_match(self):
        """Fields are not evaluated when rule doesn't match."""
        content = """
[Large Purchase]
match: amount > 100
category: Shopping
field: calculated = amount * 2
"""
        engine = MerchantEngine()
        engine.parse(content)

        result = engine.match({'description': 'TEST', 'amount': 50.0})
        assert not result.matched
        assert result.extra_fields == {}

    def test_invalid_field_syntax_error(self):
        """Invalid field syntax raises error."""
        content = """
[Test]
match: true
category: Test
field: invalid syntax here
"""
        engine = MerchantEngine()
        with pytest.raises(MerchantParseError, match="Invalid field syntax"):
            engine.parse(content)

    def test_invalid_field_expression_error(self):
        """Invalid field expression raises error."""
        content = """
[Test]
match: true
category: Test
field: x = import os
"""
        engine = MerchantEngine()
        with pytest.raises(MerchantParseError, match="Invalid field expression"):
            engine.parse(content)


class TestRuleModes:
    """Tests for rule_mode: first_match vs most_specific."""

    OVERLAPPING_RULES = '''
[General]
match: contains("STORE")
category: Shopping
subcategory: General

[Specific]
match: contains("STORE") and amount > 100
category: Shopping
subcategory: Large Purchase
'''

    def test_first_match_mode_default(self):
        """Default mode is first_match."""
        engine = parse_merchants(self.OVERLAPPING_RULES)
        assert engine.match_mode == 'first_match'

    def test_first_match_mode_explicit(self):
        """Explicit first_match mode works."""
        engine = parse_merchants(self.OVERLAPPING_RULES, match_mode='first_match')
        assert engine.match_mode == 'first_match'

    def test_most_specific_mode_explicit(self):
        """Explicit most_specific mode works."""
        engine = parse_merchants(self.OVERLAPPING_RULES, match_mode='most_specific')
        assert engine.match_mode == 'most_specific'

    def test_first_match_first_rule_wins(self):
        """In first_match mode, first matching rule wins regardless of specificity."""
        engine = parse_merchants(self.OVERLAPPING_RULES, match_mode='first_match')

        # Even with amount > 100, first rule (General) wins
        txn = {'description': 'MY STORE', 'amount': 200.0}
        result = engine.match(txn)

        assert result.subcategory == "General"
        assert result.matched_rule.name == "General"

    def test_most_specific_more_conditions_wins(self):
        """In most_specific mode, rule with more conditions wins."""
        engine = parse_merchants(self.OVERLAPPING_RULES, match_mode='most_specific')

        # With amount > 100, more specific rule (Specific) wins
        txn = {'description': 'MY STORE', 'amount': 200.0}
        result = engine.match(txn)

        assert result.subcategory == "Large Purchase"
        assert result.matched_rule.name == "Specific"

    def test_both_modes_collect_all_tags(self):
        """Both modes collect tags from ALL matching rules."""
        content = '''
[General]
match: contains("STORE")
category: Shopping
tags: retail

[Large]
match: amount > 100
tags: large

[Expensive]
match: amount > 500
tags: expensive
'''
        txn = {'description': 'MY STORE', 'amount': 600.0}

        # Test first_match mode
        engine1 = parse_merchants(content, match_mode='first_match')
        result1 = engine1.match(txn)
        assert result1.tags == {"retail", "large", "expensive"}

        # Test most_specific mode
        engine2 = parse_merchants(content, match_mode='most_specific')
        result2 = engine2.match(txn)
        assert result2.tags == {"retail", "large", "expensive"}

    def test_first_match_order_matters(self):
        """In first_match mode, rule order determines winner."""
        # Rules in different order
        content_specific_first = '''
[Uber Eats]
match: contains("UBER") and contains("EATS")
category: Food

[Uber]
match: contains("UBER")
category: Transport
'''
        content_general_first = '''
[Uber]
match: contains("UBER")
category: Transport

[Uber Eats]
match: contains("UBER") and contains("EATS")
category: Food
'''
        txn = {'description': 'UBER EATS ORDER', 'amount': 25.0}

        # Specific rule first -> Food wins
        engine1 = parse_merchants(content_specific_first, match_mode='first_match')
        assert engine1.match(txn).category == "Food"

        # General rule first -> Transport wins
        engine2 = parse_merchants(content_general_first, match_mode='first_match')
        assert engine2.match(txn).category == "Transport"

    def test_most_specific_order_doesnt_matter(self):
        """In most_specific mode, order doesn't affect category."""
        content_specific_first = '''
[Uber Eats]
match: contains("UBER") and contains("EATS")
category: Food

[Uber]
match: contains("UBER")
category: Transport
'''
        content_general_first = '''
[Uber]
match: contains("UBER")
category: Transport

[Uber Eats]
match: contains("UBER") and contains("EATS")
category: Food
'''
        txn = {'description': 'UBER EATS ORDER', 'amount': 25.0}

        # Specific rule first -> Food wins (more conditions)
        engine1 = parse_merchants(content_specific_first, match_mode='most_specific')
        assert engine1.match(txn).category == "Food"

        # General rule first -> Food still wins (more conditions)
        engine2 = parse_merchants(content_general_first, match_mode='most_specific')
        assert engine2.match(txn).category == "Food"


