"""
Configuration loader for spending analysis.

Loads settings from YAML config files.
"""

import os

from .format_parser import parse_format_string, is_special_parser_type
from .classification_rules import get_fallback_rules_parsed
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

    Args:
        source: Data source configuration dict
        warnings: Optional list to append deprecation warnings to

    Returns the source dict with additional keys:
    - '_parser_type': 'amex', 'boa', or 'generic'
    - '_format_spec': FormatSpec object (for generic parser) or None
    """
    source = source.copy()
    source_name = source.get('name', 'unknown')

    # Check for deprecated account_type setting
    if 'account_type' in source:
        raise ValueError(
            f"Source '{source_name}': 'account_type' is no longer supported. "
            f"Use '{{-amount}}' in your format string to negate amounts. "
            f"To filter income/deposits, add categorization rules with 'amount < 0' conditions. "
            f"Run 'tally inspect {source.get('file', '<file>')}' to see your data's sign convention."
        )

    # Check for deprecated skip_negative setting
    if 'skip_negative' in source:
        raise ValueError(
            f"Source '{source_name}': 'skip_negative' is no longer supported. "
            f"All transactions are now included. To filter credits/deposits, "
            f"categorize them with rules using 'amount < 0' (or 'amount > 0' if using {{-amount}})."
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

    # Normalize home_locations to a set of uppercase location codes
    # Support legacy home_state for backward compatibility
    home_locations = config.get('home_locations', [])
    if not home_locations and 'home_state' in config:
        home_locations = [config['home_state']]
    if isinstance(home_locations, str):
        home_locations = [home_locations]
    config['home_locations'] = {loc.upper() for loc in home_locations}

    # Normalize travel_labels to uppercase keys
    travel_labels = config.get('travel_labels', {})
    config['travel_labels'] = {k.upper(): v for k, v in travel_labels.items()}

    # Store config dir for reference
    config['_config_dir'] = config_dir

    # Currency format for display (default: USD)
    config['currency_format'] = config.get('currency_format', '${amount}')

    # All merchants classified as 'variable' - views.rules handles custom sections
    config['classification_rules'] = get_fallback_rules_parsed()

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
