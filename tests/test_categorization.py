"""
Tests for the categorization review file generator.

Covers the Phase 1 verification list: merge semantics, identity stability,
malformed-YAML handling, and the zero-unknowns short circuit.
"""

from datetime import datetime

import pytest
import yaml

from tally.categorization import generate_categorization
from tally.categorization_common import CategorizationError
from tally.parsers import assign_transaction_keys, transaction_key


CONFIG = {'currency_format': '${amount}', '_merchants_file': 'merchants.rules'}

RULES = [
    ('contains("AMAZON")', 'Amazon', 'Shopping', 'Books', None, 'user', ['gift']),
    ('contains("VIOC")', 'Valvoline', 'Auto', 'Maintenance', None, 'user', []),
]


def make_txn(source='Chase', date='2026-05-05', amount=112.45,
             desc='AMAZON MKTPL*NB2Q31V40', category='Unknown',
             subcategory='', merchant=None, tags=None, field=None):
    """Build a transaction dict shaped like parse_generic_csv emits."""
    return {
        'date': datetime.strptime(date, '%Y-%m-%d'),
        'raw_description': desc,
        'description': merchant or desc,
        'merchant': merchant or desc,
        'amount': amount,
        'category': category,
        'subcategory': subcategory,
        'source': source,
        'tags': tags or [],
        'field': field,
        'filepath': f'data/{source}.csv',
    }


def load(path):
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def rows_of(path):
    return load(path)['unknowns']


def hints_of(tmp_path):
    """Hints live in the companion file, keyed by the same id/key as the rows."""
    data = load(tmp_path / 'categorization.hints.yaml')
    return {h['id']: h for h in data['hints']}


class TestIdentity:
    """sha1(source + date + amount + raw_description), excluding tagging/memo."""

    def test_stable_when_tagging_and_memo_change(self):
        """The agent writing CATEGORY: into the tagging column must not move a row.

        This is the whole reason identity excludes custom captures.
        """
        before = make_txn(field={'tagging': '', 'memo': ''})
        after = make_txn(field={'tagging': 'CATEGORY: Shopping / Books',
                                'memo': 'birthday gift'})
        assert transaction_key(before) == transaction_key(after)

    def test_differs_on_each_identity_component(self):
        base = make_txn()
        assert transaction_key(make_txn(source='Apple')) != transaction_key(base)
        assert transaction_key(make_txn(date='2026-05-06')) != transaction_key(base)
        assert transaction_key(make_txn(amount=1.0)) != transaction_key(base)
        assert transaction_key(make_txn(desc='OTHER')) != transaction_key(base)

    def test_identity_is_64_bits_wide(self):
        """A collision is indistinguishable from a genuine duplicate.

        At 8 hex characters the birthday bound reaches ~1% by 9,000 distinct
        transactions; assign_transaction_keys() would then ordinalize two
        unrelated rows and reattach a stored answer to the wrong one.
        """
        assert len(transaction_key(make_txn())) == 16

    def test_no_collisions_across_many_distinct_transactions(self):
        txns = [make_txn(desc=f'MERCHANT {i}', amount=float(i)) for i in range(20000)]
        assert len({transaction_key(t) for t in txns}) == len(txns)

    def test_true_duplicates_get_ordinals(self):
        txns = [make_txn(), make_txn(), make_txn()]
        assign_transaction_keys(txns)
        keys = [t['key'] for t in txns]

        assert len(set(keys)) == 3, "exact duplicates must still be distinguishable"
        base = transaction_key(make_txn())
        assert keys == [base, f'{base}#2', f'{base}#3']

    def test_ordinals_ignore_categorized_twins(self):
        """A duplicate's ordinal must not shift when its twin gets categorized.

        Two rows can share source/date/amount/description yet differ in their
        tagging column, so one may match a rule while the other does not. Keys
        are assigned over every transaction to keep the survivor's key put.
        """
        first_run = [make_txn(), make_txn()]
        assign_transaction_keys(first_run)
        survivor_key = first_run[1]['key']

        second_run = [make_txn(category='Shopping', merchant='Amazon'), make_txn()]
        assign_transaction_keys(second_run)

        assert second_run[1]['key'] == survivor_key


class TestGeneration:

    def test_zero_unknowns_writes_nothing(self, tmp_path):
        txns = [make_txn(category='Shopping', subcategory='Books', merchant='Amazon')]

        status = generate_categorization(CONFIG, str(tmp_path), txns, RULES)

        assert status.written is False
        assert not (tmp_path / 'categorization.yaml').exists()
        assert not (tmp_path / 'categorization-schema.json').exists()

    def test_writes_yaml_and_schema(self, tmp_path):
        status = generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        assert status.written is True
        assert status.unknown_count == 1
        assert status.new_count == 1
        assert (tmp_path / 'categorization.yaml').exists()
        assert (tmp_path / 'categorization-schema.json').exists()

    def test_row_shape_is_flat_with_source_as_a_field(self, tmp_path):
        """No `sources:` nesting — source is a display field on each row."""
        generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)
        data = load(tmp_path / 'categorization.yaml')

        assert 'sources' not in data
        assert set(data) == {'state', 'unknowns'}

        row = data['unknowns'][0]
        assert row['source'] == 'Chase'
        assert row['id'] == 1
        assert row['amount'] == '+$112.45'
        assert row['merchant'] == 'AMAZON MKTPL*NB2Q31V40'
        assert 'hints' not in row, "hints belong in the companion file"
        # Free-text fields carry a quote pair so it is obvious they are yours to
        # fill; useRule stays null because Ctrl+Space completes better from there.
        assert row['useRule'] is None
        assert row['aiNotes'] == ''
        assert row['newRule'] == ''
        assert row['edits'] == {'category': '', 'tags': [], 'memo': ''}

    def test_unanswered_fields_use_their_typed_empty_form(self, tmp_path):
        """The literal text matters, not just the parsed value."""
        generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)
        text = (tmp_path / 'categorization.yaml').read_text(encoding='utf-8')

        assert '    aiNotes: ""' in text
        assert '    newRule: ""' in text
        assert '      category: ""' in text
        assert '      memo: ""' in text
        assert '      tags: []' in text, "tags is an array; its empty form is []"
        assert '    useRule:\n' in text, "useRule is bare so Ctrl+Space works"
        assert '    useRule: ""' not in text

    def test_empty_tags_is_an_array_not_null(self, tmp_path):
        """tags is typed as an array; null makes the editor flag every row."""
        generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)
        text = (tmp_path / 'categorization.yaml').read_text(encoding='utf-8')

        assert '      tags: []' in text
        assert rows_of(tmp_path / 'categorization.yaml')[0]['edits']['tags'] == []

    def test_state_block_summarizes_the_run(self, tmp_path):
        txns = [make_txn(source='Apple'), make_txn(source='Chase', desc='OTHER')]
        generate_categorization(CONFIG, str(tmp_path), txns, RULES)

        state = load(tmp_path / 'categorization.yaml')['state']
        assert state['totalSources'] == 2
        assert state['totalUnknowns'] == 2
        assert state['totalReviews'] == 0
        assert state['generated']

    def test_companion_hints_file_is_written_and_correlates_by_key(self, tmp_path):
        generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        row = rows_of(tmp_path / 'categorization.yaml')[0]
        hint = hints_of(tmp_path)[row['id']]
        assert hint['key'] == row['key']
        assert hint['merchant'] == row['merchant']

    def test_negative_amount_renders_as_refund(self, tmp_path):
        generate_categorization(CONFIG, str(tmp_path), [make_txn(amount=-9.5)], RULES)
        row = rows_of(tmp_path / 'categorization.yaml')[0]

        assert row['amount'] == '-$9.50'
        assert hints_of(tmp_path)[row['id']]['refund'] is True

    def test_sorted_by_source_then_date_descending(self, tmp_path):
        txns = [
            make_txn(source='Wells', date='2026-01-01', desc='W-OLD'),
            make_txn(source='Apple', date='2026-01-01', desc='A-OLD'),
            make_txn(source='Apple', date='2026-03-01', desc='A-NEW'),
        ]
        generate_categorization(CONFIG, str(tmp_path), txns, RULES)

        got = [r['merchant'] for r in rows_of(tmp_path / 'categorization.yaml')]
        assert got == ['A-NEW', 'A-OLD', 'W-OLD']

    def test_descriptions_with_yaml_metacharacters_round_trip(self, tmp_path):
        """Raw descriptions routinely contain ':', '*', '#' and quotes."""
        nasty = 'SQ *CAFE: "THE #1" \\ PLACE'
        generate_categorization(CONFIG, str(tmp_path), [make_txn(desc=nasty)], RULES)

        assert rows_of(tmp_path / 'categorization.yaml')[0]['merchant'] == nasty


class TestHints:

    def test_hints_are_deterministic_across_runs(self, tmp_path):
        txns = [make_txn(), make_txn(amount=3.46, desc='AMAZON MKTPL*NB2Q31V40')]

        generate_categorization(CONFIG, str(tmp_path), txns, RULES)
        first = (tmp_path / 'categorization.yaml').read_text(encoding='utf-8')
        generate_categorization(CONFIG, str(tmp_path), txns, RULES)
        second = (tmp_path / 'categorization.yaml').read_text(encoding='utf-8')

        strip_ts = lambda t: [l for l in t.splitlines() if not l.startswith('generated:')]
        assert strip_ts(first) == strip_ts(second)

    def test_occurrences_and_amount_spread(self, tmp_path):
        txns = [make_txn(amount=112.45), make_txn(amount=3.46)]
        generate_categorization(CONFIG, str(tmp_path), txns, RULES)

        hints = hints_of(tmp_path)[1]
        assert hints['occurrences'] == 2
        assert hints['amountSpread'] == '$3.46 – $112.45'

    def test_prior_period_uses_an_already_categorized_twin(self, tmp_path):
        txns = [
            make_txn(desc='VIOC 1234', category='Auto', subcategory='Maintenance',
                     merchant='Valvoline', source='Apple'),
            make_txn(desc='VIOC 1234', source='Chase'),
        ]
        generate_categorization(CONFIG, str(tmp_path), txns, RULES)

        unknown = [r for r in rows_of(tmp_path / 'categorization.yaml')
                   if r['source'] == 'Chase'][0]
        hint = hints_of(tmp_path)[unknown['id']]
        assert hint['priorPeriod'] == '[Valvoline] Auto / Maintenance'

    def test_single_rule_merchant_hands_over_a_pasteable_value(self, tmp_path):
        generate_categorization(CONFIG, str(tmp_path), [make_txn(desc='valvoline')], RULES)

        nearest = hints_of(tmp_path)[1]['nearest']
        assert nearest[0] == {'merchant': 'Valvoline', 'score': 1.0, 'rules': 1,
                              'useRule': '[Valvoline] Auto / Maintenance'}

    def test_noisy_order_id_suffix_still_finds_the_merchant(self, tmp_path):
        """The dominant real-world case: 'Amazon.com*SR9ZH2VE3'.

        Whole-string similarity scores ~0.46 here, below cutoff, so matching the
        leading name fragment is what makes the hint fire at all.
        """
        generate_categorization(
            CONFIG, str(tmp_path), [make_txn(desc='Amazon.com*SR9ZH2VE3')], RULES)

        nearest = hints_of(tmp_path)[1]['nearest']
        assert nearest[0]['merchant'] == 'Amazon'
        assert nearest[0]['score'] == 1.0

    def test_scattered_character_matches_are_rejected(self, tmp_path):
        """difflib scores 'amazon' vs 'salomon' at 0.62 off 'a'+'m'+'on'.

        That cleared the 0.6 cutoff and put Salomon on every Amazon row. A match
        must share one contiguous run, not three stray fragments.
        """
        rules = RULES + [
            ('contains("SALOMON")', 'Salomon', 'Shopping', 'Outdoor', None, 'user', []),
        ]
        generate_categorization(
            CONFIG, str(tmp_path), [make_txn(desc='Amazon.com*SR9ZH2VE3')], rules)

        merchants = [n['merchant'] for n in hints_of(tmp_path)[1]['nearest']]
        assert merchants == ['Amazon']

    def test_multi_rule_merchant_reports_a_count_not_arbitrary_picks(self, tmp_path):
        """Listing 3 of 25 same-score variants would imply a false ranking."""
        rules = RULES + [
            ('contains("AMAZON")', 'Amazon', 'Home', 'Supplies', None, 'user', []),
            ('contains("AMAZON")', 'Amazon', 'Pet', 'Food', None, 'user', []),
        ]
        generate_categorization(
            CONFIG, str(tmp_path), [make_txn(desc='Amazon.com*SR9ZH2VE3')], rules)

        hint = hints_of(tmp_path)[1]['nearest'][0]
        assert hint['merchant'] == 'Amazon'
        assert hint['rules'] == 3
        assert 'useRule' not in hint, "ambiguous merchant must not suggest one variant"


class TestMerge:

    def answer(self, path, key, **fields):
        """Simulate the user editing the file in their editor."""
        data = load(path)
        for row in data['unknowns']:
            if row['key'] == key:
                row.update(fields)
        path.write_text(yaml.safe_dump(data), encoding='utf-8')

    def test_preserves_unapplied_answers_and_additional_info(self, tmp_path):
        path = tmp_path / 'categorization.yaml'
        txns = [make_txn()]
        generate_categorization(CONFIG, str(tmp_path), txns, RULES)
        key = rows_of(path)[0]['key']

        self.answer(path, key,
                    useRule='[Amazon] Shopping / Books | tags: gift',
                    newRule='also tag it as a gift',
                    aiNotes='Amazon order; pick the purchased-item category.',
                    edits={'category': 'Shopping / Books', 'tags': ['gift'], 'memo': 'bday'})

        # Row is still unknown on the next run — the apply step has not happened.
        status = generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)
        row = rows_of(path)[0]

        assert status.carried_forward == 1
        assert status.new_count == 0
        assert row['useRule'] == '[Amazon] Shopping / Books | tags: gift'
        assert row['newRule'] == 'also tag it as a gift'
        assert row['aiNotes'].startswith('Amazon order')
        assert row['edits'] == {'category': 'Shopping / Books',
                                'tags': ['gift'], 'memo': 'bday'}

    def test_answered_but_still_unknown_is_counted(self, tmp_path):
        """This count is how a failed apply reaches the user."""
        path = tmp_path / 'categorization.yaml'
        generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)
        self.answer(path, rows_of(path)[0]['key'], useRule='[Amazon] Shopping / Books')

        status = generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        assert status.answered_still_unknown == 1

    def test_unanswered_carry_forward_is_not_a_failed_apply(self, tmp_path):
        generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)
        status = generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        assert status.carried_forward == 1
        assert status.answered_still_unknown == 0

    def test_applied_rows_drop_out_and_ids_renumber(self, tmp_path):
        path = tmp_path / 'categorization.yaml'
        first = make_txn(desc='AMAZON A', source='Apple')
        second = make_txn(desc='VIOC B', source='Chase')
        generate_categorization(CONFIG, str(tmp_path), [first, second], RULES)
        assert [r['id'] for r in rows_of(path)] == [1, 2]

        # The agent applied the first row: it now matches a rule.
        applied = make_txn(desc='AMAZON A', source='Apple',
                           category='Shopping', subcategory='Books', merchant='Amazon')
        status = generate_categorization(CONFIG, str(tmp_path), [applied, second], RULES)

        rows = rows_of(path)
        assert status.dropped_count == 1
        assert [r['merchant'] for r in rows] == ['VIOC B']
        assert [r['id'] for r in rows] == [1], "ids renumber from 1 every run"

    def test_new_unknowns_append(self, tmp_path):
        path = tmp_path / 'categorization.yaml'
        existing = make_txn(desc='AMAZON A')
        generate_categorization(CONFIG, str(tmp_path), [existing], RULES)

        status = generate_categorization(
            CONFIG, str(tmp_path), [existing, make_txn(desc='NEW THING')], RULES)

        assert status.new_count == 1
        assert status.carried_forward == 1
        assert status.unknown_count == 2

    def test_answer_survives_id_renumbering(self, tmp_path):
        """Answers reattach by key, not by the ephemeral display id."""
        path = tmp_path / 'categorization.yaml'
        a = make_txn(desc='AMAZON A', source='Apple')
        b = make_txn(desc='VIOC B', source='Chase')
        generate_categorization(CONFIG, str(tmp_path), [a, b], RULES)

        b_key = [r for r in rows_of(path) if r['merchant'] == 'VIOC B'][0]['key']
        self.answer(path, b_key, useRule='[Valvoline] Auto / Maintenance')

        # 'a' gets applied, so 'b' moves from id 2 to id 1.
        applied = make_txn(desc='AMAZON A', source='Apple',
                           category='Shopping', subcategory='Books', merchant='Amazon')
        generate_categorization(CONFIG, str(tmp_path), [applied, b], RULES)

        row = rows_of(path)[0]
        assert row['id'] == 1
        assert row['key'] == b_key
        assert row['useRule'] == '[Valvoline] Auto / Maintenance'


class TestDisabledGeneration:
    """Turning generation off must not delete answers, or leave them silently stale."""

    def test_no_notice_when_nothing_was_ever_generated(self, tmp_path):
        from tally.categorization import stale_file_notice
        assert stale_file_notice(str(tmp_path)) is None

    def test_notice_names_the_file_the_date_and_what_to_do(self, tmp_path):
        from tally.categorization import stale_file_notice
        generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        notice = stale_file_notice(str(tmp_path))

        assert 'categorization.yaml' in notice
        assert 'STALE' in notice
        assert 'generate_categorization_file: false' in notice
        # Enough for an agent reading this to conclude it should not act on the file.
        assert 'Ignore its contents' in notice
        assert load(tmp_path / 'categorization.yaml')['state']['generated'] in notice

    def test_notice_never_raises_on_a_broken_file(self, tmp_path):
        """A disabled feature must not be able to fail the run."""
        from tally.categorization import stale_file_notice
        path = tmp_path / 'categorization.yaml'
        path.write_text('unknowns:\n  - useRule: "unterminated\n', encoding='utf-8')

        notice = stale_file_notice(str(tmp_path))

        assert notice and 'STALE' in notice
        assert path.read_text(encoding='utf-8').startswith('unknowns:')

    def test_notice_does_not_touch_the_file(self, tmp_path):
        from tally.categorization import stale_file_notice
        generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)
        path = tmp_path / 'categorization.yaml'
        before = path.read_text(encoding='utf-8')

        stale_file_notice(str(tmp_path))

        assert path.read_text(encoding='utf-8') == before, "reporting must be read-only"


class TestMalformedYaml:

    BROKEN = 'unknowns:\n  - id: 1\n    useRule: "unterminated\n    key: abc\n'

    def test_hard_fails_and_leaves_the_file_untouched(self, tmp_path):
        path = tmp_path / 'categorization.yaml'
        path.write_text(self.BROKEN, encoding='utf-8')

        with pytest.raises(CategorizationError):
            generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        assert path.read_text(encoding='utf-8') == self.BROKEN, \
            "never back up, move, or regenerate — the user fixes the syntax"

    def test_error_names_the_file_line_and_column(self, tmp_path):
        path = tmp_path / 'categorization.yaml'
        path.write_text(self.BROKEN, encoding='utf-8')

        with pytest.raises(CategorizationError) as exc:
            generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        message = str(exc.value)
        assert 'categorization.yaml' in message
        assert 'line ' in message and 'column ' in message
        assert 'still written' in message, "must say the report survived"

    def test_no_backup_or_sibling_file_is_created(self, tmp_path):
        path = tmp_path / 'categorization.yaml'
        path.write_text(self.BROKEN, encoding='utf-8')

        with pytest.raises(CategorizationError):
            generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        assert [p.name for p in tmp_path.iterdir()] == ['categorization.yaml']

    def test_wrong_shape_is_rejected_with_guidance(self, tmp_path):
        path = tmp_path / 'categorization.yaml'
        path.write_text('some: other file\n', encoding='utf-8')

        with pytest.raises(CategorizationError) as exc:
            generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        assert "unknowns" in str(exc.value)

    def test_empty_file_is_treated_as_no_prior_answers(self, tmp_path):
        path = tmp_path / 'categorization.yaml'
        path.write_text('', encoding='utf-8')

        status = generate_categorization(CONFIG, str(tmp_path), [make_txn()], RULES)

        assert status.written is True
        assert status.new_count == 1
