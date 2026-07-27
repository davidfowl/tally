# Tally

**A local rule engine for transaction classification.** Pair it with an AI assistant to eliminate the manual work.

Works with Claude Code, Codex, Copilot, Cursor, or any command-line AI agent.

## Install

**Linux / macOS**
```bash
curl -fsSL https://tallyai.money/install.sh | bash
```

**PowerShell**
```powershell
irm https://tallyai.money/install.ps1 | iex
```

## Quick Start

```bash
tally init ./my-budget      # Create budget folder
cd my-budget
tally workflow              # See next steps
```

Tell your AI assistant: *"Use tally to categorize my transactions"*

## Budgets

Set monthly targets in `config/settings.yaml` and every report compares plan
against actual:

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

`tally up` also flags what changed since previous months - price rises on
recurring charges, new merchants, recurring bills that did not arrive, and
category spikes - and warns when two statement exports overlap and would
otherwise double count.

## Documentation

Full documentation is available at **[tallyai.money](https://tallyai.money)**:

- [Quick Start](https://tallyai.money/quickstart.html) - Get running in 5 minutes
- [Guide](https://tallyai.money/guide.html) - Using Tally with AI assistants
- [Reference](https://tallyai.money/reference.html) - merchants.rules and views.rules syntax
- [Formats](https://tallyai.money/formats.html) - settings.yaml and CSV format strings

## Commands

| Command | Description |
|---------|-------------|
| `tally init` | Create a new budget folder |
| `tally workflow` | Show context-aware next steps |
| `tally up` | Generate HTML spending report |
| `tally discover` | Find uncategorized transactions |
| `tally explain` | Explain merchant classifications |
| `tally inspect` | Analyze CSV structure |
| `tally reference` | Show full syntax reference |
| `tally diag` | Diagnose config issues |

## License

MIT
