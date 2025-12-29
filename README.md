# Tally

**A local rule engine for transaction classification.** Pair it with an LLM to eliminate the manual work.

Works with Claude Code, Codex, Copilot, Cursor, or a local model. Also available as an [MCP server](#mcp-server) for Claude Desktop, VS Code, and other AI tools.

👉 **[Website](https://tallyai.money)** · **[Releases](https://github.com/davidfowl/tally/releases)**

## Install

**Linux/macOS:**
```bash
curl -fsSL https://tallyai.money/install.sh | bash
```

**Windows PowerShell:**
```powershell
irm https://tallyai.money/install.ps1 | iex
```

**With uv:**
```bash
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
| `tally run --format json` | Output analysis as JSON with reasoning |
| `tally explain` | Explain why merchants are classified the way they are |
| `tally explain <merchant>` | Explain specific merchant's classification |
| `tally discover` | Find uncategorized transactions (`--format json` for LLMs) |
| `tally inspect <csv>` | Show CSV structure to build format string |
| `tally diag` | Debug config issues |
| `tally version` | Show version and check for updates |
| `tally update` | Update to latest version |
| `tally mcp` | Start MCP server for AI tool integration |
| `tally mcp init` | Configure MCP client (Claude Desktop, VS Code, etc.) |

### Output Formats

Both `tally run` and `tally explain` support multiple output formats:

```bash
tally run --format json        # JSON with classification reasoning
tally run --format markdown    # Markdown report
tally run --format summary     # Text summary only
tally run -v                   # Verbose: include decision trace
tally run -vv                  # Very verbose: include thresholds, CV values
```

### Filtering

Filter output to specific classifications or categories:

```bash
tally run --format json --only monthly,variable   # Just these classifications
tally run --format json --category Food           # Just Food category
tally explain --classification monthly            # Explain all monthly merchants
tally explain --category Subscriptions            # Explain all subscriptions
```

## MCP Server

Tally includes an MCP (Model Context Protocol) server for direct integration with AI tools like Claude Desktop, VS Code, Cursor, and more.

### Quick Setup

```bash
tally mcp init                    # Auto-detect and configure your AI tool
tally mcp init --client claude-desktop  # Or specify a client
tally mcp init --json             # Output config JSON for manual setup
```

### Supported Clients

| Client | Setup Command |
|--------|--------------|
| Claude Desktop | `tally mcp init --client claude-desktop` |
| VS Code | `tally mcp init --client vscode` |
| Cursor | `tally mcp init --client cursor` |
| Claude Code | `tally mcp init --client claude-code` |
| OpenCode | `tally mcp init --client opencode` |
| Gemini CLI | `tally mcp init --client gemini` |

### Available Tools

Once configured, your AI assistant can use these tools:

| Tool | Description |
|------|-------------|
| `run_analysis` | Analyze spending and generate reports (JSON/markdown) |
| `explain_merchant` | Explain why a merchant is classified a certain way |
| `explain_summary` | Get classification summary for all merchants |
| `discover_unknown` | Find uncategorized merchants with suggested rules |
| `inspect_csv` | Analyze CSV structure for format string creation |
| `diagnose_config` | Debug configuration issues |
| `list_rules` | List all merchant categorization rules |
| `add_rule` | Add a new merchant categorization rule |
| `get_version` | Get tally version information |

### Manual Configuration

If auto-setup doesn't work, add this to your MCP config:

```json
{
  "mcpServers": {
    "tally": {
      "command": "tally",
      "args": ["mcp"]
    }
  }
}
```

## Configuration

### settings.yaml

```yaml
year: 2025
currency_format: "€{amount}"  # Optional: €1,234 or "{amount} zł" for 1,234 zł

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
  - name: German Bank
    file: data/german.csv
    format: "{date:%d.%m.%Y},{description},{amount}"
    decimal_separator: ","  # European format (1.234,56)
```

### Format Strings

| Token | Description |
|-------|-------------|
| `{date:%m/%d/%Y}` | Date with format |
| `{description}` | Transaction description |
| `{amount}` | Amount (positive = expense) |
| `{-amount}` | Negated amount (for banks where negative = expense) |
| `{_}` | Skip column |
| `{custom_name}` | Capture column for use in description template |

**Multi-column descriptions** - Some banks split info across columns:
```yaml
- name: European Bank
  file: data/bank.csv
  format: "{date:%d.%m.%Y},{_},{txn_type},{vendor},{_},{amount}"
  columns:
    description: "{vendor} ({txn_type})"  # Combines into "STORE NAME (Card payment)"
```

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

**Analysis & Reasoning:**
- `tally run --format json -v` - Full analysis with classification reasoning
- `tally explain <merchant>` - Why a specific merchant is classified
- `tally explain <merchant> -vv` - Full details including which rule matched
- `tally explain --classification variable` - Explain all variable merchants

**Example: tally explain -vv Output:**
```
Netflix → Monthly
  Monthly: Subscriptions appears 6/6 months (50% threshold = 3)

  Decision trace:
    ✗ NOT excluded: Subscriptions not in [Transfers, Cash, Income]
    ✓ IS monthly: Subscriptions with 6/6 months (>= 3 bill threshold)

  Calculation: avg (CV=0.00 (<0.3), payments are consistent)
  Rule: NETFLIX.* (user)   # Shows which pattern matched
```

**Discovery & Debugging:**
- `tally discover --format json` - Structured unknown merchant data
- `tally diag --format json` - Debug configuration
- `tally inspect <file>` - Analyze CSV format

## Development Builds

Get the latest build from main branch:

**Update existing install:**
```bash
tally update --prerelease
```

**Fresh install (Linux/macOS):**
```bash
curl -fsSL https://tallyai.money/install.sh | bash -s -- --prerelease
```

**Fresh install (Windows):**
```powershell
iex "& { $(irm https://tallyai.money/install.ps1) } -Prerelease"
```

Dev builds are created automatically on every push to main. When running a dev version, `tally version` will notify you of newer dev builds.

## License

MIT
