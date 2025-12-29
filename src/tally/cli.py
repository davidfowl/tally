"""
Tally CLI - Command-line interface.

Usage:
    tally /path/to/config/dir               # Analyze using config directory
    tally /path/to/config/dir --summary     # Summary only (no HTML)
    tally /path/to/config/dir --settings settings-2024.yaml
    tally --help-config                     # Show detailed config documentation
"""

import argparse
import os
import shutil
import sys

from ._version import (
    VERSION, GIT_SHA, REPO_URL, check_for_updates,
    get_latest_release_info, perform_update
)
from .config_loader import load_config

BANNER = r'''
  ████████╗ █████╗ ██╗     ██╗  ██╗   ██╗
  ╚══██╔══╝██╔══██╗██║     ██║  ╚██╗ ██╔╝
     ██║   ███████║██║     ██║   ╚████╔╝
     ██║   ██╔══██║██║     ██║    ╚██╔╝
     ██║   ██║  ██║███████╗███████╗██║
     ╚═╝   ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝
'''
from .merchant_utils import get_all_rules, diagnose_rules
from .analyzer import (
    parse_amex,
    parse_boa,
    parse_generic_csv,
    auto_detect_csv_format,
    analyze_transactions,
    print_summary,
    write_summary_file,
)


CONFIG_HELP = '''
BUDGET ANALYZER - CONFIGURATION
================================

QUICK START
-----------
1. Run: tally init ./my-budget
2. Add CSV/TXT statements to my-budget/data/
3. Edit my-budget/config/settings.yaml with your data sources
4. Run: tally run ./my-budget/config

DIRECTORY STRUCTURE
-------------------
my-budget/
├── config/
│   ├── settings.yaml           # Data sources & settings
│   └── merchant_categories.csv # Custom category overrides (optional)
├── data/                       # Your statement exports
└── output/                     # Generated reports

SETTINGS.YAML
-------------
year: 2025
data_sources:
  - name: AMEX
    file: data/amex.csv
    type: amex                  # or "boa" for Bank of America
  # Custom CSV format:
  - name: Chase
    file: data/chase.csv
    format: "{date:%m/%d/%Y},{description},{amount}"
output_dir: output

# Optional: specify home locations (auto-detected if not set)
home_locations:
  - WA
  - OR                          # Nearby state to not count as travel

# Optional: pretty names for travel destinations
travel_labels:
  HI: Hawaii
  GB: United Kingdom

TRAVEL DETECTION
----------------
International transactions are automatically classified as travel.
Domestic out-of-state is NOT auto-travel. To opt-in, add merchant rules:

  .*\\sHI$,Hawaii Trip,Travel,Hawaii
  .*\\sCA$,California Trip,Travel,California

DISCOVERING UNKNOWN MERCHANTS
-----------------------------
Use the discover command to find uncategorized transactions:
  tally discover               # Human-readable output
  tally discover --format csv  # CSV output to copy-paste
  tally discover --format json # JSON for programmatic use

MERCHANT RULES
--------------
Define merchant patterns in merchant_categories.csv:

Pattern,Merchant,Category,Subcategory
MY LOCAL CAFE,Local Cafe,Food,Coffee
ZELLE.*JANE,Jane (Babysitter),Personal,Childcare

Pattern syntax (Python regex):
  NETFLIX           Contains "NETFLIX"
  DELTA|SOUTHWEST   Either one
  COSTCO(?!.*GAS)   COSTCO but not COSTCO GAS
  ^ATT\\s           Starts with "ATT "

Use: tally inspect <file.csv> to see transaction formats.
'''

STARTER_SETTINGS = '''# Tally Settings
year: {year}
title: "Spending Analysis {year}"

# Data sources - add your statement files here
data_sources:
  # Example AMEX CSV export:
  # - name: AMEX
  #   file: data/amex-{year}.csv
  #   type: amex
  #
  # Example Bank of America text statement:
  # - name: BOA Checking
  #   file: data/boa-checking.txt
  #   type: boa

output_dir: output
html_filename: spending_summary.html

# Home locations (auto-detected if not specified)
# Transactions outside these locations are classified as travel
# home_locations:
#   - WA
#   - OR

# Optional: pretty names for travel destinations in reports
# travel_labels:
#   HI: Hawaii
#   GB: United Kingdom
'''

STARTER_MERCHANT_CATEGORIES = '''# Merchant Categorization Rules
#
# Define your merchant categorization rules here.
# Format: Pattern,Merchant,Category,Subcategory
#
# - Pattern: Python regex (case-insensitive) matched against transaction descriptions
# - Use | for alternatives: DELTA|SOUTHWEST matches either
# - Use (?!...) for negative lookahead: UBER\\s(?!EATS) excludes Uber Eats
# - Test patterns at regex101.com (Python flavor)
#
# First match wins.
# Run: tally inspect <file> to see your transaction descriptions.
#
# Examples:
#   MY LOCAL BAKERY,My Favorite Bakery,Food,Restaurant
#   JOHNS PLUMBING,John's Plumbing,Bills,Home Repair
#   ZELLE.*JANE,Jane (Babysitter),Personal,Childcare

Pattern,Merchant,Category,Subcategory

# Add your custom rules below:

'''

STARTER_AGENTS_MD = '''# Tally - Agent Instructions

This document provides instructions for AI agents working with Tally.

## Quick Reference

```bash
# Show help
tally

# Initialize a new budget directory (current dir or specified)
tally init
tally init ./my-budget

# Run analysis (uses ./config by default)
tally run
tally run ./path/to/config

# Output formats for programmatic analysis
tally run --format json           # JSON output with classification reasoning
tally run --format markdown       # Markdown report
tally run --format json -v        # JSON with decision trace
tally run --format json -vv       # JSON with full details (thresholds, CV)

# Filter output
tally run --format json --only monthly,variable    # Just these classifications
tally run --format json --category Food            # Just Food category merchants

# Explain why merchants are classified the way they are
tally explain                     # Overview of all classifications
tally explain Netflix             # Explain specific merchant
tally explain Netflix -v          # With decision trace
tally explain -c monthly          # Explain all monthly merchants
tally explain --category Food     # Explain all Food category merchants

# View summary only (find uncategorized transactions)
tally run --summary

# Inspect a CSV file to see its structure and get format suggestions
tally inspect path/to/file.csv

# Discover unknown merchants and get suggested rules
tally discover                    # Human-readable output
tally discover --format csv       # CSV output for import
tally discover --format json      # JSON output for programmatic use
tally discover --limit 50         # Show top 50 by spend

# Diagnose configuration issues
tally diag                        # Show config, data sources, format parsing
```

## Understanding Classifications

Tally classifies merchants into these categories based on occurrence patterns:

| Classification | Criteria | Example |
|----------------|----------|---------|
| Monthly | Bills/Utilities/Subscriptions appearing 50%+ of months | Netflix, Electric Bill |
| Annual | Bill-type categories with 1-2 charges per year | Annual insurance premium |
| Periodic | Recurring but non-monthly (tuition, quarterly payments) | School tuition |
| Travel | Travel category or international location | Airlines, Hotels abroad |
| One-Off | High-value infrequent (>$1000, ≤3 months) | Home improvement, Electronics |
| Variable | Everything else (discretionary) | Restaurants, Shopping |

Use `tally explain` to understand why a specific merchant is classified:

```bash
tally explain Netflix -v          # See the decision trace
tally explain Netflix -vv         # Full details + which rule matched
tally explain --classification variable  # Why is something variable?
```

### Example: tally explain -vv Output

```
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

  Rule: NETFLIX.* (user)
```

The `Rule:` line shows:
- The pattern that matched (e.g., `NETFLIX.*`)
- The source: `user` (from merchant_categories.csv)

## Troubleshooting

When something isn't working, use `tally diag` to inspect:
- Configuration file location and status
- Data source format parsing (columns, custom captures, templates)
- Merchant rules (user-defined)

## Your Tasks

When working with this budget analyzer, you may be asked to:

1. **Set up a new budget directory** - Use `tally init`
2. **Add merchant categorization rules** - Edit `config/merchant_categories.csv`
3. **Configure data sources** - Edit `config/settings.yaml`
4. **Analyze and fix uncategorized transactions** - Run with `--summary`, then add rules
5. **Debug configuration issues** - Use `tally diag`
6. **Analyze spending patterns** - Use `tally run --format json` for structured data
7. **Explain classifications** - Use `tally explain <merchant>` to understand why
8. **Answer "why" questions** - Use `tally explain -v` for decision traces

## Understanding merchant_categories.csv

This is the main file you'll edit. Each row maps a transaction pattern to a category.

**Format:** `Pattern,Merchant,Category,Subcategory`

**Pattern** is a Python regex (case-insensitive) matched against transaction descriptions.

### Pattern Examples

| Pattern | What it matches | Use case |
|---------|-----------------|----------|
| `NETFLIX` | Contains "NETFLIX" | Simple substring |
| `STARBUCKS` | Contains "STARBUCKS" | Simple substring |
| `DELTA\\|SOUTHWEST` | "DELTA" OR "SOUTHWEST" | Multiple variations |
| `WHOLE FOODS\\|WHOLEFDS` | Either spelling | Handle abbreviations |
| `UBER\\s(?!EATS)` | "UBER " NOT followed by "EATS" | Exclude Uber Eats from rideshare |
| `COSTCO(?!.*GAS)` | "COSTCO" without "GAS" | Exclude gas station |
| `APPLE\\.COM(?!/BILL)` | Apple.com but not /BILL | Exclude subscriptions |
| `^ATT\\s` | Starts with "ATT " | Avoid matching "SEATTLE" |
| `CHICK.FIL.A` | CHICK-FIL-A or CHICKFILA | `.` matches any char |

### Inline Modifiers (Target Specific Transactions)

Add conditions to patterns to match on amount or date:

```csv
# Amount modifiers
COSTCO[amount>200],Costco Bulk,Shopping,Bulk
STARBUCKS[amount<10],Quick Coffee,Food,Coffee
BESTBUY[amount=499.99],TV Purchase,Shopping,Electronics
RESTAURANT[amount:20-100],Dining Out,Food,Restaurant

# Date modifiers
COSTCO[date=2025-01-15],Costco Jan 15,Shopping,Grocery
SUBSCRIPTION[date:2025-01-01..2025-06-30],H1 Subscription,Bills,Subscription
PURCHASE[date:last30days],Recent Purchase,Shopping,Retail
HOLIDAY[month=12],December Shopping,Shopping,Gifts

# Combined (AND logic)
COSTCO(?!GAS)[amount>200][date=2025-01-15],Specific Costco Trip,Shopping,Bulk
```

**Amount modifiers:**
- `[amount>100]` - Greater than $100
- `[amount>=100]` - Greater than or equal to $100
- `[amount<50]` - Less than $50
- `[amount<=50]` - Less than or equal to $50
- `[amount=99.99]` - Exactly $99.99
- `[amount:50-200]` - Between $50 and $200 (inclusive)

**Date modifiers:**
- `[date=2025-01-15]` - Exact date (YYYY-MM-DD format)
- `[date:2025-01-01..2025-01-31]` - Date range (inclusive)
- `[date:last30days]` - Within last N days
- `[month=12]` - Any transaction in December (any year)

Use modifiers to express rules like *"that $500 Best Buy purchase was a gift"* or *"Costco purchases over $200 are bulk shopping"*.

### Adding New Rules

1. Look at the raw transaction description from the bank statement
2. Find a unique substring or pattern that identifies the merchant
3. Add a row: `PATTERN,Clean Name,Category,Subcategory`

**Example:** If you see `"WHOLEFDS MKT 10847"` in a statement:
```csv
WHOLEFDS,Whole Foods,Food,Grocery
```

### Rule Order Matters

Rules are matched top-to-bottom. Put specific rules before general ones:

```csv
# Specific first
UBER\\s*EATS,Uber Eats,Food,Delivery
# General second
UBER,Uber,Transport,Rideshare
```

### Standard Categories

Use these categories for consistency:

| Category | Subcategories |
|----------|---------------|
| Food | Grocery, Restaurant, Fast Food, Fast Casual, Coffee, Delivery, Bakery |
| Shopping | Online, Retail, Clothing, Electronics, Home, Kids, Beauty, Books |
| Travel | Airline, Lodging, Car Rental, Agency |
| Transport | Rideshare, Gas, Parking, Tolls, Auto Service |
| Subscriptions | Streaming, Software, News |
| Health | Gym, Pharmacy, Medical, Vision, Fitness |
| Utilities | Mobile, Internet/TV, Electric, Water |
| Entertainment | Movies, Events, Activities, Attractions |
| Transfers | P2P, CC Payment, Investment, Transfer |
| Bills | Mortgage, Insurance, Tax |
| Personal | Childcare, Grooming, Spa |
| Cash | ATM, Check |

## Workflow: Adding Rules for Uncategorized Transactions

### Method 1: Using the discover command (Recommended for agents)

1. Run discover to find unknown merchants sorted by spend:
   ```bash
   tally discover --format json
   ```

2. The output includes:
   - `raw_description`: The original transaction description
   - `suggested_pattern`: A regex pattern to match it
   - `suggested_merchant`: A clean merchant name
   - `count`: Number of transactions
   - `total_spend`: Total amount spent

3. For each unknown merchant:
   - Review the suggested pattern and merchant name
   - Determine the appropriate Category and Subcategory
   - Add to `merchant_categories.csv`

4. Re-run to verify:
   ```bash
   tally run --summary
   ```

### Method 2: Manual inspection

1. Run analysis to find unknown merchants:
   ```bash
   tally run --summary
   ```

2. Look for transactions categorized as "Unknown"

3. For each unknown merchant:
   - Find the raw description in the statement file
   - Create a pattern that uniquely matches it
   - Add to `merchant_categories.csv`

4. Re-run to verify categorization

## Using discover for Bulk Rule Creation

The discover command is designed to help agents efficiently create rules:

```bash
# Get JSON output for programmatic processing
tally discover --format json --limit 0

# Get CSV output ready for import (just needs categories filled in)
tally discover --format csv
```

### JSON Output Structure

```json
[
  {
    "raw_description": "STARBUCKS STORE 12345 SEATTLE WA",
    "suggested_pattern": "STARBUCKS\\s*STORE",
    "suggested_merchant": "Starbucks Store",
    "count": 15,
    "total_spend": 87.50,
    "examples": [
      {"date": "2025-01-15", "amount": -5.50, "description": "Starbucks Store"}
    ]
  }
]
```

### Workflow for Agents

1. Run `tally discover --format json --limit 0`
2. Parse the JSON output
3. For each unknown merchant:
   - Use `suggested_pattern` as starting point (may need refinement)
   - Use `suggested_merchant` as the merchant name
   - Determine Category/Subcategory based on merchant type
4. Append rules to `config/merchant_categories.csv`
5. Run `tally run --summary` to verify improvement
6. Repeat until Unknown transactions are minimized

## File Locations

```
my-budget/
├── config/
│   ├── settings.yaml           # Data sources, year, output settings
│   └── merchant_categories.csv # Pattern → Category rules (EDIT THIS)
├── data/                       # Bank/CC statement exports
└── output/                     # Generated reports
```

## Travel Detection

International transactions are automatically classified as travel.
Domestic out-of-state transactions are NOT auto-travel (opt-in via merchant rules).

To mark domestic locations as travel, add patterns to merchant_categories.csv:
```csv
.*\\sHI$,Hawaii Trip,Travel,Hawaii
.*\\sCA$,California Trip,Travel,California
```

Configure home in settings.yaml:
```yaml
# Optional: specify home locations (for international exclusions)
home_locations:
  - WA

# Optional: pretty names for travel destinations
travel_labels:
  HI: Hawaii
  GB: United Kingdom

# Optional: currency display format (default: ${amount})
currency_format: "€{amount}"      # Euro prefix: €1,234
# currency_format: "{amount} zł"  # Polish złoty suffix: 1,234 zł
# currency_format: "£{amount}"    # British pound prefix: £1,234
```

If `home_locations` is not specified, it's auto-detected from your most common transaction location.

## Statement Formats and Custom Parsing

The tool supports three ways to parse CSV files:

### 1. Predefined Types (backward compatible)
```yaml
data_sources:
  - name: AMEX
    file: data/amex.csv
    type: amex      # Expects Date,Description,Amount columns
  - name: BOA
    file: data/boa.txt
    type: boa       # Expects "MM/DD/YYYY Description Amount Balance" lines
```

### 2. Custom Format Strings (for any CSV)
Use a format string to specify column mappings:
```yaml
data_sources:
  - name: Chase
    file: data/chase.csv
    format: "{date:%m/%d/%Y}, {_}, {description}, {_}, {_}, {amount}"
  - name: BofA Checking
    file: data/bofa.csv
    format: "{date:%m/%d/%Y}, {description}, {-amount}"  # Bank: negative = expense
```

**Format string syntax:**
- `{date:%m/%d/%Y}` - Date column with strptime format
- `{description}` - Transaction description column
- `{amount}` - Amount column (positive = expense)
- `{-amount}` - Negate amounts (for bank accounts where negative = expense)
- `{location}` - Optional location/state column
- `{_}` - Skip this column

**Sign conventions:**
- Credit cards typically show charges as positive → use `{amount}`
- Bank accounts typically show debits as negative → use `{-amount}`

Position in the string = column index (0-based).

### Discovering CSV Structure

Use the **inspect** command to analyze an unknown CSV:
```bash
tally inspect path/to/file.csv
```

This shows column headers, indices, and sample data rows.

### Workflow: Creating a Format String for Any CSV

**Step 1: Inspect the file**
```bash
tally inspect data/newbank.csv
```

**Step 2: Identify the columns**
Look at the output and find:
- Which column has the **date** (and what format: MM/DD/YYYY, YYYY-MM-DD, etc.)
- Which column has the **description** (merchant name)
- Which column has the **amount**
- Optionally, which column has **location** (state/country code)

**Step 3: Build the format string**
For each column position (0, 1, 2, ...), add:
- `{date:%m/%d/%Y}` if it's the date column (adjust format as needed)
- `{description}` if it's the description column
- `{amount}` if it's the amount column
- `{location}` if it's a location column
- `{_}` for any columns to skip

**Example:** A CSV with columns: Transaction Date, Post Date, Description, Category, Amount

```yaml
format: "{date:%m/%d/%Y}, {_}, {description}, {_}, {amount}"
#         col 0           col 1  col 2       col 3  col 4
```

**Common date formats:**
- `%m/%d/%Y` - 01/15/2024 (US format)
- `%Y-%m-%d` - 2024-01-15 (ISO format)
- `%d/%m/%Y` - 15/01/2024 (European format)
- `%m/%d/%y` - 01/15/24 (2-digit year)

**Step 4: Add to settings.yaml and run**
```bash
tally run
```

Transaction descriptions look like:
- AMEX: `"NETFLIX.COM"`, `"UBER *EATS"`, `"STARBUCKS STORE 12345 SEATTLE WA"`
- BOA: `"NETFLIX.COM DES:RECURRING ID:xxx"`, `"ZELLE TO JOHN DOE"`

### Custom Column Captures (Multi-Column Description)

Some banks split transaction info across multiple columns (e.g., "Card payment" in one column, "STARBUCKS" in another). Use **custom captures** to combine them:

```yaml
data_sources:
  - name: European Bank
    file: data/bank.csv
    format: "{date:%Y-%m-%d},{_},{txn_type},{vendor},{_},{amount}"
    columns:
      description: "{vendor} ({txn_type})"
```

**How it works:**
- Use any name (not a reserved name) in the format string: `{txn_type}`, `{vendor}`, etc.
- The `columns.description` template combines them in any order
- Reserved names that cannot be used: `{date}`, `{amount}`, `{location}`, `{description}`, `{_}`

**Example CSV:**
```csv
Date,PostDate,Type,Recipient,AccountNum,Amount,Balance
2025-01-15,2025-01-14,Card payment,STARBUCKS COFFEE,PL123,25.50,1500
```

With the format above, the description becomes: `"STARBUCKS COFFEE (Card payment)"`

**Rules:**
- Cannot mix `{description}` with custom captures - use one approach
- If using custom captures, `columns.description` template is required

## Common Tasks

### Task: User wants to analyze their spending
1. Ensure `config/settings.yaml` has correct data sources
2. Run `tally run`
3. Open the HTML report in `output/`

### Task: User has many "Unknown" transactions
1. Run `tally discover --format json` to get unknowns sorted by spend
2. For each unknown merchant, determine appropriate Category/Subcategory
3. Add patterns to `merchant_categories.csv`
4. Run `tally run --summary` to verify improvement
5. Repeat until unknowns are minimized

### Task: User wants to track a specific merchant
1. Get the exact description from their statement
2. Create a pattern that matches it
3. Add to `merchant_categories.csv` with appropriate category

### Task: User wants to separate Costco groceries from Costco gas
```csv
COSTCO\\s*GAS,Costco Gas,Transport,Gas
COSTCO(?!\\s*GAS),Costco,Food,Grocery
```
(Gas rule must come first)

## Tips

- Run `tally` with no args to see help
- Test regex patterns at regex101.com (Python flavor)
- Comments start with `#` in CSV files
- Escape special regex chars: `\\.` for literal dot, `\\*` for literal asterisk
- The tool cleans common prefixes (APLPAY, SQ*, TST*) automatically

---

## Real-World Workflow Example

Here's a typical workflow when analyzing a new year's spending data:

### 1. Check for existing config
```bash
# Look for existing configs from other years to reuse
ls ../2024/config/ ../2025/config/ 2>/dev/null
```

### 2. Examine statement file formats
The tool expects specific formats. Your files may need transformation:

**Expected AMEX format (CSV):**
```csv
Date,Description,Amount
01/15/2024,AMAZON.COM,-45.99
01/16/2024,STARBUCKS STORE 12345,-6.50
```

**Expected BOA format (TXT, space-separated):**
```
01/15/2024 NETFLIX.COM DES:RECURRING -15.99 1234.56
01/16/2024 ZELLE TO JOHN DOE -100.00 1134.56
```

### 3. Transform data if needed
If your statement files have different formats, transform them with Python:

**Example: Transform multi-line AMEX export:**
```python
import csv
with open('Amex_raw.csv', 'r') as f:
    reader = csv.reader(f)
    # Extract date, description, amount from your specific format
    # Write to clean CSV with Date,Description,Amount columns
```

**Example: Transform BOA CSV to TXT:**
```python
import csv
with open('BOA.csv', 'r') as f:
    reader = csv.DictReader(f)
    with open('data/boa_clean.txt', 'w') as out:
        for row in reader:
            # Format: MM/DD/YYYY Description Amount Balance
            out.write(f"{row['Date']} {row['Description']} {row['Amount']} {row['Balance']}\\n")
```

### 4. Copy existing merchant categories
If another year has good patterns, start with those:
```bash
cp ../2025/config/merchant_categories.csv config/
```

### 5. Run analysis and iterate
```bash
# Initial run - will have many "Unknown"
tally run

# Check what's unknown
tally run --summary --category Unknown

# Extract unique unknown patterns for analysis
python3 << 'EOF'
import csv
unknowns = {}
with open('output/transactions.csv') as f:  # or parse from summary
    # Group by description pattern, sum amounts
    pass
# Print top unknowns by spend
EOF
```

### 6. Add patterns in batches
Add patterns for the highest-spend unknowns first:

```csv
# High-value unknowns from 2024
BMWFINANCIAL|BMW FINANCIAL,BMW Financial Services,Bills,Auto Loan
ASHTON BELLEVUE,Ashton Apartments (Rent),Bills,Rent
MICROSOFT DES:EDIPAYMENT,Microsoft Payroll,Income,Salary
```

### 7. Iterate until Unknown < 5%
Re-run after each batch of patterns until categorization rate is acceptable:
```bash
tally run  # Check Unknown total
# Add more patterns
tally run  # Verify improvement
```

### Common Data Issues

**BOA files with summary headers:**
```python
# Skip header rows before the actual data
for row in reader:
    if row['Date'] and '/' in row['Date']:  # Skip summary rows
        # Process transaction
```

**AMEX multi-line format:**
Each transaction may span multiple lines. Look for date patterns to identify record boundaries.

**CHECKCARD prefix in BOA:**
BOA often prefixes with "CHECKCARD": add patterns like `CHECKCARD.*STARBUCKS`

**State/location suffixes:**
Descriptions often end with location: `STARBUCKS SEATTLE WA` - the tool handles this automatically.
'''

STARTER_CLAUDE_MD = '''# CLAUDE.md - Instructions for Claude Code

This file provides context for Claude Code when working in this budget directory.

## Project Overview

This is a personal budget analysis directory using the `tally` CLI tool.
The tool categorizes bank/credit card transactions and generates spending reports.

## Key Commands

```bash
# Run analysis (uses ./config by default)
tally run

# Show summary only (good for checking Unknown transactions)
tally run --summary

# Run with specific config directory
tally run ./path/to/config

# Initialize a new budget directory
tally init

# Discover unknown merchants (KEY FOR CLASSIFICATION)
tally discover                # Human-readable
tally discover --format json  # For programmatic use
tally discover --format csv   # Ready to copy into rules

# Inspect a CSV to determine its format
tally inspect data/file.csv
```

## Directory Structure

```
.
├── config/
│   ├── settings.yaml           # Data sources and settings
│   └── merchant_categories.csv # Pattern matching rules (MAIN FILE TO EDIT)
├── data/                       # Statement files (DO NOT commit - contains PII)
└── output/                     # Generated reports
```

## Primary Task: Classifying Unknown Merchants

When asked to improve categorization:

1. Run `tally discover --format json` to find unknown merchants sorted by spend
2. For each unknown merchant:
   - Identify what the merchant is (restaurant, store, subscription, etc.)
   - Determine appropriate Category and Subcategory
   - Create a regex pattern that matches the transaction description
3. Add rules to `config/merchant_categories.csv`
4. Run `tally run --summary` to verify improvement
5. Repeat until Unknown < 5% of total

The `discover` command provides suggested patterns and merchant names to speed up this process.

## Pattern Syntax Quick Reference

The Pattern column uses Python regex (case-insensitive):

| Pattern | Matches |
|---------|---------|
| `NETFLIX` | Contains "NETFLIX" |
| `DELTA\\|UNITED` | "DELTA" or "UNITED" |
| `UBER\\s(?!EATS)` | "UBER " not followed by "EATS" |
| `COSTCO(?!.*GAS)` | "COSTCO" without "GAS" |
| `^ATT\\s` | Starts with "ATT " |

### Inline Modifiers (Target Specific Transactions)

Add conditions to target transactions by amount or date:

```csv
COSTCO[amount>200],Costco Bulk,Shopping,Bulk
BESTBUY[amount=499.99][date=2025-01-15],TV Purchase,Shopping,Electronics
HOLIDAY[month=12],December Shopping,Shopping,Gifts
```

**Amount:** `[amount>N]`, `[amount<N]`, `[amount=N]`, `[amount:MIN-MAX]`
**Date:** `[date=YYYY-MM-DD]`, `[date:START..END]`, `[date:lastNdays]`, `[month=N]`

## Common Categories

- **Food**: Grocery, Restaurant, Fast Food, Coffee, Delivery
- **Shopping**: Online, Retail, Clothing, Electronics, Home
- **Travel**: Airline, Lodging, Car Rental
- **Transport**: Rideshare, Gas, Parking
- **Subscriptions**: Streaming, Software
- **Health**: Gym, Pharmacy, Medical
- **Bills**: Rent, Mortgage, Insurance, Utilities
- **Transfers**: P2P, CC Payment

## Travel Detection

International transactions are automatically classified as travel.
Domestic out-of-state is NOT auto-travel (opt-in via merchant rules).

To mark a domestic location as travel, add to merchant_categories.csv:
```csv
.*\\sHI$,Hawaii Trip,Travel,Hawaii
```

## Important Notes

- Statement files in `data/` contain PII - never commit or display raw contents
- First matching rule wins - put specific patterns before general ones
- Test patterns at regex101.com (Python flavor)
- The tool auto-cleans prefixes like APLPAY, SQ*, TST*

## Data Format Requirements

The tool supports multiple data formats:

### Predefined Types
- **AMEX**: CSV with Date,Description,Amount columns (`type: amex`)
- **BOA**: TXT with "MM/DD/YYYY Description Amount Balance" per line (`type: boa`)

### Custom Format Strings
For any other CSV, use the `format` field with a format string:

```yaml
data_sources:
  - name: Chase
    file: data/chase.csv
    format: "{date:%m/%d/%Y}, {_}, {description}, {_}, {amount}"
  - name: BofA Checking
    file: data/bofa.csv
    format: "{date:%m/%d/%Y}, {description}, {-amount}"  # Bank: negative = expense
```

**Format string tokens:**
- `{date:%m/%d/%Y}` - Date column (with strptime format)
- `{description}` - Description/merchant column
- `{amount}` - Amount column (positive = expense)
- `{-amount}` - Negate amounts (for bank accounts where negative = expense)
- `{location}` - Optional location column
- `{_}` - Skip column

**Sign conventions:**
- Credit cards typically show charges as positive → use `{amount}`
- Bank accounts typically show debits as negative → use `{-amount}`

Use `tally inspect <file>` to see the CSV structure before creating a format string.
'''


def find_config_dir():
    """Find the config directory, checking environment and both layouts.

    Resolution order:
    1. TALLY_CONFIG environment variable (if set and exists)
    2. ./config (old layout - config in current directory)
    3. ./tally/config (new layout - config in tally subdirectory)

    Note: Migration prompts are handled separately by run_migrations()
    during 'tally update', not here.

    Returns None if no config directory is found.
    """
    # Check environment variable first
    env_config = os.environ.get('TALLY_CONFIG')
    if env_config:
        env_path = os.path.abspath(env_config)
        if os.path.isdir(env_path):
            return env_path

    # Check old layout (backwards compatibility)
    # Note: Migration prompts are handled by run_migrations() during 'tally update'
    old_layout = os.path.abspath('config')
    if os.path.isdir(old_layout):
        return old_layout

    # Check new layout
    new_layout = os.path.abspath(os.path.join('tally', 'config'))
    if os.path.isdir(new_layout):
        return new_layout

    return None


# Schema version for asset migrations
SCHEMA_VERSION = 1


def get_schema_version(config_dir):
    """Get current schema version from config directory.

    Returns:
        int: Schema version (0 if no marker file exists - legacy layout)
    """
    schema_file = os.path.join(config_dir, '.tally-schema')
    if os.path.exists(schema_file):
        try:
            with open(schema_file) as f:
                return int(f.read().strip())
        except (ValueError, IOError):
            return 0
    return 0


def run_migrations(config_dir, skip_confirm=False):
    """Run any pending migrations on the config directory.

    Args:
        config_dir: Path to current config directory
        skip_confirm: If True, skip confirmation prompts (--yes flag)

    Returns:
        str: Path to config directory (may change if layout migrated)
    """
    current = get_schema_version(config_dir)

    if current >= SCHEMA_VERSION:
        return config_dir  # Already up to date

    # Run migrations in order
    if current < 1:
        result = migrate_v0_to_v1(config_dir, skip_confirm)
        if result:
            config_dir = result

    return config_dir


def migrate_v0_to_v1(old_config_dir, skip_confirm=False):
    """Migrate from legacy layout (./config) to new layout (./tally/config).

    Args:
        old_config_dir: Path to the old config directory
        skip_confirm: If True, skip confirmation prompt

    Returns:
        str: Path to new config directory, or None if user declined
    """
    # Only migrate if we're in the old layout (./config at working directory root)
    if os.path.basename(old_config_dir) != 'config':
        return None
    if os.path.dirname(old_config_dir) != os.getcwd():
        return None

    # Prompt user (skip if non-interactive or --yes flag)
    if not skip_confirm:
        # In non-interactive mode (e.g., LLM/CI), skip migration silently
        if not sys.stdin.isatty():
            return None

        print()
        print("Migration available: Layout update")
        print("  Current: ./config (legacy layout)")
        print("  New: ./tally/config")
        print()
        try:
            response = input("Migrate to new layout? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nSkipped.")
            return None
        if response == 'n':
            return None

    # Perform migration
    tally_dir = os.path.abspath('tally')
    try:
        os.makedirs(tally_dir, exist_ok=True)

        # Move config directory
        new_config = os.path.join(tally_dir, 'config')
        print(f"  Moving config/ → tally/config/")
        shutil.move(old_config_dir, new_config)

        # Move data and output directories if they exist
        for subdir in ['data', 'output']:
            old_path = os.path.abspath(subdir)
            if os.path.isdir(old_path):
                new_path = os.path.join(tally_dir, subdir)
                print(f"  Moving {subdir}/ → tally/{subdir}/")
                shutil.move(old_path, new_path)

        # Write schema version marker
        schema_file = os.path.join(new_config, '.tally-schema')
        with open(schema_file, 'w') as f:
            f.write('1\n')

        print("✓ Migrated to ./tally/")
        return new_config

    except (OSError, shutil.Error) as e:
        print(f"Error during migration: {e}", file=sys.stderr)
        return None


def init_config(target_dir):
    """Initialize a new config directory with starter files."""
    import datetime

    config_dir = os.path.join(target_dir, 'config')
    data_dir = os.path.join(target_dir, 'data')
    output_dir = os.path.join(target_dir, 'output')

    # Create directories
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    current_year = datetime.datetime.now().year
    files_created = []
    files_skipped = []

    # Write settings.yaml
    settings_path = os.path.join(config_dir, 'settings.yaml')
    if not os.path.exists(settings_path):
        with open(settings_path, 'w', encoding='utf-8') as f:
            f.write(STARTER_SETTINGS.format(year=current_year))
        files_created.append('config/settings.yaml')
    else:
        files_skipped.append('config/settings.yaml')

    # Write merchant_categories.csv
    merchants_path = os.path.join(config_dir, 'merchant_categories.csv')
    if not os.path.exists(merchants_path):
        with open(merchants_path, 'w', encoding='utf-8') as f:
            f.write(STARTER_MERCHANT_CATEGORIES)
        files_created.append('config/merchant_categories.csv')
    else:
        files_skipped.append('config/merchant_categories.csv')

    # Create .gitignore for data privacy
    gitignore_path = os.path.join(target_dir, '.gitignore')
    if not os.path.exists(gitignore_path):
        with open(gitignore_path, 'w', encoding='utf-8') as f:
            f.write('''# Tally - Ignore sensitive data
data/
output/
''')
        files_created.append('.gitignore')

    # Create README
    readme_path = os.path.join(target_dir, 'README.md')
    if not os.path.exists(readme_path):
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(f'''# My Budget Analysis

Budget analysis for {current_year}.

## Setup

1. Export your bank/credit card statements to `data/`
2. Update `config/settings.yaml` with your data sources
3. Add merchant rules to `config/merchant_categories.csv`
4. Run: `tally ./config`

## Documentation

Run `tally --help-config` for detailed configuration guide.
''')
        files_created.append('README.md')

    # Create AGENTS.md for AI agent instructions
    agents_path = os.path.join(target_dir, 'AGENTS.md')
    if not os.path.exists(agents_path):
        with open(agents_path, 'w', encoding='utf-8') as f:
            f.write(STARTER_AGENTS_MD)
        files_created.append('AGENTS.md')
    else:
        files_skipped.append('AGENTS.md')

    # Create CLAUDE.md for Claude Code specific instructions
    claude_path = os.path.join(target_dir, 'CLAUDE.md')
    if not os.path.exists(claude_path):
        with open(claude_path, 'w', encoding='utf-8') as f:
            f.write(STARTER_CLAUDE_MD)
        files_created.append('CLAUDE.md')
    else:
        files_skipped.append('CLAUDE.md')

    return files_created, files_skipped


def cmd_init(args):
    """Handle the 'init' subcommand."""
    target_dir = os.path.abspath(args.dir)
    print(f"Initializing budget directory: {target_dir}")
    print()

    created, skipped = init_config(target_dir)

    if created:
        print("Created:")
        for f in created:
            print(f"  {f}")

    if skipped:
        print("\nSkipped (already exist):")
        for f in skipped:
            print(f"  {f}")

    print(f"""
================================================================================
NEXT STEPS
================================================================================

1. Export your bank/credit card statements to:
   {target_dir}/data/

2. Edit settings to add your data sources:
   {target_dir}/config/settings.yaml

   Example configuration:
   ```yaml
   year: 2025
   data_sources:
     - name: AMEX
       file: data/amex-2025.csv
       type: amex
     - name: BOA Checking
       file: data/checking.txt
       type: boa
   ```

3. Run the analyzer:
   tally run

================================================================================
STATEMENT FILE FORMATS
================================================================================

AMEX (CSV):
  Export from American Express website. Expected columns:
  Date,Description,Amount
  01/15/2025,AMAZON.COM,-45.99
  01/16/2025,STARBUCKS STORE 12345,-6.50

BOA (TXT):
  Export from Bank of America. Space-separated format:
  MM/DD/YYYY Description Amount Balance
  01/15/2025 NETFLIX.COM DES:RECURRING -15.99 1234.56

================================================================================
MERCHANT CATEGORIZATION
================================================================================

Edit config/merchant_categories.csv to add patterns for your merchants.

Format: Pattern,Merchant,Category,Subcategory

Pattern Syntax (Python regex, case-insensitive):
  NETFLIX              Contains "NETFLIX"
  DELTA|SOUTHWEST      Matches "DELTA" OR "SOUTHWEST"
  UBER\\s(?!EATS)       "UBER " not followed by "EATS"
  COSTCO(?!.*GAS)      "COSTCO" without "GAS" anywhere after
  ^ATT\\s               Starts with "ATT "

Common Categories:
  Food:          Grocery, Restaurant, Fast Food, Coffee, Delivery
  Shopping:      Online, Retail, Clothing, Electronics, Home
  Travel:        Airline, Lodging, Car Rental
  Transport:     Rideshare, Gas, Parking
  Subscriptions: Streaming, Software
  Health:        Gym, Pharmacy, Medical
  Bills:         Rent, Mortgage, Insurance
  Transfers:     P2P, CC Payment

Tips:
  - First match wins - put specific patterns before general ones
  - Run 'tally run --summary' to find Unknown transactions
  - Test patterns at regex101.com (Python flavor)
  - Lines starting with # are comments

================================================================================
""")


def cmd_run(args):
    """Handle the 'run' subcommand."""
    # Determine config directory
    if args.config:
        config_dir = os.path.abspath(args.config)
    else:
        # Auto-detect config directory (supports both old and new layouts)
        config_dir = find_config_dir()

    if not config_dir or not os.path.isdir(config_dir):
        print(f"Error: Config directory not found.", file=sys.stderr)
        print(f"Looked for: ./config and ./tally/config", file=sys.stderr)
        print(f"\nRun 'tally init' to create a new budget directory.", file=sys.stderr)
        sys.exit(1)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    year = config.get('year', 2025)
    home_locations = config.get('home_locations', set())
    travel_labels = config.get('travel_labels', {})
    data_sources = config.get('data_sources', [])

    # Check for data sources early before printing anything
    if not data_sources:
        print("Error: No data sources configured", file=sys.stderr)
        print(f"\nEdit {config_dir}/{args.settings} to add your data sources.", file=sys.stderr)
        print(f"\nExample:", file=sys.stderr)
        print(f"  data_sources:", file=sys.stderr)
        print(f"    - name: AMEX", file=sys.stderr)
        print(f"      file: data/amex.csv", file=sys.stderr)
        print(f"      type: amex", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"Tally - {year}")
        print(f"Config: {config_dir}/{args.settings}")
        print()

    # Load merchant rules
    rules_file = os.path.join(config_dir, 'merchant_categories.csv')
    if os.path.exists(rules_file):
        rules = get_all_rules(rules_file)
        if not args.quiet:
            print(f"Loaded {len(rules)} categorization rules from {rules_file}")
    else:
        rules = get_all_rules()  # No rules file
        if not args.quiet:
            print(f"No merchant_categories.csv found - transactions will be categorized as Unknown")

    # Parse transactions from configured data sources
    all_txns = []

    for source in data_sources:
        filepath = os.path.join(config_dir, '..', source['file'])
        filepath = os.path.normpath(filepath)

        if not os.path.exists(filepath):
            # Try relative to config_dir parent
            filepath = os.path.join(os.path.dirname(config_dir), source['file'])

        if not os.path.exists(filepath):
            if not args.quiet:
                print(f"  {source['name']}: File not found - {source['file']}")
            continue

        # Get parser type and format spec (set by config_loader.resolve_source_format)
        parser_type = source.get('_parser_type', source.get('type', '')).lower()
        format_spec = source.get('_format_spec')

        try:
            if parser_type == 'amex':
                txns = parse_amex(filepath, rules, home_locations)
            elif parser_type == 'boa':
                txns = parse_boa(filepath, rules, home_locations)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'))
            else:
                if not args.quiet:
                    print(f"  {source['name']}: Unknown parser type '{parser_type}'")
                    print(f"    Use 'tally inspect {source['file']}' to determine format")
                continue
        except Exception as e:
            if not args.quiet:
                print(f"  {source['name']}: Error parsing - {e}")
            continue

        all_txns.extend(txns)
        if not args.quiet:
            print(f"  {source['name']}: {len(txns)} transactions")

    if not all_txns:
        print("Error: No transactions found", file=sys.stderr)
        sys.exit(1)

    # Auto-detect home location if not specified
    if not home_locations:
        from collections import Counter
        # US state codes for filtering
        us_states = {
            'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
            'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
            'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
            'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
            'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
            'DC', 'PR', 'VI', 'GU'
        }
        # Count US state locations
        location_counts = Counter(
            txn['location'] for txn in all_txns
            if txn.get('location') and txn['location'] in us_states
        )
        if location_counts:
            # Most common location is likely home
            detected_home = location_counts.most_common(1)[0][0]
            home_locations = {detected_home}
            if not args.quiet:
                print(f"Auto-detected home location: {detected_home}")
            # Update is_travel on transactions now that we know home
            from .analyzer import is_travel_location
            for txn in all_txns:
                txn['is_travel'] = is_travel_location(txn.get('location'), home_locations)

    if not args.quiet:
        print(f"\nTotal: {len(all_txns)} transactions")
        if home_locations:
            print(f"Home locations: {', '.join(sorted(home_locations))}")

    # Analyze
    stats = analyze_transactions(all_txns)

    # Parse filter options
    only_filter = None
    if args.only:
        valid_classifications = {'monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable'}
        only_filter = [c.strip() for c in args.only.split(',')]
        invalid = [c for c in only_filter if c not in valid_classifications]
        if invalid:
            print(f"Warning: Invalid classification(s) ignored: {', '.join(invalid)}", file=sys.stderr)
            print(f"  Valid options: {', '.join(sorted(valid_classifications))}", file=sys.stderr)
            only_filter = [c for c in only_filter if c in valid_classifications]
            if not only_filter:
                only_filter = None
    category_filter = args.category if hasattr(args, 'category') and args.category else None

    # Handle output format
    output_format = args.format if hasattr(args, 'format') else 'html'
    verbose = args.verbose if hasattr(args, 'verbose') else 0

    currency_format = config.get('currency_format', '${amount}')

    if output_format == 'json':
        # JSON output with reasoning
        from .analyzer import export_json
        print(export_json(stats, verbose=verbose, only=only_filter, category_filter=category_filter))
    elif output_format == 'markdown':
        # Markdown output with reasoning
        from .analyzer import export_markdown
        print(export_markdown(stats, verbose=verbose, only=only_filter, category_filter=category_filter))
    elif output_format == 'summary' or args.summary:
        # Text summary only (no HTML)
        print_summary(stats, year=year, currency_format=currency_format)
    else:
        # HTML output (default)
        # Print summary first
        if not args.quiet:
            print_summary(stats, year=year, currency_format=currency_format)

        # Determine output path
        if args.output:
            output_path = args.output
        else:
            output_dir = os.path.join(os.path.dirname(config_dir), config.get('output_dir', 'output'))
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, config.get('html_filename', 'spending_summary.html'))

        write_summary_file(stats, output_path, year=year, home_locations=home_locations, currency_format=currency_format)
        if not args.quiet:
            # Make the path clickable using OSC 8 hyperlink escape sequence
            abs_path = os.path.abspath(output_path)
            file_url = f"file://{abs_path}"
            # OSC 8 format: \033]8;;URL\033\\text\033]8;;\033\\
            clickable_path = f"\033]8;;{file_url}\033\\{output_path}\033]8;;\033\\"
            print(f"\nHTML report: {clickable_path}")


def cmd_discover(args):
    """Handle the 'discover' subcommand - find unknown merchants for rule creation."""
    from collections import Counter, defaultdict
    import re

    # Determine config directory
    if args.config:
        config_dir = os.path.abspath(args.config)
    else:
        config_dir = find_config_dir()

    if not config_dir or not os.path.isdir(config_dir):
        print(f"Error: Config directory not found.", file=sys.stderr)
        print(f"Looked for: ./config and ./tally/config", file=sys.stderr)
        print(f"\nRun 'tally init' to create a new budget directory.", file=sys.stderr)
        sys.exit(1)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    home_locations = config.get('home_locations', set())
    data_sources = config.get('data_sources', [])

    if not data_sources:
        print("Error: No data sources configured", file=sys.stderr)
        print(f"\nEdit {config_dir}/{args.settings} to add your data sources.", file=sys.stderr)
        print(f"\nExample:", file=sys.stderr)
        print(f"  data_sources:", file=sys.stderr)
        print(f"    - name: AMEX", file=sys.stderr)
        print(f"      file: data/amex.csv", file=sys.stderr)
        print(f"      type: amex", file=sys.stderr)
        sys.exit(1)

    # Load merchant rules
    rules_file = os.path.join(config_dir, 'merchant_categories.csv')
    if os.path.exists(rules_file):
        rules = get_all_rules(rules_file)
    else:
        rules = get_all_rules()

    # Parse transactions from configured data sources
    all_txns = []

    for source in data_sources:
        filepath = os.path.join(config_dir, '..', source['file'])
        filepath = os.path.normpath(filepath)

        if not os.path.exists(filepath):
            filepath = os.path.join(os.path.dirname(config_dir), source['file'])

        if not os.path.exists(filepath):
            continue

        parser_type = source.get('_parser_type', source.get('type', '')).lower()
        format_spec = source.get('_format_spec')

        try:
            if parser_type == 'amex':
                txns = parse_amex(filepath, rules, home_locations)
            elif parser_type == 'boa':
                txns = parse_boa(filepath, rules, home_locations)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'))
            else:
                continue
        except Exception:
            continue

        all_txns.extend(txns)

    if not all_txns:
        print("Error: No transactions found", file=sys.stderr)
        sys.exit(1)

    # Find unknown transactions
    unknown_txns = [t for t in all_txns if t.get('category') == 'Unknown']

    if not unknown_txns:
        print("No unknown transactions found! All merchants are categorized.")
        sys.exit(0)

    # Group by raw description and calculate stats
    desc_stats = defaultdict(lambda: {'count': 0, 'total': 0.0, 'examples': []})

    for txn in unknown_txns:
        raw = txn.get('raw_description', txn.get('description', ''))
        amount = abs(txn.get('amount', 0))
        desc_stats[raw]['count'] += 1
        desc_stats[raw]['total'] += amount
        if len(desc_stats[raw]['examples']) < 3:
            desc_stats[raw]['examples'].append(txn)

    # Sort by total spend (descending)
    sorted_descs = sorted(desc_stats.items(), key=lambda x: x[1]['total'], reverse=True)

    # Limit output
    limit = args.limit
    if limit > 0:
        sorted_descs = sorted_descs[:limit]

    # Output format
    if args.format == 'csv':
        # CSV output for easy import
        print("# Suggested rules for unknown merchants")
        print("# Copy the lines you want to merchant_categories.csv")
        print("Pattern,Merchant,Category,Subcategory")
        print()

        for raw_desc, stats in sorted_descs:
            # Generate a suggested pattern
            pattern = suggest_pattern(raw_desc)
            # Generate a clean merchant name
            merchant = suggest_merchant_name(raw_desc)
            # Placeholder category - agent should fill in
            print(f"{pattern},{merchant},CATEGORY,SUBCATEGORY  # ${stats['total']:.2f} ({stats['count']} txns)")

    elif args.format == 'json':
        import json
        output = []
        for raw_desc, stats in sorted_descs:
            output.append({
                'raw_description': raw_desc,
                'suggested_pattern': suggest_pattern(raw_desc),
                'suggested_merchant': suggest_merchant_name(raw_desc),
                'count': stats['count'],
                'total_spend': round(stats['total'], 2),
                'examples': [
                    {
                        'date': str(t.get('date', '')),
                        'amount': t.get('amount', 0),
                        'description': t.get('description', '')
                    }
                    for t in stats['examples']
                ]
            })
        print(json.dumps(output, indent=2))

    else:
        # Default: human-readable format
        print(f"UNKNOWN MERCHANTS - Top {len(sorted_descs)} by spend")
        print("=" * 80)
        print(f"Total unknown: {len(unknown_txns)} transactions, ${sum(s['total'] for _, s in desc_stats.items()):.2f}")
        print()

        for i, (raw_desc, stats) in enumerate(sorted_descs, 1):
            pattern = suggest_pattern(raw_desc)
            merchant = suggest_merchant_name(raw_desc)

            print(f"{i}. {raw_desc[:60]}")
            print(f"   Count: {stats['count']} | Total: ${stats['total']:.2f}")
            print(f"   Suggested pattern: {pattern}")
            print(f"   Suggested merchant: {merchant}")
            print(f"   CSV: {pattern},{merchant},CATEGORY,SUBCATEGORY")
            print()


def suggest_pattern(description):
    """Generate a suggested regex pattern from a raw description."""
    import re

    desc = description.upper()

    # Remove common suffixes that vary
    desc = re.sub(r'\s+\d{4,}.*$', '', desc)  # Remove trailing numbers (store IDs)
    desc = re.sub(r'\s+[A-Z]{2}$', '', desc)  # Remove trailing state codes
    desc = re.sub(r'\s+\d{5}$', '', desc)  # Remove zip codes
    desc = re.sub(r'\s+#\d+', '', desc)  # Remove store numbers like #1234

    # Remove common prefixes
    prefixes = ['APLPAY ', 'SQ *', 'TST*', 'SP ', 'PP*', 'GOOGLE *']
    for prefix in prefixes:
        if desc.startswith(prefix):
            desc = desc[len(prefix):]

    # Clean up
    desc = desc.strip()

    # Escape regex special characters but keep it readable
    # Only escape characters that are common in descriptions
    pattern = re.sub(r'([.*+?^${}()|[\]\\])', r'\\\1', desc)

    # Simplify: take first 2-3 significant words
    words = pattern.split()[:3]
    if words:
        pattern = r'\s*'.join(words)

    return pattern


def suggest_merchant_name(description):
    """Generate a clean merchant name from a raw description."""
    import re

    desc = description

    # Remove common prefixes
    prefixes = ['APLPAY ', 'SQ *', 'TST*', 'TST* ', 'SP ', 'PP*', 'GOOGLE *']
    for prefix in prefixes:
        if desc.upper().startswith(prefix.upper()):
            desc = desc[len(prefix):]

    # Remove trailing IDs, numbers, locations
    desc = re.sub(r'\s+\d{4,}.*$', '', desc)
    desc = re.sub(r'\s+[A-Z]{2}$', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+\d{5}$', '', desc)
    desc = re.sub(r'\s+#\d+', '', desc)
    desc = re.sub(r'\s+DES:.*$', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+ID:.*$', '', desc, flags=re.IGNORECASE)

    # Take first few words and title case
    words = desc.split()[:3]
    if words:
        return ' '.join(words).title()

    return 'Unknown'


def cmd_inspect(args):
    """Handle the 'inspect' subcommand - show CSV structure and sample rows."""
    import csv

    if not args.file:
        print("Error: No file specified", file=sys.stderr)
        print("\nUsage: tally inspect <file.csv>", file=sys.stderr)
        print("\nExample:", file=sys.stderr)
        print("  tally inspect data/transactions.csv", file=sys.stderr)
        sys.exit(1)

    filepath = os.path.abspath(args.file)

    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    num_rows = args.rows

    print(f"Inspecting: {filepath}")
    print("=" * 70)

    with open(filepath, 'r', encoding='utf-8') as f:
        # Detect if it's a valid CSV
        try:
            sample = f.read(4096)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample)
            has_header = csv.Sniffer().has_header(sample)
            f.seek(0)
        except csv.Error:
            print("Warning: Could not detect CSV dialect, using default")
            dialect = None
            has_header = True
            f.seek(0)

        reader = csv.reader(f, dialect) if dialect else csv.reader(f)

        rows = []
        for i, row in enumerate(reader):
            rows.append(row)
            if i >= num_rows:  # Get header + N data rows
                break

        if not rows:
            print("File appears to be empty.")
            return

    # Display header info
    if has_header and rows:
        print("\nDetected Headers:")
        print("-" * 70)
        for idx, col in enumerate(rows[0]):
            print(f"  Column {idx}: {col}")

    # Display sample data
    print(f"\nSample Data (first {min(num_rows, len(rows)-1)} rows):")
    print("-" * 70)

    data_rows = rows[1:] if has_header else rows
    for row_num, row in enumerate(data_rows[:num_rows], start=1):
        print(f"\nRow {row_num}:")
        for idx, val in enumerate(row):
            header = rows[0][idx] if has_header and idx < len(rows[0]) else f"Col {idx}"
            # Truncate long values
            display_val = val[:50] + "..." if len(val) > 50 else val
            print(f"  [{idx}] {header}: {display_val}")

    # Attempt auto-detection
    print("\n" + "=" * 70)
    print("Auto-Detection Results:")
    print("-" * 70)

    try:
        spec = auto_detect_csv_format(filepath)
        print("  Successfully detected format!")
        print(f"  - Date column: {spec.date_column} (format: {spec.date_format})")
        print(f"  - Description column: {spec.description_column}")
        print(f"  - Amount column: {spec.amount_column}")
        if spec.location_column is not None:
            print(f"  - Location column: {spec.location_column}")

        # Build suggested format string
        max_col = max(spec.date_column, spec.description_column, spec.amount_column)
        if spec.location_column is not None:
            max_col = max(max_col, spec.location_column)

        cols = []
        for i in range(max_col + 1):
            if i == spec.date_column:
                cols.append(f'{{date:{spec.date_format}}}')
            elif i == spec.description_column:
                cols.append('{description}')
            elif i == spec.amount_column:
                cols.append('{amount}')
            elif spec.location_column is not None and i == spec.location_column:
                cols.append('{location}')
            else:
                cols.append('{_}')

        format_str = ', '.join(cols)
        print(f"\n  Suggested format string:")
        print(f'    format: "{format_str}"')

    except ValueError as e:
        print(f"  Could not auto-detect: {e}")
        print("\n  Use a manual format string. Example:")
        print('    format: "{date:%m/%d/%Y}, {description}, {amount}"')

    print()


def cmd_diag(args):
    """Handle the 'diag' subcommand - show diagnostic information about config and rules."""
    import json as json_module

    # Determine config directory
    if args.config:
        config_dir = os.path.abspath(args.config)
    else:
        config_dir = find_config_dir() or os.path.abspath('config')

    print("BUDGET ANALYZER DIAGNOSTICS")
    print("=" * 70)
    print()

    # Config directory info
    print("CONFIGURATION")
    print("-" * 70)
    print(f"Config directory: {config_dir}")
    print(f"  Exists: {os.path.isdir(config_dir)}")
    print()

    if not os.path.isdir(config_dir):
        print("ERROR: Config directory not found!")
        print("Run 'tally init' to create a new budget directory.")
        sys.exit(1)

    # Settings file
    settings_path = os.path.join(config_dir, args.settings)
    print(f"Settings file: {settings_path}")
    print(f"  Exists: {os.path.exists(settings_path)}")

    config = None
    if os.path.exists(settings_path):
        try:
            config = load_config(config_dir, args.settings)
            print(f"  Loaded successfully: Yes")
            print(f"  Year: {config.get('year', 'not set')}")
            print(f"  Output dir: {config.get('output_dir', 'not set')}")
            home_locs = config.get('home_locations', set())
            print(f"  Home locations: {', '.join(sorted(home_locs)) if home_locs else 'auto-detect'}")
            currency_fmt = config.get('currency_format', '${amount}')
            from .analyzer import format_currency
            print(f"  Currency format: {currency_fmt}")
            print(f"    Example: {format_currency(1234, currency_fmt)}")
        except Exception as e:
            print(f"  Loaded successfully: No")
            print(f"  Error: {e}")
    print()

    # Data sources
    if config and config.get('data_sources'):
        print("DATA SOURCES")
        print("-" * 70)
        for i, source in enumerate(config['data_sources'], 1):
            filepath = os.path.join(config_dir, '..', source['file'])
            filepath = os.path.normpath(filepath)
            if not os.path.exists(filepath):
                filepath = os.path.join(os.path.dirname(config_dir), source['file'])

            print(f"  {i}. {source.get('name', 'unnamed')}")
            print(f"     File: {source['file']}")
            print(f"     Exists: {os.path.exists(filepath)}")
            if source.get('type'):
                print(f"     Type: {source['type']}")
            if source.get('format'):
                print(f"     Format: {source['format']}")

            # Show format spec details if available
            format_spec = source.get('_format_spec')
            if format_spec:
                print(f"     Columns:")
                print(f"       date: column {format_spec.date_column} (format: {format_spec.date_format})")
                print(f"       amount: column {format_spec.amount_column}")
                if format_spec.description_column is not None:
                    print(f"       description: column {format_spec.description_column}")
                if format_spec.custom_captures:
                    for name, col in format_spec.custom_captures.items():
                        print(f"       {name}: column {col} (custom capture)")
                if format_spec.description_template:
                    print(f"     Description template: {format_spec.description_template}")
                if format_spec.location_column is not None:
                    print(f"       location: column {format_spec.location_column}")
                if format_spec.negate_amount:
                    print(f"     Amount negation: enabled")
            print()

    # Merchant rules diagnostics
    print("MERCHANT RULES")
    print("-" * 70)

    rules_path = os.path.join(config_dir, 'merchant_categories.csv')
    diag = diagnose_rules(rules_path)

    print(f"User rules file: {diag['user_rules_path']}")
    print(f"  Exists: {diag['user_rules_exists']}")

    if diag['user_rules_exists']:
        print(f"  File size: {diag.get('file_size_bytes', 0)} bytes")
        print(f"  Total lines: {diag.get('file_lines', 0)}")
        print(f"  Non-comment lines: {diag.get('non_comment_lines', 0)}")
        print(f"  Has valid header: {diag.get('has_header', 'unknown')}")
        print(f"  Rules loaded: {diag['user_rules_count']}")

        if diag['user_rules_errors']:
            print()
            print("  ERRORS/WARNINGS:")
            for err in diag['user_rules_errors']:
                print(f"    - {err}")

        if diag['user_rules']:
            print()
            print("  USER RULES (all):")
            for pattern, merchant, category, subcategory in diag['user_rules']:
                print(f"    {pattern}")
                print(f"      -> {merchant} | {category} > {subcategory}")
    else:
        print()
        print("  No merchant_categories.csv found.")
        print("  Transactions will be categorized as 'Unknown'.")
    print()

    print(f"Total rules: {diag['total_rules']}")
    print()

    # JSON output option
    if args.format == 'json':
        print("JSON OUTPUT")
        print("-" * 70)
        output = {
            'config_dir': config_dir,
            'config_dir_exists': os.path.isdir(config_dir),
            'settings_file': settings_path,
            'settings_exists': os.path.exists(settings_path),
            'data_sources': [],
            'rules': {
                'user_rules_path': diag['user_rules_path'],
                'user_rules_exists': diag['user_rules_exists'],
                'user_rules_count': diag['user_rules_count'],
                'user_rules': [
                    {'pattern': p, 'merchant': m, 'category': c, 'subcategory': s}
                    for p, m, c, s in diag['user_rules']
                ],
                'errors': diag['user_rules_errors'],
                'total_rules': diag['total_rules'],
            }
        }
        if config and config.get('data_sources'):
            for source in config['data_sources']:
                filepath = os.path.join(os.path.dirname(config_dir), source['file'])
                output['data_sources'].append({
                    'name': source.get('name'),
                    'file': source['file'],
                    'exists': os.path.exists(filepath),
                    'type': source.get('type'),
                    'format': source.get('format'),
                })
        print(json_module.dumps(output, indent=2))


def cmd_update(args):
    """Handle the update command."""
    print("Checking for updates...")

    # Get release info (may fail if offline or rate-limited)
    release_info = get_latest_release_info()
    has_update = False

    if release_info:
        latest = release_info['version']
        current = VERSION

        # Show version comparison
        from ._version import _version_greater
        has_update = _version_greater(latest, current)

        if has_update:
            print(f"New version available: v{latest} (current: v{current})")
        else:
            print(f"Already on latest version: v{current}")
    else:
        print("Could not check for version updates (network issue?)")

    # If --check only, just show status and exit
    if args.check:
        if has_update:
            print(f"\nRun 'tally update' to install the update.")
        sys.exit(0)

    # Check for migrations (layout updates, etc.)
    # This runs even if version check failed
    config_dir = find_config_dir()
    did_migrate = False
    if config_dir:
        old_config = config_dir
        new_config = run_migrations(config_dir, skip_confirm=args.yes)
        if new_config and new_config != old_config:
            did_migrate = True

    # Handle --assets flag (update AGENTS.md and CLAUDE.md)
    if args.assets:
        update_assets(args.yes)

    # Skip binary update if no update available
    if not has_update:
        if not args.assets and not did_migrate:
            print("\nNothing to update.")
        sys.exit(0)

    # Check if running from source (can't self-update)
    import sys as _sys
    if not getattr(_sys, 'frozen', False):
        if args.assets:
            # Already updated assets, just note that binary update isn't possible from source
            print("\nNote: Binary self-update not available when running from source.")
        else:
            print(f"\n✗ Cannot self-update when running from source. Use: uv tool upgrade tally")
            sys.exit(1)
        sys.exit(0)

    # Perform binary update
    print()
    success, message = perform_update(release_info)

    if success:
        print(f"\n✓ {message}")
        print("\nRestart tally to use the new version.")
    else:
        print(f"\n✗ {message}")
        sys.exit(1)


def update_assets(skip_confirm: bool = False):
    """Update AGENTS.md and CLAUDE.md in current directory."""
    from pathlib import Path

    target_dir = Path.cwd()
    assets = [
        ('AGENTS.md', STARTER_AGENTS_MD),
        ('CLAUDE.md', STARTER_CLAUDE_MD),
    ]

    print("\nUpdating assets in current directory...")

    for filename, content in assets:
        path = target_dir / filename
        if path.exists() and not skip_confirm:
            print(f"\nWarning: {filename} exists and will be overwritten.")
            try:
                confirm = input("Continue? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled.")
                return
            if confirm != 'y':
                print(f"Skipping {filename}")
                continue

        path.write_text(content)
        print(f"✓ Updated {filename}")


def cmd_explain(args):
    """Handle the 'explain' subcommand - explain merchant classifications."""
    from difflib import get_close_matches
    from .analyzer import export_json, export_markdown, build_merchant_json

    # Determine config directory
    # Check if first merchant arg looks like a config path
    config_dir = None
    merchant_names = args.merchant if args.merchant else []

    if merchant_names and os.path.isdir(merchant_names[-1]):
        # Last arg is a directory, treat it as config
        config_dir = os.path.abspath(merchant_names[-1])
        merchant_names = merchant_names[:-1]
    elif args.config:
        config_dir = os.path.abspath(args.config)
    else:
        config_dir = find_config_dir()

    if not config_dir or not os.path.isdir(config_dir):
        print(f"Error: Config directory not found.", file=sys.stderr)
        print(f"Looked for: ./config and ./tally/config", file=sys.stderr)
        print(f"\nRun 'tally init' to create a new budget directory.", file=sys.stderr)
        sys.exit(1)

    # Load configuration
    try:
        config = load_config(config_dir, args.settings)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    home_locations = config.get('home_locations', set())
    data_sources = config.get('data_sources', [])

    if not data_sources:
        print("Error: No data sources configured", file=sys.stderr)
        sys.exit(1)

    # Load merchant rules
    rules_file = os.path.join(config_dir, 'merchant_categories.csv')
    if os.path.exists(rules_file):
        rules = get_all_rules(rules_file)
    else:
        rules = get_all_rules()

    # Parse transactions (quietly)
    all_txns = []
    for source in data_sources:
        filepath = os.path.join(config_dir, '..', source['file'])
        filepath = os.path.normpath(filepath)
        if not os.path.exists(filepath):
            filepath = os.path.join(os.path.dirname(config_dir), source['file'])
        if not os.path.exists(filepath):
            continue

        parser_type = source.get('_parser_type', source.get('type', '')).lower()
        format_spec = source.get('_format_spec')

        try:
            if parser_type == 'amex':
                txns = parse_amex(filepath, rules, home_locations)
            elif parser_type == 'boa':
                txns = parse_boa(filepath, rules, home_locations)
            elif parser_type == 'generic' and format_spec:
                txns = parse_generic_csv(filepath, format_spec, rules,
                                         home_locations,
                                         source_name=source.get('name', 'CSV'),
                                         decimal_separator=source.get('decimal_separator', '.'))
            else:
                continue
        except Exception:
            continue

        all_txns.extend(txns)

    if not all_txns:
        print("Error: No transactions found", file=sys.stderr)
        sys.exit(1)

    # Analyze
    stats = analyze_transactions(all_txns)

    # Get all merchants from all classifications
    all_merchants = {}
    for section in ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']:
        merchants_dict = stats.get(f'{section}_merchants', {})
        for name, data in merchants_dict.items():
            all_merchants[name] = data

    verbose = args.verbose

    # Handle output based on what was requested
    if merchant_names:
        # Explain specific merchants
        found_any = False
        for merchant_query in merchant_names:
            # Try exact match first
            if merchant_query in all_merchants:
                found_any = True
                _print_merchant_explanation(merchant_query, all_merchants[merchant_query], args.format, verbose, stats['num_months'])
            else:
                # Try case-insensitive match
                matches = [m for m in all_merchants.keys() if m.lower() == merchant_query.lower()]
                if matches:
                    found_any = True
                    _print_merchant_explanation(matches[0], all_merchants[matches[0]], args.format, verbose, stats['num_months'])
                else:
                    # Try fuzzy match
                    close_matches = get_close_matches(merchant_query, list(all_merchants.keys()), n=3, cutoff=0.6)
                    if close_matches:
                        print(f"No merchant matching '{merchant_query}'. Did you mean:", file=sys.stderr)
                        for m in close_matches:
                            print(f"  - {m}", file=sys.stderr)
                    else:
                        print(f"No merchant matching '{merchant_query}'", file=sys.stderr)

        if not found_any:
            sys.exit(1)

    elif args.classification:
        # Show all merchants in a specific classification
        section = args.classification
        merchants_dict = stats.get(f'{section}_merchants', {})
        if not merchants_dict:
            print(f"No merchants in classification '{section}'")
            sys.exit(0)

        if args.format == 'json':
            import json
            merchants = [build_merchant_json(name, data, verbose) for name, data in merchants_dict.items()]
            merchants.sort(key=lambda x: x['monthly_value'], reverse=True)
            print(json.dumps({'classification': section, 'merchants': merchants}, indent=2))
        elif args.format == 'markdown':
            print(export_markdown(stats, verbose=verbose, only=[section], category_filter=args.category))
        else:
            # Text format
            _print_classification_summary(section, merchants_dict, verbose, stats['num_months'])

    elif args.category:
        # Filter by category across all classifications
        if args.format == 'json':
            print(export_json(stats, verbose=verbose, category_filter=args.category))
        elif args.format == 'markdown':
            print(export_markdown(stats, verbose=verbose, category_filter=args.category))
        else:
            # Text format - show all merchants in category
            print(f"Merchants in category: {args.category}\n")
            found_any = False
            for section in ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']:
                merchants_dict = stats.get(f'{section}_merchants', {})
                section_merchants = {k: v for k, v in merchants_dict.items() if v.get('category') == args.category}
                if section_merchants:
                    found_any = True
                    _print_classification_summary(section, section_merchants, verbose, stats['num_months'])
            if not found_any:
                # Suggest categories that do exist
                all_categories = set()
                for section in ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']:
                    for data in stats.get(f'{section}_merchants', {}).values():
                        if data.get('category'):
                            all_categories.add(data['category'])
                print(f"No merchants found in category '{args.category}'")
                if all_categories:
                    print(f"\nAvailable categories: {', '.join(sorted(all_categories))}")

    else:
        # No specific merchant - show classification summary
        _print_explain_summary(stats, verbose)


def _print_merchant_explanation(name, data, output_format, verbose, num_months):
    """Print explanation for a single merchant."""
    import json
    from .analyzer import build_merchant_json

    if output_format == 'json':
        print(json.dumps(build_merchant_json(name, data, verbose), indent=2))
    elif output_format == 'markdown':
        reasoning = data.get('reasoning', {})
        print(f"## {name}")
        print(f"**Classification:** {data.get('classification', 'unknown').replace('_', ' ').title()}")
        print(f"**Reason:** {reasoning.get('decision', 'N/A')}")
        print(f"**Category:** {data.get('category', '')} > {data.get('subcategory', '')}")
        print(f"**Monthly Value:** ${data.get('monthly_value', 0):.2f}")
        print(f"**YTD Total:** ${data.get('total', 0):.2f}")
        print(f"**Months Active:** {data.get('months_active', 0)}/{num_months}")

        if verbose >= 1:
            trace = reasoning.get('trace', [])
            if trace:
                print('\n**Decision Trace:**')
                for i, step in enumerate(trace, 1):
                    print(f"  {i}. {step}")

        if verbose >= 2:
            print(f"\n**Calculation:** {data.get('calc_type', '')} ({data.get('calc_reasoning', '')})")
            print(f"  Formula: {data.get('calc_formula', '')}")

        # Show pattern match info
        match_info = data.get('match_info')
        if match_info:
            pattern = match_info.get('pattern', '')
            source = match_info.get('source', 'unknown')
            print(f"\n**Pattern:** `{pattern}` ({source})")
        print()
    else:
        # Text format
        classification = data.get('classification', 'unknown').replace('_', ' ').title()
        reasoning = data.get('reasoning', {})
        print(f"{name} → {classification}")
        print(f"  {reasoning.get('decision', 'N/A')}")

        if verbose >= 1:
            trace = reasoning.get('trace', [])
            if trace:
                print()
                print("  Decision trace:")
                for step in trace:
                    print(f"    {step}")

        if verbose >= 2:
            print()
            print(f"  Calculation: {data.get('calc_type', '')} ({data.get('calc_reasoning', '')})")
            print(f"    Formula: {data.get('calc_formula', '')}")
            print(f"    CV: {reasoning.get('cv', 0):.2f}")

        # Show pattern match info (always show if available)
        match_info = data.get('match_info')
        if match_info:
            pattern = match_info.get('pattern', '')
            source = match_info.get('source', 'unknown')
            print(f"\n  Rule: {pattern} ({source})")
        print()


def _print_classification_summary(section, merchants_dict, verbose, num_months):
    """Print summary of merchants in a classification."""
    section_name = section.replace('_', ' ').title()
    print(f"{section_name} ({len(merchants_dict)} merchants)")
    print("-" * 50)

    sorted_merchants = sorted(merchants_dict.items(), key=lambda x: x[1].get('monthly_value', 0), reverse=True)
    for name, data in sorted_merchants:
        reasoning = data.get('reasoning', {})
        category = data.get('category', '')
        months = data.get('months_active', 0)

        # Short reason
        decision = reasoning.get('decision', '')
        short_reason = f"{category} ({months}/{num_months} months)"

        print(f"  {name:<24} {short_reason}")

        if verbose >= 1:
            trace = reasoning.get('trace', [])
            if trace:
                for step in trace:
                    print(f"    {step}")
            print()

    print()


def _print_explain_summary(stats, verbose):
    """Print overview summary of all classifications with brief reasons."""
    section_names = {
        'monthly': 'Monthly Recurring',
        'annual': 'Annual Bills',
        'periodic': 'Periodic Recurring',
        'travel': 'Travel',
        'one_off': 'One-Off',
        'variable': 'Variable/Discretionary',
    }

    print("Classification Summary")
    print("=" * 60)
    print()

    num_months = stats['num_months']

    for section in ['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable']:
        merchants_dict = stats.get(f'{section}_merchants', {})
        if not merchants_dict:
            continue

        section_name = section_names.get(section, section)
        print(f"{section_name} ({len(merchants_dict)} merchants)")

        sorted_merchants = sorted(merchants_dict.items(), key=lambda x: x[1].get('monthly_value', 0), reverse=True)

        # Show top 5 or all if verbose
        display_count = len(sorted_merchants) if verbose >= 1 else min(5, len(sorted_merchants))

        for name, data in sorted_merchants[:display_count]:
            category = data.get('category', '')
            months = data.get('months_active', 0)
            cv = data.get('cv', 0)

            # Short classification hint
            if data.get('is_consistent', True):
                consistency = "consistent"
            else:
                consistency = "varies"

            print(f"  {name:<26} {category} ({months}/{num_months} months, {consistency})")

        if len(sorted_merchants) > display_count:
            remaining = len(sorted_merchants) - display_count
            print(f"  ... and {remaining} more")

        print()

    print("Run `tally explain <merchant>` for detailed reasoning.")
    print("Run `tally explain -v` for full details on all merchants.")


def main():
    """Main entry point for tally CLI."""
    parser = argparse.ArgumentParser(
        prog='tally',
        description='Let AI classify your transactions - LLM-powered spending categorization.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  tally init                 Initialize in ./tally directory
  tally init ./my-budget     Initialize in specified directory
  tally run                  Run analysis (uses ./config)
  tally run ./my-budget/config   Run with specific config
  tally run --summary        Show summary only, no HTML report
  tally inspect data/bank.csv  Inspect CSV to determine format
  tally discover             Find unknown merchants, suggest rules
  tally discover --format json  JSON output for agents
  tally diag                  Show diagnostic info about rules
  tally diag --format json    JSON output for agents
  tally update               Update tally to latest version
  tally update --check       Check for updates without installing
  tally update --assets      Update AGENTS.md and CLAUDE.md
'''
    )

    subparsers = parser.add_subparsers(dest='command', title='commands', metavar='<command>')

    # init subcommand
    init_parser = subparsers.add_parser(
        'init',
        help='Set up a new budget folder with config files (run once to get started)',
        description='Initialize a new budget directory with settings, merchant categories, and documentation.'
    )
    init_parser.add_argument(
        'dir',
        nargs='?',
        default='tally',
        help='Directory to initialize (default: ./tally)'
    )

    # run subcommand
    run_parser = subparsers.add_parser(
        'run',
        help='Parse transactions, categorize them, and generate HTML spending report',
        description='Run the budget analyzer on your transaction data.'
    )
    run_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    run_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    run_parser.add_argument(
        '--summary',
        action='store_true',
        help='Print summary only, do not generate HTML'
    )
    run_parser.add_argument(
        '--output', '-o',
        help='Override output file path'
    )
    run_parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Minimal output'
    )
    run_parser.add_argument(
        '--format', '-f',
        choices=['html', 'json', 'markdown', 'summary'],
        default='html',
        help='Output format: html (default), json (with reasoning), markdown, summary (text)'
    )
    run_parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='Increase output verbosity (use -v for trace, -vv for full details)'
    )
    run_parser.add_argument(
        '--only',
        help='Filter to specific classifications (comma-separated: monthly,variable,travel)'
    )
    run_parser.add_argument(
        '--category',
        help='Filter to specific category'
    )

    # inspect subcommand
    inspect_parser = subparsers.add_parser(
        'inspect',
        help='Show CSV columns and sample data to help build a format string',
        description='Show headers and sample rows from a CSV file, with auto-detection suggestions.'
    )
    inspect_parser.add_argument(
        'file',
        nargs='?',
        help='Path to the CSV file to inspect'
    )
    inspect_parser.add_argument(
        '--rows', '-n',
        type=int,
        default=5,
        help='Number of sample rows to display (default: 5)'
    )

    # discover subcommand
    discover_parser = subparsers.add_parser(
        'discover',
        help='List uncategorized transactions with suggested patterns (use --format json for LLMs)',
        description='Analyze transactions to find unknown merchants, sorted by spend. '
                    'Outputs suggested patterns for merchant_categories.csv.'
    )
    discover_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    discover_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    discover_parser.add_argument(
        '--limit', '-n',
        type=int,
        default=20,
        help='Maximum number of unknown merchants to show (default: 20, 0 for all)'
    )
    discover_parser.add_argument(
        '--format', '-f',
        choices=['text', 'csv', 'json'],
        default='text',
        help='Output format: text (human readable), csv (for import), json (for agents)'
    )

    # diag subcommand
    diag_parser = subparsers.add_parser(
        'diag',
        help='Debug config issues: show loaded rules, data sources, and errors',
        description='Display detailed diagnostic info to help troubleshoot rule loading issues.'
    )
    diag_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    diag_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    diag_parser.add_argument(
        '--format', '-f',
        choices=['text', 'json'],
        default='text',
        help='Output format: text (human readable), json (for agents)'
    )

    # explain subcommand
    explain_parser = subparsers.add_parser(
        'explain',
        help='Explain why merchants are classified the way they are',
        description='Show classification reasoning for merchants. '
                    'Runs analysis on-the-fly and explains the decision process.'
    )
    explain_parser.add_argument(
        'merchant',
        nargs='*',
        help='Merchant name(s) to explain (optional, shows summary if omitted)'
    )
    explain_parser.add_argument(
        'config',
        nargs='?',
        help='Path to config directory (default: ./config)'
    )
    explain_parser.add_argument(
        '--settings', '-s',
        default='settings.yaml',
        help='Settings file name (default: settings.yaml)'
    )
    explain_parser.add_argument(
        '--format', '-f',
        choices=['text', 'json', 'markdown'],
        default='text',
        help='Output format: text (default), json, markdown'
    )
    explain_parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='Increase output verbosity (use -v for trace, -vv for full details)'
    )
    explain_parser.add_argument(
        '--classification', '-c',
        choices=['monthly', 'annual', 'periodic', 'travel', 'one_off', 'variable'],
        help='Show all merchants in a specific classification'
    )
    explain_parser.add_argument(
        '--category',
        help='Filter to specific category'
    )

    # version subcommand
    subparsers.add_parser(
        'version',
        help='Show version information',
        description='Display tally version and build information.'
    )

    # update subcommand
    update_parser = subparsers.add_parser(
        'update',
        help='Update tally to the latest version',
        description='Download and install the latest tally release, optionally update assets.'
    )
    update_parser.add_argument(
        '--check',
        action='store_true',
        help='Check for updates without installing'
    )
    update_parser.add_argument(
        '--assets',
        action='store_true',
        help='Update AGENTS.md and CLAUDE.md in current directory (will prompt before overwriting)'
    )
    update_parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Skip confirmation prompts for asset updates'
    )

    args = parser.parse_args()

    # If no command specified, show help with banner
    if args.command is None:
        print(BANNER)
        parser.print_help()

        # Check for updates
        update_info = check_for_updates()
        if update_info and update_info.get('update_available'):
            print()
            print(f"Update available: v{update_info['latest_version']} (current: v{update_info['current_version']})")
            print(f"  Run 'tally update' to install")

        sys.exit(0)

    # Dispatch to command handler
    if args.command == 'init':
        cmd_init(args)
    elif args.command == 'run':
        cmd_run(args)
    elif args.command == 'inspect':
        cmd_inspect(args)
    elif args.command == 'discover':
        cmd_discover(args)
    elif args.command == 'diag':
        cmd_diag(args)
    elif args.command == 'explain':
        cmd_explain(args)
    elif args.command == 'version':
        sha_display = GIT_SHA[:8] if GIT_SHA != 'unknown' else 'unknown'
        print(f"tally {VERSION} ({sha_display})")
        print(REPO_URL)

        # Check for updates
        update_info = check_for_updates()
        if update_info and update_info.get('update_available'):
            print()
            print(f"Update available: v{update_info['latest_version']}")
            print(f"  Run 'tally update' to install")
    elif args.command == 'update':
        cmd_update(args)


if __name__ == '__main__':
    main()
