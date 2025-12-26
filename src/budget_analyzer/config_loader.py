"""
Configuration loader for spending analysis.

Loads settings from YAML config files.
"""

import os

from .format_parser import parse_format_string, is_special_parser_type

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


def resolve_source_format(source):
    """
    Resolve the format specification for a data source.

    Handles two configuration styles:
    - type: 'amex' or 'boa' (predefined parsers, backward compatible)
    - format: '{date:%m/%d/%Y}, {description}, {amount}' (custom format string)

    Returns the source dict with additional keys:
    - '_parser_type': 'amex', 'boa', or 'generic'
    - '_format_spec': FormatSpec object (for generic parser) or None
    """
    source = source.copy()

    if 'format' in source:
        # Custom format string provided
        format_str = source['format']
        try:
            source['_format_spec'] = parse_format_string(format_str)
            source['_parser_type'] = 'generic'
        except ValueError as e:
            raise ValueError(f"Invalid format for source '{source.get('name', 'unknown')}': {e}")

    elif 'type' in source:
        source_type = source['type'].lower()

        if is_special_parser_type(source_type):
            # Use legacy parser (amex, boa)
            source['_parser_type'] = source_type
            source['_format_spec'] = None
        else:
            raise ValueError(f"Unknown source type: '{source_type}'. Use 'amex', 'boa', or provide a 'format' string.")

    else:
        raise ValueError(
            f"Data source '{source.get('name', 'unknown')}' must specify "
            "'type' or 'format'. Use 'budget-analyze inspect <file>' to determine the format."
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

    # Process data sources to resolve format specs
    if config.get('data_sources'):
        config['data_sources'] = [
            resolve_source_format(source)
            for source in config['data_sources']
        ]
    else:
        config['data_sources'] = []

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

    return config
