"""MCP server for tally - exposes spending analysis tools via Model Context Protocol."""

import json
import os
import sys
from io import StringIO

from mcp.server.fastmcp import FastMCP

# Create the MCP server
mcp = FastMCP("tally")


@mcp.resource("file://rules/{path}")
def get_rules_file(path: str) -> str:
    """
    Read the merchant_categories.csv rules file.

    Use path like "config/merchant_categories.csv" relative to current directory.
    """
    import os
    file_path = os.path.abspath(path)
    if not os.path.exists(file_path):
        return f"File not found: {file_path}"
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()


def _capture_output(func, *args, **kwargs):
    """Capture stdout from a function that prints."""
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        func(*args, **kwargs)
        return sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout


def _load_and_parse(config_dir: str):
    """Load config and parse all transactions. Returns (config, transactions, rules) or raises."""
    from .config_loader import load_config
    from .merchant_utils import get_all_rules
    from .analyzer import parse_amex, parse_boa, parse_generic_csv

    config_dir = os.path.abspath(config_dir)
    if not os.path.isdir(config_dir):
        raise FileNotFoundError(f"Config directory not found: {config_dir}")

    config = load_config(config_dir)
    home_locations = config.get('home_locations', set())
    data_sources = config.get('data_sources', [])

    if not data_sources:
        raise ValueError("No data sources configured in settings.yaml")

    # Load merchant rules
    rules_file = os.path.join(config_dir, 'merchant_categories.csv')
    if os.path.exists(rules_file):
        rules = get_all_rules(rules_file)
    else:
        rules = get_all_rules()

    # Parse all transactions
    all_txns = []
    for source in data_sources:
        filepath = os.path.join(config_dir, '..', source['file'])
        filepath = os.path.normpath(filepath)
        if not os.path.exists(filepath):
            filepath = os.path.join(os.path.dirname(config_dir), source['file'])
        if not os.path.exists(filepath):
            continue

        parser_type = source.get('_parser_type', source.get('type', '')).lower()
        format_spec = source.get('_format_spec')

        try:
            if parser_type == 'amex':
                txns = parse_amex(filepath, rules, home_locations)
            elif parser_type == 'boa':
                txns = parse_boa(filepath, rules, home_locations)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(
                    filepath, format_spec, rules,
                    home_locations,
                    source_name=source.get('name', 'CSV'),
                    decimal_separator=source.get('decimal_separator', '.')
                )
            else:
                continue
        except Exception:
            continue

        all_txns.extend(txns)

    if not all_txns:
        raise ValueError("No transactions found in configured data sources")

    return config, all_txns, rules


@mcp.tool()
def run_analysis(
    config_dir: str = "./config",
    format: str = "json",
    verbose: int = 0,
    only: str | None = None,
    category: str | None = None
) -> str:
    """
    Analyze transactions and generate spending report.

    Args:
        config_dir: Path to config directory (default: ./config)
        format: Output format - json, markdown, or summary
        verbose: Verbosity level (0, 1, or 2)
        only: Filter to specific classifications (comma-separated, e.g. "monthly,variable")
        category: Filter to specific category

    Returns:
        Analysis report in the specified format
    """
    try:
        from .analyzer import analyze_transactions, export_json, export_markdown, print_summary

        config, all_txns, _ = _load_and_parse(config_dir)
        stats = analyze_transactions(all_txns)

        only_filter = only.split(',') if only else None
        currency_format = config.get('currency_format', '${amount}')

        if format == 'json':
            return export_json(stats, verbose=verbose, only=only_filter, category_filter=category)
        elif format == 'markdown':
            return export_markdown(stats, verbose=verbose, only=only_filter, category_filter=category)
        else:  # summary
            year = config.get('year', 2025)
            return _capture_output(print_summary, stats, year=year, currency_format=currency_format)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def explain_merchant(
    merchant: str,
    config_dir: str = "./config",
    verbose: int = 0
) -> str:
    """
    Explain why a specific merchant is classified the way it is.

    Args:
        merchant: Name of the merchant to explain
        config_dir: Path to config directory (default: ./config)
        verbose: Verbosity level (0=basic, 1=trace, 2=full details)

    Returns:
        Explanation of merchant classification with reasoning
    """
    try:
        from .analyzer import analyze_transactions, build_merchant_json
        from difflib import get_close_matches

        _, all_txns, _ = _load_and_parse(config_dir)
        stats = analyze_transactions(all_txns)

        # Find merchant across all classifications
        all_merchants = {}
        for section in ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']:
            merchants_dict = stats.get(f'{section}_merchants', {})
            for name, data in merchants_dict.items():
                all_merchants[name] = data

        # Try exact match first
        merchant_lower = merchant.lower()
        matched_name = None
        for name in all_merchants:
            if name.lower() == merchant_lower:
                matched_name = name
                break

        if not matched_name:
            # Try fuzzy match
            matches = get_close_matches(merchant, list(all_merchants.keys()), n=3, cutoff=0.6)
            if matches:
                return json.dumps({
                    "error": f"No merchant matching '{merchant}'",
                    "suggestions": matches
                })
            else:
                return json.dumps({"error": f"No merchant matching '{merchant}'"})

        data = all_merchants[matched_name]
        return json.dumps(build_merchant_json(matched_name, data, verbose), indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def explain_summary(
    config_dir: str = "./config",
    classification: str | None = None,
    category: str | None = None
) -> str:
    """
    Get classification summary for all merchants.

    Args:
        config_dir: Path to config directory (default: ./config)
        classification: Filter to specific classification (monthly, annual, periodic, travel, one_off, variable)
        category: Filter to specific category

    Returns:
        JSON summary of merchant classifications
    """
    try:
        from .analyzer import analyze_transactions, build_merchant_json

        _, all_txns, _ = _load_and_parse(config_dir)
        stats = analyze_transactions(all_txns)

        sections = ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']
        if classification:
            if classification not in sections:
                return json.dumps({
                    "error": f"Invalid classification: {classification}",
                    "valid_options": sections
                })
            sections = [classification]

        result = {"classifications": {}}
        for section in sections:
            merchants_dict = stats.get(f'{section}_merchants', {})
            merchants_list = []
            for name, data in merchants_dict.items():
                if category and data.get('category') != category:
                    continue
                merchants_list.append(build_merchant_json(name, data, verbose=0))
            if merchants_list:
                result["classifications"][section] = merchants_list

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def discover_unknown(
    config_dir: str = "./config",
    limit: int = 20,
    format: str = "json"
) -> str:
    """
    Find uncategorized merchants and suggest categorization rules.

    Args:
        config_dir: Path to config directory (default: ./config)
        limit: Maximum number of unknown merchants to return (default: 20)
        format: Output format - json or csv

    Returns:
        List of unknown merchants with suggested patterns, sorted by spend
    """
    try:
        from .analyzer import analyze_transactions
        from .merchant_utils import extract_merchant_name
        import re

        _, all_txns, _ = _load_and_parse(config_dir)
        stats = analyze_transactions(all_txns)

        # Collect unknown merchants from variable category
        unknown = []
        for section in ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']:
            merchants_dict = stats.get(f'{section}_merchants', {})
            for name, data in merchants_dict.items():
                if data.get('category') == 'Unknown':
                    # Get sample descriptions
                    descriptions = data.get('descriptions', [])
                    sample_desc = descriptions[0] if descriptions else name

                    # Generate suggested pattern
                    clean_name = extract_merchant_name(sample_desc)
                    pattern = re.escape(clean_name.upper()[:15]) + '.*'

                    unknown.append({
                        "merchant": name,
                        "raw_description": sample_desc,
                        "suggested_pattern": pattern,
                        "suggested_merchant": clean_name.title(),
                        "count": data.get('count', 1),
                        "total_spend": data.get('total', 0)
                    })

        # Sort by spend and limit
        unknown.sort(key=lambda x: x['total_spend'], reverse=True)
        unknown = unknown[:limit]

        if format == 'csv':
            lines = ["Pattern,Merchant,Category,Subcategory"]
            for item in unknown:
                lines.append(f"{item['suggested_pattern']},{item['suggested_merchant']},Unknown,Unknown")
            return "\n".join(lines)
        else:
            return json.dumps({"unknown_merchants": unknown}, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def inspect_csv(
    file_path: str,
    rows: int = 5
) -> str:
    """
    Analyze CSV file structure to help build format strings.

    Args:
        file_path: Path to the CSV file to inspect
        rows: Number of sample rows to show (default: 5)

    Returns:
        JSON with headers, sample rows, and suggested format string
    """
    try:
        import csv
        from .analyzer import auto_detect_csv_format

        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            return json.dumps({"error": f"File not found: {file_path}"})

        # Read headers and sample rows
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            # Try to detect delimiter
            sample = f.read(4096)
            f.seek(0)

            try:
                dialect = csv.Sniffer().sniff(sample)
                reader = csv.reader(f, dialect)
            except csv.Error:
                reader = csv.reader(f)

            all_rows = list(reader)

        if not all_rows:
            return json.dumps({"error": "Empty file"})

        headers = all_rows[0] if all_rows else []
        sample_rows = all_rows[1:rows + 1] if len(all_rows) > 1 else []

        # Try auto-detection
        format_spec = auto_detect_csv_format(file_path)
        suggested_format = None
        if format_spec:
            # Build format string from detected spec
            parts = []
            if format_spec.date_column is not None:
                parts.append(f"{{date:{format_spec.date_format or '%Y-%m-%d'}}}")
            if format_spec.description_column is not None:
                parts.append("{description}")
            if format_spec.amount_column is not None:
                parts.append("{amount}")
            suggested_format = ",".join(parts) if parts else None

        result = {
            "file": file_path,
            "headers": headers,
            "sample_rows": sample_rows,
            "row_count": len(all_rows) - 1,
        }
        if suggested_format:
            result["suggested_format"] = suggested_format

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def diagnose_config(
    config_dir: str = "./config",
    format: str = "json"
) -> str:
    """
    Debug configuration issues - show loaded rules, data sources, and errors.

    Args:
        config_dir: Path to config directory (default: ./config)
        format: Output format - json or text

    Returns:
        Diagnostic information about config, rules, and data sources
    """
    try:
        from .config_loader import load_config
        from .merchant_utils import get_all_rules, diagnose_rules

        config_dir = os.path.abspath(config_dir)
        result = {
            "config_dir": config_dir,
            "exists": os.path.isdir(config_dir),
            "settings": None,
            "data_sources": [],
            "rules": {"user": 0, "baseline": 0, "total": 0},
            "errors": []
        }

        if not result["exists"]:
            result["errors"].append(f"Config directory not found: {config_dir}")
            return json.dumps(result, indent=2)

        # Check settings file
        settings_path = os.path.join(config_dir, 'settings.yaml')
        if os.path.exists(settings_path):
            try:
                config = load_config(config_dir)
                result["settings"] = {
                    "year": config.get('year'),
                    "currency_format": config.get('currency_format', '${amount}'),
                    "data_source_count": len(config.get('data_sources', []))
                }

                # Check data sources
                for source in config.get('data_sources', []):
                    filepath = os.path.join(config_dir, '..', source.get('file', ''))
                    filepath = os.path.normpath(filepath)
                    result["data_sources"].append({
                        "name": source.get('name'),
                        "file": source.get('file'),
                        "exists": os.path.exists(filepath),
                        "type": source.get('_parser_type', source.get('type', 'generic'))
                    })
            except Exception as e:
                result["errors"].append(f"Error loading config: {str(e)}")
        else:
            result["errors"].append(f"Settings file not found: {settings_path}")

        # Check rules
        rules_file = os.path.join(config_dir, 'merchant_categories.csv')
        if os.path.exists(rules_file):
            rules = get_all_rules(rules_file)
            user_rules = sum(1 for r in rules if len(r) >= 6 and r[5] == 'user')
            result["rules"] = {
                "user": user_rules,
                "baseline": len(rules) - user_rules,
                "total": len(rules)
            }
        else:
            rules = get_all_rules()
            result["rules"] = {
                "user": 0,
                "baseline": len(rules),
                "total": len(rules)
            }

        if format == 'text':
            lines = [
                f"Config Directory: {result['config_dir']}",
                f"Exists: {result['exists']}",
                f"Settings: {result['settings']}",
                f"Data Sources: {len(result['data_sources'])}",
                f"Rules: {result['rules']['user']} user + {result['rules']['baseline']} baseline = {result['rules']['total']} total",
            ]
            if result['errors']:
                lines.append(f"Errors: {', '.join(result['errors'])}")
            return "\n".join(lines)

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def add_rule(
    pattern: str,
    merchant: str,
    category: str,
    subcategory: str,
    config_dir: str = "./config"
) -> str:
    """
    Add a new merchant categorization rule to merchant_categories.csv.

    Args:
        pattern: Regex pattern to match transaction descriptions
        merchant: Display name for the merchant
        category: Category (e.g., Food, Shopping, Transport)
        subcategory: Subcategory (e.g., Grocery, Online, Rideshare)
        config_dir: Path to config directory (default: ./config)

    Returns:
        Confirmation message or error
    """
    try:
        import re
        import csv

        config_dir = os.path.abspath(config_dir)
        if not os.path.isdir(config_dir):
            return json.dumps({"error": f"Config directory not found: {config_dir}"})

        # Validate pattern is valid regex
        try:
            re.compile(pattern)
        except re.error as e:
            return json.dumps({"error": f"Invalid regex pattern: {e}"})

        rules_file = os.path.join(config_dir, 'merchant_categories.csv')

        # Check if file exists and has header
        has_header = False
        if os.path.exists(rules_file):
            with open(rules_file, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                has_header = first_line.lower().startswith('pattern')

        # Append the new rule
        with open(rules_file, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            if not os.path.exists(rules_file) or os.path.getsize(rules_file) == 0:
                # Write header if new file
                writer.writerow(['Pattern', 'Merchant', 'Category', 'Subcategory'])
            writer.writerow([pattern, merchant, category, subcategory])

        return json.dumps({
            "success": True,
            "message": f"Added rule: {pattern} -> {merchant} ({category}/{subcategory})",
            "file": rules_file
        })

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_rules(
    config_dir: str = "./config",
    category: str | None = None,
    source: str | None = None
) -> str:
    """
    List all merchant categorization rules.

    Args:
        config_dir: Path to config directory (default: ./config)
        category: Filter by category (e.g., "Food", "Shopping")
        source: Filter by source ("user" or "baseline")

    Returns:
        JSON array of rules with pattern, merchant, category, subcategory, source
    """
    try:
        from .merchant_utils import get_all_rules

        config_dir = os.path.abspath(config_dir)
        rules_file = os.path.join(config_dir, 'merchant_categories.csv')

        if os.path.exists(rules_file):
            rules = get_all_rules(rules_file)
        else:
            rules = get_all_rules()

        result = []
        for rule in rules:
            if len(rule) >= 6:
                pattern, merchant, cat, subcat, _, rule_source = rule[:6]
            else:
                pattern, merchant, cat, subcat = rule[:4]
                rule_source = "baseline"

            # Apply filters
            if category and cat != category:
                continue
            if source and rule_source != source:
                continue

            result.append({
                "pattern": pattern,
                "merchant": merchant,
                "category": cat,
                "subcategory": subcat,
                "source": rule_source
            })

        return json.dumps({"rules": result, "count": len(result)}, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_version() -> str:
    """
    Get tally version information.

    Returns:
        Version string and update info
    """
    try:
        from . import __version__
        version = __version__
    except (ImportError, AttributeError):
        version = "unknown"

    return json.dumps({
        "name": "tally",
        "version": version,
        "description": "LLM-powered spending categorization"
    })


def run_server():
    """Run the MCP server in stdio mode."""
    mcp.run()


if __name__ == "__main__":
    run_server()
