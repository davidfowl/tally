"""
Review rows: surfacing, persistence, and the aggregate-safety invariant.

Per the plan, review rows persist indefinitely — months and files later — until
the file is stamped reviewComplete. Resolution is file-level: leave a row untouched
and its existing rule stands.
"""

from datetime import date, datetime

import yaml

from tally.categorization import generate_categorization
from tally.inventory import register_files

CONFIG = {'currency_format': '${amount}', '_merchants_file': 'merchants.rules'}

RULES = [
    ('contains("BESTBUY")', 'Best Buy', 'Shopping', 'Electronics', None, 'user', []),
]


def make_txn(tmp_path, review=False, category='Shopping', subcategory='Electronics',
             merchant='Best Buy', desc='BESTBUY #123', amount=250.0,
             filename='q2.csv', source='Chase'):
    """A transaction as parse_generic_csv emits it, including match_info."""
    path = tmp_path.parent / 'data' / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('', encoding='utf-8')
    return {
        'date': datetime(2026, 5, 5),
        'raw_description': desc,
        'description': merchant,
        'merchant': merchant,
        'amount': amount,
        'category': category,
        'subcategory': subcategory,
        'source': source,
        'tags': [],
        'filepath': str(path),
        'match_info': {'pattern': 'contains("BESTBUY")', 'rule_name': merchant,
                       'source': 'user', 'tags': [], 'review': review},
    }


def load(cfg):
    return yaml.safe_load((cfg / 'categorization.yaml').read_text(encoding='utf-8'))


def config_dir(tmp_path):
    cfg = tmp_path / 'config'
    cfg.mkdir(exist_ok=True)
    return cfg


class TestReviewRows:

    def test_flagged_transaction_surfaces_for_review(self, tmp_path):
        cfg = config_dir(tmp_path)
        txn = make_txn(cfg, review=True)

        status = generate_categorization(CONFIG, str(cfg), [txn], RULES)

        assert status.awaiting_review == 1
        assert status.unknown_count == 0
        data = load(cfg)
        assert data['state']['totalReviews'] == 1
        assert data['state']['totalUnknowns'] == 0

        row = data['reviews'][0]
        assert row['currently'] == '[Best Buy] Shopping / Electronics'
        assert row['file'] == 'data/q2.csv'
        assert row['useRule'] is None, "untouched means the current rule stands"

    def test_unflagged_transaction_does_not_surface(self, tmp_path):
        cfg = config_dir(tmp_path)
        status = generate_categorization(
            CONFIG, str(cfg), [make_txn(cfg, review=False)], RULES)

        assert status.awaiting_review == 0
        assert status.written is False, "nothing unknown, nothing to review"

    def test_review_rows_persist_across_runs_until_stamped(self, tmp_path):
        cfg = config_dir(tmp_path)
        txn = make_txn(cfg, review=True)

        for _ in range(3):
            status = generate_categorization(CONFIG, str(cfg), [txn], RULES)
            assert status.awaiting_review == 1, "persists indefinitely"

        # Register the file, then stamp it the way the agent or user would.
        register_files(str(cfg), [(txn['filepath'], 'Chase')], today=date(2026, 8, 1))
        inv = cfg / 'inventory.yaml'
        inv.write_text(
            inv.read_text(encoding='utf-8').replace(
                '    reviewComplete: false', '    reviewComplete: true'),
            encoding='utf-8')

        status = generate_categorization(CONFIG, str(cfg), [txn], RULES)
        assert status.awaiting_review == 0, "stamping closes it"

        # The file is rewritten empty rather than left holding the closed row:
        # an agent reading a stale row would act on an answer already applied.
        # It is never deleted - it is the user's file.
        assert status.written is True
        assert status.dropped_count == 1
        data = load(cfg)
        assert data['unknowns'] == [], "explicit [], so the file can be re-read"
        assert not data.get('reviews')
        assert data['state']['totalReviews'] == 0

        # Re-reading its own output must not raise.
        status = generate_categorization(CONFIG, str(cfg), [txn], RULES)
        assert status.dropped_count == 0

    def test_unsetting_review_complete_reopens_the_file(self, tmp_path):
        """Documented workflow for when a CSV gains new rows."""
        cfg = config_dir(tmp_path)
        txn = make_txn(cfg, review=True)
        register_files(str(cfg), [(txn['filepath'], 'Chase')], today=date(2026, 8, 1))
        inv = cfg / 'inventory.yaml'
        stamped = inv.read_text(encoding='utf-8').replace(
            '    reviewComplete: false', '    reviewComplete: true')
        inv.write_text(stamped, encoding='utf-8')
        assert generate_categorization(CONFIG, str(cfg), [txn], RULES).awaiting_review == 0

        inv.write_text(stamped.replace('    reviewComplete: true', '    reviewComplete: false'), encoding='utf-8')
        assert generate_categorization(CONFIG, str(cfg), [txn], RULES).awaiting_review == 1

    def test_review_is_scoped_per_file_not_per_rule(self, tmp_path):
        """Stamping one file must not silence the same rule in another file."""
        cfg = config_dir(tmp_path)
        q2 = make_txn(cfg, review=True, filename='q2.csv')
        q3 = make_txn(cfg, review=True, filename='q3.csv', desc='BESTBUY #999')

        register_files(str(cfg), [(q2['filepath'], 'Chase')], today=date(2026, 8, 1))
        inv = cfg / 'inventory.yaml'
        inv.write_text(inv.read_text(encoding='utf-8').replace(
            '    reviewComplete: false', '    reviewComplete: true'), encoding='utf-8')

        status = generate_categorization(CONFIG, str(cfg), [q2, q3], RULES)

        assert status.awaiting_review == 1
        assert load(cfg)['reviews'][0]['file'] == 'data/q3.csv'

    def test_ids_continue_from_unknowns(self, tmp_path):
        """One id space across both lists, so "process 3" is never ambiguous."""
        cfg = config_dir(tmp_path)
        unknown = make_txn(cfg, category='Unknown', subcategory='',
                           merchant='MYSTERY', desc='MYSTERY LLC', filename='q2.csv')
        unknown['match_info']['review'] = False
        reviews = [make_txn(cfg, review=True, desc='BESTBUY #1', filename='q2.csv'),
                   make_txn(cfg, review=True, desc='BESTBUY #2', filename='q2.csv')]

        generate_categorization(CONFIG, str(cfg), [unknown] + reviews, RULES)
        data = load(cfg)

        assert [r['id'] for r in data['unknowns']] == [1]
        assert [r['id'] for r in data['reviews']] == [2, 3]

    def test_answers_on_review_rows_carry_forward(self, tmp_path):
        cfg = config_dir(tmp_path)
        txn = make_txn(cfg, review=True)
        generate_categorization(CONFIG, str(cfg), [txn], RULES)

        data = load(cfg)
        data['reviews'][0]['newRule'] = 'should be Home / Appliances'
        (cfg / 'categorization.yaml').write_text(yaml.safe_dump(data), encoding='utf-8')

        generate_categorization(CONFIG, str(cfg), [txn], RULES)
        assert load(cfg)['reviews'][0]['newRule'] == 'should be Home / Appliances'


class TestAggregateSafety:
    """reviewComplete must never gate parsing or analysis."""

    def test_stamped_files_still_feed_the_report(self, tmp_path):
        """Skipping stamped files would silently corrupt totals.

        generate_categorization must not remove, filter, or reorder the caller's
        transaction list — analyze_transactions has already consumed it, and
        `tally up` reuses the same list.
        """
        from tally.analyzer import analyze_transactions

        cfg = config_dir(tmp_path)
        txn = make_txn(cfg, review=True)
        register_files(str(cfg), [(txn['filepath'], 'Chase')], today=date(2026, 8, 1))
        inv = cfg / 'inventory.yaml'
        inv.write_text(inv.read_text(encoding='utf-8').replace(
            '    reviewComplete: false', '    reviewComplete: true'), encoding='utf-8')

        all_txns = [txn]
        before = analyze_transactions(list(all_txns))
        generate_categorization(CONFIG, str(cfg), all_txns, RULES)
        after = analyze_transactions(list(all_txns))

        assert len(all_txns) == 1, "the transaction list must not be filtered"
        assert before['spending_total'] == after['spending_total']
        assert after['spending_total'] > 0, "a stamped file still counts"


class TestReviewFlagScope:
    """review: comes from the rules that applied, not from every rule that matched."""

    def _rule(self, name, expr, review=False, category='Shopping', subcategory=''):
        from tally.merchant_engine import MerchantRule
        return MerchantRule(
            name=name, match_expr=expr, category=category,
            subcategory=subcategory, tags=[], review=review,
        )

    def test_broad_review_rule_does_not_leak_onto_a_specific_match(self):
        """The specific/general pattern in docs/guide.html, working as documented.

        A broad catch-all flagged review: also matches, but it lost the
        specificity contest and set nothing, so it has no say in whether the
        transaction needs confirming.
        """
        from tally.merchant_engine import MatchResult
        from tally.merchant_utils import _review_flag

        specific = self._rule('Amazon Books', 'contains("books")', subcategory='Books')
        broad = self._rule('Amazon', 'contains("amazon")', review=True)

        result = MatchResult(matched=True)
        result.matched_rule = specific
        result.merchant_rule = specific
        result.subcategory_rule = specific
        result.all_matching_rules = [specific, broad]
        result.tag_rules = []

        assert _review_flag(result) is False

    def test_the_winning_rule_being_flagged_still_surfaces(self):
        from tally.merchant_engine import MatchResult
        from tally.merchant_utils import _review_flag

        winner = self._rule('Amazon Books', 'contains("books")', review=True)
        result = MatchResult(matched=True)
        result.matched_rule = winner
        result.all_matching_rules = [winner]
        result.tag_rules = []

        assert _review_flag(result) is True

    def test_a_tag_contributing_rule_being_flagged_still_surfaces(self):
        """A tag rule did apply, even though it set no category."""
        from tally.merchant_engine import MatchResult
        from tally.merchant_utils import _review_flag

        categoriser = self._rule('Amazon Books', 'contains("books")')
        tagger = self._rule('Gifts', 'contains("amazon")', review=True)

        result = MatchResult(matched=True)
        result.matched_rule = categoriser
        result.all_matching_rules = [categoriser, tagger]
        result.tag_rules = [tagger]

        assert _review_flag(result) is True


class TestRowFieldValidation:
    """A misspelled answer field must not be silently dropped."""

    def test_misspelled_answer_field_is_rejected(self, tmp_path):
        from tally.categorization import generate_categorization
        from tally.categorization_common import CategorizationError
        import pytest

        cfg = config_dir(tmp_path)
        txn = make_txn(cfg, category='Unknown')
        generate_categorization(CONFIG, str(cfg), [txn], RULES)

        path = cfg / 'categorization.yaml'
        path.write_text(
            path.read_text(encoding='utf-8').replace('useRule:', 'useRules:'),
            encoding='utf-8')

        with pytest.raises(CategorizationError) as excinfo:
            generate_categorization(CONFIG, str(cfg), [txn], RULES)
        assert 'useRules' in str(excinfo.value)
        assert "did you mean 'useRule'" in str(excinfo.value)

    def test_a_review_only_field_is_allowed_in_a_review_row(self, tmp_path):
        """currently/file are legitimate in reviews and must not be rejected."""
        cfg = config_dir(tmp_path)
        txn = make_txn(cfg, review=True)
        generate_categorization(CONFIG, str(cfg), [txn], RULES)

        status = generate_categorization(CONFIG, str(cfg), [txn], RULES)
        assert status.awaiting_review == 1


class TestUnknownAndReviewAreDisjoint:
    def test_an_uncategorized_flagged_transaction_is_only_an_unknown(self, tmp_path):
        """A tag-only review: rule can match a still-uncategorized transaction.

        Listing it in both sections puts two rows under one key, and the blank
        review row can overwrite the answered unknown row on the next merge.
        """
        cfg = config_dir(tmp_path)
        txn = make_txn(cfg, review=True, category='Unknown')

        status = generate_categorization(CONFIG, str(cfg), [txn], RULES)

        assert status.unknown_count == 1
        assert status.awaiting_review == 0
        data = load(cfg)
        assert len(data['unknowns']) == 1
        assert not data.get('reviews')

    def test_an_answer_is_not_clobbered_by_a_duplicate_blank_row(self, tmp_path):
        """Belt and braces: a hand-edited file listing one key twice."""
        from tally.categorization import _load_existing

        cfg = config_dir(tmp_path)
        path = cfg / 'categorization.yaml'
        path.write_text(
            'state:\n'
            '  generated: "2026-08-04T00:00:00"\n'
            'unknowns:\n'
            '  - id: 1\n'
            '    key: "abc123"\n'
            '    useRule: "[Amazon] Shopping"\n'
            'reviews:\n'
            '  - id: 2\n'
            '    key: "abc123"\n'
            '    useRule:\n',
            encoding='utf-8')

        existing = _load_existing(str(path))
        assert existing['abc123']['useRule'] == '[Amazon] Shopping'
