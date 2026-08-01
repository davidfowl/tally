"""
Tally-owned register of the data files it has seen.

`config/inventory.yaml` records every data file tally has parsed, and whether the
user has confirmed the categorizations in it:

    files:
      - path: data/amazon-chase-visa-2026-Q2.CSV
        source: Amazon Chase Visa
        registered: 2026-07-31
        reviewComplete: true

That is the whole schema. Tally auto-registers files it discovers and only ever
appends — it never removes an entry, even for a file that has since disappeared,
because the review history is the point.

`reviewComplete` is a plain boolean rather than a date: the only question ever
asked of it is "is this done?", and a bare date is a hazard to hand-type (YAML
loads `2026-08-02` as a date object but `"2026-08-02"` as a string).

It is review-scoped ONLY. It must never gate parsing or analysis: every
transaction feeds analyze_spending's aggregates, so skipping completed files
would silently corrupt totals and monthly averages and blind `tally discover`.

The file is hand-editable by design — setting `reviewComplete` back to false
forces a file into review again after a CSV append — so it carries the same
hard-fail contract as categorization.yaml: bad YAML stops the run and the file
is never rewritten.
"""

import json
import os
from datetime import date

import yaml

from .categorization_common import CategorizationError

INVENTORY_FILENAME = 'inventory.yaml'

# The complete set of keys an entry may carry. Anything else is a typo or a
# hand-edit gone wrong, and silently ignoring it would hide a lost
# reviewComplete — the file would quietly go back to surfacing review rows.
ENTRY_KEYS = {'path', 'source', 'registered', 'reviewComplete'}


class InventoryError(CategorizationError):
    """inventory.yaml exists but cannot be used.

    Subclasses CategorizationError so callers handle both the same way: report
    the message, leave the file alone, exit non-zero.
    """


def _base_dir(config_dir):
    """Data paths are relative to the config directory's parent.

    Mirrors path_utils.resolve_data_source_paths, so entries read the same way
    the user wrote them in settings.yaml ("data/statements.csv").
    """
    return os.path.dirname(os.path.abspath(config_dir))


def to_relative(config_dir, filepath):
    """Express a data file the way settings.yaml does: relative, forward slashes.

    Falls back to the absolute path when the file lives outside the budget
    directory (a different drive, or an absolute path in settings.yaml).
    """
    absolute = os.path.abspath(filepath)
    try:
        relative = os.path.relpath(absolute, _base_dir(config_dir))
    except ValueError:
        return absolute.replace('\\', '/')
    if relative.startswith('..'):
        return absolute.replace('\\', '/')
    return relative.replace('\\', '/')


def _match_key(path):
    """Comparison key for paths. Windows file names are case-insensitive."""
    return os.path.normcase(path.replace('\\', '/'))


def load_inventory(config_dir):
    """Read inventory.yaml into an ordered dict of match-key -> entry.

    Returns an empty dict when the file does not exist yet.

    Raises:
        InventoryError: malformed YAML, wrong shape, or an unrecognized key.
    """
    path = os.path.join(config_dir, INVENTORY_FILENAME)
    if not os.path.exists(path):
        return {}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        context = f"\n  {e.context}" if e.context else ""
        raise InventoryError(
            f"{path} is not valid YAML{location}: "
            f"{e.problem or 'could not be parsed'}{context}\n"
            f"\nYour report was still written and this file was left untouched.\n"
            f"Fix the syntax error and re-run 'tally up'."
        )
    except OSError as e:
        raise InventoryError(f"Cannot read {path}: {e}")

    if not data:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get('files'), list):
        raise InventoryError(
            f"{path} should have a top-level 'files:' list.\n"
            f"Delete the file to let tally rebuild it — you will lose any\n"
            f"reviewComplete flags and those files will surface for review again."
        )

    entries = {}
    for index, entry in enumerate(data['files'], start=1):
        if not isinstance(entry, dict) or not entry.get('path'):
            raise InventoryError(
                f"{path}: entry {index} has no 'path:'. Every entry needs one."
            )
        unknown = set(entry) - ENTRY_KEYS
        if unknown:
            raise InventoryError(
                f"{path}: entry '{entry['path']}' has unrecognized "
                f"key(s): {', '.join(sorted(unknown))}.\n"
                f"Allowed keys are: {', '.join(sorted(ENTRY_KEYS))}."
            )
        complete = entry.get('reviewComplete')
        if complete is not None and not isinstance(complete, bool):
            raise InventoryError(
                f"{path}: entry '{entry['path']}' has "
                f"reviewComplete: {complete!r}, which is not true or false.\n"
                f"Use 'reviewComplete: true' to mark the file reviewed."
            )
        entries[_match_key(str(entry['path']))] = entry

    return entries


def register_files(config_dir, discovered, today=None):
    """Append newly-seen data files to the inventory and write it back.

    Args:
        config_dir: directory holding inventory.yaml
        discovered: iterable of (filepath, source_name) actually parsed this run
        today: date to stamp new entries with (injectable for tests)

    Returns:
        (entries, newly_registered) — entries is match-key -> entry for every
        file now known; newly_registered is the count added this run.

    Existing entries are never modified or removed, so a hand-set reviewComplete
    always survives.
    """
    entries = load_inventory(config_dir)
    stamp = (today or date.today()).isoformat()

    added = 0
    seen_this_run = set()
    for filepath, source_name in discovered:
        relative = to_relative(config_dir, filepath)
        key = _match_key(relative)
        if key in entries or key in seen_this_run:
            continue
        seen_this_run.add(key)
        entries[key] = {
            'path': relative,
            'source': source_name,
            'registered': stamp,
            'reviewComplete': False,
        }
        added += 1

    if added:
        _write(config_dir, entries)
    return entries, added


def is_reviewed(entries, config_dir, filepath):
    """True when this file has been confirmed and should stop surfacing rows.

    An unregistered file counts as unreviewed: a file tally has never seen
    certainly has not been confirmed.
    """
    entry = entries.get(_match_key(to_relative(config_dir, filepath)))
    return bool(entry and entry.get('reviewComplete'))


def _write(config_dir, entries):
    """Rewrite inventory.yaml.

    Emitted by hand so the header survives and reviewComplete reads as a plain
    true/false rather than a quoted string. PyYAML does not preserve comments;
    ruamel.yaml is deliberately not a dependency, so any comments the user adds
    here will be lost on the next run.
    """
    lines = [
        "# Data files tally has seen, and whether you have confirmed their",
        "# categorizations. Tally only ever appends entries.",
        "#",
        "# Set reviewComplete: true to confirm a file and stop its rows appearing",
        "# for review. Set it back to false to review a file again after",
        "# appending new rows to the CSV.",
        "#",
        "# reviewComplete never affects parsing or analysis — every transaction is",
        "# always included in your report totals.",
        "#",
        "# Note: comments you add below are lost when tally rewrites this file.",
        "",
        "files:",
    ]
    for entry in entries.values():
        lines.append(f"  - path: {_scalar(entry.get('path'))}")
        lines.append(f"    source: {_scalar(entry.get('source'))}")
        lines.append(f"    registered: {_scalar(entry.get('registered'))}")
        complete = 'true' if entry.get('reviewComplete') else 'false'
        lines.append(f"    reviewComplete: {complete}")

    path = os.path.join(config_dir, INVENTORY_FILENAME)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def _scalar(value):
    """Quote a value for YAML.

    JSON is a subset of YAML, so json.dumps gives correct quoting and escaping
    for paths containing spaces, colons, or quotes. Dates round-trip as their
    ISO string.
    """
    if isinstance(value, date):
        return json.dumps(value.isoformat())
    return json.dumps(value, ensure_ascii=False)
