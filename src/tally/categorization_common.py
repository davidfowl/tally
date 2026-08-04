"""
Shared vocabulary for the categorization review workflow.

Split out from `categorization` so the schema builder and the generator can both
depend on the rule-label format without importing each other.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional


YAML_FILENAME = 'categorization.yaml'
HINTS_FILENAME = 'categorization.hints.yaml'
SCHEMA_FILENAME = 'categorization-schema.json'

# Fields tally owns and rewrites on every generation. Everything else in a row
# belongs to the user or the agent and is carried forward verbatim.
MACHINE_FIELDS = ('id', 'key', 'source', 'date', 'merchant', 'amount')

# Fields preserved across regeneration for a row that is still unknown.
# aiNotes is named for its owner: a blank then reads as "the agent wrote nothing
# here", not as an unanswered question the user still owes.
PRESERVED_FIELDS = ('aiNotes', 'useRule', 'newRule', 'edits')


class CategorizationError(Exception):
    """Raised when categorization.yaml exists but cannot be read.

    The message is user-facing and must say what to fix. The report has already
    been written by the time this is raised; the file is never modified.
    """


@dataclass
class CategorizationStatus:
    """Counts for the one status line `tally up` prints."""
    written: bool = False
    yaml_path: Optional[str] = None
    hints_path: Optional[str] = None
    schema_path: Optional[str] = None
    unknown_count: int = 0          # rows in the file after merge
    new_count: int = 0              # unknowns not present in the previous file
    carried_forward: int = 0        # still-unknown rows that kept their answers
    dropped_count: int = 0          # previously-unknown rows now matching a rule
    answered_still_unknown: int = 0 # carried rows that had an answer = failed apply
    awaiting_review: int = 0        # Phase 2 placeholder; always 0 in Phase 1


def format_rule_label(name, category, subcategory, tags=None):
    """Render a rule as the single-line label used in `useRule` values.

    Format: ``[Name] Category / Subcategory | tags: a, b``

    Category/subcategory are omitted for tag-only rules; the tags clause is
    omitted when the rule has no tags. This exact string is both the schema enum
    entry and what the agent reads back, so the two must never diverge.
    """
    label = f"[{name}]"
    parts = [p for p in (category, subcategory) if p]
    if parts:
        label += ' ' + ' / '.join(parts)
    if tags:
        label += ' | tags: ' + ', '.join(tags)
    return label


def rule_labels(rules):
    """Sorted, de-duplicated `useRule` labels for a rule list.

    Args:
        rules: list of 7-tuples from `merchant_utils.get_all_rules`,
            ``(pattern, merchant, category, subcategory, parsed, source, tags)``

    Two rules can agree on merchant, category, subcategory and tags while
    matching different things. Their labels are then identical, and de-duplicating
    would leave `useRule` naming a rule the agent cannot identify - it would have
    to guess which one to widen. Where that happens, and only there, the match
    expression is appended to tell them apart.

    Returns:
        Sorted list of unique label strings, case-insensitive by merchant name.
    """
    by_label = defaultdict(list)
    for pattern, name, category, subcategory, _parsed, _source, tags in rules:
        by_label[format_rule_label(name, category, subcategory, tags)].append(pattern)

    labels = set()
    for label, patterns in by_label.items():
        distinct = sorted(set(patterns))
        if len(distinct) < 2:
            labels.add(label)
            continue
        for pattern in distinct:
            labels.add(f"{label} | match: {pattern}")
    return sorted(labels, key=str.lower)


def rule_facets(rules):
    """Distinct categories, subcategories, and tags across all rules.

    Used for the `edits.category` / `edits.tags` schema enums.

    Returns:
        Tuple of (categories, subcategories, tags), each a sorted list.
    """
    categories, subcategories, tags = set(), set(), set()
    for _pattern, _name, category, subcategory, _parsed, _source, rule_tags in rules:
        if category:
            categories.add(category)
        if subcategory:
            subcategories.add(subcategory)
        tags.update(t for t in (rule_tags or []) if t)
    return (
        sorted(categories, key=str.lower),
        sorted(subcategories, key=str.lower),
        sorted(tags, key=str.lower),
    )
