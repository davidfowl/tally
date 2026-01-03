"""
Rule management commands.

Provides CLI commands for adding, listing, updating, and deleting rules.
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tally.rule_manager import RuleManager, ValidationResult
from tally.cli import resolve_config_dir


def _find_rules_file(args) -> Tuple[Path, Optional[Path]]:
    """Find the .rules file and config directory using standard resolution."""
    # Use shared config resolver
    config_path = resolve_config_dir(args)
    if config_path:
        config_dir = Path(config_path)
    else:
        config_dir = Path('.')

    # Look for .rules file
    rules_files = list(config_dir.glob('*.rules'))
    if rules_files:
        return rules_files[0], config_dir

    # Default to merchants.rules
    return config_dir / 'merchants.rules', config_dir


def _get_manager(args) -> Tuple[RuleManager, Optional[Path]]:
    """Get a RuleManager and config directory."""
    rules_path, config_dir = _find_rules_file(args)
    return RuleManager(rules_path), config_dir


def _load_transactions(config_dir: Optional[Path]) -> List[Dict]:
    """Load transactions from config for validation."""
    if not config_dir or not config_dir.is_dir():
        return []

    try:
        from tally.config_loader import load_config
        from tally.merchant_utils import get_all_rules, get_transforms
        from tally.analyzer import parse_amex, parse_boa, parse_generic_csv

        config = load_config(str(config_dir))
        data_sources = config.get('data_sources', [])
        if not data_sources:
            return []

        # Load merchant rules
        merchants_file = config.get('_merchants_file')
        if merchants_file and os.path.exists(merchants_file):
            rules = get_all_rules(merchants_file)
        else:
            rules = get_all_rules()

        transforms = get_transforms(merchants_file)
        all_txns = []

        for source in data_sources:
            filepath = os.path.join(str(config_dir), '..', source['file'])
            filepath = os.path.normpath(filepath)

            if not os.path.exists(filepath):
                filepath = os.path.join(os.path.dirname(str(config_dir)), source['file'])

            if not os.path.exists(filepath):
                continue

            parser_type = source.get('_parser_type', source.get('type', '')).lower()
            format_spec = source.get('_format_spec')

            try:
                if parser_type == 'amex':
                    txns = parse_amex(filepath, rules)
                elif parser_type == 'boa':
                    txns = parse_boa(filepath, rules)
                elif parser_type == 'generic' and format_spec:
                    txns = parse_generic_csv(
                        filepath, format_spec, rules,
                        source_name=source.get('name', 'CSV'),
                        decimal_separator=source.get('decimal_separator', '.'),
                        transforms=transforms
                    )
                else:
                    continue
            except Exception:
                continue

            all_txns.extend(txns)

        return all_txns

    except Exception:
        return []


def _format_validation_human(validation: ValidationResult, rule_name: str) -> List[str]:
    """Format validation result for human output."""
    from .discover import suggest_pattern

    lines = []

    if validation.matches > 0:
        lines.append(f"  ✓ Matches {validation.matches} transactions (${validation.total:,.2f})")
    else:
        lines.append(f"  ⚠ Matches 0 transactions")
        if validation.similar_unmatched:
            lines.append(f"  Did you mean:")
            for desc, count, total in validation.similar_unmatched[:3]:
                # Suggest a pattern from the unmatched description
                suggested = suggest_pattern(desc)
                lines.append(f"    \"{suggested}\" - matches \"{desc[:40]}...\" ({count} txns, ${total:,.2f})")

    if validation.shadows:
        lines.append(f"  ⚠ Shadowed by:")
        for shadow in validation.shadows[:3]:
            lines.append(f"    {shadow.name} (priority {shadow.priority})")

    return lines


def cmd_rule(args):
    """Dispatch rule subcommands."""
    if args.rule_command == 'add':
        cmd_rule_add(args)
    elif args.rule_command == 'list':
        cmd_rule_list(args)
    elif args.rule_command == 'show':
        cmd_rule_show(args)
    elif args.rule_command == 'update':
        cmd_rule_update(args)
    elif args.rule_command == 'delete':
        cmd_rule_delete(args)
    elif args.rule_command == 'import':
        cmd_rule_import(args)
    else:
        print("Usage: tally rule <add|list|show|update|delete|import>")
        sys.exit(1)


def cmd_rule_add(args):
    """Add or update a rule."""
    manager, config_dir = _get_manager(args)

    # Parse tags
    tags = set()
    if args.tags:
        for tag in args.tags.split(','):
            tag = tag.strip()
            if tag:
                tags.add(tag)

    # Check if rule exists (for output message)
    existing = manager.get(args.merchant or args.pattern) or manager.find_by_pattern(args.pattern)

    rule = manager.add(
        pattern=args.pattern,
        merchant=args.merchant,
        category=args.category,
        subcategory=args.subcategory,
        tags=tags if tags else None,
        priority=args.priority,
    )

    manager.save()

    # Validate only if --validate is specified
    validation = None
    should_validate = getattr(args, 'validate', False)
    if should_validate:
        transactions = _load_transactions(config_dir)
        if transactions:
            validation = manager.validate_rule(rule, transactions)

    # Output
    action = "updated" if existing else "added"

    if getattr(args, 'json', False):
        import json
        output = {
            'action': action,
            'rule': rule.name,
            'pattern': rule.match_expr,
            'category': rule.category,
            'subcategory': rule.subcategory,
            'tags': list(rule.tags),
            'priority': rule.priority,
        }
        if validation:
            output['validation'] = {
                'matches': validation.matches,
                'total': validation.total,
                'shadows': [{'name': s.name, 'priority': s.priority} for s in validation.shadows],
                'similar_unmatched': [
                    {'description': desc, 'count': count, 'total': total}
                    for desc, count, total in validation.similar_unmatched
                ],
            }
        print(json.dumps(output, indent=2))
        return

    # Human-readable output
    print(f"{action.capitalize()} rule: {rule.name}")
    print(f"  Pattern: {rule.match_expr}")
    if rule.category:
        cat_display = f"{rule.category}"
        if rule.subcategory:
            cat_display += f" / {rule.subcategory}"
        print(f"  Category: {cat_display}")
    if rule.tags:
        print(f"  Tags: {', '.join(sorted(rule.tags))}")
    if rule.priority != 50:
        print(f"  Priority: {rule.priority}")

    # Show validation results
    if validation:
        for line in _format_validation_human(validation, rule.name):
            print(line)
    elif should_validate:
        print("  (no transactions to validate against)")
    # No message when validation is off - that's the default now


def cmd_rule_list(args):
    """List rules."""
    manager, _ = _get_manager(args)

    # Filter
    category = args.category if hasattr(args, 'category') and args.category else None
    rules = manager.list(category=category)

    if not rules:
        print("No rules found.")
        return

    if args.json:
        import json
        output = []
        for rule in rules:
            output.append({
                'name': rule.name,
                'match': rule.match_expr,
                'category': rule.category,
                'subcategory': rule.subcategory,
                'tags': list(rule.tags),
                'priority': rule.priority,
                'complex': bool(rule.let_bindings or rule.fields),
            })
        print(json.dumps(output, indent=2))
        return

    # Table format
    # Calculate column widths
    name_width = max(len(r.name) for r in rules)
    name_width = max(name_width, 4)  # "NAME"
    name_width = min(name_width, 20)

    match_width = max(len(r.match_expr) for r in rules)
    match_width = max(match_width, 5)  # "MATCH"
    match_width = min(match_width, 40)

    cat_width = max(len(r.category) for r in rules)
    cat_width = max(cat_width, 8)  # "CATEGORY"
    cat_width = min(cat_width, 20)

    # Header
    print(f"{'NAME':<{name_width}}  {'MATCH':<{match_width}}  {'CATEGORY':<{cat_width}}  PRI")
    print("-" * (name_width + match_width + cat_width + 10))

    for rule in rules:
        name = rule.name[:name_width]
        match = rule.match_expr[:match_width]
        cat = rule.category[:cat_width]
        pri = str(rule.priority)
        complex_marker = " *" if rule.let_bindings or rule.fields else ""

        print(f"{name:<{name_width}}  {match:<{match_width}}  {cat:<{cat_width}}  {pri}{complex_marker}")

    # Legend
    if any(r.let_bindings or r.fields for r in rules):
        print()
        print("* = complex rule (has let/field). Use `tally rule show <name>` for details.")


def cmd_rule_show(args):
    """Show details of a single rule."""
    manager, _ = _get_manager(args)

    rule = manager.get(args.name)
    if not rule:
        print(f"Rule not found: {args.name}")
        sys.exit(1)

    # Output in .rules format
    print(manager.format_rule(rule))


def cmd_rule_update(args):
    """Update an existing rule."""
    manager, _ = _get_manager(args)

    # Parse tag modifications
    add_tags = set()
    remove_tags = set()

    if args.tags:
        for tag in args.tags.split(','):
            tag = tag.strip()
            if tag.startswith('+'):
                add_tags.add(tag[1:])
            elif tag.startswith('-'):
                remove_tags.add(tag[1:])
            else:
                # Plain tag = add it
                add_tags.add(tag)

    try:
        rule = manager.update(
            name=args.name,
            category=args.category,
            subcategory=args.subcategory,
            add_tags=add_tags if add_tags else None,
            remove_tags=remove_tags if remove_tags else None,
            priority=args.priority,
        )
        manager.save()

        print(f"Updated rule: {rule.name}")
        if args.category:
            print(f"  Category: {rule.category}")
        if args.subcategory:
            print(f"  Subcategory: {rule.subcategory}")
        if add_tags or remove_tags:
            print(f"  Tags: {', '.join(sorted(rule.tags))}")
        if args.priority is not None:
            print(f"  Priority: {rule.priority}")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_rule_delete(args):
    """Delete a rule."""
    manager, _ = _get_manager(args)

    if args.pattern:
        deleted = manager.delete_by_pattern(args.pattern)
        identifier = f"pattern: {args.pattern}"
    else:
        deleted = manager.delete(args.name)
        identifier = args.name

    if deleted:
        manager.save()
        print(f"Deleted rule: {identifier}")
    else:
        print(f"Rule not found: {identifier}")
        sys.exit(1)


def cmd_rule_import(args):
    """Import rules from CSV."""
    manager, config_dir = _get_manager(args)

    # Read CSV content
    if args.stdin:
        import sys as sys_module
        csv_content = sys_module.stdin.read()
    elif args.file:
        csv_path = Path(args.file)
        if not csv_path.exists():
            print(f"File not found: {args.file}")
            sys.exit(1)
        csv_content = csv_path.read_text(encoding='utf-8')
    else:
        print("Error: Specify --stdin or provide a file path")
        sys.exit(1)

    imported = manager.import_csv(csv_content)
    manager.save()

    # Validate if requested
    validations = {}
    if getattr(args, 'validate', False):
        transactions = _load_transactions(config_dir)
        if transactions:
            for rule in imported:
                validations[rule.name] = manager.validate_rule(rule, transactions)

    # JSON output
    if getattr(args, 'json', False):
        import json
        output = {
            'imported': len(imported),
            'rules': []
        }
        total_matches = 0
        total_warnings = 0
        for rule in imported:
            rule_data = {
                'name': rule.name,
                'pattern': rule.match_expr,
                'category': rule.category,
            }
            if rule.name in validations:
                v = validations[rule.name]
                rule_data['matches'] = v.matches
                rule_data['total'] = v.total
                total_matches += v.matches
                if v.matches == 0 or v.shadows:
                    total_warnings += 1
            output['rules'].append(rule_data)
        if validations:
            output['total_matches'] = total_matches
            output['warnings'] = total_warnings
        print(json.dumps(output, indent=2))
        return

    # Human-readable output
    print(f"Imported {len(imported)} rules:")
    for rule in imported:
        cat = f" -> {rule.category}" if rule.category else ""
        if rule.name in validations:
            v = validations[rule.name]
            if v.matches > 0:
                print(f"  ✓ {rule.name}{cat}: {v.matches} transactions (${v.total:,.2f})")
            else:
                print(f"  ⚠ {rule.name}{cat}: 0 transactions")
        else:
            print(f"  {rule.name}{cat}")
