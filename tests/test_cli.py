"""Tests for CLI error handling and user experience."""

import json
import pytest
import subprocess
import tempfile
import os
from pathlib import Path


class TestCLIErrorHandling:
    """Tests for helpful error messages when CLI is misused."""

    def test_explain_no_config_suggests_init(self):
        """Running explain without config should suggest tally init."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ['uv', 'run', 'tally', 'explain'],
                cwd=tmpdir,
                capture_output=True,
                text=True
            )
            assert result.returncode == 1
            assert 'tally init' in result.stderr

    def test_explain_invalid_merchant_suggests_similar(self):
        """Typo in merchant name should suggest similar names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up minimal config
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            # Create settings
            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: Test
    file: data/test.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            # Create merchant rules file
            with open(os.path.join(config_dir, 'merchant_categories.csv'), 'w') as f:
                f.write("Pattern,Merchant,Category,Subcategory\n")
                f.write("NETFLIX,Netflix,Subscriptions,Streaming\n")

            # Create test data with Netflix
            with open(os.path.join(data_dir, 'test.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-15,NETFLIX STREAMING,15.99\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'explain', 'Netflx', config_dir],
                capture_output=True,
                text=True
            )
            assert result.returncode == 1
            assert 'Did you mean' in result.stderr
            assert 'Netflix' in result.stderr

    def test_up_invalid_only_shows_warning(self):
        """Invalid --only value should warn and show valid options."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up minimal config
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

            result = subprocess.run(
                ['uv', 'run', 'tally', 'up', '--only', 'invalid', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            assert 'Warning: Invalid view' in result.stderr
            # Valid views may or may not be shown depending on whether views.rules exists

    def test_up_mixed_only_filters_invalid(self):
        """Mixed valid/invalid --only values should warn about invalid ones."""
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

            result = subprocess.run(
                ['uv', 'run', 'tally', 'up', '--only', 'monthly,invalid,travel', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            assert 'Warning: Invalid view' in result.stderr
            assert 'invalid' in result.stderr
            # Should exit since no valid views remain
            # (monthly and travel are not valid view names anymore)

    def test_explain_invalid_category_shows_available(self):
        """Invalid --category should show available categories."""
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

            # Create merchant rules file
            with open(os.path.join(config_dir, 'merchant_categories.csv'), 'w') as f:
                f.write("Pattern,Merchant,Category,Subcategory\n")
                f.write("NETFLIX,Netflix,Subscriptions,Streaming\n")

            # Create data that will be categorized
            with open(os.path.join(data_dir, 'test.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-15,NETFLIX STREAMING,15.99\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'explain', '--category', 'NonExistent', config_dir],
                capture_output=True,
                text=True
            )
            assert "No merchants found matching: category:NonExistent" in result.stdout
            assert 'Available categories:' in result.stdout

    def test_explain_summary_handles_tuple_merchant_keys(self):
        """Default explain summary should render duplicate merchant names without crashing."""
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
merchants_file: config/merchants.rules
""")

            with open(os.path.join(config_dir, 'merchants.rules'), 'w') as f:
                f.write("""[School Food]
match: contains("ROCHESTER PUBLIC SCHOOLS CAFE")
category: Food
subcategory: School Meals
merchant: Rochester Public Schools

[School Fees]
match: contains("ROCHESTER PUBLIC SCHOOLS ACTIVITY")
category: Education
subcategory: Fees
merchant: Rochester Public Schools
""")

            with open(os.path.join(data_dir, 'test.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-05,ROCHESTER PUBLIC SCHOOLS CAFE,42.50\n")
                f.write("2025-01-12,ROCHESTER PUBLIC SCHOOLS ACTIVITY FEE,75.00\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'explain', '--config', config_dir],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            assert 'Rochester Public Schools' in result.stdout
            assert "('Rochester Public Schools'" not in result.stdout

    def test_invalid_format_shows_choices(self):
        """Invalid --format should show valid choices."""
        result = subprocess.run(
            ['uv', 'run', 'tally', 'run', '--format', 'invalid'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 2
        assert 'invalid choice' in result.stderr
        assert 'html' in result.stderr
        assert 'json' in result.stderr

    def test_invalid_view_shows_available(self):
        """Invalid --view should show available views."""
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

            result = subprocess.run(
                ['uv', 'run', 'tally', 'explain', '--view', 'invalid', config_dir],
                capture_output=True,
                text=True
            )
            # Should fail because 'invalid' is not a valid view
            assert result.returncode == 1
            # Message may be in stdout or stderr depending on error type
            output = result.stdout + result.stderr
            assert 'No view' in output or 'views' in output.lower()


class TestExplainJsonOutput:
    """Tests for JSON explain output."""

    def _create_config(self, tmpdir):
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

        with open(os.path.join(config_dir, 'merchant_categories.csv'), 'w') as f:
            f.write("Pattern,Merchant,Category,Subcategory\n")
            f.write("AMAZON BOOKS,Amazon,Shopping,Books\n")
            f.write("AMAZON PRIME,Amazon,Subscriptions,Streaming\n")
            f.write("AMAZE CAFE,Amaze Cafe,Food,Coffee\n")

        with open(os.path.join(data_dir, 'test.csv'), 'w') as f:
            f.write("date,description,amount\n")
            f.write("2025-01-15,AMAZON BOOKS ORDER,12.99\n")
            f.write("2025-01-18,AMAZON PRIME MEMBERSHIP,14.99\n")
            f.write("2025-01-20,AMAZE CAFE LATTE,6.50\n")

        return config_dir

    @pytest.mark.parametrize(
        ("query", "match_mode", "matched_names", "merchant_count"),
        [
            ("Amazon", "exact", ["Amazon"], 2),
            ("amazon", "case_insensitive", ["Amazon"], 2),
            ("Amaz", "partial", ["Amaze Cafe", "Amazon"], 3),
        ],
    )
    def test_explain_json_match_modes_return_single_payload(self, query, match_mode, matched_names, merchant_count):
        """Exact/case-insensitive/partial explain JSON should be a single parseable payload."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = self._create_config(tmpdir)

            result = subprocess.run(
                ['uv', 'run', 'tally', 'explain', '--format', 'json', query, config_dir],
                capture_output=True,
                text=True
            )

            assert result.returncode == 0
            payload = json.loads(result.stdout)
            assert payload['query'] == query
            assert payload['match_mode'] == match_mode
            assert payload['matched_names'] == matched_names
            assert len(payload['merchants']) == merchant_count
            assert [merchant['name'] for merchant in payload['merchants']].count('Amazon') == min(merchant_count, 2)


class TestMigration:
    """Tests for migration from old tally format to new format."""

    def test_init_detects_existing_config_directory(self):
        """Running tally init in existing config dir should use current dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create existing config structure (like old tally would)
            config_dir = os.path.join(tmpdir, 'config')
            os.makedirs(config_dir)
            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("year: 2025\n")

            # Run tally init (default would create ./tally/)
            result = subprocess.run(
                ['uv', 'run', 'tally', 'init'],
                cwd=tmpdir,
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            # Should detect existing config and use current dir
            assert 'Found existing config/' in result.stdout


    def test_init_migrates_csv_to_rules(self):
        """Running tally init should migrate merchant_categories.csv to merchants.rules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            os.makedirs(config_dir)

            # Create old-style settings.yaml
            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("year: 2025\n")

            # Create old-style merchant_categories.csv with rules
            with open(os.path.join(config_dir, 'merchant_categories.csv'), 'w') as f:
                f.write("Pattern,Merchant,Category,Subcategory\n")
                f.write("NETFLIX,Netflix,Subscriptions,Streaming\n")
                f.write("AMAZON,Amazon,Shopping,Online\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'init'],
                cwd=tmpdir,
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            # Should mention migration
            assert 'legacy' in result.stdout.lower() or 'converting' in result.stdout.lower()
            # Should create merchants.rules
            assert os.path.exists(os.path.join(config_dir, 'merchants.rules'))
            # Should backup old CSV
            assert os.path.exists(os.path.join(config_dir, 'merchant_categories.csv.bak'))
            # Old CSV should be gone
            assert not os.path.exists(os.path.join(config_dir, 'merchant_categories.csv'))

            # Verify merchants.rules has the converted rules
            with open(os.path.join(config_dir, 'merchants.rules'), 'r') as f:
                content = f.read()
            assert 'Netflix' in content
            assert 'Amazon' in content

    def test_init_updates_settings_yaml(self):
        """Running tally init should add merchants_file and views_file to settings.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            os.makedirs(config_dir)

            # Create minimal old-style settings.yaml
            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("year: 2025\ntitle: Test\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'init'],
                cwd=tmpdir,
                capture_output=True,
                text=True
            )
            assert result.returncode == 0

            # Check settings.yaml was updated
            with open(os.path.join(config_dir, 'settings.yaml'), 'r') as f:
                content = f.read()
            assert 'views_file:' in content
            assert 'config/views.rules' in content

    def test_init_skips_migration_for_empty_csv(self):
        """CSV with only headers/comments should not trigger migration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            os.makedirs(config_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("year: 2025\n")

            # Create CSV with only header, no rules
            with open(os.path.join(config_dir, 'merchant_categories.csv'), 'w') as f:
                f.write("# Comments\n")
                f.write("Pattern,Merchant,Category,Subcategory\n")
                f.write("# More comments\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'init'],
                cwd=tmpdir,
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            # Should NOT mention migration (no rules to migrate)
            assert 'converting' not in result.stdout.lower()
            # CSV should still exist (not renamed to .bak)
            assert os.path.exists(os.path.join(config_dir, 'merchant_categories.csv'))

    def test_run_migrate_flag_converts_csv(self):
        """Running tally run --migrate should convert CSV to rules format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            # Create settings with data source
            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: Test
    file: data/test.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            # Create old-style CSV rules
            with open(os.path.join(config_dir, 'merchant_categories.csv'), 'w') as f:
                f.write("Pattern,Merchant,Category,Subcategory\n")
                f.write("TEST,Test Merchant,Shopping,General\n")

            # Create test data
            with open(os.path.join(data_dir, 'test.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-15,TEST PURCHASE,-10.00\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'run', '--migrate', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            # Should succeed and create merchants.rules
            assert os.path.exists(os.path.join(config_dir, 'merchants.rules'))


class TestMonthFilter:
    """Tests for the --month filter parsing logic."""

    def test_yyyy_mm_format_passes_through(self):
        """YYYY-MM format should be returned as-is."""
        from tally.commands.explain import _parse_month_filter
        available = {'2025-01', '2025-02', '2025-03'}
        assert _parse_month_filter('2025-02', available) == '2025-02'
        # Even if not in available, YYYY-MM passes through
        assert _parse_month_filter('2024-12', available) == '2024-12'

    def test_month_name_single_match(self):
        """Month name with single year match should find it."""
        from tally.commands.explain import _parse_month_filter
        available = {'2025-01', '2025-02', '2025-03'}
        assert _parse_month_filter('Jan', available) == '2025-01'
        assert _parse_month_filter('feb', available) == '2025-02'
        assert _parse_month_filter('MARCH', available) == '2025-03'

    def test_month_name_multiple_years_picks_most_recent(self):
        """Month name appearing in multiple years should pick most recent."""
        from tally.commands.explain import _parse_month_filter
        available = {'2024-03', '2025-03', '2023-03'}
        assert _parse_month_filter('Mar', available) == '2025-03'
        assert _parse_month_filter('march', available) == '2025-03'

    def test_month_name_no_match_returns_none(self):
        """Month name not in data should return None."""
        from tally.commands.explain import _parse_month_filter
        available = {'2025-01', '2025-02', '2025-03'}
        assert _parse_month_filter('Dec', available) is None
        assert _parse_month_filter('december', available) is None

    def test_month_number_single_digit(self):
        """Single digit month numbers should work."""
        from tally.commands.explain import _parse_month_filter
        available = {'2025-01', '2025-02', '2025-03'}
        assert _parse_month_filter('1', available) == '2025-01'
        assert _parse_month_filter('2', available) == '2025-02'
        assert _parse_month_filter('3', available) == '2025-03'

    def test_month_number_double_digit(self):
        """Double digit month numbers should work."""
        from tally.commands.explain import _parse_month_filter
        available = {'2025-10', '2025-11', '2025-12'}
        assert _parse_month_filter('10', available) == '2025-10'
        assert _parse_month_filter('11', available) == '2025-11'
        assert _parse_month_filter('12', available) == '2025-12'

    def test_month_number_invalid(self):
        """Invalid month numbers should return None."""
        from tally.commands.explain import _parse_month_filter
        available = {'2025-01', '2025-02', '2025-03'}
        assert _parse_month_filter('0', available) is None
        assert _parse_month_filter('13', available) is None
        assert _parse_month_filter('-1', available) is None

    def test_month_number_multiple_years(self):
        """Month number appearing in multiple years should pick most recent."""
        from tally.commands.explain import _parse_month_filter
        available = {'2024-01', '2025-01'}
        assert _parse_month_filter('1', available) == '2025-01'

    def test_empty_available_months(self):
        """Empty available months should return None for names/numbers."""
        from tally.commands.explain import _parse_month_filter
        available = set()
        assert _parse_month_filter('Jan', available) is None
        assert _parse_month_filter('1', available) is None
        # YYYY-MM still passes through
        assert _parse_month_filter('2025-01', available) == '2025-01'

    def test_invalid_input(self):
        """Random strings should return None."""
        from tally.commands.explain import _parse_month_filter
        available = {'2025-01', '2025-02', '2025-03'}
        assert _parse_month_filter('foo', available) is None
        assert _parse_month_filter('', available) is None
        assert _parse_month_filter('2025', available) is None
        assert _parse_month_filter('01-2025', available) is None

    def test_case_insensitivity(self):
        """Month names should be case-insensitive."""
        from tally.commands.explain import _parse_month_filter
        available = {'2025-01'}
        assert _parse_month_filter('jan', available) == '2025-01'
        assert _parse_month_filter('JAN', available) == '2025-01'
        assert _parse_month_filter('Jan', available) == '2025-01'
        assert _parse_month_filter('JANUARY', available) == '2025-01'
        assert _parse_month_filter('january', available) == '2025-01'
        assert _parse_month_filter('January', available) == '2025-01'


class TestDataSourcePaths:
    """Tests for data source folder and glob support."""

    def _write_csv(self, path, rows):
        with open(path, 'w') as f:
            f.write("date,description,amount\n")
            for row in rows:
                f.write(row + "\n")

    def test_up_directory_top_level_only(self):
        """Directory sources should load top-level CSVs only (non-recursive)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            nested_dir = os.path.join(data_dir, 'nested')
            os.makedirs(config_dir)
            os.makedirs(data_dir)
            os.makedirs(nested_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: Test
    file: data
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            self._write_csv(os.path.join(data_dir, 'a.csv'), ["2025-01-01,ONE,10.00"])
            self._write_csv(os.path.join(data_dir, 'b.csv'), ["2025-01-02,TWO,20.00"])
            self._write_csv(os.path.join(nested_dir, 'c.csv'), ["2025-01-03,THREE,30.00"])

            result = subprocess.run(
                ['uv', 'run', 'tally', 'up', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            assert "Test: 2 transactions" in result.stdout

    def test_up_glob_star_matches_top_level(self):
        """Single-star globs should match top-level CSVs only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            nested_dir = os.path.join(data_dir, 'nested')
            os.makedirs(config_dir)
            os.makedirs(data_dir)
            os.makedirs(nested_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: Test
    file: data/*.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            self._write_csv(os.path.join(data_dir, 'a.csv'), ["2025-01-01,ONE,10.00"])
            self._write_csv(os.path.join(data_dir, 'b.csv'), ["2025-01-02,TWO,20.00"])
            self._write_csv(os.path.join(nested_dir, 'c.csv'), ["2025-01-03,THREE,30.00"])

            result = subprocess.run(
                ['uv', 'run', 'tally', 'up', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            assert "Test: 2 transactions" in result.stdout

    def test_up_glob_double_star_matches_recursive(self):
        """Double-star globs should match CSVs recursively."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            nested_dir = os.path.join(data_dir, 'nested')
            os.makedirs(config_dir)
            os.makedirs(data_dir)
            os.makedirs(nested_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: Test
    file: data/**/*.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            self._write_csv(os.path.join(data_dir, 'a.csv'), ["2025-01-01,ONE,10.00"])
            self._write_csv(os.path.join(data_dir, 'b.csv'), ["2025-01-02,TWO,20.00"])
            self._write_csv(os.path.join(nested_dir, 'c.csv'), ["2025-01-03,THREE,30.00"])

            result = subprocess.run(
                ['uv', 'run', 'tally', 'up', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            assert "Test: 3 transactions" in result.stdout


class TestMarkdownRendering:
    """Tests for terminal markdown rendering."""

    # ANSI codes
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

    def test_render_header(self):
        """Headers should render as bold without # symbols."""
        from tally.commands.update import _render_markdown_line

        assert _render_markdown_line('# Title') == f'{self.BOLD}Title{self.RESET}'
        assert _render_markdown_line('## Section') == f'{self.BOLD}Section{self.RESET}'
        assert _render_markdown_line('### Subsection') == f'{self.BOLD}Subsection{self.RESET}'

    def test_render_bullet_dash(self):
        """Dash bullets should render with bullet character."""
        from tally.commands.update import _render_markdown_line

        assert _render_markdown_line('- Item one') == '  • Item one'
        assert _render_markdown_line('- Another item') == '  • Another item'

    def test_render_bullet_asterisk(self):
        """Asterisk bullets should render with bullet character."""
        from tally.commands.update import _render_markdown_line

        assert _render_markdown_line('* Item one') == '  • Item one'
        assert _render_markdown_line('* Another item') == '  • Another item'

    def test_render_inline_bold(self):
        """Bold text should render with ANSI bold."""
        from tally.commands.update import _render_markdown_line

        result = _render_markdown_line('This is **bold** text')
        assert result == f'This is {self.BOLD}bold{self.RESET} text'

    def test_render_inline_code(self):
        """Inline code should render dim."""
        from tally.commands.update import _render_markdown_line

        result = _render_markdown_line('Run `tally update` now')
        assert result == f'Run {self.DIM}tally update{self.RESET} now'

    def test_render_bullet_with_formatting(self):
        """Bullets with inline formatting should work."""
        from tally.commands.update import _render_markdown_line

        result = _render_markdown_line('- Added **CSV export** for `tally run`')
        assert f'{self.BOLD}CSV export{self.RESET}' in result
        assert f'{self.DIM}tally run{self.RESET}' in result
        assert result.startswith('  • ')

    def test_render_plain_text(self):
        """Plain text should pass through unchanged."""
        from tally.commands.update import _render_markdown_line

        assert _render_markdown_line('Just plain text') == 'Just plain text'
        assert _render_markdown_line('') == ''


class TestUpdateReleaseNotes:
    """Tests for release notes display after update."""

    def test_show_release_notes_displays_summary(self, capsys):
        """Release notes should show first few lines."""
        from tally.commands.update import _show_release_notes

        release_info = {
            'version': '0.1.50',
            'release_url': 'https://github.com/davidfowl/tally/releases/tag/v0.1.50',
            'body': """### New Features
- Added CSV export format
- Improved error messages

### Bug Fixes
- Fixed parsing issues

## Install
curl -fsSL ..."""
        }

        _show_release_notes(release_info)
        captured = capsys.readouterr()

        assert "What's New" in captured.out
        assert "New Features" in captured.out
        assert "CSV export" in captured.out
        assert "Bug Fixes" in captured.out
        # Should stop before install section
        assert "curl" not in captured.out
        assert release_info['release_url'] in captured.out

    def test_show_release_notes_truncates_long_content(self, capsys):
        """Long release notes should be truncated with ellipsis."""
        from tally.commands.update import _show_release_notes

        long_body = "\n".join([f"- Feature {i}: some description" for i in range(50)])
        release_info = {
            'version': '0.1.50',
            'release_url': 'https://github.com/davidfowl/tally/releases/tag/v0.1.50',
            'body': long_body
        }

        _show_release_notes(release_info)
        captured = capsys.readouterr()

        # Should have ellipsis indicating truncation
        assert "..." in captured.out
        # Should include release URL
        assert release_info['release_url'] in captured.out

    def test_show_release_notes_empty_body(self, capsys):
        """Empty body should just show release URL."""
        from tally.commands.update import _show_release_notes

        release_info = {
            'version': '0.1.50',
            'release_url': 'https://github.com/davidfowl/tally/releases/tag/v0.1.50',
            'body': ''
        }

        _show_release_notes(release_info)
        captured = capsys.readouterr()

        assert "What's New" not in captured.out
        assert release_info['release_url'] in captured.out

    def test_show_release_notes_no_url(self, capsys):
        """Missing release URL should still show notes."""
        from tally.commands.update import _show_release_notes

        release_info = {
            'version': '0.1.50',
            'release_url': '',
            'body': '### New feature\n- Added stuff'
        }

        _show_release_notes(release_info)
        captured = capsys.readouterr()

        assert "What's New" in captured.out
        assert "New feature" in captured.out
        assert "Full release notes:" not in captured.out
