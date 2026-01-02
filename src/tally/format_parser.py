"""
Format string parser for custom CSV column mappings.

Parses format strings like: {date:%m/%d/%Y}, {description}, {_}, {amount}
Position in the string implies column index.
"""

import re
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class FormatSpec:
    """Parsed format specification for CSV parsing."""
    date_column: int
    date_format: str
    amount_column: int
    description_column: Optional[int] = None  # Mode 1: single {description}
    custom_captures: Optional[dict] = None    # Mode 2: {type}, {merchant}, etc.
    description_template: Optional[str] = None  # Mode 2: "{merchant} - {type}"
    extra_fields: Optional[dict] = None  # Extra field captures alongside {description}
    location_column: Optional[int] = None
    has_header: bool = True
    source_name: Optional[str] = None  # Optional override for transaction source
    negate_amount: bool = False  # If True, flip the sign of amounts (use {-amount} in format)
    abs_amount: bool = False  # If True, take absolute value of amounts (use {+amount} in format)
    delimiter: Optional[str] = None  # Column delimiter: None=comma, 'tab', 'whitespace', or regex pattern


# Reserved field names that cannot be used for custom captures
RESERVED_NAMES = {'date', 'amount', 'location', 'description', '_', '*', 'field'}


def parse_format_string(format_str: str, description_template: Optional[str] = None) -> FormatSpec:
    """
    Parse a format string into a FormatSpec.

    Format string syntax:
        {field}           - Map this column to a field
        {field:format}    - Field with format specifier (e.g., date format)
        {-amount}         - Negate amount (flip sign, credits become debits)
        {+amount}         - Absolute value (all amounts become positive)
        {_}               - Skip this column
        {*}               - Skip this column (alias for {_})

    Two modes:
        Mode 1 (simple): Use {description} to capture a single column
        Mode 2 (custom): Use named captures like {type}, {merchant} with a description_template

    Required fields: date, amount, and either {description} or custom captures
    Optional fields: location

    Examples:
        Mode 1: "{date:%m/%d/%Y}, {description}, {amount}"
        Mode 2: "{date:%m/%d/%Y}, {type}, {merchant}, {amount}"
                with description_template="{merchant} ({type})"

    Args:
        format_str: The format string to parse
        description_template: Template for combining custom captures (Mode 2)

    Returns:
        FormatSpec with column mappings

    Raises:
        ValueError: If format string is invalid or missing required fields
    """
    # Pattern to match {field}, {-field}, {+field}, {field:format}, or {*}
    # Note: * is an alias for _ (skip column), common from AI-generated configs
    field_pattern = re.compile(r'\{([-+]?)(\w+|\*)(?::([^}]+))?\}')

    # Split by comma and parse each column
    parts = [p.strip() for p in format_str.split(',')]

    if not parts:
        raise ValueError("Empty format string")

    field_positions = {}
    custom_captures = {}
    date_format = '%m/%d/%Y'  # Default
    negate_amount = False
    abs_amount = False

    for idx, part in enumerate(parts):
        match = field_pattern.match(part)
        if not match:
            raise ValueError(f"Invalid format at column {idx}: '{part}'. Expected {{field}} or {{field:format}}")

        negate_prefix = match.group(1)  # '-' or ''
        field_name = match.group(2).lower()
        format_spec = match.group(3)  # May be None

        # Skip placeholder columns: {_} or {*} (alias)
        if field_name == '_' or field_name == '*':
            continue

        # Check if it's a reserved field or custom capture
        if field_name in RESERVED_NAMES:
            # Reserved field
            if field_name in field_positions:
                raise ValueError(f"Duplicate field '{field_name}' at column {idx}")
            field_positions[field_name] = idx

            # Capture date format if specified
            if field_name == 'date' and format_spec:
                date_format = format_spec

            # Capture sign modifier for amount
            if field_name == 'amount':
                if negate_prefix == '-':
                    negate_amount = True
                elif negate_prefix == '+':
                    abs_amount = True
        else:
            # Custom capture for description template
            if field_name in custom_captures:
                raise ValueError(f"Duplicate custom capture '{field_name}' at column {idx}")
            custom_captures[field_name] = idx

    has_description = 'description' in field_positions
    has_custom = len(custom_captures) > 0

    # If we have {description} + custom captures, treat custom captures as extra fields
    # They become accessible as field.* in rule expressions
    extra_fields = None
    if has_description and has_custom:
        extra_fields = custom_captures
        custom_captures = {}  # Clear - not used for description template mode
        has_custom = False

    # Validate: need either {description} or custom captures
    if not has_description and not has_custom:
        raise ValueError(
            "Format must include {description} or custom captures. "
            "Add {description} for simple mode, or use named captures like {merchant}, {type}."
        )

    # Validate: custom captures (without {description}) require description_template
    if has_custom and not description_template:
        first_custom = list(custom_captures.keys())[0]
        raise ValueError(
            f"Custom captures require a description template. "
            f"Add to your data source config:\n"
            f"  columns:\n"
            f"    description: \"{{{first_custom}}} ...\""
        )

    # Validate: template references must exist in captures
    if description_template:
        for ref in re.findall(r'\{(\w+)\}', description_template):
            if ref not in custom_captures:
                available = ', '.join('{' + k + '}' for k in custom_captures)
                raise ValueError(
                    f"Description template references '{{{ref}}}' but it's not captured. "
                    f"Available captures: {available}"
                )

    # Validate required reserved fields
    required = {'date', 'amount'}
    missing = required - set(field_positions.keys())
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    return FormatSpec(
        date_column=field_positions['date'],
        date_format=date_format,
        amount_column=field_positions['amount'],
        description_column=field_positions.get('description'),
        custom_captures=custom_captures if custom_captures else None,
        description_template=description_template,
        extra_fields=extra_fields,
        location_column=field_positions.get('location'),
        has_header=True,
        negate_amount=negate_amount,
        abs_amount=abs_amount,
        delimiter=None  # Will be set by config_loader if specified
    )


# Predefined format shortcuts for backward compatibility
PREDEFINED_FORMATS = {
    # Standard AMEX CSV: Date,Description,Amount (with headers)
    'amex': None,  # Use legacy parser - handles header-based CSV
    # BOA text format - not a standard CSV, needs special parser
    'boa': None,   # Use legacy parser - regex-based line parsing
}


def get_predefined_format(source_type: str) -> Optional[str]:
    """
    Get the format string for a predefined source type.

    Returns None if the type requires a special parser (not generic CSV).
    """
    return PREDEFINED_FORMATS.get(source_type.lower())


def is_special_parser_type(source_type: str) -> bool:
    """Check if a source type requires a special (non-generic) parser."""
    return source_type.lower() in PREDEFINED_FORMATS
