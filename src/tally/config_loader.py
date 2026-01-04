"""
Configuration loader for spending analysis.

Loads settings from YAML config files.
"""

import os

from .format_parser import parse_format_string, is_special_parser_type
from .section_engine import load_sections, SectionParseError

# Try to import yaml, fall back to simple parsing if not available
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_yaml_simple(filepath):
    """Simple YAML parser for basic key-value configs (fallback if PyYAML not installed)."""
    config = {}
    current_list_key = None
    current_list = []
    current_item = {}

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            # Skip comments and empty lines
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            # Check indentation level
            indent = len(line) - len(line.lstrip())

            # Handle list items
            if stripped.startswith('- '):
                if current_list_key:
                    if current_item:
                        current_list.append(current_item)
                        current_item = {}
                    # Parse the item
                    item_content = stripped[2:].strip()
                    if ':' in item_content:
                        key, value = item_content.split(':', 1)
                        current_item[key.strip()] = value.strip()
                continue

            # Handle nested list item properties
            if indent > 2 and current_list_key and ':' in stripped:
                key, value = stripped.split(':', 1)
                current_item[key.strip()] = value.strip()
                continue

            # Handle top-level key-value pairs
            if ':' in stripped and indent == 0:
                # Save any pending list
                if current_list_key and current_list:
                    if current_item:
                        current_list.append(current_item)
                    config[current_list_key] = current_list
                    current_list = []
                    current_item = {}
                    current_list_key = None

                key, value = stripped.split(':', 1)
                key = key.strip()
                value = value.strip()

                if value:
                    # Remove quotes if present
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    config[key] = value
                else:
                    # This might be a list
                    current_list_key = key

    # Save any pending list
    if current_list_key:
        if current_item:
            current_list.append(current_item)
        if current_list:
            config[current_list_key] = current_list

    return config


def load_settings(config_dir, settings_file='settings.yaml'):
    """Load main settings from settings.yaml (or specified file)."""
    settings_path = os.path.join(config_dir, settings_file)

    if not os.path.exists(settings_path):
        raise FileNotFoundError(f"Settings file not found: {settings_path}")

    if HAS_YAML:
        with open(settings_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    else:
        return load_yaml_simple(settings_path)


def resolve_source_format(source, warnings=None):
    """
    Resolve the format specification for a data source.

    Handles two configuration styles:
    - type: 'amex' or 'boa' (predefined parsers, backward compatible)
    - format: '{date:%m/%d/%Y}, {description}, {amount}' (custom format string)

    For custom formats, also supports:
    - columns.description: Template for combining custom captures
      Example: "{merchant} ({type})" when format uses {type}, {merchant}
    - supplemental: true (data source is query-only, doesn't generate transactions)

    Args:
        source: Data source configuration dict
        warnings: Optional list to append deprecation warnings to

    Returns the source dict with additional keys:
    - '_parser_type': 'amex', 'boa', or 'generic'
    - '_format_spec': FormatSpec object (for generic parser) or None
    - '_supplemental': True if this is a supplemental (query-only) source
    """
    source = source.copy()
    source_name = source.get('name', 'unknown')

    # Check for deprecated account_type setting
    if 'account_type' in source:
        raise ValueError(
            f"Source '{source_name}': 'account_type' is no longer supported. "
            f"Use '{{-amount}}' to negate, or '{{+amount}}' for absolute value. "
            f"To filter income/deposits, add categorization rules with 'amount < 0' conditions. "
            f"Run 'tally inspect {source.get('file', '<file>')}' to see your data's sign convention."
        )

    # Check for deprecated skip_negative setting
    if 'skip_negative' in source:
        raise ValueError(
            f"Source '{source_name}': 'skip_negative' is no longer supported. "
            f"All transactions are now included. To filter credits/deposits, "
            f"categorize them with rules using 'amount < 0'. "
            f"Use '{{+amount}}' if both signs represent spending."
        )

    if 'format' in source:
        # Custom format string provided
        format_str = source['format']

        # {-amount} is a first-class feature for normalizing source sign conventions
        # No deprecation warning needed

        # Check for columns.description template
        columns = source.get('columns', {})
        description_template = columns.get('description') if isinstance(columns, dict) else None

        try:
            format_spec = parse_format_string(format_str, description_template)

            # Apply explicit settings
            if 'delimiter' in source:
                format_spec.delimiter = source['delimiter']
            if 'has_header' in source:
                format_spec.has_header = source['has_header']
            if 'negate_amount' in source:
                format_spec.negate_amount = source['negate_amount']
            if 'tags_from_fields' in source:
                format_spec.tags_from_fields = source['tags_from_fields']

            source['_format_spec'] = format_spec
            source['_parser_type'] = 'generic'
        except ValueError as e:
            raise ValueError(f"Invalid format for source '{source_name}': {e}")

    elif 'type' in source:
        source_type = source['type'].lower()

        if is_special_parser_type(source_type):
            # Use legacy parser (amex, boa) - add deprecation warning
            if warnings is not None:
                warnings.append({
                    'type': 'deprecated',
                    'source': source_name,
                    'feature': f'type: {source_type}',
                    'message': f"Source '{source_name}' uses deprecated 'type: {source_type}'.",
                    'suggestion': "Use 'format' instead for better control.",
                    'example': f"  - name: {source_name}\n    format: \"{{date:%m/%d/%Y}}, {{description}}, {{amount}}\"",
                })
            source['_parser_type'] = source_type
            source['_format_spec'] = None
        else:
            raise ValueError(f"Unknown source type: '{source_type}'. Use 'format' instead.")

    else:
        raise ValueError(
            f"Data source '{source.get('name', 'unknown')}' must specify "
            "'format'. Use 'tally inspect <file>' to determine the format."
        )

    # Mark supplemental sources (query-only, don't generate transactions)
    source['_supplemental'] = source.get('supplemental', False)

    return source


def load_config(config_dir, settings_file='settings.yaml'):
    """Load all configuration files.

    Args:
        config_dir: Path to config directory containing settings.yaml and CSV files.
        settings_file: Name of the settings file to load (default: settings.yaml)

    Returns:
        dict with all configuration values
    """
    config_dir = os.path.abspath(config_dir)

    if not os.path.isdir(config_dir):
        raise FileNotFoundError(f"Config directory not found: {config_dir}")

    # Load main settings
    config = load_settings(config_dir, settings_file)

    # Collect deprecation warnings
    warnings = []

    # Process data sources to resolve format specs
    if config.get('data_sources'):
        config['data_sources'] = [
            resolve_source_format(source, warnings=warnings)
            for source in config['data_sources']
        ]
    else:
        config['data_sources'] = []

    # Store warnings for CLI to display
    config['_warnings'] = warnings

    # Warn about removed home_locations/travel_locations feature
    removed_settings = []
    if 'home_locations' in config:
        removed_settings.append('home_locations')
    if 'home_state' in config:
        removed_settings.append('home_state')
    if 'travel_labels' in config:
        removed_settings.append('travel_labels')

    if removed_settings:
        warnings.append({
            'type': 'deprecated',
            'source': 'settings.yaml',
            'feature': ', '.join(removed_settings),
            'message': f"Settings '{', '.join(removed_settings)}' have been removed.",
            'suggestion': "Use merchant rules with a 'Travel' category instead. Remove these settings from settings.yaml.",
        })

    # Store config dir for reference
    config['_config_dir'] = config_dir

    # Rule matching mode: 'first_match' (default, backwards compatible) or 'most_specific'
    rule_mode = config.get('rule_mode', 'first_match')
    if rule_mode not in ('first_match', 'most_specific'):
        warnings.append({
            'type': 'warning',
            'source': 'settings.yaml',
            'message': f"Invalid rule_mode: '{rule_mode}'. Using 'first_match'.",
            'suggestion': "Use 'first_match' or 'most_specific'.",
        })
        rule_mode = 'first_match'
    config['rule_mode'] = rule_mode


    # Load merchants file (optional - merchants_file in settings.yaml)
    # This is the new .rules format; merchant_categories.csv is deprecated
    merchants_file = config.get('merchants_file')
    if merchants_file:
        budget_dir = os.path.dirname(config_dir)
        merchants_path = os.path.join(budget_dir, merchants_file)
        if os.path.exists(merchants_path):
            config['_merchants_file'] = merchants_path
            config['_merchants_format'] = 'new'  # .merchants format
        else:
            warnings.append({
                'type': 'warning',
                'source': 'settings.yaml',
                'message': f"Merchants file not found: {merchants_file}",
                'suggestion': f"Create {merchants_file} or remove merchants_file from settings.yaml",
            })
            config['_merchants_file'] = None
            config['_merchants_format'] = None
    else:
        # No merchants_file configured - check for legacy CSV
        csv_file = os.path.join(config_dir, 'merchant_categories.csv')
        if os.path.exists(csv_file):
            config['_merchants_file'] = csv_file
            config['_merchants_format'] = 'csv'  # Legacy format
        else:
            config['_merchants_file'] = None
            config['_merchants_format'] = None

    # Load view definitions (optional - views_file in settings.yaml)
    views_file = config.get('views_file')
    if views_file:
        # Resolve path relative to config directory's parent (budget directory)
        budget_dir = os.path.dirname(config_dir)
        views_path = os.path.join(budget_dir, views_file)
        if os.path.exists(views_path):
            try:
                config['sections'] = load_sections(views_path)
                config['_views_file'] = views_path
            except SectionParseError as e:
                warnings.append({
                    'type': 'error',
                    'source': views_file,
                    'message': f"Error loading views: {e}",
                    'suggestion': f"Fix the syntax error in {views_file}",
                })
                config['sections'] = None
                config['_views_file'] = None
        else:
            warnings.append({
                'type': 'warning',
                'source': 'settings.yaml',
                'message': f"Views file not found: {views_file}",
                'suggestion': f"Create {views_file} or remove views_file from settings.yaml",
            })
            config['sections'] = None
            config['_views_file'] = None
    else:
        # No views_file configured - views feature is optional
        config['sections'] = None
        config['_views_file'] = None

    return config


def load_supplemental_sources(config, config_dir):
    """
    Load supplemental data sources as queryable row dictionaries.

    Supplemental sources (marked with supplemental: true) are loaded into memory
    but don't generate transactions. They can be queried from rule expressions
    using list comprehensions.

    Args:
        config: Config dict from load_config()
        config_dir: Path to config directory

    Returns:
        Dict mapping source names to list of row dicts.
        Each row dict has fields from the source's format string.
        Example: {'amazon_orders': [{'date': date(...), 'item': 'Book', 'amount': 12.99}, ...]}
    """
    import csv
    from datetime import datetime

    data_sources = {}

    for source in config.get('data_sources', []):
        if not source.get('_supplemental', False):
            continue

        source_name = source.get('name', '').lower()
        if not source_name:
            continue

        # Find the file
        filepath = os.path.join(config_dir, '..', source['file'])
        filepath = os.path.normpath(filepath)
        if not os.path.exists(filepath):
            filepath = os.path.join(os.path.dirname(config_dir), source['file'])
        if not os.path.exists(filepath):
            continue

        format_spec = source.get('_format_spec')
        if not format_spec:
            continue

        # Parse the CSV file into row dicts
        rows = []
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                # Handle delimiter: None means comma (default)
                delimiter = format_spec.delimiter
                if delimiter == 'tab':
                    delimiter = '\t'
                elif delimiter == 'whitespace' or delimiter is None:
                    delimiter = ','

                reader = csv.reader(f, delimiter=delimiter)

                # Skip header if specified
                if format_spec.has_header:
                    next(reader, None)

                # Build column map from format_spec
                # custom_captures: {'symbol': 1, 'action': 2, ...}
                column_map = {}
                if format_spec.custom_captures:
                    for name, col_idx in format_spec.custom_captures.items():
                        column_map[name.lower()] = col_idx

                # Add standard columns
                column_map['date'] = format_spec.date_column
                column_map['amount'] = format_spec.amount_column
                if format_spec.description_column is not None:
                    column_map['description'] = format_spec.description_column
                if format_spec.location_column is not None:
                    column_map['location'] = format_spec.location_column

                for line in reader:
                    if not line or all(not cell.strip() for cell in line):
                        continue

                    # Parse row according to column map
                    row = {}
                    for field_name, col_idx in column_map.items():
                        if col_idx >= len(line):
                            continue

                        value = line[col_idx].strip()

                        # Type conversion
                        if field_name == 'date':
                            try:
                                row[field_name] = datetime.strptime(value, format_spec.date_format).date()
                            except ValueError:
                                row[field_name] = value
                        elif field_name in ('amount', 'item_amount', 'price', 'total', 'proceeds', 'costbasis', 'gainloss', 'grosspay', 'federal', 'state', 'socialsec', 'medicare', '401k', 'hsa', 'netpay', 'shares'):
                            try:
                                # Handle decimal separator
                                decimal_sep = source.get('decimal_separator', '.')
                                if decimal_sep != '.':
                                    value = value.replace(decimal_sep, '.')
                                # Remove currency symbols
                                value = value.replace('$', '').replace(',', '').strip()
                                row[field_name] = float(value) if value else 0.0
                            except ValueError:
                                row[field_name] = 0.0
                        else:
                            row[field_name] = value

                    if row:
                        rows.append(row)

        except Exception:
            # Skip sources that can't be loaded
            continue

        if rows:
            data_sources[source_name] = rows

    return data_sources
