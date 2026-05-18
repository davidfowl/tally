<p align="center">
  <img src="docs/logo.svg" alt="Tally Logo" width="80" height="80">
</p>

<h1 align="center">Tally</h1>

<p align="center">
  <strong>AI-powered transaction classification for your bank statements</strong>
</p>

<p align="center">
  <a href="https://github.com/davidfowl/tally/actions"><img src="https://github.com/davidfowl/tally/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/davidfowl/tally/releases"><img src="https://img.shields.io/github/v/release/davidfowl/tally" alt="Release"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"></a>
</p>

<p align="center">
  <a href="https://tallyai.money">Website</a> &bull;
  <a href="https://tallyai.money/quickstart.html">Quick Start</a> &bull;
  <a href="https://tallyai.money/guide.html">Guide</a> &bull;
  <a href="https://tallyai.money/reference.html">Reference</a>
</p>

---

Tally is a local rule engine that works with AI coding assistants to automatically categorize your bank transactions. No cloud services, no databases - just simple rule files you control.

**Works with:** Claude Code, GitHub Copilot, Codex, Cursor, and any command-line AI agent.

<p align="center">
  <img src="docs/demo.gif" alt="Tally Demo" width="700">
</p>

## Why Tally?

Bank transactions are cryptic:
```
WHOLEFDS MKT 10847 SEATTLE WA
AMZN MKTP US*2K7X9
SQ *JOES COFFEE SEATTLE
```

Your bank's categories are too broad - "Shopping" when you need "Kids > Clothing" vs "Home > Furniture". AI assistants *know* what these merchants are. Tally bridges the gap:

1. **You describe rules in plain English** to your AI assistant
2. **AI writes the rules** to a simple file
3. **Tally generates reports** with your custom categories

## Installation

**Linux / macOS**
```bash
curl -fsSL https://tallyai.money/install.sh | bash
```

**Windows (PowerShell)**
```powershell
irm https://tallyai.money/install.ps1 | iex
```

**From PyPI**
```bash
pip install tally
```

## Quick Start

```bash
# Create a new budget folder
tally init ./my-budget
cd my-budget

# See context-aware next steps
tally workflow
```

Then tell your AI assistant: *"Use tally to categorize my transactions"*

## Commands

| Command | Description |
|---------|-------------|
| `tally init [dir]` | Create a new budget folder with config templates |
| `tally up` | Generate HTML spending report |
| `tally up --summary` | Quick text summary without HTML |
| `tally up --format json` | Export as JSON with full reasoning |
| `tally up --format csv` | Export transaction-level CSV |
| `tally up --format markdown` | Export as Markdown |
| `tally discover` | Find uncategorized transactions with suggested rules |
| `tally explain [merchant]` | Show how merchants are categorized |
| `tally explain --category Food` | List all merchants in a category |
| `tally explain --tags business` | List merchants with specific tags |
| `tally inspect <file.csv>` | Analyze CSV structure for format strings |
| `tally workflow` | Show context-aware next steps |
| `tally reference` | Display complete rule syntax documentation |
| `tally diag` | Debug configuration issues |
| `tally update` | Update to the latest version |

## How It Works

### 1. Configure Data Sources

Add your bank CSVs to the `data/` folder and configure them in `config/settings.yaml`:

```yaml
year: 2025
title: "My Budget Analysis"

data_sources:
  - name: Chase
    file: data/chase-2025.csv
    format: "{date:%m/%d/%Y},{description},{amount}"

  - name: Amex
    file: data/amex-*.csv  # Glob patterns supported
    type: amex             # Built-in format
```

Use `tally inspect yourfile.csv` to auto-detect the format string.

### 2. Write Categorization Rules

Create rules in `config/merchants.rules`:

```
[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment, recurring

[Uber Eats]
match: contains("UBER") and contains("EATS")
category: Food
subcategory: Delivery

[Uber]
match: contains("UBER")
category: Transport
subcategory: Rideshare
```

### 3. Generate Reports

```bash
tally up              # HTML report
tally up --summary    # Quick text summary
```

## Rule Syntax

Tally uses an expressive rule language:

### Match Functions

```
contains("AMAZON")           # Substring match
regex("AMZN.*MKTP")         # Regular expression
normalized("WHOLEFOODS")     # Fuzzy match (ignores spaces, case)
startswith("SQ *")          # Prefix match
anyof("DELTA", "UNITED")    # Match any of multiple patterns
fuzzy("Starbucks", 0.8)     # Similarity threshold
```

### Conditions

```
amount > 100                 # Amount filters
amount >= 50 and amount < 200
date >= "2025-01-01"        # Date filters
month == 12                 # Month number
source == "Chase"           # Data source name
```

### Combined Rules

```
[Large Amazon Purchase]
match: contains("AMAZON") and amount > 500
category: Shopping
subcategory: Electronics
tags: large
```

### Tag-Only Rules

Add tags without changing categorization:

```
[Business Expenses]
match: anyof("GITHUB", "AWS", "DIGITALOCEAN")
tags: business, reimbursable
```

### Field Transforms

Strip payment processor prefixes before matching:

```
# Add at top of merchants.rules
field.description = regex_replace(field.description, "^APLPAY\\s+", "")
field.description = regex_replace(field.description, "^SQ\\s*\\*", "")
```

## Special Tags

These tags affect how transactions appear in reports:

| Tag | Effect |
|-----|--------|
| `income` | Excluded from spending, shown as income |
| `transfer` | Excluded from spending, tracked as transfers |
| `investment` | 401K, IRA contributions - tracked separately |
| `refund` | Credits and returns - shown in Credits section |

Example:
```
[Paycheck]
match: contains("PAYROLL")
category: Income
subcategory: Salary
tags: income

[401K]
match: contains("VANGUARD 401K")
category: Investments
subcategory: Retirement
tags: investment
```

## Views (Custom Report Sections)

Define custom sections in `config/views.rules`:

```
[Monthly Bills]
filter: category in ("Utilities", "Insurance", "Subscriptions")
sort: total desc

[Discretionary]
filter: category in ("Entertainment", "Dining", "Shopping")
sort: monthly_value desc
```

## Output Formats

### HTML Report
```bash
tally up
```
Generates an interactive HTML report with:
- Cash flow summary (income, spending, credits)
- Transfer tracking
- Monthly breakdown charts
- Drill-down by merchant/category
- Tag filtering

### JSON Export
```bash
tally up --format json -v
```
Full data export with reasoning and calculation details.

### CSV Export
```bash
tally up --format csv
```
Transaction-level export for spreadsheet analysis.

### Markdown
```bash
tally up --format markdown
```
Text-based report for documentation.

## Configuration Reference

### settings.yaml

```yaml
# Time period
year: 2025
title: "2025 Budget Analysis"

# Currency display (default: "${amount}")
currency_format: "€{amount}"    # Euro
# currency_format: "£{amount}"  # British Pound
# currency_format: "{amount} zł" # Polish Złoty

# Data sources
data_sources:
  - name: Bank Name
    file: data/transactions.csv
    format: "{date:%m/%d/%Y},{description},{amount}"
    # delimiter: ";"      # For semicolon-separated files
    # decimal: ","        # For European number format (1.234,56)

# Output
output_dir: output
html_filename: spending_summary.html

# Rule matching (default: first_match)
# rule_mode: most_specific  # Most specific rule wins instead of first
```

### Amount Modifiers

| Format | Behavior |
|--------|----------|
| `{amount}` | Use as-is (positive = expense) |
| `{-amount}` | Negate (flip the sign) |
| `{+amount}` | Absolute value (always positive) |

## Tips & Best Practices

1. **Start broad, refine later** - Write general rules first, add specific overrides when needed

2. **Consolidate similar merchants**:
   ```
   [Airlines]
   match: anyof("DELTA", "UNITED", "AMERICAN", "SOUTHWEST")
   category: Travel
   subcategory: Flights
   ```

3. **Use `normalized()` for inconsistent names**:
   ```
   match: normalized("WHOLEFOODS")  # Matches "WHOLE FOODS", "WHOLEFDS", etc.
   ```

4. **Avoid overly generic patterns**:
   ```
   # BAD: contains("AT") matches everything
   # GOOD: regex(r'\bAT&T\b')
   ```

5. **Verify with explain**:
   ```bash
   tally explain Amazon              # Check by merchant name
   tally explain "WHOLEFDS MKT"      # Test raw description
   tally explain --category Food     # List all Food merchants
   ```

6. **Use field transforms instead of catch-all rules**:
   ```
   # BAD: [ApplePay] match: startswith("APLPAY")
   # GOOD: field.description = regex_replace(field.description, "^APLPAY\\s+", "")
   ```

## Project Structure

```
my-budget/
├── config/
│   ├── settings.yaml        # Data sources and options
│   ├── merchants.rules      # Categorization rules
│   └── views.rules          # Custom report sections
├── data/
│   ├── chase-2025.csv       # Your bank exports
│   └── amex-2025.csv
└── output/
    └── spending_summary.html  # Generated report
```

## Documentation

Full documentation is available at **[tallyai.money](https://tallyai.money)**:

- [Quick Start](https://tallyai.money/quickstart.html) - Get running in minutes
- [Guide](https://tallyai.money/guide.html) - Using Tally with AI assistants
- [Reference](https://tallyai.money/reference.html) - Complete rule syntax
- [Formats](https://tallyai.money/formats.html) - CSV format strings

## Contributing

Contributions are welcome! Please see the [GitHub repository](https://github.com/davidfowl/tally) for:

- [Issues](https://github.com/davidfowl/tally/issues) - Bug reports and feature requests
- [Releases](https://github.com/davidfowl/tally/releases) - Version history

## License

MIT License - see [LICENSE](LICENSE) for details.
