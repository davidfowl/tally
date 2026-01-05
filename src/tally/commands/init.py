"""
Tally 'init' command - Initialize a new budget directory.
"""

import os
import sys

from ..colors import C, supports_color
from ..cli_utils import init_config
from ..migrations import migrate_csv_to_rules
from ..templates import STARTER_SETTINGS, STARTER_MERCHANTS, STARTER_VIEWS


def cmd_init(args):
    """Handle the 'init' subcommand."""
    import shutil

    # Check if we're already in a tally directory (has config/)
    # If user didn't explicitly specify a directory, use current dir instead of ./tally/
    if args.dir == 'tally' and os.path.isdir('./config'):
        target_dir = os.path.abspath('.')
        print(f"{C.CYAN}Found existing config/ directory{C.RESET}")
        print(f"  Upgrading current directory in place (won't create nested tally/)")
        print()
    else:
        target_dir = os.path.abspath(args.dir)

    # Use relative paths for display
    rel_target = os.path.relpath(target_dir)
    if rel_target == '.':
        rel_target = './'

    print(f"Initializing budget directory: {C.BOLD}{rel_target}{C.RESET}")
    print()

    # Check for old merchant_categories.csv BEFORE init_config creates new files
    config_dir = os.path.join(target_dir, 'config')
    old_csv = os.path.join(config_dir, 'merchant_categories.csv')
    new_rules = os.path.join(config_dir, 'merchants.rules')

    if os.path.exists(old_csv) and not os.path.exists(new_rules):
        # Check if CSV has actual rules (not just header/comments)
        has_rules = False
        try:
            with open(old_csv, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and not line.startswith('Pattern,'):
                        has_rules = True
                        break
        except Exception:
            pass

        if has_rules:
            print()
            print(f"{C.CYAN}Upgrading merchant rules to new format...{C.RESET}")
            print(f"  Found: config/merchant_categories.csv (legacy CSV format)")
            print()
            migrate_csv_to_rules(old_csv, config_dir, backup=True)
            print()

    created, skipped = init_config(target_dir)

    # Update settings.yaml to add views_file if missing
    settings_path = os.path.join(config_dir, 'settings.yaml')
    views_rules = os.path.join(config_dir, 'views.rules')
    if os.path.exists(settings_path) and os.path.exists(views_rules):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'views_file:' not in content:
                with open(settings_path, 'a', encoding='utf-8') as f:
                    f.write('\n# Views file (custom spending views)\n')
                    f.write('views_file: config/views.rules\n')
                print(f"  {C.GREEN}✓{C.RESET} config/settings.yaml (added views_file)")
        except Exception:
            pass

    # Show each file with its status and description
    all_files = [(f, True) for f in created] + [(f, False) for f in skipped]
    # Sort by filename for consistent ordering
    all_files.sort(key=lambda x: x[0])

    # Brief descriptions for key files
    file_descriptions = {
        'config/merchants.rules': 'match transactions to categories',
        'config/views.rules': 'organize report by spending patterns',
        'config/settings.yaml': 'configure data sources',
    }

    for f, was_created in all_files:
        desc = file_descriptions.get(f, '')
        desc_str = f" {C.DIM}— {desc}{C.RESET}" if desc else ""
        if was_created:
            print(f"  {C.GREEN}✓{C.RESET} {f}{desc_str}")
        else:
            print(f"  {C.YELLOW}→{C.RESET} {C.DIM}{f} (exists){C.RESET}")

    # Check if data sources are configured in settings.yaml
    import yaml
    has_data_sources = False
    settings_path = os.path.join(target_dir, 'config', 'settings.yaml')
    # Use native path separators, with ./ prefix on Unix only
    rel_settings = os.path.relpath(settings_path)
    rel_data = os.path.relpath(os.path.join(target_dir, 'data')) + os.sep
    if os.sep == '/':
        rel_settings = './' + rel_settings
        rel_data = './' + rel_data
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                settings = yaml.safe_load(f) or {}
                has_data_sources = bool(settings.get('data_sources'))
        except Exception:
            pass

    # Show next steps with agent detection
    print()

    # Helper for clickable links (OSC 8 hyperlinks, with fallback)
    def link(url, text=None):
        text = text or url
        if supports_color():
            return f"\033]8;;{url}\033\\{C.UNDERLINE}{C.BLUE}{text}{C.RESET}\033]8;;\033\\"
        return url

    # Check which agents are installed
    agents = [
        ('claude', 'Claude Code', 'https://claude.com/product/claude-code'),
        ('copilot', 'GitHub Copilot', 'https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli'),
        ('opencode', 'OpenCode', 'https://opencode.ai'),
        ('codex', 'OpenAI Codex', 'https://developers.openai.com/codex/cli'),
    ]

    agent_lines = []
    for cmd, name, url in agents:
        installed = shutil.which(cmd) is not None
        if installed:
            status = f"{C.GREEN}✓ installed{C.RESET}"
        else:
            status = link(url)
        agent_lines.append(f"     {C.CYAN}{cmd:<11}{C.RESET} {name:<16} {status}")

    agents_block = '\n'.join(agent_lines)

    print(f"""{C.BOLD}Next steps:{C.RESET}

  {C.BOLD}1.{C.RESET} Drop your bank/credit card exports into {C.CYAN}{rel_data}{C.RESET}

  {C.BOLD}2.{C.RESET} Open this folder in an AI coding agent:
{agents_block}
     {C.DIM}Or any agent that can run command-line tools.{C.RESET}

  {C.BOLD}3.{C.RESET} Tell the agent what to do:
     {C.DIM}• "Use tally to configure my Chase credit card CSV"{C.RESET}
     {C.DIM}• "Use tally to categorize all my transactions"{C.RESET}
     {C.DIM}• "Use tally to generate my spending report"{C.RESET}

{C.DIM}The agent can run {C.RESET}{C.GREEN}tally workflow{C.RESET}{C.DIM} at any time to see the next steps.{C.RESET}
""")
