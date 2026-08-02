"""
Transaction Analyzer - Core analysis logic.

Analyzes transactions using merchant categorization rules.
"""

import json
import os
from collections import defaultdict
from datetime import datetime

from . import section_engine
from .colors import C
from .classification import (
    categorize_amount,
    normalize_amount,
    calculate_cash_flow,
    calculate_transfers_net,
)

# Import parsing functions from parsers module (and re-export for backwards compatibility)
from .parsers import (
    parse_amount,
    parse_amex,
    parse_boa,
    parse_generic_csv,
    auto_detect_csv_format,
    _iter_rows_with_delimiter,
)

# Import report generation from report module (and re-export for backwards compatibility)
from .report import (
    get_template_dir,
    generate_embeddings,
    write_summary_file_vue,
    format_currency,
    format_currency_decimal,
    format_currency_signed,
    EMBEDDINGS_AVAILABLE,
)


# ============================================================================


def analyze_transactions(transactions):
    """Analyze transactions and return summary statistics."""
    by_category = defaultdict(lambda: {'count': 0, 'total': 0})
    by_merchant = defaultdict(lambda: {
        'count': 0,
        'total': 0,
        'category': '',
        'subcategory': '',
        'months': set(),  # Track which months this merchant appears
        'monthly_amounts': defaultdict(float),  # Amount per month
        'max_payment': 0,  # Largest single payment
        'payments': [],  # All individual payment amounts
        'transactions': [],  # Individual transactions for drill-down
        'tags': set(),  # Collect all tags from matching rules
        'raw_descriptions': defaultdict(int),  # Track raw description variations
        # Per-merchant money-flow buckets, using the same rules as the report
        # totals so merchant-level output always reconciles with the headline
        # numbers (a transfer with a negative amount is not a refund).
        'spending': 0.0,
        'credits': 0.0,
        'income': 0.0,
        'investment': 0.0,
        'transfer_in': 0.0,
        'transfer_out': 0.0,
    })
    by_month = defaultdict(float)

    # Track money flow totals (separated by transfers vs cash flow)
    income_total = 0.0
    spending_total = 0.0
    credits_total = 0.0  # Refunds from non-income merchants
    transfers_in = 0.0
    transfers_out = 0.0
    investment_total = 0.0  # 401K, IRA, and other investment contributions

    for txn in transactions:
        tags = txn.get('tags', [])

        # Use classification module for consistent amount handling
        effective_amount = normalize_amount(txn['amount'], tags)

        # Categorize amount into appropriate bucket (all values positive)
        cat = categorize_amount(txn['amount'], tags)
        income_total += cat['income']
        investment_total += cat['investment']
        spending_total += cat['spending']
        credits_total += cat['credits']      # Now stored as positive
        transfers_in += cat['transfer_in']
        transfers_out += cat['transfer_out']  # Now stored as positive

        key = (txn['category'], txn['subcategory'])
        by_category[key]['count'] += 1
        by_category[key]['total'] += effective_amount

        month_key = txn['date'].strftime('%Y-%m')

        # Track by merchant
        by_merchant[txn['merchant']]['count'] += 1
        by_merchant[txn['merchant']]['total'] += effective_amount
        by_merchant[txn['merchant']]['spending'] += cat['spending']
        by_merchant[txn['merchant']]['credits'] += cat['credits']
        by_merchant[txn['merchant']]['income'] += cat['income']
        by_merchant[txn['merchant']]['investment'] += cat['investment']
        by_merchant[txn['merchant']]['transfer_in'] += cat['transfer_in']
        by_merchant[txn['merchant']]['transfer_out'] += cat['transfer_out']
        by_merchant[txn['merchant']]['category'] = txn['category']
        by_merchant[txn['merchant']]['subcategory'] = txn['subcategory']
        by_merchant[txn['merchant']]['months'].add(month_key)
        by_merchant[txn['merchant']]['monthly_amounts'][month_key] += effective_amount
        by_merchant[txn['merchant']]['payments'].append(effective_amount)
        txn_data = {
            'date': txn['date'].strftime('%m/%d'),
            'month': month_key,
            # Use transformed description if available, otherwise raw_description
            'description': txn.get('description') if txn.get('original_description') else txn.get('raw_description', txn['description']),
            'amount': effective_amount,
            'source': txn['source'],
            'tags': txn.get('tags', [])
        }
        # Include extra_fields from field: directives
        if txn.get('extra_fields'):
            txn_data['extra_fields'] = txn['extra_fields']
        # Include original_description if transform was applied
        if txn.get('original_description'):
            txn_data['original_description'] = txn['original_description']
        by_merchant[txn['merchant']]['transactions'].append(txn_data)
        # Track max payment
        if effective_amount > by_merchant[txn['merchant']]['max_payment']:
            by_merchant[txn['merchant']]['max_payment'] = effective_amount
        # Store match info (pattern that matched) - first transaction sets this
        if 'match_info' not in by_merchant[txn['merchant']] and txn.get('match_info'):
            by_merchant[txn['merchant']]['match_info'] = txn['match_info']
        # Collect tags from all transactions
        by_merchant[txn['merchant']]['tags'].update(txn.get('tags', []))
        # Track raw description variations
        raw_desc = txn.get('raw_description', txn.get('description', ''))
        by_merchant[txn['merchant']]['raw_descriptions'][raw_desc] += 1

        by_month[month_key] += effective_amount

    # Calculate months active and monthly average for each merchant
    all_months = set(by_month.keys())
    num_months = len(all_months) if all_months else 12

    for merchant, data in by_merchant.items():
        data['months_active'] = len(data['months'])
        data['avg_when_active'] = data['total'] / data['months_active'] if data['months_active'] > 0 else 0

        # Calculate consistency: are monthly amounts similar or lumpy?
        monthly_vals = list(data['monthly_amounts'].values())
        if len(monthly_vals) >= 2:
            avg = sum(monthly_vals) / len(monthly_vals)
            variance = sum((x - avg) ** 2 for x in monthly_vals) / len(monthly_vals)
            std_dev = variance ** 0.5
            # Coefficient of variation: std_dev / mean (0 = perfectly consistent, >0.5 = lumpy)
            data['cv'] = std_dev / avg if avg > 0 else 0
            data['is_consistent'] = data['cv'] < 0.3  # Less than 30% variation = consistent
        else:
            data['cv'] = 0
            data['is_consistent'] = True

        data['months'] = sorted(list(data['months']))

    # =========================================================================
    # CALCULATE MONTHLY VALUES
    # =========================================================================
    # All merchants use YTD/12 for monthly value calculation
    # Custom grouping/views are defined in views.rules
    for merchant, data in by_merchant.items():
        data['calc_type'] = '/12'
        monthly_value = data['total'] / 12
        data['monthly_value'] = monthly_value
        data['calc_reasoning'] = 'Spread over 12 months'
        data['calc_formula'] = f"total / 12 = {data['total']:.2f} / 12 = {monthly_value:.2f}"
        data['reasoning'] = {
            'category': data.get('category', ''),
            'subcategory': data.get('subcategory', ''),
            'months_active': data.get('months_active', 1),
            'num_months': num_months,
            'cv': round(data.get('cv', 0), 2),
        }

    # Calculate monthly totals (views.rules handles custom grouping/sections)
    total_transactions = sum(d['total'] for d in by_merchant.values())
    monthly_avg = sum(d.get('monthly_value', 0) for d in by_merchant.values())

    # Gross spending = sum of all positive merchant totals (for percentage calculations)
    gross_spending = sum(d['total'] for d in by_merchant.values() if d['total'] > 0)

    return {
        'by_category': dict(by_category),
        'by_merchant': {k: dict(v) for k, v in by_merchant.items()},
        'by_month': dict(by_month),
        'total': sum(t['amount'] for t in transactions),
        'count': len(transactions),
        'num_months': num_months,
        # Totals
        'total_transactions': total_transactions,
        'monthly_avg': monthly_avg,
        # Money flow (all values positive for clarity)
        # Cash flow (excludes transfers and investments)
        'income_total': income_total,
        'spending_total': spending_total,
        'credits_total': credits_total,  # Refunds (now stored as positive)
        'cash_flow': calculate_cash_flow(income_total, spending_total, credits_total),
        # Transfers (money moving between accounts, both positive)
        'transfers_in': transfers_in,
        'transfers_out': transfers_out,
        'transfers_net': calculate_transfers_net(transfers_in, transfers_out),
        # Investments (401K, IRA contributions - excluded from spending)
        'investment_total': investment_total,
        # Gross spending (for percentage calculations in output formats)
        'gross_spending': gross_spending,
    }


def classify_by_sections(by_merchant, sections_config, num_months=12):
    """
    Classify merchants into user-defined sections.

    Args:
        by_merchant: Dict of merchant_name -> merchant data (from analyze_transactions)
        sections_config: SectionConfig from section_engine
        num_months: Number of months in the data period

    Returns:
        Dict mapping section_name -> list of (merchant_name, merchant_data) tuples
    """
    if sections_config is None:
        return {}

    # Collect all unique months across all transactions for period_data
    all_months = set()
    all_years = set()

    # Convert by_merchant to the format expected by section_engine
    merchant_groups = []
    for merchant_name, data in by_merchant.items():

        # Build transactions list for the section filter
        # The 'transactions' key already has the individual transactions
        txns = data.get('transactions', [])

        # Convert transaction format for section_engine
        section_txns = []
        for txn in txns:
            txn_date = datetime.strptime(txn['month'] + '-15', '%Y-%m-%d')
            section_txns.append({
                'amount': txn['amount'],
                'date': txn_date,
                'category': data.get('category', ''),
                'subcategory': data.get('subcategory', ''),
                'merchant': merchant_name,
                'tags': list(data.get('tags', [])),
            })
            # Track global periods
            all_months.add(txn['month'])
            all_years.add(txn_date.year)

        merchant_groups.append({
            'merchant': merchant_name,
            'category': data.get('category', ''),
            'subcategory': data.get('subcategory', ''),
            'transactions': section_txns,
            'data': data,  # Keep reference to original data
        })

    # Compute period_data from all transactions
    period_data = {
        'month': len(all_months) if all_months else num_months,
        'year': len(all_years) if all_years else 1,
    }

    # Classify using section_engine
    section_results = section_engine.classify_merchants(
        sections_config,
        merchant_groups,
        num_months,
        period_data=period_data,
    )

    # Convert results back to (merchant_name, data) tuples
    result = {}
    for section_name, merchants in section_results.items():
        result[section_name] = [
            (m['merchant'], m['data'])
            for m in merchants
        ]

    return result


def compute_section_totals(section_merchants):
    """
    Compute totals for a section.

    Args:
        section_merchants: List of (merchant_name, merchant_data) tuples

    Returns:
        Dict with section totals
    """
    total = sum(data.get('total', 0) for _, data in section_merchants)
    monthly = sum(data.get('monthly_value', 0) for _, data in section_merchants)
    count = len(section_merchants)

    return {
        'total': total,
        'monthly': monthly,
        'count': count,
        'merchants': section_merchants,
    }


# ============================================================================
# EXPORT FUNCTIONS
# ============================================================================

def build_merchant_json(merchant_name, data, verbose=0):
    """Build JSON representation of a merchant with reasoning based on verbosity level.

    Args:
        merchant_name: Name of the merchant
        data: Merchant data dictionary
        verbose: Verbosity level (0=basic, 1=trace, 2=full)

    Returns: dict suitable for JSON serialization
    """
    # Handle tags - could be a set or list
    tags = data.get('tags', [])
    if isinstance(tags, set):
        tags = sorted(tags)
    else:
        tags = sorted(set(tags))

    result = {
        'name': merchant_name,
        'category': data.get('category', ''),
        'subcategory': data.get('subcategory', ''),
        'tags': tags,
        'total': round(data.get('total', 0), 2),
        'count': data.get('count', 0),
        'months_active': data.get('months_active', 0),
        'monthly_value': round(data.get('monthly_value', 0), 2),
    }

    # Add reasoning (always include decision)
    reasoning = data.get('reasoning', {})
    result['reasoning'] = {
        'decision': reasoning.get('decision', ''),
    }

    # Add calculation info
    result['calculation'] = {
        'type': data.get('calc_type', ''),
        'reason': data.get('calc_reasoning', ''),
    }

    # Verbose: add decision trace and raw description variations
    if verbose >= 1:
        result['reasoning']['trace'] = reasoning.get('trace', [])
        raw_descs = data.get('raw_descriptions', {})
        if raw_descs:
            # Convert defaultdict to regular dict for JSON
            result['raw_descriptions'] = dict(raw_descs)

    # Very verbose: add thresholds, CV, and calculation formula
    if verbose >= 2:
        result['reasoning']['thresholds'] = reasoning.get('thresholds', {})
        result['reasoning']['cv'] = reasoning.get('cv', 0)
        result['reasoning']['is_consistent'] = reasoning.get('is_consistent', True)
        result['calculation']['formula'] = data.get('calc_formula', '')
        result['months'] = data.get('months', [])

    # Add pattern match info if available
    match_info = data.get('match_info')
    if match_info:
        pattern_tags = match_info.get('tags', [])
        if isinstance(pattern_tags, set):
            pattern_tags = sorted(pattern_tags)
        result['pattern'] = {
            'matched': match_info.get('pattern', ''),
            'source': match_info.get('source', 'unknown'),
            'tags': pattern_tags,
        }

    return result


def export_json(stats, verbose=0, category_filter=None, merchant_filter=None,
                budgets=None, anomalies=None, duplicates=None):
    """Export analysis results as JSON with reasoning.

    Args:
        stats: Analysis results from analyze_transactions()
        verbose: Verbosity level (0=basic, 1=trace, 2=full)
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)
        budgets: Optional budget report from budgets.build_budget_report()
        anomalies: Optional report from anomalies.detect_anomalies()
        duplicates: Optional report from duplicates.build_duplicate_report()

    Returns: JSON string
    """
    import json

    by_merchant = stats.get('by_merchant', {})
    by_month = stats.get('by_month', {})
    by_category = stats.get('by_category', {})

    # Use values from stats
    gross_spending = stats.get('gross_spending', 0)
    credits_total = stats.get('credits_total', 0)

    # Use income and transfers from stats (same as other output formats)
    income_total = stats.get('income_total', 0)
    transfers_out = stats.get('transfers_out', 0)

    spending_total = stats.get('spending_total', 0)
    cash_flow = stats.get('cash_flow', 0)

    output = {
        'summary': {
            'total_spending': round(stats['total'], 2),
            'gross_spending': round(gross_spending, 2),
            'credits_total': round(credits_total, 2),
            'monthly_budget': round(stats['monthly_avg'], 2),
            'num_months': stats['num_months'],
            # Cash flow (matches other formats)
            'income_total': round(abs(income_total), 2),
            'spending_total': round(spending_total, 2),
            'cash_flow': round(cash_flow, 2),
            # Transfers
            'transfers_total': round(transfers_out, 2),
        },
        'by_month': {month: {'total': round(total, 2)}
                     for month, total in sorted(by_month.items())},
        'by_category': [
            {
                'category': cat,
                'subcategory': subcat,
                'total': round(data['total'], 2),
                'percentage': round(data['total'] / gross_spending * 100, 1) if gross_spending > 0 else 0
            }
            for (cat, subcat), data in sorted(by_category.items(), key=lambda x: x[1]['total'], reverse=True)
            if data['total'] > 0
        ],
        'credits': [
            {'merchant': name, 'category': data.get('category', ''), 'amount': round(data['credits'], 2)}
            for name, data in sorted(by_merchant.items(), key=lambda x: x[1].get('credits', 0), reverse=True)
            if data.get('credits', 0) > 0
        ],
        'merchants': []
    }

    merchants = []
    for name, data in by_merchant.items():
        # Apply filters
        if category_filter and data.get('category') != category_filter:
            continue
        if merchant_filter and name not in merchant_filter:
            continue

        merchants.append(build_merchant_json(name, data, verbose))

    # Sort by monthly value descending
    merchants.sort(key=lambda x: x['monthly_value'], reverse=True)
    output['merchants'] = merchants

    # Review data. Only emitted when present so existing consumers of the JSON
    # (including the run-over-run diff) see no change unless the feature is used.
    if budgets and budgets.get('enabled'):
        from .budgets import budget_report_to_dict
        output['budgets'] = budget_report_to_dict(budgets)
    if anomalies and anomalies.get('enabled'):
        from .anomalies import anomaly_report_to_dict
        output['anomalies'] = anomaly_report_to_dict(anomalies)
    if duplicates and duplicates.get('enabled') and duplicates.get('total_count'):
        from .duplicates import duplicate_report_to_dict
        output['duplicates'] = duplicate_report_to_dict(duplicates)

    return json.dumps(output, indent=2)


def export_markdown(stats, verbose=0, category_filter=None, merchant_filter=None,
                    currency_format="${amount}", budgets=None, anomalies=None, duplicates=None):
    """Export analysis results as Markdown with reasoning.

    Args:
        stats: Analysis results from analyze_transactions()
        verbose: Verbosity level (0=basic, 1=trace, 2=full)
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)
        currency_format: Format string for currency (e.g. "${amount}" or "£{amount}")
        budgets: Optional budget report from budgets.build_budget_report()
        anomalies: Optional report from anomalies.detect_anomalies()
        duplicates: Optional report from duplicates.build_duplicate_report()

    Returns: Markdown string
    """
    # Local helper for currency formatting
    def fmt(amount, show_sign=False):
        """Format amount with currency. If show_sign=True, prefix with + for positive."""
        formatted = format_currency_decimal(abs(amount), currency_format)
        if show_sign and amount >= 0:
            return '+' + formatted
        elif amount < 0:
            return '-' + formatted
        return formatted

    by_merchant = stats.get('by_merchant', {})
    by_month = stats.get('by_month', {})
    by_category = stats.get('by_category', {})

    # Use values from stats
    gross_spending = stats.get('gross_spending', 0)
    income_total = stats.get('income_total', 0)
    spending_total = stats.get('spending_total', 0)
    credits_total = stats.get('credits_total', 0)
    cash_flow = stats.get('cash_flow', 0)
    transfers_in = stats.get('transfers_in', 0)
    transfers_out = stats.get('transfers_out', 0)
    transfers_net = stats.get('transfers_net', 0)

    lines = ['# Financial Report\n']

    # Budgets lead the report: during a review the targets matter more than
    # the raw totals underneath them.
    if budgets and budgets.get('enabled') and budgets.get('results'):
        lines.append('## Budgets\n')
        lines.append('| Target | Period | Budget | Actual | Used | Variance | Status |')
        lines.append('|--------|--------|--------|--------|------|----------|--------|')
        for result in budgets['results']:
            lines.append(
                f"| {result.label} | {result.period} | {fmt(result.target)} | "
                f"{fmt(result.comparison_actual)} | {result.pct_used:.0f}% | "
                f"{fmt(result.variance, show_sign=True)} | {result.status} |"
            )
        lines.append('')
        for problem in budgets.get('problems', []):
            suggestions = ', '.join(problem['suggestions']) or 'none found'
            lines.append(f"> Budget `{problem['key']}` matched no transactions. Did you mean: {suggestions}")
        if budgets.get('problems'):
            lines.append('')

    # Anomalies
    if anomalies and anomalies.get('enabled') and anomalies.get('anomalies'):
        lines.append(f"## Worth a Look ({anomalies.get('latest_month', '')})\n")
        for anomaly in anomalies['anomalies']:
            marker = '**!**' if anomaly.severity == 'warn' else '-'
            lines.append(f"{marker} **{anomaly.title}** - {anomaly.detail}")
        lines.append('')

    # Duplicate warning
    if duplicates and duplicates.get('enabled') and duplicates.get('cross_file'):
        cross = duplicates['cross_file']
        lines.append('## Possible Duplicates\n')
        lines.append(f"{len(cross)} transaction(s) appear in more than one file, "
                     f"double counting {fmt(duplicates.get('cross_file_impact', 0))}.\n")
        lines.append('| Date | Amount | Description | Files |')
        lines.append('|------|--------|-------------|-------|')
        for group in cross[:20]:
            files = ', '.join(os.path.basename(f) for f in group.files)
            lines.append(f"| {group.date} | {fmt(group.amount)} | {group.description} | {files} |")
        lines.append('')

    # Cash Flow Summary
    lines.append('## Cash Flow\n')
    lines.append(f"| Item | Amount |")
    lines.append(f"|------|--------|")
    lines.append(f"| Income | {fmt(income_total, show_sign=True)} |")
    lines.append(f"| Spending | {fmt(-spending_total)} |")
    lines.append(f"| Credits/Refunds | {fmt(credits_total, show_sign=True)} |")
    lines.append(f"| **Net Cash Flow** | **{fmt(cash_flow, show_sign=True)}** |")
    lines.append('')

    # Transfers Summary
    lines.append('## Transfers\n')
    lines.append(f"| Item | Amount |")
    lines.append(f"|------|--------|")
    lines.append(f"| In | {fmt(transfers_in, show_sign=True)} |")
    lines.append(f"| Out | {fmt(transfers_out)} |")
    lines.append(f"| **Net Transfers** | **{fmt(transfers_net, show_sign=True)}** |")
    lines.append(f"- **Data Period:** {stats['num_months']} months\n")

    # Monthly Breakdown
    if by_month:
        lines.append('## Monthly Breakdown\n')
        lines.append('| Month | Spending |')
        lines.append('|-------|----------|')
        for month in sorted(by_month.keys()):
            total = by_month[month]
            lines.append(f"| {month} | {fmt(total)} |")
        lines.append('')

    # Credits/Refunds (genuine refunds only - transfers/income have their own rows)
    credit_merchants = [(m, d) for m, d in by_merchant.items() if d.get('credits', 0) > 0]
    if credit_merchants:
        lines.append('## Credits/Refunds\n')
        lines.append('| Merchant | Category | Amount |')
        lines.append('|----------|----------|--------|')
        for name, data in sorted(credit_merchants, key=lambda x: x[1]['credits'], reverse=True):
            lines.append(f"| {name} | {data.get('category', '')} | {fmt(data['credits'], show_sign=True)} |")
        lines.append(f"| **Total** | | **{fmt(credits_total, show_sign=True)}** |")
        lines.append('')

    # By Category
    lines.append('## By Category\n')
    lines.append('| Category | Subcategory | YTD | % |')
    lines.append('|----------|-------------|-----|---|')
    positive_cats = [(k, v) for k, v in by_category.items() if v['total'] > 0]
    for (cat, subcat), data in sorted(positive_cats, key=lambda x: x[1]['total'], reverse=True)[:15]:
        pct = (data['total'] / gross_spending * 100) if gross_spending > 0 else 0
        lines.append(f"| {cat} | {subcat} | {fmt(data['total'])} | {pct:.1f}% |")
    lines.append('')

    # Merchants
    lines.append("## Merchants\n")

    # Sort by monthly value (positive merchants only)
    positive_merchants = [(m, d) for m, d in by_merchant.items() if d['total'] > 0]
    sorted_merchants = sorted(
        positive_merchants,
        key=lambda x: x[1].get('monthly_value', 0),
        reverse=True
    )

    for name, data in sorted_merchants:
        # Apply filters
        if category_filter and data.get('category') != category_filter:
            continue
        if merchant_filter and name not in merchant_filter:
            continue

        reasoning = data.get('reasoning', {})

        lines.append(f"### {name}")
        lines.append(f"**Category:** {data.get('category', '')} > {data.get('subcategory', '')}")
        lines.append(f"**Monthly Value:** {fmt(data.get('monthly_value', 0))}")
        lines.append(f"**YTD Total:** {fmt(data.get('total', 0))}")
        lines.append(f"**Months Active:** {data.get('months_active', 0)}/{stats['num_months']}")

        # Verbose: add decision trace
        if verbose >= 1:
            trace = reasoning.get('trace', [])
            if trace:
                lines.append('\n**Decision Trace:**')
                for i, step in enumerate(trace, 1):
                    lines.append(f"  {i}. {step}")

        # Very verbose: add calculation details
        if verbose >= 2:
            lines.append(f"\n**Calculation:** {data.get('calc_type', '')} ({data.get('calc_reasoning', '')})")
            lines.append(f"  Formula: {data.get('calc_formula', '')}")
            lines.append(f"  CV: {reasoning.get('cv', 0):.2f}")

        lines.append('')  # Empty line between merchants

    return '\n'.join(lines)


def export_csv(stats, category_filter=None, merchant_filter=None):
    """Export analysis results as CSV (transaction-level).

    Args:
        stats: Analysis results from analyze_transactions()
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)

    Returns: CSV string with headers
    """
    import csv
    import io

    by_merchant = stats.get('by_merchant', {})

    # Collect all transactions and detect extra_fields columns
    all_transactions = []
    extra_field_names = set()

    for merchant_name, data in by_merchant.items():
        # Apply filters
        if category_filter and data.get('category') != category_filter:
            continue
        if merchant_filter and merchant_name not in merchant_filter:
            continue

        category = data.get('category', '')
        subcategory = data.get('subcategory', '')

        for txn in data.get('transactions', []):
            # Construct full date from month (YYYY-MM) and date (MM/DD)
            month = txn.get('month', '')  # e.g., "2025-01"
            date_str = txn.get('date', '')  # e.g., "01/15"
            if month and date_str:
                # Extract day from MM/DD format
                day = date_str.split('/')[1] if '/' in date_str else '01'
                full_date = f"{month}-{day}"  # YYYY-MM-DD
            else:
                full_date = date_str

            row = {
                'date': full_date,
                'description': txn.get('description', ''),
                'amount': txn.get('amount', 0),
                'merchant': merchant_name,
                'category': category,
                'subcategory': subcategory,
                'source': txn.get('source', ''),
                'tags': ';'.join(sorted(txn.get('tags', []))),
            }

            # Collect extra_fields
            if txn.get('extra_fields'):
                for field_name, field_value in txn['extra_fields'].items():
                    extra_field_names.add(field_name)
                    row[field_name] = field_value

            all_transactions.append(row)

    # Sort transactions by date
    all_transactions.sort(key=lambda x: x['date'])

    # Build header: fixed columns + dynamic extra_fields
    base_columns = ['date', 'description', 'amount', 'merchant', 'category', 'subcategory', 'source', 'tags']
    extra_columns = sorted(extra_field_names)
    all_columns = base_columns + extra_columns

    # Write CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(all_transactions)

    return output.getvalue()


def print_summary(stats, title=None, filter_category=None, currency_format="${amount}", group_by='merchant'):
    """Print analysis summary.

    Args:
        stats: Analysis statistics dict
        title: Report title for display (e.g., "2025 Budget Analysis")
        filter_category: Optional category to filter to
        currency_format: Format string for currency
        group_by: How to group in BY CATEGORY section - 'merchant' or 'subcategory'
    """
    # Import colors for terminal output
    from .colors import C

    # Local helper for currency formatting
    def fmt(amount):
        return format_currency(amount, currency_format)

    by_category = stats['by_category']
    by_merchant = stats.get('by_merchant', {})
    by_month = stats.get('by_month', {})

    # Use cash flow values from stats
    income_total = stats.get('income_total', 0)
    spending_total = stats.get('spending_total', 0)
    credits_total = stats.get('credits_total', 0)
    cash_flow = stats.get('cash_flow', 0)
    transfers_in = stats.get('transfers_in', 0)
    transfers_out = stats.get('transfers_out', 0)
    transfers_net = stats.get('transfers_net', 0)
    gross_spending = stats.get('gross_spending', 0)

    # =========================================================================
    # FINANCIAL SUMMARY
    # =========================================================================
    print("=" * 80)
    print(title or "FINANCIAL REPORT")
    print("=" * 80)

    print("\nCASH FLOW")
    print("-" * 50)
    print(f"Income:                     +{fmt(income_total):>14}")
    print(f"Spending:                   -{fmt(spending_total):>14}")
    print(f"Credits/Refunds:            +{fmt(abs(credits_total)):>14}")
    print("-" * 50)
    sign = '+' if cash_flow >= 0 else ''
    print(f"Net Cash Flow:              {sign}{fmt(cash_flow):>14}")

    print("\nTRANSFERS")
    print("-" * 50)
    print(f"In:                         +{fmt(transfers_in):>14}")
    print(f"Out:                         {fmt(transfers_out):>14}")
    print("-" * 50)
    sign = '+' if transfers_net >= 0 else ''
    print(f"Net Transfers:              {sign}{fmt(transfers_net):>14}")

    print(f"\nMerchants:                   {len(by_merchant):>14}")

    # =========================================================================
    # CREDITS/REFUNDS
    # =========================================================================
    # Only genuine refunds count here. A negative merchant total can also come
    # from transfers or income (e.g. a stock sale), which are reported in their
    # own sections - including them here made the rows disagree with the total.
    credit_merchants = [(m, d) for m, d in by_merchant.items() if d.get('credits', 0) > 0]
    if credit_merchants:
        print("\n" + "=" * 80)
        print("CREDITS/REFUNDS")
        print("=" * 80)
        print(f"\n{'Merchant':<30} {'Category':<20} {'Amount':>14}")
        print("-" * 68)
        for merchant, data in sorted(credit_merchants, key=lambda x: x[1]['credits'], reverse=True):
            category = data.get('category', 'Unknown')[:20]
            print(f"{merchant:<30} {category:<20} +{fmt(data['credits']):>14}")
        print(f"\n{'TOTAL CREDITS':<30} {'':<20} +{fmt(credits_total):>14}")

    # =========================================================================
    # MONTHLY BREAKDOWN
    # =========================================================================
    if by_month:
        print("\n" + "=" * 80)
        print("MONTHLY BREAKDOWN")
        print("=" * 80)
        print(f"\n{'Month':<12} {'Total':>14}")
        print("-" * 28)
        for month in sorted(by_month.keys()):
            total = by_month[month]
            month_label = month  # Format: "2024-01"
            print(f"{month_label:<12} {fmt(total):>14}")
        avg_monthly = abs(spending_total + transfers_out) / len(by_month) if by_month else 0
        print("-" * 28)
        print(f"{'AVERAGE':<12} {fmt(avg_monthly):>14}/mo")

    # =========================================================================
    # TOP MERCHANTS BY SPENDING
    # =========================================================================
    print("\n" + "=" * 80)
    print("TOP MERCHANTS BY SPENDING")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<18} {'Mo':>3} {'Monthly':>12} {'YTD':>14}")
    print("-" * 80)

    # Only show positive-total merchants here (credits shown separately)
    positive_merchants = [(m, d) for m, d in by_merchant.items() if d['total'] > 0]
    sorted_merchants = sorted(
        positive_merchants,
        key=lambda x: x[1].get('total', 0),
        reverse=True
    )

    for merchant, data in sorted_merchants[:25]:
        if filter_category and data.get('category', '').lower() != filter_category.lower():
            continue
        months_active = data.get('months_active', 0)
        monthly = data.get('monthly_value', 0)
        total = data.get('total', 0)
        category = data.get('category', 'Unknown')[:18]
        print(f"{merchant:<28} {category:<18} {months_active:>3} {fmt(monthly):>12} {fmt(total):>14}")

    print(f"\n{'TOTAL':<28} {'':<18} {'':<3} {fmt(stats['monthly_avg']):>12}/mo {fmt(abs(spending_total)):>14}")

    # =========================================================================
    # BY CATEGORY (with percentages)
    # =========================================================================
    print("\n" + "=" * 80)
    print(f"BY CATEGORY (grouped by {group_by})")
    print("=" * 80)

    if group_by == 'subcategory':
        # Group by subcategory within category
        print(f"\n{'Category':<20} {'Subcategory':<16} {'YTD':>12} {'%':>8}")
        print("-" * 60)

        # Only show positive categories (credits shown separately above)
        positive_cats = [(k, v) for k, v in by_category.items() if v['total'] > 0]
        sorted_cats = sorted(positive_cats, key=lambda x: x[1]['total'], reverse=True)
        for (cat, subcat), data in sorted_cats[:20]:
            if filter_category and cat.lower() != filter_category.lower():
                continue
            pct = (data['total'] / gross_spending * 100) if gross_spending > 0 else 0
            print(f"{cat:<20} {subcat:<16} {fmt(data['total']):>12} {pct:>7.1f}%")
    else:
        # Group by merchant within category (default)
        print(f"\n{'Category':<20} {'Merchant':<20} {'YTD':>12} {'%':>8}")
        print("-" * 64)

        # Build category -> merchants mapping
        cat_merchants = {}
        for merchant, data in by_merchant.items():
            if data['total'] <= 0:
                continue
            cat = data.get('category', 'Unknown')
            if cat not in cat_merchants:
                cat_merchants[cat] = []
            cat_merchants[cat].append((merchant, data))

        # Sort categories by total
        sorted_cats = sorted(
            cat_merchants.items(),
            key=lambda x: sum(d['total'] for _, d in x[1]),
            reverse=True
        )

        count = 0
        for cat, merchants in sorted_cats:
            if filter_category and cat.lower() != filter_category.lower():
                continue
            if count >= 20:
                break
            # Sort merchants within category by total
            for merchant, data in sorted(merchants, key=lambda x: x[1]['total'], reverse=True)[:5]:
                pct = (data['total'] / gross_spending * 100) if gross_spending > 0 else 0
                print(f"{cat:<20} {merchant[:20]:<20} {fmt(data['total']):>12} {pct:>7.1f}%")
                count += 1
                if count >= 20:
                    break


def print_sections_summary(stats, title=None, currency_format="${amount}", only_filter=None):
    """Print sections-based analysis summary.

    Args:
        stats: Analysis statistics dict
        title: Report title for display (e.g., "2025 Budget Analysis")
        currency_format: Format string for currency
        only_filter: Optional list of section names (lowercase) to show
    """
    # Import colors for terminal output
    from .colors import C

    def fmt(amount):
        return format_currency(amount, currency_format)

    sections = stats.get('sections', {})
    sections_config = stats.get('_sections_config')

    if not sections:
        print("No views defined. Add views to config/views.rules")
        return

    # Get the order of sections from config
    section_order = [s.name for s in sections_config.sections] if sections_config else list(sections.keys())

    # Filter sections if only_filter is specified
    if only_filter:
        section_order = [s for s in section_order if s.lower() in only_filter]

    num_months = stats.get('num_months', 12)

    print("=" * 80)
    print(title or "SPENDING ANALYSIS")
    print("=" * 80)

    # Print each section
    for section_name in section_order:
        if section_name not in sections:
            continue

        section_data = sections[section_name]
        section_total = section_data.get('total', 0)
        section_monthly = section_data.get('monthly', 0)
        merchants = section_data.get('merchants', [])

        if not merchants:
            continue

        # Section header with totals
        print()
        print(f"{section_name.upper()} ({fmt(section_total)}/yr · {fmt(section_monthly)}/mo)")
        print("-" * 70)

        # Print merchants in section
        print(f"{'Merchant':<28} {'Mo':>3} {'Type':<6} {'Monthly':>12} {'YTD':>14}")
        print("-" * 70)

        # Sort merchants by total (descending)
        sorted_merchants = sorted(merchants, key=lambda x: x[1].get('total', 0), reverse=True)

        for merchant_name, data in sorted_merchants[:20]:
            months_active = data.get('months_active', 0)
            total = data.get('total', 0)
            is_consistent = data.get('is_consistent', False)

            if is_consistent and months_active > 0:
                calc_type = "avg"
                monthly = data.get('avg_when_active', total / months_active)
            else:
                calc_type = "/12"
                monthly = total / num_months

            print(f"{merchant_name:<28} {months_active:>3} {calc_type:<6} {fmt(monthly):>12} {fmt(total):>14}")

        if len(sorted_merchants) > 20:
            print(f"  ... and {len(sorted_merchants) - 20} more merchants")

    # Use transaction-level totals from stats (matches HTML Cash Flow card)
    spending_total = stats.get('spending_total', 0)
    income_total = stats.get('income_total', 0)
    credits_total = stats.get('credits_total', 0)
    cash_flow = stats.get('cash_flow', 0)
    investment_total = stats.get('investment_total', 0)
    monthly_spending = spending_total / num_months if num_months > 0 else 0

    print()
    print(f"{C.BOLD}TOTAL SPENDING:{C.RESET} {C.CYAN}{fmt(spending_total)}/yr{C.RESET} · {C.DIM}{fmt(monthly_spending)}/mo{C.RESET}")
    print("=" * 80)

    # Cash flow summary (aligns with HTML report)
    print()
    print(f"{C.BOLD}CASH FLOW SUMMARY{C.RESET}")
    print(f"{C.DIM}{'-' * 40}{C.RESET}")
    print(f"  {C.DIM}Income:{C.RESET}      {C.GREEN}+{fmt(income_total)}{C.RESET}")
    print(f"  {C.DIM}Spending:{C.RESET}    {C.RED}-{fmt(spending_total)}{C.RESET}")
    if credits_total > 0:
        print(f"  {C.DIM}Credits:{C.RESET}     {C.GREEN}+{fmt(credits_total)}{C.RESET}")
    print(f"               {C.DIM}{'-' * 15}{C.RESET}")
    if cash_flow >= 0:
        print(f"  {C.BOLD}Cash Flow:{C.RESET}   {C.GREEN}+{fmt(cash_flow)}{C.RESET}")
    else:
        print(f"  {C.BOLD}Cash Flow:{C.RESET}   {C.RED}{fmt(cash_flow)}{C.RESET}")
    if investment_total > 0:
        print()
        print(f"  {C.DIM}Investments:{C.RESET} {C.CYAN}{fmt(investment_total)}{C.RESET} {C.DIM}(401K, IRA, etc.){C.RESET}")
    print("=" * 80)


# =============================================================================
# REVIEW OUTPUT - Budgets, anomalies and duplicate warnings
# =============================================================================

# Width of the inline progress bar drawn next to each budget.
BUDGET_BAR_WIDTH = 14


def _budget_bar(pct_used):
    """Render a progress bar for a budget, capped at 100% of the bar width."""
    filled = int(min(pct_used, 100) / 100 * BUDGET_BAR_WIDTH)
    return '█' * filled + '░' * (BUDGET_BAR_WIDTH - filled)


def _budget_color(status):
    return {'over': C.RED, 'near': C.YELLOW}.get(status, C.GREEN)


def print_budget_summary(budget_report, currency_format="${amount}", latest_month_complete=True):
    """Print budget targets versus actual spending.

    Args:
        budget_report: Report from budgets.build_budget_report()
        currency_format: Format string for currency
        latest_month_complete: False when the newest month is only partially
            covered by the data, which makes actuals look artificially low.
    """
    if not budget_report or not budget_report.get('enabled'):
        return

    results = budget_report.get('results', [])
    if not results:
        return

    def fmt(amount):
        return format_currency_decimal(amount, currency_format)

    def fmt_signed(amount):
        return format_currency_signed(amount, currency_format)

    print("\n" + "=" * 80)
    print("BUDGETS")
    print("=" * 80)
    print()

    over = [r for r in results if r.status == 'over']
    near = [r for r in results if r.status == 'near']
    if over:
        print(f"  {C.RED}{len(over)} of {len(results)} targets over budget{C.RESET}")
    elif near:
        print(f"  {C.YELLOW}{len(near)} of {len(results)} targets close to the limit{C.RESET}")
    else:
        print(f"  {C.GREEN}All {len(results)} targets within budget{C.RESET}")
    print()

    print(f"{'Target':<24} {'Budget':>11} {'Actual':>11}  {'Progress':<{BUDGET_BAR_WIDTH}} {'':>6} {'Variance':>12}")
    print("-" * 80)

    for result in results:
        color = _budget_color(result.status)
        period_note = '/yr' if result.period == 'yearly' else '/mo'
        label = result.label[:23]
        variance = f"{fmt_signed(result.variance)}{'/yr' if result.period == 'yearly' else '/mo'}"
        print(
            f"{label:<24} {fmt(result.target):>11} {fmt(result.comparison_actual):>11}  "
            f"{color}{_budget_bar(result.pct_used)}{C.RESET} {color}{result.pct_used:>5.0f}%{C.RESET} "
            f"{color}{variance:>15}{C.RESET}"
        )

        if result.months_over:
            print(f"    {C.DIM}↳ over target in {result.months_over} of "
                  f"{result.num_months} months{C.RESET}")

    if not latest_month_complete:
        print()
        print(f"  {C.DIM}Note: the most recent month is partial, so actuals for it "
              f"are lower than a full month.{C.RESET}")

    # A budget that matches nothing silently reads as perfect discipline, so
    # always call it out with something concrete to fix.
    for problem in budget_report.get('problems', []):
        print()
        print(f"  {C.YELLOW}⚠{C.RESET} Budget '{problem['key']}' did not match any transactions.")
        if problem['suggestions']:
            print(f"    Did you mean: {', '.join(problem['suggestions'])}")
        else:
            print(f"    No {problem['scope']} values were found in your data.")
        print(f"    Check the name against 'tally up --format summary' or 'tally explain'.")


def print_anomaly_summary(anomaly_report, currency_format="${amount}"):
    """Print the 'worth a look' list of changes since previous months."""
    if not anomaly_report or not anomaly_report.get('enabled'):
        return

    anomalies = anomaly_report.get('anomalies', [])
    if not anomalies:
        return

    latest_month = anomaly_report.get('latest_month', '')
    print("\n" + "=" * 80)
    print(f"WORTH A LOOK ({latest_month})")
    print("=" * 80)
    print()

    for anomaly in anomalies:
        if anomaly.severity == 'warn':
            marker = f"{C.YELLOW}⚠{C.RESET}"
        else:
            marker = f"{C.DIM}·{C.RESET}"
        print(f"  {marker} {C.BOLD}{anomaly.title}{C.RESET}")
        print(f"    {C.DIM}{anomaly.detail}{C.RESET}")

    total_found = anomaly_report.get('total_found', len(anomalies))
    if total_found > len(anomalies):
        print()
        print(f"  {C.DIM}... and {total_found - len(anomalies)} more{C.RESET}")

    if not anomaly_report.get('latest_month_complete', True):
        print()
        print(f"  {C.DIM}Note: {latest_month} is partial, so month-over-month "
              f"comparisons will understate it.{C.RESET}")


def print_duplicate_warning(duplicate_report, currency_format="${amount}", verbose=0):
    """Warn about transactions that appear more than once.

    Cross-file duplicates are shown by default because they almost always mean
    two exports overlap and the totals are inflated. Repeats within a single
    file are usually legitimate, so they need -v.
    """
    if not duplicate_report or not duplicate_report.get('enabled'):
        return

    def fmt(amount):
        return format_currency_decimal(amount, currency_format)

    def fmt_signed(amount):
        return format_currency_signed(amount, currency_format)

    cross = duplicate_report.get('cross_file', [])
    same = duplicate_report.get('same_file', [])

    if cross:
        impact = duplicate_report.get('cross_file_impact', 0)
        print()
        print(f"{C.YELLOW}⚠ {len(cross)} transaction(s) appear in more than one file "
              f"({fmt(impact)} counted twice){C.RESET}")

        shown = cross if verbose >= 1 else cross[:3]
        for group in shown:
            files = ', '.join(os.path.basename(f) for f in group.files)
            print(f"    {group.date}  {fmt_signed(group.amount):>12}  {group.description[:34]:<34} {C.DIM}{files}{C.RESET}")
        if len(cross) > len(shown):
            print(f"    {C.DIM}... and {len(cross) - len(shown)} more{C.RESET}")

        print(f"  {C.DIM}Your totals include these twice. Remove the overlapping export, "
              f"narrow the glob in settings.yaml,{C.RESET}")
        print(f"  {C.DIM}or set 'duplicate_check: off' if the repeats are real.{C.RESET}")

    if same and verbose >= 1:
        impact = duplicate_report.get('same_file_impact', 0)
        print()
        print(f"{C.DIM}{len(same)} repeated transaction(s) within a single file "
              f"({fmt(impact)}). These are often legitimate.{C.RESET}")
        for group in same[:10]:
            print(f"    {group.date}  {fmt_signed(group.amount):>12}  {group.description[:34]:<34} "
                  f"{C.DIM}x{group.count}{C.RESET}")
        if len(same) > 10:
            print(f"    {C.DIM}... and {len(same) - 10} more{C.RESET}")
    elif same:
        print()
        print(f"{C.DIM}{len(same)} repeated transaction(s) within a single file. "
              f"Run with -v to list them.{C.RESET}")


# =============================================================================
# REPORT DIFF - Compare current vs previous report
# =============================================================================

def compare_reports(prev_data: dict, curr_data: dict) -> dict:
    """Compare two report JSON structures and return differences.

    Args:
        prev_data: Previous report data (parsed JSON)
        curr_data: Current report data (parsed JSON)

    Returns:
        Dict with: summary_changes, new_merchants, removed_merchants,
                   tag_changes, category_changes
    """
    diff = {
        'summary_changes': {},
        'new_merchants': [],
        'removed_merchants': [],
        'tag_changes': [],
        'category_changes': [],
    }

    # Compare summary totals
    prev_summary = prev_data.get('summary', {})
    curr_summary = curr_data.get('summary', {})

    for key in ['spending_total', 'income_total', 'cash_flow', 'transfers_total', 'credits_total']:
        prev_val = prev_summary.get(key, 0)
        curr_val = curr_summary.get(key, 0)
        if prev_val != curr_val:
            diff['summary_changes'][key] = {
                'prev': prev_val,
                'curr': curr_val,
                'delta': curr_val - prev_val
            }

    # Build merchant lookups
    prev_merchants = {m['name']: m for m in prev_data.get('merchants', [])}
    curr_merchants = {m['name']: m for m in curr_data.get('merchants', [])}

    prev_names = set(prev_merchants.keys())
    curr_names = set(curr_merchants.keys())

    # New merchants
    for name in sorted(curr_names - prev_names):
        m = curr_merchants[name]
        diff['new_merchants'].append({
            'name': name,
            'total': m.get('total', 0),
            'category': m.get('category', ''),
            'subcategory': m.get('subcategory', ''),
        })

    # Removed merchants
    for name in sorted(prev_names - curr_names):
        m = prev_merchants[name]
        diff['removed_merchants'].append({
            'name': name,
            'total': m.get('total', 0),
            'category': m.get('category', ''),
        })

    # Tag and category changes for existing merchants
    for name in sorted(prev_names & curr_names):
        prev_m = prev_merchants[name]
        curr_m = curr_merchants[name]

        prev_tags = set(prev_m.get('tags', []))
        curr_tags = set(curr_m.get('tags', []))

        if prev_tags != curr_tags:
            lost = prev_tags - curr_tags
            gained = curr_tags - prev_tags
            diff['tag_changes'].append({
                'name': name,
                'lost': sorted(lost),
                'gained': sorted(gained),
            })

        prev_cat = (prev_m.get('category', ''), prev_m.get('subcategory', ''))
        curr_cat = (curr_m.get('category', ''), curr_m.get('subcategory', ''))

        if prev_cat != curr_cat:
            diff['category_changes'].append({
                'name': name,
                'prev_category': prev_cat[0],
                'prev_subcategory': prev_cat[1],
                'curr_category': curr_cat[0],
                'curr_subcategory': curr_cat[1],
            })

    return diff


def has_changes(diff: dict) -> bool:
    """Check if diff contains any changes."""
    return bool(
        diff.get('summary_changes') or
        diff.get('new_merchants') or
        diff.get('removed_merchants') or
        diff.get('tag_changes') or
        diff.get('category_changes')
    )


def format_diff_summary(diff: dict, currency_format: str = "${amount}") -> str:
    """Format diff as a brief summary string.

    Args:
        diff: Output from compare_reports()
        currency_format: Currency format string

    Returns:
        Brief summary string (or empty if no changes)
    """
    if not has_changes(diff):
        return ""

    lines = [f"\n{C.BOLD}Changes since last run:{C.RESET}"]

    # Summary changes
    summary = diff.get('summary_changes', {})
    if 'spending_total' in summary:
        s = summary['spending_total']
        delta_str = f"+{format_currency(s['delta'], currency_format)}" if s['delta'] >= 0 else format_currency(s['delta'], currency_format)
        lines.append(f"  Totals: spending {format_currency(s['prev'], currency_format)} → {format_currency(s['curr'], currency_format)} ({delta_str})")

    # Merchant counts
    new_count = len(diff.get('new_merchants', []))
    removed_count = len(diff.get('removed_merchants', []))
    tag_count = len(diff.get('tag_changes', []))
    cat_count = len(diff.get('category_changes', []))

    merchant_parts = []
    if new_count:
        merchant_parts.append(f"+{new_count} new")
    if removed_count:
        merchant_parts.append(f"{removed_count} removed")
    if tag_count:
        merchant_parts.append(f"{tag_count} tag changes")
    if cat_count:
        merchant_parts.append(f"{cat_count} category changes")

    if merchant_parts:
        lines.append(f"  Merchants: {', '.join(merchant_parts)}")

    lines.append(f"\n  {C.DIM}Use --diff for details.{C.RESET}")

    return "\n".join(lines)


def format_diff_detailed(diff: dict, currency_format: str = "${amount}") -> str:
    """Format diff as detailed output string.

    Args:
        diff: Output from compare_reports()
        currency_format: Currency format string

    Returns:
        Detailed diff string (or empty if no changes)
    """
    if not has_changes(diff):
        return f"\n{C.DIM}No changes since last run.{C.RESET}"

    lines = [f"\n{C.BOLD}{'=' * 60}{C.RESET}"]
    lines.append(f"{C.BOLD}REPORT DIFF{C.RESET}")
    lines.append(f"{C.BOLD}{'=' * 60}{C.RESET}")

    # Summary changes
    summary = diff.get('summary_changes', {})
    if summary:
        lines.append(f"\n{C.BOLD}Summary Changes:{C.RESET}")
        for key, data in summary.items():
            label = key.replace('_', ' ').title()
            delta_str = f"+{format_currency(data['delta'], currency_format)}" if data['delta'] >= 0 else format_currency(data['delta'], currency_format)
            lines.append(f"  {label}: {format_currency(data['prev'], currency_format)} → {format_currency(data['curr'], currency_format)} ({delta_str})")

    # New merchants
    new_merchants = diff.get('new_merchants', [])
    if new_merchants:
        lines.append(f"\n{C.BOLD}New Merchants ({len(new_merchants)}):{C.RESET}")
        for m in new_merchants[:10]:  # Limit to 10
            cat_str = f"{m['category']}/{m['subcategory']}" if m['subcategory'] else m['category']
            lines.append(f"  {C.GREEN}+{C.RESET} {m['name']} ({format_currency(m['total'], currency_format)}, {cat_str})")
        if len(new_merchants) > 10:
            lines.append(f"  {C.DIM}... and {len(new_merchants) - 10} more{C.RESET}")

    # Removed merchants
    removed_merchants = diff.get('removed_merchants', [])
    if removed_merchants:
        lines.append(f"\n{C.BOLD}Removed Merchants ({len(removed_merchants)}):{C.RESET}")
        for m in removed_merchants[:10]:
            lines.append(f"  {C.RED}-{C.RESET} {m['name']} ({format_currency(m['total'], currency_format)})")
        if len(removed_merchants) > 10:
            lines.append(f"  {C.DIM}... and {len(removed_merchants) - 10} more{C.RESET}")

    # Tag changes
    tag_changes = diff.get('tag_changes', [])
    if tag_changes:
        lines.append(f"\n{C.BOLD}Tag Changes ({len(tag_changes)}):{C.RESET}")
        for t in tag_changes[:10]:
            parts = []
            if t['lost']:
                parts.append(f"lost '{', '.join(t['lost'])}'")
            if t['gained']:
                parts.append(f"gained '{', '.join(t['gained'])}'")
            lines.append(f"  {t['name']}: {'; '.join(parts)}")
        if len(tag_changes) > 10:
            lines.append(f"  {C.DIM}... and {len(tag_changes) - 10} more{C.RESET}")

    # Category changes
    cat_changes = diff.get('category_changes', [])
    if cat_changes:
        lines.append(f"\n{C.BOLD}Category Changes ({len(cat_changes)}):{C.RESET}")
        for c in cat_changes[:10]:
            prev_cat = f"{c['prev_category']}/{c['prev_subcategory']}" if c['prev_subcategory'] else c['prev_category']
            curr_cat = f"{c['curr_category']}/{c['curr_subcategory']}" if c['curr_subcategory'] else c['curr_category']
            lines.append(f"  {c['name']}: {prev_cat} → {curr_cat}")
        if len(cat_changes) > 10:
            lines.append(f"  {C.DIM}... and {len(cat_changes) - 10} more{C.RESET}")

    lines.append(f"\n{C.BOLD}{'=' * 60}{C.RESET}")

    return "\n".join(lines)


