"""
Transaction Analyzer - Core analysis logic.

Analyzes transactions using merchant categorization rules.
"""

import json
from collections import defaultdict
from datetime import datetime

from . import section_engine
from .classification import (
    categorize_amount,
    normalize_amount,
    is_excluded_from_spending,
    calculate_cash_flow,
    calculate_transfers_net,
)

# Import parsing functions from parsers module (and re-export for backwards compatibility)
from .parsers import (
    parse_amount,
    extract_location,
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
        by_merchant[txn['merchant']]['category'] = txn['category']
        by_merchant[txn['merchant']]['subcategory'] = txn['subcategory']
        by_merchant[txn['merchant']]['months'].add(month_key)
        by_merchant[txn['merchant']]['monthly_amounts'][month_key] += effective_amount
        by_merchant[txn['merchant']]['payments'].append(effective_amount)
        txn_data = {
            'date': txn['date'].strftime('%m/%d'),
            'month': month_key,
            'description': txn.get('raw_description', txn['description']),
            'amount': effective_amount,
            'source': txn['source'],
            'location': txn.get('location'),
            'tags': txn.get('tags', [])
        }
        # Include extra_fields from field: directives
        if txn.get('extra_fields'):
            txn_data['extra_fields'] = txn['extra_fields']
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
        data['classification'] = 'variable'
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
        # Skip merchants excluded from spending (income, transfer, investment)
        # They appear on their respective cards, not in spending sections
        if is_excluded_from_spending(list(data.get('tags', []))):
            continue

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

    result = {
        'name': merchant_name,
        'classification': data.get('classification', 'unknown'),
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
        result['pattern'] = {
            'matched': match_info.get('pattern', ''),
            'source': match_info.get('source', 'unknown'),
            'tags': match_info.get('tags', []),
        }

    return result


def export_json(stats, verbose=0, category_filter=None, merchant_filter=None):
    """Export analysis results as JSON with reasoning.

    Args:
        stats: Analysis results from analyze_transactions()
        verbose: Verbosity level (0=basic, 1=trace, 2=full)
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)

    Returns: JSON string
    """
    import json

    by_merchant = stats.get('by_merchant', {})
    by_month = stats.get('by_month', {})
    by_category = stats.get('by_category', {})

    # Calculate gross spending and credits
    gross_spending = sum(d['total'] for d in by_merchant.values() if d['total'] > 0)
    credits_total = abs(sum(d['total'] for d in by_merchant.values() if d['total'] < 0))

    # Calculate income and transfers from merchants by tag
    income_total = sum(d['total'] for d in by_merchant.values()
                       if 'income' in [t.lower() for t in d.get('tags', set())])
    transfers_total = abs(sum(d['total'] for d in by_merchant.values()
                              if 'transfer' in [t.lower() for t in d.get('tags', set())]))

    output = {
        'summary': {
            'total_spending': round(stats['total'], 2),
            'gross_spending': round(gross_spending, 2),
            'credits_total': round(credits_total, 2),
            'monthly_budget': round(stats['monthly_avg'], 2),
            'num_months': stats['num_months'],
            'income_total': round(income_total, 2),
            'transfers_total': round(transfers_total, 2),
            'net_cash_flow': round(income_total - stats['total'], 2) if income_total > 0 else None,  # transfers excluded
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
            {'merchant': name, 'category': data.get('category', ''), 'amount': round(abs(data['total']), 2)}
            for name, data in by_merchant.items() if data['total'] < 0
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

    return json.dumps(output, indent=2)


def export_markdown(stats, verbose=0, category_filter=None, merchant_filter=None, currency_format="${amount}"):
    """Export analysis results as Markdown with reasoning.

    Args:
        stats: Analysis results from analyze_transactions()
        verbose: Verbosity level (0=basic, 1=trace, 2=full)
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)
        currency_format: Format string for currency (e.g. "${amount}" or "£{amount}")

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

    # Use cash flow values from stats
    income_total = stats.get('income_total', 0)
    spending_total = stats.get('spending_total', 0)
    credits_total = stats.get('credits_total', 0)
    cash_flow = stats.get('cash_flow', 0)
    transfers_in = stats.get('transfers_in', 0)
    transfers_out = stats.get('transfers_out', 0)
    transfers_net = stats.get('transfers_net', 0)

    lines = ['# Financial Report\n']

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

    # Credits/Refunds
    credit_merchants = [(m, d) for m, d in by_merchant.items() if d['total'] < 0]
    if credit_merchants:
        lines.append('## Credits/Refunds\n')
        lines.append('| Merchant | Category | Amount |')
        lines.append('|----------|----------|--------|')
        for name, data in sorted(credit_merchants, key=lambda x: x[1]['total']):
            lines.append(f"| {name} | {data.get('category', '')} | {fmt(data['total'], show_sign=True)} |")
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


def print_summary(stats, year=2025, filter_category=None, currency_format="${amount}"):
    """Print analysis summary."""
    # Lazy import to avoid circular dependency
    from .cli import C

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

    # Calculate gross spending (positive merchants only) for display
    gross_spending = sum(d['total'] for d in by_merchant.values() if d['total'] > 0)

    # =========================================================================
    # FINANCIAL SUMMARY
    # =========================================================================
    print("=" * 80)
    print(f"{year} FINANCIAL REPORT")
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
    # CREDITS/REFUNDS (if any negative totals)
    # =========================================================================
    credit_merchants = [(m, d) for m, d in by_merchant.items() if d['total'] < 0]
    if credit_merchants:
        print("\n" + "=" * 80)
        print("CREDITS/REFUNDS")
        print("=" * 80)
        print(f"\n{'Merchant':<30} {'Category':<20} {'Amount':>14}")
        print("-" * 68)
        for merchant, data in sorted(credit_merchants, key=lambda x: x[1]['total']):
            category = data.get('category', 'Unknown')[:20]
            print(f"{merchant:<30} {category:<20} +{fmt(abs(data['total'])):>14}")
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
    print("BY CATEGORY")
    print("=" * 80)
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


def print_sections_summary(stats, year=2025, currency_format="${amount}", only_filter=None):
    """Print sections-based analysis summary.

    Args:
        stats: Analysis statistics dict
        year: Year for display
        currency_format: Format string for currency
        only_filter: Optional list of section names (lowercase) to show
    """
    # Lazy import to avoid circular dependency
    from .cli import C

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
    print(f"{year} SPENDING ANALYSIS")
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


