"""
Snapshot tests for rule engine stability.

These tests ensure rule processing behavior doesn't change unexpectedly.
The rule engine is the CORE VALUE of tally - users carry personalized rules
across versions and depend on consistent behavior.

If a test fails, either:
1. The change is a regression - fix it
2. The change is intentional - update the snapshot AND make it opt-in

To regenerate the snapshot after intentional changes:
    rm tests/fixtures/rule_snapshot/expected_output.json
    pytest tests/test_rule_snapshots.py::TestRuleSnapshots::test_rule_processing_stability -v

Historical context: commit 952c508 broke customers by changing "first match wins"
to "most specific wins" without making it opt-in.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


class TestRuleSnapshots:
    """Snapshot tests to catch rule engine regressions."""

    FIXTURE_DIR = Path(__file__).parent / "fixtures" / "rule_snapshot"
    SNAPSHOT_FILE = FIXTURE_DIR / "expected_output.json"

    def test_rule_processing_stability(self, tmp_path):
        """Verify rule processing produces identical results.

        This test catches regressions in:
        - Merchant-to-category assignments
        - Tag assignments
        - Total calculations
        - Rule matching order/specificity
        """
        # Copy fixture to temp directory
        config_dir = tmp_path / "config"
        data_dir = tmp_path / "data"
        shutil.copytree(self.FIXTURE_DIR / "config", config_dir)
        shutil.copytree(self.FIXTURE_DIR / "data", data_dir)

        # Run tally and capture JSON output (--quiet for pure JSON)
        result = subprocess.run(
            ["uv", "run", "tally", "up", "--format", "json", "--quiet", str(config_dir)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        assert result.returncode == 0, f"tally failed: {result.stderr}"

        # Parse actual output
        actual = json.loads(result.stdout)

        # Load or create expected snapshot
        if not self.SNAPSHOT_FILE.exists():
            self._save_snapshot(actual)
            pytest.fail(
                f"Snapshot created at {self.SNAPSHOT_FILE}. "
                "Run test again to verify stability."
            )

        expected = json.loads(self.SNAPSHOT_FILE.read_text())

        # Normalize both for deterministic comparison
        actual_normalized = self._normalize_output(actual)

        # Direct JSON comparison - catches any difference including new/removed fields
        if expected != actual_normalized:
            # Pretty print both for diff
            expected_str = json.dumps(expected, indent=2, sort_keys=True)
            actual_str = json.dumps(actual_normalized, indent=2, sort_keys=True)

            # Find first difference for helpful error message
            import difflib
            diff = list(difflib.unified_diff(
                expected_str.splitlines(),
                actual_str.splitlines(),
                fromfile='expected',
                tofile='actual',
                lineterm=''
            ))

            pytest.fail(
                "Snapshot mismatch! JSON output changed.\n\n"
                + "\n".join(diff[:50])  # Show first 50 lines of diff
                + ("\n... (diff truncated)" if len(diff) > 50 else "")
            )

    def test_first_match_wins_behavior(self, tmp_path):
        """Verify first-match-wins is preserved (default behavior).

        IMPORTANT: This test verifies the CURRENT behavior where the first
        matching rule wins. This is by design for backwards compatibility.

        If this test fails, it means rule ordering behavior changed, which
        would break existing user configurations.

        The current behavior is:
        - "UBER EATS" matches the [Uber] rule (contains "UBER"), NOT [Uber Eats]
        - "AMAZON PRIME" matches the [Amazon] rule (contains "AMAZON"), NOT [Amazon Prime]

        This is because rules are evaluated in file order and first match wins.
        """
        config_dir = tmp_path / "config"
        data_dir = tmp_path / "data"
        shutil.copytree(self.FIXTURE_DIR / "config", config_dir)
        shutil.copytree(self.FIXTURE_DIR / "data", data_dir)

        result = subprocess.run(
            ["uv", "run", "tally", "up", "--format", "json", "--quiet", str(config_dir)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        assert result.returncode == 0, f"tally failed: {result.stderr}"
        actual = json.loads(result.stdout)

        # Build merchant lookup
        merchants = {m["name"]: m for m in actual.get("merchants", [])}

        # Verify first-match-wins behavior:
        # All Uber transactions (including UBER EATS) should go to Transport
        # because [Uber] rule appears before [Uber Eats] and matches first
        uber = merchants.get("Uber", {})
        assert uber.get("category") == "Transport", (
            f"Expected Uber to be Transport (first-match-wins), "
            f"got {uber.get('category')}. Rule ordering may have changed."
        )

        # All Amazon transactions (including AMAZON PRIME) should go to Shopping
        # because [Amazon] rule appears before [Amazon Prime] and matches first
        amazon = merchants.get("Amazon", {})
        assert amazon.get("category") == "Shopping", (
            f"Expected Amazon to be Shopping (first-match-wins), "
            f"got {amazon.get('category')}. Rule ordering may have changed."
        )

    def test_tag_accumulation(self, tmp_path):
        """Verify tags are accumulated from all matching, tag-only rules.

        Even with first-match-wins for category, tags should be accumulated
        from all rules that match the transaction (that don't set a category).
        """
        config_dir = tmp_path / "config"
        data_dir = tmp_path / "data"
        shutil.copytree(self.FIXTURE_DIR / "config", config_dir)
        shutil.copytree(self.FIXTURE_DIR / "data", data_dir)

        result = subprocess.run(
            ["uv", "run", "tally", "up", "--format", "json", "--quiet", str(config_dir)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        assert result.returncode == 0, f"tally failed: {result.stderr}"
        actual = json.loads(result.stdout)

        # Build merchant lookup
        merchants = {m["name"]: m for m in actual.get("merchants", [])}

        # Uber should have tags from the [Uber] rule
        uber = merchants.get("Uber", {})
        uber_tags = set(uber.get("tags", []))

        # Should have 'travel' from [Uber] rule
        assert "travel" in uber_tags, (
            f"Expected 'travel' tag from [Uber] rule, got tags: {uber_tags}"
        )

        # Should not have 'food' tag from [Uber Eats] rule (more specific category)
        assert "food" not in uber_tags, (
            f"Expected 'food' tag from [Uber Eats] rule, got tags: {uber_tags}"
        )

    def _normalize_output(self, output: dict) -> dict:
        """Normalize output for deterministic comparison.

        - Sort merchants by name for stable ordering
        - Sort all tag arrays alphabetically
        - Sort by_category by category/subcategory
        - Sort credits by merchant name
        """
        import copy
        normalized = copy.deepcopy(output)

        # Sort merchants by name and normalize their internal arrays
        if 'merchants' in normalized:
            for merchant in normalized['merchants']:
                # Sort tags array
                if 'tags' in merchant:
                    merchant['tags'] = sorted(merchant['tags'])
                # Sort pattern.tags if present
                if 'pattern' in merchant and 'tags' in merchant['pattern']:
                    merchant['pattern']['tags'] = sorted(merchant['pattern']['tags'])

            normalized['merchants'] = sorted(
                normalized['merchants'],
                key=lambda m: m.get('name', '')
            )

        # Sort by_category by category/subcategory tuple
        if 'by_category' in normalized:
            normalized['by_category'] = sorted(
                normalized['by_category'],
                key=lambda c: (c.get('category', ''), c.get('subcategory', ''))
            )

        # Sort credits by merchant name
        if 'credits' in normalized:
            normalized['credits'] = sorted(
                normalized['credits'],
                key=lambda c: c.get('merchant', '')
            )

        return normalized

    def _save_snapshot(self, output: dict):
        """Save full JSON snapshot to file."""
        normalized = self._normalize_output(output)
        self.SNAPSHOT_FILE.write_text(
            json.dumps(normalized, indent=2, sort_keys=True)
        )
