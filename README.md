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

## Currency Detection

Tally automatically detects the currency from your transaction data. If you want to override this, set `currency_format` in `settings.yaml`:

```yaml
# Auto-detect currency from data (default behavior)
# currency_format: not set

# Or explicitly set your currency:
currency_format: "€{amount}"      # Euro
currency_format: "£{amount}"      # British Pound
currency_format: "{amount} lei"   # Romanian Leu
```

When `currency_format` is set in `settings.yaml`, it overrides auto-detection. If not set, Tally will detect the most common currency from your transaction files.

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
| `tally run` | Generate HTML spending report |
| `tally discover` | Find uncategorized transactions |
| `tally explain` | Explain merchant classifications |
| `tally inspect` | Analyze CSV structure |
| `tally reference` | Show full syntax reference |

## License

MIT
