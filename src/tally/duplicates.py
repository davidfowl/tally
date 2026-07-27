"""
Duplicate transaction detection.

Tally concatenates every configured data source into one transaction list. That
is usually what you want, but it means overlapping exports silently double
count. This is easy to hit once a source uses a folder or glob pattern:

    data_sources:
      - name: Checking
        file: data/exports/*.csv     # 2025-01.csv and 2025-Q1.csv both have Jan

Detection is deliberately conservative. A duplicate is only reported when the
date, the amount and the normalized description all match exactly, because a
false alarm during a budget review is worse than a missed one.

Two kinds are reported separately:

    cross_file  Same transaction found in more than one file. Almost always an
                overlapping export, so this is warned about by default.
    same_file   The same transaction repeated inside a single file. Often
                legitimate (two identical coffees on one day), so this is only
                shown on request.
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List

# Kinds of duplicate groups
CROSS_FILE = 'cross_file'
SAME_FILE = 'same_file'

_NON_ALNUM = re.compile(r'[^A-Z0-9]+')


def normalize_description(description: str) -> str:
    """Normalize a description for duplicate comparison.

    Upper-cases and strips punctuation/whitespace so that trivial formatting
    differences between two exports of the same transaction still match.
    """
    if not description:
        return ''
    return _NON_ALNUM.sub(' ', description.upper()).strip()


@dataclass
class DuplicateGroup:
    """A set of transactions that look like the same real-world transaction."""
    date: str
    amount: float
    description: str
    kind: str
    count: int
    sources: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    merchant: str = ''

    @property
    def impact(self) -> float:
        """Absolute amount that is double counted by this group."""
        return abs(self.amount) * (self.count - 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date,
            'amount': round(self.amount, 2),
            'description': self.description,
            'merchant': self.merchant,
            'kind': self.kind,
            'count': self.count,
            'sources': self.sources,
            'files': [os.path.basename(f) for f in self.files],
            'impact': round(self.impact, 2),
        }


def _transaction_key(txn):
    date = txn.get('date')
    date_key = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
    description = txn.get('raw_description') or txn.get('description') or ''
    return date_key, round(float(txn.get('amount', 0) or 0), 2), normalize_description(description)


def find_duplicates(transactions) -> List[DuplicateGroup]:
    """Find groups of transactions that appear to be duplicates.

    Args:
        transactions: Parsed transaction dicts. ``source_file`` is used to tell
            cross-file duplicates from repeats within one file; when it is
            missing the source name is used instead.

    Returns:
        List of DuplicateGroup, cross-file first, then by descending impact.
    """
    groups = defaultdict(list)
    for txn in transactions:
        groups[_transaction_key(txn)].append(txn)

    results = []
    for (date_key, amount, _), txns in groups.items():
        if len(txns) < 2:
            continue

        files = []
        sources = []
        for txn in txns:
            source_file = txn.get('source_file') or txn.get('source') or ''
            if source_file not in files:
                files.append(source_file)
            source = txn.get('source') or ''
            if source and source not in sources:
                sources.append(source)

        kind = CROSS_FILE if len(files) > 1 else SAME_FILE
        first = txns[0]
        results.append(DuplicateGroup(
            date=date_key,
            amount=amount,
            description=first.get('raw_description') or first.get('description') or '',
            kind=kind,
            count=len(txns),
            sources=sources,
            files=files,
            merchant=first.get('merchant', ''),
        ))

    results.sort(key=lambda g: (g.kind != CROSS_FILE, -g.impact, g.date))
    return results


def build_duplicate_report(transactions, enabled: bool = True) -> Dict[str, Any]:
    """Build the duplicate summary consumed by the CLI, JSON export and report.

    Returns a dict with ``cross_file`` and ``same_file`` group lists plus the
    total double-counted amount. Returns an empty (disabled) report when
    detection is turned off so callers do not need to special-case it.
    """
    if not enabled:
        return {'enabled': False, 'cross_file': [], 'same_file': [],
                'cross_file_impact': 0.0, 'same_file_impact': 0.0, 'total_count': 0}

    groups = find_duplicates(transactions)
    cross = [g for g in groups if g.kind == CROSS_FILE]
    same = [g for g in groups if g.kind == SAME_FILE]

    return {
        'enabled': True,
        'cross_file': cross,
        'same_file': same,
        'cross_file_impact': sum(g.impact for g in cross),
        'same_file_impact': sum(g.impact for g in same),
        'total_count': sum(g.count - 1 for g in groups),
    }


def duplicate_report_to_dict(report: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a duplicate report to plain JSON-serializable data."""
    return {
        'enabled': report.get('enabled', False),
        'cross_file': [g.to_dict() for g in report.get('cross_file', [])],
        'same_file': [g.to_dict() for g in report.get('same_file', [])],
        'cross_file_impact': round(report.get('cross_file_impact', 0.0), 2),
        'same_file_impact': round(report.get('same_file_impact', 0.0), 2),
        'total_count': report.get('total_count', 0),
    }
