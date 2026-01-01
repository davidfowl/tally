"""
Playwright tests for the HTML spending report.

These tests verify:
1. UI Navigation - interactive elements work (expand, filter, sort, theme)
2. Calculation Accuracy - totals, counts, percentages are correct when filtering

Tests skip with a warning if Playwright is not installed.
Run: playwright install chromium
"""
from __future__ import annotations

import os
import subprocess
import warnings
from typing import TYPE_CHECKING

import pytest

# Skip all tests if Playwright not installed
try:
    from playwright.sync_api import expect
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    warnings.warn(
        "Playwright not installed. Skipping HTML report tests. "
        "Install with: playwright install chromium",
        UserWarning
    )

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright not installed"
)


@pytest.fixture(scope="module")
def report_path(tmp_path_factory):
    """Generate a test report with known fixture data.

    Fixture data:
    - 12 transactions across 4 merchants
    - 2 card holders: David and Sarah
    - Total: $1,030.98
    - David's total: $772.49
    - Sarah's total: $258.49
    """
    tmp_dir = tmp_path_factory.mktemp("report_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    # Create test CSV
    csv_content = """Date,Description,Amount,Card Holder
01/05/2024,AMAZON MARKETPLACE,45.99,David
01/10/2024,AMAZON MARKETPLACE,29.99,Sarah
01/15/2024,WHOLE FOODS MARKET,125.50,David
01/18/2024,WHOLE FOODS MARKET,89.00,Sarah
02/01/2024,AMAZON MARKETPLACE,199.00,David
02/05/2024,STARBUCKS,8.50,Sarah
02/10/2024,STARBUCKS,12.00,David
02/15/2024,WHOLE FOODS MARKET,156.00,David
03/01/2024,AMAZON MARKETPLACE,55.00,Sarah
03/05/2024,STARBUCKS,9.00,Sarah
03/10/2024,TARGET,234.00,David
03/15/2024,TARGET,67.00,Sarah
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    # Create settings
    settings_content = """year: 2024

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount},{card_holder}"

merchants_file: config/merchants.rules
"""
    (config_dir / "settings.yaml").write_text(settings_content)

    # Create merchants rules with tags
    rules_content = """[Amazon]
match: normalized("AMAZON")
category: Shopping
subcategory: Online
tags: {field.card_holder}

[Whole Foods]
match: normalized("WHOLE FOODS")
category: Food
subcategory: Grocery
tags: {field.card_holder}

[Starbucks]
match: normalized("STARBUCKS")
category: Food
subcategory: Coffee
tags: {field.card_holder}

[Target]
match: normalized("TARGET")
category: Shopping
subcategory: Retail
tags: {field.card_holder}
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate the report
    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")

    return str(report_file)


# =============================================================================
# Category 1: UI Navigation Tests
# =============================================================================

class TestUINavigation:
    """Tests for interactive UI elements."""

    def test_report_loads_without_errors(self, page: Page, report_path):
        """Report loads and shows correct title."""
        page.goto(f"file://{report_path}")
        expect(page.get_by_test_id("report-title")).to_contain_text("2024 Spending Report")

    def test_total_spending_displayed(self, page: Page, report_path):
        """Total spending card shows the correct amount."""
        page.goto(f"file://{report_path}")
        expect(page.get_by_test_id("total-spending-amount")).to_contain_text("$1,031")

    def test_categories_visible(self, page: Page, report_path):
        """Category sections are visible."""
        page.goto(f"file://{report_path}")
        expect(page.get_by_test_id("section-cat-Shopping")).to_be_visible()
        expect(page.get_by_test_id("section-cat-Food")).to_be_visible()

    def test_merchants_visible_in_table(self, page: Page, report_path):
        """Merchants are visible in their category tables."""
        page.goto(f"file://{report_path}")
        expect(page.get_by_test_id("merchant-row-Amazon")).to_be_visible()
        expect(page.get_by_test_id("merchant-row-Target")).to_be_visible()

    def test_merchant_row_expands_on_click(self, page: Page, report_path):
        """Clicking merchant row expands to show transactions."""
        page.goto(f"file://{report_path}")
        # Click on the Amazon row to expand it
        amazon_row = page.get_by_test_id("merchant-row-Amazon")
        amazon_row.click()
        # Should see transaction details
        expect(page.locator("text=AMAZON MARKETPLACE").first).to_be_visible()

    def test_tag_click_adds_filter(self, page: Page, report_path):
        """Clicking a tag adds it as a filter."""
        page.goto(f"file://{report_path}")
        # Click the 'david' tag badge
        page.get_by_test_id("tag-badge").filter(has_text="david").first.click()
        # A filter chip should appear
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

    def test_search_box_accepts_input(self, page: Page, report_path):
        """Search box accepts text input."""
        page.goto(f"file://{report_path}")
        search = page.locator("input[type='text']")
        search.fill("test")
        expect(search).to_have_value("test")

    def test_theme_toggle_exists(self, page: Page, report_path):
        """Theme toggle button is present."""
        page.goto(f"file://{report_path}")
        expect(page.get_by_test_id("theme-toggle")).to_be_visible()


# =============================================================================
# Category 2: Calculation/Data Accuracy Tests
# =============================================================================

class TestCalculationAccuracy:
    """Tests for correct totals, counts, and percentages."""

    def test_unfiltered_total_spending(self, page: Page, report_path):
        """Total spending matches sum of all transactions."""
        page.goto(f"file://{report_path}")
        # Total: 45.99 + 29.99 + 125.50 + 89.00 + 199.00 + 8.50 + 12.00
        #        + 156.00 + 55.00 + 9.00 + 234.00 + 67.00 = 1030.98 ≈ $1,031
        expect(page.get_by_test_id("total-spending-amount")).to_contain_text("$1,031")

    def test_shopping_category_total(self, page: Page, report_path):
        """Shopping category total is correct."""
        page.goto(f"file://{report_path}")
        # Shopping: Amazon (329.98) + Target (301.00) = 630.98 ≈ $631
        # The total is shown in the category section header
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        expect(shopping_section.locator("text=$631").first).to_be_visible()

    def test_merchant_transaction_count(self, page: Page, report_path):
        """Merchant shows correct transaction count."""
        page.goto(f"file://{report_path}")
        # Amazon has 4 transactions
        amazon_row = page.get_by_test_id("merchant-row-Amazon")
        expect(amazon_row.get_by_test_id("merchant-count")).to_have_text("4")

    def test_tag_filter_updates_total(self, page: Page, report_path):
        """Filtering by tag updates total to only tagged transactions."""
        page.goto(f"file://{report_path}")

        # Click david tag badge
        page.get_by_test_id("tag-badge").filter(has_text="david").first.click()

        # David's transactions total: $772 (rounded)
        expect(page.get_by_test_id("total-spending-amount")).to_contain_text("$772")

    def test_tag_filter_updates_merchant_count(self, page: Page, report_path):
        """Merchant transaction count updates when filtered by tag."""
        page.goto(f"file://{report_path}")

        # Amazon unfiltered: 4 transactions
        amazon_row = page.get_by_test_id("merchant-row-Amazon")
        expect(amazon_row.get_by_test_id("merchant-count")).to_have_text("4")

        # Apply david filter
        page.get_by_test_id("tag-badge").filter(has_text="david").first.click()

        # Amazon filtered: 2 david transactions
        expect(amazon_row.get_by_test_id("merchant-count")).to_have_text("2")

    def test_tag_filter_updates_merchant_total(self, page: Page, report_path):
        """Merchant total amount updates when filtered by tag."""
        page.goto(f"file://{report_path}")

        # Apply david filter
        page.get_by_test_id("tag-badge").filter(has_text="david").first.click()

        # Amazon david total: 45.99 + 199.00 = 244.99 ≈ $245
        amazon_row = page.get_by_test_id("merchant-row-Amazon")
        expect(amazon_row.get_by_test_id("merchant-total")).to_contain_text("$245")

    def test_clear_filter_restores_totals(self, page: Page, report_path):
        """Clearing filter restores original totals."""
        page.goto(f"file://{report_path}")

        # Apply filter
        page.get_by_test_id("tag-badge").filter(has_text="david").first.click()
        expect(page.get_by_test_id("total-spending-amount")).to_contain_text("$772")

        # Clear filter by clicking the remove button on the filter chip
        page.get_by_test_id("filter-chip-remove").first.click()

        # Original total restored
        expect(page.get_by_test_id("total-spending-amount")).to_contain_text("$1,031")
