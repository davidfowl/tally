"""Tests for the AST-based expression parser."""

import pytest
from datetime import date
from tally.expr_parser import (
    parse, parse_expression, evaluate, evaluate_ast, evaluate_filter,
    create_context, ExpressionContext, ExpressionEvaluator,
    ExpressionError, UnsafeNodeError, validate_ast,
)


# =============================================================================
# Test Data Helpers
# =============================================================================

def make_transactions(amounts, category="Food", subcategory="Grocery", tags=None):
    """Create test transactions with given amounts."""
    txns = []
    for i, amount in enumerate(amounts):
        txns.append({
            'amount': amount,
            'date': date(2025, (i % 12) + 1, 15),
            'category': category,
            'subcategory': subcategory,
            'merchant': 'Test Merchant',
            'tags': tags or [],
        })
    return txns


# =============================================================================
# Parsing Tests - Valid Expressions
# =============================================================================

class TestParsing:
    """Test that valid expressions parse without error."""

    def test_number(self):
        tree = parse("42")
        assert tree is not None

    def test_float(self):
        tree = parse("3.14")
        assert tree is not None

    def test_string(self):
        tree = parse('"hello"')
        assert tree is not None

    def test_identifier(self):
        tree = parse("category")
        assert tree is not None

    def test_comparison_equal(self):
        tree = parse('category == "Food"')
        assert tree is not None

    def test_comparison_not_equal(self):
        tree = parse('category != "Food"')
        assert tree is not None

    def test_comparison_less_than(self):
        tree = parse("months < 6")
        assert tree is not None

    def test_comparison_greater_equal(self):
        tree = parse("months >= 6")
        assert tree is not None

    def test_boolean_and(self):
        tree = parse('category == "Food" and months >= 6')
        assert tree is not None

    def test_boolean_or(self):
        tree = parse('category == "Food" or category == "Bills"')
        assert tree is not None

    def test_boolean_not(self):
        tree = parse('not category == "Food"')
        assert tree is not None

    def test_arithmetic_add(self):
        tree = parse("1 + 2")
        assert tree is not None

    def test_arithmetic_subtract(self):
        tree = parse("5 - 3")
        assert tree is not None

    def test_arithmetic_multiply(self):
        tree = parse("2 * 3")
        assert tree is not None

    def test_arithmetic_divide(self):
        tree = parse("10 / 2")
        assert tree is not None

    def test_unary_minus(self):
        tree = parse("-5")
        assert tree is not None

    def test_parentheses(self):
        tree = parse("(1 + 2) * 3")
        assert tree is not None

    def test_function_call(self):
        tree = parse("sum(payments)")
        assert tree is not None

    def test_in_operator(self):
        tree = parse('"recurring" in tags')
        assert tree is not None

    def test_not_in_operator(self):
        tree = parse('"recurring" not in tags')
        assert tree is not None

    def test_complex_expression(self):
        tree = parse('category == "Bills" and months >= 6 and sum(payments) > 1000')
        assert tree is not None

    def test_ternary(self):
        tree = parse('1 if True else 0')
        assert tree is not None


# =============================================================================
# Parsing Tests - Invalid Expressions
# =============================================================================

class TestParsingErrors:
    """Test that invalid expressions raise appropriate errors."""

    def test_syntax_error(self):
        with pytest.raises(ExpressionError) as exc:
            parse("1 +")
        assert "Syntax error" in str(exc.value)

    def test_unclosed_paren(self):
        with pytest.raises(ExpressionError) as exc:
            parse("(1 + 2")
        assert "Syntax error" in str(exc.value)

    def test_unsafe_import(self):
        # __import__ parses as a valid function call, but evaluation fails
        tree = parse("__import__('os')")
        ctx = create_context()
        with pytest.raises(ExpressionError) as exc:
            evaluate("__import__('os')", ctx)
        assert "Unknown function" in str(exc.value)

    def test_unsafe_lambda(self):
        with pytest.raises(UnsafeNodeError):
            parse("lambda x: x")

    def test_list_comp_allowed(self):
        """List comprehensions are now allowed for data source queries."""
        # Should parse without error
        tree = parse("[r for r in source if r > 0]")
        assert tree is not None

    def test_unsafe_dict(self):
        with pytest.raises(UnsafeNodeError):
            parse("{'a': 1}")

    def test_unsafe_list(self):
        with pytest.raises(UnsafeNodeError):
            parse("[1, 2, 3]")


# =============================================================================
# Evaluation Tests - Literals
# =============================================================================

class TestEvaluateLiterals:
    """Test evaluation of literal values."""

    def test_integer(self):
        ctx = create_context()
        assert evaluate("42", ctx) == 42

    def test_float(self):
        ctx = create_context()
        assert evaluate("3.14", ctx) == 3.14

    def test_string(self):
        ctx = create_context()
        assert evaluate('"hello"', ctx) == "hello"

    def test_true(self):
        ctx = create_context()
        assert evaluate("True", ctx) is True

    def test_false(self):
        ctx = create_context()
        assert evaluate("False", ctx) is False


# =============================================================================
# Evaluation Tests - Arithmetic
# =============================================================================

class TestEvaluateArithmetic:
    """Test evaluation of arithmetic expressions."""

    def test_addition(self):
        ctx = create_context()
        assert evaluate("1 + 2", ctx) == 3

    def test_subtraction(self):
        ctx = create_context()
        assert evaluate("5 - 3", ctx) == 2

    def test_multiplication(self):
        ctx = create_context()
        assert evaluate("2 * 3", ctx) == 6

    def test_division(self):
        ctx = create_context()
        assert evaluate("10 / 2", ctx) == 5

    def test_modulo(self):
        ctx = create_context()
        assert evaluate("7 % 3", ctx) == 1

    def test_unary_minus(self):
        ctx = create_context()
        assert evaluate("-5", ctx) == -5

    def test_precedence(self):
        ctx = create_context()
        assert evaluate("1 + 2 * 3", ctx) == 7

    def test_parentheses_override_precedence(self):
        ctx = create_context()
        assert evaluate("(1 + 2) * 3", ctx) == 9

    def test_division_by_zero(self):
        ctx = create_context()
        # Should return 0, not raise
        assert evaluate("10 / 0", ctx) == 0


# =============================================================================
# Evaluation Tests - Comparisons
# =============================================================================

class TestEvaluateComparisons:
    """Test evaluation of comparison expressions."""

    def test_equal_numbers(self):
        ctx = create_context()
        assert evaluate("1 == 1", ctx) is True
        assert evaluate("1 == 2", ctx) is False

    def test_not_equal_numbers(self):
        ctx = create_context()
        assert evaluate("1 != 2", ctx) is True
        assert evaluate("1 != 1", ctx) is False

    def test_less_than(self):
        ctx = create_context()
        assert evaluate("1 < 2", ctx) is True
        assert evaluate("2 < 1", ctx) is False

    def test_less_equal(self):
        ctx = create_context()
        assert evaluate("1 <= 1", ctx) is True
        assert evaluate("1 <= 2", ctx) is True
        assert evaluate("2 <= 1", ctx) is False

    def test_greater_than(self):
        ctx = create_context()
        assert evaluate("2 > 1", ctx) is True
        assert evaluate("1 > 2", ctx) is False

    def test_greater_equal(self):
        ctx = create_context()
        assert evaluate("1 >= 1", ctx) is True
        assert evaluate("2 >= 1", ctx) is True
        assert evaluate("1 >= 2", ctx) is False

    def test_string_equality_case_insensitive(self):
        ctx = create_context()
        assert evaluate('"FOOD" == "food"', ctx) is True
        assert evaluate('"Food" == "FOOD"', ctx) is True

    def test_chained_comparison(self):
        ctx = create_context()
        assert evaluate("1 < 2 < 3", ctx) is True
        assert evaluate("1 < 2 > 3", ctx) is False


# =============================================================================
# Evaluation Tests - Boolean Logic
# =============================================================================

class TestEvaluateBooleanLogic:
    """Test evaluation of boolean expressions."""

    def test_and_true(self):
        ctx = create_context()
        assert evaluate("True and True", ctx) is True

    def test_and_false(self):
        ctx = create_context()
        assert evaluate("True and False", ctx) is False
        assert evaluate("False and True", ctx) is False

    def test_or_true(self):
        ctx = create_context()
        assert evaluate("True or False", ctx) is True
        assert evaluate("False or True", ctx) is True

    def test_or_false(self):
        ctx = create_context()
        assert evaluate("False or False", ctx) is False

    def test_not_true(self):
        ctx = create_context()
        assert evaluate("not True", ctx) is False

    def test_not_false(self):
        ctx = create_context()
        assert evaluate("not False", ctx) is True

    def test_short_circuit_and(self):
        # Should not evaluate second expression if first is False
        ctx = create_context()
        assert evaluate("False and unknown_var", ctx) is False

    def test_short_circuit_or(self):
        # Should not evaluate second expression if first is True
        ctx = create_context()
        assert evaluate("True or unknown_var", ctx) is True


# =============================================================================
# Evaluation Tests - In Operator
# =============================================================================

class TestEvaluateIn:
    """Test evaluation of 'in' operator for set membership."""

    def test_string_in_set(self):
        ctx = create_context(
            transactions=make_transactions([100], tags=["recurring", "monthly"])
        )
        assert evaluate('"recurring" in tags', ctx) is True
        assert evaluate('"annual" in tags', ctx) is False

    def test_string_in_set_case_insensitive(self):
        ctx = create_context(
            transactions=make_transactions([100], tags=["Recurring"])
        )
        assert evaluate('"recurring" in tags', ctx) is True
        assert evaluate('"RECURRING" in tags', ctx) is True

    def test_not_in_set(self):
        ctx = create_context(
            transactions=make_transactions([100], tags=["recurring"])
        )
        assert evaluate('"annual" not in tags', ctx) is True
        assert evaluate('"recurring" not in tags', ctx) is False


# =============================================================================
# Evaluation Tests - Primitives
# =============================================================================

class TestEvaluatePrimitives:
    """Test evaluation of built-in primitives."""

    def test_payments(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate("payments", ctx) == [100, 200, 300]

    def test_months(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        # Each transaction is in a different month (1, 2, 3)
        assert evaluate("months", ctx) == 3

    def test_category(self):
        ctx = create_context(
            transactions=make_transactions([100], category="Food")
        )
        assert evaluate("category", ctx) == "Food"

    def test_subcategory(self):
        ctx = create_context(
            transactions=make_transactions([100], subcategory="Grocery")
        )
        assert evaluate("subcategory", ctx) == "Grocery"

    def test_tags(self):
        ctx = create_context(
            transactions=make_transactions([100], tags=["recurring", "monthly"])
        )
        tags = evaluate("tags", ctx)
        assert "recurring" in tags
        assert "monthly" in tags

    def test_empty_transactions(self):
        ctx = create_context(transactions=[])
        assert evaluate("payments", ctx) == []
        assert evaluate("months", ctx) == 1  # Default to 1
        assert evaluate("category", ctx) == ""
        assert evaluate("subcategory", ctx) == ""


# =============================================================================
# Evaluation Tests - Functions
# =============================================================================

class TestEvaluateFunctions:
    """Test evaluation of built-in functions."""

    def test_sum(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate("sum(payments)", ctx) == 600

    def test_sum_empty(self):
        ctx = create_context(transactions=[])
        assert evaluate("sum(payments)", ctx) == 0

    def test_count(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate("count(payments)", ctx) == 3

    def test_count_empty(self):
        ctx = create_context(transactions=[])
        assert evaluate("count(payments)", ctx) == 0

    def test_avg(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate("avg(payments)", ctx) == 200

    def test_avg_empty(self):
        ctx = create_context(transactions=[])
        assert evaluate("avg(payments)", ctx) == 0

    def test_max(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate("max(payments)", ctx) == 300

    def test_max_empty(self):
        ctx = create_context(transactions=[])
        assert evaluate("max(payments)", ctx) == 0

    def test_min(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate("min(payments)", ctx) == 100

    def test_min_empty(self):
        ctx = create_context(transactions=[])
        assert evaluate("min(payments)", ctx) == 0

    def test_stddev(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        result = evaluate("stddev(payments)", ctx)
        assert abs(result - 100.0) < 0.01

    def test_stddev_single_value(self):
        ctx = create_context(transactions=make_transactions([100]))
        assert evaluate("stddev(payments)", ctx) == 0

    def test_abs(self):
        ctx = create_context()
        assert evaluate("abs(-5)", ctx) == 5

    def test_round(self):
        ctx = create_context()
        assert evaluate("round(3.7)", ctx) == 4


# =============================================================================
# Evaluation Tests - Variables
# =============================================================================

class TestEvaluateVariables:
    """Test evaluation with user-defined variables."""

    def test_custom_variable(self):
        ctx = create_context(variables={"threshold": 500})
        assert evaluate("threshold", ctx) == 500

    def test_variable_in_expression(self):
        ctx = create_context(
            transactions=make_transactions([100, 200, 300]),
            variables={"threshold": 500}
        )
        assert evaluate("sum(payments) > threshold", ctx) is True

    def test_variable_overrides_primitive(self):
        # User variables should take precedence
        ctx = create_context(
            transactions=make_transactions([100, 200, 300]),
            variables={"payments": [1000]}
        )
        assert evaluate("sum(payments)", ctx) == 1000

    def test_unknown_variable(self):
        ctx = create_context()
        with pytest.raises(ExpressionError) as exc:
            evaluate("unknown_var", ctx)
        assert "Unknown variable" in str(exc.value)


# =============================================================================
# Evaluation Tests - Complex Expressions
# =============================================================================

class TestEvaluateComplex:
    """Test evaluation of complex expressions."""

    def test_category_filter(self):
        ctx = create_context(
            transactions=make_transactions([100], category="Food")
        )
        assert evaluate('category == "Food"', ctx) is True
        assert evaluate('category == "Bills"', ctx) is False

    def test_combined_filter(self):
        ctx = create_context(
            transactions=make_transactions([100, 200, 300], category="Bills")
        )
        assert evaluate('category == "Bills" and months >= 3', ctx) is True
        assert evaluate('category == "Bills" and months >= 6', ctx) is False

    def test_sum_comparison(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate("sum(payments) > 500", ctx) is True
        assert evaluate("sum(payments) > 700", ctx) is False

    def test_cv_calculation(self):
        ctx = create_context(transactions=make_transactions([100, 100, 100]))
        # CV = stddev / avg, all same = stddev 0
        assert evaluate("stddev(payments) / avg(payments)", ctx) == 0

    def test_monthly_average(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        # Total = 600, months = 3, avg = 200
        assert evaluate("sum(payments) / months", ctx) == 200

    def test_ternary_expression(self):
        ctx = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate('1 if sum(payments) > 500 else 0', ctx) == 1
        assert evaluate('1 if sum(payments) > 700 else 0', ctx) == 0


# =============================================================================
# Convenience Function Tests
# =============================================================================

class TestEvaluateFilter:
    """Test the evaluate_filter convenience function."""

    def test_simple_filter(self):
        txns = make_transactions([100, 200], category="Food")
        assert evaluate_filter('category == "Food"', txns) is True
        assert evaluate_filter('category == "Bills"', txns) is False

    def test_filter_with_variables(self):
        txns = make_transactions([100, 200])
        assert evaluate_filter("sum(payments) > threshold", txns, variables={"threshold": 250}) is True
        assert evaluate_filter("sum(payments) > threshold", txns, variables={"threshold": 350}) is False

    def test_filter_with_num_months(self):
        txns = make_transactions([100, 200])
        # This tests that num_months is passed to context (used for percentage calculations)
        ctx = create_context(transactions=txns, num_months=12)
        assert ctx.num_months == 12


# =============================================================================
# Pre-parsed AST Tests
# =============================================================================

class TestPreParsedAST:
    """Test evaluation of pre-parsed AST for performance."""

    def test_evaluate_ast(self):
        tree = parse("sum(payments) > 500")

        ctx1 = create_context(transactions=make_transactions([100, 200, 300]))
        assert evaluate_ast(tree, ctx1) is True

        ctx2 = create_context(transactions=make_transactions([100, 100]))
        assert evaluate_ast(tree, ctx2) is False

    def test_reuse_parsed_ast(self):
        """Parse once, evaluate many times."""
        tree = parse('category == "Food" and sum(payments) > 100')

        # Test with multiple contexts
        ctx1 = create_context(transactions=make_transactions([50, 60], category="Food"))
        ctx2 = create_context(transactions=make_transactions([50, 60], category="Bills"))
        ctx3 = create_context(transactions=make_transactions([150], category="Food"))

        assert evaluate_ast(tree, ctx1) is True  # Food, sum=110 > 100
        assert evaluate_ast(tree, ctx2) is False  # Bills, not Food
        assert evaluate_ast(tree, ctx3) is True  # Food, sum=150 > 100


# =============================================================================
# Group By Tests
# =============================================================================

def make_monthly_transactions(monthly_amounts):
    """Create transactions with specific amounts per month.

    Args:
        monthly_amounts: dict of {month: [amounts]} or list of (month, amount) tuples
    """
    txns = []
    if isinstance(monthly_amounts, dict):
        for month, amounts in monthly_amounts.items():
            for amount in amounts:
                txns.append({
                    'amount': amount,
                    'date': date(2025, month, 15),
                    'category': 'Food',
                    'subcategory': 'Grocery',
                    'merchant': 'Test',
                    'tags': [],
                })
    return txns


class TestByFunction:
    """Test the by() grouping function."""

    def test_by_month_basic(self):
        """by('month') returns list of payment lists grouped by month."""
        txns = make_monthly_transactions({
            1: [100, 200],  # January: 2 transactions
            2: [150],       # February: 1 transaction
            3: [75, 125],   # March: 2 transactions
        })
        ctx = create_context(transactions=txns)
        result = evaluate("by('month')", ctx)

        # Should return 3 groups (one per month)
        assert len(result) == 3
        # Groups are sorted by month key
        assert result[0] == [100, 200]  # Jan
        assert result[1] == [150]       # Feb
        assert result[2] == [75, 125]   # Mar

    def test_by_month_empty(self):
        """by('month') with no transactions returns empty list."""
        ctx = create_context(transactions=[])
        result = evaluate("by('month')", ctx)
        assert result == []

    def test_by_invalid_field(self):
        """by() with invalid field raises error."""
        txns = make_monthly_transactions({1: [100]})
        ctx = create_context(transactions=txns)
        with pytest.raises(ExpressionError, match="Unknown grouping field"):
            evaluate("by('invalid')", ctx)


class TestAutoMapFunctions:
    """Test that aggregation functions auto-map over nested lists."""

    def test_sum_by_month(self):
        """sum(by('month')) returns monthly totals."""
        txns = make_monthly_transactions({
            1: [100, 200],  # 300
            2: [150],       # 150
            3: [75, 125],   # 200
        })
        ctx = create_context(transactions=txns)
        result = evaluate("sum(by('month'))", ctx)
        assert result == [300, 150, 200]

    def test_count_by_month(self):
        """count(by('month')) returns transaction counts per month."""
        txns = make_monthly_transactions({
            1: [100, 200],  # 2
            2: [150],       # 1
            3: [75, 125],   # 2
        })
        ctx = create_context(transactions=txns)
        result = evaluate("count(by('month'))", ctx)
        assert result == [2, 1, 2]

    def test_avg_by_month(self):
        """avg(by('month')) returns average per month."""
        txns = make_monthly_transactions({
            1: [100, 200],  # avg 150
            2: [150],       # avg 150
            3: [100, 200],  # avg 150
        })
        ctx = create_context(transactions=txns)
        result = evaluate("avg(by('month'))", ctx)
        assert result == [150, 150, 150]

    def test_max_by_month(self):
        """max(by('month')) returns max per month."""
        txns = make_monthly_transactions({
            1: [100, 200],  # max 200
            2: [150],       # max 150
            3: [75, 125],   # max 125
        })
        ctx = create_context(transactions=txns)
        result = evaluate("max(by('month'))", ctx)
        assert result == [200, 150, 125]

    def test_min_by_month(self):
        """min(by('month')) returns min per month."""
        txns = make_monthly_transactions({
            1: [100, 200],  # min 100
            2: [150],       # min 150
            3: [75, 125],   # min 75
        })
        ctx = create_context(transactions=txns)
        result = evaluate("min(by('month'))", ctx)
        assert result == [100, 150, 75]


class TestByComposition:
    """Test composing by() with other functions."""

    def test_avg_sum_by_month(self):
        """avg(sum(by('month'))) returns average of monthly totals."""
        txns = make_monthly_transactions({
            1: [100, 200],  # 300
            2: [150],       # 150
            3: [75, 125],   # 200
        })
        ctx = create_context(transactions=txns)
        # Average of [300, 150, 200] = 650/3 ≈ 216.67
        result = evaluate("avg(sum(by('month')))", ctx)
        assert abs(result - 216.67) < 0.01

    def test_max_sum_by_month(self):
        """max(sum(by('month'))) returns highest monthly total."""
        txns = make_monthly_transactions({
            1: [100, 200],  # 300
            2: [150],       # 150
            3: [75, 125],   # 200
        })
        ctx = create_context(transactions=txns)
        result = evaluate("max(sum(by('month')))", ctx)
        assert result == 300

    def test_cv_with_by(self):
        """CV can be computed using by(): stddev(sum(by('month'))) / avg(sum(by('month')))"""
        txns = make_monthly_transactions({
            1: [100],  # 100
            2: [100],  # 100
            3: [100],  # 100
        })
        ctx = create_context(transactions=txns)
        # All monthly totals are 100, so stddev is 0, CV is 0
        result = evaluate("stddev(sum(by('month'))) / avg(sum(by('month')))", ctx)
        assert result == 0

    def test_cv_variable_with_by(self):
        """CV can be computed with variable: monthly = sum(by('month')); cv = stddev(monthly) / avg(monthly)"""
        txns = make_monthly_transactions({
            1: [100],
            2: [200],
            3: [300],
        })
        ctx = create_context(
            transactions=txns,
            variables={'monthly': [100, 200, 300]}  # Pre-computed for test
        )
        # Test the formula works
        result = evaluate("stddev(monthly) / avg(monthly)", ctx)
        assert result > 0  # Should be non-zero for varying values

    def test_peak_month_ratio(self):
        """Complex expression: max(sum(by('month'))) / avg(sum(by('month')))"""
        txns = make_monthly_transactions({
            1: [100],   # 100
            2: [100],   # 100
            3: [400],   # 400 (peak)
        })
        ctx = create_context(transactions=txns)
        # max=400, avg=200, ratio=2.0
        result = evaluate("max(sum(by('month'))) / avg(sum(by('month')))", ctx)
        assert result == 2.0


# =============================================================================
# Period Function Tests
# =============================================================================

class TestPeriodFunction:
    """Test the period() function for global analysis period."""

    def test_period_month_from_data(self):
        """period('month') returns value from period_data."""
        ctx = create_context(
            transactions=make_transactions([100]),
            period_data={'month': 12, 'year': 1}
        )
        result = evaluate("period('month')", ctx)
        assert result == 12

    def test_period_year_from_data(self):
        """period('year') returns value from period_data."""
        ctx = create_context(
            transactions=make_transactions([100]),
            period_data={'month': 12, 'year': 1}
        )
        result = evaluate("period('year')", ctx)
        assert result == 1

    def test_period_month_default(self):
        """period('month') defaults to 12 if not provided."""
        ctx = create_context(transactions=make_transactions([100]))
        result = evaluate("period('month')", ctx)
        assert result == 12

    def test_period_year_default(self):
        """period('year') defaults to 1 if not provided."""
        ctx = create_context(transactions=make_transactions([100]))
        result = evaluate("period('year')", ctx)
        assert result == 1

    def test_period_in_threshold(self):
        """period() can be used in threshold calculations."""
        ctx = create_context(
            transactions=make_transactions([100, 200, 300]),  # 3 months
            period_data={'month': 12}
        )
        # months=3, period('month')=12, threshold=6
        result = evaluate("months >= period('month') * 0.5", ctx)
        assert result is False  # 3 < 6

    def test_period_partial_year(self):
        """period() works for partial year analysis."""
        ctx = create_context(
            transactions=make_transactions([100, 200, 300]),  # 3 months active
            period_data={'month': 6}  # Only 6 months of data
        )
        # months=3, period('month')=6, threshold=3
        result = evaluate("months >= period('month') * 0.5", ctx)
        assert result is True  # 3 >= 3


# =============================================================================
# Scalar Max/Min Tests
# =============================================================================

class TestScalarMaxMin:
    """Test max_val() and min_val() for scalar comparisons."""

    def test_max_val_first_larger(self):
        """max_val returns first when larger."""
        ctx = create_context(transactions=make_transactions([100]))
        result = evaluate("max_val(10, 5)", ctx)
        assert result == 10

    def test_max_val_second_larger(self):
        """max_val returns second when larger."""
        ctx = create_context(transactions=make_transactions([100]))
        result = evaluate("max_val(5, 10)", ctx)
        assert result == 10

    def test_max_val_equal(self):
        """max_val with equal values."""
        ctx = create_context(transactions=make_transactions([100]))
        result = evaluate("max_val(5, 5)", ctx)
        assert result == 5

    def test_min_val_first_smaller(self):
        """min_val returns first when smaller."""
        ctx = create_context(transactions=make_transactions([100]))
        result = evaluate("min_val(5, 10)", ctx)
        assert result == 5

    def test_min_val_second_smaller(self):
        """min_val returns second when smaller."""
        ctx = create_context(transactions=make_transactions([100]))
        result = evaluate("min_val(10, 5)", ctx)
        assert result == 5

    def test_max_val_with_expression(self):
        """max_val with computed expressions."""
        ctx = create_context(
            transactions=make_transactions([100]),
            period_data={'month': 12}
        )
        # max_val(2, 12 * 0.5) = max_val(2, 6) = 6
        result = evaluate("max_val(2, period('month') * 0.5)", ctx)
        assert result == 6

    def test_max_val_threshold_floor(self):
        """max_val provides floor for proportional thresholds."""
        ctx = create_context(
            transactions=make_transactions([100]),
            period_data={'month': 3}  # Short period
        )
        # max_val(2, 3 * 0.5) = max_val(2, 1.5) = 2
        result = evaluate("max_val(2, period('month') * 0.5)", ctx)
        assert result == 2


# =============================================================================
# List Comprehension and Data Source Tests
# =============================================================================

class TestListComprehensions:
    """Test list comprehensions for data source queries."""

    def test_simple_list_comp(self):
        """Simple list comprehension over data source."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
            ]
        }

        result = evaluate_transaction(
            "[r.item for r in orders]",
            txn,
            data_sources=data_sources
        )
        assert result == ['Book', 'Cable']

    def test_list_comp_with_filter(self):
        """List comprehension with if condition."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
                {'item': 'Toy', 'amount': 10.0},
            ]
        }

        result = evaluate_transaction(
            "[r.item for r in orders if r.amount > 15]",
            txn,
            data_sources=data_sources
        )
        assert result == ['Book', 'Cable']

    def test_sum_with_generator(self):
        """sum() with generator expression."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
            ]
        }

        result = evaluate_transaction(
            "sum(r.amount for r in orders)",
            txn,
            data_sources=data_sources
        )
        assert result == 50.0

    def test_any_with_generator(self):
        """any() with generator expression."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
            ]
        }

        # At least one order with amount > 25
        result = evaluate_transaction(
            "any(r.amount > 25 for r in orders)",
            txn,
            data_sources=data_sources
        )
        assert result is True

        # No order with amount > 100
        result = evaluate_transaction(
            "any(r.amount > 100 for r in orders)",
            txn,
            data_sources=data_sources
        )
        assert result is False

    def test_len_with_list_comp(self):
        """len() with list comprehension."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
                {'item': 'Toy', 'amount': 10.0},
            ]
        }

        result = evaluate_transaction(
            "len([r for r in orders if r.amount >= 20])",
            txn,
            data_sources=data_sources
        )
        assert result == 2

    def test_next_with_default(self):
        """next() with generator and default."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
            ]
        }

        # First matching item
        result = evaluate_transaction(
            "next((r.item for r in orders if r.amount > 25), 'None')",
            txn,
            data_sources=data_sources
        )
        assert result == 'Cable'

        # No match, return default
        result = evaluate_transaction(
            "next((r.item for r in orders if r.amount > 100), 'None')",
            txn,
            data_sources=data_sources
        )
        assert result == 'None'

    def test_subscript_access(self):
        """List subscript access [0]."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
            ]
        }

        result = evaluate_transaction(
            "[r.item for r in orders][0]",
            txn,
            data_sources=data_sources
        )
        assert result == 'Book'


class TestTxnNamespace:
    """Test txn. namespace for explicit transaction context."""

    def test_txn_amount(self):
        """txn.amount accesses transaction amount."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        result = evaluate_transaction("txn.amount > 40", txn)
        assert result is True

    def test_txn_date(self):
        """txn.date accesses transaction date."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        result = evaluate_transaction('txn.date >= "2025-01-01"', txn)
        assert result is True

    def test_txn_in_list_comp(self):
        """txn. can be used inside list comprehension filter."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0, 'date': date(2025, 1, 14)},
                {'item': 'Cable', 'amount': 30.0, 'date': date(2025, 1, 15)},
                {'item': 'Toy', 'amount': 10.0, 'date': date(2025, 2, 1)},
            ]
        }

        # Match orders on same date as transaction
        result = evaluate_transaction(
            "[r.item for r in orders if r.date == txn.date]",
            txn,
            data_sources=data_sources
        )
        assert result == ['Cable']

    def test_txn_amount_match(self):
        """Match orders where sum equals transaction amount."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
            ]
        }

        result = evaluate_transaction(
            "sum(r.amount for r in orders) == txn.amount",
            txn,
            data_sources=data_sources
        )
        assert result is True


class TestWalrusOperator:
    """Test walrus operator for variable binding."""

    def test_walrus_basic(self):
        """Basic walrus operator usage."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
            ]
        }

        # Capture result and use it
        result = evaluate_transaction(
            "(items := [r.item for r in orders]) and len(items) > 0",
            txn,
            data_sources=data_sources
        )
        assert result is True

    def test_walrus_reuse(self):
        """Walrus captures value for reuse."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'AMAZON', 'amount': 50.00, 'date': date(2025, 1, 15)}
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.0},
                {'item': 'Cable', 'amount': 30.0},
            ]
        }

        # Capture and check length, then access first element
        result = evaluate_transaction(
            "(items := [r.item for r in orders]) and len(items) == 2 and items[0] == 'Book'",
            txn,
            data_sources=data_sources
        )
        assert result is True


class TestSupplementalDataIntegration:
    """Test supplemental data sources with rule matching."""

    def test_normalize_merchant_with_data_sources(self):
        """normalize_merchant passes data_sources to expression evaluator."""
        from tally.merchant_utils import normalize_merchant

        # Create supplemental data for cross-source matching
        data_sources = {
            'amazon_orders': [
                {'item': 'Book', 'amount': 45.99, 'date': date(2025, 1, 15)},
                {'item': 'Cable', 'amount': 10.00, 'date': date(2025, 1, 15)},
            ]
        }

        # Rule that uses data_sources in match expression
        rules = [(
            'contains("AMAZON") and any(r for r in amazon_orders if r.amount == amount)',
            'Amazon',
            'Shopping',
            'Online',
            None,
            'user',
            ['amazon']
        )]

        # Transaction matching an order amount
        merchant, category, subcategory, match_info = normalize_merchant(
            'AMAZON MARKETPLACE',
            rules,
            amount=45.99,
            txn_date=date(2025, 1, 15),
            data_sources=data_sources
        )

        assert merchant == 'Amazon'
        assert category == 'Shopping'
        assert 'amazon' in match_info['tags']

    def test_normalize_merchant_no_data_sources_fallback(self):
        """normalize_merchant works without data_sources for non-cross-source rules."""
        from tally.merchant_utils import normalize_merchant

        # Simple rule without data source access
        rules = [(
            'contains("NETFLIX")',
            'Netflix',
            'Subscriptions',
            'Streaming',
            None,
            'user',
            ['entertainment']
        )]

        merchant, category, subcategory, match_info = normalize_merchant(
            'NETFLIX.COM',
            rules,
            amount=15.99,
        )

        assert merchant == 'Netflix'
        assert category == 'Subscriptions'

    def test_matches_transaction_with_data_sources(self):
        """matches_transaction accepts data_sources parameter."""
        from tally.expr_parser import matches_transaction

        txn = {
            'description': 'AMAZON MARKETPLACE',
            'amount': 50.00,  # Use clean amounts to avoid float precision issues
            'date': date(2025, 1, 15)
        }
        data_sources = {
            'orders': [
                {'item': 'Book', 'amount': 20.00},
                {'item': 'Cable', 'amount': 30.00},
            ]
        }

        # Match where sum of order amounts equals transaction amount
        result = matches_transaction(
            'contains("AMAZON") and sum(r.amount for r in orders) == amount',
            txn,
            data_sources=data_sources
        )
        assert result is True

        # No match when amounts don't equal
        txn['amount'] = 100.00
        result = matches_transaction(
            'contains("AMAZON") and sum(r.amount for r in orders) == amount',
            txn,
            data_sources=data_sources
        )
        assert result is False
