"""
Rule manager for CRUD operations on .rules files.

Provides a high-level API for adding, updating, deleting, and listing rules
without needing to understand the .rules file format.
"""

import csv
import io
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from tally.merchant_engine import MerchantEngine, MerchantRule, MerchantParseError


@dataclass
class ValidationResult:
    """Result of validating a rule against transactions."""
    matches: int
    total: float
    samples: List[Dict]
    shadows: List[MerchantRule]
    similar_unmatched: List[Tuple[str, int, float]]  # (description, count, total)


class RuleManager:
    """
    Manages rules in a .rules file.

    Provides CRUD operations and serialization.
    """

    def __init__(self, rules_path: Path):
        self.path = rules_path
        self.engine = MerchantEngine()
        self._loaded = False

    def load(self) -> None:
        """Load rules from the file."""
        if self.path.exists():
            self.engine.load_file(self.path)
        self._loaded = True

    def save(self) -> None:
        """Save rules back to the file."""
        content = self.to_file_content()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content, encoding='utf-8')

    def _ensure_loaded(self) -> None:
        """Ensure rules are loaded before operations."""
        if not self._loaded:
            self.load()

    @property
    def rules(self) -> List[MerchantRule]:
        """Get all rules."""
        self._ensure_loaded()
        return self.engine.rules

    def add(
        self,
        pattern: str,
        merchant: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        tags: Optional[Set[str]] = None,
        priority: int = 50,
    ) -> MerchantRule:
        """
        Add a new rule or update existing one.

        If a rule with the same name or pattern exists, it will be updated.

        Args:
            pattern: Regex pattern or expression (e.g., "NETFLIX" or "contains('UBER')")
            merchant: Display name (defaults to pattern-derived name)
            category: Category for the rule
            subcategory: Subcategory for the rule
            tags: Set of tags
            priority: Rule priority (higher = checked first, default 50)

        Returns:
            The created or updated rule
        """
        self._ensure_loaded()

        # Derive rule name from merchant or pattern
        name = merchant or self._derive_name(pattern)

        # Convert simple pattern to regex expression
        match_expr = self._pattern_to_expr(pattern)

        # Check if rule with same name exists
        existing = self.get(name)
        if existing:
            return self._update_rule(existing, match_expr, category, subcategory, tags, priority)

        # Check if rule with same pattern exists
        existing = self.find_by_pattern(pattern)
        if existing:
            return self._update_rule(existing, match_expr, category, subcategory, tags, priority)

        # Create new rule
        rule = MerchantRule(
            name=name,
            match_expr=match_expr,
            merchant=merchant or name,
            category=category or "",
            subcategory=subcategory or "",
            tags=tags or set(),
            priority=priority,
            line_number=0,  # Will be updated on save
        )

        self.engine.rules.append(rule)
        # Re-sort after adding
        self.engine.rules.sort(key=lambda r: (-r.priority, r.line_number))

        return rule

    def _update_rule(
        self,
        rule: MerchantRule,
        match_expr: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        tags: Optional[Set[str]] = None,
        priority: Optional[int] = None,
    ) -> MerchantRule:
        """Update an existing rule in place."""
        idx = self.engine.rules.index(rule)

        updated = replace(
            rule,
            match_expr=match_expr if match_expr is not None else rule.match_expr,
            category=category if category is not None else rule.category,
            subcategory=subcategory if subcategory is not None else rule.subcategory,
            tags=tags if tags is not None else rule.tags,
            priority=priority if priority is not None else rule.priority,
        )

        self.engine.rules[idx] = updated
        # Re-sort after updating
        self.engine.rules.sort(key=lambda r: (-r.priority, r.line_number))

        return updated

    def get(self, name: str) -> Optional[MerchantRule]:
        """Get a rule by name (case-insensitive)."""
        self._ensure_loaded()
        name_lower = name.lower()
        for rule in self.engine.rules:
            if rule.name.lower() == name_lower:
                return rule
        return None

    def find_by_pattern(self, pattern: str) -> Optional[MerchantRule]:
        """Find a rule by its match pattern."""
        self._ensure_loaded()
        match_expr = self._pattern_to_expr(pattern)
        for rule in self.engine.rules:
            if rule.match_expr == match_expr:
                return rule
        return None

    def update(
        self,
        name: str,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        tags: Optional[Set[str]] = None,
        add_tags: Optional[Set[str]] = None,
        remove_tags: Optional[Set[str]] = None,
        priority: Optional[int] = None,
    ) -> MerchantRule:
        """
        Update an existing rule.

        Args:
            name: Rule name to update
            category: New category (None = keep existing)
            subcategory: New subcategory (None = keep existing)
            tags: Replace all tags (None = keep existing)
            add_tags: Tags to add
            remove_tags: Tags to remove
            priority: New priority (None = keep existing)

        Returns:
            The updated rule

        Raises:
            ValueError: If rule not found
        """
        self._ensure_loaded()
        rule = self.get(name)
        if not rule:
            raise ValueError(f"Rule not found: {name}")

        # Handle tag modifications
        new_tags = rule.tags.copy() if tags is None else tags.copy()
        if add_tags:
            new_tags.update(add_tags)
        if remove_tags:
            new_tags -= remove_tags

        return self._update_rule(
            rule,
            category=category,
            subcategory=subcategory,
            tags=new_tags,
            priority=priority,
        )

    def delete(self, name: str) -> bool:
        """
        Delete a rule by name.

        Returns:
            True if deleted, False if not found
        """
        self._ensure_loaded()
        rule = self.get(name)
        if rule:
            self.engine.rules.remove(rule)
            return True
        return False

    def delete_by_pattern(self, pattern: str) -> bool:
        """
        Delete a rule by pattern.

        Returns:
            True if deleted, False if not found
        """
        self._ensure_loaded()
        rule = self.find_by_pattern(pattern)
        if rule:
            self.engine.rules.remove(rule)
            return True
        return False

    def list(
        self,
        category: Optional[str] = None,
        tags: Optional[Set[str]] = None,
    ) -> List[MerchantRule]:
        """
        List rules with optional filtering.

        Args:
            category: Filter by category (case-insensitive)
            tags: Filter by tags (rule must have all specified tags)

        Returns:
            List of matching rules
        """
        self._ensure_loaded()
        rules = self.engine.rules

        if category:
            cat_lower = category.lower()
            rules = [r for r in rules if r.category.lower() == cat_lower]

        if tags:
            rules = [r for r in rules if tags <= r.tags]

        return rules

    def import_csv(self, csv_content: str) -> List[MerchantRule]:
        """
        Import rules from CSV content.

        CSV format: Pattern,Merchant,Category,Subcategory,Tags,Priority
        (Subcategory, Tags, Priority are optional)

        Returns:
            List of imported rules
        """
        self._ensure_loaded()
        imported = []

        reader = csv.reader(io.StringIO(csv_content))
        for row in reader:
            if not row or row[0].startswith('#'):
                continue

            # Skip header row
            if row[0].lower() == 'pattern':
                continue

            pattern = row[0].strip() if len(row) > 0 else ""
            merchant = row[1].strip() if len(row) > 1 else None
            category = row[2].strip() if len(row) > 2 else None
            subcategory = row[3].strip() if len(row) > 3 else None
            tags_str = row[4].strip() if len(row) > 4 else ""
            priority_str = row[5].strip() if len(row) > 5 else "50"

            if not pattern:
                continue

            # Parse tags (pipe or comma separated)
            tags = set()
            if tags_str:
                for tag in re.split(r'[|,]', tags_str):
                    tag = tag.strip()
                    if tag:
                        tags.add(tag)

            # Parse priority
            try:
                priority = int(priority_str) if priority_str else 50
            except ValueError:
                priority = 50

            rule = self.add(
                pattern=pattern,
                merchant=merchant or None,
                category=category or None,
                subcategory=subcategory or None,
                tags=tags or None,
                priority=priority,
            )
            imported.append(rule)

        return imported

    def format_rule(self, rule: MerchantRule) -> str:
        """Format a single rule as .rules file content."""
        lines = [f"[{rule.name}]"]

        if rule.priority != 50:
            lines.append(f"priority: {rule.priority}")

        # Include let bindings
        for var_name, expr in rule.let_bindings:
            lines.append(f"let: {var_name} = {expr}")

        lines.append(f"match: {rule.match_expr}")

        if rule.merchant and rule.merchant != rule.name:
            lines.append(f"merchant: {rule.merchant}")

        if rule.category:
            lines.append(f"category: {rule.category}")

        if rule.subcategory:
            lines.append(f"subcategory: {rule.subcategory}")

        # Include field expressions
        for field_name, expr in rule.fields.items():
            lines.append(f"field: {field_name} = {expr}")

        if rule.tags:
            lines.append(f"tags: {', '.join(sorted(rule.tags))}")

        return '\n'.join(lines)

    def to_file_content(self) -> str:
        """Serialize all rules to .rules file format."""
        sections = []

        # Global variables
        if self.engine.variables:
            var_lines = []
            for name, expr in self.engine.variables.items():
                var_lines.append(f"{name} = {expr}")
            sections.append('\n'.join(var_lines))

        # Global field transforms
        if self.engine.transforms:
            transform_lines = []
            for field_path, expr in self.engine.transforms:
                transform_lines.append(f"{field_path} = {expr}")
            sections.append('\n'.join(transform_lines))

        # Rules (sorted by priority desc, then original order)
        for rule in self.engine.rules:
            sections.append(self.format_rule(rule))

        return '\n\n'.join(sections) + '\n'

    def _derive_name(self, pattern: str) -> str:
        """Derive a rule name from a pattern."""
        # If it looks like an expression, use a generic name
        if '(' in pattern or ' ' in pattern:
            # Try to extract a meaningful name from the expression
            match = re.search(r'contains\(["\']([^"\']+)["\']\)', pattern)
            if match:
                return match.group(1).title()
            match = re.search(r'regex\(["\']([^"\']+)["\']\)', pattern)
            if match:
                return match.group(1).title()
            return "Rule"

        # Simple pattern - use as name with title case
        return pattern.replace('_', ' ').title()

    def _pattern_to_expr(self, pattern: str) -> str:
        """Convert a pattern to a match expression."""
        # If it's already an expression (contains function calls or operators)
        if '(' in pattern or ' and ' in pattern or ' or ' in pattern:
            return pattern

        # Simple pattern - convert to regex()
        return f'regex("{pattern}")'

    def validate_rule(
        self,
        rule: MerchantRule,
        transactions: List[Dict],
    ) -> ValidationResult:
        """
        Validate a rule against transactions.

        Tests the rule to see how many transactions it matches and
        detects potential issues like shadowing.

        Args:
            rule: The rule to validate
            transactions: List of transaction dicts with 'description', 'amount', etc.

        Returns:
            ValidationResult with match count, total spend, shadows, and similar unmatched
        """
        matches = []
        pattern_text = self._extract_pattern_text(rule.match_expr)

        # Test rule against each transaction
        for txn in transactions:
            result = self.engine.match(txn)
            if result.matched_rule and result.matched_rule.name == rule.name:
                matches.append(txn)

        # Calculate totals
        total = sum(abs(t.get('amount', 0)) for t in matches)
        samples = matches[:3]

        # Find shadows (higher priority rules that might match same transactions)
        shadows = self.find_shadows(rule)

        # If no matches, find similar unmatched descriptions
        similar = []
        if not matches and pattern_text:
            similar = self.find_similar_unmatched(pattern_text, transactions)

        return ValidationResult(
            matches=len(matches),
            total=round(total, 2),
            samples=samples,
            shadows=shadows,
            similar_unmatched=similar,
        )

    def find_shadows(self, rule: MerchantRule) -> List[MerchantRule]:
        """
        Find rules that would shadow (take priority over) this rule.

        A rule shadows another if:
        1. It has higher priority
        2. Its pattern might overlap

        Returns:
            List of rules that could shadow this one
        """
        self._ensure_loaded()
        shadows = []

        rule_pattern = self._extract_pattern_text(rule.match_expr)
        if not rule_pattern:
            return shadows

        for other in self.engine.rules:
            if other.name == rule.name:
                continue
            if other.priority <= rule.priority:
                continue

            # Check if patterns might overlap
            other_pattern = self._extract_pattern_text(other.match_expr)
            if other_pattern and self._patterns_might_overlap(rule_pattern, other_pattern):
                shadows.append(other)

        return shadows

    def find_similar_unmatched(
        self,
        pattern: str,
        transactions: List[Dict],
        threshold: float = 0.3,
        limit: int = 3,
    ) -> List[Tuple[str, int, float]]:
        """
        Find unmatched transactions with descriptions similar to the pattern.

        Useful when a rule matches 0 transactions - suggests what they might
        have meant to match.

        Args:
            pattern: The pattern text to compare against
            transactions: All transactions
            threshold: Minimum similarity ratio (0-1)
            limit: Max number of suggestions to return

        Returns:
            List of (description, count, total) tuples for similar unmatched descriptions
        """
        # Group unmatched transactions by description
        from collections import defaultdict
        unmatched_groups = defaultdict(lambda: {'count': 0, 'total': 0.0})

        for txn in transactions:
            if txn.get('category') == 'Unknown':
                desc = txn.get('raw_description', txn.get('description', ''))
                unmatched_groups[desc]['count'] += 1
                unmatched_groups[desc]['total'] += abs(txn.get('amount', 0))

        # Score each unique description by similarity
        scored = []
        pattern_upper = pattern.upper()

        for desc, stats in unmatched_groups.items():
            desc_upper = desc.upper()
            # Use SequenceMatcher for fuzzy matching
            ratio = SequenceMatcher(None, pattern_upper, desc_upper).ratio()
            # Also check if pattern is a substring
            if pattern_upper in desc_upper:
                ratio = max(ratio, 0.8)
            if ratio >= threshold:
                scored.append((ratio, desc, stats['count'], stats['total']))

        # Sort by similarity (desc) and return top matches
        scored.sort(reverse=True)
        return [(desc, count, round(total, 2)) for _, desc, count, total in scored[:limit]]

    def _extract_pattern_text(self, match_expr: str) -> Optional[str]:
        """
        Extract the text pattern from a match expression.

        E.g., 'regex("NETFLIX")' -> 'NETFLIX'
              'contains("UBER")' -> 'UBER'
        """
        # Try regex() pattern
        match = re.search(r'regex\(["\']([^"\']+)["\']\)', match_expr)
        if match:
            return match.group(1)

        # Try contains() pattern
        match = re.search(r'contains\(["\']([^"\']+)["\']\)', match_expr)
        if match:
            return match.group(1)

        # Try startswith() pattern
        match = re.search(r'startswith\(["\']([^"\']+)["\']\)', match_expr)
        if match:
            return match.group(1)

        return None

    def _patterns_might_overlap(self, pattern1: str, pattern2: str) -> bool:
        """
        Check if two patterns might match the same descriptions.

        This is a heuristic - we check if one pattern contains the other
        or if they share significant substrings.
        """
        p1_upper = pattern1.upper()
        p2_upper = pattern2.upper()

        # One contains the other
        if p1_upper in p2_upper or p2_upper in p1_upper:
            return True

        # Check for significant overlap (at least 3 chars in common)
        shorter = p1_upper if len(p1_upper) < len(p2_upper) else p2_upper
        longer = p2_upper if len(p1_upper) < len(p2_upper) else p1_upper

        # Check if words overlap
        words1 = set(p1_upper.split())
        words2 = set(p2_upper.split())
        if words1 & words2:
            return True

        return False
