"""
HTML Report Generation - Generate spending analysis HTML reports.

This module handles generation of interactive HTML reports from analyzed transaction data.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .explain_utils import explain_pattern, explain_view_filter

# Try to import sentence_transformers for semantic search
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    SentenceTransformer = None

_EMBEDDINGS_MODEL = None


def get_template_dir():
    """Get the directory containing template files.

    When running as a PyInstaller bundle, files are in sys._MEIPASS/tally/.
    Otherwise, they're in the same directory as this module.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle
        return Path(sys._MEIPASS) / 'tally'
    else:
        # Running as normal Python
        return Path(__file__).parent


# ============================================================================
# CURRENCY FORMATTING (used by report generation)
# ============================================================================

def format_currency(amount: float, currency_format: str = "${amount}") -> str:
    """Format amount with currency symbol/format (no decimals).

    Args:
        amount: The amount to format
        currency_format: Format string with {amount} placeholder, e.g. "${amount}" or "{amount} zl"

    Returns:
        Formatted currency string, e.g. "$1,234" or "1,234 zl"

    Negative amounts put the minus in front of the whole thing ("-$1,234"),
    matching formatCurrency() in spending_report.js so the CLI and the HTML
    report never disagree.
    """
    formatted_num = f"{abs(amount):,.0f}"
    sign = '-' if round(amount) < 0 else ''
    return sign + currency_format.format(amount=formatted_num)


def format_currency_decimal(amount: float, currency_format: str = "${amount}") -> str:
    """Format amount with currency symbol/format (with 2 decimal places).

    Args:
        amount: The amount to format
        currency_format: Format string with {amount} placeholder

    Returns:
        Formatted currency string with decimals, e.g. "$1,234.56"

    Negative amounts are rendered as "-$1,234.56" for the same reason as
    format_currency().
    """
    formatted_num = f"{abs(amount):,.2f}"
    sign = '-' if round(amount, 2) < 0 else ''
    return sign + currency_format.format(amount=formatted_num)


def format_currency_signed(amount: float, currency_format: str = "${amount}") -> str:
    """Format an amount with an explicit leading sign.

    The sign goes outside the currency symbol ("-$200.00"), which naive
    formatting of a negative number would render as "$-200.00".
    """
    sign = '+' if amount > 0 else ''
    return f"{sign}{format_currency_decimal(amount, currency_format)}"


# ============================================================================
# EMBEDDINGS
# ============================================================================

def generate_embeddings(items):
    """Generate embeddings for a list of text items using sentence-transformers."""
    if not EMBEDDINGS_AVAILABLE:
        return None

    print("Generating semantic embeddings...")
    # Use a small, fast model optimized for semantic similarity.
    global _EMBEDDINGS_MODEL
    if _EMBEDDINGS_MODEL is None:
        _EMBEDDINGS_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
    embeddings = _EMBEDDINGS_MODEL.encode(items, show_progress_bar=False)
    return embeddings.tolist()


# ============================================================================
# VUE-BASED HTML REPORT (Modern)
# ============================================================================

def _budgets_payload(budgets):
    """Shape the budget report for the Vue app, or None when unused."""
    if not budgets or not budgets.get('enabled') or not budgets.get('results'):
        return None
    from .budgets import budget_report_to_dict
    return budget_report_to_dict(budgets)


def _anomalies_payload(anomalies):
    """Shape the anomaly report for the Vue app, or None when there is nothing."""
    if not anomalies or not anomalies.get('enabled') or not anomalies.get('anomalies'):
        return None
    from .anomalies import anomaly_report_to_dict
    return anomaly_report_to_dict(anomalies)


def _duplicates_payload(duplicates):
    """Shape the duplicate report for the Vue app, or None when clean."""
    if not duplicates or not duplicates.get('enabled') or not duplicates.get('cross_file'):
        return None
    from .duplicates import duplicate_report_to_dict
    return duplicate_report_to_dict(duplicates)


def write_summary_file_vue(stats, filepath, year=None, currency_format="${amount}", sources=None,
                           embedded_html=True, title=None, budgets=None, anomalies=None,
                           duplicates=None):
    """Write summary to HTML file using Vue 3 for client-side rendering.

    Args:
        stats: Analysis statistics dict
        filepath: Output file path
        year: Deprecated - use title instead
        currency_format: Format string for currency display, e.g. "${amount}" or "{amount} zl"
        sources: List of data source names (e.g., ['Amex', 'Chase'])
        embedded_html: If True (default), embed CSS/JS inline. If False, output separate files.
        title: Custom report title (e.g., "2025 Budget Analysis")
        budgets: Optional budget report from budgets.build_budget_report()
        anomalies: Optional report from anomalies.detect_anomalies()
        duplicates: Optional report from duplicates.build_duplicate_report()
    """
    sources = sources or []

    # Load template files
    template_dir = get_template_dir()
    html_template = (template_dir / 'spending_report.html').read_text(encoding='utf-8')
    css_content = (template_dir / 'spending_report.css').read_text(encoding='utf-8')
    js_content = (template_dir / 'spending_report.js').read_text(encoding='utf-8')

    # Get number of months for averaging
    num_months = stats['num_months']
    by_merchant = stats.get('by_merchant', {})

    # Helper function to create merchant IDs
    def make_merchant_id(name):
        return name.replace("'", "").replace('"', '').replace(' ', '_')

    # Build section merchants data
    def build_section_merchants(merchant_dict):
        merchants = {}
        for merchant_name, data in merchant_dict.items():
            merchant_id = make_merchant_id(merchant_name)

            # Build transactions array with unique IDs
            txns = []
            for i, txn in enumerate(data.get('transactions', [])):
                txn_json = {
                    'id': f"{merchant_id}_{i}",
                    'date': txn.get('date', ''),
                    'month': txn.get('month', ''),
                    # Use transformed description if available, otherwise raw_description
                    'description': txn.get('description') if txn.get('original_description') else txn.get('raw_description', txn.get('description', '')),
                    'amount': txn.get('amount', 0),
                    'source': txn.get('source', ''),

                    'tags': txn.get('tags', [])
                }
                # Include extra_fields from field: directives
                if txn.get('extra_fields'):
                    txn_json['extra_fields'] = txn['extra_fields']
                # Include original_description if transform was applied
                if txn.get('original_description'):
                    txn_json['original_description'] = txn['original_description']
                txns.append(txn_json)

            # Build match info for tooltip
            match_info = data.get('match_info')
            match_info_json = None
            if match_info:
                pattern = match_info.get('pattern', '')
                match_info_json = {
                    'pattern': pattern,
                    'source': match_info.get('source', ''),
                    'explanation': explain_pattern(pattern),
                    'assignedMerchant': merchant_name,
                    'assignedCategory': data.get('category', ''),
                    'assignedSubcategory': data.get('subcategory', ''),
                    'assignedTags': sorted(match_info.get('tags', [])),
                    'tagSources': match_info.get('tag_sources', {}),
                }

            merchants[merchant_id] = {
                'id': merchant_id,
                'displayName': merchant_name,
                'category': data.get('category', 'Other'),
                'subcategory': data.get('subcategory', 'Uncategorized'),
                'categoryPath': f"{data.get('category', 'Other')}/{data.get('subcategory', 'Uncategorized')}".lower(),
                'calcType': data.get('calc_type', '/12'),
                'monthsActive': data.get('months_active', 0),
                'isConsistent': data.get('is_consistent', False),
                'ytd': data.get('total', 0),
                'monthly': data.get('avg_when_active') or (data.get('total', 0) / num_months if num_months > 0 else 0),
                'count': data.get('count', len(txns)),
                'transactions': txns,
                'tags': sorted(data.get('tags', set())),  # Convert set to sorted list
                'matchInfo': match_info_json,  # Pattern/source for explain tooltip
            }
        return merchants

    sections = {}

    # Use user-defined views from views.rules
    user_sections = stats.get('sections')
    sections_config = stats.get('_sections_config')

    # Build lookups for section descriptions and filters from config
    section_descriptions = {}
    section_filters = {}
    if sections_config:
        for section in sections_config.sections:
            section_descriptions[section.name] = section.description
            section_filters[section.name] = section.filter_expr

    if user_sections:
        for section_name, section_data in user_sections.items():
            section_id = section_name.lower().replace(' ', '_')
            merchants_list = section_data.get('merchants', [])

            if not merchants_list:
                continue

            # Convert list of (name, data) tuples to dict format
            merchant_dict = {name: data for name, data in merchants_list}
            merchants = build_section_merchants(merchant_dict)

            # Add view info to each merchant
            view_filter = section_filters.get(section_name, '')
            for merchant_id, merchant in merchants.items():
                merchant['viewInfo'] = {
                    'viewName': section_name,
                    'filterExpr': view_filter,
                    'explanation': explain_view_filter(view_filter) if view_filter else '',
                }

            if merchants:
                # Use description from config, or empty string if not set
                description = section_descriptions.get(section_name, '')
                sections[section_id] = {
                    'title': section_name,
                    'hasMonthlyColumn': True,  # All sections show monthly
                    'description': description,
                    'merchants': merchants
                }

    # Calculate data through date (latest transaction date)
    latest_date = ''
    for data in by_merchant.values():
        for txn in data.get('transactions', []):
            if txn.get('date', '') > latest_date:
                latest_date = txn.get('date', '')

    # Build category view - group all merchants by category -> subcategory
    # This uses by_merchant (all merchants) so it's not filtered by views.rules
    def build_category_view():
        # Build from by_merchant which contains ALL merchants (not filtered by sections)
        all_merchants = {}
        by_merchant = stats.get('by_merchant', {})
        for merchant_name, data in by_merchant.items():
            merchant_id = make_merchant_id(merchant_name)
            all_merchants[merchant_id] = build_section_merchants({merchant_name: data})[merchant_id]

        # Group by category -> subcategory
        categories = {}
        for merchant_id, merchant in all_merchants.items():
            cat = merchant.get('category', 'Uncategorized') or 'Uncategorized'
            subcat = merchant.get('subcategory', 'Other') or 'Other'

            # Handle unknown merchants
            if cat == 'Unknown':
                cat = 'Uncategorized'
                subcat = 'Unknown'

            if cat not in categories:
                categories[cat] = {
                    'total': 0,
                    'monthly': 0,
                    'count': 0,
                    'subcategories': {}
                }

            if subcat not in categories[cat]['subcategories']:
                categories[cat]['subcategories'][subcat] = {
                    'total': 0,
                    'monthly': 0,
                    'count': 0,
                    'merchants': {}
                }

            # Add merchant to subcategory
            categories[cat]['subcategories'][subcat]['merchants'][merchant_id] = merchant
            categories[cat]['subcategories'][subcat]['total'] += merchant.get('ytd', 0)
            categories[cat]['subcategories'][subcat]['monthly'] += merchant.get('monthly', 0)
            categories[cat]['subcategories'][subcat]['count'] += merchant.get('count', 0)

            # Update category totals
            categories[cat]['total'] += merchant.get('ytd', 0)
            categories[cat]['monthly'] += merchant.get('monthly', 0)
            categories[cat]['count'] += merchant.get('count', 0)

        # Compute typeTotals from transactions for each category
        # This tracks spending/income/investment/transfer breakdown by transaction tags
        for cat_name, cat_data in categories.items():
            type_totals = {'spending': 0, 'income': 0, 'investment': 0, 'transfer': 0}

            for subcat in cat_data['subcategories'].values():
                for merchant in subcat['merchants'].values():
                    for txn in merchant.get('transactions', []):
                        txn_tags = set(t.lower() for t in txn.get('tags', []))
                        amount = txn.get('amount', 0)

                        if 'income' in txn_tags:
                            type_totals['income'] += abs(amount)
                        elif 'investment' in txn_tags:
                            type_totals['investment'] += abs(amount)
                        elif 'transfer' in txn_tags:
                            type_totals['transfer'] += abs(amount)
                        elif amount >= 0:  # Positive = spending
                            type_totals['spending'] += amount
                        # Negative amounts without special tags are credits (refunds)

            cat_data['typeTotals'] = type_totals

        return categories

    category_view = build_category_view()

    # Build final spending data object
    spending_data = {
        'title': title,  # Custom report title (None = auto-generate in JS)
        'numMonths': num_months,
        'sources': sources,
        'dataThrough': latest_date,
        'currencyFormat': currency_format,  # For JS formatting (e.g., "${amount}" or "£{amount}")
        'sections': sections,
        'categoryView': category_view,
        # Cash flow (excludes transfers and investments)
        # All values are positive; cash_flow = income - spending + credits
        'incomeTotal': stats.get('income_total', 0),
        'spendingTotal': stats.get('spending_total', 0),
        'creditsTotal': stats.get('credits_total', 0),  # Refunds (positive value)
        'cashFlow': stats.get('cash_flow', 0),
        # Transfers (money moving between accounts, both positive)
        'transfersIn': stats.get('transfers_in', 0),
        'transfersOut': stats.get('transfers_out', 0),  # Positive value
        'transfersNet': stats.get('transfers_net', 0),  # in - out
        # Investments (401K, IRA - excluded from spending)
        'investmentTotal': stats.get('investment_total', 0),
        # Review data. Each key is null when the feature is unused, so the Vue
        # app can hide the corresponding panel entirely.
        'budgets': _budgets_payload(budgets),
        'anomalies': _anomalies_payload(anomalies),
        'duplicates': _duplicates_payload(duplicates),
    }

    # Assemble final HTML
    data_script = f'window.spendingData = {json.dumps(spending_data)};'

    if not embedded_html:
        # Write separate files for easier development
        output_path = Path(filepath)
        output_dir = output_path.parent

        # Write CSS file
        css_path = output_dir / 'spending_report.css'
        css_path.write_text(css_content, encoding='utf-8')

        # Write JS file
        js_path = output_dir / 'spending_report.js'
        js_path.write_text(js_content, encoding='utf-8')

        # Write data file
        data_path = output_dir / 'spending_data.js'
        data_path.write_text(data_script, encoding='utf-8')

        # Create HTML with external references
        final_html = html_template.replace(
            '<style>/* CSS_PLACEHOLDER */</style>',
            '<link rel="stylesheet" href="spending_report.css">'
        ).replace(
            '<script>/* DATA_PLACEHOLDER */</script>',
            '<script src="spending_data.js"></script>'
        ).replace(
            '<script>/* JS_PLACEHOLDER */</script>',
            '<script src="spending_report.js"></script>'
        )
    else:
        # Embed everything inline (default)
        final_html = html_template.replace(
            '/* CSS_PLACEHOLDER */', css_content
        ).replace(
            '/* DATA_PLACEHOLDER */', data_script
        ).replace(
            '/* JS_PLACEHOLDER */', js_content
        )

    # Write output file
    Path(filepath).write_text(final_html, encoding='utf-8')
