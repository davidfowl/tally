"""
HTML Report Generation - Generate spending analysis HTML reports.

This module handles generation of interactive HTML reports from analyzed transaction data.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Try to import sentence_transformers for semantic search
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False


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
    """
    formatted_num = f"{amount:,.0f}"
    return currency_format.format(amount=formatted_num)


def format_currency_decimal(amount: float, currency_format: str = "${amount}") -> str:
    """Format amount with currency symbol/format (with 2 decimal places).

    Args:
        amount: The amount to format
        currency_format: Format string with {amount} placeholder

    Returns:
        Formatted currency string with decimals, e.g. "$1,234.56"
    """
    formatted_num = f"{amount:,.2f}"
    return currency_format.format(amount=formatted_num)


# ============================================================================
# EMBEDDINGS
# ============================================================================

def generate_embeddings(items):
    """Generate embeddings for a list of text items using sentence-transformers."""
    if not EMBEDDINGS_AVAILABLE:
        return None

    print("Generating semantic embeddings...")
    # Use a small, fast model optimized for semantic similarity
    model = SentenceTransformer('all-MiniLM-L6-v2')
    embeddings = model.encode(items, show_progress_bar=False)
    return embeddings.tolist()


# ============================================================================
# VUE-BASED HTML REPORT (Modern)
# ============================================================================

def write_summary_file_vue(stats, filepath, year=2025, currency_format="${amount}", sources=None, embedded_html=True):
    """Write summary to HTML file using Vue 3 for client-side rendering.

    Args:
        stats: Analysis statistics dict
        filepath: Output file path
        year: Year for display in title
        currency_format: Format string for currency display, e.g. "${amount}" or "{amount} zl"
        sources: List of data source names (e.g., ['Amex', 'Chase'])
        embedded_html: If True (default), embed CSS/JS inline. If False, output separate files.
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

    # Helper to explain a pattern in human-readable form
    def _explain_pattern(pattern):
        """Convert a regex/expression pattern to human-readable explanation."""
        import re
        if not pattern:
            return ''

        # Handle expression-style patterns (contains, startswith, etc.)
        if 'contains(' in pattern.lower():
            match = re.search(r'contains\(["\']([^"\']+)["\']\)', pattern, re.IGNORECASE)
            if match:
                return f'Description contains "{match.group(1)}"'

        if 'startswith(' in pattern.lower():
            match = re.search(r'startswith\(["\']([^"\']+)["\']\)', pattern, re.IGNORECASE)
            if match:
                return f'Description starts with "{match.group(1)}"'

        if 'anyof(' in pattern.lower():
            match = re.search(r'anyof\(([^)]+)\)', pattern, re.IGNORECASE)
            if match:
                terms = [t.strip().strip('"\'') for t in match.group(1).split(',')]
                if len(terms) <= 3:
                    return f'Description contains any of: {", ".join(terms)}'
                return f'Description contains any of: {", ".join(terms[:3])}...'

        # Handle regex patterns
        parts = []

        # Check for alternation (OR)
        if '|' in pattern and not pattern.startswith('('):
            alternatives = pattern.split('|')
            if len(alternatives) <= 3:
                terms = [a.replace('.*', ' ... ').replace('\\s', ' ').strip() for a in alternatives]
                return f'Matches: {" OR ".join(terms)}'
            else:
                terms = [a.replace('.*', ' ... ').replace('\\s', ' ').strip() for a in alternatives[:3]]
                return f'Matches: {" OR ".join(terms)} (+ {len(alternatives) - 3} more)'

        # Check for start anchor
        if pattern.startswith('^'):
            pattern = pattern[1:]
            parts.append('Starts with')
        else:
            parts.append('Contains')

        # Check for end anchor
        end_anchor = pattern.endswith('$')
        if end_anchor:
            pattern = pattern[:-1]

        # Clean up common regex syntax for display
        display = pattern
        display = display.replace('.*', ' ... ')
        display = display.replace('.+', ' ... ')
        display = display.replace('\\s+', ' ')
        display = display.replace('\\s', ' ')
        display = display.replace('\\d+', '#')
        display = display.replace('\\d', '#')
        display = display.replace('(?!', ' (not followed by ')
        display = display.replace('(?:', '(')
        display = display.replace(')', ')')

        parts.append(f'"{display.strip()}"')

        if end_anchor:
            parts.append('at end')

        return ' '.join(parts)

    # Helper to explain a view filter expression
    def _explain_view_filter(filter_expr):
        """Convert a view filter expression to human-readable explanation."""
        import re
        if not filter_expr:
            return ''

        explanations = []

        # Parse common conditions
        if 'category ==' in filter_expr.lower() or "category='" in filter_expr.lower():
            match = re.search(r'category\s*==?\s*["\']([^"\']+)["\']', filter_expr, re.IGNORECASE)
            if match:
                explanations.append(f'Category is "{match.group(1)}"')

        if 'subcategory ==' in filter_expr.lower() or "subcategory='" in filter_expr.lower():
            match = re.search(r'subcategory\s*==?\s*["\']([^"\']+)["\']', filter_expr, re.IGNORECASE)
            if match:
                explanations.append(f'Subcategory is "{match.group(1)}"')

        if 'tag(' in filter_expr.lower() or 'has_tag(' in filter_expr.lower():
            matches = re.findall(r'(?:tag|has_tag)\(["\']([^"\']+)["\']\)', filter_expr, re.IGNORECASE)
            for tag in matches:
                explanations.append(f'Has tag "{tag}"')

        if 'months >' in filter_expr or 'months>=' in filter_expr:
            match = re.search(r'months\s*>=?\s*(\d+)', filter_expr)
            if match:
                explanations.append(f'Active {match.group(1)}+ months')

        if 'total >' in filter_expr or 'total>=' in filter_expr:
            match = re.search(r'total\s*>=?\s*(\d+)', filter_expr)
            if match:
                explanations.append(f'Total ≥ ${match.group(1)}')

        if 'cv <' in filter_expr or 'cv<=' in filter_expr:
            match = re.search(r'cv\s*<=?\s*([\d.]+)', filter_expr)
            if match:
                explanations.append(f'Coefficient of variation ≤ {match.group(1)}')

        if explanations:
            return ' AND '.join(explanations)

        # Fallback: return cleaned up version of expression
        return filter_expr.replace('==', '=').replace('&&', ' and ').replace('||', ' or ')

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
                    'description': txn.get('raw_description', txn.get('description', '')),
                    'amount': txn.get('amount', 0),
                    'source': txn.get('source', ''),
                    'location': txn.get('location'),
                    'tags': txn.get('tags', [])
                }
                # Include extra_fields from field: directives
                if txn.get('extra_fields'):
                    txn_json['extra_fields'] = txn['extra_fields']
                txns.append(txn_json)

            # Build match info for tooltip
            match_info = data.get('match_info')
            match_info_json = None
            if match_info:
                pattern = match_info.get('pattern', '')
                match_info_json = {
                    'pattern': pattern,
                    'source': match_info.get('source', ''),
                    'explanation': _explain_pattern(pattern),
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
                    'explanation': _explain_view_filter(view_filter) if view_filter else '',
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

        return categories

    category_view = build_category_view()

    # Build final spending data object
    spending_data = {
        'year': year,
        'numMonths': num_months,
        'sources': sources,
        'dataThrough': latest_date,
        'sections': sections,
        'categoryView': category_view,
        # Excluded transactions for transparency
        'excludedTransactions': stats.get('excluded_transactions', []),
        'excludedCount': stats.get('excluded_count', 0),
        'excludedTotal': stats.get('excluded_total', 0),
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


