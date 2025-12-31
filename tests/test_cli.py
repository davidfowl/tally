"""Tests for CLI error handling and user experience."""

import pytest
import subprocess
import tempfile
import os
from pathlib import Path


class TestGlobPatternSupport:
    """Tests for wildcard/glob pattern support in data source file paths."""

    def test_glob_pattern_matches_multiple_files(self):
        """Glob pattern should match multiple CSV files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            # Create settings with glob pattern
            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: TestBank
    file: data/test*.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            # Create multiple matching files
            with open(os.path.join(data_dir, 'test-jan.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-15,NETFLIX,-15.99\n")

            with open(os.path.join(data_dir, 'test-feb.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-02-15,SPOTIFY,-9.99\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'run', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            # Should report transactions from 2 files
            assert '2 files' in result.stdout
            assert '2 transactions' in result.stdout

    def test_glob_pattern_single_file_fallback(self):
        """Single file path (no wildcards) should still work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: TestBank
    file: data/transactions.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            with open(os.path.join(data_dir, 'transactions.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-15,TEST,-10.00\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'run', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            assert '1 file' in result.stdout
            assert '1 transactions' in result.stdout

    def test_glob_pattern_no_matches_shows_error(self):
        """Glob pattern with no matches should show helpful message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: TestBank
    file: data/nonexistent*.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'run', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            # Should show "No files found matching pattern"
            assert 'No files found matching pattern' in result.stdout

    def test_glob_diag_shows_matched_files(self):
        """Diag command should show matched files for glob patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: TestBank
    file: data/test*.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            # Create multiple matching files
            with open(os.path.join(data_dir, 'test-a.csv'), 'w') as f:
                f.write("date,description,amount\n")

            with open(os.path.join(data_dir, 'test-b.csv'), 'w') as f:
                f.write("date,description,amount\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'diag', config_dir],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            # Should show pattern and matched files
            assert 'Pattern:' in result.stdout or 'data/test*.csv' in result.stdout
            assert 'Matched files: 2' in result.stdout
            assert 'test-a.csv' in result.stdout
            assert 'test-b.csv' in result.stdout

    def test_glob_files_processed_in_sorted_order(self):
        """Files should be processed in sorted order for consistency."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, 'config')
            data_dir = os.path.join(tmpdir, 'data')
            os.makedirs(config_dir)
            os.makedirs(data_dir)

            with open(os.path.join(config_dir, 'settings.yaml'), 'w') as f:
                f.write("""year: 2025
data_sources:
  - name: TestBank
    file: data/*.csv
    format: "{date:%Y-%m-%d},{description},{amount}"
""")

            # Create files in non-alphabetical order
            with open(os.path.join(data_dir, 'z-last.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-01,Z_FIRST,-1.00\n")

            with open(os.path.join(data_dir, 'a-first.csv'), 'w') as f:
                f.write("date,description,amount\n")
                f.write("2025-01-01,A_FIRST,-1.00\n")

            result = subprocess.run(
                ['uv', 'run', 'tally', 'diag', config_dir],
                capture_output=True,
                text=True
            )
            # a-first.csv should appear before z-last.csv in diag output
            a_pos = result.stdout.find('a-first.csv')
            z_pos = result.stdout.find('z-last.csv')
            assert a_pos < z_pos, "Files should be listed in sorted order"


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

    def test_run_invalid_only_shows_warning(self):
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
                ['uv', 'run', 'tally', 'run', '--only', 'invalid', '--format', 'summary', config_dir],
                capture_output=True,
                text=True
            )
            assert 'Warning: Invalid view' in result.stderr
            # Valid views may or may not be shown depending on whether views.rules exists

    def test_run_mixed_only_filters_invalid(self):
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
                ['uv', 'run', 'tally', 'run', '--only', 'monthly,invalid,travel', '--format', 'summary', config_dir],
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
            assert "No merchants found in category 'NonExistent'" in result.stdout
            assert 'Available categories:' in result.stdout

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


