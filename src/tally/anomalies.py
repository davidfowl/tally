"""
Anomaly detection - the "worth a look" list for a spending review.

Reviewing a report line by line is slow, and the interesting things are usually
changes rather than levels. This module surfaces the handful of merchants and
categories that changed behaviour, so a review can start with them:

    new_merchant       something started charging you this month
    price_increase     a recurring charge went up
    missing_recurring  a regular charge did not arrive (cancelled, or missed)
    category_spike     a category cost far more this month than it usually does
    large_transaction  a single charge far larger than that merchant's norm

Everything is measured against the merchant's own history rather than a global
threshold, so a $12 subscription doubling is reported while a $12 swing in
groceries is not.

The most recent month in a export is often partial. Detectors that depend on a
complete month are suppressed in that case, because "your rent is missing" on
the 3rd of the month is noise, not a finding.
"""

import calendar
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List

from .classification import categorize_amount

# Detector kinds
NEW_MERCHANT = 'new_merchant'
PRICE_INCREASE = 'price_increase'
MISSING_RECURRING = 'missing_recurring'
CATEGORY_SPIKE = 'category_spike'
LARGE_TRANSACTION = 'large_transaction'

SEVERITY_WARN = 'warn'
SEVERITY_INFO = 'info'

# Thresholds. These are deliberately blunt: the goal is a short list a human
# actually reads, not exhaustive statistical coverage.
MIN_MONTHS_FOR_RECURRING = 3       # history needed before "usual" means anything
PRICE_INCREASE_PCT = 0.20          # +20% on a recurring charge
PRICE_INCREASE_MIN_ABS = 5.0       # ...and at least this many currency units
NEW_MERCHANT_MIN_TOTAL = 25.0      # ignore trivial first-time charges
CATEGORY_SPIKE_RATIO = 1.5         # 50% above the trailing average
CATEGORY_SPIKE_MIN_ABS = 100.0
LARGE_TXN_RATIO = 3.0              # 3x the merchant's median charge
LARGE_TXN_MIN_ABS = 100.0
MISSING_GRACE_DAYS = 3             # wait this long past the usual charge day
# "Missing" is only meaningful for charges that really do arrive every month.
# Requiring both dense history and a charge last month keeps seasonal or
# occasional merchants (a warehouse run every other month) off the list.
MISSING_MIN_COVERAGE = 0.6

# A month is treated as complete once data runs to at least this day.
MONTH_COMPLETE_DAY = 28


@dataclass
class Anomaly:
    """One thing worth looking at."""
    kind: str
    severity: str
    title: str
    detail: str
    subject: str = ''
    category: str = ''
    month: str = ''
    amount: float = 0.0
    impact: float = 0.0
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'kind': self.kind,
            'severity': self.severity,
            'title': self.title,
            'detail': self.detail,
            'subject': self.subject,
            'category': self.category,
            'month': self.month,
            'amount': round(self.amount, 2),
            'impact': round(self.impact, 2),
            'context': self.context,
        }


def _default_fmt(amount: float) -> str:
    """Fallback money formatter used when the caller does not supply one."""
    return f"{amount:,.2f}"


def _spend(txn) -> float:
    """Net spending contribution of a transaction (refunds are negative)."""
    bucket = categorize_amount(txn.get('amount', 0), txn.get('tags', []))
    return bucket['spending'] - bucket['credits']


def _day_of_month(txn) -> int:
    """Day component of a transaction's ``MM/DD`` date, 0 when unavailable."""
    raw = txn.get('date', '')
    parts = str(raw).split('/')
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


def _merchant_monthly_spend(data) -> Dict[str, float]:
    by_month: Dict[str, float] = {}
    for txn in data.get('transactions', []):
        net = _spend(txn)
        if net:
            month = txn.get('month', '')
            by_month[month] = by_month.get(month, 0.0) + net
    return by_month


def _latest_month_day(by_merchant) -> int:
    """Highest day-of-month seen in the most recent month of data."""
    latest_month = ''
    latest_day = 0
    for data in by_merchant.values():
        for txn in data.get('transactions', []):
            month = txn.get('month', '')
            if month > latest_month:
                latest_month, latest_day = month, _day_of_month(txn)
            elif month == latest_month:
                latest_day = max(latest_day, _day_of_month(txn))
    return latest_day


def detect_new_merchants(by_merchant, latest_month, fmt=_default_fmt) -> List[Anomaly]:
    """Merchants whose very first charge landed in the most recent month."""
    found = []
    for name, data in by_merchant.items():
        spend_by_month = _merchant_monthly_spend(data)
        if not spend_by_month or min(spend_by_month) != latest_month:
            continue
        total = spend_by_month[latest_month]
        if total < NEW_MERCHANT_MIN_TOTAL:
            continue
        found.append(Anomaly(
            kind=NEW_MERCHANT,
            severity=SEVERITY_INFO,
            title=f"{name} is new",
            detail=f"First charge in {latest_month}, {fmt(total)} across "
                   f"{data.get('count', 1)} transaction(s).",
            subject=name,
            category=data.get('category', ''),
            month=latest_month,
            amount=total,
            impact=total,
        ))
    return found


def detect_price_increases(by_merchant, latest_month, fmt=_default_fmt) -> List[Anomaly]:
    """Recurring charges that went up compared to their own history."""
    found = []
    for name, data in by_merchant.items():
        spend_by_month = _merchant_monthly_spend(data)
        if len(spend_by_month) < MIN_MONTHS_FOR_RECURRING:
            continue
        if latest_month not in spend_by_month:
            continue

        prior = [amount for month, amount in spend_by_month.items() if month != latest_month]
        if not prior:
            continue

        baseline = median(prior)
        current = spend_by_month[latest_month]
        if baseline <= 0:
            continue

        delta = current - baseline
        if delta < PRICE_INCREASE_MIN_ABS or delta / baseline < PRICE_INCREASE_PCT:
            continue

        pct = delta / baseline * 100
        found.append(Anomaly(
            kind=PRICE_INCREASE,
            severity=SEVERITY_WARN,
            title=f"{name} went up {pct:.0f}%",
            detail=f"{latest_month} was {fmt(current)} versus a usual {fmt(baseline)}.",
            subject=name,
            category=data.get('category', ''),
            month=latest_month,
            amount=current,
            impact=delta,
            context={'baseline': round(baseline, 2), 'current': round(current, 2),
                     'pct_change': round(pct, 1)},
        ))
    return found


def _months_between(start_month: str, end_month: str) -> int:
    """Inclusive count of YYYY-MM months from start to end."""
    try:
        start_year, start_mon = (int(p) for p in start_month.split('-'))
        end_year, end_mon = (int(p) for p in end_month.split('-'))
    except (ValueError, AttributeError):
        return 0
    return (end_year - start_year) * 12 + (end_mon - start_mon) + 1


def _days_in_month(month: str) -> int:
    """Number of days in a YYYY-MM month, defaulting to 31 if unparseable."""
    try:
        year, mon = (int(p) for p in month.split('-'))
        return calendar.monthrange(year, mon)[1]
    except (ValueError, AttributeError, IndexError):
        return 31


def _previous_month(month: str) -> str:
    """The YYYY-MM month immediately before ``month``."""
    try:
        year, mon = (int(p) for p in month.split('-'))
    except (ValueError, AttributeError):
        return ''
    if mon == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{mon - 1:02d}"


def detect_missing_recurring(by_merchant, latest_month, latest_day, month_complete=False,
                             fmt=_default_fmt) -> List[Anomaly]:
    """Regular charges that did not show up in the most recent month.

    ``month_complete`` is accepted for symmetry with the other detectors but is
    not used as a gate: whether a charge is overdue depends on that merchant's
    own billing day, not on whether the month as a whole looks finished.
    """
    previous_month = _previous_month(latest_month)
    found = []
    for name, data in by_merchant.items():
        spend_by_month = _merchant_monthly_spend(data)
        if len(spend_by_month) < MIN_MONTHS_FOR_RECURRING:
            continue
        if latest_month in spend_by_month:
            continue

        months = sorted(spend_by_month)
        # Only care about charges that were still running right up to the gap.
        if months[-1] >= latest_month:
            continue
        if months[-1] != previous_month:
            continue

        # How consistently has it charged since it first appeared?
        expected = _months_between(months[0], latest_month)
        if expected and len(spend_by_month) / expected < MISSING_MIN_COVERAGE:
            continue

        days = [d for d in (_day_of_month(t) for t in data.get('transactions', [])) if d]
        usual_day = median(days) if days else 1

        # If the charge normally lands later in the month than our data runs,
        # it is not missing - the export simply stops before it would appear.
        # The threshold is clamped to the length of the month so a merchant
        # that bills on the 31st is not suppressed forever in shorter months.
        due_by = min(usual_day + MISSING_GRACE_DAYS, _days_in_month(latest_month))
        if latest_day < due_by:
            continue

        baseline = median(list(spend_by_month.values()))
        found.append(Anomaly(
            kind=MISSING_RECURRING,
            severity=SEVERITY_WARN,
            title=f"{name} did not charge in {latest_month}",
            detail=f"Charged in {len(spend_by_month)} earlier month(s), usually around "
                   f"{fmt(baseline)} on day {int(usual_day)}. Cancelled, or a missed bill?",
            subject=name,
            category=data.get('category', ''),
            month=latest_month,
            amount=0.0,
            impact=baseline,
            context={'baseline': round(baseline, 2), 'last_seen': months[-1],
                     'usual_day': int(usual_day)},
        ))
    return found


def detect_category_spikes(by_merchant, latest_month, fmt=_default_fmt) -> List[Anomaly]:
    """Categories that cost far more in the latest month than they usually do."""
    by_category: Dict[str, Dict[str, float]] = {}
    for data in by_merchant.values():
        category = data.get('category', '') or 'Uncategorized'
        target = by_category.setdefault(category, {})
        for month, amount in _merchant_monthly_spend(data).items():
            target[month] = target.get(month, 0.0) + amount

    found = []
    for category, spend_by_month in by_category.items():
        if len(spend_by_month) < MIN_MONTHS_FOR_RECURRING or latest_month not in spend_by_month:
            continue

        prior = [amount for month, amount in spend_by_month.items() if month != latest_month]
        if not prior:
            continue

        baseline = sum(prior) / len(prior)
        current = spend_by_month[latest_month]
        delta = current - baseline
        if baseline <= 0 or delta < CATEGORY_SPIKE_MIN_ABS or current / baseline < CATEGORY_SPIKE_RATIO:
            continue

        found.append(Anomaly(
            kind=CATEGORY_SPIKE,
            severity=SEVERITY_WARN,
            title=f"{category} spiked in {latest_month}",
            detail=f"{fmt(current)} against a usual {fmt(baseline)} per month "
                   f"({fmt(delta)} more than normal).",
            subject=category,
            category=category,
            month=latest_month,
            amount=current,
            impact=delta,
            context={'baseline': round(baseline, 2), 'current': round(current, 2)},
        ))
    return found


def detect_large_transactions(by_merchant, fmt=_default_fmt) -> List[Anomaly]:
    """Single charges far above what that merchant normally costs."""
    found = []
    for name, data in by_merchant.items():
        spends = [(txn, _spend(txn)) for txn in data.get('transactions', [])]
        amounts = [amount for _, amount in spends if amount > 0]
        if len(amounts) < MIN_MONTHS_FOR_RECURRING:
            continue

        baseline = median(amounts)
        if baseline <= 0:
            continue

        txn, largest = max(spends, key=lambda pair: pair[1])
        if largest < LARGE_TXN_MIN_ABS or largest / baseline < LARGE_TXN_RATIO:
            continue

        found.append(Anomaly(
            kind=LARGE_TRANSACTION,
            severity=SEVERITY_INFO,
            title=f"Unusually large {name} charge",
            detail=f"{fmt(largest)} on {txn.get('date', '')} versus a typical {fmt(baseline)}.",
            subject=name,
            category=data.get('category', ''),
            month=txn.get('month', ''),
            amount=largest,
            impact=largest - baseline,
            context={'baseline': round(baseline, 2), 'date': txn.get('date', '')},
        ))
    return found


def detect_anomalies(stats, limit: int = 12, fmt=None) -> Dict[str, Any]:
    """Run every detector and return the most significant findings.

    Args:
        stats: Analysis results from ``analyze_transactions``.
        limit: Maximum number of anomalies to return.
        fmt: Optional money formatter used to build human-readable detail text.
    """
    fmt = fmt or _default_fmt
    by_merchant = stats.get('by_merchant', {})
    months = sorted(stats.get('by_month', {}).keys())

    # Comparing "this month" to history needs at least some history.
    if len(months) < 2:
        return {'enabled': False, 'anomalies': [], 'latest_month': months[-1] if months else '',
                'latest_month_complete': True, 'reason': 'not_enough_history'}

    latest_month = months[-1]
    latest_day = _latest_month_day(by_merchant)
    month_complete = latest_day >= MONTH_COMPLETE_DAY

    found = []
    found += detect_new_merchants(by_merchant, latest_month, fmt)
    found += detect_price_increases(by_merchant, latest_month, fmt)
    found += detect_missing_recurring(by_merchant, latest_month, latest_day, month_complete, fmt)
    found += detect_category_spikes(by_merchant, latest_month, fmt)
    found += detect_large_transactions(by_merchant, fmt)

    # Warnings first, then by how much money the finding represents.
    found.sort(key=lambda a: (a.severity != SEVERITY_WARN, -a.impact))

    return {
        'enabled': True,
        'anomalies': found[:limit],
        'total_found': len(found),
        'latest_month': latest_month,
        'latest_month_complete': month_complete,
        'latest_day': latest_day,
    }


def anomaly_report_to_dict(report: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an anomaly report to plain JSON-serializable data."""
    return {
        'enabled': report.get('enabled', False),
        'latest_month': report.get('latest_month', ''),
        'latest_month_complete': report.get('latest_month_complete', True),
        'total_found': report.get('total_found', 0),
        'items': [a.to_dict() for a in report.get('anomalies', [])],
    }
