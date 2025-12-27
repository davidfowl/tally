# Tally

**Let AI classify your transactions.** LLM-powered spending categorization.

👉 **[Website](https://davidfowl.github.io/tally/)** · **[Releases](https://github.com/davidfowl/tally/releases)**

## Install

```bash
# Linux/macOS (installs to ~/.tally)
curl -fsSL https://raw.githubusercontent.com/davidfowl/tally/main/install.sh | bash

# Windows PowerShell (installs to %LOCALAPPDATA%\tally)
irm https://raw.githubusercontent.com/davidfowl/tally/main/install.ps1 | iex

# With uv
uv tool install git+https://github.com/davidfowl/tally
```

## Quick Start

```bash
tally init ./my-budget      # Create budget folder
# Add bank exports to my-budget/data/
# Edit my-budget/config/settings.yaml
tally run                    # Generate spending report
```

## Commands

| Command | Description |
|---------|-------------|
| `tally init [dir]` | Set up a new budget folder |
| `tally run` | Parse transactions and generate HTML report |
| `tally discover` | Find uncategorized transactions (`--format json` for LLMs) |
| `tally inspect <csv>` | Show CSV structure to build format string |
| `tally diag` | Debug config issues |

## Configuration

### settings.yaml

```yaml
year: 2025
data_sources:
  - name: AMEX
    file: data/amex.csv
    type: amex
  - name: Chase
    file: data/chase.csv
    format: "{date:%m/%d/%Y},{description},{amount}"
  - name: BofA Checking
    file: data/bofa.csv
    format: "{date:%m/%d/%Y},{description},{-amount}"
```

### Format Strings

| Token | Description |
|-------|-------------|
| `{date:%m/%d/%Y}` | Date with format |
| `{description}` | Transaction description |
| `{amount}` | Amount (positive = expense) |
| `{-amount}` | Negated amount (for banks where negative = expense) |
| `{_}` | Skip column |

### merchant_categories.csv

```csv
Pattern,Merchant,Category,Subcategory
WHOLEFDS,Whole Foods,Food,Grocery
UBER\s(?!EATS),Uber,Transport,Rideshare
UBER\s*EATS,Uber Eats,Food,Delivery
COSTCO[amount>200],Costco Bulk,Shopping,Bulk
BESTBUY[amount=499.99][date=2025-01-15],TV Purchase,Shopping,Electronics
```

Patterns are Python regex (case-insensitive). First match wins.

**Inline modifiers** target specific transactions:
- `[amount>200]`, `[amount:50-100]` - Amount conditions
- `[date=2025-01-15]`, `[month=12]` - Date conditions

## For AI Agents

Run `tally init` to generate `AGENTS.md` with detailed instructions. Key commands:
- `tally discover --format json` - Structured unknown merchant data
- `tally diag --format json` - Debug configuration
- `tally inspect <file>` - Analyze CSV format

## License

MIT
