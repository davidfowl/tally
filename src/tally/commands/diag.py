"""
Tally 'diag' command - Show diagnostic information about config and rules.
"""

import os
import sys

from ..cli import C, find_config_dir
from ..config_loader import load_config
from ..merchant_utils import get_all_rules, diagnose_rules, get_transforms


def cmd_diag(args):
    """Handle the 'diag' subcommand - show diagnostic information about config and rules."""
    import json as json_module

    # Determine config directory
    if args.config:
        config_dir = os.path.abspath(args.config)
    else:
        config_dir = find_config_dir() or os.path.abspath('config')

    print("BUDGET ANALYZER DIAGNOSTICS")
    print("=" * 70)
    print()

    # Config directory info
    print("CONFIGURATION")
    print("-" * 70)
    print(f"Config directory: {config_dir}")
    print(f"  Exists: {os.path.isdir(config_dir)}")
    print()

    if not os.path.isdir(config_dir):
        print("ERROR: Config directory not found!")
        print("Run 'tally init' to create a new budget directory.")
        sys.exit(1)

    # Settings file
    settings_path = os.path.join(config_dir, args.settings)
    budget_dir = os.path.dirname(config_dir)
    print(f"Settings file: {settings_path}")
    print(f"  Exists: {os.path.exists(settings_path)}")

    config = None
    config_issues = []

    if os.path.exists(settings_path):
        try:
            config = load_config(config_dir, args.settings)
            print(f"  Loaded successfully: Yes")
            print(f"  Year: {config.get('year', 'not set')}")
            print(f"  Output dir: {config.get('output_dir', 'not set')}")
            currency_fmt = config.get('currency_format', '${amount}')
            from ..analyzer import format_currency
            print(f"  Currency format: {currency_fmt}")
            print(f"    Example: {format_currency(1234, currency_fmt)}")
            # Show field transforms from merchants.rules
            transforms = get_transforms(config.get('_merchants_file'))
            if transforms:
                print(f"  Field transforms: {len(transforms)} transform(s)")
                for field_path, expr in transforms[:5]:
                    print(f"    - {field_path} = {expr}")
                if len(transforms) > 5:
                    print(f"    ... and {len(transforms) - 5} more")
            else:
                print(f"  Field transforms: none configured")
        except Exception as e:
            print(f"  Loaded successfully: No")
            print(f"  Error: {e}")
            config_issues.append(f"settings.yaml error: {e}")
    else:
        config_issues.append("settings.yaml not found")
    print()

    # CONFIG HEALTH CHECK - identify common issues
    print("CONFIG HEALTH CHECK")
    print("-" * 70)

    # Check for legacy CSV file
    legacy_csv = os.path.join(config_dir, 'merchant_categories.csv')
    merchants_rules = os.path.join(config_dir, 'merchants.rules')
    views_rules = os.path.join(config_dir, 'views.rules')

    if os.path.exists(legacy_csv) and not os.path.exists(merchants_rules):
        config_issues.append(f"Legacy CSV format detected: {os.path.basename(legacy_csv)}")
        print(f"  {C.YELLOW}⚠{C.RESET}  Legacy merchant_categories.csv found")
        print(f"       Run 'tally run --migrate' to upgrade to .rules format")

    # Check if merchants_file is set in settings
    if config:
        merchants_file_setting = config.get('merchants_file')
        views_file_setting = config.get('views_file')

        # Check merchants_file reference
        if not merchants_file_setting:
            if os.path.exists(merchants_rules):
                config_issues.append("merchants.rules exists but not configured in settings.yaml")
                print(f"  {C.YELLOW}⚠{C.RESET}  config/merchants.rules exists but not in settings.yaml")
                print(f"       Add: merchants_file: config/merchants.rules")
            elif not os.path.exists(legacy_csv):
                print(f"  {C.YELLOW}⚠{C.RESET}  No merchant rules configured")
                print(f"       All transactions will be categorized as 'Unknown'")
        else:
            resolved_path = os.path.join(budget_dir, merchants_file_setting)
            if not os.path.exists(resolved_path):
                config_issues.append(f"merchants_file points to missing file: {merchants_file_setting}")
                print(f"  {C.RED}✗{C.RESET}  merchants_file: {merchants_file_setting}")
                print(f"       File not found at: {resolved_path}")
            else:
                print(f"  {C.GREEN}✓{C.RESET}  merchants_file: {merchants_file_setting}")

        # Check views_file reference
        if not views_file_setting:
            if os.path.exists(views_rules):
                config_issues.append("views.rules exists but not configured in settings.yaml")
                print(f"  {C.YELLOW}⚠{C.RESET}  config/views.rules exists but not in settings.yaml")
                print(f"       Add: views_file: config/views.rules")
        else:
            resolved_path = os.path.join(budget_dir, views_file_setting)
            if not os.path.exists(resolved_path):
                config_issues.append(f"views_file points to missing file: {views_file_setting}")
                print(f"  {C.RED}✗{C.RESET}  views_file: {views_file_setting}")
                print(f"       File not found at: {resolved_path}")
            else:
                print(f"  {C.GREEN}✓{C.RESET}  views_file: {views_file_setting}")

        # Check data sources
        data_sources = config.get('data_sources', [])
        if not data_sources:
            config_issues.append("No data sources configured")
            print(f"  {C.YELLOW}⚠{C.RESET}  No data sources configured")
            print(f"       Add data_sources to settings.yaml to process transactions")
        else:
            missing_sources = []
            for source in data_sources:
                filepath = os.path.join(budget_dir, source['file'])
                if not os.path.exists(filepath):
                    missing_sources.append(source['file'])
            if missing_sources:
                config_issues.append(f"Missing data files: {', '.join(missing_sources)}")
                for f in missing_sources:
                    print(f"  {C.RED}✗{C.RESET}  data source: {f}")
                    print(f"       File not found")
            else:
                print(f"  {C.GREEN}✓{C.RESET}  data_sources: {len(data_sources)} configured, all files exist")

    if not config_issues:
        print(f"  {C.GREEN}✓{C.RESET}  All configuration files are valid")

    print()

    # FILE PATHS - show how paths are resolved
    print("FILE PATHS")
    print("-" * 70)
    print(f"  Budget directory:  {budget_dir}")
    print(f"  Config directory:  {config_dir}")
    print()
    print("  Path resolution (relative paths in settings.yaml are resolved from budget dir):")
    if config:
        if config.get('merchants_file'):
            mf = config['merchants_file']
            resolved = os.path.join(budget_dir, mf)
            exists = "exists" if os.path.exists(resolved) else "NOT FOUND"
            print(f"    merchants_file: {mf}")
            print(f"      → {resolved} ({exists})")
        if config.get('views_file'):
            vf = config['views_file']
            resolved = os.path.join(budget_dir, vf)
            exists = "exists" if os.path.exists(resolved) else "NOT FOUND"
            print(f"    views_file: {vf}")
            print(f"      → {resolved} ({exists})")
        for source in config.get('data_sources', []):
            sf = source['file']
            resolved = os.path.join(budget_dir, sf)
            exists = "exists" if os.path.exists(resolved) else "NOT FOUND"
            print(f"    data_source: {sf}")
            print(f"      → {resolved} ({exists})")
    print()

    # Data sources
    if config and config.get('data_sources'):
        print("DATA SOURCES")
        print("-" * 70)
        for i, source in enumerate(config['data_sources'], 1):
            filepath = os.path.join(config_dir, '..', source['file'])
            filepath = os.path.normpath(filepath)
            if not os.path.exists(filepath):
                filepath = os.path.join(os.path.dirname(config_dir), source['file'])

            print(f"  {i}. {source.get('name', 'unnamed')}")
            print(f"     File: {source['file']}")
            print(f"     Exists: {os.path.exists(filepath)}")
            if source.get('type'):
                print(f"     Type: {source['type']}")
            if source.get('format'):
                print(f"     Format: {source['format']}")

            # Show format spec details if available
            format_spec = source.get('_format_spec')
            if format_spec:
                print(f"     Columns:")
                print(f"       date: column {format_spec.date_column} (format: {format_spec.date_format})")
                print(f"       amount: column {format_spec.amount_column}")
                if format_spec.description_column is not None:
                    print(f"       description: column {format_spec.description_column}")
                if format_spec.custom_captures:
                    for name, col in format_spec.custom_captures.items():
                        print(f"       {name}: column {col} (custom capture)")
                if format_spec.description_template:
                    print(f"     Description template: {format_spec.description_template}")
                if format_spec.location_column is not None:
                    print(f"       location: column {format_spec.location_column}")
                if format_spec.negate_amount:
                    print(f"     Amount negation: enabled")
            print()

    # Merchant rules diagnostics
    print("MERCHANT RULES")
    print("-" * 70)

    merchants_file = config.get('_merchants_file') if config else None
    merchants_format = config.get('_merchants_format') if config else None

    if merchants_file and os.path.exists(merchants_file):
        print(f"Merchants file: {merchants_file}")
        print(f"  Format: {merchants_format or 'unknown'}")
        print(f"  Exists: True")

        # Get file stats
        file_size = os.path.getsize(merchants_file)
        print(f"  File size: {file_size} bytes")

        if merchants_format == 'new':
            # New .rules format
            try:
                from ..merchant_engine import load_merchants_file
                from pathlib import Path
                engine = load_merchants_file(Path(merchants_file))
                print(f"  Rules loaded: {len(engine.rules)}")

                # Tag statistics
                rules_with_tags = sum(1 for r in engine.rules if r.tags)
                all_tags = set()
                for r in engine.rules:
                    all_tags.update(r.tags)

                # Special tags that affect spending analysis
                SPECIAL_TAGS = {'income', 'refund', 'transfer'}
                special_tags_used = all_tags & SPECIAL_TAGS

                print()
                if rules_with_tags > 0:
                    pct = (rules_with_tags / len(engine.rules) * 100) if engine.rules else 0
                    print(f"  Rules with tags: {rules_with_tags}/{len(engine.rules)} ({pct:.0f}%)")
                    if all_tags:
                        # Show special tags in cyan, others normally
                        tag_strs = []
                        for tag in sorted(all_tags):
                            if tag in SPECIAL_TAGS:
                                tag_strs.append(f"{C.CYAN}{tag}{C.RESET}")
                            else:
                                tag_strs.append(tag)
                        print(f"  Unique tags: {', '.join(tag_strs)}")

                # Show special tag usage
                print()
                print(f"  {C.BOLD}Special Tags:{C.RESET} (affect spending analysis)")
                for tag, desc in [('income', 'exclude deposits/salary'), ('refund', 'net against merchant'), ('transfer', 'exclude account transfers')]:
                    if tag in special_tags_used:
                        print(f"    {C.GREEN}✓{C.RESET} {C.CYAN}{tag}{C.RESET}: {C.DIM}{desc}{C.RESET}")
                    else:
                        print(f"    {C.DIM}○ {tag}: {desc}{C.RESET}")

                print()
                print("  MERCHANT RULES (all):")
                for rule in engine.rules:
                    print(f"    [{rule.name}]")
                    print(f"      match: {rule.match_expr}")
                    print(f"      category: {rule.category} > {rule.subcategory}")
                    if rule.tags:
                        print(f"      tags: {', '.join(rule.tags)}")
            except Exception as e:
                print(f"  Error loading rules: {e}")
        else:
            # Legacy CSV format
            rules_path = merchants_file
            diag = diagnose_rules(rules_path)
            print(f"  Rules loaded: {diag['user_rules_count']}")
            print()
            print(f"  {C.YELLOW}NOTE: Using legacy CSV format. Run 'tally run --migrate' to upgrade.{C.RESET}")

            if diag['user_rules_errors']:
                print()
                print("  ERRORS/WARNINGS:")
                for err in diag['user_rules_errors']:
                    print(f"    - {err}")

            if diag.get('rules_with_tags', 0) > 0:
                print()
                pct = (diag['rules_with_tags'] / diag['user_rules_count'] * 100) if diag['user_rules_count'] > 0 else 0
                print(f"  Rules with tags: {diag['rules_with_tags']}/{diag['user_rules_count']} ({pct:.0f}%)")
                if diag.get('unique_tags'):
                    print(f"  Unique tags: {', '.join(sorted(diag['unique_tags']))}")

            if diag['user_rules']:
                print()
                print("  MERCHANT RULES (CSV format):")
                for rule in diag['user_rules']:
                    if len(rule) == 5:
                        pattern, merchant, category, subcategory, tags = rule
                    else:
                        pattern, merchant, category, subcategory = rule
                        tags = []
                    print(f"    {pattern}")
                    tags_str = f" [{', '.join(tags)}]" if tags else ""
                    print(f"      -> {merchant} | {category} > {subcategory}{tags_str}")
    else:
        print("Merchants file: not configured")
        print()
        print("  No merchant rules found.")
        print("  Add 'merchants_file: config/merchants.rules' to settings.yaml")
        print("  Transactions will be categorized as 'Unknown'.")
    print()

    # Views configuration
    print("VIEWS")
    print("-" * 70)
    views_file_setting = config.get('views_file') if config else None
    if views_file_setting:
        # Resolve path relative to budget directory (parent of config dir)
        budget_dir = os.path.dirname(config_dir)
        views_path = os.path.join(budget_dir, views_file_setting)
        print(f"Configured in settings.yaml: {views_file_setting}")
        print(f"  Resolved path: {views_path}")
        print(f"  Exists: {os.path.exists(views_path)}")
        if os.path.exists(views_path):
            try:
                from ..section_engine import load_sections
                views_config = load_sections(views_path)
                print(f"  Views defined: {len(views_config.sections)}")
                if views_config.global_variables:
                    print()
                    print("  Global variables:")
                    for name, expr in views_config.global_variables.items():
                        print(f"    {name} = {expr}")
                print()
                print("  Views:")
                for view in views_config.sections:
                    print(f"    [{view.name}]")
                    if view.description:
                        print(f"      description: {view.description}")
                    print(f"      filter: {view.filter_expr}")
            except Exception as e:
                print(f"  Error loading views: {e}")
        else:
            print()
            print("  WARNING: Views file not found!")
            print(f"  Create {views_file_setting} or remove views_file from settings.yaml")
    else:
        print("Not configured (optional)")
        print("  To enable views, add to settings.yaml:")
        print("    views_file: config/views.rules")
        print()
        print("  Then create the file with view definitions. Example:")
        print("    [Every Month]")
        print("    filter: months >= 6 and cv < 0.3")
    print()

    # JSON output option
    if args.format == 'json':
        print("JSON OUTPUT")
        print("-" * 70)
        diag = diagnose_rules(merchants_file) if merchants_file else {'user_rules_path': None, 'user_rules_exists': False, 'user_rules_count': 0, 'user_rules': [], 'user_rules_errors': [], 'total_rules': 0}
        output = {
            'config_dir': config_dir,
            'config_dir_exists': os.path.isdir(config_dir),
            'settings_file': settings_path,
            'settings_exists': os.path.exists(settings_path),
            'data_sources': [],
            'rules': {
                'user_rules_path': diag['user_rules_path'],
                'user_rules_exists': diag['user_rules_exists'],
                'user_rules_count': diag['user_rules_count'],
                'user_rules': [
                    {'pattern': r[0], 'merchant': r[1], 'category': r[2], 'subcategory': r[3], 'tags': r[4] if len(r) > 4 else []}
                    for r in diag['user_rules']
                ],
                'errors': diag['user_rules_errors'],
                'total_rules': diag['total_rules'],
                'rules_with_tags': diag.get('rules_with_tags', 0),
                'unique_tags': sorted(diag.get('unique_tags', set())),
            }
        }
        if config and config.get('data_sources'):
            for source in config['data_sources']:
                filepath = os.path.join(os.path.dirname(config_dir), source['file'])
                output['data_sources'].append({
                    'name': source.get('name'),
                    'file': source['file'],
                    'exists': os.path.exists(filepath),
                    'type': source.get('type'),
                    'format': source.get('format'),
                })
        print(json_module.dumps(output, indent=2))
