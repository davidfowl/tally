"""
Starter template strings for tally init command.
"""

STARTER_SETTINGS = '''# Tally Settings
title: "{year} Spending Analysis"

# Data sources - add your statement files here
# Run: tally inspect <file> to auto-detect the format string
data_sources:
  # Example credit card CSV (positive amounts = purchases):
  # - name: Credit Card
  #   file: data/card.csv
  #   format: "{{date:%m/%d/%Y}},{{description}},{{amount}}"
  #
  # Amount modifiers:
  #   {{amount}}   - Keep original sign from CSV
  #   {{-amount}}  - Flip sign (bank statements where negative = expense)
  #   {{+amount}}  - Absolute value (mixed-sign sources like escrow accounts)
  #
  # - name: Checking
  #   file: data/checking.csv
  #   format: "{{date:%Y-%m-%d}},{{description}},{{-amount}}"
  #
  # Folder + glob examples (CSV only):
  # - name: Exports (top-level only)
  #   file: data/exports/          # loads all *.csv in this folder
  # - name: Monthly Exports
  #   file: data/exports/*.csv     # top-level glob
  # - name: All Exports (recursive)
  #   file: data/exports/**/*.csv  # recursive glob

output_dir: output
html_filename: spending_summary.html

# Merchant rules file - expression-based categorization
merchants_file: config/merchants.rules

# Rule matching mode:
#   first_match (default) - First matching rule sets category. Order matters!
#   most_specific         - Most specific rule wins. More conditions = wins.
# rule_mode: first_match

# Views file (optional) - custom spending views
# Create config/views.rules and uncomment:
# views_file: config/views.rules

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

STARTER_MERCHANTS = '''# Tally Merchant Rules
#
# Expression-based rules for categorizing transactions.
# Tags are collected from tag-only rules plus the winning categorization rule.
#
# RULE MATCHING (controlled by rule_mode in settings.yaml):
#   first_match (default) - First matching rule sets category. Order matters!
#   most_specific         - Most specific rule wins. More conditions = wins.
#
# Match expressions:
#   contains("X")     - Case-insensitive substring match
#   regex("pattern")  - Regex pattern match
#   normalized("X")   - Match ignoring spaces/hyphens/punctuation
#   anyof("A", "B")   - Match any of multiple patterns
#   startswith("X")   - Match only at beginning
#   fuzzy("X")        - Approximate matching (catches typos)
#   fuzzy("X", 0.85)  - Fuzzy with custom threshold (default 0.80)
#   amount > 100      - Amount conditions
#   month == 12       - Date component (month, year, day, weekday)
#   weekday == 0      - Day of week (0=Monday, 1=Tuesday, ... 6=Sunday)
#   date >= "2025-01-01"  - Date range
#
# You can combine conditions with 'and', 'or', 'not'
#
# Run: tally inspect <file> to see your transaction descriptions.
# Run: tally discover to find unknown merchants.

# === Special Tags ===
# These tags control how transactions appear in your spending report:
#
#   income   - Deposits, salary, interest (excluded from spending)
#   transfer - Account transfers, CC payments (excluded from spending)
#   refund   - Returns and credits (shown in Credits Applied section)
#
# Example:
#   [Paycheck]
#   match: contains("DIRECT DEPOSIT") or contains("PAYROLL")
#   category: Income
#   subcategory: Salary
#   tags: income
#
#   [Credit Card Payment]
#   match: contains("PAYMENT THANK YOU")
#   category: Finance
#   subcategory: Payment
#   tags: transfer

# === Field Transforms (optional) ===
# Strip payment processor prefixes before matching:
# field.description = regex_replace(field.description, "^APLPAY\\s+", "")
# field.description = regex_replace(field.description, "^SQ\\s*\\*", "")

# === Variables (optional) ===
# is_large = amount > 500
# is_holiday = month >= 11 and month <= 12

# === Example Rules ===

# [Netflix]
# match: contains("NETFLIX")
# category: Subscriptions
# subcategory: Streaming
# tags: entertainment

# [Costco Grocery]
# match: contains("COSTCO") and amount <= 200
# category: Food
# subcategory: Grocery

# [Costco Bulk]
# match: contains("COSTCO") and amount > 200
# category: Shopping
# subcategory: Wholesale

# [Uber Rides]
# match: regex("UBER\\s(?!EATS)")  # Uber but not Uber Eats
# category: Transportation
# subcategory: Rideshare

# [Uber Eats]
# match: normalized("UBEREATS")  # Matches "UBER EATS", "UBER-EATS", etc.
# category: Food
# subcategory: Delivery

# [Streaming Services]
# match: anyof("NETFLIX", "HULU", "DISNEY+", "HBO")
# category: Subscriptions
# subcategory: Streaming

# === Weekday-based tagging ===
# Tag weekday vs weekend transactions differently

# [Starbucks - Workdays]
# match: contains("Starbucks") and weekday < 5  # Monday-Friday (0-4)
# category: Food
# subcategory: Coffee
# tags: work

# [Starbucks]
# match: contains("Starbucks") and weekday >= 5  # Saturday-Sunday (5-6)
# category: Food
# subcategory: Coffee

# === Add your rules below ===

'''

STARTER_VIEWS = '''# Tally Views Configuration (.rules format)
#
# Views define groups of merchants for your spending report.
# Each merchant is evaluated against all view filters.
# Views can overlap - the same merchant can appear in multiple views.
#
# SYNTAX:
#   [View Name]
#   description: Human-readable description (optional)
#   filter: <expression>
#
# PRIMITIVES:
#   months      - count of unique months with transactions
#   total       - sum of all payments
#   cv          - coefficient of variation of monthly totals (0 = very consistent)
#   category    - category string (e.g., "Food", "Travel")
#   subcategory - subcategory string (e.g., "Grocery", "Airline")
#   merchant    - merchant name
#   tags        - set of tag strings
#   payments    - list of payment amounts
#
# FUNCTIONS:
#   sum(x), count(x), avg(x), min(x), max(x), stddev(x)
#   abs(x), round(x)
#   by(field) - group payments by: month, year, week, day
#
# GROUPING:
#   by("month")           - list of payment lists per month
#   sum(by("month"))      - list of monthly totals
#   avg(sum(by("month"))) - average monthly spend
#   max(sum(by("month"))) - highest spending month
#
# OPERATORS:
#   Comparison: ==  !=  <  <=  >  >=
#   Boolean:    and  or  not
#   Membership: "tag" in tags
#   Arithmetic: +  -  *  /  %
#
# ============================================================================
# SAMPLE VIEWS (uncomment and customize)
# ============================================================================

# [Every Month]
# description: Consistent recurring expenses (rent, utilities, subscriptions)
# filter: months >= 6 and cv < 0.3

# [Variable Recurring]
# description: Frequent but inconsistent (groceries, shopping, delivery)
# filter: months >= 6 and cv >= 0.3

# [Periodic]
# description: Quarterly or semi-annual (tuition, insurance)
# filter: months >= 2 and months <= 5

# [Travel]
# description: All travel expenses
# filter: category == "Travel"

# [Large Purchases]
# description: Big one-time expenses over $1,000
# filter: total > 1000 and months <= 3

# [Food & Dining]
# description: All food-related spending
# filter: category == "Food"

# [Subscriptions]
# description: Streaming, software, memberships
# filter: category == "Subscriptions"

# [Tagged: Business]
# description: Business expenses for reimbursement
# filter: "business" in tags

'''
