"""
Spending Analyzer - Core analysis logic.

Analyzes AMEX and BOA transactions using merchant categorization rules.
"""

import json
from collections import defaultdict
from datetime import datetime

from . import section_engine

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

    # Track excluded transactions separately (for transparency in UI)
    excluded_transactions = []

    # Special tags that affect spending analysis
    EXCLUDE_TAGS = {'income', 'transfer'}  # Excluded from spending totals

    for txn in transactions:
        # Check for special tags: income, transfer -> exclude from spending
        txn_tags = set(t.lower() for t in txn.get('tags', []))
        excluded_reason = txn.get('excluded')
        if not excluded_reason and (txn_tags & EXCLUDE_TAGS):
            excluded_reason = 'tagged-' + next(iter(txn_tags & EXCLUDE_TAGS))

        if excluded_reason:
            excluded_transactions.append({
                'date': txn['date'].strftime('%m/%d'),
                'month': txn['date'].strftime('%Y-%m'),
                'description': txn.get('raw_description', txn['description']),
                'merchant': txn['merchant'],
                'amount': txn['amount'],
                'category': txn['category'],
                'subcategory': txn['subcategory'],
                'source': txn['source'],
                'location': txn.get('location'),
                'tags': txn.get('tags', []),
                'excluded_reason': excluded_reason,
            })
            continue  # Don't include in spending totals
        key = (txn['category'], txn['subcategory'])
        by_category[key]['count'] += 1
        by_category[key]['total'] += txn['amount']

        month_key = txn['date'].strftime('%Y-%m')

        # Track by merchant
        by_merchant[txn['merchant']]['count'] += 1
        by_merchant[txn['merchant']]['total'] += txn['amount']
        by_merchant[txn['merchant']]['category'] = txn['category']
        by_merchant[txn['merchant']]['subcategory'] = txn['subcategory']
        by_merchant[txn['merchant']]['months'].add(month_key)
        by_merchant[txn['merchant']]['monthly_amounts'][month_key] += txn['amount']
        by_merchant[txn['merchant']]['payments'].append(txn['amount'])
        by_merchant[txn['merchant']]['transactions'].append({
            'date': txn['date'].strftime('%m/%d'),
            'month': month_key,
            'description': txn.get('raw_description', txn['description']),
            'amount': txn['amount'],
            'source': txn['source'],
            'location': txn.get('location'),
            'tags': txn.get('tags', [])
        })
        # Track max payment
        if txn['amount'] > by_merchant[txn['merchant']]['max_payment']:
            by_merchant[txn['merchant']]['max_payment'] = txn['amount']
        # Store match info (pattern that matched) - first transaction sets this
        if 'match_info' not in by_merchant[txn['merchant']] and txn.get('match_info'):
            by_merchant[txn['merchant']]['match_info'] = txn['match_info']
        # Collect tags from all transactions
        by_merchant[txn['merchant']]['tags'].update(txn.get('tags', []))
        # Track raw description variations
        raw_desc = txn.get('raw_description', txn.get('description', ''))
        by_merchant[txn['merchant']]['raw_descriptions'][raw_desc] += 1

        by_month[month_key] += txn['amount']

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

    # Legacy bucket support - all merchants go into variable
    # Views.rules handles custom grouping
    monthly_merchants = {}
    annual_merchants = {}
    periodic_merchants = {}
    one_off_merchants = {}
    variable_merchants = dict(by_merchant)

    # Bucket totals - all spending is variable now (views.rules handles custom grouping)
    monthly_total = 0
    annual_total = 0
    periodic_total = 0
    one_off_total = 0
    variable_total = sum(d['total'] for d in variable_merchants.values())

    # Monthly value averages - all in variable
    monthly_avg = 0
    annual_monthly = 0
    periodic_monthly = 0
    variable_monthly = sum(d.get('monthly_value', 0) for d in variable_merchants.values())

    # Calculate totals only from non-excluded transactions
    included_transactions = [t for t in transactions if not t.get('excluded')]

    return {
        'by_category': dict(by_category),
        'by_merchant': {k: dict(v) for k, v in by_merchant.items()},
        'by_month': dict(by_month),
        'total': sum(t['amount'] for t in included_transactions),
        'count': len(included_transactions),
        'num_months': num_months,
        # Classified merchants
        'monthly_merchants': monthly_merchants,
        'annual_merchants': annual_merchants,
        'periodic_merchants': periodic_merchants,
        'one_off_merchants': one_off_merchants,
        'variable_merchants': variable_merchants,
        # Totals (YTD)
        'monthly_total': monthly_total,
        'annual_total': annual_total,
        'periodic_total': periodic_total,
        'one_off_total': one_off_total,
        'variable_total': variable_total,
        # True monthly averages
        'monthly_avg': monthly_avg,         # Avg when active
        'annual_monthly': annual_monthly,   # Annual / 12
        'periodic_monthly': periodic_monthly, # Periodic / 12
        'variable_monthly': variable_monthly,
        'true_monthly': monthly_avg + annual_monthly + periodic_monthly + variable_monthly,
        # Excluded transactions (for UI transparency)
        'excluded_transactions': excluded_transactions,
        'excluded_count': len(excluded_transactions),
        'excluded_total': sum(t['amount'] for t in excluded_transactions),
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


def export_json(stats, verbose=0, only=None, category_filter=None, merchant_filter=None):
    """Export analysis results as JSON with reasoning.

    Args:
        stats: Analysis results from analyze_transactions()
        verbose: Verbosity level (0=basic, 1=trace, 2=full)
        only: List of classifications to include (e.g., ['monthly', 'variable'])
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)

    Returns: JSON string
    """
    import json

    output = {
        'summary': {
            'total_spending': round(stats['total'], 2),
            'monthly_budget': round(stats['true_monthly'], 2),
            'num_months': stats['num_months'],
            'breakdown': {
                'monthly_recurring': round(stats['monthly_avg'], 2),
                'annual_monthly': round(stats['annual_monthly'], 2),
                'periodic_monthly': round(stats['periodic_monthly'], 2),
                'variable_monthly': round(stats['variable_monthly'], 2),
            },
            'totals': {
                'monthly': round(stats['monthly_total'], 2),
                'annual': round(stats['annual_total'], 2),
                'periodic': round(stats['periodic_total'], 2),
                'one_off': round(stats['one_off_total'], 2),
                'variable': round(stats['variable_total'], 2),
            }
        },
        'classifications': {}
    }

    # Classification sections to process
    all_sections = ['monthly', 'annual', 'periodic', 'one_off', 'variable']
    sections = only if only else all_sections

    for section in sections:
        if section not in all_sections:
            continue
        merchants_dict = stats.get(f'{section}_merchants', {})
        merchants = []

        for name, data in merchants_dict.items():
            # Apply filters
            if category_filter and data.get('category') != category_filter:
                continue
            if merchant_filter and name not in merchant_filter:
                continue

            merchants.append(build_merchant_json(name, data, verbose))

        # Sort by monthly value descending
        merchants.sort(key=lambda x: x['monthly_value'], reverse=True)
        output['classifications'][section] = merchants

    return json.dumps(output, indent=2)


def export_markdown(stats, verbose=0, only=None, category_filter=None, merchant_filter=None):
    """Export analysis results as Markdown with reasoning.

    Args:
        stats: Analysis results from analyze_transactions()
        verbose: Verbosity level (0=basic, 1=trace, 2=full)
        only: List of classifications to include (e.g., ['monthly', 'variable'])
        category_filter: Only include merchants in this category
        merchant_filter: Only include these merchants (list of names)

    Returns: Markdown string
    """
    lines = ['# Spending Analysis\n']

    # Summary
    lines.append('## Summary\n')
    lines.append(f"- **Monthly Budget:** ${stats['true_monthly']:.2f}/mo")
    lines.append(f"- **Total Spending (YTD):** ${stats['total']:.2f}")
    lines.append(f"- **Data Period:** {stats['num_months']} months\n")

    # Classification sections to process
    all_sections = ['monthly', 'annual', 'periodic', 'one_off', 'variable']
    section_names = {
        'monthly': 'Every Month',
        'annual': 'Once a Year',
        'periodic': 'A Few Times/Year',
        'one_off': 'Large One-Time',
        'variable': 'Varies by Month',
    }
    sections = only if only else all_sections

    for section in sections:
        if section not in all_sections:
            continue
        merchants_dict = stats.get(f'{section}_merchants', {})
        if not merchants_dict:
            continue

        lines.append(f"\n## {section_names.get(section, section)}\n")

        # Sort by monthly value
        sorted_merchants = sorted(
            merchants_dict.items(),
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
            lines.append(f"**Classification:** {section.replace('_', ' ').title()}")
            lines.append(f"**Reason:** {reasoning.get('decision', 'N/A')}")
            lines.append(f"**Category:** {data.get('category', '')} > {data.get('subcategory', '')}")
            lines.append(f"**Monthly Value:** ${data.get('monthly_value', 0):.2f}")
            lines.append(f"**YTD Total:** ${data.get('total', 0):.2f}")
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
                thresholds = reasoning.get('thresholds', {})
                if thresholds:
                    lines.append(f"  Thresholds: bill={thresholds.get('bill_threshold')}, general={thresholds.get('general_threshold')}")

            lines.append('')  # Empty line between merchants

    return '\n'.join(lines)


def print_summary(stats, year=2025, filter_category=None, currency_format="${amount}"):
    """Print analysis summary."""
    # Local helper for currency formatting
    def fmt(amount):
        return format_currency(amount, currency_format)

    by_category = stats['by_category']
    monthly_merchants = stats['monthly_merchants']
    annual_merchants = stats['annual_merchants']
    periodic_merchants = stats['periodic_merchants']
    one_off_merchants = stats['one_off_merchants']
    variable_merchants = stats['variable_merchants']

    # Calculate actual spending (transactions tagged income/transfer already excluded)
    actual_spending = sum(data['total'] for (cat, sub), data in by_category.items())

    # =========================================================================
    # MONTHLY BUDGET SUMMARY
    # =========================================================================
    print("=" * 80)
    print(f"{year} SPENDING ANALYSIS (Occurrence-Based)")
    print("=" * 80)

    print("\nMONTHLY BUDGET")
    print("-" * 50)
    print(f"Every Month (6+ mo):         {fmt(stats['monthly_avg']):>14}/mo")
    print(f"Varies by Month:             {fmt(stats['variable_monthly']):>14}/mo")
    print(f"                             {'-'*14}")
    print(f"TRUE MONTHLY BUDGET:         {fmt(stats['monthly_avg'] + stats['variable_monthly']):>14}/mo")
    print()
    print("NON-RECURRING (YTD)")
    print("-" * 50)
    print(f"Once a Year:                 {fmt(stats['annual_total']):>14}")
    print(f"A Few Times/Year:            {fmt(stats['periodic_total']):>14}")
    print(f"Large One-Time:              {fmt(stats['one_off_total']):>14}")
    print(f"                             {'-'*14}")
    print(f"Total Non-Recurring:         {fmt(stats['annual_total'] + stats['periodic_total'] + stats['one_off_total']):>14}")
    print()
    print(f"TOTAL SPENDING (YTD):        {fmt(actual_spending):>14}")

    # Show excluded transactions info
    excluded_count = stats.get('excluded_count', 0)
    excluded_total = stats.get('excluded_total', 0)
    if excluded_count > 0:
        print()
        print(f"Excluded (income/transfer):  {fmt(excluded_total):>14}  ({excluded_count} transactions)")
    else:
        # Hint about special tags when none are used
        print()
        print("TIP: Use special tags to exclude non-spending transactions:")
        print("     income   - salary, deposits    (excluded from totals)")
        print("     transfer - CC payments, moves  (excluded from totals)")
        print("     refund   - returns, credits    (shown in Credits section)")

    # =========================================================================
    # EVERY MONTH (6+ months)
    # =========================================================================
    print("\n" + "=" * 80)
    print("EVERY MONTH (Appears 6+ Months)")
    print("=" * 80)
    print(f"\n{'Merchant':<26} {'Mo':>3} {'Type':<6} {'Monthly':>10} {'YTD':>12}")
    print("-" * 62)

    sorted_monthly = sorted(monthly_merchants.items(),
        key=lambda x: x[1]['avg_when_active'] if x[1]['is_consistent'] else x[1]['total']/12,
        reverse=True)
    for merchant, data in sorted_monthly[:25]:
        if data['is_consistent']:
            calc_type = "avg"
            monthly = data['avg_when_active']
        else:
            calc_type = "/12"
            monthly = data['total'] / 12
        print(f"{merchant:<26} {data['months_active']:>3} {calc_type:<6} {fmt(monthly):>12} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL':<26} {'':<3} {'':<6} {fmt(stats['monthly_avg']):>12}/mo {fmt(stats['monthly_total']):>14}")

    # =========================================================================
    # ONCE A YEAR
    # =========================================================================
    print("\n" + "=" * 80)
    print("ONCE A YEAR")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Total':>12}")
    print("-" * 58)

    sorted_annual = sorted(annual_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_annual:
        print(f"{merchant:<28} {data['subcategory']:<15} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL':<28} {'':<15} {fmt(stats['annual_total']):>14}")

    # =========================================================================
    # A FEW TIMES/YEAR
    # =========================================================================
    print("\n" + "=" * 80)
    print("A FEW TIMES/YEAR")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Count':>6} {'Total':>12}")
    print("-" * 65)

    sorted_periodic = sorted(periodic_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_periodic:
        print(f"{merchant:<28} {data['subcategory']:<15} {data['count']:>6} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL':<28} {'':<15} {'':<6} {fmt(stats['periodic_total']):>14}")

    # =========================================================================
    # LARGE ONE-TIME
    # =========================================================================
    print("\n" + "=" * 80)
    print("LARGE ONE-TIME")
    print("=" * 80)
    print(f"\n{'Merchant':<28} {'Category':<15} {'Total':>12}")
    print("-" * 58)

    sorted_oneoff = sorted(one_off_merchants.items(), key=lambda x: x[1]['total'], reverse=True)
    for merchant, data in sorted_oneoff[:15]:
        print(f"{merchant:<28} {data['category']:<15} {fmt(data['total']):>14}")

    print(f"\n{'TOTAL ONE-OFF':<28} {'':<15} {fmt(stats['one_off_total']):>14}")

    # =========================================================================
    # VARIES BY MONTH
    # =========================================================================
    print("\n" + "=" * 80)
    print("VARIES BY MONTH")
    print("=" * 80)
    print(f"\n{'Category':<18} {'Subcategory':<15} {'Months':>6} {'Avg/Mo':>10} {'YTD':>12}")
    print("-" * 70)

    # Group variable merchants by category
    variable_by_cat = defaultdict(lambda: {'total': 0, 'months': set()})
    for merchant, data in variable_merchants.items():
        key = (data['category'], data['subcategory'])
        variable_by_cat[key]['total'] += data['total']
        variable_by_cat[key]['months'].update(data['months'])

    sorted_var_cats = sorted(variable_by_cat.items(), key=lambda x: x[1]['total'], reverse=True)
    for (cat, subcat), info in sorted_var_cats[:20]:
        if filter_category and cat.lower() != filter_category.lower():
            continue
        months_active = len(info['months'])
        avg = info['total'] / months_active if months_active > 0 else 0
        print(f"{cat:<18} {subcat:<15} {months_active:>6} {fmt(avg):>12} {fmt(info['total']):>14}")

    print(f"\n{'TOTAL VARIABLE':<18} {'':<15} {'':<6} {fmt(stats['variable_monthly']):>12}/mo {fmt(stats['variable_total']):>14}")


def print_sections_summary(stats, year=2025, currency_format="${amount}", only_filter=None):
    """Print sections-based analysis summary.

    Args:
        stats: Analysis statistics dict
        year: Year for display
        currency_format: Format string for currency
        only_filter: Optional list of section names (lowercase) to show
    """
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

    print()
    print("=" * 80)


