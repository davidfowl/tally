"""Tests for transaction-level expression matching."""

import pytest
from datetime import date
from tally.expr_parser import (
    TransactionContext,
    TransactionEvaluator,
    matches_transaction,
    evaluate_transaction,
    parse_expression,
    ExpressionError,
)


class TestTransactionContext:
    """Tests for TransactionContext creation and properties."""

    def test_basic_context(self):
        """Context stores basic properties."""
        ctx = TransactionContext(
            description="NETFLIX STREAMING",
            amount=15.99,
            date=date(2025, 1, 15),
        )
        assert ctx.description == "NETFLIX STREAMING"
        assert ctx.amount == 15.99
        assert ctx.month == 1
        assert ctx.year == 2025
        assert ctx.day == 15

    def test_amount_preserves_sign(self):
        """Amount preserves its sign (use abs(amount) in rules for magnitude)."""
        ctx = TransactionContext(amount=-99.50)
        assert ctx.amount == -99.50

        ctx_pos = TransactionContext(amount=99.50)
        assert ctx_pos.amount == 99.50

    def test_from_transaction_dict(self):
        """Create context from transaction dictionary."""
        txn = {
            'description': 'AMAZON PURCHASE',
            'amount': -45.00,
            'date': date(2025, 12, 25),
        }
        ctx = TransactionContext.from_transaction(txn)
        assert ctx.description == 'AMAZON PURCHASE'
        assert ctx.amount == -45.00  # Sign preserved
        assert ctx.month == 12
        assert ctx.year == 2025
        assert ctx.day == 25

    def test_from_transaction_with_raw_description(self):
        """Falls back to raw_description if description not present."""
        txn = {
            'raw_description': 'RAW DESC',
            'amount': 10.00,
        }
        ctx = TransactionContext.from_transaction(txn)
        assert ctx.description == 'RAW DESC'

    def test_no_date(self):
        """Date components are 0 when no date provided."""
        ctx = TransactionContext(description="TEST")
        assert ctx.month == 0
        assert ctx.year == 0
        assert ctx.day == 0


class TestContainsFunction:
    """Tests for the contains() function."""

    def test_contains_match(self):
        """contains() finds substring."""
        txn = {'description': 'NETFLIX.COM STREAMING', 'amount': 15.99}
        assert matches_transaction('contains("NETFLIX")', txn)
        assert matches_transaction('contains("netflix")', txn)  # case insensitive
        assert matches_transaction('contains("STREAMING")', txn)

    def test_contains_no_match(self):
        """contains() returns False when not found."""
        txn = {'description': 'AMAZON PURCHASE', 'amount': 45.00}
        assert not matches_transaction('contains("NETFLIX")', txn)

    def test_contains_with_and(self):
        """contains() works with boolean AND."""
        txn = {'description': 'UBER EATS ORDER', 'amount': 25.00}
        assert matches_transaction('contains("UBER") and contains("EATS")', txn)
        assert not matches_transaction('contains("UBER") and contains("RIDES")', txn)

    def test_contains_with_not(self):
        """contains() works with NOT."""
        txn = {'description': 'UBER RIDES', 'amount': 15.00}
        assert matches_transaction('contains("UBER") and not contains("EATS")', txn)


class TestRegexFunction:
    """Tests for the regex() function."""

    def test_regex_simple(self):
        """Basic regex matching."""
        txn = {'description': 'NETFLIX.COM', 'amount': 15.99}
        assert matches_transaction('regex("NETFLIX")', txn)
        assert matches_transaction('regex("NET.*COM")', txn)

    def test_regex_negative_lookahead(self):
        """Regex with negative lookahead for Uber vs Uber Eats."""
        uber_rides = {'description': 'UBER TRIP', 'amount': 25.00}
        uber_eats = {'description': 'UBER EATS ORDER', 'amount': 30.00}

        # Match Uber but exclude if EATS appears anywhere
        expr = r'regex("UBER(?!.*EATS)")'
        assert matches_transaction(expr, uber_rides)
        assert not matches_transaction(expr, uber_eats)

    def test_regex_case_insensitive(self):
        """Regex is case insensitive."""
        txn = {'description': 'Netflix Streaming', 'amount': 15.99}
        assert matches_transaction('regex("NETFLIX")', txn)
        assert matches_transaction('regex("netflix")', txn)

    def test_regex_invalid_pattern(self):
        """Invalid regex raises error."""
        txn = {'description': 'TEST', 'amount': 10.00}
        with pytest.raises(ExpressionError, match="Invalid regex pattern"):
            matches_transaction('regex("[invalid")', txn)


class TestAmountConditions:
    """Tests for amount-based conditions."""

    def test_amount_greater_than(self):
        """amount > threshold."""
        txn = {'description': 'PURCHASE', 'amount': 150.00}
        assert matches_transaction('amount > 100', txn)
        assert not matches_transaction('amount > 200', txn)

    def test_amount_less_than(self):
        """amount < threshold."""
        txn = {'description': 'PURCHASE', 'amount': 50.00}
        assert matches_transaction('amount < 100', txn)
        assert not matches_transaction('amount < 25', txn)

    def test_amount_range(self):
        """amount in range."""
        txn = {'description': 'PURCHASE', 'amount': 75.00}
        assert matches_transaction('amount >= 50 and amount <= 100', txn)
        assert not matches_transaction('amount >= 100 and amount <= 200', txn)

    def test_negative_amount_preserved(self):
        """Negative amounts are preserved for matching."""
        txn = {'description': 'REFUND', 'amount': -150.00}
        assert matches_transaction('amount < 0', txn)
        assert matches_transaction('amount == -150', txn)
        assert not matches_transaction('amount > 0', txn)

        # Use abs() for magnitude-only matching
        assert matches_transaction('abs(amount) > 100', txn)
        assert matches_transaction('abs(amount) == 150', txn)


class TestDateConditions:
    """Tests for date-based conditions."""

    def test_month_equals(self):
        """month == number."""
        txn = {'description': 'PURCHASE', 'amount': 100, 'date': date(2025, 12, 15)}
        assert matches_transaction('month == 12', txn)
        assert not matches_transaction('month == 1', txn)

    def test_year_equals(self):
        """year == number."""
        txn = {'description': 'PURCHASE', 'amount': 100, 'date': date(2025, 6, 1)}
        assert matches_transaction('year == 2025', txn)
        assert not matches_transaction('year == 2024', txn)

    def test_day_equals(self):
        """day == number."""
        txn = {'description': 'PURCHASE', 'amount': 100, 'date': date(2025, 1, 25)}
        assert matches_transaction('day == 25', txn)

    def test_date_comparison(self):
        """date >= "YYYY-MM-DD" comparison."""
        txn = {'description': 'PURCHASE', 'amount': 100, 'date': date(2025, 6, 15)}
        assert matches_transaction('date >= "2025-01-01"', txn)
        assert matches_transaction('date <= "2025-12-31"', txn)
        assert not matches_transaction('date < "2025-06-01"', txn)

    def test_date_range(self):
        """Date range for Black Friday example."""
        black_friday = {'description': 'BESTBUY', 'amount': 500, 'date': date(2025, 11, 29)}
        regular_day = {'description': 'BESTBUY', 'amount': 500, 'date': date(2025, 7, 15)}

        expr = 'date >= "2025-11-28" and date <= "2025-11-30"'
        assert matches_transaction(expr, black_friday)
        assert not matches_transaction(expr, regular_day)

    def test_invalid_date_format(self):
        """Invalid date format raises error."""
        txn = {'description': 'TEST', 'amount': 10, 'date': date(2025, 1, 1)}
        with pytest.raises(ExpressionError, match="Invalid date format"):
            matches_transaction('date >= "01/01/2025"', txn)


class TestCombinedExpressions:
    """Tests for complex combined expressions."""

    def test_contains_and_amount(self):
        """Pattern + amount condition."""
        small_costco = {'description': 'COSTCO #123', 'amount': 75.00}
        large_costco = {'description': 'COSTCO #123', 'amount': 250.00}

        expr = 'contains("COSTCO") and amount > 200'
        assert not matches_transaction(expr, small_costco)
        assert matches_transaction(expr, large_costco)

    def test_pattern_month_amount(self):
        """Pattern + month + amount for holiday gifts."""
        holiday_gift = {'description': 'AMAZON', 'amount': 150, 'date': date(2025, 12, 10)}
        regular = {'description': 'AMAZON', 'amount': 150, 'date': date(2025, 6, 10)}
        small_holiday = {'description': 'AMAZON', 'amount': 25, 'date': date(2025, 12, 10)}

        expr = 'contains("AMAZON") and month == 12 and amount > 100'
        assert matches_transaction(expr, holiday_gift)
        assert not matches_transaction(expr, regular)
        assert not matches_transaction(expr, small_holiday)

    def test_or_conditions(self):
        """OR conditions."""
        netflix = {'description': 'NETFLIX', 'amount': 15.99}
        spotify = {'description': 'SPOTIFY', 'amount': 9.99}
        amazon = {'description': 'AMAZON', 'amount': 45.00}

        expr = 'contains("NETFLIX") or contains("SPOTIFY")'
        assert matches_transaction(expr, netflix)
        assert matches_transaction(expr, spotify)
        assert not matches_transaction(expr, amazon)


class TestVariables:
    """Tests for user-defined variables."""

    def test_variable_in_expression(self):
        """Variables can be used in expressions."""
        txn = {'description': 'PURCHASE', 'amount': 600}
        variables = {'is_large': True, 'threshold': 500}

        # Using variable as condition
        assert matches_transaction('is_large', txn, variables)
        assert matches_transaction('amount > threshold', txn, variables)

    def test_computed_variable(self):
        """Pre-computed variable values."""
        txn = {'description': 'AMAZON', 'amount': 150, 'date': date(2025, 12, 1)}
        # Simulate pre-computed: is_holiday_season = month >= 11 and month <= 12
        variables = {'is_holiday_season': True}

        expr = 'contains("AMAZON") and is_holiday_season'
        assert matches_transaction(expr, txn, variables)


class TestInOperator:
    """Tests for the 'in' operator with strings."""

    def test_string_in_description(self):
        """'X' in description (case insensitive)."""
        txn = {'description': 'NETFLIX STREAMING SERVICE', 'amount': 15.99}
        assert matches_transaction('"NETFLIX" in description', txn)
        assert matches_transaction('"netflix" in description', txn)
        assert matches_transaction('"STREAMING" in description', txn)
        assert not matches_transaction('"AMAZON" in description', txn)

    def test_not_in_description(self):
        """'X' not in description."""
        txn = {'description': 'UBER RIDES', 'amount': 25.00}
        assert matches_transaction('"EATS" not in description', txn)
        assert not matches_transaction('"UBER" not in description', txn)


class TestNormalizedFunction:
    """Tests for the normalized() function."""

    def test_normalized_ignores_spaces(self):
        """normalized() matches ignoring spaces."""
        txn = {'description': 'UBER EATS ORDER', 'amount': 25.00}
        assert matches_transaction('normalized("UBEREATS")', txn)
        assert matches_transaction('normalized("UBER EATS")', txn)

    def test_normalized_ignores_hyphens(self):
        """normalized() matches ignoring hyphens."""
        txn = {'description': 'COCA-COLA PURCHASE', 'amount': 5.00}
        assert matches_transaction('normalized("COCACOLA")', txn)
        assert matches_transaction('normalized("COCA-COLA")', txn)
        assert matches_transaction('normalized("COCA COLA")', txn)

    def test_normalized_ignores_apostrophes(self):
        """normalized() matches ignoring apostrophes."""
        txn = {'description': "MCDONALD'S RESTAURANT", 'amount': 12.00}
        assert matches_transaction('normalized("MCDONALDS")', txn)
        assert matches_transaction("normalized(\"MCDONALD'S\")", txn)

    def test_normalized_ignores_periods(self):
        """normalized() matches ignoring periods."""
        txn = {'description': 'NETFLIX.COM STREAMING', 'amount': 15.99}
        assert matches_transaction('normalized("NETFLIXCOM")', txn)
        assert matches_transaction('normalized("NETFLIX.COM")', txn)

    def test_normalized_case_insensitive(self):
        """normalized() is case insensitive."""
        txn = {'description': 'Uber Eats', 'amount': 25.00}
        assert matches_transaction('normalized("ubereats")', txn)
        assert matches_transaction('normalized("UBEREATS")', txn)

    def test_normalized_no_match(self):
        """normalized() returns False when pattern not found."""
        txn = {'description': 'AMAZON PURCHASE', 'amount': 45.00}
        assert not matches_transaction('normalized("UBEREATS")', txn)


class TestAnyofFunction:
    """Tests for the anyof() function."""

    def test_anyof_first_match(self):
        """anyof() matches first pattern."""
        txn = {'description': 'NETFLIX STREAMING', 'amount': 15.99}
        assert matches_transaction('anyof("NETFLIX", "HULU", "DISNEY")', txn)

    def test_anyof_middle_match(self):
        """anyof() matches middle pattern."""
        txn = {'description': 'HULU SUBSCRIPTION', 'amount': 12.99}
        assert matches_transaction('anyof("NETFLIX", "HULU", "DISNEY")', txn)

    def test_anyof_last_match(self):
        """anyof() matches last pattern."""
        txn = {'description': 'DISNEY PLUS', 'amount': 9.99}
        assert matches_transaction('anyof("NETFLIX", "HULU", "DISNEY")', txn)

    def test_anyof_no_match(self):
        """anyof() returns False when no pattern matches."""
        txn = {'description': 'AMAZON PRIME', 'amount': 14.99}
        assert not matches_transaction('anyof("NETFLIX", "HULU", "DISNEY")', txn)

    def test_anyof_case_insensitive(self):
        """anyof() is case insensitive."""
        txn = {'description': 'Netflix Streaming', 'amount': 15.99}
        assert matches_transaction('anyof("NETFLIX", "HULU")', txn)
        assert matches_transaction('anyof("netflix", "hulu")', txn)

    def test_anyof_two_patterns(self):
        """anyof() works with two patterns."""
        uber = {'description': 'UBER RIDES', 'amount': 20.00}
        lyft = {'description': 'LYFT RIDE', 'amount': 18.00}
        taxi = {'description': 'YELLOW CAB', 'amount': 25.00}

        expr = 'anyof("UBER", "LYFT")'
        assert matches_transaction(expr, uber)
        assert matches_transaction(expr, lyft)
        assert not matches_transaction(expr, taxi)


class TestStartswithFunction:
    """Tests for the startswith() function."""

    def test_startswith_match(self):
        """startswith() matches at beginning."""
        txn = {'description': 'AMAZON MARKETPLACE', 'amount': 45.00}
        assert matches_transaction('startswith("AMAZON")', txn)

    def test_startswith_no_match_middle(self):
        """startswith() doesn't match in middle."""
        txn = {'description': 'BUY AMAZON GIFT CARD', 'amount': 50.00}
        assert not matches_transaction('startswith("AMAZON")', txn)

    def test_startswith_case_insensitive(self):
        """startswith() is case insensitive."""
        txn = {'description': 'Amazon Purchase', 'amount': 45.00}
        assert matches_transaction('startswith("AMAZON")', txn)
        assert matches_transaction('startswith("amazon")', txn)

    def test_startswith_vs_contains(self):
        """startswith() is stricter than contains()."""
        txn = {'description': 'APPLE PAY AMAZON', 'amount': 30.00}
        # contains would match, startswith should not
        assert matches_transaction('contains("AMAZON")', txn)
        assert not matches_transaction('startswith("AMAZON")', txn)
        # But APPLE PAY should match startswith
        assert matches_transaction('startswith("APPLE")', txn)


class TestFuzzyFunction:
    """Tests for the fuzzy() function."""

    def test_fuzzy_exact_match(self):
        """fuzzy() matches exact strings."""
        txn = {'description': 'MARKETPLACE PURCHASE', 'amount': 50.00}
        assert matches_transaction('fuzzy("MARKETPLACE")', txn)

    def test_fuzzy_catches_typo(self):
        """fuzzy() catches common typos."""
        # Missing letter
        txn = {'description': 'MARKEPLACE ORDER', 'amount': 50.00}  # missing 'T'
        assert matches_transaction('fuzzy("MARKETPLACE")', txn)

    def test_fuzzy_catches_transposition(self):
        """fuzzy() catches letter transpositions."""
        txn = {'description': 'AMZAON PURCHASE', 'amount': 45.00}  # transposed Z and A
        assert matches_transaction('fuzzy("AMAZON")', txn)

    def test_fuzzy_no_match_very_different(self):
        """fuzzy() doesn't match very different strings."""
        txn = {'description': 'NETFLIX STREAMING', 'amount': 15.99}
        assert not matches_transaction('fuzzy("AMAZON")', txn)

    def test_fuzzy_custom_threshold(self):
        """fuzzy() respects custom threshold."""
        # MARKEPLACE (missing T) is 95% similar to MARKETPLACE
        txn = {'description': 'MARKEPLACE ORDER', 'amount': 50.00}
        # Default threshold (0.80) should match
        assert matches_transaction('fuzzy("MARKETPLACE")', txn)
        # Higher threshold should still match (95% similar)
        assert matches_transaction('fuzzy("MARKETPLACE", 0.90)', txn)
        # Very high threshold won't match
        assert not matches_transaction('fuzzy("MARKETPLACE", 0.99)', txn)

    def test_fuzzy_case_insensitive(self):
        """fuzzy() is case insensitive."""
        txn = {'description': 'Amazon Purchase', 'amount': 45.00}
        assert matches_transaction('fuzzy("AMAZON")', txn)
        assert matches_transaction('fuzzy("amazon")', txn)

    def test_fuzzy_in_longer_description(self):
        """fuzzy() finds match within longer description."""
        txn = {'description': 'PAYMENT TO AMZAON SERVICES', 'amount': 100.00}
        assert matches_transaction('fuzzy("AMAZON")', txn)


# =============================================================================
# Custom Field Access Tests
# =============================================================================

class TestFieldAccess:
    """Tests for field.name attribute access."""

    def test_field_access_basic(self):
        """Basic field access works."""
        txn = {
            'description': 'BANK WIRE',
            'amount': 1000.00,
            'field': {'txn_type': 'WIRE', 'memo': 'Payment to vendor'}
        }
        assert matches_transaction('field.txn_type == "WIRE"', txn)
        assert matches_transaction('field.memo == "Payment to vendor"', txn)

    def test_field_access_case_insensitive_comparison(self):
        """Field comparisons are case insensitive for strings."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'code': 'ACH'}
        }
        assert matches_transaction('field.code == "ach"', txn)
        assert matches_transaction('field.code == "ACH"', txn)

    def test_field_access_with_contains(self):
        """Field value can be used with matching functions."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'memo': 'REF:12345 PAYROLL'}
        }
        assert matches_transaction('contains(field.memo, "PAYROLL")', txn)
        assert matches_transaction('contains(field.memo, "REF")', txn)
        assert not matches_transaction('contains(field.memo, "WIRE")', txn)

    def test_field_access_missing_field_raises_error(self):
        """Accessing nonexistent field raises error with helpful message."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'txn_type': 'ACH'}
        }
        with pytest.raises(ExpressionError, match="Unknown field: field.missing"):
            matches_transaction('field.missing == "X"', txn)
        # Also verify it shows available fields (includes built-ins + custom fields)
        with pytest.raises(ExpressionError, match="txn_type"):
            matches_transaction('field.missing == "X"', txn)

    def test_field_access_no_field_dict_raises_error(self):
        """Accessing non-builtin field when no field dict exists raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="Unknown field: field.txn_type"):
            matches_transaction('field.txn_type == "WIRE"', txn)

    def test_field_access_empty_field_dict(self):
        """Empty field dict still raises error for non-builtin field access."""
        txn = {'description': 'TEST', 'amount': 100.00, 'field': {}}
        with pytest.raises(ExpressionError, match="Unknown field: field.code"):
            matches_transaction('field.code == "X"', txn)

    def test_field_access_with_and_or(self):
        """Field access works with boolean operators."""
        txn = {
            'description': 'BANK WIRE',
            'amount': 1000.00,
            'field': {'txn_type': 'WIRE', 'direction': 'OUT'}
        }
        assert matches_transaction('field.txn_type == "WIRE" and field.direction == "OUT"', txn)
        assert matches_transaction('field.txn_type == "ACH" or field.direction == "OUT"', txn)
        assert not matches_transaction('field.txn_type == "ACH" and field.direction == "IN"', txn)


class TestExistsFunction:
    """Tests for the exists() function."""

    def test_exists_field_present_nonempty(self):
        """exists() returns True for non-empty field."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'txn_type': 'WIRE', 'memo': 'Something'}
        }
        assert matches_transaction('exists(field.txn_type)', txn)
        assert matches_transaction('exists(field.memo)', txn)

    def test_exists_field_empty_string(self):
        """exists() returns False for empty string field."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'txn_type': 'WIRE', 'memo': ''}
        }
        assert matches_transaction('exists(field.txn_type)', txn)
        assert not matches_transaction('exists(field.memo)', txn)

    def test_exists_field_whitespace_only(self):
        """exists() returns False for whitespace-only field."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'memo': '   '}
        }
        assert not matches_transaction('exists(field.memo)', txn)

    def test_exists_missing_field(self):
        """exists() returns False for missing field (no error)."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'txn_type': 'WIRE'}
        }
        # This should NOT raise an error, but return False
        assert not matches_transaction('exists(field.nonexistent)', txn)

    def test_exists_no_field_dict(self):
        """exists() returns False when no field dict (no error)."""
        txn = {'description': 'TEST', 'amount': 100.00}
        assert not matches_transaction('exists(field.anything)', txn)

    def test_exists_with_and(self):
        """exists() can be used with AND to guard field access."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'txn_type': 'WIRE'}
        }
        # Safe pattern: check exists before accessing
        assert matches_transaction('exists(field.txn_type) and field.txn_type == "WIRE"', txn)
        # Short-circuit: if exists is False, second part is not evaluated
        assert not matches_transaction('exists(field.missing) and field.missing == "X"', txn)

    def test_exists_wrong_arg_count(self):
        """exists() with wrong number of arguments raises error."""
        txn = {'description': 'TEST', 'amount': 100.00, 'field': {'a': '1'}}
        with pytest.raises(ExpressionError, match="exists\\(\\) requires exactly 1 argument"):
            matches_transaction('exists(field.a, "extra")', txn)


# =============================================================================
# Matching Functions with Text Argument Tests
# =============================================================================

class TestMatchingFunctionsWithText:
    """Tests for matching functions accepting optional text argument."""

    def test_contains_with_field(self):
        """contains() with custom field as first argument."""
        txn = {
            'description': 'AMAZON PURCHASE',
            'amount': 45.00,
            'field': {'memo': 'Order #12345 - REF:ABC'}
        }
        # Search in field instead of description
        assert matches_transaction('contains(field.memo, "REF")', txn)
        assert matches_transaction('contains(field.memo, "Order")', txn)
        assert not matches_transaction('contains(field.memo, "AMAZON")', txn)

    def test_regex_with_field(self):
        """regex() with custom field as first argument."""
        txn = {
            'description': 'BANK WIRE',
            'amount': 1000.00,
            'field': {'code': 'ACH-OUT-12345'}
        }
        assert matches_transaction('regex(field.code, "^ACH-")', txn)
        assert matches_transaction(r'regex(field.code, "\\d{5}$")', txn)
        assert not matches_transaction('regex(field.code, "^WIRE")', txn)

    def test_normalized_with_field(self):
        """normalized() with custom field as first argument."""
        txn = {
            'description': 'BANK WIRE',
            'amount': 1000.00,
            'field': {'vendor': 'WHOLE-FOODS MARKET'}
        }
        assert matches_transaction('normalized(field.vendor, "WHOLEFOODS")', txn)
        assert matches_transaction('normalized(field.vendor, "WHOLE FOODS")', txn)

    def test_startswith_with_field(self):
        """startswith() with custom field as first argument."""
        txn = {
            'description': 'BANK WIRE',
            'amount': 1000.00,
            'field': {'vendor': 'COSTCO WHOLESALE'}
        }
        assert matches_transaction('startswith(field.vendor, "COSTCO")', txn)
        assert not matches_transaction('startswith(field.vendor, "WHOLESALE")', txn)

    def test_fuzzy_with_field(self):
        """fuzzy() with custom field as first argument."""
        txn = {
            'description': 'PAYMENT',
            'amount': 50.00,
            'field': {'vendor': 'STARBCKS COFFEE'}  # typo
        }
        assert matches_transaction('fuzzy(field.vendor, "STARBUCKS")', txn)

    def test_fuzzy_with_field_and_threshold(self):
        """fuzzy() with field and custom threshold."""
        txn = {
            'description': 'PAYMENT',
            'amount': 50.00,
            'field': {'vendor': 'STAR COFFEE'}  # very different
        }
        # Default threshold won't match
        assert not matches_transaction('fuzzy(field.vendor, "STARBUCKS")', txn)
        # Very low threshold might match
        assert matches_transaction('fuzzy(field.vendor, "STARBUCKS", 0.3)', txn)

    def test_contains_wrong_args(self):
        """contains() with wrong number of arguments raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="contains\\(\\) requires 1 or 2 arguments"):
            matches_transaction('contains("a", "b", "c")', txn)

    def test_regex_wrong_args(self):
        """regex() with wrong number of arguments raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="regex\\(\\) requires 1 or 2 arguments"):
            matches_transaction('regex("a", "b", "c")', txn)


# =============================================================================
# Extraction Function Tests
# =============================================================================

class TestExtractFunction:
    """Tests for the extract() function."""

    def test_extract_from_description(self):
        """extract() captures from description."""
        txn = {'description': 'WIRE REF:98765 TO ACME', 'amount': 1000.00}
        # Extract the reference number
        result = evaluate_transaction(r'extract("REF:(\\d+)")', txn)
        assert result == '98765'

    def test_extract_from_field(self):
        """extract() captures from custom field."""
        txn = {
            'description': 'WIRE',
            'amount': 1000.00,
            'field': {'memo': 'CHK#12345 PAYROLL'}
        }
        result = evaluate_transaction(r'extract(field.memo, "#(\\d+)")', txn)
        assert result == '12345'

    def test_extract_no_match(self):
        """extract() returns empty string when no match."""
        txn = {'description': 'AMAZON PURCHASE', 'amount': 45.00}
        result = evaluate_transaction(r'extract("REF:(\\d+)")', txn)
        assert result == ''

    def test_extract_no_capture_group(self):
        """extract() returns empty string when pattern has no capture group."""
        txn = {'description': 'WIRE REF:98765', 'amount': 1000.00}
        result = evaluate_transaction(r'extract("REF:\\d+")', txn)
        assert result == ''

    def test_extract_first_group_only(self):
        """extract() returns only the first capture group."""
        txn = {'description': 'ORDER-ABC-12345', 'amount': 100.00}
        result = evaluate_transaction(r'extract("ORDER-(\\w+)-(\\d+)")', txn)
        assert result == 'ABC'  # First group only

    def test_extract_in_condition(self):
        """extract() can be used in conditions."""
        txn = {'description': 'WIRE REF:98765', 'amount': 1000.00}
        assert matches_transaction(r'extract("REF:(\\d+)") == "98765"', txn)
        assert matches_transaction(r'extract("REF:(\\d+)") != ""', txn)

    def test_extract_case_insensitive(self):
        """extract() is case insensitive."""
        txn = {'description': 'wire ref:98765', 'amount': 1000.00}
        result = evaluate_transaction(r'extract("REF:(\\d+)")', txn)
        assert result == '98765'

    def test_extract_invalid_regex(self):
        """extract() with invalid regex raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="Invalid regex pattern"):
            evaluate_transaction(r'extract("[invalid")', txn)

    def test_extract_wrong_args(self):
        """extract() with wrong number of arguments raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="extract\\(\\) requires 1 or 2 arguments"):
            evaluate_transaction('extract("a", "b", "c")', txn)


class TestSplitFunction:
    """Tests for the split() function."""

    def test_split_from_description(self):
        """split() splits description and returns element."""
        txn = {'description': 'ACH-CREDIT-PAYROLL', 'amount': 1000.00}
        assert evaluate_transaction('split("-", 0)', txn) == 'ACH'
        assert evaluate_transaction('split("-", 1)', txn) == 'CREDIT'
        assert evaluate_transaction('split("-", 2)', txn) == 'PAYROLL'

    def test_split_from_field(self):
        """split() splits custom field."""
        txn = {
            'description': 'WIRE',
            'amount': 1000.00,
            'field': {'code': 'WIR-OUT-12345'}
        }
        assert evaluate_transaction('split(field.code, "-", 0)', txn) == 'WIR'
        assert evaluate_transaction('split(field.code, "-", 1)', txn) == 'OUT'
        assert evaluate_transaction('split(field.code, "-", 2)', txn) == '12345'

    def test_split_out_of_bounds(self):
        """split() returns empty string for out of bounds index."""
        txn = {'description': 'A-B-C', 'amount': 100.00}
        assert evaluate_transaction('split("-", 10)', txn) == ''
        assert evaluate_transaction('split("-", -1)', txn) == ''

    def test_split_in_condition(self):
        """split() can be used in conditions."""
        txn = {'description': 'ACH-CREDIT-PAYROLL', 'amount': 1000.00}
        assert matches_transaction('split("-", 0) == "ACH"', txn)
        assert matches_transaction('split("-", 1) == "CREDIT"', txn)

    def test_split_strips_whitespace(self):
        """split() strips whitespace from results."""
        txn = {'description': 'A - B - C', 'amount': 100.00}
        assert evaluate_transaction('split("-", 1)', txn) == 'B'

    def test_split_no_delimiter(self):
        """split() with non-matching delimiter returns full string at index 0."""
        txn = {'description': 'NODASHES', 'amount': 100.00}
        assert evaluate_transaction('split("-", 0)', txn) == 'NODASHES'
        assert evaluate_transaction('split("-", 1)', txn) == ''

    def test_split_non_integer_index(self):
        """split() with non-integer index raises error."""
        txn = {'description': 'A-B-C', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="index must be an integer"):
            evaluate_transaction('split("-", "0")', txn)

    def test_split_wrong_args(self):
        """split() with wrong number of arguments raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="split\\(\\) requires 2 or 3 arguments"):
            evaluate_transaction('split("-")', txn)


class TestSubstringFunction:
    """Tests for the substring() function."""

    def test_substring_from_description(self):
        """substring() extracts from description."""
        txn = {'description': 'AMZN*MARKETPLACE', 'amount': 45.00}
        assert evaluate_transaction('substring(0, 4)', txn) == 'AMZN'
        assert evaluate_transaction('substring(5, 16)', txn) == 'MARKETPLACE'

    def test_substring_from_field(self):
        """substring() extracts from custom field."""
        txn = {
            'description': 'WIRE',
            'amount': 1000.00,
            'field': {'code': 'WIRE12345'}
        }
        assert evaluate_transaction('substring(field.code, 0, 4)', txn) == 'WIRE'
        assert evaluate_transaction('substring(field.code, 4, 9)', txn) == '12345'

    def test_substring_beyond_length(self):
        """substring() handles end beyond string length gracefully."""
        txn = {'description': 'SHORT', 'amount': 100.00}
        assert evaluate_transaction('substring(0, 100)', txn) == 'SHORT'

    def test_substring_in_condition(self):
        """substring() can be used in conditions."""
        txn = {'description': 'AMZN*MARKETPLACE', 'amount': 45.00}
        assert matches_transaction('substring(0, 4) == "AMZN"', txn)

    def test_substring_non_integer_args(self):
        """substring() with non-integer args raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="start and end must be integers"):
            evaluate_transaction('substring("0", 4)', txn)

    def test_substring_wrong_args(self):
        """substring() with wrong number of arguments raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="substring\\(\\) requires 2 or 3 arguments"):
            evaluate_transaction('substring(0)', txn)


class TestTrimFunction:
    """Tests for the trim() function."""

    def test_trim_description(self):
        """trim() with no args trims description."""
        txn = {'description': '  AMAZON  ', 'amount': 45.00}
        assert evaluate_transaction('trim()', txn) == 'AMAZON'

    def test_trim_field(self):
        """trim() with field argument trims field value."""
        txn = {
            'description': 'WIRE',
            'amount': 1000.00,
            'field': {'memo': '  PAYROLL  '}
        }
        assert evaluate_transaction('trim(field.memo)', txn) == 'PAYROLL'

    def test_trim_already_trimmed(self):
        """trim() on already trimmed string returns same string."""
        txn = {'description': 'AMAZON', 'amount': 45.00}
        assert evaluate_transaction('trim()', txn) == 'AMAZON'

    def test_trim_in_condition(self):
        """trim() can be used in conditions."""
        txn = {'description': '  AMAZON  ', 'amount': 45.00}
        assert matches_transaction('trim() == "AMAZON"', txn)

    def test_trim_empty_string(self):
        """trim() on empty or whitespace-only returns empty."""
        txn = {'description': '   ', 'amount': 100.00}
        assert evaluate_transaction('trim()', txn) == ''

    def test_trim_wrong_args(self):
        """trim() with too many arguments raises error."""
        txn = {'description': 'TEST', 'amount': 100.00}
        with pytest.raises(ExpressionError, match="trim\\(\\) requires 0 or 1 arguments"):
            evaluate_transaction('trim("a", "b")', txn)


# =============================================================================
# Integration and Edge Case Tests
# =============================================================================

class TestFieldAndFunctionIntegration:
    """Tests combining field access with extraction functions."""

    def test_extract_from_field_in_condition(self):
        """Complex expression: extract from field in condition."""
        txn = {
            'description': 'WIRE TRANSFER',
            'amount': 5000.00,
            'field': {'memo': 'REF:ABC123 INVOICE #456'}
        }
        assert matches_transaction(r'extract(field.memo, "REF:(\\w+)") == "ABC123"', txn)
        assert matches_transaction(r'extract(field.memo, "#(\\d+)") == "456"', txn)

    def test_split_field_in_condition(self):
        """Complex expression: split field in condition."""
        txn = {
            'description': 'PAYMENT',
            'amount': 1000.00,
            'field': {'code': 'ACH-OUT-VENDOR'}
        }
        assert matches_transaction('split(field.code, "-", 0) == "ACH"', txn)
        assert matches_transaction('split(field.code, "-", 1) == "OUT"', txn)

    def test_combined_field_and_description_match(self):
        """Match using both field and description."""
        txn = {
            'description': 'BANK WIRE TO VENDOR',
            'amount': 5000.00,
            'field': {'txn_type': 'WIRE', 'memo': 'Invoice payment'}
        }
        expr = 'contains("WIRE") and field.txn_type == "WIRE" and contains(field.memo, "Invoice")'
        assert matches_transaction(expr, txn)

    def test_exists_guards_extract(self):
        """Use exists() to guard extract() on optional field."""
        txn_with_memo = {
            'description': 'WIRE',
            'amount': 1000.00,
            'field': {'memo': 'REF:12345'}
        }
        txn_without_memo = {
            'description': 'WIRE',
            'amount': 1000.00,
            'field': {'other': 'value'}
        }
        # Without exists guard - should still work with exists
        expr = r'exists(field.memo) and extract(field.memo, "REF:(\\d+)") == "12345"'
        assert matches_transaction(expr, txn_with_memo)
        assert not matches_transaction(expr, txn_without_memo)


class TestEdgeCases:
    """Edge case tests for robustness."""

    def test_empty_description(self):
        """Functions handle empty description."""
        txn = {'description': '', 'amount': 100.00}
        assert not matches_transaction('contains("AMAZON")', txn)
        assert evaluate_transaction('trim()', txn) == ''
        assert evaluate_transaction('split("-", 0)', txn) == ''

    def test_field_with_special_characters(self):
        """Field values with special characters work correctly."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'memo': 'Payment for "Project X" (2025)'}
        }
        assert matches_transaction('contains(field.memo, "Project X")', txn)
        assert matches_transaction('contains(field.memo, "(2025)")', txn)

    def test_multiple_field_access(self):
        """Multiple field accesses in same expression."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {
                'type': 'ACH',
                'direction': 'OUT',
                'category': 'PAYMENT'
            }
        }
        expr = 'field.type == "ACH" and field.direction == "OUT" and field.category == "PAYMENT"'
        assert matches_transaction(expr, txn)

    def test_from_transaction_includes_field(self):
        """TransactionContext.from_transaction() includes field."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'code': 'ABC'}
        }
        ctx = TransactionContext.from_transaction(txn)
        assert ctx.field == {'code': 'ABC'}

    def test_from_transaction_no_field(self):
        """TransactionContext.from_transaction() works without field."""
        txn = {'description': 'TEST', 'amount': 100.00}
        ctx = TransactionContext.from_transaction(txn)
        assert ctx.field is None


class TestSourceVariable:
    """Tests for the source variable in expressions."""

    def test_source_accessible(self):
        """source variable is accessible in expressions."""
        txn = {'description': 'TEST', 'amount': 100.00, 'source': 'Amex'}
        assert matches_transaction('source == "Amex"', txn)
        assert matches_transaction('source == "amex"', txn)  # case-insensitive

    def test_source_not_matches(self):
        """source comparison returns false for non-matching."""
        txn = {'description': 'TEST', 'amount': 100.00, 'source': 'Amex'}
        assert not matches_transaction('source == "Chase"', txn)

    def test_source_combined_with_other_conditions(self):
        """source can be combined with other conditions."""
        txn = {
            'description': 'AMAZON PURCHASE',
            'amount': 50.00,
            'source': 'Amex'
        }
        assert matches_transaction('contains("AMAZON") and source == "Amex"', txn)
        assert not matches_transaction('contains("AMAZON") and source == "Chase"', txn)

    def test_source_with_field(self):
        """source can be combined with field access."""
        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'source': 'BankA',
            'field': {'type': 'WIRE'}
        }
        assert matches_transaction('source == "BankA" and field.type == "WIRE"', txn)

    def test_source_none_becomes_empty_string(self):
        """source is empty string when None."""
        txn = {'description': 'TEST', 'amount': 100.00, 'source': None}
        assert matches_transaction('source == ""', txn)

    def test_source_missing_becomes_empty_string(self):
        """source is empty string when not in transaction."""
        txn = {'description': 'TEST', 'amount': 100.00}
        assert matches_transaction('source == ""', txn)

    def test_from_transaction_includes_source(self):
        """TransactionContext.from_transaction() includes source."""
        txn = {'description': 'TEST', 'amount': 100.00, 'source': 'Chase'}
        ctx = TransactionContext.from_transaction(txn)
        assert ctx.source == 'Chase'

    def test_from_transaction_no_source(self):
        """TransactionContext.from_transaction() works without source."""
        txn = {'description': 'TEST', 'amount': 100.00}
        ctx = TransactionContext.from_transaction(txn)
        assert ctx.source == ""  # Default to empty string

    def test_source_can_be_returned_as_value(self):
        """source can be used as a return value (for dynamic tags)."""
        txn = {'description': 'TEST', 'amount': 100.00, 'source': 'AmexGold'}
        result = evaluate_transaction('source', txn)
        assert result == 'AmexGold'

    def test_source_in_contains(self):
        """source value can be checked with contains if needed."""
        txn = {'description': 'TEST', 'amount': 100.00, 'source': 'AmexGold'}
        # Can use contains on the source string
        assert matches_transaction('contains(source, "Amex")', txn)
        assert matches_transaction('contains(source, "Gold")', txn)
        assert not matches_transaction('contains(source, "Chase")', txn)

    def test_source_or_condition(self):
        """source works with OR conditions."""
        txn = {'description': 'TEST', 'amount': 100.00, 'source': 'Chase'}
        assert matches_transaction('source == "Amex" or source == "Chase"', txn)
        assert not matches_transaction('source == "Amex" or source == "Discover"', txn)


class TestBuiltinFieldAccess:
    """Tests for accessing built-in fields with field.* syntax."""

    def test_field_description_access(self):
        """field.description returns the description."""
        txn = {'description': 'STARBUCKS COFFEE', 'amount': 5.50}
        assert evaluate_transaction('field.description', txn) == 'STARBUCKS COFFEE'

    def test_field_amount_access(self):
        """field.amount returns the amount."""
        txn = {'description': 'TEST', 'amount': 123.45}
        assert evaluate_transaction('field.amount', txn) == 123.45

    def test_field_date_access(self):
        """field.date returns the date."""
        from datetime import date
        txn = {'description': 'TEST', 'amount': 100.00, 'date': date(2025, 1, 15)}
        result = evaluate_transaction('field.date', txn)
        assert result == date(2025, 1, 15)

    def test_field_source_access(self):
        """field.source returns the source."""
        txn = {'description': 'TEST', 'amount': 100.00, 'source': 'AmexGold'}
        assert evaluate_transaction('field.source', txn) == 'AmexGold'

    def test_field_source_empty_when_missing(self):
        """field.source returns empty string when not set."""
        txn = {'description': 'TEST', 'amount': 100.00}
        assert evaluate_transaction('field.source', txn) == ''

    def test_field_description_in_contains(self):
        """field.description works with contains()."""
        txn = {'description': 'APLPAY STARBUCKS', 'amount': 5.50}
        assert matches_transaction('contains(field.description, "STARBUCKS")', txn)
        assert matches_transaction('contains(field.description, "APLPAY")', txn)

    def test_field_amount_in_comparison(self):
        """field.amount works in comparisons."""
        txn = {'description': 'TEST', 'amount': 150.00}
        assert matches_transaction('field.amount > 100', txn)
        assert matches_transaction('field.amount < 200', txn)
        assert not matches_transaction('field.amount > 200', txn)

    def test_builtin_and_custom_field_together(self):
        """Built-in and custom fields work together."""
        txn = {
            'description': 'WIRE TRANSFER',
            'amount': 1000.00,
            'source': 'Chase',
            'field': {'txn_type': 'WIRE'}
        }
        assert matches_transaction('field.source == "Chase" and field.txn_type == "WIRE"', txn)
        assert matches_transaction('field.amount > 500 and field.txn_type == "WIRE"', txn)


class TestRegexReplaceFunction:
    """Tests for the regex_replace() function."""

    def test_regex_replace_basic(self):
        """regex_replace removes matched pattern."""
        txn = {'description': 'APLPAY STARBUCKS', 'amount': 5.50}
        result = evaluate_transaction('regex_replace(field.description, "^APLPAY\\\\s+", "")', txn)
        assert result == 'STARBUCKS'

    def test_regex_replace_with_replacement(self):
        """regex_replace can replace with new text."""
        txn = {'description': 'UBER TRIP', 'amount': 25.00}
        result = evaluate_transaction('regex_replace(field.description, "UBER", "LYFT")', txn)
        assert result == 'LYFT TRIP'

    def test_regex_replace_multiple_matches(self):
        """regex_replace replaces all matches."""
        txn = {'description': 'A B A C A', 'amount': 100.00}
        result = evaluate_transaction('regex_replace(field.description, "A", "X")', txn)
        assert result == 'X B X C X'

    def test_regex_replace_no_match(self):
        """regex_replace returns original when no match."""
        txn = {'description': 'STARBUCKS', 'amount': 5.50}
        result = evaluate_transaction('regex_replace(field.description, "^APLPAY\\\\s+", "")', txn)
        assert result == 'STARBUCKS'

    def test_regex_replace_complex_pattern(self):
        """regex_replace works with complex regex patterns."""
        txn = {'description': 'TEST DES:1234567 INFO', 'amount': 100.00}
        result = evaluate_transaction('regex_replace(field.description, "\\\\s+DES:\\\\d+", "")', txn)
        assert result == 'TEST INFO'

    def test_regex_replace_case_insensitive(self):
        """regex_replace is case insensitive by default."""
        txn = {'description': 'ApLpAy COFFEE', 'amount': 5.50}
        # The pattern should match case-insensitively
        result = evaluate_transaction('regex_replace(field.description, "(?i)aplpay\\\\s+", "")', txn)
        assert result == 'COFFEE'

    def test_regex_replace_on_custom_field(self):
        """regex_replace works on custom fields."""
        txn = {'description': 'TEST', 'amount': 100.00, 'field': {'memo': 'REF:12345 NOTE'}}
        result = evaluate_transaction('regex_replace(field.memo, "REF:\\\\d+\\\\s*", "")', txn)
        assert result == 'NOTE'


class TestUppercaseFunction:
    """Tests for the uppercase() function."""

    def test_uppercase_basic(self):
        """uppercase converts string to uppercase."""
        txn = {'description': 'Starbucks Coffee', 'amount': 5.50}
        assert evaluate_transaction('uppercase(field.description)', txn) == 'STARBUCKS COFFEE'

    def test_uppercase_already_upper(self):
        """uppercase works on already uppercase strings."""
        txn = {'description': 'ALREADY UPPER', 'amount': 100.00}
        assert evaluate_transaction('uppercase(field.description)', txn) == 'ALREADY UPPER'

    def test_uppercase_mixed(self):
        """uppercase handles mixed case."""
        txn = {'description': 'MiXeD CaSe', 'amount': 100.00}
        assert evaluate_transaction('uppercase(field.description)', txn) == 'MIXED CASE'

    def test_uppercase_in_comparison(self):
        """uppercase works in comparisons."""
        txn = {'description': 'starbucks', 'amount': 5.50}
        assert matches_transaction('uppercase(field.description) == "STARBUCKS"', txn)


class TestLowercaseFunction:
    """Tests for the lowercase() function."""

    def test_lowercase_basic(self):
        """lowercase converts string to lowercase."""
        txn = {'description': 'STARBUCKS COFFEE', 'amount': 5.50}
        assert evaluate_transaction('lowercase(field.description)', txn) == 'starbucks coffee'

    def test_lowercase_already_lower(self):
        """lowercase works on already lowercase strings."""
        txn = {'description': 'already lower', 'amount': 100.00}
        assert evaluate_transaction('lowercase(field.description)', txn) == 'already lower'

    def test_lowercase_in_comparison(self):
        """lowercase works in comparisons."""
        txn = {'description': 'STARBUCKS', 'amount': 5.50}
        assert matches_transaction('lowercase(field.description) == "starbucks"', txn)


class TestStripPrefixFunction:
    """Tests for the strip_prefix() function."""

    def test_strip_prefix_basic(self):
        """strip_prefix removes prefix when present."""
        txn = {'description': 'APLPAY STARBUCKS', 'amount': 5.50}
        result = evaluate_transaction('strip_prefix(field.description, "APLPAY ")', txn)
        assert result == 'STARBUCKS'

    def test_strip_prefix_no_match(self):
        """strip_prefix returns original when prefix not present."""
        txn = {'description': 'STARBUCKS', 'amount': 5.50}
        result = evaluate_transaction('strip_prefix(field.description, "APLPAY ")', txn)
        assert result == 'STARBUCKS'

    def test_strip_prefix_case_insensitive(self):
        """strip_prefix is case insensitive."""
        txn = {'description': 'aplpay STARBUCKS', 'amount': 5.50}
        result = evaluate_transaction('strip_prefix(field.description, "APLPAY ")', txn)
        assert result == 'STARBUCKS'  # Matches despite case difference

    def test_strip_prefix_partial(self):
        """strip_prefix only matches at the beginning."""
        txn = {'description': 'START APLPAY END', 'amount': 5.50}
        result = evaluate_transaction('strip_prefix(field.description, "APLPAY")', txn)
        assert result == 'START APLPAY END'  # Not at start, unchanged


class TestStripSuffixFunction:
    """Tests for the strip_suffix() function."""

    def test_strip_suffix_basic(self):
        """strip_suffix removes suffix when present."""
        txn = {'description': 'STARBUCKS DES:12345', 'amount': 5.50}
        result = evaluate_transaction('strip_suffix(field.description, " DES:12345")', txn)
        assert result == 'STARBUCKS'

    def test_strip_suffix_no_match(self):
        """strip_suffix returns original when suffix not present."""
        txn = {'description': 'STARBUCKS', 'amount': 5.50}
        result = evaluate_transaction('strip_suffix(field.description, " DES:12345")', txn)
        assert result == 'STARBUCKS'

    def test_strip_suffix_partial(self):
        """strip_suffix only matches at the end."""
        txn = {'description': 'DES:12345 STARBUCKS', 'amount': 5.50}
        result = evaluate_transaction('strip_suffix(field.description, "DES:12345")', txn)
        assert result == 'DES:12345 STARBUCKS'  # Not at end, unchanged


# =============================================================================
# Location Field Tests
# =============================================================================

class TestLocationField:
    """Tests for field.location access."""

    def test_location_in_context(self):
        """TransactionContext stores location."""
        ctx = TransactionContext(
            description="SAFEWAY #1222",
            amount=50.00,
            location="LAHAINA\nHI"
        )
        assert ctx.location == "LAHAINA\nHI"

    def test_location_default_empty(self):
        """Location defaults to empty string."""
        ctx = TransactionContext(description="TEST", amount=100.00)
        assert ctx.location == ""

    def test_from_transaction_includes_location(self):
        """TransactionContext.from_transaction() includes location."""
        txn = {
            'description': 'SAFEWAY #1222',
            'amount': 50.00,
            'location': 'LAHAINA\nHI'
        }
        ctx = TransactionContext.from_transaction(txn)
        assert ctx.location == 'LAHAINA\nHI'

    def test_from_transaction_no_location(self):
        """TransactionContext.from_transaction() works without location."""
        txn = {'description': 'TEST', 'amount': 100.00}
        ctx = TransactionContext.from_transaction(txn)
        assert ctx.location == ""

    def test_field_location_access(self):
        """field.location returns the location."""
        txn = {'description': 'SAFEWAY', 'amount': 50.00, 'location': 'SEATTLE\nWA'}
        result = evaluate_transaction('field.location', txn)
        assert result == 'SEATTLE\nWA'

    def test_field_location_empty_when_missing(self):
        """field.location returns empty string when not set."""
        txn = {'description': 'TEST', 'amount': 100.00}
        assert evaluate_transaction('field.location', txn) == ''

    def test_field_location_in_contains(self):
        """field.location works with contains()."""
        txn = {'description': 'SAFEWAY #1222', 'amount': 50.00, 'location': 'LAHAINA\nHI'}
        assert matches_transaction('contains(field.location, "HI")', txn)
        assert matches_transaction('contains(field.location, "LAHAINA")', txn)
        assert not matches_transaction('contains(field.location, "WA")', txn)

    def test_field_location_in_comparison(self):
        """field.location works in comparisons."""
        txn = {'description': 'TEST', 'amount': 100.00, 'location': 'SEATTLE\nWA'}
        assert matches_transaction('field.location == "SEATTLE\\nWA"', txn)

    def test_field_location_with_exists(self):
        """exists() works with field.location."""
        txn_with_loc = {'description': 'TEST', 'amount': 100.00, 'location': 'SEATTLE\nWA'}
        txn_no_loc = {'description': 'TEST', 'amount': 100.00}
        txn_empty_loc = {'description': 'TEST', 'amount': 100.00, 'location': ''}

        assert matches_transaction('exists(field.location)', txn_with_loc)
        assert not matches_transaction('exists(field.location)', txn_no_loc)
        assert not matches_transaction('exists(field.location)', txn_empty_loc)

    def test_field_location_combined_conditions(self):
        """field.location works with combined conditions."""
        hawaii_safeway = {
            'description': 'SAFEWAY #1222',
            'amount': 75.00,
            'location': 'LAHAINA\nHI'
        }
        wa_safeway = {
            'description': 'SAFEWAY #1142',
            'amount': 50.00,
            'location': 'KIRKLAND\nWA'
        }

        # Match Safeway in Hawaii only
        expr = 'contains("SAFEWAY") and contains(field.location, "HI")'
        assert matches_transaction(expr, hawaii_safeway)
        assert not matches_transaction(expr, wa_safeway)

    def test_field_location_regex(self):
        """regex() works with field.location."""
        txn = {'description': 'STORE', 'amount': 100.00, 'location': 'SEATTLE\nWA'}
        assert matches_transaction(r'regex(field.location, "\\bWA$")', txn)
        assert not matches_transaction(r'regex(field.location, "\\bHI$")', txn)
