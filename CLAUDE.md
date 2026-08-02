# CLAUDE.md

Project-specific guidance for Claude when working on this codebase.

## Bash Commands

```bash
uv run tally --help                                # Show all commands
uv run tally up --config /path/to/config           # Run analysis
uv run tally up --config CONFIG --format json -v   # JSON output with reasoning
uv run tally up --config CONFIG --format summary   # Text summary, no HTML
uv run tally up --config CONFIG --diff             # Compare against previous run
uv run tally explain --config CONFIG               # Classification summary
uv run tally explain Netflix --config CONFIG       # Explain specific merchant
uv run tally explain Netflix -vv --config CONFIG   # Full details + which rule matched
uv run tally explain --category Food --config CONFIG  # All Food category merchants
uv run tally explain --tags business --config CONFIG  # Business-tagged merchants
uv run tally diag --config CONFIG                  # Debug config (rules, budgets, sources)
uv run tally discover --config CONFIG              # Find unknown merchants
uv run tally workflow --config CONFIG              # Context-aware next steps
uv run tally reference                             # Full rule syntax reference
uv run tally inspect file.csv                      # Analyze CSV structure
```

The positional config path (`tally up /path/to/config`) still works but is
deprecated; prefer `--config`.

**Tests require the dev extra** — plain `uv run pytest` fails with
"Failed to spawn: pytest":

```bash
uv run --extra dev pytest tests/                   # Run all tests
uv run --extra dev pytest tests/test_analyzer.py -v        # Analyzer tests
uv run --extra dev pytest tests/test_rule_snapshots.py     # Rule engine snapshots
uv run --extra dev pytest tests/test_report_html.py        # Playwright HTML tests
```

## Example: tally explain Output

```bash
$ tally explain Netflix -vv
Netflix → Monthly
  Monthly: Subscriptions appears 6/6 months (50% threshold = 3)
  Tags: entertainment, recurring

  Decision trace:
    ✗ NOT excluded: Subscriptions not in [Transfers, Cash, Income]
    ✗ NOT travel: category=Subscriptions
    ✗ NOT annual: (Subscriptions, Streaming) not in annual categories
    ✗ NOT periodic: no periodic patterns matched
    ✓ IS monthly: Subscriptions with 6/6 months (>= 3 bill threshold)

  Calculation: avg (CV=0.00 (<0.3), payments are consistent)
    Formula: avg_when_active = 95.94 / 6 months = 15.99
    CV: 0.00

  Rule: NETFLIX.* (user)   # Shows which pattern matched
```

## merchants.rules Format

Rules live in `config/merchants.rules`. The legacy `merchant_categories.csv`
format is still read and auto-migrated (`migrations.py`), but `.rules` is the
current format.

```
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment, recurring

[Uber Eats]
match: normalized("UBEREATS")
category: Food
subcategory: Delivery

[Costco Bulk]
match: contains("COSTCO") and amount > 200
category: Shopping
subcategory: Wholesale
```

Match functions: `contains`, `regex`, `normalized`, `anyof`, `startswith`,
`fuzzy`, plus `amount`/`date`/`month`/`weekday` conditions combined with
`and`/`or`/`not`. Run `tally reference merchants` for the full syntax.

**Tags** are optional labels. Filter with `--tags business` or in the UI via
`t:business`. Four tags are special: `income`, `transfer` and `investment`
are excluded from spending totals, and `refund` shows under Credits.

## Budgets

Optional monthly targets in `settings.yaml`, compared against actuals in every
output format:

```yaml
budgets:
  total: 5000              # all spending, per month
  Food: 800                # a category
  Food/Groceries: 500      # a subcategory
  tag:business: 400        # everything with a tag
  Travel:                  # an annual pot instead of monthly
    amount: 6000
    period: yearly
```

Only real spending counts (income/transfer/investment are excluded, refunds
reduce the month they land in). A budget matching nothing is reported with a
suggested spelling rather than silently showing zero.

## Core Files

- `src/tally/analyzer.py` - Core analysis, terminal summaries, JSON/markdown/CSV export
- `src/tally/report.py` - HTML report generation, currency formatting
- `src/tally/cli.py` - Argument parsing only; each command lives in `src/tally/commands/`
- `src/tally/commands/` - One module per command (`run.py` is `tally up`)
- `src/tally/config_loader.py` - Settings loading, validation
- `src/tally/merchant_engine.py` - Rule matching engine
- `src/tally/merchant_utils.py` - Merchant normalization, rule matching, tags parsing
- `src/tally/section_engine.py` + `expr_parser.py` - views.rules parsing and evaluation
- `src/tally/budgets.py` - Budget targets and actual-vs-target evaluation
- `src/tally/anomalies.py` - Change detection ("worth a look")
- `src/tally/duplicates.py` - Overlapping-export detection
- `src/tally/classification.py` - Special tag semantics (income/transfer/investment)
- `src/tally/templates.py` - Starter files written by `tally init`
- `src/tally/spending_report.{html,css,js}` - Vue 3 report UI
- `tests/test_analyzer.py` - Main test file for new features
- `tests/test_budget_review.py` - Budgets, anomalies, duplicates
- `tests/test_report_html.py` - Playwright tests for the HTML report
- `docs/` - Marketing website (GitHub Pages)
- `config/` - Example configuration files

User-facing features are documented via `tally workflow` and `tally reference`
(there is no AGENTS.md template in the source; the repo's own `AGENTS.md`
just points at this file).

## IMPORTANT: Requirements

**Testing:**
- YOU MUST add tests for new analyzer features in `tests/test_analyzer.py`
- YOU MUST use `uv run --extra dev pytest` (plain `uv run pytest` cannot find pytest)
- YOU MUST use Playwright MCP to verify HTML report changes before committing

**Development:**
- YOU MUST use `uv run` to run tally during development
- YOU MUST NOT use `python -m tally` or direct Python invocation

**HTML Report Development:**
- Use `--no-embedded-html` to output separate CSS/JS/data files for easier iteration:
  ```bash
  uv run tally up --no-embedded-html -o /tmp/dev-report/spending.html /path/to/config
  ```
  This creates:
  - `spending.html` - HTML with external `<link>` and `<script>` references
  - `spending_report.css` - Editable styles
  - `spending_report.js` - Editable Vue app
  - `spending_data.js` - Transaction data

  Edit CSS/JS directly and refresh browser - no need to regenerate the report.

**Releases:**
- YOU MUST use GitHub workflow for releases
- YOU MUST NOT create releases manually or tag commits directly
- YOU MUST update release notes after workflow completes (see Release Process below)

**Commits:**
- YOU MUST use `Fixes #<issue>` or `Closes #<issue>` syntax to auto-close issues:
  ```
  Fix tooltip display on mobile

  Fixes #42
  ```
- YOU MUST NOT commit without referencing the issue when working on a tracked issue

**Configuration:**
- YOU MUST maintain backwards compatibility for `settings.yaml`
- YOU MUST implement automatic migration in `config_loader.py` if breaking changes are unavoidable
- YOU MUST document new options in `config/settings.yaml.example`
- YOU MUST update `tally workflow` (`src/tally/commands/workflow.py`) and
  `tally diag` for new user-facing settings, since agents read those to
  discover features

**Rule Engine (CRITICAL):**
- The rule engine is the CORE VALUE of tally - users carry personalized rules across versions
- YOU MUST NOT change rule matching behavior without making it opt-in
- YOU MUST run snapshot tests (`tests/test_rule_snapshots.py`) before committing rule engine changes
- Breaking changes to `merchant_engine.py` or `merchant_utils.py` require:
  1. New behavior behind a flag (e.g., `rule_mode` in settings.yaml)
  2. Default behavior unchanged
  3. Snapshot tests passing
- Historical example: commit 952c508 broke customers by changing "first match wins" to "most specific wins"

## Release Process

1. **Check commits since last release:**
   ```bash
   git fetch --tags
   gh release list --limit 1                    # Get latest version
   git log v0.1.XX..HEAD --oneline              # See what's new
   ```

2. **Draft release notes** focusing on user-facing features (not repo/doc changes):
   - New Features (with code examples)
   - Bug Fixes
   - Improvements

3. **Trigger release with notes:**
   ```bash
   gh workflow run release.yml -f release_notes="
   ### Currency Display Format (Issue #12)
   Display amounts in your local currency:
   \`\`\`yaml
   currency_format: \"€{amount}\"  # Euro
   currency_format: \"{amount} zł\" # Złoty
   \`\`\`

   ### Bug Fixes
   - Fixed X
   "
   gh run watch                                 # Wait for completion
   ```

   The workflow auto-appends install instructions to your notes.

## Error Messages & Diagnostics

- Error messages MUST be self-descriptive and guide users on what to do next
- SHOULD include specific suggestions (e.g., `Add: columns:\n  description: "{field} ..."`)
- Use `tally diag` to debug - it shows:
  - Config directory and settings file status
  - Data sources with parsed format details (columns, custom captures, templates)
  - Merchant rules (user-defined rules)
- The tool MUST be usable without external documentation
