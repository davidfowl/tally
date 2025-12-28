# CLAUDE.md

Project-specific guidance for Claude when working on this codebase.

## Bash Commands

```bash
uv run tally --help              # Show all commands
uv run tally run /path/to/config # Run analysis
uv run tally diag /path/to/config # Debug config issues
uv run tally discover /path/to/config # Find unknown merchants
uv run tally inspect file.csv    # Analyze CSV structure
uv run pytest tests/             # Run all tests
uv run pytest tests/test_analyzer.py -v # Run analyzer tests
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

## Error Messages & Diagnostics

- Error messages MUST be self-descriptive and guide users on what to do next
- SHOULD include specific suggestions (e.g., `Add: columns:\n  description: "{field} ..."`)
- Use `tally diag` to debug - it shows:
  - Config directory and settings file status
  - Data sources with parsed format details (columns, custom captures, templates)
  - Merchant rules (baseline + user rules)
- The tool MUST be usable without external documentation
