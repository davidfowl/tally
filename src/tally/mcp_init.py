"""MCP client setup for tally - configure MCP server on various AI tools."""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def get_tally_command():
    """Get the full path to tally executable."""
    # Try to find tally in PATH
    tally_path = shutil.which('tally')
    if tally_path:
        return tally_path

    # If running from source, use the module path
    return sys.executable


def get_tally_args():
    """Get the arguments needed to run tally mcp."""
    tally_path = shutil.which('tally')
    if tally_path:
        return ["mcp"]
    else:
        # Running from source via python -m or uv run
        return ["-m", "tally", "mcp"]


def detect_mcp_clients():
    """Auto-detect available MCP clients."""
    detected = []

    # Claude Desktop
    if _get_claude_desktop_config_path().parent.exists():
        detected.append('claude-desktop')

    # VS Code (check if code command exists)
    if shutil.which('code'):
        detected.append('vscode')

    # Cursor (check config directory)
    cursor_config = Path.cwd() / '.cursor'
    if cursor_config.exists() or shutil.which('cursor'):
        detected.append('cursor')

    # Claude Code (check if claude command exists)
    if shutil.which('claude'):
        detected.append('claude-code')

    # OpenCode (check for opencode.json in cwd)
    if (Path.cwd() / 'opencode.json').exists() or (Path.cwd() / 'opencode.jsonc').exists():
        detected.append('opencode')

    # Gemini CLI (check config directory)
    gemini_config = Path.home() / '.gemini'
    if gemini_config.exists() or shutil.which('gemini'):
        detected.append('gemini')

    return detected


def _get_claude_desktop_config_path():
    """Get Claude Desktop config file path based on platform."""
    system = platform.system()
    if system == 'Darwin':
        return Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
    elif system == 'Windows':
        appdata = os.environ.get('APPDATA', '')
        return Path(appdata) / 'Claude/claude_desktop_config.json'
    else:
        return Path.home() / '.config/Claude/claude_desktop_config.json'


def setup_claude_desktop():
    """Add tally to Claude Desktop config."""
    config_path = _get_claude_desktop_config_path()

    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    if 'mcpServers' not in existing:
        existing['mcpServers'] = {}

    tally_cmd = get_tally_command()
    tally_args = get_tally_args()

    existing['mcpServers']['tally'] = {
        "command": tally_cmd,
        "args": tally_args
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2))

    print(f"Added tally to Claude Desktop config: {config_path}")
    print("  Restart Claude Desktop to activate.")
    return True


def setup_vscode():
    """Add tally to VS Code via CLI."""
    tally_cmd = get_tally_command()
    tally_args = get_tally_args()

    mcp_config = {
        "name": "tally",
        "command": tally_cmd,
        "args": tally_args
    }

    try:
        subprocess.run(
            ['code', '--add-mcp', json.dumps(mcp_config)],
            check=True,
            capture_output=True
        )
        print("Added tally to VS Code MCP servers.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to add tally to VS Code: {e.stderr.decode() if e.stderr else str(e)}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("VS Code CLI not found. Make sure 'code' command is in PATH.", file=sys.stderr)
        return False


def setup_cursor():
    """Add tally to Cursor config."""
    config_path = Path.cwd() / '.cursor' / 'mcp.json'

    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    if 'mcpServers' not in existing:
        existing['mcpServers'] = {}

    tally_cmd = get_tally_command()
    tally_args = get_tally_args()

    existing['mcpServers']['tally'] = {
        "command": tally_cmd,
        "args": tally_args
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2))

    print(f"Added tally to Cursor config: {config_path}")
    return True


def setup_claude_code():
    """Add tally to Claude Code."""
    tally_cmd = get_tally_command()

    try:
        subprocess.run(
            ['claude', 'mcp', 'add', 'tally', '--', tally_cmd, 'mcp'],
            check=True,
            capture_output=True
        )
        print("Added tally to Claude Code.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to add tally to Claude Code: {e.stderr.decode() if e.stderr else str(e)}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("Claude Code CLI not found. Make sure 'claude' command is in PATH.", file=sys.stderr)
        return False


def setup_opencode():
    """Add tally to OpenCode config."""
    # Look for existing config file
    for filename in ['opencode.jsonc', 'opencode.json']:
        config_path = Path.cwd() / filename
        if config_path.exists():
            break
    else:
        config_path = Path.cwd() / 'opencode.json'

    if config_path.exists():
        try:
            # Read and strip comments for .jsonc
            content = config_path.read_text()
            # Simple comment stripping for JSONC
            import re
            content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
            content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
            existing = json.loads(content)
        except json.JSONDecodeError:
            existing = {"$schema": "https://opencode.ai/config.json"}
    else:
        existing = {"$schema": "https://opencode.ai/config.json"}

    if 'mcp' not in existing:
        existing['mcp'] = {}

    tally_cmd = get_tally_command()

    # OpenCode uses command as array
    existing['mcp']['tally'] = {
        "type": "local",
        "command": [tally_cmd, "mcp"],
        "enabled": True
    }

    config_path.write_text(json.dumps(existing, indent=2))
    print(f"Added tally to OpenCode config: {config_path}")
    return True


def setup_gemini():
    """Add tally to Gemini CLI config."""
    config_path = Path.home() / '.gemini' / 'settings.json'

    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    if 'mcpServers' not in existing:
        existing['mcpServers'] = {}

    tally_cmd = get_tally_command()
    tally_args = get_tally_args()

    existing['mcpServers']['tally'] = {
        "command": tally_cmd,
        "args": tally_args
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2))

    print(f"Added tally to Gemini CLI config: {config_path}")
    return True


def output_json_config():
    """Output JSON config for manual setup."""
    tally_cmd = get_tally_command()
    tally_args = get_tally_args()

    config = {
        "mcpServers": {
            "tally": {
                "command": tally_cmd,
                "args": tally_args
            }
        }
    }
    print(json.dumps(config, indent=2))


def run_mcp_init(client=None, output_json=False):
    """
    Set up tally MCP server on various clients.

    Args:
        client: Specific client to configure (or None for auto-detect)
        output_json: If True, output JSON config instead of setting up
    """
    if output_json:
        output_json_config()
        return

    # Map client names to setup functions
    setup_functions = {
        'claude-desktop': setup_claude_desktop,
        'vscode': setup_vscode,
        'cursor': setup_cursor,
        'claude-code': setup_claude_code,
        'opencode': setup_opencode,
        'gemini': setup_gemini,
    }

    if client:
        if client not in setup_functions:
            print(f"Unknown client: {client}", file=sys.stderr)
            print(f"Valid clients: {', '.join(setup_functions.keys())}", file=sys.stderr)
            sys.exit(1)
        setup_functions[client]()
        return

    # Auto-detect available clients
    detected = detect_mcp_clients()

    if not detected:
        print("No supported MCP clients detected.")
        print("Add this to your MCP config manually:")
        print()
        output_json_config()
        return

    print(f"Detected MCP clients: {', '.join(detected)}")
    print()

    # Set up the first detected client
    for client_name in detected:
        print(f"Setting up {client_name}...")
        if setup_functions[client_name]():
            print()
            print("To set up additional clients, run:")
            for other in detected:
                if other != client_name:
                    print(f"  tally mcp init --client {other}")
            break
