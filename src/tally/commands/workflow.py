"""
Tally 'workflow' command - Show context-aware workflow instructions for AI agents.
"""

import os

from ..config_loader import load_config

# Import shared utilities from parent cli module
from ..cli import C, find_config_dir


def cmd_workflow(args):
    """Show context-aware workflow instructions for AI agents."""
    import subprocess

    # Detect current state
    config_dir = find_config_dir()
    has_config = config_dir is not None
    has_data_sources = False
    unknown_count = 0
    total_unknown_spend = 0

    # Calculate relative paths for display (OS-aware)
    def make_path(relative_to_config_parent, trailing_sep=False):
        """Create display path relative to cwd with correct OS separators."""
        if config_dir:
            parent = os.path.dirname(config_dir)
            full_path = os.path.join(parent, relative_to_config_parent)
        else:
            full_path = relative_to_config_parent
        rel = os.path.relpath(full_path)
        if trailing_sep:
            rel = rel + os.sep
        # Add ./ prefix on Unix only
        if os.sep == '/' and not rel.startswith('.'):
            rel = './' + rel
        return rel

    # Default paths (used when no config exists)
    path_data = make_path('data', trailing_sep=True) if config_dir else './data/'
    path_settings = make_path(os.path.join('config', 'settings.yaml')) if config_dir else './config/settings.yaml'
    path_merchants = make_path(os.path.join('config', 'merchants.rules')) if config_dir else './config/merchants.rules'

    if has_config:
        try:
            config = load_config(config_dir)
            has_data_sources = bool(config.get('data_sources'))

            if has_data_sources:
                # Try to get unknown merchant count
                try:
                    result = subprocess.run(
                        ['tally', 'discover', '--format', 'json'],
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

    # Status bar
    if not has_config:
        print(f"  {C.YELLOW}●{C.RESET} No config found")
        section("Getting Started")
        print(f"    {C.DIM}1.{C.RESET} Initialize: {C.GREEN}tally init{C.RESET}")
        print(f"       {C.DIM}Creates settings.yaml, merchants.rules, views.rules{C.RESET}")
        print()
        print(f"    {C.DIM}2.{C.RESET} Add bank/credit card CSVs to {C.CYAN}./data/{C.RESET}")
        print()
        print(f"    {C.DIM}3.{C.RESET} Configure data sources in {C.CYAN}./config/settings.yaml{C.RESET}")
        print()
        return

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
        section("Categorization Workflow")
        print(f"    {C.DIM}1.{C.RESET} Get unknown merchants with suggested rules:")
        print(f"       {C.GREEN}tally discover --format json{C.RESET}")
        print()
        print(f"    {C.DIM}2.{C.RESET} Add rules to {C.CYAN}{path_merchants}{C.RESET}")
        print(f"       {C.YELLOW}READ the Best Practices below first!{C.RESET}")
        print()
        print(f"    {C.DIM}3.{C.RESET} Check progress:")
        print(f"       {C.GREEN}tally run --summary{C.RESET}")
        print()
        print(f"    {C.YELLOW}{C.BOLD}KEEP GOING UNTIL ALL UNKNOWNS ARE RESOLVED!{C.RESET}")
        print(f"    {C.DIM}Your report is only as good as your rules. Don't stop at 80%.{C.RESET}")

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
    print(f"    {C.YELLOW}{C.BOLD}MOST SPECIFIC RULE WINS{C.RESET}")
    print(f"    {C.DIM}All matching rules are evaluated. Most specific sets category.{C.RESET}")
    print(f"    {C.DIM}Tags are collected from ALL matching rules.{C.RESET}")
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
    print(f"    {C.BOLD}3. Specificity determines category{C.RESET}")
    print(f"       {C.DIM}More conditions = more specific = wins. Order doesn't matter:{C.RESET}")
    print(f"       {C.DIM}  [Uber] match: contains(\"UBER\"){C.RESET}")
    print(f"       {C.DIM}  [Uber Eats] match: contains(\"UBER\") and contains(\"EATS\")  # wins{C.RESET}")
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
