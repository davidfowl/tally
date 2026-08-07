"""
Tests for the review inventory and the `review: true` rule property.

The critical invariant here is that reviewComplete is review-scoped only: it must
never gate parsing or analysis, because every transaction feeds the report's
aggregates.
"""

from datetime import date
from pathlib import Path

import pytest

from tally.inventory import (
    InventoryError,
    is_reviewed,
    load_inventory,
    register_files,
    to_relative,
)
from tally.merchant_engine import MerchantParseError, load_merchants_file


def write_rules(tmp_path, body):
    path = tmp_path / 'merchants.rules'
    path.write_text(body, encoding='utf-8')
    return path


class TestReviewProperty:
    """`review: true` is a bare boolean; the parser stays strict otherwise."""

    def test_parses_true(self, tmp_path):
        path = write_rules(tmp_path, (
            '[Best Buy]\n'
            'match: contains("BEST BUY")\n'
            'category: Shopping\n'
            'subcategory: Electronics\n'
            'review: true\n'
        ))
        rule = load_merchants_file(Path(path)).rules[0]
        assert rule.review is True

    def test_parses_false(self, tmp_path):
        path = write_rules(tmp_path, (
            '[Best Buy]\n'
            'match: contains("BEST BUY")\n'
            'category: Shopping\n'
            'review: false\n'
        ))
        assert load_merchants_file(Path(path)).rules[0].review is False

    def test_defaults_to_false_when_absent(self, tmp_path):
        path = write_rules(tmp_path, (
            '[Netflix]\n'
            'match: contains("NETFLIX")\n'
            'category: Subscriptions\n'
        ))
        assert load_merchants_file(Path(path)).rules[0].review is False

    def test_rejects_a_condition(self, tmp_path):
        """Granularity belongs in the rule's own match:, not in review:."""
        path = write_rules(tmp_path, (
            '[Best Buy]\n'
            'match: contains("BEST BUY")\n'
            'category: Shopping\n'
            'review: amount > 500\n'
        ))
        with pytest.raises(MerchantParseError) as exc:
            load_merchants_file(Path(path))
        assert 'match:' in str(exc.value), "error should point at the real fix"

    def test_unknown_properties_still_hard_fail(self, tmp_path):
        """Adding review: must not have loosened the parser."""
        path = write_rules(tmp_path, (
            '[Best Buy]\n'
            'match: contains("BEST BUY")\n'
            'category: Shopping\n'
            'reviewed: true\n'
        ))
        with pytest.raises(MerchantParseError) as exc:
            load_merchants_file(Path(path))
        assert 'Unknown property' in str(exc.value)


class TestInventory:

    def discovered(self, tmp_path, *names):
        base = tmp_path.parent
        out = []
        for name in names:
            f = base / 'data' / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text('', encoding='utf-8')
            out.append((str(f), 'Chase'))
        return out

    def test_absent_file_is_empty_not_an_error(self, tmp_path):
        assert load_inventory(str(tmp_path)) == {}

    def test_registers_new_files(self, tmp_path):
        cfg = tmp_path / 'config'
        cfg.mkdir()
        entries, added = register_files(
            str(cfg), self.discovered(cfg, 'a.csv', 'b.csv'), today=date(2026, 7, 31))

        assert added == 2
        assert (cfg / 'inventory.yaml').exists()
        paths = {e['path'] for e in entries.values()}
        assert paths == {'data/a.csv', 'data/b.csv'}
        assert all(e['registered'] == '2026-07-31' for e in entries.values())
        assert all(e['reviewComplete'] is False for e in entries.values())

    def test_registration_is_idempotent(self, tmp_path):
        cfg = tmp_path / 'config'
        cfg.mkdir()
        files = self.discovered(cfg, 'a.csv')
        register_files(str(cfg), files, today=date(2026, 7, 31))
        _, added = register_files(str(cfg), files, today=date(2026, 8, 5))

        assert added == 0

    def test_never_overwrites_a_hand_set_review_complete(self, tmp_path):
        cfg = tmp_path / 'config'
        cfg.mkdir()
        files = self.discovered(cfg, 'a.csv')
        register_files(str(cfg), files, today=date(2026, 7, 31))

        # The user marks it reviewed by hand.
        inv = cfg / 'inventory.yaml'
        inv.write_text(inv.read_text(encoding='utf-8').replace(
            '    reviewComplete: false', '    reviewComplete: true'), encoding='utf-8')

        register_files(str(cfg), files, today=date(2026, 8, 5))
        entry = list(load_inventory(str(cfg)).values())[0]
        assert entry['reviewComplete'] is True

    def test_hand_written_flag_is_honoured_and_survives_a_rewrite(self, tmp_path):
        cfg = tmp_path / 'config'
        cfg.mkdir()
        (cfg / 'inventory.yaml').write_text(
            'files:\n'
            '  - path: data/a.csv\n'
            '    source: Chase\n'
            '    registered: 2026-07-31\n'
            '    reviewComplete: true\n',
            encoding='utf-8')

        entries = load_inventory(str(cfg))
        assert is_reviewed(entries, str(cfg), str(cfg.parent / 'data' / 'a.csv'))

        # And it survives a rewrite triggered by registering something else.
        register_files(str(cfg), self.discovered(cfg, 'b.csv'), today=date(2026, 8, 5))
        reloaded = load_inventory(str(cfg))
        assert is_reviewed(reloaded, str(cfg), str(cfg.parent / 'data' / 'a.csv'))

    def test_non_boolean_flag_is_rejected(self, tmp_path):
        """A boolean has no quoted-vs-bare ambiguity, unlike the date it replaced."""
        cfg = tmp_path / 'config'
        cfg.mkdir()
        (cfg / 'inventory.yaml').write_text(
            'files:\n'
            '  - path: data/a.csv\n'
            '    reviewComplete: 2026-08-02\n',
            encoding='utf-8')

        with pytest.raises(InventoryError) as exc:
            load_inventory(str(cfg))
        assert 'true or false' in str(exc.value)

    def test_entries_are_only_ever_appended(self, tmp_path):
        """A file that has since disappeared keeps its review history."""
        cfg = tmp_path / 'config'
        cfg.mkdir()
        register_files(str(cfg), self.discovered(cfg, 'a.csv'), today=date(2026, 7, 31))
        register_files(str(cfg), self.discovered(cfg, 'b.csv'), today=date(2026, 8, 1))

        paths = {e['path'] for e in load_inventory(str(cfg)).values()}
        assert paths == {'data/a.csv', 'data/b.csv'}

    def test_registration_is_independent_of_review_file_generation(self, tmp_path):
        """`registered` must keep advancing while review generation is off.

        register_files takes no generation flag by design — run.py calls it
        unconditionally. If registration paused while the review file was
        disabled, re-enabling would backdate every file seen in the meantime to
        that day, losing the real first-seen date. Files registered while off
        simply carry reviewComplete: false and surface once review resumes.
        """
        cfg = tmp_path / 'config'
        cfg.mkdir()
        register_files(str(cfg), self.discovered(cfg, 'seen-early.csv'),
                       today=date(2026, 7, 1))
        # ... time passes with the review file switched off ...
        register_files(str(cfg), self.discovered(cfg, 'seen-later.csv'),
                       today=date(2026, 9, 1))

        entries = {e['path']: e for e in load_inventory(str(cfg)).values()}
        assert entries['data/seen-early.csv']['registered'] == '2026-07-01'
        assert entries['data/seen-later.csv']['registered'] == '2026-09-01'
        assert all(e['reviewComplete'] is False for e in entries.values()), \
            "nothing was confirmed while review was off, so all of it surfaces"

    def test_is_reviewed_treats_unregistered_as_unreviewed(self, tmp_path):
        cfg = tmp_path / 'config'
        cfg.mkdir()
        assert is_reviewed({}, str(cfg), str(cfg.parent / 'data' / 'never-seen.csv')) is False

    def test_round_trips_paths_with_spaces_and_colons(self, tmp_path):
        cfg = tmp_path / 'config'
        cfg.mkdir()
        entries, _ = register_files(
            str(cfg), self.discovered(cfg, 'my statements, Q2.csv'),
            today=date(2026, 7, 31))

        reloaded = load_inventory(str(cfg))
        assert {e['path'] for e in reloaded.values()} == \
               {e['path'] for e in entries.values()}


class TestInventoryHardFail:

    def test_malformed_yaml_names_line_and_column(self, tmp_path):
        (tmp_path / 'inventory.yaml').write_text(
            'files:\n  - path: "unterminated\n', encoding='utf-8')

        with pytest.raises(InventoryError) as exc:
            load_inventory(str(tmp_path))

        message = str(exc.value)
        assert 'line ' in message and 'column ' in message
        assert 'left untouched' in message

    def test_file_is_not_rewritten_on_failure(self, tmp_path):
        broken = 'files:\n  - path: "unterminated\n'
        path = tmp_path / 'inventory.yaml'
        path.write_text(broken, encoding='utf-8')

        with pytest.raises(InventoryError):
            register_files(str(tmp_path), [], today=date(2026, 7, 31))

        assert path.read_text(encoding='utf-8') == broken

    def test_unknown_keys_are_rejected(self, tmp_path):
        """No unknown-key tolerance — a typo could hide a lost reviewComplete."""
        (tmp_path / 'inventory.yaml').write_text(
            'files:\n  - path: data/a.csv\n    reviewdOn: 2026-08-02\n',
            encoding='utf-8')

        with pytest.raises(InventoryError) as exc:
            load_inventory(str(tmp_path))
        assert 'reviewdOn' in str(exc.value)

    def test_wrong_shape_is_rejected(self, tmp_path):
        (tmp_path / 'inventory.yaml').write_text('something: else\n', encoding='utf-8')

        with pytest.raises(InventoryError) as exc:
            load_inventory(str(tmp_path))
        assert 'files' in str(exc.value)

    def test_entry_without_a_path_is_rejected(self, tmp_path):
        (tmp_path / 'inventory.yaml').write_text(
            'files:\n  - source: Chase\n', encoding='utf-8')

        with pytest.raises(InventoryError):
            load_inventory(str(tmp_path))

