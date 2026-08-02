"""
Budget targets - compare what you planned to spend against what you did spend.

Budgets are declared in settings.yaml and are entirely optional:

    budgets:
      total: 5000                             # all spending, per month
      Food: 800                               # a category, per month
      Food/Groceries: 500                     # a category/subcategory
      tag:business: 400                       # everything tagged 'business'
      Travel:                                 # an annual pot rather than monthly
        amount: 6000
        period: yearly

Only real spending counts toward a budget. Income, transfers and investment
contributions are excluded, and refunds reduce the spend for the month they
land in, so the numbers reconcile with the spending total in the report.
"""

import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .classification import categorize_amount

# Budget scope types
SCOPE_TOTAL = 'total'
SCOPE_CATEGORY = 'category'
SCOPE_SUBCATEGORY = 'subcategory'
SCOPE_TAG = 'tag'

# Period types
PERIOD_MONTHLY = 'monthly'
PERIOD_YEARLY = 'yearly'
VALID_PERIODS = (PERIOD_MONTHLY, PERIOD_YEARLY)

# A budget within this fraction of its target is flagged as 'near' rather than
# 'under', so the report can warn before the limit is actually breached.
NEAR_THRESHOLD = 0.9

TOTAL_KEY = 'total'
TAG_PREFIX = 'tag:'


class BudgetConfigError(ValueError):
    """Raised when the budgets block in settings.yaml cannot be understood."""


@dataclass
class Budget:
    """A single parsed budget target."""
    key: str
    scope_type: str
    target: float
    period: str = PERIOD_MONTHLY
    category: Optional[str] = None
    subcategory: Optional[str] = None
    tag: Optional[str] = None

    @property
    def label(self) -> str:
        if self.scope_type == SCOPE_TOTAL:
            return 'Total spending'
        if self.scope_type == SCOPE_TAG:
            return f"Tagged: {self.tag}"
        if self.scope_type == SCOPE_SUBCATEGORY:
            return f"{self.category}/{self.subcategory}"
        return self.category or self.key

    def matches(self, category: str, subcategory: str, tags) -> bool:
        """Does a merchant fall inside this budget's scope?"""
        if self.scope_type == SCOPE_TOTAL:
            return True
        if self.scope_type == SCOPE_TAG:
            return self.tag in {str(t).lower() for t in (tags or [])}
        if (category or '').lower() != (self.category or '').lower():
            return False
        if self.scope_type == SCOPE_SUBCATEGORY:
            return (subcategory or '').lower() == (self.subcategory or '').lower()
        return True


@dataclass
class BudgetResult:
    """A budget evaluated against actual spending."""
    budget: Budget
    actual_total: float
    actual_by_month: Dict[str, float] = field(default_factory=dict)
    num_months: int = 0
    matched_merchants: int = 0
    # The latest month when it is only partially covered by the data. It is
    # kept in ``actual_by_month`` and ``actual_total`` for display, but excluded
    # from the monthly average so a mid-month review is not flattered by the few
    # days of spend that have landed so far.
    excluded_month: str = ''

    @property
    def key(self) -> str:
        return self.budget.key

    @property
    def label(self) -> str:
        return self.budget.label

    @property
    def period(self) -> str:
        return self.budget.period

    @property
    def target(self) -> float:
        """Target for the period the budget is expressed in."""
        return self.budget.target

    @property
    def target_for_range(self) -> float:
        """Target scaled to the date range actually present in the data."""
        if self.budget.period == PERIOD_YEARLY:
            return self.budget.target * (self.num_months / 12) if self.num_months else self.budget.target
        return self.budget.target * self.num_months

    @property
    def actual_monthly_avg(self) -> float:
        months = self._avg_month_count
        return self._avg_total / months if months else 0.0

    @property
    def _avg_month_count(self) -> int:
        """Months the monthly average is spread over.

        A partial latest month is dropped so it does not drag the average down,
        unless it is the only month we have (in which case a partial figure is
        better than nothing).
        """
        if self.excluded_month and self.num_months > 1:
            return self.num_months - 1
        return self.num_months

    @property
    def _avg_total(self) -> float:
        """Spend the monthly average is computed from (partial month removed)."""
        if self.excluded_month and self.num_months > 1:
            return self.actual_total - self.actual_by_month.get(self.excluded_month, 0.0)
        return self.actual_total

    @property
    def comparison_actual(self) -> float:
        """The actual figure compared against ``target``."""
        if self.budget.period == PERIOD_YEARLY:
            return self.actual_total
        return self.actual_monthly_avg

    @property
    def variance(self) -> float:
        """Positive means over budget."""
        return self.comparison_actual - self.target

    @property
    def pct_used(self) -> float:
        if self.target <= 0:
            # A zero target cannot be divided into, but any spend against it is
            # a full breach rather than 0% used.
            return 100.0 if self.comparison_actual > 0 else 0.0
        return self.comparison_actual / self.target * 100

    @property
    def status(self) -> str:
        if self.target <= 0:
            return 'over' if self.comparison_actual > 0 else 'under'
        ratio = self.comparison_actual / self.target
        if ratio > 1:
            return 'over'
        if ratio >= NEAR_THRESHOLD:
            return 'near'
        return 'under'

    @property
    def months_over(self) -> int:
        """Months whose spend exceeded a monthly target (monthly budgets only)."""
        if self.budget.period != PERIOD_MONTHLY:
            return 0
        return sum(1 for amount in self.actual_by_month.values() if amount > self.target)

    @property
    def worst_month(self):
        if not self.actual_by_month:
            return None
        month, amount = max(self.actual_by_month.items(), key=lambda kv: kv[1])
        return {'month': month, 'amount': round(amount, 2)}

    @property
    def latest_month(self):
        if not self.actual_by_month:
            return None
        month = max(self.actual_by_month)
        return {'month': month, 'amount': round(self.actual_by_month[month], 2)}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'key': self.key,
            'label': self.label,
            'scope': self.budget.scope_type,
            'period': self.period,
            'target': round(self.target, 2),
            'target_for_range': round(self.target_for_range, 2),
            'actual_total': round(self.actual_total, 2),
            'actual_monthly_avg': round(self.actual_monthly_avg, 2),
            'variance': round(self.variance, 2),
            'pct_used': round(self.pct_used, 1),
            'status': self.status,
            'months_over': self.months_over,
            'num_months': self.num_months,
            'matched_merchants': self.matched_merchants,
            'by_month': {m: round(v, 2) for m, v in sorted(self.actual_by_month.items())},
            'worst_month': self.worst_month,
            'latest_month': self.latest_month,
        }


def _parse_target(key: str, raw: Any) -> Budget:
    """Parse one ``key: value`` pair from the budgets block."""
    period = PERIOD_MONTHLY

    if isinstance(raw, dict):
        if 'amount' not in raw:
            raise BudgetConfigError(
                f"Budget '{key}' is missing 'amount'.\n"
                f"  Use either:\n"
                f"    {key}: 500\n"
                f"  or:\n"
                f"    {key}:\n"
                f"      amount: 500\n"
                f"      period: yearly"
            )
        amount = raw['amount']
        period = str(raw.get('period', PERIOD_MONTHLY)).lower()
        if period not in VALID_PERIODS:
            raise BudgetConfigError(
                f"Budget '{key}' has an unknown period '{raw.get('period')}'.\n"
                f"  Use one of: {', '.join(VALID_PERIODS)}"
            )
    else:
        amount = raw

    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise BudgetConfigError(
            f"Budget '{key}' must be a number, got: {amount!r}\n"
            f"  Example:\n"
            f"    {key}: 500"
        )
    if amount < 0:
        raise BudgetConfigError(
            f"Budget '{key}' must not be negative, got: {amount}\n"
            f"  Budgets are spending limits, so use a positive number."
        )

    if key.lower() == TOTAL_KEY:
        return Budget(key=key, scope_type=SCOPE_TOTAL, target=float(amount), period=period)

    if key.lower().startswith(TAG_PREFIX):
        tag = key[len(TAG_PREFIX):].strip().lower()
        if not tag:
            raise BudgetConfigError(
                f"Budget '{key}' is missing a tag name.\n"
                f"  Example:\n"
                f"    tag:business: 400"
            )
        return Budget(key=key, scope_type=SCOPE_TAG, target=float(amount), period=period, tag=tag)

    if '/' in key:
        category, _, subcategory = key.partition('/')
        category, subcategory = category.strip(), subcategory.strip()
        if not category or not subcategory:
            raise BudgetConfigError(
                f"Budget '{key}' is not a valid Category/Subcategory.\n"
                f"  Example:\n"
                f"    Food/Groceries: 500"
            )
        return Budget(key=key, scope_type=SCOPE_SUBCATEGORY, target=float(amount),
                      period=period, category=category, subcategory=subcategory)

    return Budget(key=key, scope_type=SCOPE_CATEGORY, target=float(amount),
                  period=period, category=key.strip())


def parse_budgets(config) -> List[Budget]:
    """Parse the optional ``budgets`` block from settings.

    Raises:
        BudgetConfigError: with an actionable message if the block is malformed.
    """
    raw = (config or {}).get('budgets')
    if not raw:
        return []

    if not isinstance(raw, dict):
        raise BudgetConfigError(
            "Setting 'budgets' must be a mapping of target names to amounts.\n"
            "  Example:\n"
            "    budgets:\n"
            "      total: 5000\n"
            "      Food: 800\n"
            "      Food/Groceries: 500"
        )

    return [_parse_target(str(key), value) for key, value in raw.items()]


def _merchant_spend_by_month(data) -> Dict[str, float]:
    """Net spending per month for one merchant (purchases minus refunds)."""
    by_month: Dict[str, float] = {}
    for txn in data.get('transactions', []):
        bucket = categorize_amount(txn.get('amount', 0), txn.get('tags', []))
        net = bucket['spending'] - bucket['credits']
        if net:
            month = txn.get('month', '')
            by_month[month] = by_month.get(month, 0.0) + net
    return by_month


def evaluate_budgets(budgets: List[Budget], stats, latest_month_complete: bool = True) -> List[BudgetResult]:
    """Evaluate parsed budgets against analysis results.

    When ``latest_month_complete`` is False the most recent month is treated as
    partial and excluded from each budget's monthly average, so a review run
    mid-month is not misled into thinking spending is under target.
    """
    if not budgets:
        return []

    by_merchant = stats.get('by_merchant', {})
    all_months = sorted(stats.get('by_month', {}).keys())
    num_months = len(all_months) or stats.get('num_months', 0)
    excluded_month = all_months[-1] if (all_months and not latest_month_complete) else ''

    # Pre-compute each merchant's monthly spend once, then fan out to budgets.
    merchant_spend = []
    for name, data in by_merchant.items():
        merchant_spend.append((
            data.get('category', ''),
            data.get('subcategory', ''),
            data.get('tags', set()),
            _merchant_spend_by_month(data),
        ))

    results = []
    for budget in budgets:
        actual_by_month: Dict[str, float] = {month: 0.0 for month in all_months}
        matched = 0
        for category, subcategory, tags, spend_by_month in merchant_spend:
            if not budget.matches(category, subcategory, tags):
                continue
            if spend_by_month:
                matched += 1
            for month, amount in spend_by_month.items():
                actual_by_month[month] = actual_by_month.get(month, 0.0) + amount

        results.append(BudgetResult(
            budget=budget,
            actual_total=sum(actual_by_month.values()),
            actual_by_month=actual_by_month,
            num_months=num_months,
            matched_merchants=matched,
            excluded_month=excluded_month,
        ))

    # Worst overspend first so a review starts with the problems.
    results.sort(key=lambda r: (-r.pct_used, r.label))
    return results


def find_unmatched_budgets(results: List[BudgetResult], stats) -> List[Dict[str, Any]]:
    """Flag budgets that matched nothing, with a suggestion for what to use.

    A silent zero is the worst outcome for a budget review, because it looks
    like perfect discipline when it actually means the name was wrong.
    """
    by_merchant = stats.get('by_merchant', {})
    categories = sorted({d.get('category', '') for d in by_merchant.values() if d.get('category')})
    subcategories = sorted({
        f"{d.get('category', '')}/{d.get('subcategory', '')}"
        for d in by_merchant.values() if d.get('category') and d.get('subcategory')
    })
    tags = sorted({str(t).lower() for d in by_merchant.values() for t in (d.get('tags') or set())})

    problems = []
    for result in results:
        if result.matched_merchants:
            continue

        budget = result.budget
        if budget.scope_type == SCOPE_TOTAL:
            # Nothing to correct: 'total' names no category or tag, so an empty
            # result means there was no spending, not a typo.
            continue

        if budget.scope_type == SCOPE_TAG:
            candidates, prefix = tags, TAG_PREFIX
        elif budget.scope_type == SCOPE_SUBCATEGORY:
            candidates, prefix = subcategories, ''
        else:
            candidates, prefix = categories, ''

        lookup = budget.tag if budget.scope_type == SCOPE_TAG else budget.key
        close = difflib.get_close_matches(lookup, candidates, n=3, cutoff=0.6)
        suggestion = close or candidates[:5]

        problems.append({
            'key': budget.key,
            'scope': budget.scope_type,
            'suggestions': [f"{prefix}{c}" for c in suggestion],
        })

    return problems


def build_budget_report(config, stats, latest_month_complete: bool = True) -> Dict[str, Any]:
    """Parse and evaluate budgets, returning everything the outputs need.

    ``latest_month_complete`` is forwarded to :func:`evaluate_budgets` so a
    partial final month does not deflate the monthly averages.
    """
    budgets = parse_budgets(config)
    if not budgets:
        return {'enabled': False, 'results': [], 'problems': []}

    results = evaluate_budgets(budgets, stats, latest_month_complete=latest_month_complete)
    return {
        'enabled': True,
        'results': results,
        'problems': find_unmatched_budgets(results, stats),
    }


def budget_report_to_dict(report: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a budget report to plain JSON-serializable data."""
    results = report.get('results', [])
    return {
        'enabled': report.get('enabled', False),
        'over_count': sum(1 for r in results if r.status == 'over'),
        'targets': [r.to_dict() for r in results],
        'problems': report.get('problems', []),
    }
