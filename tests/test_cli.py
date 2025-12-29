"""Tests for CLI error handling and user experience."""

import pytest
import subprocess
import tempfile
import os


class TestCLIErrorHandling:
    """Tests for helpful error messages when CLI is misused."""

    def test_explain_no_config_suggests_init(self):
        """Running explain without config should suggest tally init."""
        result = subprocess.run(
            ['uv', 'run', 'tally', 'explain'],
            cwd='/tmp',
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
            assert 'Warning: Invalid classification' in result.stderr
            assert 'Valid options:' in result.stderr
            assert 'monthly' in result.stderr

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
            assert 'Warning: Invalid classification' in result.stderr
            assert 'invalid' in result.stderr
            # Should still run successfully with valid filters
            assert result.returncode == 0

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

    def test_invalid_classification_shows_choices(self):
        """Invalid --classification should show valid choices."""
        result = subprocess.run(
            ['uv', 'run', 'tally', 'explain', '--classification', 'invalid'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 2
        assert 'invalid choice' in result.stderr
        assert 'monthly' in result.stderr
        assert 'variable' in result.stderr
