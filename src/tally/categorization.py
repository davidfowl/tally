"""
Generate the categorization review file for unknown transactions.

`tally up` writes its report, then calls generate_categorization() to emit
`config/categorization.yaml` plus a JSON Schema that drives editor autocomplete.
The user answers in an editor at their own pace; an agent reads the file back and
applies the answers by editing merchants.rules and the user's tagging column.

Tally only ever *generates* this file. It has no rules-file writer and never
reads anything an agent owns beyond carrying user-authored fields forward.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches

import yaml

from .categorization_common import (
    CategorizationError,
    CategorizationStatus,
    HINTS_FILENAME,
    PRESERVED_FIELDS,
    SCHEMA_FILENAME,
    YAML_FILENAME,
    format_rule_label,
)
from .parsers import assign_transaction_keys
from .report import format_currency_decimal

# difflib cutoff for nearest-rule hints. Matches commands/explain.py, which uses
# the same threshold for its "did you mean" suggestions.
HINT_CUTOFF = 0.6
HINT_COUNT = 3

# Shortest rule name allowed to match by verbatim containment. A 2-3 character
# name ("AAU", "Co") appears inside unrelated descriptions constantly; such rules
# still get a chance through the fuzzy branch below.
MIN_CONTAINS_LEN = 4


def _is_substantial_match(candidate, name):
    """Reject fuzzy matches built from scattered characters rather than a word.

    difflib happily scores "amazon" against "salomon" at 0.62 by matching
    'a' + 'm' + 'on' — three fragments that mean nothing. Requiring one
    contiguous run worth at least half the candidate removes that whole class of
    false positive without discarding real near-misses like "wholefds"/"whole
    foods", where the shared run is the entire first word.
    """
    longest = max(
        (block.size for block in SequenceMatcher(None, candidate, name).get_matching_blocks()),
        default=0,
    )
    return longest >= max(3, 0.5 * len(candidate))


HEADER = """\
# yaml-language-server: $schema={schema}
#
# Answer the rows below, then tell your agent to process the file.
# Editing this file IS the interface — hand-editing is expected.
#
# This file is a convenience, not a requirement. Categorizing by talking to your
# agent in prose works exactly as it always has; use whichever you prefer, or
# both.
#
# Machine-owned fields are rewritten on every run; edits to them are discarded:
#   id, key, source, date, merchant, amount
# Your fields are carried forward until the row is categorized:
#   useRule, newRule, edits, additionalInfo
#
# ─── Navigation ───
# 1. Copy "useRule: " to the clipboard.
# 2. Ctrl+F "useRule: ", then F3 to jump between items.
# 3. Paste (replacing the same text) and press Ctrl+Space for autocomplete.
#
# ─── Getting help on a row ───
# "additionalInfo" is blank until you ask for it. Say "annotate" to have your
# agent fill it in with its best guess and reasoning, then answer from there.
# Tally never writes this field and never overwrites what your agent put there.
# Scope it however you like: "annotate", "annotate 5-12", "annotate the Amazons".
#
# "id" is a display label only — it is renumbered every run, so "process 7, 9, 13"
# always means this file as it looks right now.
#
# Deterministic match data for these rows lives in {hints_file};
# it is regenerated every run and safe to delete.
"""

HINTS_HEADER = """\
# Machine-generated match data for {yaml_file} — one entry per unresolved row,
# correlated by "key" (stable) and "id" (display order, renumbered every run).
#
# Regenerated from scratch on every `tally up`. Nothing here is hand-edited, and
# deleting this file loses nothing.
#
# Computed by string comparison against your own merchants.rules. No AI, no
# network, no guessing — "score" is literal character similarity, not confidence.
#
#   nearest[].merchant  a merchant already in merchants.rules
#   nearest[].score     1.0 = the name appears verbatim in the transaction text
#   nearest[].rules     how many rules that merchant has; >1 means the category
#                       depends on what was bought, so no single answer exists
#   nearest[].useRule   present only when rules == 1, i.e. an unambiguous answer
#   occurrences         unresolved transactions sharing this exact description
#   priorPeriod         rule this exact description matched in an earlier period
#   amountSpread        min-max across those occurrences
#   refund              amount is a credit rather than a charge
"""


def _q(value):
    """Render a Python value as a YAML scalar.

    JSON is a subset of YAML, so json.dumps gives correct quoting and escaping
    for strings, numbers, booleans, lists, and dicts alike — including
    descriptions containing ':', '*', '#', or quotes.
    """
    return json.dumps(value, ensure_ascii=False)


def _is_answered(row):
    """True if the user or agent put anything actionable in this row."""
    if row.get('useRule') or row.get('newRule'):
        return True
    edits = row.get('edits')
    if isinstance(edits, dict):
        return any(v for v in edits.values())
    return False


def _load_existing(yaml_path):
    """Read the previous categorization.yaml, keyed by row key.

    Returns {} when the file is absent. Raises CategorizationError when it is
    present but unparseable — the file is never modified, moved, or regenerated,
    because forcing the user to merge two files is worse than fixing the syntax.
    """
    if not os.path.exists(yaml_path):
        return {}

    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        detail = e.problem or "could not be parsed"
        context = f"\n  {e.context}" if e.context else ""
        raise CategorizationError(
            f"{yaml_path} is not valid YAML{location}: {detail}{context}\n"
            f"\nYour report was still written. The file above was left untouched —\n"
            f"fix the syntax error and re-run 'tally up'. Nothing was lost.\n"
            f"\nMost often this is an unquoted value containing a ':' — wrap it in\n"
            f'quotes, e.g. newRule: "Amazon: books only".'
        )
    except OSError as e:
        raise CategorizationError(f"Cannot read {yaml_path}: {e}")

    if not data:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get('unknowns'), list):
        raise CategorizationError(
            f"{yaml_path} does not look like a categorization file — expected a\n"
            f"top-level 'unknowns:' list. Delete the file to regenerate it, or\n"
            f"restore the version you meant to edit."
        )

    existing = {}
    for row in data['unknowns']:
        if isinstance(row, dict) and row.get('key'):
            existing[str(row['key'])] = row
    return existing


def _build_hints(txn, unknown_txns, known_by_desc, rule_lookup, currency_format, cache):
    """Deterministic hints for one unknown transaction. No LLM, ever.

    Everything here is derived from the user's own data and rules, so the default
    path costs zero tokens and the whole feature works with no AI at all.
    """
    desc = txn.get('raw_description', txn.get('description', ''))
    siblings = unknown_txns.get(desc, [])
    amounts = [t.get('amount', 0) for t in siblings]

    hints = {}

    nearest = _nearest_rules(desc, rule_lookup, cache)
    if nearest:
        hints['nearest'] = nearest

    prior = known_by_desc.get(desc)
    if prior:
        hints['priorPeriod'] = prior

    hints['occurrences'] = len(siblings)

    if len(amounts) > 1 and min(amounts) != max(amounts):
        low = format_currency_decimal(min(amounts), currency_format)
        high = format_currency_decimal(max(amounts), currency_format)
        hints['amountSpread'] = f"{low} – {high}"

    if any(a < 0 for a in amounts):
        hints['refund'] = True

    return hints


def _match_candidates(description):
    """Reduce a raw description to the strings worth fuzzy-matching on.

    Matching the whole description is useless when a per-order suffix dominates
    it: "amazon.com*sr9zh2ve3" scores only 0.46 against the rule name "Amazon",
    below any sensible cutoff, so the single most useful hint would never fire.
    Trying the leading name-like fragment as well recovers it.
    """
    full = description.lower().strip()
    candidates = [full]

    # Leading word run, up to the first digit or separator:
    # "amazon.com*sr9zh2ve3" -> "amazon";  "vioc 1234 rochester mn" -> "vioc"
    lead = re.match(r"[a-z][a-z&' -]*", full)
    if lead:
        token = lead.group(0).strip(" -'&")
        if len(token) >= 3 and token != full:
            candidates.append(token)

    # First two words, for names that carry a store number: "whole foods mkt 123"
    words = full.split()
    if len(words) >= 2 and ' '.join(words[:2]) != full:
        candidates.append(' '.join(words[:2]))

    return candidates


def _nearest_rules(description, rule_lookup, cache):
    """Fuzzy-match a description against existing rule names.

    Follows the get_close_matches approach used by commands/explain.py rather
    than expr_parser's SequenceMatcher, which implements the fuzzy() operator and
    carries different semantics. A rule keeps its best score across candidates.
    """
    if not description or not rule_lookup:
        return []
    if description in cache:
        return cache[description]

    names = list(rule_lookup.keys())
    best = {}
    for candidate in _match_candidates(description):
        # A rule name appearing verbatim in the transaction text is the strongest
        # signal there is; no similarity heuristic beats it.
        for name in names:
            if len(name) >= MIN_CONTAINS_LEN and name in candidate:
                best[name] = 1.0

        for name in get_close_matches(candidate, names, n=HINT_COUNT, cutoff=HINT_CUTOFF):
            if name in best:
                continue
            if not _is_substantial_match(candidate, name):
                continue
            score = round(SequenceMatcher(None, candidate, name).ratio(), 2)
            if score > best.get(name, 0):
                best[name] = score

    # Deduplicate by merchant name rather than by label. A merchant like Amazon
    # can carry 25 category variants that all score identically; listing the
    # first three alphabetically would imply "Auto / Maintenance" is a leading
    # candidate when it is just first in sort order. Naming the merchant and
    # counting its variants is the honest signal — autocomplete does the rest.
    nearest = []
    for key, score in sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:HINT_COUNT]:
        name, labels = rule_lookup[key]
        hint = {'merchant': name, 'score': score, 'rules': len(labels)}
        if len(labels) == 1:
            # Exactly one variant, so hand over the value to paste into useRule.
            hint['useRule'] = labels[0]
        nearest.append(hint)

    cache[description] = nearest
    return nearest


def _build_rule_lookup(rules):
    """Map lowercased merchant name -> (display name, sorted labels).

    Matching is done case-insensitively, but hints quote the merchant name as the
    user actually wrote it in merchants.rules.
    """
    labels = defaultdict(set)
    display = {}
    for _pattern, name, category, subcategory, _parsed, _source, tags in rules:
        if not name:
            continue
        key = name.lower()
        labels[key].add(format_rule_label(name, category, subcategory, tags))
        display.setdefault(key, name)
    return {key: (display[key], sorted(values)) for key, values in labels.items()}


def _known_labels_by_description(all_txns):
    """Map raw description -> rule label, for already-categorized transactions.

    This is the "prior period" hint: the same description was categorized before,
    so the answer is probably the same again.
    """
    known = {}
    for txn in all_txns:
        if txn.get('category') == 'Unknown':
            continue
        desc = txn.get('raw_description', txn.get('description', ''))
        if desc and desc not in known:
            known[desc] = format_rule_label(
                txn.get('merchant', ''),
                txn.get('category', ''),
                txn.get('subcategory', ''),
                txn.get('tags') or [],
            )
    return known


def _render_state(state):
    """Render the collapsible `state:` summary block shared by both files."""
    return [
        "state:",
        f"  generated: {_q(state['generated'])}",
        f"  totalSources: {state['totalSources']}",
        f"  totalUnknowns: {state['totalUnknowns']}",
        f"  totalReviews: {state['totalReviews']}",
    ]


def _render(rows, state):
    """Render the file the user answers in.

    Emitted by hand rather than via yaml.safe_dump so that unanswered fields stay
    visually blank ("useRule:") instead of becoming "useRule: null", and so the
    header comment survives. Every value goes through _q(), so quoting is still
    handled by a real serializer.
    """
    out = [HEADER.format(schema=SCHEMA_FILENAME, hints_file=HINTS_FILENAME)]
    out.extend(_render_state(state))
    out.append("unknowns:")

    for row in rows:
        out.append(f"  - id: {row['id']}")
        out.append(f"    key: {_q(row['key'])}")
        out.append(f"    source: {_q(row['source'])}")
        out.append(f"    date: {_q(row['date'])}")
        out.append(f"    merchant: {_q(row['merchant'])}")
        out.append(f"    amount: {_q(row['amount'])}")
        out.append(_field_line('additionalInfo', row.get('additionalInfo'), indent=4))
        out.append(_field_line('useRule', row.get('useRule'), indent=4))
        out.append(_field_line('newRule', row.get('newRule'), indent=4))

        edits = row.get('edits') if isinstance(row.get('edits'), dict) else {}
        out.append("    edits:")
        out.append(_field_line('category', edits.get('category'), indent=6))
        # tags is typed as an array in the schema, so an omitted value must be []
        # and not null — otherwise the editor flags every untouched row.
        out.append(_field_line('tags', edits.get('tags'), indent=6, empty='[]'))
        out.append(_field_line('memo', edits.get('memo'), indent=6))

    return '\n'.join(out) + '\n'


def _render_hints(rows, state):
    """Render the machine-generated companion file.

    Split out of the answer file so that file stays short enough to scan: hints
    ran 4-8 lines per row and buried the fields the user actually fills in.
    """
    out = [HINTS_HEADER.format(yaml_file=YAML_FILENAME)]
    out.extend(_render_state(state))
    out.append("hints:")

    for row in rows:
        hints = row.get('hints') or {}
        out.append(f"  - id: {row['id']}")
        out.append(f"    key: {_q(row['key'])}")
        out.append(f"    merchant: {_q(row['merchant'])}")
        if hints.get('nearest'):
            out.append("    nearest:")
            for near in hints['nearest']:
                out.append(f"      - merchant: {_q(near['merchant'])}")
                out.append(f"        score: {near['score']}")
                out.append(f"        rules: {near['rules']}")
                if 'useRule' in near:
                    out.append(f"        useRule: {_q(near['useRule'])}")
        for field in ('priorPeriod', 'occurrences', 'amountSpread', 'refund'):
            if field in hints:
                out.append(f"    {field}: {_q(hints[field])}")

    return '\n'.join(out) + '\n'


def _field_line(name, value, indent, empty=''):
    """Emit 'name: value', or 'name:' (or a typed empty) when nothing to carry."""
    pad = ' ' * indent
    if value is None or value == '' or value == []:
        return f"{pad}{name}:{' ' + empty if empty else ''}"
    return f"{pad}{name}: {_q(value)}"


def generate_categorization(config, config_dir, all_txns, rules):
    """Merge the previous review file with the current unknowns and rewrite it.

    Called after the HTML report is written, so there is exactly one read point:
    failing fast buys nothing when the report is written regardless.

    Rows whose transactions now match a rule are dropped. Rows still unknown carry
    their answers and additionalInfo forward verbatim. New unknowns append.

    Args:
        config: loaded config dict (needs 'currency_format', '_merchants_file')
        config_dir: directory the review file is written to
        all_txns: every parsed transaction, categorized and not
        rules: 7-tuples from merchant_utils.get_all_rules

    Returns:
        CategorizationStatus. written=False when there is nothing to write.

    Raises:
        CategorizationError: the existing file could not be read. It is left
            untouched; the caller exits non-zero with the message.
    """
    status = CategorizationStatus()

    yaml_path = os.path.join(config_dir, YAML_FILENAME)
    hints_path = os.path.join(config_dir, HINTS_FILENAME)
    schema_path = os.path.join(config_dir, SCHEMA_FILENAME)
    status.yaml_path = yaml_path
    status.hints_path = hints_path
    status.schema_path = schema_path

    # Keys are assigned over every transaction, not just the unknown ones, so a
    # row's ordinal never shifts because its duplicate twin got categorized.
    assign_transaction_keys(all_txns)

    unknown_txns = [t for t in all_txns if t.get('category') == 'Unknown']
    if not unknown_txns:
        return status

    existing = _load_existing(yaml_path)
    currency_format = config.get('currency_format', '${amount}')

    by_description = defaultdict(list)
    for txn in unknown_txns:
        by_description[txn.get('raw_description', txn.get('description', ''))].append(txn)

    known_by_desc = _known_labels_by_description(all_txns)
    rule_lookup = _build_rule_lookup(rules)

    # Sort by source ascending, then date descending.
    unknown_txns.sort(key=lambda t: (str(t.get('source', '')), _negated_date(t)))

    rows = []
    current_keys = set()
    hint_cache = {}  # fuzzy matching is the expensive part; descriptions repeat
    for index, txn in enumerate(unknown_txns, start=1):
        key = txn['key']
        current_keys.add(key)
        prior = existing.get(key)

        row = {
            'id': index,
            'key': key,
            'source': str(txn.get('source', '')),
            'date': _format_date(txn.get('date')),
            'merchant': txn.get('raw_description', txn.get('description', '')),
            'amount': _format_amount(txn.get('amount', 0), currency_format),
            'hints': _build_hints(txn, by_description, known_by_desc,
                                  rule_lookup, currency_format, hint_cache),
        }

        if prior:
            status.carried_forward += 1
            for field in PRESERVED_FIELDS:
                if field in prior:
                    row[field] = prior[field]
            # An answered row that is still unknown means the apply step did not
            # take. This count is how that failure reaches the user.
            if _is_answered(prior):
                status.answered_still_unknown += 1
        else:
            status.new_count += 1

        rows.append(row)

    status.dropped_count = len(set(existing) - current_keys)
    status.unknown_count = len(rows)

    state = {
        'generated': datetime.now().replace(microsecond=0).isoformat(),
        'totalSources': len({row['source'] for row in rows}),
        'totalUnknowns': len(rows),
        'totalReviews': status.awaiting_review,  # Phase 2; always 0 for now
    }

    _write(yaml_path, _render(rows, state))
    _write(hints_path, _render_hints(rows, state))
    _write(schema_path, _build_schema_json(rules))

    status.written = True
    return status


def _build_schema_json(rules):
    from .categorization_schema import build_schema
    return json.dumps(build_schema(rules), indent=2, ensure_ascii=False) + '\n'


def _write(path, content):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)


def _negated_date(txn):
    """Sort key giving descending date order under an ascending sort."""
    date = txn.get('date')
    return -date.toordinal() if hasattr(date, 'toordinal') else 0


def _format_date(date):
    return date.strftime('%m/%d/%Y') if hasattr(date, 'strftime') else str(date or '')


def _format_amount(amount, currency_format):
    """Signed display amount, e.g. '+$112.45'. Refunds read as negative."""
    sign = '-' if amount < 0 else '+'
    return f"{sign}{format_currency_decimal(abs(amount), currency_format)}"
