"""
Merchant rule engine for expression-based transaction categorization.

Parses .rules files and evaluates rules against transactions.
Supports two-pass evaluation: categorization (first match) + tagging (all matches).

File format:
    # Variables (optional)
    is_large = amount > 500

    # Rules
    [Netflix]
    match: contains("NETFLIX")
    category: Subscriptions
    subcategory: Streaming
    tags: entertainment, recurring

    [Large Purchase]
    match: is_large
    tags: large
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import date as date_type

from tally import expr_parser


@dataclass
class MerchantRule:
    """A rule for matching and categorizing transactions."""

    name: str  # Rule name (from [Name])
    match_expr: str  # Match expression string
    category: str = ""  # Category (may be empty for tag-only rules)
    subcategory: str = ""
    merchant: str = ""  # Display name (defaults to rule name)
    tags: Set[str] = field(default_factory=set)
    line_number: int = 0  # For error reporting
    let_bindings: List[Tuple[str, str]] = field(default_factory=list)  # [(var_name, expr), ...]
    fields: Dict[str, str] = field(default_factory=dict)  # {field_name: expr} extra fields to add

    def __post_init__(self):
        if not self.merchant:
            self.merchant = self.name

    @property
    def is_categorization_rule(self) -> bool:
        """True if this rule assigns a category (not just tags)."""
        return bool(self.category)


@dataclass
class MatchResult:
    """Result of matching a transaction against rules."""

    matched: bool = False
    merchant: str = ""
    category: str = ""
    subcategory: str = ""
    tags: Set[str] = field(default_factory=set)
    matched_rule: Optional[MerchantRule] = None
    tag_rules: List[MerchantRule] = field(default_factory=list)
    extra_fields: Dict[str, Any] = field(default_factory=dict)  # Evaluated fields from matching rule
    tag_sources: Dict[str, Dict] = field(default_factory=dict)  # {tag: {rule: name, pattern: expr}}


class MerchantParseError(Exception):
    """Error parsing .rules file."""

    def __init__(self, message: str, line_number: int = 0, line: str = ""):
        self.line_number = line_number
        self.line = line
        if line_number:
            message = f"Line {line_number}: {message}"
        super().__init__(message)


class MerchantEngine:
    """
    Engine for parsing .rules files and matching transactions.

    Uses two-pass evaluation:
    1. Categorization pass: First matching rule with category wins
    2. Tagging pass: ALL matching rules contribute their tags
    """

    def __init__(self):
        self.rules: List[MerchantRule] = []
        self.variables: Dict[str, Any] = {}
        self.transforms: List[Tuple[str, str]] = []  # [(field_path, expression), ...]
        self._compiled_exprs: Dict[str, Any] = {}  # Cache of parsed ASTs

    def load_file(self, filepath: Path) -> None:
        """Load rules from a .rules file."""
        content = filepath.read_text(encoding='utf-8')
        self.parse(content)

    def parse(self, content: str) -> None:
        """Parse .rules file content."""
        self.rules = []
        self.variables = {}
        self.transforms = []
        self._compiled_exprs = {}

        lines = content.split('\n')
        current_rule: Optional[Dict[str, Any]] = None
        rule_start_line = 0

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith('#'):
                continue

            # Rule header: [Name]
            if stripped.startswith('[') and stripped.endswith(']'):
                # Save previous rule
                if current_rule:
                    self._add_rule(current_rule, rule_start_line)

                # Start new rule
                rule_name = stripped[1:-1].strip()
                if not rule_name:
                    raise MerchantParseError("Empty rule name", line_num, line)
                current_rule = {'name': rule_name}
                rule_start_line = line_num
                continue

            # Variable or transform assignment: name = expression
            if '=' in stripped and current_rule is None:
                # Check if it's not inside a rule (i.e., a top-level assignment)
                # Match field.name or regular variable name
                match = re.match(r'^(field\.[a-zA-Z_][a-zA-Z0-9_]*|[a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)$', stripped)
                if match:
                    lhs, rhs = match.groups()
                    try:
                        if lhs.startswith('field.'):
                            # Field transform: field.description = regex_replace(...)
                            self.transforms.append((lhs, rhs))
                        else:
                            # Variable definition: is_large = amount > 500
                            self.variables[lhs.lower()] = rhs
                    except Exception as e:
                        raise MerchantParseError(
                            f"Invalid expression: {e}", line_num, line
                        )
                    continue

            # Rule property: key: value
            if ':' in stripped and current_rule is not None:
                key, value = stripped.split(':', 1)
                key = key.strip().lower()
                value = value.strip()

                if key == 'let':
                    # let: var_name = expression
                    let_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)$', value)
                    if not let_match:
                        raise MerchantParseError(
                            f"Invalid let syntax. Expected: let: name = expression",
                            line_num, line
                        )
                    var_name, expr = let_match.groups()
                    if 'let_bindings' not in current_rule:
                        current_rule['let_bindings'] = []
                    current_rule['let_bindings'].append((var_name.lower(), expr))
                elif key == 'field':
                    # field: name = expression (adds extra field to transaction)
                    field_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)$', value)
                    if not field_match:
                        raise MerchantParseError(
                            f"Invalid field syntax. Expected: field: name = expression",
                            line_num, line
                        )
                    field_name, expr = field_match.groups()
                    if 'fields' not in current_rule:
                        current_rule['fields'] = {}
                    current_rule['fields'][field_name.lower()] = expr
                elif key == 'match':
                    current_rule['match_expr'] = value
                elif key == 'category':
                    current_rule['category'] = value
                elif key == 'subcategory':
                    current_rule['subcategory'] = value
                elif key == 'merchant':
                    current_rule['merchant'] = value
                elif key == 'tags':
                    # Parse comma-separated tags, but don't split inside parentheses
                    tags = set()
                    depth = 0
                    current = []
                    for char in value:
                        if char == '(':
                            depth += 1
                            current.append(char)
                        elif char == ')':
                            depth -= 1
                            current.append(char)
                        elif char == ',' and depth == 0:
                            tag = ''.join(current).strip()
                            if tag:
                                tags.add(tag)
                            current = []
                        else:
                            current.append(char)
                    # Don't forget the last tag
                    tag = ''.join(current).strip()
                    if tag:
                        tags.add(tag)
                    current_rule['tags'] = tags
                else:
                    raise MerchantParseError(
                        f"Unknown property: {key}", line_num, line
                    )
                continue

            # If we get here and have a current rule, it might be an error
            if current_rule is not None:
                raise MerchantParseError(
                    f"Unexpected content in rule", line_num, line
                )

        # Save final rule
        if current_rule:
            self._add_rule(current_rule, rule_start_line)

    def _add_rule(self, rule_data: Dict[str, Any], line_number: int) -> None:
        """Add a parsed rule to the engine."""
        if 'match_expr' not in rule_data:
            raise MerchantParseError(
                f"Rule '{rule_data['name']}' missing 'match:' expression",
                line_number
            )

        # A rule must have either category or tags (or both)
        has_category = 'category' in rule_data and rule_data['category']
        has_tags = 'tags' in rule_data and rule_data['tags']

        if not has_category and not has_tags:
            raise MerchantParseError(
                f"Rule '{rule_data['name']}' must have 'category:' or 'tags:'",
                line_number
            )

        # Pre-parse let expressions for validation
        let_bindings = rule_data.get('let_bindings', [])
        for var_name, expr in let_bindings:
            try:
                expr_parser.parse_expression(expr)
            except expr_parser.ExpressionError as e:
                raise MerchantParseError(
                    f"Invalid let expression '{var_name}' in '{rule_data['name']}': {e}",
                    line_number
                )

        # Pre-parse field expressions for validation
        fields = rule_data.get('fields', {})
        for field_name, expr in fields.items():
            try:
                expr_parser.parse_expression(expr)
            except expr_parser.ExpressionError as e:
                raise MerchantParseError(
                    f"Invalid field expression '{field_name}' in '{rule_data['name']}': {e}",
                    line_number
                )

        # Pre-parse the match expression for validation
        try:
            expr_parser.parse_expression(rule_data['match_expr'])
        except expr_parser.ExpressionError as e:
            raise MerchantParseError(
                f"Invalid match expression in '{rule_data['name']}': {e}",
                line_number
            )

        rule = MerchantRule(
            name=rule_data['name'],
            match_expr=rule_data['match_expr'],
            category=rule_data.get('category', ''),
            subcategory=rule_data.get('subcategory', ''),
            merchant=rule_data.get('merchant', ''),
            tags=rule_data.get('tags', set()),
            line_number=line_number,
            let_bindings=let_bindings,
            fields=fields,
        )
        self.rules.append(rule)

    def _evaluate_variables(self, transaction: Dict, data_sources: Optional[Dict] = None) -> Dict[str, Any]:
        """Evaluate variable expressions against a transaction."""
        evaluated = {}
        for name, expr in self.variables.items():
            try:
                result = expr_parser.evaluate_transaction(expr, transaction, data_sources=data_sources)
                evaluated[name] = result
            except expr_parser.ExpressionError:
                # If variable can't be evaluated, skip it
                # (might depend on another variable not yet evaluated)
                pass
        return evaluated

    def _evaluate_let_bindings(
        self,
        rule: MerchantRule,
        transaction: Dict,
        base_variables: Dict[str, Any],
        data_sources: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Evaluate rule-level let bindings in order.

        Let bindings are evaluated sequentially so later bindings can
        reference earlier ones. Returns dict of evaluated variables.
        """
        variables = base_variables.copy()
        for var_name, expr in rule.let_bindings:
            try:
                result = expr_parser.evaluate_transaction(
                    expr, transaction, variables=variables, data_sources=data_sources
                )
                variables[var_name] = result
            except expr_parser.ExpressionError:
                # If binding fails, set to None so match can still work
                variables[var_name] = None
        return variables

    def _evaluate_fields(
        self,
        rule: MerchantRule,
        transaction: Dict,
        variables: Dict[str, Any],
        data_sources: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Evaluate field expressions for a matching rule.

        Returns dict of field_name -> evaluated_value.
        """
        evaluated = {}
        for field_name, expr in rule.fields.items():
            try:
                result = expr_parser.evaluate_transaction(
                    expr, transaction, variables=variables, data_sources=data_sources
                )
                evaluated[field_name] = result
            except expr_parser.ExpressionError:
                # If field evaluation fails, skip it
                pass
        return evaluated

    def _resolve_tags(
        self,
        rule: MerchantRule,
        transaction: Dict,
        variables: Dict,
        data_sources: Optional[Dict] = None,
    ) -> Set[str]:
        """
        Resolve dynamic tags from a rule, evaluating any {expression} placeholders.

        Supports dynamic tag values from field access, let bindings, or expressions:
            ["wire", "banking"]           -> {"wire", "banking"}
            ["{field.txn_type}"]          -> {"ach"} (if field.txn_type == "ACH")
            ["{trade[0]['term']}"]        -> {"long"} (if trade is from let binding)

        Args:
            rule: The matched rule with tags
            transaction: Transaction dict
            variables: Dict of let bindings and global variables
            data_sources: Optional dict of supplemental sources

        Returns:
            Set of resolved tag strings (lowercased)
        """
        resolved = set()
        for tag in rule.tags:
            tag = tag.strip()
            if not tag:
                continue

            if tag.startswith('{') and tag.endswith('}'):
                # Dynamic tag - evaluate expression
                expr = tag[1:-1].strip()
                if not expr:
                    continue

                try:
                    result = expr_parser.evaluate_transaction(
                        expr, transaction, variables=variables, data_sources=data_sources
                    )
                    if result:
                        # Handle list results (e.g., from list comprehensions)
                        if isinstance(result, list):
                            for item in result:
                                if item:
                                    resolved.add(str(item).strip().lower())
                        else:
                            stripped = str(result).strip()
                            if stripped:
                                resolved.add(stripped.lower())
                except expr_parser.ExpressionError:
                    # Skip invalid expressions silently
                    pass
            else:
                # Static tag
                resolved.add(tag.lower())

        return resolved

    def match(self, transaction: Dict, data_sources: Optional[Dict] = None) -> MatchResult:
        """
        Match a transaction against all rules.

        Two-pass evaluation:
        1. Find first categorization rule that matches (sets merchant/category/subcategory)
        2. Collect tags from ALL matching rules (including tag-only rules)

        Args:
            transaction: Transaction dict with description, amount, date, etc.
            data_sources: Optional dict mapping source names to list of row dicts
        """
        result = MatchResult()
        all_tags: Set[str] = set()
        tag_sources: Dict[str, Dict] = {}

        # Evaluate global variables for this transaction
        global_variables = self._evaluate_variables(transaction, data_sources)

        # Pass 1: Find categorization (first match wins)
        # Pass 2: Collect all tags (runs through all rules)
        for rule in self.rules:
            try:
                # Evaluate rule-level let bindings (can reference global variables)
                if rule.let_bindings:
                    variables = self._evaluate_let_bindings(
                        rule, transaction, global_variables, data_sources
                    )
                else:
                    variables = global_variables

                matches = expr_parser.matches_transaction(
                    rule.match_expr, transaction, variables, data_sources
                )
            except expr_parser.ExpressionError:
                # Skip rules that can't be evaluated
                continue

            if matches:
                # Collect tags from ALL matching rules (resolve dynamic expressions)
                resolved_tags = self._resolve_tags(rule, transaction, variables, data_sources)
                for tag in resolved_tags:
                    if tag not in all_tags:
                        all_tags.add(tag)
                        # Track which rule added this tag (first rule wins)
                        tag_sources[tag] = {'rule': rule.name, 'pattern': rule.match_expr}
                result.tag_rules.append(rule)

                # Categorization: first rule with category wins
                if not result.matched and rule.is_categorization_rule:
                    result.matched = True
                    result.merchant = rule.merchant
                    result.category = rule.category
                    result.subcategory = rule.subcategory
                    result.matched_rule = rule

                    # Evaluate extra fields for the matching categorization rule
                    if rule.fields:
                        result.extra_fields = self._evaluate_fields(
                            rule, transaction, variables, data_sources
                        )

        result.tags = all_tags
        result.tag_sources = tag_sources
        return result

    def match_all(self, transactions: List[Dict]) -> List[MatchResult]:
        """Match multiple transactions."""
        return [self.match(txn) for txn in transactions]

    @property
    def categorization_rules(self) -> List[MerchantRule]:
        """Rules that assign categories."""
        return [r for r in self.rules if r.is_categorization_rule]

    @property
    def tag_only_rules(self) -> List[MerchantRule]:
        """Rules that only assign tags."""
        return [r for r in self.rules if not r.is_categorization_rule]


def load_merchants_file(filepath: Path) -> MerchantEngine:
    """Load a .rules file and return configured engine."""
    engine = MerchantEngine()
    engine.load_file(filepath)
    return engine


def parse_merchants(content: str) -> MerchantEngine:
    """Parse .rules content and return configured engine."""
    engine = MerchantEngine()
    engine.parse(content)
    return engine


# =============================================================================
# CSV Conversion (Backwards Compatibility)
# =============================================================================

def _modifier_to_expr(parsed_pattern) -> str:
    """Convert parsed CSV modifiers to expression string."""
    conditions = []

    # Import here to avoid circular dependency
    from tally.modifier_parser import ParsedPattern

    if not isinstance(parsed_pattern, ParsedPattern):
        return ""

    # Amount conditions
    for cond in parsed_pattern.amount_conditions:
        if cond.operator == ':':
            # Range
            conditions.append(f"amount >= {cond.min_value} and amount <= {cond.max_value}")
        elif cond.operator == '=':
            conditions.append(f"amount == {cond.value}")
        else:
            conditions.append(f"amount {cond.operator} {cond.value}")

    # Date conditions
    for cond in parsed_pattern.date_conditions:
        if cond.operator == '=':
            conditions.append(f'date == "{cond.value.isoformat()}"')
        elif cond.operator == ':':
            conditions.append(
                f'date >= "{cond.start_date.isoformat()}" and '
                f'date <= "{cond.end_date.isoformat()}"'
            )
        elif cond.operator == 'month':
            conditions.append(f"month == {cond.month}")
        elif cond.operator == 'relative':
            # Relative dates can't be easily converted - use approximation
            # Note: This isn't perfect, but it's a reasonable migration
            conditions.append(f"# Note: was last{cond.relative_days}days")

    return " and ".join(conditions)


def csv_rule_to_merchant_rule(
    pattern: str,
    merchant: str,
    category: str,
    subcategory: str,
    parsed_pattern,
    tags: List[str] = None,
) -> MerchantRule:
    """
    Convert a CSV rule to a MerchantRule.

    Args:
        pattern: The regex pattern (already extracted from modifiers)
        merchant: Merchant display name
        category: Category
        subcategory: Subcategory
        parsed_pattern: ParsedPattern with conditions
        tags: Optional list of tags

    Returns:
        MerchantRule that matches the same transactions
    """
    # Build match expression
    parts = []

    # Regex pattern match
    if pattern:
        # Escape any special characters in the pattern for the match expression
        # We use regex() function for the pattern
        parts.append(f'regex("{pattern}")')

    # Add modifier conditions
    modifier_expr = _modifier_to_expr(parsed_pattern)
    if modifier_expr:
        parts.append(modifier_expr)

    match_expr = " and ".join(parts) if parts else "true"

    return MerchantRule(
        name=merchant,
        match_expr=match_expr,
        category=category,
        subcategory=subcategory,
        merchant=merchant,
        tags=set(tags) if tags else set(),
    )


def csv_to_rules(csv_rules: List[Tuple]) -> List[MerchantRule]:
    """
    Convert a list of CSV rules to MerchantRules.

    Args:
        csv_rules: List of tuples from load_merchant_rules() or get_all_rules()
                   Format: (pattern, merchant, category, subcategory, parsed, [source], [tags])

    Returns:
        List of MerchantRule objects
    """
    rules = []
    for rule in csv_rules:
        # Handle various tuple formats
        tags = []
        if len(rule) == 7:
            pattern, merchant, category, subcategory, parsed, source, tags = rule
        elif len(rule) == 6:
            # Could be (p,m,c,s,parsed,source) or (p,m,c,s,parsed,tags)
            pattern, merchant, category, subcategory, parsed, extra = rule
            if isinstance(extra, list):
                tags = extra
        elif len(rule) == 5:
            pattern, merchant, category, subcategory, parsed = rule
        else:
            pattern, merchant, category, subcategory = rule
            parsed = None

        rules.append(csv_rule_to_merchant_rule(
            pattern=pattern,
            merchant=merchant,
            category=category,
            subcategory=subcategory,
            parsed_pattern=parsed,
            tags=tags,
        ))

    return rules


def csv_to_merchants_content(csv_rules: List[Tuple]) -> str:
    """
    Convert CSV rules to .rules file content.

    Used for migrating existing merchant_categories.csv to new format.

    Args:
        csv_rules: List of tuples from load_merchant_rules()

    Returns:
        String content for a .rules file
    """
    lines = [
        "# Tally Merchant Rules",
        "# Migrated from merchant_categories.csv",
        "#",
        "# Format:",
        "#   [Rule Name]",
        "#   match: <expression>",
        "#   category: <category>",
        "#   subcategory: <subcategory>",
        "#   tags: tag1, tag2  # optional",
        "",
    ]

    for rule in csv_rules:
        # Handle various tuple formats
        tags = []
        if len(rule) == 7:
            pattern, merchant, category, subcategory, parsed, source, tags = rule
        elif len(rule) == 6:
            pattern, merchant, category, subcategory, parsed, extra = rule
            if isinstance(extra, list):
                tags = extra
        elif len(rule) == 5:
            pattern, merchant, category, subcategory, parsed = rule
        else:
            pattern, merchant, category, subcategory = rule
            parsed = None

        # Build match expression
        parts = []
        if pattern:
            # Pattern is already properly escaped for regex use, write as-is
            parts.append(f'regex("{pattern}")')

        modifier_expr = _modifier_to_expr(parsed) if parsed else ""
        if modifier_expr and not modifier_expr.startswith("#"):
            parts.append(modifier_expr)

        match_expr = " and ".join(parts) if parts else "true"

        # Write rule block
        lines.append(f"[{merchant}]")
        lines.append(f"match: {match_expr}")
        lines.append(f"category: {category}")
        lines.append(f"subcategory: {subcategory}")
        if tags:
            lines.append(f"tags: {', '.join(tags)}")
        lines.append("")

    return "\n".join(lines)


def load_csv_as_engine(csv_path: Path) -> MerchantEngine:
    """
    Load a merchant_categories.csv file as a MerchantEngine.

    This provides backwards compatibility - existing CSV files
    work seamlessly with the new engine.

    Args:
        csv_path: Path to merchant_categories.csv

    Returns:
        Configured MerchantEngine
    """
    from tally.merchant_utils import load_merchant_rules

    csv_rules = load_merchant_rules(str(csv_path))
    merchant_rules = csv_to_rules(csv_rules)

    engine = MerchantEngine()
    engine.rules = merchant_rules
    return engine
