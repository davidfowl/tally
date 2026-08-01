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
        assert status.written is False

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

