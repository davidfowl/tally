"""
Tally 'workflow' command - Show context-aware workflow instructions for AI agents.
"""

import os

from ..config_loader import load_config

# Import shared utilities from parent cli module
from ..cli import C, resolve_config_dir, print_no_config_help


def cmd_workflow(args):
    """Show context-aware workflow instructions for AI agents."""
    import subprocess

    # Detect current state
    config_dir = resolve_config_dir(args)
    has_config = config_dir is not None and os.path.isdir(config_dir)
    has_data_sources = False
    unknown_count = 0
    total_unknown_spend = 0

    # If no config, show getting started guide and exit
    if not has_config:
        print_no_config_help()
        return

    # Calculate relative paths for display (OS-aware)
    def make_path(relative_to_config_parent, trailing_sep=False):
        """Create display path relative to cwd with correct OS separators."""
        parent = os.path.dirname(config_dir)
        full_path = os.path.join(parent, relative_to_config_parent)
        rel = os.path.relpath(full_path)
        if trailing_sep:
            rel = rel + os.sep
        # Add ./ prefix on Unix only
        if os.sep == '/' and not rel.startswith('.'):
            rel = './' + rel
        return rel

    # Paths relative to config
    path_data = make_path('data', trailing_sep=True)
    path_settings = make_path(os.path.join('config', 'settings.yaml'))
    path_merchants = make_path(os.path.join('config', 'merchants.rules'))

    rule_mode = 'first_match'  # Default

    try:
        config = load_config(config_dir)
        has_data_sources = bool(config.get('data_sources'))
        rule_mode = config.get('rule_mode', 'first_match')

        if has_data_sources:
            # Try to get unknown merchant count
            try:
                result = subprocess.run(
                    ['tally', 'discover', '--format', 'json', '--config', config_dir],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    import json as json_module
                    unknowns = json_module.loads(result.stdout)
                    unknown_count = len(unknowns)
                    total_unknown_spend = sum(u.get('total_spend', 0) for u in unknowns)
            except Exception:
                pass
    except Exception:
        pass

    # Helper for section headers
    def section(title):
        print()
        print(f"{C.BOLD}{C.CYAN}▸ {title}{C.RESET}")

    # Build context-aware output
    print()
    print(f"{C.BOLD}  TALLY WORKFLOW{C.RESET}")
    print(f"{C.DIM}  ─────────────────────────────────────────{C.RESET}")

    if not has_data_sources:
        print(f"  {C.YELLOW}●{C.RESET} No data sources configured")
        section("Setup Data Sources")
        print(f"    {C.DIM}1.{C.RESET} Add bank/credit card CSVs to {C.CYAN}{path_data}{C.RESET}")
        print()
        print(f"    {C.DIM}2.{C.RESET} Inspect your file to get the format string:")
        print(f"       {C.GREEN}tally inspect {path_data}yourfile.csv{C.RESET}")
        print()
        print(f"    {C.DIM}3.{C.RESET} Add to {C.CYAN}{path_settings}{C.RESET}:")
        print(f"       {C.DIM}data_sources:")
        print(f"         - name: My Card")
        print(f"           file: data/transactions.csv")
        print(f"           format: \"{{date:%m/%d/%Y}},{{description}},{{amount}}\"{C.RESET}")
        print()
        section("Then: Categorize Transactions")
        print(f"    {C.DIM}Use{C.RESET} {C.GREEN}tally discover{C.RESET} {C.DIM}to find merchants, add rules to:{C.RESET}")
        print(f"    {C.CYAN}{path_merchants}{C.RESET} {C.DIM}— match transactions to categories{C.RESET}")
        print()
        return

    # Configured state
    if unknown_count > 0:
        print(f"  {C.GREEN}●{C.RESET} Config ready  {C.DIM}│{C.RESET}  {C.YELLOW}●{C.RESET} {unknown_count} unknown merchants {C.DIM}(${total_unknown_spend:,.0f}){C.RESET}")
    else:
        print(f"  {C.GREEN}●{C.RESET} Config ready  {C.DIM}│{C.RESET}  {C.GREEN}●{C.RESET} All merchants categorized")

    # Show categorization workflow if there are unknowns
    if unknown_count > 0:
        section("Categorize All Transactions")
        print()
        print(f"    {C.BOLD}Step 1:{C.RESET} Check for payment processor prefixes to strip:")
        print(f"           {C.GREEN}tally discover --prefixes{C.RESET}")
        print()
        print(f"    {C.BOLD}Step 2:{C.RESET} Import all unknown merchants as rules:")
        print(f"           {C.GREEN}tally discover --pipe | tally rule import --stdin{C.RESET}")
        print()
        print(f"    {C.BOLD}Step 3:{C.RESET} Edit {C.CYAN}{path_merchants}{C.RESET} to set categories")
        print(f"           {C.DIM}Each rule has category: CATEGORY — replace with Food, Travel, etc.{C.RESET}")
        print()
        print(f"    {C.BOLD}Step 4:{C.RESET} Check progress and repeat until done:")
        print(f"           {C.GREEN}tally up --summary{C.RESET}")
        print()
    else:
        section("Generate Report")
        print(f"    {C.GREEN}tally up{C.RESET}           {C.DIM}Generate HTML spending report{C.RESET}")
        print(f"    {C.GREEN}tally up --summary{C.RESET} {C.DIM}Quick text summary{C.RESET}")
        print()

    section("Commands")
    cmds = [
        ("tally run", "Generate HTML spending report"),
        ("tally run --summary", "Quick text summary"),
        ("tally discover", "Find unknown merchants"),
        ("tally explain <merchant>", "Debug classification"),
        ("tally diag", "Diagnose config issues"),
    ]
    for cmd, desc in cmds:
        print(f"    {C.GREEN}{cmd:<24}{C.RESET} {C.DIM}{desc}{C.RESET}")

    section("Field Transforms")
    print(f"    {C.DIM}Strip payment processor prefixes before matching rules.{C.RESET}")
    print(f"    {C.DIM}Add to the top of {C.RESET}{C.CYAN}{path_merchants}{C.RESET}{C.DIM}:{C.RESET}")
    print()
    print(f"    {C.DIM}field.description = regex_replace(field.description, \"^APLPAY\\\\s+\", \"\")  # Apple Pay")
    print(f"    field.description = regex_replace(field.description, \"^SQ\\\\s*\\\\*\", \"\")   # Square")
    print(f"    field.description = regex_replace(field.description, \"\\\\s+DES:.*$\", \"\")  # BOA suffix{C.RESET}")

    section("Rule Syntax Reference")
    print(f"    Run {C.GREEN}tally reference{C.RESET} for complete syntax documentation:")
    print()
    print(f"    {C.DIM}• Match functions: contains(), regex(), normalized(), fuzzy(), etc.{C.RESET}")
    print(f"    {C.DIM}• Custom fields: field.name, extraction functions{C.RESET}")
    print(f"    {C.DIM}• Dynamic tags: {{field.txn_type}}, {{source}}{C.RESET}")
    print(f"    {C.DIM}• Tag-only rules: add tags without changing category{C.RESET}")
    print(f"    {C.DIM}• Views: group merchants into report sections{C.RESET}")
    print()
    print(f"    {C.GREEN}tally reference merchants{C.RESET}  {C.DIM}Merchant rules only{C.RESET}")
    print(f"    {C.GREEN}tally reference views{C.RESET}      {C.DIM}View definitions only{C.RESET}")

    section("Special Tags")
    print(f"    {C.DIM}These tags affect how transactions appear in your report:{C.RESET}")
    print()
    print(f"    {C.CYAN}income{C.RESET}       {C.DIM}Salary, deposits, interest → excluded from spending{C.RESET}")
    print(f"    {C.CYAN}transfer{C.RESET}     {C.DIM}CC payments, account transfers → excluded from spending{C.RESET}")
    print(f"    {C.CYAN}investment{C.RESET}   {C.DIM}401K, IRA contributions → tracked separately{C.RESET}")
    print(f"    {C.CYAN}refund{C.RESET}       {C.DIM}Returns and credits → shown in Credits Applied section{C.RESET}")
    print()
    print(f"    {C.DIM}Example:{C.RESET}")
    print(f"    {C.DIM}  [Paycheck] match: contains(\"PAYROLL\") tags: income{C.RESET}")
    print(f"    {C.DIM}  [401K] match: contains(\"VANGUARD\") tags: investment{C.RESET}")

    section("Best Practices")
    if rule_mode == 'most_specific':
        print(f"    {C.YELLOW}{C.BOLD}MOST SPECIFIC RULE WINS{C.RESET}  {C.DIM}(rule_mode: most_specific){C.RESET}")
        print(f"    {C.DIM}More conditions = more specific = wins. Tags are collected from ALL matching rules.{C.RESET}")
    else:
        print(f"    {C.YELLOW}{C.BOLD}FIRST MATCHING RULE WINS{C.RESET}  {C.DIM}(rule_mode: first_match){C.RESET}")
        print(f"    {C.DIM}Put specific rules before general ones. Tags are collected from ALL matching rules.{C.RESET}")
    print()
    print(f"    {C.BOLD}1. Start broad, refine later{C.RESET}")
    print(f"       {C.DIM}Write general rules first, then add specific overrides only when needed.{C.RESET}")
    print()
    print(f"    {C.BOLD}2. Consolidate similar merchants{C.RESET}")
    print(f"       {C.DIM}One rule for all airlines is better than one per airline:{C.RESET}")
    print(f"       {C.DIM}  [Airlines]{C.RESET}")
    print(f"       {C.DIM}  match: anyof(\"DELTA\", \"UNITED\", \"AMERICAN\", \"SOUTHWEST\"){C.RESET}")
    print(f"       {C.DIM}  category: Travel{C.RESET}")
    print()
    if rule_mode == 'most_specific':
        print(f"    {C.BOLD}3. Specificity determines category{C.RESET}")
        print(f"       {C.DIM}More conditions = more specific = wins. Order doesn't matter:{C.RESET}")
        print(f"       {C.DIM}  [Uber] match: contains(\"UBER\"){C.RESET}")
        print(f"       {C.DIM}  [Uber Eats] match: contains(\"UBER\") and contains(\"EATS\")  # wins{C.RESET}")
    else:
        print(f"    {C.BOLD}3. Specific rules go first{C.RESET}")
        print(f"       {C.DIM}First matching rule sets category. Put \"Uber Eats\" before \"Uber\":{C.RESET}")
        print(f"       {C.DIM}  [Uber Eats] match: contains(\"UBER\") and contains(\"EATS\"){C.RESET}")
        print(f"       {C.DIM}  [Uber] match: contains(\"UBER\")  # catches remaining{C.RESET}")
    print()
    print(f"    {C.BOLD}4. Use normalized() for inconsistent names{C.RESET}")
    print(f"       {C.DIM}normalized(\"WHOLEFOODS\") matches \"WHOLE FOODS\", \"WHOLEFDS\", etc.{C.RESET}")
    print()
    print(f"    {C.BOLD}5. Avoid overly generic patterns{C.RESET}")
    print(f"       {C.DIM}contains(\"PHO\") matches \"PHONE\" — use regex(r'\\bPHO\\b') instead{C.RESET}")
    print(f"       {C.DIM}contains(\"AT\") would match everything — be specific!{C.RESET}")
    print()
    print(f"    {C.BOLD}6. Use word boundaries in regex{C.RESET}")
    print(f"       {C.DIM}regex(r'\\bTARGET\\b') won't match \"TARGETED\" or \"STARGET\"{C.RESET}")
    print()
    print(f"    {C.BOLD}7. Use tags for cross-category grouping{C.RESET}")
    print(f"       {C.DIM}Tag rules collect from ALL matching rules (not just first):{C.RESET}")
    print(f"       {C.DIM}  [Recurring Tag] match: anyof(\"NETFLIX\", \"SPOTIFY\") tags: recurring{C.RESET}")
    print()
    print(f"    {C.BOLD}8. Verify with explain{C.RESET}")
    print(f"       {C.DIM}tally explain Amazon              # check by merchant name{C.RESET}")
    print(f"       {C.DIM}tally explain \"WHOLEFDS MKT\"      # test raw description{C.RESET}")
    print(f"       {C.DIM}tally explain --category Food     # list all Food merchants{C.RESET}")
    print(f"       {C.DIM}tally explain --tags business     # list business-tagged{C.RESET}")
    print()
    print(f"    {C.BOLD}9. Strip prefixes, don't catch them{C.RESET}")
    print(f"       {C.DIM}BAD:  [ApplePay] match: startswith(\"APLPAY\")  # hides real merchants{C.RESET}")
    print(f"       {C.DIM}GOOD: Use field transforms at top of merchants.rules:{C.RESET}")
    print(f"       {C.DIM}      field.description = regex_replace(field.description, \"^APLPAY\\\\s+\", \"\"){C.RESET}")
    print(f"       {C.DIM}      \"APLPAY STARBUCKS\" → \"STARBUCKS\" → matches correctly{C.RESET}")
    print()

    section("Getting CSV Format Right")
    print(f"    {C.DIM}Use{C.RESET} {C.GREEN}tally inspect{C.RESET} {C.DIM}to analyze your CSV, but verify amount handling:{C.RESET}")
    print()
    print(f"    {C.CYAN}{{amount}}{C.RESET}      {C.DIM}Use as-is (positive = expense, negative = refund){C.RESET}")
    print(f"    {C.CYAN}{{-amount}}{C.RESET}     {C.DIM}Negate (flip the sign){C.RESET}")
    print(f"    {C.CYAN}{{+amount}}{C.RESET}     {C.DIM}Absolute value (always positive){C.RESET}")
    print()
    print(f"    {C.DIM}Common patterns:{C.RESET}")
    print(f"    {C.DIM}  Chase/Amex:  debits positive, credits negative → {{amount}}{C.RESET}")
    print(f"    {C.DIM}  Some banks:  credits positive, debits negative → {{-amount}}{C.RESET}")
    print(f"    {C.DIM}  Others:      all positive with type column     → {{+amount}}{C.RESET}")
    print()
    print(f"    {C.DIM}Test with:{C.RESET} {C.GREEN}tally run --summary{C.RESET} {C.DIM}(check if totals make sense){C.RESET}")

    section("Common Pitfalls")
    print(f"    {C.DIM}• Amounts inverted? Try {{-amount}} or {{+amount}} in format{C.RESET}")
    print(f"    {C.DIM}• Rule not matching? Use{C.RESET} {C.GREEN}tally explain \"RAW DESC\"{C.RESET}")
    print(f"    {C.DIM}• Too many matches? Use startswith() or regex word boundaries{C.RESET}")
    print(f"    {C.DIM}• Catch-all hiding merchants? Use field transforms instead{C.RESET}")
    print()
