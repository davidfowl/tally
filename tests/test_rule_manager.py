"""Tests for the rule manager."""

import pytest
from pathlib import Path
from tally.rule_manager import RuleManager, ValidationResult
from tally.merchant_engine import MerchantEngine


@pytest.fixture
def temp_rules_file(tmp_path):
    """Create a temporary rules file."""
    rules_file = tmp_path / "merchants.rules"
    return rules_file


@pytest.fixture
def manager(temp_rules_file):
    """Create a RuleManager with a temporary file."""
    return RuleManager(temp_rules_file)


@pytest.fixture
def manager_with_rules(temp_rules_file):
    """Create a RuleManager with some pre-existing rules."""
    content = '''[Netflix]
match: regex("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment, recurring

[Uber]
match: regex("UBER")
category: Transport
subcategory: Rideshare
'''
    temp_rules_file.write_text(content)
    return RuleManager(temp_rules_file)


class TestRuleManagerAdd:
    """Tests for adding rules."""

    def test_add_simple_rule(self, manager):
        """Add a simple rule with pattern and category."""
        rule = manager.add(
            pattern="NETFLIX",
            merchant="Netflix",
            category="Subscriptions",
        )

        assert rule.name == "Netflix"
        assert rule.match_expr == 'regex("NETFLIX")'
        assert rule.category == "Subscriptions"
        assert rule.priority == 50

    def test_add_rule_with_all_fields(self, manager):
        """Add a rule with all fields specified."""
        rule = manager.add(
            pattern="NETFLIX",
            merchant="Netflix",
            category="Subscriptions",
            subcategory="Streaming",
            tags={"entertainment", "recurring"},
            priority=100,
        )

        assert rule.name == "Netflix"
        assert rule.category == "Subscriptions"
        assert rule.subcategory == "Streaming"
        assert rule.tags == {"entertainment", "recurring"}
        assert rule.priority == 100

    def test_add_expression_pattern(self, manager):
        """Add a rule with an expression pattern."""
        rule = manager.add(
            pattern="contains('UBER') and not contains('EATS')",
            merchant="Uber",
            category="Transport",
        )

        assert rule.match_expr == "contains('UBER') and not contains('EATS')"

    def test_add_updates_existing_by_name(self, manager_with_rules):
        """Adding a rule with existing name updates it."""
        original = manager_with_rules.get("Netflix")
        assert original.category == "Subscriptions"

        updated = manager_with_rules.add(
            pattern="NETFLIX",
            merchant="Netflix",
            category="Entertainment",
        )

        assert updated.category == "Entertainment"
        assert len(manager_with_rules.rules) == 2  # Still 2 rules

    def test_add_updates_existing_by_pattern(self, manager):
        """Adding a rule with existing pattern updates it."""
        manager.add(pattern="NETFLIX", merchant="Netflix", category="Subscriptions")
        manager.add(pattern="NETFLIX", merchant="Netflix", category="Entertainment")

        assert len(manager.rules) == 1
        assert manager.rules[0].category == "Entertainment"

    def test_add_derives_name_from_pattern(self, manager):
        """Name is derived from pattern if merchant not specified."""
        rule = manager.add(pattern="COSTCO", category="Food")

        assert rule.name == "Costco"
        assert rule.merchant == "Costco"

    def test_add_derives_name_from_expression(self, manager):
        """Name is derived from expression if merchant not specified."""
        rule = manager.add(
            pattern="contains('NETFLIX')",
            category="Subscriptions",
        )

        assert rule.name == "Netflix"


class TestRuleManagerGet:
    """Tests for getting rules."""

    def test_get_by_name(self, manager_with_rules):
        """Get a rule by exact name."""
        rule = manager_with_rules.get("Netflix")
        assert rule is not None
        assert rule.name == "Netflix"

    def test_get_by_name_case_insensitive(self, manager_with_rules):
        """Get is case-insensitive."""
        rule = manager_with_rules.get("netflix")
        assert rule is not None
        assert rule.name == "Netflix"

    def test_get_not_found(self, manager_with_rules):
        """Get returns None if not found."""
        rule = manager_with_rules.get("NonExistent")
        assert rule is None

    def test_find_by_pattern(self, manager_with_rules):
        """Find a rule by pattern."""
        rule = manager_with_rules.find_by_pattern("NETFLIX")
        assert rule is not None
        assert rule.name == "Netflix"


class TestRuleManagerUpdate:
    """Tests for updating rules."""

    def test_update_category(self, manager_with_rules):
        """Update a rule's category."""
        rule = manager_with_rules.update("Netflix", category="Entertainment")

        assert rule.category == "Entertainment"
        assert rule.subcategory == "Streaming"  # Unchanged

    def test_update_add_tags(self, manager_with_rules):
        """Add tags to a rule."""
        rule = manager_with_rules.update("Netflix", add_tags={"streaming", "monthly"})

        assert "streaming" in rule.tags
        assert "monthly" in rule.tags
        assert "entertainment" in rule.tags  # Original tags kept

    def test_update_remove_tags(self, manager_with_rules):
        """Remove tags from a rule."""
        rule = manager_with_rules.update("Netflix", remove_tags={"entertainment"})

        assert "entertainment" not in rule.tags
        assert "recurring" in rule.tags

    def test_update_priority(self, manager_with_rules):
        """Update a rule's priority."""
        rule = manager_with_rules.update("Netflix", priority=100)

        assert rule.priority == 100

    def test_update_not_found(self, manager_with_rules):
        """Update raises ValueError if rule not found."""
        with pytest.raises(ValueError, match="Rule not found"):
            manager_with_rules.update("NonExistent", category="Test")


class TestRuleManagerDelete:
    """Tests for deleting rules."""

    def test_delete_by_name(self, manager_with_rules):
        """Delete a rule by name."""
        assert manager_with_rules.get("Netflix") is not None

        result = manager_with_rules.delete("Netflix")

        assert result is True
        assert manager_with_rules.get("Netflix") is None
        assert len(manager_with_rules.rules) == 1

    def test_delete_not_found(self, manager_with_rules):
        """Delete returns False if not found."""
        result = manager_with_rules.delete("NonExistent")
        assert result is False

    def test_delete_by_pattern(self, manager_with_rules):
        """Delete a rule by pattern."""
        result = manager_with_rules.delete_by_pattern("NETFLIX")

        assert result is True
        assert manager_with_rules.get("Netflix") is None


class TestRuleManagerList:
    """Tests for listing rules."""

    def test_list_all(self, manager_with_rules):
        """List all rules."""
        rules = manager_with_rules.list()
        assert len(rules) == 2

    def test_list_by_category(self, manager_with_rules):
        """List rules filtered by category."""
        rules = manager_with_rules.list(category="Subscriptions")

        assert len(rules) == 1
        assert rules[0].name == "Netflix"

    def test_list_by_category_case_insensitive(self, manager_with_rules):
        """Category filter is case-insensitive."""
        rules = manager_with_rules.list(category="subscriptions")
        assert len(rules) == 1


class TestRuleManagerImportCSV:
    """Tests for CSV import."""

    def test_import_simple_csv(self, manager):
        """Import rules from simple CSV."""
        csv_content = """NETFLIX,Netflix,Subscriptions,Streaming,entertainment
COSTCO,Costco,Food,Grocery,
"""
        imported = manager.import_csv(csv_content)

        assert len(imported) == 2
        assert manager.get("Netflix") is not None
        assert manager.get("Costco") is not None

    def test_import_with_header(self, manager):
        """Import skips header row."""
        csv_content = """Pattern,Merchant,Category,Subcategory,Tags
NETFLIX,Netflix,Subscriptions,Streaming,entertainment
"""
        imported = manager.import_csv(csv_content)

        assert len(imported) == 1
        assert imported[0].name == "Netflix"

    def test_import_with_priority(self, manager):
        """Import respects priority column."""
        csv_content = """UBER,Uber,Transport,Rideshare,,50
UBER EATS,Uber Eats,Food,Delivery,,100
"""
        imported = manager.import_csv(csv_content)

        uber = manager.get("Uber")
        uber_eats = manager.get("Uber Eats")

        assert uber.priority == 50
        assert uber_eats.priority == 100

    def test_import_with_pipe_separated_tags(self, manager):
        """Import handles pipe-separated tags."""
        csv_content = """AMAZON,Amazon,Shopping,Online,prime|ecommerce
"""
        imported = manager.import_csv(csv_content)

        assert imported[0].tags == {"prime", "ecommerce"}

    def test_import_skips_comments(self, manager):
        """Import skips comment lines."""
        csv_content = """# This is a comment
NETFLIX,Netflix,Subscriptions,Streaming,
"""
        imported = manager.import_csv(csv_content)

        assert len(imported) == 1


class TestRuleManagerPriority:
    """Tests for priority ordering."""

    def test_rules_sorted_by_priority(self, manager):
        """Rules are sorted by priority (descending)."""
        manager.add(pattern="LOW", merchant="Low", category="Test", priority=10)
        manager.add(pattern="HIGH", merchant="High", category="Test", priority=100)
        manager.add(pattern="MED", merchant="Med", category="Test", priority=50)

        rules = manager.rules
        assert rules[0].name == "High"
        assert rules[1].name == "Med"
        assert rules[2].name == "Low"

    def test_same_priority_preserves_order(self, manager):
        """Same priority rules maintain insertion order."""
        manager.add(pattern="FIRST", merchant="First", category="Test", priority=50)
        manager.add(pattern="SECOND", merchant="Second", category="Test", priority=50)
        manager.add(pattern="THIRD", merchant="Third", category="Test", priority=50)

        rules = manager.rules
        # All same priority, should be in insertion order
        names = [r.name for r in rules]
        assert names == ["First", "Second", "Third"]


class TestRuleManagerSaveLoad:
    """Tests for saving and loading rules."""

    def test_save_creates_file(self, manager, temp_rules_file):
        """Save creates the rules file."""
        manager.add(pattern="NETFLIX", merchant="Netflix", category="Subscriptions")
        manager.save()

        assert temp_rules_file.exists()

    def test_save_load_roundtrip(self, manager, temp_rules_file):
        """Rules survive save/load roundtrip."""
        manager.add(
            pattern="NETFLIX",
            merchant="Netflix",
            category="Subscriptions",
            subcategory="Streaming",
            tags={"entertainment"},
            priority=100,
        )
        manager.save()

        # Load in new manager
        manager2 = RuleManager(temp_rules_file)
        manager2.load()

        rule = manager2.get("Netflix")
        assert rule is not None
        assert rule.category == "Subscriptions"
        assert rule.subcategory == "Streaming"
        assert rule.tags == {"entertainment"}
        assert rule.priority == 100

    def test_format_rule(self, manager):
        """format_rule outputs valid .rules format."""
        manager.add(
            pattern="NETFLIX",
            merchant="Netflix",
            category="Subscriptions",
            tags={"entertainment"},
            priority=100,
        )

        formatted = manager.format_rule(manager.rules[0])

        assert "[Netflix]" in formatted
        assert "priority: 100" in formatted
        assert "match: regex(\"NETFLIX\")" in formatted
        assert "category: Subscriptions" in formatted
        assert "tags: entertainment" in formatted


class TestRuleManagerEdgeCases:
    """Tests for edge cases."""

    def test_empty_file(self, manager):
        """Handle empty rules file."""
        rules = manager.rules
        assert rules == []

    def test_add_without_category_or_tags(self, manager):
        """Rule must have category or tags."""
        # This should work - category provided
        rule = manager.add(pattern="TEST", category="Test")
        assert rule.category == "Test"

    def test_pattern_to_expr_simple(self, manager):
        """Simple patterns become regex expressions."""
        rule = manager.add(pattern="NETFLIX", category="Test")
        assert rule.match_expr == 'regex("NETFLIX")'

    def test_pattern_to_expr_expression(self, manager):
        """Expression patterns are kept as-is."""
        rule = manager.add(pattern="contains('UBER')", category="Test")
        assert rule.match_expr == "contains('UBER')"


class TestRuleManagerValidation:
    """Tests for validation features."""

    def test_extract_pattern_text_regex(self, manager):
        """Extract pattern from regex expression."""
        pattern = manager._extract_pattern_text('regex("NETFLIX")')
        assert pattern == "NETFLIX"

    def test_extract_pattern_text_contains(self, manager):
        """Extract pattern from contains expression."""
        pattern = manager._extract_pattern_text("contains('UBER')")
        assert pattern == "UBER"

    def test_extract_pattern_text_startswith(self, manager):
        """Extract pattern from startswith expression."""
        pattern = manager._extract_pattern_text('startswith("SQ *")')
        assert pattern == "SQ *"

    def test_extract_pattern_text_complex(self, manager):
        """Complex expressions return None."""
        pattern = manager._extract_pattern_text("contains('A') and contains('B')")
        # Should return first match or None
        assert pattern == "A"

    def test_patterns_might_overlap_contains(self, manager):
        """Detect when one pattern contains another."""
        assert manager._patterns_might_overlap("UBER", "UBER EATS") is True
        assert manager._patterns_might_overlap("UBER EATS", "UBER") is True

    def test_patterns_might_overlap_word_match(self, manager):
        """Detect when patterns share words."""
        assert manager._patterns_might_overlap("WHOLE FOODS", "WHOLE PAYCHECK") is True

    def test_patterns_might_overlap_different(self, manager):
        """Non-overlapping patterns."""
        assert manager._patterns_might_overlap("NETFLIX", "SPOTIFY") is False

    def test_find_shadows(self, temp_rules_file):
        """Find rules that shadow lower-priority rules."""
        content = '''[Uber]
priority: 100
match: regex("UBER")
category: Transport

[Uber Eats]
priority: 50
match: regex("UBER EATS")
category: Food
'''
        temp_rules_file.write_text(content)
        manager = RuleManager(temp_rules_file)
        manager.load()

        uber_eats = manager.get("Uber Eats")
        shadows = manager.find_shadows(uber_eats)

        assert len(shadows) == 1
        assert shadows[0].name == "Uber"

    def test_find_shadows_no_overlap(self, temp_rules_file):
        """No shadows when patterns don't overlap."""
        content = '''[Netflix]
priority: 100
match: regex("NETFLIX")
category: Subscriptions

[Spotify]
priority: 50
match: regex("SPOTIFY")
category: Subscriptions
'''
        temp_rules_file.write_text(content)
        manager = RuleManager(temp_rules_file)
        manager.load()

        spotify = manager.get("Spotify")
        shadows = manager.find_shadows(spotify)

        assert len(shadows) == 0

    def test_find_similar_unmatched(self, manager):
        """Find similar unmatched transactions."""
        transactions = [
            {'description': 'WHOLEFDS MKT 123', 'raw_description': 'WHOLEFDS MKT 123', 'category': 'Unknown', 'amount': 50.0},
            {'description': 'WHOLEFDS MKT 456', 'raw_description': 'WHOLEFDS MKT 456', 'category': 'Unknown', 'amount': 75.0},
            {'description': 'WALMART', 'raw_description': 'WALMART', 'category': 'Unknown', 'amount': 30.0},
        ]

        similar = manager.find_similar_unmatched("WHOLE FOODS", transactions)

        assert len(similar) >= 1
        # Should find the WHOLEFDS entries as similar
        descriptions = [desc for desc, _, _ in similar]
        assert any("WHOLEFDS" in d for d in descriptions)

    def test_validate_rule_with_matches(self, temp_rules_file):
        """Validate a rule that matches transactions."""
        content = '''[Netflix]
match: regex("NETFLIX")
category: Subscriptions
'''
        temp_rules_file.write_text(content)
        manager = RuleManager(temp_rules_file)
        manager.load()

        transactions = [
            {'description': 'NETFLIX', 'raw_description': 'NETFLIX', 'amount': 15.99},
            {'description': 'NETFLIX', 'raw_description': 'NETFLIX', 'amount': 15.99},
            {'description': 'HULU', 'raw_description': 'HULU', 'category': 'Unknown', 'amount': 12.99},
        ]

        rule = manager.get("Netflix")
        result = manager.validate_rule(rule, transactions)

        assert result.matches == 2
        assert result.total == 31.98
        assert len(result.samples) == 2

    def test_validate_rule_no_matches(self, temp_rules_file):
        """Validate a rule that matches nothing."""
        content = '''[Typo Netflix]
match: regex("NETFLX")
category: Subscriptions
'''
        temp_rules_file.write_text(content)
        manager = RuleManager(temp_rules_file)
        manager.load()

        transactions = [
            {'description': 'NETFLIX', 'raw_description': 'NETFLIX', 'category': 'Unknown', 'amount': 15.99},
        ]

        rule = manager.get("Typo Netflix")
        result = manager.validate_rule(rule, transactions)

        assert result.matches == 0
        # Should suggest similar
        assert len(result.similar_unmatched) >= 1
