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
    priority: int = 50  # Higher priority = checked first for tie-breaking (default 50)
    line_number: int = 0  # For error reporting
    priority: int = 50  # Higher priority rules match first (default: 50)
    let_bindings: List[Tuple[str, str]] = field(default_factory=list)  # [(var_name, expr), ...]
    fields: Dict[str, str] = field(default_factory=dict)  # {field_name: expr} extra fields to add

    def __post_init__(self):
        if not self.merchant:
            self.merchant = self.name

    @property
    def is_categorization_rule(self) -> bool:
        """True if this rule assigns a category (not just tags)."""
        return bool(self.category)

    @property
    def has_merchant(self) -> bool:
        """True if this rule sets a merchant name."""
        return bool(self.merchant)

    @property
    def has_subcategory(self) -> bool:
        """True if this rule sets a subcategory."""
        return bool(self.subcategory)


@dataclass
class MatchResult:
    """Result of matching a transaction against rules."""

    matched: bool = False
    merchant: str = ""
    category: str = ""
    subcategory: str = ""
    tags: Set[str] = field(default_factory=set)
    matched_rule: Optional[MerchantRule] = None  # Rule that set category (most specific)
    merchant_rule: Optional[MerchantRule] = None  # Rule that set merchant (most specific)
    subcategory_rule: Optional[MerchantRule] = None  # Rule that set subcategory (most specific)
    all_matching_rules: List[MerchantRule] = field(default_factory=list)  # All rules that matched
    tag_rules: List[MerchantRule] = field(default_factory=list)  # Rules that contributed tags
    extra_fields: Dict[str, Any] = field(default_factory=dict)  # Evaluated fields from matching rule
    tag_sources: Dict[str, Dict] = field(default_factory=dict)  # {tag: {rule: name, pattern: expr}}


def calculate_specificity(rule: MerchantRule) -> Tuple[int, int, int, int]:
    """
    Calculate specificity score for a rule.

    Higher specificity = more specific rule = wins in conflict resolution.
    Returns tuple for lexicographic comparison:
        (priority, pattern_conditions, field_constraints, pattern_length)

    Examples:
        contains("UBER")                          -> (50, 1, 0, 4)
        contains("UBER") and contains("EATS")     -> (50, 2, 0, 8)
        contains("UBER") and amount > 50          -> (50, 1, 1, 4)
    """
    expr = rule.match_expr.lower()

    # Count pattern conditions (each pattern function adds specificity)
    pattern_funcs = ['contains(', 'regex(', 'normalized(', 'startswith(', 'fuzzy(', 'anyof(']
    pattern_count = sum(expr.count(f) for f in pattern_funcs)

    # Count field constraints (amount, date, month, etc.)
    field_keywords = ['amount', 'date', 'month', 'year', 'day', 'weekday', 'source', 'field.']
    field_count = sum(1 for kw in field_keywords if kw in expr)

    # Extract pattern text length (rough measure of specificity)
    pattern_length = _extract_pattern_length(rule.match_expr)

    return (rule.priority, pattern_count, field_count, pattern_length)


def _extract_pattern_length(match_expr: str) -> int:
    """Extract total length of pattern strings in a match expression."""
    import re
    # Find all quoted strings in the expression
    strings = re.findall(r'"([^"]*)"', match_expr)
    strings += re.findall(r"'([^']*)'", match_expr)
    return sum(len(s) for s in strings)


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

    Supports two matching modes (controlled by match_mode):
    - 'first_match' (default): First matching rule sets category (backwards compatible)
    - 'most_specific': Most specific matching rule wins (opt-in)

    In both modes, tags are collected from ALL matching rules.
    """

    def __init__(self, match_mode: str = 'first_match'):
        """
        Initialize the merchant engine.

        Args:
            match_mode: 'first_match' (default) or 'most_specific'
        """
        self.rules: List[MerchantRule] = []
        self.variables: Dict[str, Any] = {}
        self.transforms: List[Tuple[str, str]] = []  # [(field_path, expression), ...]
        self._compiled_exprs: Dict[str, Any] = {}  # Cache of parsed ASTs
        self.match_mode = match_mode

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
                elif key == 'priority':
                    try:
                        current_rule['priority'] = int(value)
                    except ValueError:
                        raise MerchantParseError(
                            f"Invalid priority value: {value} (must be integer)",
                            line_num, line
                        )
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

        # Sort rules by priority (desc), then file order (line_number asc)
        self.rules.sort(key=lambda r: (-r.priority, r.line_number))

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
            priority=rule_data.get('priority', 50),
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
        Match a transaction against rules.

        Behavior depends on match_mode:
        - 'first_match': First matching rule with category wins (backwards compatible)
        - 'most_specific': Most specific matching rule wins

        In both modes, tags are collected from ALL matching rules.

        Args:
            transaction: Transaction dict with description, amount, date, etc.
            data_sources: Optional dict mapping source names to list of row dicts
        """
        result = MatchResult()
        all_tags: Set[str] = set()
        tag_sources: Dict[str, Dict] = {}

        # Evaluate global variables for this transaction
        global_variables = self._evaluate_variables(transaction, data_sources)

        # Track the first categorization rule (for first_match mode)
        first_category_rule: Optional[Tuple[MerchantRule, Dict]] = None

        # Collect all matching rules (needed for most_specific mode and tag collection)
        matching_rules: List[Tuple[MerchantRule, Tuple[int, int, int, int], Dict]] = []

        # Evaluate ALL rules (we always need to do this for tag collection)
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
                specificity = calculate_specificity(rule)
                matching_rules.append((rule, specificity, variables))

                # Track first categorization rule for first_match mode
                if first_category_rule is None and rule.is_categorization_rule:
                    first_category_rule = (rule, variables)

                # Collect tags from ALL matching rules (resolve dynamic expressions)
                resolved_tags = self._resolve_tags(rule, transaction, variables, data_sources)
                for tag in resolved_tags:
                    if tag not in all_tags:
                        all_tags.add(tag)
                        tag_sources[tag] = {'rule': rule.name, 'pattern': rule.match_expr}
                result.tag_rules.append(rule)

        # Store all matching rules
        result.all_matching_rules = [r for r, _, _ in matching_rules]

        # Resolve category based on match_mode
        if self.match_mode == 'first_match':
            # First match wins (backwards compatible)
            if first_category_rule:
                rule, variables = first_category_rule
                result.matched = True
                result.merchant = rule.merchant
                result.merchant_rule = rule
                result.category = rule.category
                result.matched_rule = rule
                if rule.subcategory:
                    result.subcategory = rule.subcategory
                    result.subcategory_rule = rule

                # Evaluate extra fields for the winning rule
                if rule.fields:
                    result.extra_fields = self._evaluate_fields(
                        rule, transaction, variables, data_sources
                    )
        else:
            # most_specific mode: resolve each field independently by specificity
            if matching_rules:
                # Merchant: most specific rule that sets merchant
                merchant_rules = [(r, s, v) for r, s, v in matching_rules if r.has_merchant]
                if merchant_rules:
                    winner = max(merchant_rules, key=lambda x: x[1])
                    result.merchant = winner[0].merchant
                    result.merchant_rule = winner[0]

                # Category: most specific rule that sets category
                category_rules = [(r, s, v) for r, s, v in matching_rules if r.is_categorization_rule]
                if category_rules:
                    winner = max(category_rules, key=lambda x: x[1])
                    result.matched = True
                    result.category = winner[0].category
                    result.matched_rule = winner[0]

                    # Evaluate extra fields for the category winner
                    if winner[0].fields:
                        result.extra_fields = self._evaluate_fields(
                            winner[0], transaction, winner[2], data_sources
                        )

                # Subcategory: most specific rule that sets subcategory
                subcategory_rules = [(r, s, v) for r, s, v in matching_rules if r.has_subcategory]
                if subcategory_rules:
                    winner = max(subcategory_rules, key=lambda x: x[1])
                    result.subcategory = winner[0].subcategory
                    result.subcategory_rule = winner[0]

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


def load_merchants_file(filepath: Path, match_mode: str = 'first_match') -> MerchantEngine:
    """Load a .rules file and return configured engine.

    Args:
        filepath: Path to the .rules file
        match_mode: 'first_match' (default) or 'most_specific'
    """
    engine = MerchantEngine(match_mode=match_mode)
    engine.load_file(filepath)
    return engine


def parse_merchants(content: str, match_mode: str = 'first_match') -> MerchantEngine:
    """Parse .rules content and return configured engine.

    Args:
        content: Rules file content
        match_mode: 'first_match' (default) or 'most_specific'
    """
    engine = MerchantEngine(match_mode=match_mode)
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


def load_csv_as_engine(csv_path: Path, match_mode: str = 'first_match') -> MerchantEngine:
    """
    Load a merchant_categories.csv file as a MerchantEngine.

    This provides backwards compatibility - existing CSV files
    work seamlessly with the new engine.

    Args:
        csv_path: Path to merchant_categories.csv
        match_mode: 'first_match' (default) or 'most_specific'

    Returns:
        Configured MerchantEngine
    """
    from tally.merchant_utils import load_merchant_rules

    csv_rules = load_merchant_rules(str(csv_path))
    merchant_rules = csv_to_rules(csv_rules)

    engine = MerchantEngine(match_mode=match_mode)
    engine.rules = merchant_rules
    return engine
