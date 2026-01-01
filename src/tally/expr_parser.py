"""
Expression parser using Python AST with whitelist validation.

Uses Python's ast.parse for parsing, then validates against a whitelist
of allowed node types. Custom evaluator handles lookups and functions.

Supports expressions like:
    # Section filters (merchant-level)
    category == "Food" and months >= 6
    sum(payments) > 1000
    "recurring" in tags
    stddev(payments) / avg(payments) < 0.3

    # Transaction matching (transaction-level)
    contains("NETFLIX")
    regex("UBER\\s(?!EATS)")
    amount > 100 and month == 12
"""

import ast
import re
import statistics
import warnings
from datetime import date as date_type
from typing import Any, Dict, List, Optional, Set, Callable, Union


# Cache for parsed expressions (expression string -> validated AST)
_expression_cache: Dict[str, ast.Expression] = {}

# Cache for compiled regex patterns (pattern string -> compiled Pattern)
_regex_cache: Dict[str, re.Pattern] = {}


# =============================================================================
# Whitelist of allowed AST nodes
# =============================================================================

ALLOWED_NODES = {
    # Expressions
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.IfExp,  # ternary: x if cond else y

    # Operators
    ast.And,
    ast.Or,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,  # unary minus

    # Comparisons
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,

    # Literals and names
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Store,  # For loop variable assignment in comprehensions

    # For attribute access like payment.amount (optional)
    ast.Attribute,

    # List comprehensions and generators
    ast.ListComp,       # [expr for x in iter if cond]
    ast.comprehension,  # for x in iter if cond
    ast.GeneratorExp,   # (expr for x in iter)
    ast.Subscript,      # list[0] access
    ast.Index,          # Index wrapper (Python 3.8 compat)

    # Walrus operator for variable binding
    ast.NamedExpr,      # (x := value)
}


class ExpressionError(Exception):
    """Error in expression parsing or evaluation."""
    pass


class UnsafeNodeError(ExpressionError):
    """Expression contains disallowed AST node."""
    pass


# =============================================================================
# AST Validation
# =============================================================================

def validate_ast(node: ast.AST, allowed: Set[type] = ALLOWED_NODES) -> None:
    """
    Validate that an AST only contains allowed node types.

    Raises UnsafeNodeError if disallowed nodes are found.
    """
    if type(node) not in allowed:
        raise UnsafeNodeError(f"Disallowed node type: {type(node).__name__}")

    for child in ast.iter_child_nodes(node):
        validate_ast(child, allowed)


def parse_expression(expr: str) -> ast.Expression:
    """
    Parse an expression string into a validated AST.

    Returns the AST if valid, raises ExpressionError otherwise.
    Results are cached for performance (same expression = same AST).
    """
    # Check cache first
    if expr in _expression_cache:
        return _expression_cache[expr]

    try:
        # Suppress SyntaxWarnings for invalid escape sequences in regex patterns
        # e.g., regex("UBER\s*EATS") contains \s which isn't a valid Python escape
        # but is a valid regex escape. Only suppress the specific escape sequence warning.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                'ignore',
                category=SyntaxWarning,
                message=r"invalid escape sequence"
            )
            tree = ast.parse(expr, mode='eval')
        validate_ast(tree)
        _expression_cache[expr] = tree
        return tree
    except SyntaxError as e:
        raise ExpressionError(f"Syntax error: {e.msg} at position {e.offset}")
    except UnsafeNodeError:
        raise


# =============================================================================
# Expression Evaluator
# =============================================================================

class TransactionContext:
    """
    Context for evaluating expressions against a single transaction.

    Provides primitives for transaction-level matching:
    - description: Raw transaction description (string)
    - amount: Transaction amount (float, absolute value)
    - date: Transaction date (date object)
    - month: Month number 1-12
    - year: Year (e.g., 2025)
    - day: Day of month 1-31
    - weekday: Day of week (0=Monday, 1=Tuesday, ... 6=Sunday)
    - field: Custom fields captured from CSV format string (dict)
    - source: Data source name (string)
    - location: Transaction location (string)
    - data_sources: Dict mapping source names to list of row dicts (for queries)
    """

    __slots__ = ('description', 'amount', 'date', 'variables', 'field', 'source',
                 'month', 'year', 'day', 'weekday', 'location', 'data_sources')

    # Class-level function name mapping (looked up dynamically)
    _FUNCTION_NAMES: Set[str] = {
        'contains', 'regex', 'normalized', 'anyof', 'startswith', 'fuzzy',
        'abs', 'round', 'extract', 'split', 'substring', 'trim',
        # String mutation functions (for field transforms)
        'regex_replace', 'uppercase', 'lowercase', 'strip_prefix', 'strip_suffix'
    }

    def __init__(
        self,
        description: str = "",
        amount: float = 0.0,
        date: Optional[date_type] = None,
        variables: Optional[Dict[str, Any]] = None,
        field: Optional[Dict[str, str]] = None,
        source: Optional[str] = None,
        location: Optional[str] = None,
        data_sources: Optional[Dict[str, List[Dict]]] = None,
    ):
        self.description = description
        self.amount = amount  # Preserve sign - use abs(amount) in rules if needed
        self.date = date
        self.variables = variables or {}
        self.field = field  # Custom captures from CSV format string (None if not available)
        self.source = source or ""  # Data source name (e.g., "Amex", "Chase")
        self.location = location or ""  # Transaction location (e.g., "Seattle, WA")
        self.data_sources = data_sources or {}  # Source name -> list of row dicts

        # Extract date components
        if date:
            self.month = date.month
            self.year = date.year
            self.day = date.day
            self.weekday = date.weekday()  # 0=Monday, 6=Sunday
        else:
            self.month = 0
            self.year = 0
            self.day = 0
            self.weekday = 0

    def get_function(self, name: str) -> Optional[Callable]:
        """Get a function by name, looking up method dynamically."""
        if name == 'abs':
            return abs
        if name == 'round':
            return round
        if name in self._FUNCTION_NAMES:
            return getattr(self, f'_fn_{name}', None)
        return None

    def _fn_contains(self, *args) -> bool:
        """Check if text contains pattern (case-insensitive).

        Usage:
            contains("AMAZON")           # Search description
            contains(field.memo, "REF")  # Search custom field
        """
        if len(args) == 1:
            text, pattern = self.description, args[0]
        elif len(args) == 2:
            text, pattern = args[0], args[1]
        else:
            raise ExpressionError("contains() requires 1 or 2 arguments: contains(pattern) or contains(text, pattern)")
        return pattern.upper() in text.upper()

    def _fn_regex(self, *args) -> bool:
        """Check if text matches regex pattern (case-insensitive).

        Usage:
            regex(r'UBER(?!.*EATS)')       # Search description
            regex(field.code, r'^ACH-')    # Search custom field
        """
        if len(args) == 1:
            text, pattern = self.description, args[0]
        elif len(args) == 2:
            text, pattern = args[0], args[1]
        else:
            raise ExpressionError("regex() requires 1 or 2 arguments: regex(pattern) or regex(text, pattern)")
        try:
            # Use cached compiled pattern
            if pattern not in _regex_cache:
                _regex_cache[pattern] = re.compile(pattern, re.IGNORECASE)
            return bool(_regex_cache[pattern].search(text))
        except re.error as e:
            raise ExpressionError(f"Invalid regex pattern: {e}")

    def _fn_normalized(self, *args) -> bool:
        """Check if text contains pattern after normalizing both.

        Normalization removes spaces, hyphens, apostrophes, and other punctuation.
        Useful for matching variations like 'UBER EATS' vs 'UBEREATS'.

        Usage:
            normalized("UBEREATS")                 # Search description
            normalized(field.name, "WHOLEFOODS")   # Search custom field
        """
        if len(args) == 1:
            text, pattern = self.description, args[0]
        elif len(args) == 2:
            text, pattern = args[0], args[1]
        else:
            raise ExpressionError("normalized() requires 1 or 2 arguments: normalized(pattern) or normalized(text, pattern)")

        def normalize(s: str) -> str:
            # Remove spaces, hyphens, apostrophes, periods, asterisks
            return re.sub(r"[\s\-'.*]+", '', s.upper())
        return normalize(pattern) in normalize(text)

    def _fn_anyof(self, *patterns: str) -> bool:
        """Check if description contains any of the given patterns (case-insensitive).

        Cleaner syntax for: contains("A") or contains("B") or contains("C")
        Note: This function only works on description (not custom fields).
        """
        desc_upper = self.description.upper()
        return any(p.upper() in desc_upper for p in patterns)

    def _fn_startswith(self, *args) -> bool:
        """Check if text starts with pattern (case-insensitive).

        Useful for prefix matching without catching mid-string matches.

        Usage:
            startswith("AMZN")               # Check description
            startswith(field.vendor, "COST") # Check custom field
        """
        if len(args) == 1:
            text, pattern = self.description, args[0]
        elif len(args) == 2:
            text, pattern = args[0], args[1]
        else:
            raise ExpressionError("startswith() requires 1 or 2 arguments: startswith(pattern) or startswith(text, pattern)")
        return text.upper().startswith(pattern.upper())

    def _fn_fuzzy(self, *args) -> bool:
        """Check if text is similar to pattern using fuzzy matching.

        Useful for catching typos like 'MARKEPLACE' vs 'MARKETPLACE'.
        Threshold is similarity ratio (0.0 to 1.0), default 0.80.

        Usage:
            fuzzy("STARBUCKS")                     # Search description
            fuzzy(field.vendor, "STARBCKS", 0.75)  # Search custom field with threshold
        """
        from difflib import SequenceMatcher

        # Parse arguments: fuzzy(pattern), fuzzy(pattern, threshold),
        # fuzzy(text, pattern), or fuzzy(text, pattern, threshold)
        if len(args) == 1:
            text, pattern, threshold = self.description, args[0], 0.80
        elif len(args) == 2:
            if isinstance(args[1], (int, float)):
                # fuzzy(pattern, threshold)
                text, pattern, threshold = self.description, args[0], args[1]
            else:
                # fuzzy(text, pattern)
                text, pattern, threshold = args[0], args[1], 0.80
        elif len(args) == 3:
            text, pattern, threshold = args[0], args[1], args[2]
        else:
            raise ExpressionError("fuzzy() requires 1-3 arguments: fuzzy(pattern), fuzzy(text, pattern), or fuzzy(text, pattern, threshold)")

        text_upper = text.upper()
        pattern_upper = pattern.upper()
        # Check if pattern appears as substring with fuzzy match
        # Slide a window of pattern length across text
        if len(pattern_upper) > len(text_upper):
            return SequenceMatcher(None, text_upper, pattern_upper).ratio() >= threshold
        for i in range(len(text_upper) - len(pattern_upper) + 1):
            window = text_upper[i:i + len(pattern_upper)]
            if SequenceMatcher(None, window, pattern_upper).ratio() >= threshold:
                return True
        return False

    # Extraction functions

    def _fn_extract(self, *args) -> str:
        """Extract first regex capture group from text.

        Returns empty string if no match or no capture group.

        Usage:
            extract(r'REF:(\\d+)')              # From description
            extract(field.memo, r'#(\\d+)')     # From custom field
        """
        if len(args) == 1:
            text, pattern = self.description, args[0]
        elif len(args) == 2:
            text, pattern = args[0], args[1]
        else:
            raise ExpressionError("extract() requires 1 or 2 arguments: extract(pattern) or extract(text, pattern)")

        try:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and match.groups():
                return match.group(1)
            return ''
        except re.error as e:
            raise ExpressionError(f"Invalid regex pattern in extract(): {e}")

    def _fn_split(self, *args) -> str:
        """Split text and return element at index.

        Returns empty string if index is out of bounds.

        Usage:
            split("-", 0)               # First element from description
            split(field.code, "-", 1)   # Second element from custom field
        """
        if len(args) == 2:
            text, delimiter, index = self.description, args[0], args[1]
        elif len(args) == 3:
            text, delimiter, index = args[0], args[1], args[2]
        else:
            raise ExpressionError("split() requires 2 or 3 arguments: split(delim, index) or split(text, delim, index)")

        if not isinstance(index, int):
            raise ExpressionError(f"split() index must be an integer, got: {type(index).__name__}")

        parts = text.split(delimiter)
        if 0 <= index < len(parts):
            return parts[index].strip()
        return ''

    def _fn_substring(self, *args) -> str:
        """Extract substring from text by position.

        Returns partial string if end is beyond text length.

        Usage:
            substring(0, 4)               # First 4 chars from description
            substring(field.code, 0, 3)   # First 3 chars from custom field
        """
        if len(args) == 2:
            text, start, end = self.description, args[0], args[1]
        elif len(args) == 3:
            text, start, end = args[0], args[1], args[2]
        else:
            raise ExpressionError("substring() requires 2 or 3 arguments: substring(start, end) or substring(text, start, end)")

        if not isinstance(start, int) or not isinstance(end, int):
            raise ExpressionError(f"substring() start and end must be integers")

        return text[start:end]

    def _fn_trim(self, *args) -> str:
        """Remove leading/trailing whitespace from text.

        Usage:
            trim()              # Trim description
            trim(field.memo)    # Trim custom field
        """
        if len(args) == 0:
            return self.description.strip()
        elif len(args) == 1:
            return str(args[0]).strip()
        else:
            raise ExpressionError("trim() requires 0 or 1 arguments: trim() or trim(text)")

    def _fn_regex_replace(self, *args) -> str:
        """Replace regex pattern in text.

        Usage:
            regex_replace(text, pattern, replacement)
            regex_replace(field.description, "^APLPAY\\s+", "")
        """
        if len(args) != 3:
            raise ExpressionError("regex_replace() requires 3 arguments: regex_replace(text, pattern, replacement)")
        text, pattern, replacement = str(args[0]), str(args[1]), str(args[2])
        return re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    def _fn_uppercase(self, *args) -> str:
        """Convert text to uppercase.

        Usage:
            uppercase(field.memo)
        """
        if len(args) != 1:
            raise ExpressionError("uppercase() requires 1 argument: uppercase(text)")
        return str(args[0]).upper()

    def _fn_lowercase(self, *args) -> str:
        """Convert text to lowercase.

        Usage:
            lowercase(field.memo)
        """
        if len(args) != 1:
            raise ExpressionError("lowercase() requires 1 argument: lowercase(text)")
        return str(args[0]).lower()

    def _fn_strip_prefix(self, *args) -> str:
        """Remove prefix from text if present.

        Usage:
            strip_prefix(field.description, "APLPAY ")
        """
        if len(args) != 2:
            raise ExpressionError("strip_prefix() requires 2 arguments: strip_prefix(text, prefix)")
        text, prefix = str(args[0]), str(args[1])
        if text.upper().startswith(prefix.upper()):
            return text[len(prefix):]
        return text

    def _fn_strip_suffix(self, *args) -> str:
        """Remove suffix from text if present.

        Usage:
            strip_suffix(field.description, " DES:XXX")
        """
        if len(args) != 2:
            raise ExpressionError("strip_suffix() requires 2 arguments: strip_suffix(text, suffix)")
        text, suffix = str(args[0]), str(args[1])
        if text.upper().endswith(suffix.upper()):
            return text[:-len(suffix)]
        return text

    @classmethod
    def from_transaction(
        cls,
        txn: Dict,
        variables: Optional[Dict[str, Any]] = None,
        data_sources: Optional[Dict[str, List[Dict]]] = None,
    ) -> 'TransactionContext':
        """Create context from a transaction dictionary."""
        return cls(
            description=txn.get('description', txn.get('raw_description', '')),
            amount=txn.get('amount', 0.0),
            date=txn.get('date'),
            variables=variables,
            field=txn.get('field'),
            source=txn.get('source'),
            location=txn.get('location'),
            data_sources=data_sources,
        )


class ExpressionContext:
    """
    Context for evaluating expressions.

    Provides variables, functions, and transaction data for evaluation.
    Used for merchant-level (aggregate) expressions in sections.
    """

    def __init__(
        self,
        transactions: Optional[List[Dict]] = None,
        num_months: int = 12,
        variables: Optional[Dict[str, Any]] = None,
        period_data: Optional[Dict[str, int]] = None,
    ):
        self.transactions = transactions or []
        self.num_months = num_months
        self.variables = variables or {}
        self.period_data = period_data or {}  # {'month': 12, 'year': 1, ...}

        # Built-in functions
        self.functions: Dict[str, Callable] = {
            'sum': self._fn_sum,
            'count': self._fn_count,
            'avg': self._fn_avg,
            'max': self._fn_max,
            'min': self._fn_min,
            'stddev': self._fn_stddev,
            'abs': abs,
            'round': round,
            'by': self._fn_by,
            'period': self._fn_period,
            'max_val': self._fn_max_val,
            'min_val': self._fn_min_val,
        }

    def get_function(self, name: str) -> Optional[Callable]:
        """Get a function by name."""
        return self.functions.get(name)

    def get_payments(self) -> List[float]:
        """Get all payment amounts from transactions."""
        return [t['amount'] for t in self.transactions]

    def get_months(self) -> int:
        """Get count of unique months with transactions."""
        months = set()
        for t in self.transactions:
            if 'date' in t:
                months.add(t['date'].strftime('%Y-%m'))
        return len(months) if months else 1

    def get_tags(self) -> Set[str]:
        """Get all tags from transactions."""
        tags = set()
        for t in self.transactions:
            for tag in t.get('tags', []):
                tags.add(tag.lower())
        return tags

    def get_category(self) -> str:
        """Get category (assumes all transactions have same category)."""
        if self.transactions:
            return self.transactions[0].get('category', '')
        return ''

    def get_subcategory(self) -> str:
        """Get subcategory."""
        if self.transactions:
            return self.transactions[0].get('subcategory', '')
        return ''

    def get_merchant(self) -> str:
        """Get merchant name."""
        if self.transactions:
            return self.transactions[0].get('merchant', '')
        return ''

    def get_cv(self) -> float:
        """Get coefficient of variation (stddev/avg) of monthly totals.

        Aggregates payments by month first, then computes CV of monthly totals.
        This matches the analyzer's calculation and reflects whether spending
        is consistent month-to-month (e.g., rent) vs variable (e.g., shopping).
        """
        # Aggregate payments by month
        monthly_totals = {}
        for t in self.transactions:
            if 'date' in t:
                month_key = t['date'].strftime('%Y-%m')
                monthly_totals[month_key] = monthly_totals.get(month_key, 0) + t['amount']

        if len(monthly_totals) < 2:
            return 0.0

        values = list(monthly_totals.values())
        avg = sum(values) / len(values)
        if avg == 0:
            return 0.0
        variance = sum((x - avg) ** 2 for x in values) / len(values)
        stddev = variance ** 0.5
        return stddev / avg

    def get_total(self) -> float:
        """Get total of all payments."""
        return sum(self.get_payments())

    def get_by(self, field: str) -> List[List[float]]:
        """Group payments by a field and return list of lists.

        Supported fields: month, year, day, week
        """
        field = field.lower()
        groups: Dict[str, List[float]] = {}

        for t in self.transactions:
            if 'date' not in t:
                continue

            if field == 'month':
                key = t['date'].strftime('%Y-%m')
            elif field == 'year':
                key = t['date'].strftime('%Y')
            elif field == 'day':
                key = t['date'].strftime('%Y-%m-%d')
            elif field == 'week':
                key = t['date'].strftime('%Y-W%W')
            else:
                raise ExpressionError(f"Unknown grouping field: {field}. Use: month, year, day, week")

            groups.setdefault(key, []).append(t['amount'])

        # Return groups sorted by key for consistent ordering
        return [groups[k] for k in sorted(groups.keys())]

    # Built-in functions (auto-map over nested lists)

    def _is_nested(self, values) -> bool:
        """Check if values is a list of lists."""
        return values and isinstance(values, list) and values and isinstance(values[0], list)

    def _fn_sum(self, values: List[float]):
        if self._is_nested(values):
            return [sum(group) if group else 0 for group in values]
        return sum(values) if values else 0

    def _fn_count(self, values: List[float]):
        if self._is_nested(values):
            return [len(group) for group in values]
        return len(values)

    def _fn_avg(self, values: List[float]):
        if self._is_nested(values):
            return [sum(g) / len(g) if g else 0 for g in values]
        return sum(values) / len(values) if values else 0

    def _fn_max(self, values: List[float]):
        if self._is_nested(values):
            return [max(group) if group else 0 for group in values]
        return max(values) if values else 0

    def _fn_min(self, values: List[float]):
        if self._is_nested(values):
            return [min(group) if group else 0 for group in values]
        return min(values) if values else 0

    def _fn_stddev(self, values: List[float]):
        if self._is_nested(values):
            return [statistics.stdev(g) if len(g) >= 2 else 0 for g in values]
        if len(values) < 2:
            return 0
        return statistics.stdev(values)

    def _fn_by(self, field: str) -> List[List[float]]:
        """Group payments by field. Returns list of lists."""
        return self.get_by(field)

    def _fn_period(self, field: str) -> int:
        """Get total unique periods across all transactions (global).

        This returns the analysis period length, not the merchant's active months.
        Supported fields: month, year, week, day
        """
        field = field.lower()
        if field not in self.period_data:
            # If not provided, return a sensible default
            if field == 'month':
                return 12  # Assume full year
            elif field == 'year':
                return 1
            raise ExpressionError(f"Unknown period field: {field}. Use: month, year, week, day")
        return self.period_data[field]

    def _fn_max_val(self, a: float, b: float) -> float:
        """Return the maximum of two scalar values."""
        return max(a, b)

    def _fn_min_val(self, a: float, b: float) -> float:
        """Return the minimum of two scalar values."""
        return min(a, b)


class ExpressionEvaluator:
    """
    Evaluates a parsed AST expression against a context.
    """

    def __init__(self, ctx: ExpressionContext):
        self.ctx = ctx

    def evaluate(self, node: ast.AST) -> Any:
        """Evaluate an AST node and return its value."""
        method = f'_eval_{type(node).__name__}'
        if hasattr(self, method):
            return getattr(self, method)(node)
        raise ExpressionError(f"Cannot evaluate node type: {type(node).__name__}")

    def _eval_Expression(self, node: ast.Expression) -> Any:
        return self.evaluate(node.body)

    def _eval_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def _eval_Name(self, node: ast.Name) -> Any:
        name = node.id.lower()

        # Check user-defined variables first
        if name in self.ctx.variables:
            return self.ctx.variables[name]

        # Built-in primitives
        if name == 'payments':
            return self.ctx.get_payments()
        if name == 'months':
            return self.ctx.get_months()
        if name == 'category':
            return self.ctx.get_category()
        if name == 'subcategory':
            return self.ctx.get_subcategory()
        if name == 'merchant':
            return self.ctx.get_merchant()
        if name == 'tags':
            return self.ctx.get_tags()
        if name == 'cv':
            return self.ctx.get_cv()
        if name == 'total':
            return self.ctx.get_total()
        if name == 'true':
            return True
        if name == 'false':
            return False

        raise ExpressionError(f"Unknown variable: {node.id}")

    def _eval_BoolOp(self, node: ast.BoolOp) -> bool:
        if isinstance(node.op, ast.And):
            for value in node.values:
                if not self.evaluate(value):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            for value in node.values:
                if self.evaluate(value):
                    return True
            return False
        raise ExpressionError(f"Unknown boolean operator: {type(node.op).__name__}")

    def _eval_BinOp(self, node: ast.BinOp) -> Any:
        left = self.evaluate(node.left)
        right = self.evaluate(node.right)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                return 0
            return left / right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                return 0
            return left % right

        raise ExpressionError(f"Unknown binary operator: {type(node.op).__name__}")

    def _eval_UnaryOp(self, node: ast.UnaryOp) -> Any:
        operand = self.evaluate(node.operand)

        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand

        raise ExpressionError(f"Unknown unary operator: {type(node.op).__name__}")

    def _eval_Compare(self, node: ast.Compare) -> bool:
        left = self.evaluate(node.left)

        for op, comparator in zip(node.ops, node.comparators):
            right = self.evaluate(comparator)

            if isinstance(op, ast.Eq):
                # Case-insensitive string comparison
                if isinstance(left, str) and isinstance(right, str):
                    result = left.lower() == right.lower()
                else:
                    result = left == right
            elif isinstance(op, ast.NotEq):
                if isinstance(left, str) and isinstance(right, str):
                    result = left.lower() != right.lower()
                else:
                    result = left != right
            elif isinstance(op, ast.Lt):
                result = left < right
            elif isinstance(op, ast.LtE):
                result = left <= right
            elif isinstance(op, ast.Gt):
                result = left > right
            elif isinstance(op, ast.GtE):
                result = left >= right
            elif isinstance(op, ast.In):
                # Handle "x" in tags (set membership)
                if isinstance(right, set):
                    result = left.lower() in right if isinstance(left, str) else left in right
                else:
                    result = left in right
            elif isinstance(op, ast.NotIn):
                if isinstance(right, set):
                    result = left.lower() not in right if isinstance(left, str) else left not in right
                else:
                    result = left not in right
            else:
                raise ExpressionError(f"Unknown comparison operator: {type(op).__name__}")

            if not result:
                return False
            left = right

        return True

    def _eval_Call(self, node: ast.Call) -> Any:
        # Get function name
        if isinstance(node.func, ast.Name):
            func_name = node.func.id.lower()
        else:
            raise ExpressionError("Only simple function calls are supported")

        func = self.ctx.get_function(func_name)
        if func is None:
            raise ExpressionError(f"Unknown function: {func_name}")

        # Evaluate arguments
        args = [self.evaluate(arg) for arg in node.args]

        # Call the function
        return func(*args)

    def _eval_IfExp(self, node: ast.IfExp) -> Any:
        """Evaluate ternary: x if condition else y"""
        if self.evaluate(node.test):
            return self.evaluate(node.body)
        else:
            return self.evaluate(node.orelse)


class TransactionEvaluator:
    """
    Evaluates a parsed AST expression against a transaction context.

    Handles transaction-level primitives (description, amount, date, etc.)
    and supports date comparisons with string literals.

    Supports list comprehensions for querying data sources:
        [r.item for r in amazon_orders if r.amount == txn.amount]
    """

    def __init__(self, ctx: TransactionContext):
        self.ctx = ctx
        # Scope stack for loop variables and walrus assignments
        self._scope: Dict[str, Any] = {}

    def evaluate(self, node: ast.AST) -> Any:
        """Evaluate an AST node and return its value."""
        method = f'_eval_{type(node).__name__}'
        if hasattr(self, method):
            return getattr(self, method)(node)
        raise ExpressionError(f"Cannot evaluate node type: {type(node).__name__}")

    def _eval_Expression(self, node: ast.Expression) -> Any:
        return self.evaluate(node.body)

    def _eval_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def _eval_Name(self, node: ast.Name) -> Any:
        name = node.id.lower()

        # Check loop variable scope first (for list comprehensions)
        if name in self._scope:
            return self._scope[name]

        # Check user-defined variables (from let: bindings)
        if name in self.ctx.variables:
            return self.ctx.variables[name]

        # Transaction-level primitives
        if name == 'description':
            return self.ctx.description
        if name == 'amount':
            return self.ctx.amount
        if name == 'date':
            return self.ctx.date
        if name == 'month':
            return self.ctx.month
        if name == 'year':
            return self.ctx.year
        if name == 'day':
            return self.ctx.day
        if name == 'weekday':
            return self.ctx.weekday
        if name == 'source':
            return self.ctx.source
        if name == 'true':
            return True
        if name == 'false':
            return False

        # Check data sources (for list comprehension iteration)
        if name in self.ctx.data_sources:
            return self.ctx.data_sources[name]

        raise ExpressionError(f"Unknown variable: {node.id}")

    def _eval_BoolOp(self, node: ast.BoolOp) -> bool:
        if isinstance(node.op, ast.And):
            for value in node.values:
                if not self.evaluate(value):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            for value in node.values:
                if self.evaluate(value):
                    return True
            return False
        raise ExpressionError(f"Unknown boolean operator: {type(node.op).__name__}")

    def _eval_BinOp(self, node: ast.BinOp) -> Any:
        left = self.evaluate(node.left)
        right = self.evaluate(node.right)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                return 0
            return left / right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                return 0
            return left % right

        raise ExpressionError(f"Unknown binary operator: {type(node.op).__name__}")

    def _eval_UnaryOp(self, node: ast.UnaryOp) -> Any:
        operand = self.evaluate(node.operand)

        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand

        raise ExpressionError(f"Unknown unary operator: {type(node.op).__name__}")

    def _parse_date_string(self, date_str: str) -> date_type:
        """Parse a date string in YYYY-MM-DD format."""
        try:
            return date_type.fromisoformat(date_str)
        except ValueError:
            raise ExpressionError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")

    def _eval_Compare(self, node: ast.Compare) -> bool:
        left = self.evaluate(node.left)

        for op, comparator in zip(node.ops, node.comparators):
            right = self.evaluate(comparator)

            # Handle date comparisons: date >= "2025-01-01"
            if isinstance(left, date_type) and isinstance(right, str):
                right = self._parse_date_string(right)
            elif isinstance(left, str) and isinstance(right, date_type):
                left = self._parse_date_string(left)

            if isinstance(op, ast.Eq):
                if isinstance(left, str) and isinstance(right, str):
                    result = left.lower() == right.lower()
                else:
                    result = left == right
            elif isinstance(op, ast.NotEq):
                if isinstance(left, str) and isinstance(right, str):
                    result = left.lower() != right.lower()
                else:
                    result = left != right
            elif isinstance(op, ast.Lt):
                result = left < right
            elif isinstance(op, ast.LtE):
                result = left <= right
            elif isinstance(op, ast.Gt):
                result = left > right
            elif isinstance(op, ast.GtE):
                result = left >= right
            elif isinstance(op, ast.In):
                # "NETFLIX" in description
                if isinstance(right, str):
                    result = left.upper() in right.upper() if isinstance(left, str) else left in right
                else:
                    result = left in right
            elif isinstance(op, ast.NotIn):
                if isinstance(right, str):
                    result = left.upper() not in right.upper() if isinstance(left, str) else left not in right
                else:
                    result = left not in right
            else:
                raise ExpressionError(f"Unknown comparison operator: {type(op).__name__}")

            if not result:
                return False
            left = right

        return True

    def _eval_Attribute(self, node: ast.Attribute) -> Any:
        """Handle attribute access like field.txn_type, txn.amount, or r.item."""
        # Handle txn.name access (explicit transaction context)
        if isinstance(node.value, ast.Name) and node.value.id.lower() == 'txn':
            attr_name = node.attr.lower()

            if attr_name == 'description':
                return self.ctx.description
            elif attr_name == 'amount':
                return self.ctx.amount
            elif attr_name == 'date':
                return self.ctx.date
            elif attr_name == 'source':
                return getattr(self.ctx, 'source', '')
            elif attr_name == 'location':
                return getattr(self.ctx, 'location', '')
            elif attr_name == 'month':
                return self.ctx.month
            elif attr_name == 'year':
                return self.ctx.year
            elif attr_name == 'day':
                return self.ctx.day
            elif attr_name == 'weekday':
                return self.ctx.weekday
            else:
                available = ['description', 'amount', 'date', 'source', 'location',
                             'month', 'year', 'day', 'weekday']
                raise ExpressionError(
                    f"Unknown txn attribute: txn.{node.attr}. "
                    f"Available: {', '.join(available)}"
                )

        # Handle field.name access (custom CSV fields)
        if isinstance(node.value, ast.Name) and node.value.id.lower() == 'field':
            field_name = node.attr.lower()

            # Built-in fields
            if field_name == 'description':
                return self.ctx.description
            elif field_name == 'amount':
                return self.ctx.amount
            elif field_name == 'date':
                return self.ctx.date
            elif field_name == 'source':
                return getattr(self.ctx, 'source', '')
            elif field_name == 'location':
                return getattr(self.ctx, 'location', '')

            # Custom fields from CSV
            if self.ctx.field is not None and field_name in self.ctx.field:
                return self.ctx.field[field_name]

            # Field not found
            available = ['description', 'amount', 'date', 'source', 'location']
            if self.ctx.field:
                available.extend(sorted(self.ctx.field.keys()))
            raise ExpressionError(
                f"Unknown field: field.{node.attr}. "
                f"Available fields: {', '.join(available)}"
            )

        # Handle row.attr access for list comprehension loop variables or subscript access
        # Examples: r.name, orders[0].name, merchant.item
        try:
            value = self.evaluate(node.value)
            # If value is a dict (row from data source), access the attribute as key
            if isinstance(value, dict):
                attr_name = node.attr.lower()
                if attr_name in value:
                    return value[attr_name]
                raise ExpressionError(
                    f"Unknown attribute: {node.attr}. "
                    f"Available: {', '.join(sorted(value.keys()))}"
                )
        except ExpressionError:
            pass

        raise ExpressionError(f"Unsupported attribute access: {ast.dump(node)}")

    def _eval_Call(self, node: ast.Call) -> Any:
        # Handle method calls like str.lower(), str.upper(), etc.
        if isinstance(node.func, ast.Attribute):
            obj = self.evaluate(node.func.value)
            method_name = node.func.attr.lower()

            # String methods
            if isinstance(obj, str):
                if method_name == 'lower':
                    return obj.lower()
                elif method_name == 'upper':
                    return obj.upper()
                elif method_name == 'strip':
                    return obj.strip()
                elif method_name == 'startswith':
                    if len(node.args) != 1:
                        raise ExpressionError("startswith() requires 1 argument")
                    return obj.startswith(self.evaluate(node.args[0]))
                elif method_name == 'endswith':
                    if len(node.args) != 1:
                        raise ExpressionError("endswith() requires 1 argument")
                    return obj.endswith(self.evaluate(node.args[0]))
                elif method_name == 'replace':
                    if len(node.args) != 2:
                        raise ExpressionError("replace() requires 2 arguments")
                    return obj.replace(
                        self.evaluate(node.args[0]),
                        self.evaluate(node.args[1])
                    )

            raise ExpressionError(f"Unsupported method call: {method_name}")

        # Get function name
        if isinstance(node.func, ast.Name):
            func_name = node.func.id.lower()
        else:
            raise ExpressionError("Only simple function calls are supported")

        # Special handling for exists() - catch field access errors
        if func_name == 'exists':
            if len(node.args) != 1:
                raise ExpressionError("exists() requires exactly 1 argument: exists(field.name)")
            try:
                arg_value = self.evaluate(node.args[0])
                # Field exists if it has a non-empty string value
                return bool(arg_value and str(arg_value).strip())
            except ExpressionError:
                # Field doesn't exist - return False
                return False

        # Python built-in functions for list comprehensions
        if func_name == 'len':
            if len(node.args) != 1:
                raise ExpressionError("len() requires exactly 1 argument")
            return len(self.evaluate(node.args[0]))

        if func_name == 'sum':
            if len(node.args) < 1 or len(node.args) > 2:
                raise ExpressionError("sum() requires 1 or 2 arguments")
            iterable = self.evaluate(node.args[0])
            start = self.evaluate(node.args[1]) if len(node.args) == 2 else 0
            return sum(iterable, start)

        if func_name == 'any':
            if len(node.args) != 1:
                raise ExpressionError("any() requires exactly 1 argument")
            return any(self.evaluate(node.args[0]))

        if func_name == 'all':
            if len(node.args) != 1:
                raise ExpressionError("all() requires exactly 1 argument")
            return all(self.evaluate(node.args[0]))

        if func_name == 'next':
            if len(node.args) < 1 or len(node.args) > 2:
                raise ExpressionError("next() requires 1 or 2 arguments")
            iterator = self.evaluate(node.args[0])
            if len(node.args) == 2:
                default = self.evaluate(node.args[1])
                return next(iterator, default)
            return next(iterator)

        if func_name == 'min':
            if len(node.args) == 1:
                return min(self.evaluate(node.args[0]))
            return min(self.evaluate(arg) for arg in node.args)

        if func_name == 'max':
            if len(node.args) == 1:
                return max(self.evaluate(node.args[0]))
            return max(self.evaluate(arg) for arg in node.args)

        func = self.ctx.get_function(func_name)
        if func is None:
            raise ExpressionError(f"Unknown function: {func_name}")

        # Evaluate arguments
        args = [self.evaluate(arg) for arg in node.args]

        # Call the function
        return func(*args)

    def _eval_IfExp(self, node: ast.IfExp) -> Any:
        """Evaluate ternary: x if condition else y"""
        if self.evaluate(node.test):
            return self.evaluate(node.body)
        else:
            return self.evaluate(node.orelse)

    def _eval_ListComp(self, node: ast.ListComp) -> List[Any]:
        """Evaluate [expr for x in iter if cond].

        Example: [r.item for r in amazon_orders if r.amount == txn.amount]
        """
        result = []
        self._eval_comprehension_loop(node.generators, 0, node.elt, result)
        return result

    def _eval_comprehension_loop(
        self,
        generators: List[ast.comprehension],
        index: int,
        element_expr: ast.AST,
        result: List[Any],
    ) -> None:
        """Recursively evaluate nested comprehension loops."""
        if index >= len(generators):
            # All loops done, evaluate the element expression
            result.append(self.evaluate(element_expr))
            return

        comp = generators[index]
        iterable = self.evaluate(comp.iter)

        # Get loop variable name (handle simple Name for now)
        if isinstance(comp.target, ast.Name):
            var_name = comp.target.id.lower()
        else:
            raise ExpressionError("Only simple loop variables supported (not tuple unpacking)")

        for item in iterable:
            # Set loop variable in scope
            old_value = self._scope.get(var_name)
            self._scope[var_name] = item

            # Check all if conditions
            conditions_pass = all(self.evaluate(if_clause) for if_clause in comp.ifs)

            if conditions_pass:
                # Recurse to next generator or evaluate element
                self._eval_comprehension_loop(generators, index + 1, element_expr, result)

            # Restore old scope value
            if old_value is None:
                self._scope.pop(var_name, None)
            else:
                self._scope[var_name] = old_value

    def _eval_GeneratorExp(self, node: ast.GeneratorExp) -> Any:
        """Evaluate (expr for x in iter if cond).

        Used by sum(), any(), next(), len() etc.
        Returns a generator that yields values.
        """
        def generator():
            yield from self._generator_helper(node.generators, 0, node.elt)
        return generator()

    def _generator_helper(
        self,
        generators: List[ast.comprehension],
        index: int,
        element_expr: ast.AST,
    ):
        """Recursively yield values from nested generator loops."""
        if index >= len(generators):
            yield self.evaluate(element_expr)
            return

        comp = generators[index]
        iterable = self.evaluate(comp.iter)

        if isinstance(comp.target, ast.Name):
            var_name = comp.target.id.lower()
        else:
            raise ExpressionError("Only simple loop variables supported")

        for item in iterable:
            old_value = self._scope.get(var_name)
            self._scope[var_name] = item

            conditions_pass = all(self.evaluate(if_clause) for if_clause in comp.ifs)

            if conditions_pass:
                yield from self._generator_helper(generators, index + 1, element_expr)

            if old_value is None:
                self._scope.pop(var_name, None)
            else:
                self._scope[var_name] = old_value

    def _eval_Subscript(self, node: ast.Subscript) -> Any:
        """Evaluate list[index] access."""
        value = self.evaluate(node.value)
        # Handle both Python 3.8 (Index wrapper) and 3.9+ (direct slice)
        if isinstance(node.slice, ast.Index):
            index = self.evaluate(node.slice.value)
        else:
            index = self.evaluate(node.slice)

        try:
            return value[index]
        except (IndexError, KeyError) as e:
            raise ExpressionError(f"Index error: {e}")

    def _eval_NamedExpr(self, node: ast.NamedExpr) -> Any:
        """Evaluate walrus operator (x := value).

        Example: (matches := [r for r in orders if ...]) and len(matches) > 0
        """
        value = self.evaluate(node.value)
        var_name = node.target.id.lower()
        # Store in scope for later access
        self._scope[var_name] = value
        return value


# =============================================================================
# Public API
# =============================================================================

def parse(expr: str) -> ast.Expression:
    """Parse an expression string into a validated AST."""
    return parse_expression(expr)


def evaluate(expr: str, ctx: ExpressionContext) -> Any:
    """Parse and evaluate an expression in the given context."""
    tree = parse_expression(expr)
    evaluator = ExpressionEvaluator(ctx)
    return evaluator.evaluate(tree)


def evaluate_ast(tree: ast.Expression, ctx: ExpressionContext) -> Any:
    """Evaluate a pre-parsed AST in the given context."""
    evaluator = ExpressionEvaluator(ctx)
    return evaluator.evaluate(tree)


# =============================================================================
# Convenience Functions
# =============================================================================

def evaluate_filter(
    expr: str,
    transactions: List[Dict],
    num_months: int = 12,
    variables: Optional[Dict[str, Any]] = None,
    period_data: Optional[Dict[str, int]] = None,
) -> bool:
    """
    Evaluate a filter expression against transactions.

    Returns True if the filter matches, False otherwise.
    """
    ctx = ExpressionContext(
        transactions=transactions,
        num_months=num_months,
        variables=variables or {},
        period_data=period_data,
    )
    result = evaluate(expr, ctx)
    return bool(result)


def create_context(
    transactions: Optional[List[Dict]] = None,
    num_months: int = 12,
    variables: Optional[Dict[str, Any]] = None,
    period_data: Optional[Dict[str, int]] = None,
) -> ExpressionContext:
    """Create an expression evaluation context."""
    return ExpressionContext(
        transactions=transactions,
        num_months=num_months,
        variables=variables,
        period_data=period_data,
    )


# =============================================================================
# Transaction Matching API
# =============================================================================

def evaluate_transaction(
    expr: str,
    transaction: Dict,
    variables: Optional[Dict[str, Any]] = None,
    data_sources: Optional[Dict[str, List[Dict]]] = None,
) -> Any:
    """
    Evaluate an expression against a single transaction.

    Args:
        expr: Expression string (e.g., 'contains("NETFLIX") and amount > 10')
        transaction: Transaction dict with 'description', 'amount', 'date' keys
        variables: Optional user-defined variables
        data_sources: Optional dict mapping source names to list of row dicts

    Returns:
        Result of the expression evaluation (typically bool for match expressions)
    """
    tree = parse_expression(expr)
    ctx = TransactionContext.from_transaction(transaction, variables, data_sources)
    evaluator = TransactionEvaluator(ctx)
    return evaluator.evaluate(tree)


def evaluate_transaction_ast(
    tree: ast.Expression,
    transaction: Dict,
    variables: Optional[Dict[str, Any]] = None,
    data_sources: Optional[Dict[str, List[Dict]]] = None,
) -> Any:
    """Evaluate a pre-parsed AST against a transaction."""
    ctx = TransactionContext.from_transaction(transaction, variables, data_sources)
    evaluator = TransactionEvaluator(ctx)
    return evaluator.evaluate(tree)


def matches_transaction(
    expr: str,
    transaction: Dict,
    variables: Optional[Dict[str, Any]] = None,
    data_sources: Optional[Dict[str, List[Dict]]] = None,
) -> bool:
    """
    Check if a transaction matches an expression.

    This is a convenience wrapper that ensures bool return.

    Args:
        expr: Match expression (e.g., 'contains("NETFLIX")')
        transaction: Transaction dict
        variables: Optional variables
        data_sources: Optional dict mapping source names to list of row dicts

    Returns:
        True if the transaction matches, False otherwise
    """
    return bool(evaluate_transaction(expr, transaction, variables, data_sources))


def create_transaction_context(
    description: str = "",
    amount: float = 0.0,
    date: Optional[date_type] = None,
    variables: Optional[Dict[str, Any]] = None,
) -> TransactionContext:
    """Create a transaction context for expression evaluation."""
    return TransactionContext(
        description=description,
        amount=amount,
        date=date,
        variables=variables,
    )
