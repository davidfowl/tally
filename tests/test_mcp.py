"""Tests for MCP server tools."""

import json
import os
import tempfile

import pytest


class TestMCPTools:
    """Tests for MCP server tool functions."""

    def test_get_version(self):
        """Test get_version tool returns version info."""
        from tally.mcp_server import get_version

        result = get_version()
        data = json.loads(result)

        assert data["name"] == "tally"
        assert "version" in data
        assert "description" in data

    def test_inspect_csv(self):
        """Test inspect_csv with a sample file."""
        from tally.mcp_server import inspect_csv

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write("date,description,amount\n")
            f.write("2025-01-15,TEST MERCHANT,10.00\n")
            f.write("2025-01-16,ANOTHER TEST,25.50\n")
            f.flush()
            temp_path = f.name

        try:
            result = inspect_csv(temp_path)
            data = json.loads(result)

            assert "headers" in data
            assert data["headers"] == ["date", "description", "amount"]
            assert "sample_rows" in data
            assert len(data["sample_rows"]) == 2
            assert data["row_count"] == 2
        finally:
            os.unlink(temp_path)

    def test_inspect_csv_missing_file(self):
        """Test inspect_csv with missing file returns error."""
        from tally.mcp_server import inspect_csv

        result = inspect_csv("/nonexistent/path/file.csv")
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"].lower() or "No such file" in data["error"]

    def test_add_rule(self):
        """Test add_rule creates valid rule entry."""
        from tally.mcp_server import add_rule

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            os.makedirs(config_dir)

            # Create minimal settings
            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("year: 2025\ndata_sources: []\n")

            result = add_rule(
                pattern="TEST.*",
                merchant="Test Merchant",
                category="Shopping",
                subcategory="Online",
                config_dir=config_dir
            )
            data = json.loads(result)

            assert data.get("success") is True
            assert "added" in data.get("message", "").lower()

            # Verify rule was written
            rules_file = os.path.join(config_dir, 'merchant_categories.csv')
            assert os.path.exists(rules_file)
            with open(rules_file) as f:
                content = f.read()
                assert "TEST.*" in content
                assert "Test Merchant" in content
                assert "Shopping" in content

    def test_add_rule_invalid_regex(self):
        """Test add_rule rejects invalid regex patterns."""
        from tally.mcp_server import add_rule

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            os.makedirs(config_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("year: 2025\ndata_sources: []\n")

            result = add_rule(
                pattern="[invalid",  # Invalid regex
                merchant="Test",
                category="Test",
                subcategory="Test",
                config_dir=config_dir
            )
            data = json.loads(result)

            assert "error" in data
            assert "regex" in data["error"].lower()

    def test_add_rule_missing_config_dir(self):
        """Test add_rule with missing config directory."""
        from tally.mcp_server import add_rule

        result = add_rule(
            pattern="TEST.*",
            merchant="Test",
            category="Test",
            subcategory="Test",
            config_dir="/nonexistent/path"
        )
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_run_analysis_missing_config(self):
        """Test run_analysis with missing config returns error."""
        from tally.mcp_server import run_analysis

        result = run_analysis(config_dir="/nonexistent/path")
        data = json.loads(result)

        assert "error" in data

    def test_explain_merchant_missing_config(self):
        """Test explain_merchant with missing config returns error."""
        from tally.mcp_server import explain_merchant

        result = explain_merchant(merchant="Netflix", config_dir="/nonexistent/path")
        data = json.loads(result)

        assert "error" in data

    def test_discover_unknown_missing_config(self):
        """Test discover_unknown with missing config returns error."""
        from tally.mcp_server import discover_unknown

        result = discover_unknown(config_dir="/nonexistent/path")
        data = json.loads(result)

        assert "error" in data

    def test_diagnose_config_missing_dir(self):
        """Test diagnose_config with missing directory."""
        from tally.mcp_server import diagnose_config

        result = diagnose_config(config_dir="/nonexistent/path")
        data = json.loads(result)

        assert data["exists"] is False
        assert len(data["errors"]) > 0

    def test_diagnose_config_valid(self):
        """Test diagnose_config with valid config."""
        from tally.mcp_server import diagnose_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: Test
    file: data/test.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            with open(os.path.join(data_dir, 'test.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-15,TEST,10.00\n")

            result = diagnose_config(config_dir=config_dir)
            data = json.loads(result)

            assert data["exists"] is True
            assert data["settings"] is not None
            assert data["settings"]["year"] == 2025

    def test_explain_summary_invalid_classification(self):
        """Test explain_summary with invalid classification returns error."""
        from tally.mcp_server import explain_summary

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: Test
    file: data/test.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            with open(os.path.join(data_dir, 'test.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-15,TEST,10.00\n")

            result = explain_summary(config_dir=config_dir, classification="invalid")
            data = json.loads(result)

            assert "error" in data
            assert "invalid classification" in data["error"].lower()
            assert "valid_options" in data

    def test_list_rules_baseline(self):
        """Test list_rules returns baseline rules."""
        from tally.mcp_server import list_rules

        # Without a config dir, should return baseline rules
        result = list_rules(config_dir="/nonexistent")
        data = json.loads(result)

        assert "rules" in data
        assert "count" in data
        assert data["count"] > 0  # Should have baseline rules
        # All should be baseline since no user rules file
        for rule in data["rules"]:
            assert rule["source"] == "baseline"

    def test_list_rules_with_user_rules(self):
        """Test list_rules returns user and baseline rules."""
        from tally.mcp_server import list_rules

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            os.makedirs(config_dir)

            # Create user rules file
            with open(os.path.join(config_dir, 'merchant_categories.csv'), 'w') as f:
                f.write("Pattern,Merchant,Category,Subcategory\n")
                f.write("TEST.*,Test Merchant,Shopping,Online\n")

            result = list_rules(config_dir=config_dir)
            data = json.loads(result)

            assert "rules" in data
            # Should have at least our user rule
            user_rules = [r for r in data["rules"] if r["source"] == "user"]
            assert len(user_rules) >= 1
            assert user_rules[0]["pattern"] == "TEST.*"
            assert user_rules[0]["merchant"] == "Test Merchant"

    def test_list_rules_filter_by_category(self):
        """Test list_rules filters by category."""
        from tally.mcp_server import list_rules

        result = list_rules(config_dir="/nonexistent", category="Food")
        data = json.loads(result)

        assert "rules" in data
        for rule in data["rules"]:
            assert rule["category"] == "Food"

    def test_list_rules_filter_by_source(self):
        """Test list_rules filters by source."""
        from tally.mcp_server import list_rules

        result = list_rules(config_dir="/nonexistent", source="baseline")
        data = json.loads(result)

        assert "rules" in data
        for rule in data["rules"]:
            assert rule["source"] == "baseline"


class TestMCPInit:
    """Tests for MCP client setup functions."""

    def test_get_tally_command(self):
        """Test get_tally_command returns valid path."""
        from tally.mcp_init import get_tally_command

        result = get_tally_command()
        assert result is not None
        assert len(result) > 0

    def test_detect_mcp_clients(self):
        """Test detect_mcp_clients returns a list."""
        from tally.mcp_init import detect_mcp_clients

        result = detect_mcp_clients()
        assert isinstance(result, list)

    def test_output_json_config(self, capsys):
        """Test output_json_config outputs valid JSON."""
        from tally.mcp_init import output_json_config

        output_json_config()
        captured = capsys.readouterr()

        data = json.loads(captured.out)
        assert "mcpServers" in data
        assert "tally" in data["mcpServers"]
        assert "command" in data["mcpServers"]["tally"]
        assert "args" in data["mcpServers"]["tally"]


class TestMCPCLI:
    """Tests for MCP CLI commands."""

    def test_mcp_help(self):
        """Test tally mcp --help works."""
        import subprocess

        result = subprocess.run(
            ['uv', 'run', 'tally', 'mcp', '--help'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert 'MCP' in result.stdout or 'mcp' in result.stdout.lower()

    def test_mcp_init_help(self):
        """Test tally mcp init --help works."""
        import subprocess

        result = subprocess.run(
            ['uv', 'run', 'tally', 'mcp', 'init', '--help'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert '--client' in result.stdout
        assert '--json' in result.stdout

    def test_mcp_init_json(self):
        """Test tally mcp init --json outputs valid JSON."""
        import subprocess

        result = subprocess.run(
            ['uv', 'run', 'tally', 'mcp', 'init', '--json'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0

        data = json.loads(result.stdout)
        assert "mcpServers" in data
        assert "tally" in data["mcpServers"]
