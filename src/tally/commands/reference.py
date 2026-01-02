"""
Tally 'reference' command - Show complete rule syntax reference.
"""

# Import shared utilities from parent cli module
from ..cli import C


def cmd_reference(args):
    """Show complete rule syntax reference."""
    topic = args.topic.lower() if args.topic else None

    def header(title):
        print()
        print(f"{C.BOLD}{C.CYAN}{'═' * 60}{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}  {title}{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'═' * 60}{C.RESET}")
        print()

    def section(title):
        print()
        print(f"{C.BOLD}{title}{C.RESET}")
        print(f"{C.DIM}{'─' * 40}{C.RESET}")

    def show_merchants_reference():
        header("MERCHANTS.RULES REFERENCE")

        print(f"{C.DIM}File: config/merchants.rules{C.RESET}")
        print(f"{C.DIM}Purpose: Categorize transactions by matching descriptions{C.RESET}")

        section("Rule Structure")
        print(f"""
  {C.CYAN}[Rule Name]{C.RESET}              {C.DIM}# Display name for matched transactions{C.RESET}
  {C.CYAN}match:{C.RESET} <expression>      {C.DIM}# Required: when to apply this rule{C.RESET}
  {C.CYAN}category:{C.RESET} <Category>     {C.DIM}# Required: primary grouping{C.RESET}
  {C.CYAN}subcategory:{C.RESET} <Sub>       {C.DIM}# Optional: secondary grouping{C.RESET}
  {C.CYAN}tags:{C.RESET} tag1, tag2         {C.DIM}# Optional: labels for filtering{C.RESET}
""")

        section("Match Functions")
        print(f"""  {C.DIM}All match functions search description by default.{C.RESET}
  {C.DIM}Add an optional first argument to search a custom field instead.{C.RESET}
""")
        funcs = [
            ('contains("text")', 'Case-insensitive substring match',
             'match: contains("NETFLIX")', 'Matches "NETFLIX.COM", "netflix", etc.'),
            ('contains(field.x, "text")', 'Search custom field',
             'match: contains(field.memo, "REF")', 'Searches in field.memo'),
            ('regex("pattern")', 'Perl-compatible regex',
             'match: regex("UBER\\\\s(?!EATS)")', 'Matches "UBER TRIP" but not "UBER EATS"'),
            ('normalized("text")', 'Ignores spaces, hyphens, punctuation',
             'match: normalized("WHOLEFOODS")', 'Matches "WHOLE FOODS", "WHOLE-FOODS", etc.'),
            ('anyof("a", "b", ...)', 'Match any of multiple patterns',
             'match: anyof("NETFLIX", "HULU", "HBO")', 'Matches any streaming service'),
            ('startswith("text")', 'Match only at beginning',
             'match: startswith("AMZN")', 'Matches "AMZN MKTP" but not "PAY AMZN"'),
            ('fuzzy("text")', 'Approximate matching (typos)',
             'match: fuzzy("STARBUCKS")', 'Matches "STARBUKS", "STARBUCK" (80% similar)'),
            ('fuzzy("text", 0.90)', 'Fuzzy with custom threshold',
             'match: fuzzy("COSTCO", 0.90)', 'Requires 90% similarity'),
        ]
        for func, desc, example, note in funcs:
            print(f"  {C.GREEN}{func}{C.RESET}")
            print(f"    {desc}")
            print(f"    {C.DIM}Example: {example}{C.RESET}")
            print(f"    {C.DIM}→ {note}{C.RESET}")
            print()

        section("Amount & Date Conditions")
        conditions = [
            ('amount > 100', 'Transactions over $100'),
            ('amount <= 50', 'Transactions $50 or less'),
            ('amount < 0', 'Credits/refunds (negative amounts)'),
            ('month == 12', 'December transactions only'),
            ('month >= 11', 'November and December'),
            ('year == 2024', 'Specific year'),
            ('day == 1', 'First of the month'),
            ('date >= "2024-01-01"', 'On or after a specific date'),
            ('date < "2024-06-01"', 'Before a specific date'),
        ]
        for cond, desc in conditions:
            print(f"  {C.GREEN}{cond:<28}{C.RESET} {C.DIM}{desc}{C.RESET}")

        section("Combining Conditions")
        print(f"""
  {C.GREEN}and{C.RESET}   Both conditions must be true
        {C.DIM}match: contains("COSTCO") and amount > 200{C.RESET}

  {C.GREEN}or{C.RESET}    Either condition can be true
        {C.DIM}match: contains("SHELL") or contains("CHEVRON"){C.RESET}

  {C.GREEN}not{C.RESET}   Negates a condition
        {C.DIM}match: contains("UBER") and not contains("EATS"){C.RESET}

  {C.GREEN}( ){C.RESET}   Group conditions
        {C.DIM}match: (contains("AMAZON") or contains("AMZN")) and amount > 100{C.RESET}
""")

        section("Custom CSV Fields")
        print(f"""
  Access custom fields captured from CSV format strings using {C.GREEN}field.<name>{C.RESET}:

  {C.DIM}# In settings.yaml:{C.RESET}
  {C.CYAN}format: "{{date}},{{txn_type}},{{memo}},{{vendor}},{{amount}}"{C.RESET}
  {C.CYAN}columns:{C.RESET}
  {C.CYAN}  description: "{{vendor}}"{C.RESET}

  {C.DIM}# In merchants.rules:{C.RESET}
  {C.DIM}[Wire Transfer]{C.RESET}
  {C.DIM}match: field.txn_type == "WIRE"{C.RESET}
  {C.DIM}category: Transfers{C.RESET}

  {C.DIM}[Invoice Payment]{C.RESET}
  {C.DIM}match: contains(field.memo, "Invoice"){C.RESET}
  {C.DIM}category: Bills{C.RESET}

  Use {C.GREEN}exists(field.name){C.RESET} to safely check if a field exists:
  {C.DIM}match: exists(field.memo) and contains(field.memo, "REF"){C.RESET}
""")

        section("Extraction Functions")
        extract_funcs = [
            ('extract("pattern")', 'Extract first regex capture group',
             r'extract("REF:(\\d+)")', 'Captures "12345" from "REF:12345"'),
            ('extract(field.x, "pattern")', 'Extract from custom field',
             r'extract(field.memo, "#(\\d+)")', 'Captures from field.memo'),
            ('split("-", 0)', 'Split by delimiter, get element at index',
             'split("-", 0)', '"ACH-OUT-123" → "ACH"'),
            ('split(field.x, "-", 1)', 'Split custom field',
             'split(field.code, "-", 1)', 'Gets second element'),
            ('substring(0, 4)', 'Extract substring by position',
             'substring(0, 4)', '"AMZN*MARKET" → "AMZN"'),
            ('trim()', 'Remove leading/trailing whitespace',
             'trim()', '"  AMAZON  " → "AMAZON"'),
            ('trim(field.x)', 'Trim custom field',
             'trim(field.memo)', 'Trims field.memo'),
            ('exists(field.x)', 'Check if field exists and is non-empty',
             'exists(field.memo)', 'Returns false if missing or empty'),
        ]
        for func, desc, example, note in extract_funcs:
            print(f"  {C.GREEN}{func}{C.RESET}")
            print(f"    {desc}")
            print(f"    {C.DIM}Example: {example} → {note}{C.RESET}")
            print()

        section("Variables")
        print(f"""
  Define reusable conditions at the top of your file:

  {C.CYAN}is_large = amount > 500{C.RESET}
  {C.CYAN}is_holiday = month >= 11 and month <= 12{C.RESET}
  {C.CYAN}is_coffee = anyof("STARBUCKS", "PEETS", "PHILZ"){C.RESET}

  Then use in rules:
  {C.DIM}[Holiday Splurge]{C.RESET}
  {C.DIM}match: is_large and is_holiday{C.RESET}
  {C.DIM}category: Shopping{C.RESET}
""")

        section("Field Transforms")
        print(f"""
  Mutate field values before matching. Place at the top of your file:

  {C.CYAN}field.description = regex_replace(field.description, "^APLPAY\\\\s+", ""){C.RESET}
  {C.CYAN}field.description = regex_replace(field.description, "^SQ\\\\*\\\\s*", ""){C.RESET}
  {C.CYAN}field.memo = trim(field.memo){C.RESET}

  {C.BOLD}Transform Functions:{C.RESET}
""")
        transform_funcs = [
            ('regex_replace(text, pattern, repl)', 'Regex substitution (replaces all matches)',
             'regex_replace(field.description, "^APLPAY\\\\s+", "")', '"APLPAY STARBUCKS" → "STARBUCKS"'),
            ('uppercase(text)', 'Convert to uppercase',
             'uppercase(field.description)', '"Starbucks" → "STARBUCKS"'),
            ('lowercase(text)', 'Convert to lowercase',
             'lowercase(field.description)', '"STARBUCKS" → "starbucks"'),
            ('strip_prefix(text, prefix)', 'Remove prefix (case-insensitive)',
             'strip_prefix(field.description, "SQ*")', '"SQ*COFFEE" → "COFFEE"'),
            ('strip_suffix(text, suffix)', 'Remove suffix (case-insensitive)',
             'strip_suffix(field.description, " DES:123")', '"STORE DES:123" → "STORE"'),
            ('trim(text)', 'Remove leading/trailing whitespace',
             'trim(field.memo)', '"  text  " → "text"'),
        ]
        for func, desc, example, note in transform_funcs:
            print(f"  {C.GREEN}{func}{C.RESET}")
            print(f"    {desc}")
            print(f"    {C.DIM}Example: {example} → {note}{C.RESET}")
            print()

        print(f"""  {C.BOLD}Built-in fields:{C.RESET} {C.GREEN}field.description{C.RESET}, {C.GREEN}field.amount{C.RESET}, {C.GREEN}field.date{C.RESET}, {C.GREEN}field.source{C.RESET}
  {C.BOLD}Custom fields:{C.RESET} Any field captured from CSV (e.g., {C.GREEN}field.memo{C.RESET})

  {C.DIM}Original values are preserved in _raw_<field> (e.g., _raw_description){C.RESET}
""")

        section("Special Tags")
        print(f"""
  These tags have special meaning in the spending report:

  {C.CYAN}income{C.RESET}     Money coming in (salary, interest, deposits)
             {C.DIM}→ Excluded from spending totals{C.RESET}

  {C.CYAN}transfer{C.RESET}   Moving money between accounts (CC payments, transfers)
             {C.DIM}→ Excluded from spending totals{C.RESET}

  {C.CYAN}refund{C.RESET}     Returns and credits on purchases
             {C.DIM}→ Shown in "Credits Applied" section, nets against spending{C.RESET}
""")

        section("Dynamic Tags")
        print(f"""
  Use {C.GREEN}{{expression}}{C.RESET} to create tags from field values or data source:

  {C.DIM}[Bank Transaction]{C.RESET}
  {C.DIM}match: contains("BANK"){C.RESET}
  {C.DIM}category: Transfers{C.RESET}
  {C.DIM}tags: banking, {{field.txn_type}}{C.RESET}     {C.DIM}# → "banking", "wire" or "ach"{C.RESET}

  {C.DIM}[Project Expense]{C.RESET}
  {C.DIM}match: contains(field.memo, "PROJ:"){C.RESET}
  {C.DIM}category: Business{C.RESET}
  {C.DIM}tags: project, {{extract(field.memo, "PROJ:(\\w+)")}}{C.RESET}  {C.DIM}# → "project", "alpha"{C.RESET}

  Use {C.GREEN}{{source}}{C.RESET} to tag by data source (e.g., card holder):
  {C.DIM}[All Purchases]{C.RESET}
  {C.DIM}match: *{C.RESET}
  {C.DIM}tags: {{source}}{C.RESET}                      {C.DIM}# → "alice-amex", "bob-chase", etc.{C.RESET}

  Use {C.GREEN}source{C.RESET} in match expressions to vary rules by data source:
  {C.DIM}match: contains("AMAZON") and source == "Amex"{C.RESET}

  {C.DIM}Empty or whitespace-only values are automatically skipped.{C.RESET}
  {C.DIM}All tags are lowercased for consistency.{C.RESET}
""")

        section("Supplemental Data Sources")
        print(f"""
  Query external data (receipts, orders, etc.) to enrich transactions.

  {C.BOLD}In settings.yaml:{C.RESET}
  {C.CYAN}data_sources:{C.RESET}
    {C.CYAN}- name: amazon_orders{C.RESET}
      {C.CYAN}file: data/amazon-orders.csv{C.RESET}
      {C.CYAN}format: "{{date}},{{item}},{{amount}}"{C.RESET}
      {C.CYAN}supplemental: true{C.RESET}    {C.DIM}# Query-only, no transactions generated{C.RESET}

  {C.BOLD}Query with list comprehensions:{C.RESET}
  {C.CYAN}[r for r in source_name if condition]{C.RESET}

  {C.BOLD}Built-in functions:{C.RESET}
  {C.GREEN}len(list){C.RESET}         Number of items
  {C.GREEN}sum(generator){C.RESET}    Sum of values
  {C.GREEN}any(generator){C.RESET}    True if any match
  {C.GREEN}next(gen, default){C.RESET} First match or default

  {C.BOLD}Example: Match Amazon orders by amount{C.RESET}
  {C.DIM}[Amazon - Verified]{C.RESET}
  {C.DIM}let: orders = [r for r in amazon_orders if r.amount == txn.amount]{C.RESET}
  {C.DIM}match: contains("AMAZON") and len(orders) > 0{C.RESET}
  {C.DIM}category: Shopping{C.RESET}
  {C.DIM}tags: verified{C.RESET}
""")

        section("Rule-Level Directives")
        print(f"""
  {C.BOLD}let:{C.RESET} Cache expensive expressions for reuse
  {C.CYAN}let: orders = [r for r in amazon_orders if r.amount == txn.amount]{C.RESET}
  {C.CYAN}let: total = sum(r.amount for r in orders){C.RESET}
  {C.CYAN}let: matched = total == txn.amount{C.RESET}

  {C.BOLD}field:{C.RESET} Add extra fields to transaction (available in reports)
  {C.CYAN}field: items = [r.item for r in orders]{C.RESET}
  {C.CYAN}field: order_count = len(orders){C.RESET}

  {C.BOLD}Complete example:{C.RESET}
  {C.DIM}[PayPal - Enriched]{C.RESET}
  {C.DIM}let: m = [r for r in paypal if r.amount == txn.amount]{C.RESET}
  {C.DIM}match: contains("PAYPAL") and len(m) > 0{C.RESET}
  {C.DIM}category: Shopping{C.RESET}
  {C.DIM}field: merchant_name = m[0].merchant{C.RESET}
  {C.DIM}tags: paypal{C.RESET}
""")

        section("Transaction Context (txn.)")
        print(f"""
  Use {C.GREEN}txn.{C.RESET} prefix for explicit transaction field access:

  {C.GREEN}txn.amount{C.RESET}        Transaction amount
  {C.GREEN}txn.date{C.RESET}          Transaction date
  {C.GREEN}txn.description{C.RESET}   Transaction description
  {C.GREEN}txn.source{C.RESET}        Data source name
  {C.GREEN}txn.location{C.RESET}      Transaction location

  {C.BOLD}Useful in list comprehensions to avoid ambiguity:{C.RESET}
  {C.DIM}# Find orders within 3 days of transaction{C.RESET}
  {C.CYAN}[r for r in orders if abs(r.date - txn.date) <= 3]{C.RESET}

  {C.DIM}# Match by amount{C.RESET}
  {C.CYAN}any(r for r in orders if r.amount == txn.amount){C.RESET}

  {C.DIM}Bare names (amount, date) also work when unambiguous.{C.RESET}
""")

        section("Tag-Only Rules")
        print(f"""
  Rules without {C.GREEN}category:{C.RESET} add tags without affecting categorization:

  {C.DIM}[Large Purchase]{C.RESET}
  {C.DIM}match: amount > 500{C.RESET}
  {C.DIM}tags: large, review{C.RESET}              {C.DIM}# No category - just adds tags{C.RESET}

  {C.DIM}[Holiday Season]{C.RESET}
  {C.DIM}match: month >= 11 and month <= 12{C.RESET}
  {C.DIM}tags: holiday{C.RESET}

  {C.BOLD}Two-pass matching:{C.RESET}
  1. First rule with {C.GREEN}category:{C.RESET} sets merchant/category/subcategory
  2. Tags are collected from {C.BOLD}ALL{C.RESET} matching rules

  Example: A $600 Netflix charge in December gets:
  • Category from Netflix rule (Subscriptions)
  • Tags: entertainment + large + review + holiday
""")

        section("Rule Priority")
        print(f"""
  {C.BOLD}First categorization rule wins{C.RESET} — put specific patterns before general:

  {C.DIM}[Uber Eats]                    # ← More specific, checked first{C.RESET}
  {C.DIM}match: contains("UBER EATS"){C.RESET}
  {C.DIM}category: Food{C.RESET}

  {C.DIM}[Uber Rides]                   # ← Less specific, checked second{C.RESET}
  {C.DIM}match: contains("UBER"){C.RESET}
  {C.DIM}category: Transportation{C.RESET}

  {C.BOLD}Tags accumulate{C.RESET} from all matching rules. Use 'and not' if needed:
  {C.DIM}match: contains("UBER") and not contains("EATS"){C.RESET}
""")

        section("Complete Example")
        print(f"""{C.DIM}# === Variables ===
is_large = amount > 500

# === Subscriptions ===

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming
tags: entertainment

[Spotify]
match: contains("SPOTIFY")
category: Subscriptions
subcategory: Music
tags: entertainment

# === Food ===

[Costco Grocery]
match: contains("COSTCO") and amount <= 200
category: Food
subcategory: Grocery

[Costco Bulk]
match: contains("COSTCO") and is_large
category: Shopping
subcategory: Wholesale

# === Special Handling ===

[Salary]
match: contains("PAYROLL") or contains("DIRECT DEP")
category: Income
subcategory: Salary
tags: income

[CC Payment]
match: contains("PAYMENT THANK YOU")
category: Finance
subcategory: Credit Card
tags: transfer

[Amazon Refund]
match: contains("AMAZON") and amount < 0
category: Shopping
subcategory: Online
tags: refund{C.RESET}
""")

    def show_views_reference():
        header("VIEWS.RULES REFERENCE")

        print(f"{C.DIM}File: config/views.rules{C.RESET}")
        print(f"{C.DIM}Purpose: Create custom sections in the spending report{C.RESET}")

        section("View Structure")
        print(f"""
  {C.CYAN}[View Name]{C.RESET}                {C.DIM}# Section header in report{C.RESET}
  {C.CYAN}description:{C.RESET} <text>        {C.DIM}# Optional: subtitle under header{C.RESET}
  {C.CYAN}filter:{C.RESET} <expression>       {C.DIM}# Required: which merchants to include{C.RESET}
""")

        section("Filter Primitives")
        primitives = [
            ('months', 'Number of months with transactions', 'filter: months >= 6'),
            ('payments', 'Total number of transactions', 'filter: payments >= 12'),
            ('total', 'Total spending for this merchant', 'filter: total > 1000'),
            ('cv', 'Coefficient of variation (consistency)', 'filter: cv < 0.3'),
            ('category', 'Merchant category', 'filter: category == "Subscriptions"'),
            ('subcategory', 'Merchant subcategory', 'filter: subcategory == "Streaming"'),
            ('tags', 'Merchant tags (contains check)', 'filter: tags has "business"'),
        ]
        for prim, desc, example in primitives:
            print(f"  {C.GREEN}{prim:<12}{C.RESET} {desc}")
            print(f"             {C.DIM}{example}{C.RESET}")
            print()

        section("Aggregate Functions")
        funcs = [
            ('sum()', 'Total of all values', 'sum(by("month"))'),
            ('avg()', 'Average value', 'avg(by("month"))'),
            ('count()', 'Number of items', 'count(by("month"))'),
            ('min()', 'Minimum value', 'min(by("month"))'),
            ('max()', 'Maximum value', 'max(by("month"))'),
            ('stddev()', 'Standard deviation', 'stddev(by("month"))'),
        ]
        for func, desc, example in funcs:
            print(f"  {C.GREEN}{func:<12}{C.RESET} {desc:<24} {C.DIM}{example}{C.RESET}")

        section("Grouping with by()")
        print(f"""
  {C.GREEN}by("month"){C.RESET}    Group transactions by month
  {C.GREEN}by("year"){C.RESET}     Group transactions by year
  {C.GREEN}by("day"){C.RESET}      Group transactions by day

  Examples:
    {C.DIM}filter: sum(by("month")) > 100     # At least $100/month{C.RESET}
    {C.DIM}filter: count(by("month")) >= 1    # Transaction every month{C.RESET}
    {C.DIM}filter: avg(by("month")) > 50      # Averages over $50/month{C.RESET}
""")

        section("Comparison Operators")
        print(f"""
  {C.GREEN}=={C.RESET}  Equal to            {C.DIM}category == "Food"{C.RESET}
  {C.GREEN}!={C.RESET}  Not equal to        {C.DIM}category != "Transfers"{C.RESET}
  {C.GREEN}>{C.RESET}   Greater than        {C.DIM}total > 500{C.RESET}
  {C.GREEN}>={C.RESET}  Greater or equal    {C.DIM}months >= 6{C.RESET}
  {C.GREEN}<{C.RESET}   Less than           {C.DIM}cv < 0.3{C.RESET}
  {C.GREEN}<={C.RESET}  Less or equal       {C.DIM}payments <= 12{C.RESET}
""")

        section("Logical Operators")
        print(f"""
  {C.GREEN}and{C.RESET}   Both conditions       {C.DIM}months >= 6 and cv < 0.3{C.RESET}
  {C.GREEN}or{C.RESET}    Either condition      {C.DIM}category == "Bills" or tags has "recurring"{C.RESET}
  {C.GREEN}not{C.RESET}   Negation              {C.DIM}not category == "Income"{C.RESET}
  {C.GREEN}has{C.RESET}   Contains (for tags)   {C.DIM}tags has "business"{C.RESET}
""")

        section("View Examples")
        print(f"""{C.DIM}# Consistent monthly expenses
[Every Month]
description: Bills that hit every month
filter: months >= 6 and cv < 0.3

# Large one-time purchases
[Big Purchases]
description: Major one-time expenses
filter: total > 1000 and months <= 2

# Subscriptions by category
[Streaming]
filter: category == "Subscriptions" and subcategory == "Streaming"

# Business expenses for reimbursement
[Business]
description: Expenses to submit for reimbursement
filter: tags has "business"

# Variable recurring (same merchant, different amounts)
[Utilities]
description: Recurring with variable amounts
filter: months >= 6 and cv >= 0.3 and cv < 1.0

# High-frequency spending
[Daily Habits]
description: Places you visit frequently
filter: payments >= 20 and total > 200{C.RESET}
""")

        section("Views vs Categories")
        print(f"""
  {C.BOLD}Categories{C.RESET} (in merchants.rules): Define WHAT a transaction is
    {C.DIM}→ Each transaction has exactly one category{C.RESET}

  {C.BOLD}Views{C.RESET} (in views.rules): Define HOW to group for reporting
    {C.DIM}→ Same merchant can appear in multiple views{C.RESET}
    {C.DIM}→ Views are optional — report works without them{C.RESET}
""")

    # Main output logic
    if topic == 'merchants':
        show_merchants_reference()
    elif topic == 'views':
        show_views_reference()
    else:
        # Show both
        show_merchants_reference()
        show_views_reference()

        # Footer
        print()
        print(f"{C.DIM}{'─' * 60}{C.RESET}")
        print(f"  {C.DIM}For specific topics:{C.RESET}")
        print(f"    {C.GREEN}tally reference merchants{C.RESET}  {C.DIM}Merchant rules only{C.RESET}")
        print(f"    {C.GREEN}tally reference views{C.RESET}      {C.DIM}View definitions only{C.RESET}")
        print()
