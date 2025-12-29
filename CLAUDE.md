# CLAUDE.md

Project-specific guidance for Claude when working on this codebase.

## Bash Commands

```bash
uv run tally --help              # Show all commands
uv run tally run /path/to/config # Run analysis
uv run tally run --format json -v /path/to/config  # JSON output with reasoning
uv run tally explain /path/to/config               # Classification summary
uv run tally explain Netflix /path/to/config       # Explain specific merchant
uv run tally explain Netflix -vv /path/to/config   # Full details + which rule matched
uv run tally explain -c monthly /path/to/config    # Explain all monthly merchants
uv run tally diag /path/to/config # Debug config issues
uv run tally discover /path/to/config # Find unknown merchants
uv run tally inspect file.csv    # Analyze CSV structure
uv run pytest tests/             # Run all tests
uv run pytest tests/test_analyzer.py -v # Run analyzer tests
```

## Example: tally explain Output

```bash
$ tally explain Netflix -vv
Netflix → Monthly
  Monthly: Subscriptions appears 6/6 months (50% threshold = 3)

  Decision trace:
    ✗ NOT excluded: Subscriptions not in [Transfers, Cash, Income]
    ✗ NOT travel: category=Subscriptions, is_travel=false
    ✗ NOT annual: (Subscriptions, Streaming) not in annual categories
    ✗ NOT periodic: no periodic patterns matched
    ✓ IS monthly: Subscriptions with 6/6 months (>= 3 bill threshold)

  Calculation: avg (CV=0.00 (<0.3), payments are consistent)
    Formula: avg_when_active = 95.94 / 6 months = 15.99
    CV: 0.00

  Rule: NETFLIX.* (user)   # Shows which pattern matched
```

## Core Files

- `src/tally/analyzer.py` - Core analysis, HTML report generation, currency formatting
- `src/tally/cli.py` - CLI commands, AGENTS.md template (update for new features)
- `src/tally/config_loader.py` - Settings loading, migration logic
- `src/tally/format_parser.py` - CSV format string parsing
- `src/tally/merchant_utils.py` - Merchant normalization, rule matching
- `tests/test_analyzer.py` - Main test file for new features
- `docs/` - Marketing website (GitHub Pages)
- `config/` - Example configuration files

## IMPORTANT: Requirements

**Testing:**
- YOU MUST add tests for new analyzer features in `tests/test_analyzer.py`
- YOU MUST use Playwright MCP to verify HTML report changes before committing

**Development:**
- YOU MUST use `uv run` to run tally during development
- YOU MUST NOT use `python -m tally` or direct Python invocation

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
- YOU MUST update AGENTS.md in `cli.py` for new user-facing features

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
