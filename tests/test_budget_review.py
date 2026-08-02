"""
Tests for budget targets, anomaly detection and duplicate transaction warnings.

These features sit on top of rule processing and drive the "budget review"
workflow: what did I plan to spend, what did I actually spend, what changed,
and can I trust the numbers at all.
"""

import json
import os
import subprocess
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from tally.analyzer import analyze_transactions
from tally.anomalies import (
    CATEGORY_SPIKE,
    LARGE_TRANSACTION,
    MISSING_RECURRING,
    NEW_MERCHANT,
    PRICE_INCREASE,
    detect_anomalies,
)
from tally.budgets import (
    BudgetConfigError,
    build_budget_report,
    evaluate_budgets,
    parse_budgets,
)
from tally.duplicates import (
    CROSS_FILE,
    SAME_FILE,
    build_duplicate_report,
    find_duplicates,
    normalize_description,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def txn(date, description, amount, merchant=None, category='Food',
        subcategory='Grocery', tags=None, source='Card', source_file='a.csv'):
    """Build a parsed-transaction dict of the shape the analyzer expects."""
    return {
        'date': datetime.strptime(date, '%Y-%m-%d'),
        'raw_description': description,
        'description': merchant or description,
        'merchant': merchant or description,
        'amount': amount,
        'category': category,
        'subcategory': subcategory,
        'source': source,
        'source_file': source_file,
        'tags': tags or [],
    }


def stats_for(transactions):
    return analyze_transactions(transactions)


# =============================================================================
# Budget parsing
# =============================================================================

class TestBudgetParsing:

    def test_no_budgets_returns_empty(self):
        assert parse_budgets({}) == []
        assert parse_budgets({'budgets': None}) == []

    def test_scalar_is_a_monthly_category_target(self):
        budget, = parse_budgets({'budgets': {'Food': 800}})
        assert budget.scope_type == 'category'
        assert budget.category == 'Food'
        assert budget.target == 800
        assert budget.period == 'monthly'

    def test_total_key_covers_all_spending(self):
        budget, = parse_budgets({'budgets': {'total': 5000}})
        assert budget.scope_type == 'total'
        assert budget.label == 'Total spending'

    def test_slash_key_is_a_subcategory(self):
        budget, = parse_budgets({'budgets': {'Food/Groceries': 500}})
        assert budget.scope_type == 'subcategory'
        assert budget.category == 'Food'
        assert budget.subcategory == 'Groceries'

    def test_tag_prefix_targets_a_tag(self):
        budget, = parse_budgets({'budgets': {'tag:Business': 400}})
        assert budget.scope_type == 'tag'
        assert budget.tag == 'business'
        assert budget.label == 'Tagged: business'

    def test_dict_form_supports_yearly_period(self):
        budget, = parse_budgets({'budgets': {'Travel': {'amount': 6000, 'period': 'yearly'}}})
        assert budget.period == 'yearly'
        assert budget.target == 6000

    @pytest.mark.parametrize('block,expected', [
        ({'Food': 'lots'}, 'must be a number'),
        ({'Food': -5}, 'must not be negative'),
        ({'Food': {'period': 'yearly'}}, "missing 'amount'"),
        ({'Food': {'amount': 100, 'period': 'fortnightly'}}, 'unknown period'),
        ({'tag:': 100}, 'missing a tag name'),
        ({'Food/': 100}, 'not a valid Category/Subcategory'),
    ])
    def test_malformed_budgets_explain_the_fix(self, block, expected):
        """Error messages must say what is wrong and show a valid example."""
        with pytest.raises(BudgetConfigError) as exc:
            parse_budgets({'budgets': block})
        message = str(exc.value)
        assert expected in message
        # Every message should carry a concrete example to copy.
        assert ':' in message and '\n' in message

    def test_budgets_must_be_a_mapping(self):
        with pytest.raises(BudgetConfigError) as exc:
            parse_budgets({'budgets': ['Food: 800']})
        assert 'mapping' in str(exc.value)


# =============================================================================
# Budget evaluation
# =============================================================================

class TestBudgetEvaluation:

    @pytest.fixture
    def two_month_stats(self):
        return stats_for([
            txn('2025-01-05', 'WHOLEFDS', 100, merchant='Whole Foods'),
            txn('2025-01-20', 'WHOLEFDS', 100, merchant='Whole Foods'),
            txn('2025-02-05', 'WHOLEFDS', 300, merchant='Whole Foods'),
            txn('2025-01-10', 'AMZN', 50, merchant='Amazon',
                category='Shopping', subcategory='Online'),
            txn('2025-02-10', 'AMZN', 50, merchant='Amazon',
                category='Shopping', subcategory='Online'),
        ])

    def test_category_actual_is_a_monthly_average(self, two_month_stats):
        budgets = parse_budgets({'budgets': {'Food': 200}})
        result, = evaluate_budgets(budgets, two_month_stats)
        # 500 of food across 2 months
        assert result.actual_total == 500
        assert result.actual_monthly_avg == 250
        assert result.status == 'over'
        assert result.variance == 50

    def test_partial_latest_month_is_excluded_from_the_average(self, two_month_stats):
        # Run mid-month, the newest month (Feb, 300) is only a few days in. It
        # must not drag the monthly average down and flatter the budget.
        budgets = parse_budgets({'budgets': {'Food': 200}})
        result, = evaluate_budgets(budgets, two_month_stats, latest_month_complete=False)
        # Feb still shows in the total/breakdown, but the average is Jan alone.
        assert result.actual_total == 500
        assert result.actual_by_month == {'2025-01': 200, '2025-02': 300}
        assert result.actual_monthly_avg == 200
        assert result.variance == 0

    def test_a_single_partial_month_still_averages_over_that_month(self):
        # With nothing to fall back on, a partial figure beats no figure.
        stats = stats_for([
            txn('2025-01-05', 'WHOLEFDS', 120, merchant='Whole Foods'),
        ])
        budgets = parse_budgets({'budgets': {'Food': 200}})
        result, = evaluate_budgets(budgets, stats, latest_month_complete=False)
        assert result.actual_monthly_avg == 120

    def test_months_over_counts_individual_months(self, two_month_stats):
        budgets = parse_budgets({'budgets': {'Food': 250}})
        result, = evaluate_budgets(budgets, two_month_stats)
        # Jan is 200 (under target), Feb is 300 (over target)
        assert result.actual_by_month == {'2025-01': 200, '2025-02': 300}
        assert result.months_over == 1

    def test_total_scope_covers_every_category(self, two_month_stats):
        budgets = parse_budgets({'budgets': {'total': 1000}})
        result, = evaluate_budgets(budgets, two_month_stats)
        assert result.actual_total == 600

    def test_subcategory_scope_is_narrower_than_category(self, two_month_stats):
        budgets = parse_budgets({'budgets': {'Food/Grocery': 100, 'Food/Dining': 100}})
        grocery = next(r for r in evaluate_budgets(budgets, two_month_stats)
                       if r.budget.subcategory == 'Grocery')
        dining = next(r for r in evaluate_budgets(budgets, two_month_stats)
                      if r.budget.subcategory == 'Dining')
        assert grocery.actual_total == 500
        assert dining.actual_total == 0

    def test_yearly_budget_compares_against_the_running_total(self):
        stats = stats_for([
            txn('2025-01-05', 'AIRLINE', 1000, merchant='Airline', category='Travel'),
            txn('2025-02-05', 'HOTEL', 500, merchant='Hotel', category='Travel'),
        ])
        budgets = parse_budgets({'budgets': {'Travel': {'amount': 2000, 'period': 'yearly'}}})
        result, = evaluate_budgets(budgets, stats)
        assert result.comparison_actual == 1500
        assert result.status == 'under'
        assert result.pct_used == pytest.approx(75.0)

    def test_income_and_transfers_never_count_as_spending(self):
        stats = stats_for([
            txn('2025-01-01', 'PAYCHECK', -5000, merchant='Salary',
                category='Income', tags=['income']),
            txn('2025-01-02', 'XFER', 1000, merchant='Savings',
                category='Transfers', tags=['transfer']),
            txn('2025-01-03', '401K', 500, merchant='401k',
                category='Transfers', tags=['investment']),
            txn('2025-01-04', 'WHOLEFDS', 100, merchant='Whole Foods'),
        ])
        budgets = parse_budgets({'budgets': {'total': 1000}})
        result, = evaluate_budgets(budgets, stats)
        assert result.actual_total == 100

    def test_refunds_reduce_the_month_they_land_in(self):
        stats = stats_for([
            txn('2025-01-05', 'AMZN', 200, merchant='Amazon', category='Shopping'),
            txn('2025-01-20', 'AMZN REFUND', -50, merchant='Amazon', category='Shopping'),
        ])
        budgets = parse_budgets({'budgets': {'Shopping': 100}})
        result, = evaluate_budgets(budgets, stats)
        assert result.actual_total == 150

    def test_status_thresholds(self):
        stats = stats_for([txn('2025-01-05', 'WHOLEFDS', 95, merchant='Whole Foods')])
        under, = evaluate_budgets(parse_budgets({'budgets': {'Food': 200}}), stats)
        near, = evaluate_budgets(parse_budgets({'budgets': {'Food': 100}}), stats)
        over, = evaluate_budgets(parse_budgets({'budgets': {'Food': 50}}), stats)
        assert (under.status, near.status, over.status) == ('under', 'near', 'over')

    def test_zero_target_is_breached_by_any_spending(self):
        """'Food: 0' means spend nothing, so $100 is over budget, not 0% used."""
        stats = stats_for([txn('2025-01-05', 'WHOLEFDS', 100, merchant='Whole Foods')])
        result, = evaluate_budgets(parse_budgets({'budgets': {'Food': 0}}), stats)
        assert result.status == 'over'
        assert result.pct_used == 100.0
        assert result.months_over == 1
        assert result.variance == 100

    def test_zero_target_with_no_spending_is_within_budget(self):
        stats = stats_for([
            txn('2025-01-05', 'PAYCHECK', -100, merchant='Salary',
                category='Income', tags=['income']),
        ])
        result, = evaluate_budgets(parse_budgets({'budgets': {'total': 0}}), stats)
        assert result.status == 'under'
        assert result.pct_used == 0.0

    def test_total_is_never_reported_as_a_typo(self):
        """'total' names no category, so an empty result is not a misspelling."""
        stats = stats_for([
            txn('2025-01-05', 'PAYCHECK', -100, merchant='Salary',
                category='Income', tags=['income']),
        ])
        report = build_budget_report({'budgets': {'total': 500}}, stats)
        assert report['problems'] == []

    def test_unmatched_budget_is_reported_with_a_suggestion(self):
        """A silent zero would read as perfect discipline instead of a typo."""
        stats = stats_for([txn('2025-01-05', 'WHOLEFDS', 100, merchant='Whole Foods')])
        report = build_budget_report({'budgets': {'Fooood': 200}}, stats)
        problem, = report['problems']
        assert problem['key'] == 'Fooood'
        assert 'Food' in problem['suggestions']

    def test_matched_budget_has_no_problem_entry(self):
        stats = stats_for([txn('2025-01-05', 'WHOLEFDS', 100, merchant='Whole Foods')])
        report = build_budget_report({'budgets': {'Food': 200}}, stats)
        assert report['problems'] == []

    def test_report_is_disabled_without_budgets(self):
        stats = stats_for([txn('2025-01-05', 'WHOLEFDS', 100, merchant='Whole Foods')])
        assert build_budget_report({}, stats)['enabled'] is False


# =============================================================================
# Anomaly detection
# =============================================================================

class TestAnomalies:

    def _kinds(self, report):
        return {a.kind for a in report['anomalies']}

    def _by_kind(self, report, kind):
        return [a for a in report['anomalies'] if a.kind == kind]

    def test_single_month_of_data_produces_nothing(self):
        """With no history there is nothing to compare against."""
        stats = stats_for([txn('2025-01-05', 'WHOLEFDS', 100, merchant='Whole Foods')])
        report = detect_anomalies(stats)
        assert report['enabled'] is False
        assert report['anomalies'] == []

    def test_price_increase_on_a_recurring_charge(self):
        stats = stats_for([
            txn('2025-01-05', 'NETFLIX', 15.99, merchant='Netflix', category='Subscriptions'),
            txn('2025-02-05', 'NETFLIX', 15.99, merchant='Netflix', category='Subscriptions'),
            txn('2025-03-05', 'NETFLIX', 15.99, merchant='Netflix', category='Subscriptions'),
            txn('2025-04-28', 'NETFLIX', 24.99, merchant='Netflix', category='Subscriptions'),
        ])
        found, = self._by_kind(detect_anomalies(stats), PRICE_INCREASE)
        assert found.subject == 'Netflix'
        assert found.impact == pytest.approx(9.0)

    def test_small_increases_are_ignored(self):
        stats = stats_for([
            txn('2025-01-05', 'NETFLIX', 15.99, merchant='Netflix'),
            txn('2025-02-05', 'NETFLIX', 15.99, merchant='Netflix'),
            txn('2025-03-05', 'NETFLIX', 15.99, merchant='Netflix'),
            txn('2025-04-28', 'NETFLIX', 17.99, merchant='Netflix'),
        ])
        assert PRICE_INCREASE not in self._kinds(detect_anomalies(stats))

    def test_new_merchant_in_the_latest_month(self):
        stats = stats_for([
            txn('2025-01-05', 'WHOLEFDS', 100, merchant='Whole Foods'),
            txn('2025-02-28', 'WHOLEFDS', 100, merchant='Whole Foods'),
            txn('2025-02-28', 'NEWGYM', 80, merchant='New Gym', category='Health'),
        ])
        found, = self._by_kind(detect_anomalies(stats), NEW_MERCHANT)
        assert found.subject == 'New Gym'

    def test_trivial_new_merchants_are_ignored(self):
        stats = stats_for([
            txn('2025-01-05', 'WHOLEFDS', 100, merchant='Whole Foods'),
            txn('2025-02-28', 'WHOLEFDS', 100, merchant='Whole Foods'),
            txn('2025-02-28', 'SNACK', 3, merchant='Snack Bar'),
        ])
        assert NEW_MERCHANT not in self._kinds(detect_anomalies(stats))

    def test_missing_recurring_charge_is_flagged(self):
        stats = stats_for([
            txn('2025-01-05', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-02-05', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-03-05', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-04-28', 'WHOLEFDS', 50, merchant='Whole Foods'),
        ])
        found, = self._by_kind(detect_anomalies(stats), MISSING_RECURRING)
        assert found.subject == 'Rent'
        assert found.impact == pytest.approx(2000)

    def test_partial_month_does_not_flag_a_charge_that_is_not_due_yet(self):
        """Rent billed on the 5th is not 'missing' when data stops on the 3rd."""
        stats = stats_for([
            txn('2025-01-25', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-02-25', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-03-25', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-04-03', 'WHOLEFDS', 50, merchant='Whole Foods'),
        ])
        report = detect_anomalies(stats)
        assert report['latest_month_complete'] is False
        assert MISSING_RECURRING not in self._kinds(report)

    def test_month_end_biller_is_not_missing_before_its_due_date(self):
        """A day-30 charge is not overdue just because data reached day 28.

        Day 28 makes the month look 'complete' in aggregate, but whether an
        individual charge is late depends on that merchant's own billing day.
        """
        stats = stats_for([
            txn('2025-01-30', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-02-28', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-03-30', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-04-28', 'WHOLEFDS', 50, merchant='Whole Foods'),
        ])
        report = detect_anomalies(stats)
        assert report['latest_month_complete'] is True
        assert MISSING_RECURRING not in self._kinds(report)

    def test_month_end_biller_is_still_flagged_once_the_month_runs_out(self):
        """A day-31 biller must not be suppressed forever in a 30-day month."""
        stats = stats_for([
            txn('2025-01-31', 'GYM', 80, merchant='Gym', category='Health'),
            txn('2025-02-28', 'GYM', 80, merchant='Gym', category='Health'),
            txn('2025-03-31', 'GYM', 80, merchant='Gym', category='Health'),
            txn('2025-04-30', 'WHOLEFDS', 50, merchant='Whole Foods'),
        ])
        found, = self._by_kind(detect_anomalies(stats), MISSING_RECURRING)
        assert found.subject == 'Gym'

    def test_early_month_biller_is_still_flagged_when_genuinely_missing(self):
        stats = stats_for([
            txn('2025-01-05', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-02-05', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-03-05', 'RENT', 2000, merchant='Rent', category='Housing'),
            txn('2025-04-28', 'WHOLEFDS', 50, merchant='Whole Foods'),
        ])
        found, = self._by_kind(detect_anomalies(stats), MISSING_RECURRING)
        assert found.subject == 'Rent'

    def test_category_spike_subject_is_a_category_not_a_merchant(self):
        """The report filters on this subject, so its scope must be knowable.

        A category_spike names a category; filtering it as a merchant would
        match nothing and blank the report.
        """
        stats = stats_for([
            txn('2025-01-05', 'WHOLEFDS', 200, merchant='Whole Foods'),
            txn('2025-02-05', 'WHOLEFDS', 200, merchant='Whole Foods'),
            txn('2025-03-05', 'WHOLEFDS', 200, merchant='Whole Foods'),
            txn('2025-04-28', 'WHOLEFDS', 900, merchant='Whole Foods'),
        ])
        spike, = self._by_kind(detect_anomalies(stats), CATEGORY_SPIKE)
        assert spike.subject == 'Food'
        assert spike.subject not in stats['by_merchant']

    def test_occasional_merchant_is_not_treated_as_missing(self):
        """Every-other-month shopping is not a cancelled subscription."""
        stats = stats_for([
            txn('2025-01-05', 'COSTCO', 200, merchant='Costco', category='Shopping'),
            txn('2025-03-05', 'COSTCO', 200, merchant='Costco', category='Shopping'),
            txn('2025-05-05', 'COSTCO', 200, merchant='Costco', category='Shopping'),
            txn('2025-06-28', 'WHOLEFDS', 50, merchant='Whole Foods'),
        ])
        assert MISSING_RECURRING not in self._kinds(detect_anomalies(stats))

    def test_category_spike(self):
        stats = stats_for([
            txn('2025-01-05', 'WHOLEFDS', 200, merchant='Whole Foods'),
            txn('2025-02-05', 'WHOLEFDS', 200, merchant='Whole Foods'),
            txn('2025-03-05', 'WHOLEFDS', 200, merchant='Whole Foods'),
            txn('2025-04-28', 'WHOLEFDS', 900, merchant='Whole Foods'),
        ])
        assert CATEGORY_SPIKE in self._kinds(detect_anomalies(stats))

    def test_large_transaction_against_a_merchant_norm(self):
        stats = stats_for([
            txn('2025-01-05', 'AMZN', 40, merchant='Amazon', category='Shopping'),
            txn('2025-02-05', 'AMZN', 45, merchant='Amazon', category='Shopping'),
            txn('2025-03-05', 'AMZN', 50, merchant='Amazon', category='Shopping'),
            txn('2025-03-18', 'AMZN', 900, merchant='Amazon', category='Shopping'),
        ])
        found, = self._by_kind(detect_anomalies(stats), LARGE_TRANSACTION)
        assert found.subject == 'Amazon'
        assert found.amount == pytest.approx(900)

    def test_warnings_sort_ahead_of_informational_items(self):
        stats = stats_for([
            txn('2025-01-05', 'NETFLIX', 10, merchant='Netflix', category='Subscriptions'),
            txn('2025-02-05', 'NETFLIX', 10, merchant='Netflix', category='Subscriptions'),
            txn('2025-03-05', 'NETFLIX', 10, merchant='Netflix', category='Subscriptions'),
            txn('2025-04-28', 'NETFLIX', 40, merchant='Netflix', category='Subscriptions'),
            txn('2025-04-28', 'NEWSHOP', 500, merchant='New Shop', category='Shopping'),
        ])
        severities = [a.severity for a in detect_anomalies(stats)['anomalies']]
        assert severities == sorted(severities, key=lambda s: s != 'warn')

    def test_currency_formatter_is_used_in_detail_text(self):
        stats = stats_for([
            txn('2025-01-05', 'NETFLIX', 10, merchant='Netflix'),
            txn('2025-02-05', 'NETFLIX', 10, merchant='Netflix'),
            txn('2025-03-05', 'NETFLIX', 10, merchant='Netflix'),
            txn('2025-04-28', 'NETFLIX', 40, merchant='Netflix'),
        ])
        report = detect_anomalies(stats, fmt=lambda v: f"{v:,.2f} zl")
        assert 'zl' in report['anomalies'][0].detail

    def test_limit_caps_the_list_but_reports_the_true_count(self):
        transactions = []
        for i in range(20):
            name = f"Shop {i}"
            for month in ('01', '02', '03'):
                transactions.append(txn(f'2025-{month}-05', name, 10, merchant=name,
                                        category='Shopping'))
            transactions.append(txn('2025-04-28', name, 100, merchant=name,
                                    category='Shopping'))
        report = detect_anomalies(stats_for(transactions), limit=5)
        assert len(report['anomalies']) == 5
        assert report['total_found'] > 5


# =============================================================================
# Duplicate detection
# =============================================================================

class TestDuplicates:

    def test_normalize_description_ignores_punctuation_and_case(self):
        assert normalize_description('SQ *Coffee-Shop') == normalize_description('sq coffee shop')

    def test_clean_data_reports_nothing(self):
        groups = find_duplicates([
            txn('2025-01-05', 'WHOLEFDS', 100),
            txn('2025-01-06', 'WHOLEFDS', 100),
        ])
        assert groups == []

    def test_same_amount_and_description_on_different_days_is_not_a_duplicate(self):
        groups = find_duplicates([
            txn('2025-01-05', 'NETFLIX', 15.99),
            txn('2025-02-05', 'NETFLIX', 15.99),
        ])
        assert groups == []

    def test_overlapping_exports_are_cross_file(self):
        groups = find_duplicates([
            txn('2025-01-05', 'WHOLEFDS', 100, source_file='jan.csv'),
            txn('2025-01-05', 'WHOLEFDS', 100, source_file='q1.csv'),
        ])
        group, = groups
        assert group.kind == CROSS_FILE
        assert group.count == 2
        assert group.impact == 100
        assert sorted(group.files) == ['jan.csv', 'q1.csv']

    def test_repeats_inside_one_file_are_reported_separately(self):
        """Two identical coffees on one day are usually real."""
        groups = find_duplicates([
            txn('2025-01-05', 'STARBUCKS', 5, source_file='jan.csv'),
            txn('2025-01-05', 'STARBUCKS', 5, source_file='jan.csv'),
        ])
        group, = groups
        assert group.kind == SAME_FILE

    def test_report_splits_kinds_and_totals_the_impact(self):
        report = build_duplicate_report([
            txn('2025-01-05', 'WHOLEFDS', 100, source_file='jan.csv'),
            txn('2025-01-05', 'WHOLEFDS', 100, source_file='q1.csv'),
            txn('2025-01-06', 'STARBUCKS', 5, source_file='jan.csv'),
            txn('2025-01-06', 'STARBUCKS', 5, source_file='jan.csv'),
        ])
        assert len(report['cross_file']) == 1
        assert len(report['same_file']) == 1
        assert report['cross_file_impact'] == 100
        assert report['same_file_impact'] == 5
        assert report['total_count'] == 2

    def test_three_copies_count_two_extras(self):
        report = build_duplicate_report([
            txn('2025-01-05', 'WHOLEFDS', 100, source_file=f'{n}.csv') for n in 'abc'
        ])
        group, = report['cross_file']
        assert group.count == 3
        assert group.impact == 200

    def test_detection_can_be_disabled(self):
        report = build_duplicate_report([
            txn('2025-01-05', 'WHOLEFDS', 100, source_file='jan.csv'),
            txn('2025-01-05', 'WHOLEFDS', 100, source_file='q1.csv'),
        ], enabled=False)
        assert report['enabled'] is False
        assert report['cross_file'] == []


# =============================================================================
# End-to-end wiring through the CLI
# =============================================================================

def _write_budget_project(tmp_path, extra_settings='', second_export=False):
    config_dir = tmp_path / 'config'
    data_dir = tmp_path / 'data'
    config_dir.mkdir()
    data_dir.mkdir()

    rows = [
        '01/05/2025,WHOLEFDS MKT,200.00',
        '02/05/2025,WHOLEFDS MKT,200.00',
        '03/05/2025,WHOLEFDS MKT,200.00',
        '04/28/2025,WHOLEFDS MKT,600.00',
    ]
    (data_dir / 'card.csv').write_text('Date,Description,Amount\n' + '\n'.join(rows) + '\n')

    sources = """  - name: Card
    file: data/card.csv
    format: "{date:%m/%d/%Y},{description},{amount}"
    has_header: true
"""
    if second_export:
        (data_dir / 'card-again.csv').write_text(
            'Date,Description,Amount\n' + rows[0] + '\n')
        sources += """  - name: Card Re-export
    file: data/card-again.csv
    format: "{date:%m/%d/%Y},{description},{amount}"
    has_header: true
"""

    (config_dir / 'settings.yaml').write_text(
        f"title: Test\n\ndata_sources:\n{sources}\n"
        f"merchants_file: config/merchants.rules\n{extra_settings}"
    )
    (config_dir / 'merchants.rules').write_text(
        '[Whole Foods]\nmatch: contains("WHOLEFDS")\ncategory: Food\nsubcategory: Grocery\n'
    )
    return config_dir


def run_tally(*args):
    return subprocess.run(['uv', 'run', 'tally', *args],
                          capture_output=True, text=True, cwd=REPO_ROOT)


class TestCliIntegration:

    def test_budgets_appear_in_the_summary(self, tmp_path):
        config_dir = _write_budget_project(tmp_path, 'budgets:\n  Food: 250\n')
        result = run_tally('up', '--config', str(config_dir), '--format', 'summary')
        assert result.returncode == 0, result.stderr
        assert 'BUDGETS' in result.stdout
        assert 'over budget' in result.stdout

    def test_budgets_appear_in_json(self, tmp_path):
        config_dir = _write_budget_project(tmp_path, 'budgets:\n  Food: 250\n')
        result = run_tally('up', '--config', str(config_dir), '--format', 'json')
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        target, = payload['budgets']['targets']
        assert target['label'] == 'Food'
        assert target['target'] == 250
        assert target['status'] == 'over'

    def test_no_budget_block_means_no_budget_output(self, tmp_path):
        config_dir = _write_budget_project(tmp_path)
        result = run_tally('up', '--config', str(config_dir), '--format', 'json')
        assert result.returncode == 0, result.stderr
        assert 'budgets' not in json.loads(result.stdout)

    def test_malformed_budget_block_fails_with_guidance(self, tmp_path):
        config_dir = _write_budget_project(tmp_path, 'budgets:\n  Food: not-a-number\n')
        result = run_tally('up', '--config', str(config_dir), '--format', 'summary')
        assert result.returncode == 1
        assert 'must be a number' in result.stderr
        assert 'tally diag' in result.stderr

    def test_overlapping_exports_warn(self, tmp_path):
        config_dir = _write_budget_project(tmp_path, second_export=True)
        result = run_tally('up', '--config', str(config_dir), '--format', 'summary')
        assert result.returncode == 0, result.stderr
        assert 'appear in more than one file' in result.stdout

    def test_duplicate_check_can_be_turned_off(self, tmp_path):
        config_dir = _write_budget_project(tmp_path, 'duplicate_check: false\n',
                                           second_export=True)
        result = run_tally('up', '--config', str(config_dir), '--format', 'summary')
        assert result.returncode == 0, result.stderr
        assert 'appear in more than one file' not in result.stdout

    def test_output_directory_is_created(self, tmp_path):
        """A missing --output directory used to raise a raw FileNotFoundError."""
        config_dir = _write_budget_project(tmp_path)
        target = tmp_path / 'nested' / 'deeper' / 'report.html'
        result = run_tally('up', '--config', str(config_dir), '-o', str(target), '--quiet')
        assert result.returncode == 0, result.stderr
        assert target.exists()

    def test_output_pointing_at_a_directory_explains_itself(self, tmp_path):
        config_dir = _write_budget_project(tmp_path)
        target = tmp_path / 'adirectory'
        target.mkdir()
        result = run_tally('up', '--config', str(config_dir), '-o', str(target), '--quiet')
        assert result.returncode == 1
        assert 'directory, not a file' in result.stderr

    def test_anomalies_reach_the_markdown_export(self, tmp_path):
        config_dir = _write_budget_project(tmp_path)
        result = run_tally('up', '--config', str(config_dir), '--format', 'markdown')
        assert result.returncode == 0, result.stderr
        assert 'Worth a Look' in result.stdout


# =============================================================================
# Credits reconciliation (regression)
# =============================================================================

class TestCreditsReconciliation:
    """A negative total is not automatically a refund.

    Transfers and income can also net negative; listing them under
    Credits/Refunds made the rows disagree with the reported total.
    """

    @pytest.fixture
    def mixed_stats(self):
        return stats_for([
            txn('2025-01-05', 'AMZN', 200, merchant='Amazon', category='Shopping'),
            txn('2025-01-20', 'AMZN REFUND', -50, merchant='Amazon', category='Shopping'),
            txn('2025-01-10', 'STOCK SALE', -10000, merchant='Stock Sale',
                category='Transfers', tags=['transfer']),
        ])

    def test_transfer_is_not_counted_as_a_credit(self, mixed_stats):
        assert mixed_stats['by_merchant']['Stock Sale']['credits'] == 0
        assert mixed_stats['credits_total'] == 50

    def test_merchant_credits_sum_to_the_reported_total(self, mixed_stats):
        per_merchant = sum(d['credits'] for d in mixed_stats['by_merchant'].values())
        assert per_merchant == pytest.approx(mixed_stats['credits_total'])

    def test_json_credits_list_matches_the_total(self, mixed_stats):
        from tally.analyzer import export_json
        payload = json.loads(export_json(mixed_stats))
        assert [c['merchant'] for c in payload['credits']] == ['Amazon']
        assert sum(c['amount'] for c in payload['credits']) == pytest.approx(
            payload['summary']['credits_total'])
