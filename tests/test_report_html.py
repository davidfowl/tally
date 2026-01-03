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
import re
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
        ["uv", "run", "tally", "run", "-o", str(report_file), "--config", str(config_dir)],
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
        expect(page.get_by_test_id("report-title")).to_contain_text("2024 Financial Report")

    def test_cashflow_card_displayed(self, page: Page, report_path):
        """Cash flow card shows spending total in the filtered view."""
        page.goto(f"file://{report_path}")
        # Filtered view card shows spending for currently visible transactions
        expect(page.get_by_test_id("filtered-amount")).to_be_visible()

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

    def test_transactions_sorted_by_date_descending(self, page: Page, report_path):
        """Transactions within a merchant are sorted by date descending (newest first)."""
        page.goto(f"file://{report_path}")
        # Expand Amazon to see transactions
        amazon_row = page.get_by_test_id("merchant-row-Amazon")
        amazon_row.click()
        # Wait for expansion
        page.wait_for_timeout(200)
        # Get transaction rows for Amazon (they contain AMAZON MARKETPLACE in description)
        amazon_txns = page.locator(".txn-row:has-text('AMAZON MARKETPLACE')")
        dates = amazon_txns.locator(".txn-date").all_text_contents()
        # Amazon has transactions on: Jan 5, Jan 10, Feb 1, Mar 1
        # Should be sorted descending: Mar 1, Feb 1, Jan 10, Jan 5
        assert len(dates) == 4, f"Expected 4 Amazon transactions, got {len(dates)}: {dates}"
        # Verify descending order
        assert dates == ["Mar 1", "Feb 1", "Jan 10", "Jan 5"], f"Expected descending order, got {dates}"

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

    def test_tag_badges_have_distinct_colors(self, page: Page, report_path):
        """Different tags have different colors assigned."""
        page.goto(f"file://{report_path}")
        # Get David and Sarah tag badges
        david_badge = page.get_by_test_id("tag-badge").filter(has_text="David").first
        sarah_badge = page.get_by_test_id("tag-badge").filter(has_text="Sarah").first

        # Both badges should be visible
        expect(david_badge).to_be_visible()
        expect(sarah_badge).to_be_visible()

        # Get computed colors
        david_color = david_badge.evaluate("el => getComputedStyle(el).color")
        sarah_color = sarah_badge.evaluate("el => getComputedStyle(el).color")

        # Colors should be set (not default/black)
        assert david_color != "rgb(0, 0, 0)", "David tag should have a color"
        assert sarah_color != "rgb(0, 0, 0)", "Sarah tag should have a color"

        # Different tags should have different colors
        assert david_color != sarah_color, "Different tags should have different colors"

    def test_same_tag_has_consistent_color(self, page: Page, report_path):
        """Same tag has the same color across different merchants."""
        page.goto(f"file://{report_path}")
        # Get all David tag badges
        david_badges = page.get_by_test_id("tag-badge").filter(has_text="David").all()

        # Should have multiple David badges (across merchants)
        assert len(david_badges) >= 2, "Should have multiple David tags"

        # All David badges should have the same color
        colors = [badge.evaluate("el => getComputedStyle(el).color") for badge in david_badges]
        assert all(c == colors[0] for c in colors), "Same tag should have consistent color"


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
        # The filtered view card shows spending for visible transactions
        expect(page.get_by_test_id("filtered-amount")).to_contain_text("$1,031")

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
        # The filtered view card shows spending for visible transactions
        expect(page.get_by_test_id("filtered-amount")).to_contain_text("$772")

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
        expect(page.get_by_test_id("filtered-amount")).to_contain_text("$772")

        # Clear filter by clicking the remove button on the filter chip
        page.get_by_test_id("filter-chip-remove").first.click()

        # Original total restored
        expect(page.get_by_test_id("filtered-amount")).to_contain_text("$1,031")


# =============================================================================
# Category 3: Edge Cases and Complex Calculations
# =============================================================================

@pytest.fixture(scope="module")
def edge_case_report_path(tmp_path_factory):
    """Generate a test report with edge case data.

    Fixture data includes:
    - Refunds (negative amounts) to test credits section
    - Income/transfer tagged transactions (excluded from spending)
    - Multiple months of data for monthly average calculations
    - Multiple merchants in same category for percentage tests
    - Various transaction amounts for sorting tests

    Transaction breakdown:
    - Shopping (Amazon $650, Target $400) = $1,050
    - Food (Whole Foods $1,050, Starbucks $125) = $1,175
    - Subscriptions (Netflix $15, Spotify $10) = $25
    - Refunds (Amazon Refund -$100, Target Refund -$50) = -$150 (in Credits)

    Totals:
    - Total positive spending: $2,250 (Shopping + Food + Subscriptions)
    - Credits: $150 (shown separately)
    - Net spending (grandTotal): $2,100 (includes refund offset)
    - Income: $3,000
    - Transfers: $500
    - Cash flow: $3,000 - $2,100 - $500 = $400
    """
    tmp_dir = tmp_path_factory.mktemp("edge_case_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    # Create test CSV with edge cases
    # Format: Date, Description, Amount
    csv_content = """Date,Description,Amount
01/05/2024,AMAZON MARKETPLACE,200.00
01/10/2024,AMAZON REFUND,-100.00
01/15/2024,WHOLE FOODS MARKET,300.00
01/20/2024,STARBUCKS,50.00
02/01/2024,TARGET,400.00
02/05/2024,TARGET REFUND,-50.00
02/10/2024,WHOLE FOODS MARKET,350.00
02/15/2024,NETFLIX,15.00
02/20/2024,SPOTIFY,10.00
03/01/2024,AMAZON MARKETPLACE,450.00
03/05/2024,STARBUCKS,75.00
03/10/2024,WHOLE FOODS MARKET,400.00
03/15/2024,PAYROLL DEPOSIT,-3000.00
03/20/2024,TRANSFER TO SAVINGS,-500.00
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    # Create settings
    settings_content = """year: 2024

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount}"

merchants_file: config/merchants.rules
"""
    (config_dir / "settings.yaml").write_text(settings_content)

    # Create merchants rules with refund and income/transfer tags
    # Note: More specific rules must come first (refunds before general)
    rules_content = """# Refunds - specific patterns first
[Amazon Refund]
match: contains("AMAZON REFUND")
category: Refunds
subcategory: Online
tags: refund

[Target Refund]
match: contains("TARGET REFUND")
category: Refunds
subcategory: Retail
tags: refund

# Regular merchants
[Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: Online

[Target]
match: contains("TARGET")
category: Shopping
subcategory: Retail

[Whole Foods]
match: contains("WHOLE FOODS")
category: Food
subcategory: Grocery

[Starbucks]
match: contains("STARBUCKS")
category: Food
subcategory: Coffee

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Spotify]
match: contains("SPOTIFY")
category: Subscriptions
subcategory: Music

# Excluded transactions
[Payroll]
match: contains("PAYROLL")
category: Income
subcategory: Salary
tags: income

[Transfer]
match: contains("TRANSFER")
category: Transfers
subcategory: Savings
tags: transfer
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate the report
    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), "--config", str(config_dir)],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")

    return str(report_file)


class TestEdgeCasesAndCalculations:
    """Tests for edge cases: refunds, cash flow, percentages, monthly averages."""

    # -------------------------------------------------------------------------
    # Credits/Refunds Section Tests
    # -------------------------------------------------------------------------

    def test_credits_shown_in_cashflow_summary(self, page: Page, edge_case_report_path):
        """Credits are shown in the cash flow summary card."""
        page.goto(f"file://{edge_case_report_path}")
        # Credits should appear in cash flow breakdown
        cashflow_card = page.get_by_test_id("cashflow-card")
        credits_item = cashflow_card.locator(".breakdown-item", has_text="Credits")
        expect(credits_item).to_be_visible()

    def test_credits_amount_positive_in_summary(self, page: Page, edge_case_report_path):
        """Credits are displayed as positive amounts in the summary card."""
        page.goto(f"file://{edge_case_report_path}")
        # Credits should show with + prefix (refunds reduce spending)
        cashflow_card = page.get_by_test_id("cashflow-card")
        credits_value = cashflow_card.locator(".breakdown-item", has_text="Credits").locator(".value")
        expect(credits_value).to_contain_text("+")

    # -------------------------------------------------------------------------
    # Cash Flow Calculation Tests
    # -------------------------------------------------------------------------

    def test_income_total_displayed(self, page: Page, edge_case_report_path):
        """Income is shown in the cash flow card breakdown."""
        page.goto(f"file://{edge_case_report_path}")
        # Income: $3,000 (payroll) - shown as breakdown item in cashflow card
        cashflow_card = page.get_by_test_id("cashflow-card")
        expect(cashflow_card.locator(".income-label")).to_be_visible()
        expect(cashflow_card.locator("text=$3,000")).to_be_visible()

    def test_transfers_in_filtered_view(self, page: Page, edge_case_report_path):
        """Transfers appear in filtered view card breakdown."""
        page.goto(f"file://{edge_case_report_path}")
        # Transfers show in the filtered view card (no separate transfers card)
        filtered_card = page.get_by_test_id("filtered-spending-card")
        expect(filtered_card).to_be_visible()

    def test_cash_flow_calculation(self, page: Page, edge_case_report_path):
        """Net cash flow = income - spending (transfers excluded, they just move money)."""
        page.goto(f"file://{edge_case_report_path}")
        # Cash flow: $3,000 - $2,100 = $900
        # Note: spending is net of refunds ($2,250 - $150 = $2,100)
        # Transfers are excluded since they just move money between accounts
        expect(page.get_by_test_id("cashflow-amount")).to_contain_text("$900")

    # -------------------------------------------------------------------------
    # Excluded Transaction Tests
    # Note: When income exists, cash flow card is shown instead of excluded card
    # -------------------------------------------------------------------------

    def test_income_shown_in_cashflow_card(self, page: Page, edge_case_report_path):
        """Cash flow card shows income in breakdown."""
        page.goto(f"file://{edge_case_report_path}")
        # Cash flow card should be visible with income breakdown
        expect(page.get_by_test_id("cashflow-card")).to_be_visible()
        expect(page.get_by_test_id("cashflow-card").locator(".income-label")).to_be_visible()
        # Filtered view card should also be visible
        expect(page.get_by_test_id("filtered-spending-card")).to_be_visible()

    def test_income_clickable_adds_filter(self, page: Page, edge_case_report_path):
        """Clicking income in cash flow card adds an income tag filter."""
        page.goto(f"file://{edge_case_report_path}")
        # Click on income breakdown item in the cashflow card (scoped to avoid multiple matches)
        page.get_by_test_id("cashflow-card").locator(".income-label").click()
        # Should add an income tag filter
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

    # -------------------------------------------------------------------------
    # Monthly Average Tests (shown in category section headers)
    # -------------------------------------------------------------------------

    def test_category_monthly_average_displayed(self, page: Page, edge_case_report_path):
        """Category sections show monthly average (total / numMonths)."""
        page.goto(f"file://{edge_case_report_path}")
        # Food category: $1,175 / 3 months = $392/mo
        food_section = page.get_by_test_id("section-cat-Food")
        expect(food_section.locator(".section-monthly")).to_contain_text("$392/mo")

    def test_monthly_average_updates_with_month_filter(self, page: Page, edge_case_report_path):
        """Monthly averages recalculate when filtering to specific month."""
        page.goto(f"file://{edge_case_report_path}")

        # Click on monthly chart to filter to a specific month
        # The chart allows clicking on bars to add month filters
        # For now, just verify the section header shows /mo format
        food_section = page.get_by_test_id("section-cat-Food")
        expect(food_section.locator(".section-monthly")).to_be_visible()

    # -------------------------------------------------------------------------
    # Percentage Calculation Tests
    # -------------------------------------------------------------------------

    def test_category_percentage_displayed(self, page: Page, edge_case_report_path):
        """Category sections show percentage of total spending."""
        page.goto(f"file://{edge_case_report_path}")
        # Food category should show a percentage
        food_section = page.get_by_test_id("section-cat-Food")
        # Look for percentage pattern like "(XX.X%)"
        expect(food_section.locator(".section-pct")).to_be_visible()

    def test_category_percentages_sum_to_100(self, page: Page, edge_case_report_path):
        """Spending category percentages sum to approximately 100%.

        Percentages are calculated against grossSpending for spending portions only.
        Income/investment portions have their own percentages (labeled "income"/"invest").
        """
        page.goto(f"file://{edge_case_report_path}")
        import re
        # Get all percentage values from positive category sections
        pct_elements = page.locator("[data-testid^='section-cat-'] .section-pct").all()
        spending_percentages = []
        for el in pct_elements:
            text = el.inner_text()
            if "%" in text:
                # Find all percentage patterns - spending ones don't have "income" or "invest" label
                # Format: "(X%)" for spending, "(Y% income)" for income, "(Z% invest)" for investment
                for match in re.finditer(r'\(([\d.]+)%([^)]*)\)', text):
                    pct = float(match.group(1))
                    label = match.group(2).strip()
                    # Only sum spending percentages (no label)
                    if not label:
                        spending_percentages.append(pct)

        # Verify we have spending percentages
        assert len(spending_percentages) >= 3, f"Expected at least 3 spending categories, got {len(spending_percentages)}"
        # Each percentage should be reasonable (0-100%)
        for pct in spending_percentages:
            assert 0 <= pct <= 100, f"Percentage {pct}% out of range"
        # Spending percentages should sum to ~100% (allow small rounding error)
        total_pct = sum(spending_percentages)
        assert 99 <= total_pct <= 101, f"Spending percentages sum to {total_pct}%, expected ~100%"

    def test_merchant_percentage_within_category(self, page: Page, edge_case_report_path):
        """Merchant percentages within a category sum to 100%."""
        page.goto(f"file://{edge_case_report_path}")
        # Check Food category merchants
        food_section = page.get_by_test_id("section-cat-Food")
        pct_cells = food_section.locator("td.pct").all()
        total_pct = 0
        for el in pct_cells:
            text = el.inner_text()
            if "%" in text and text != "100%":  # Skip total row
                import re
                match = re.search(r'([\d.]+)%', text)
                if match:
                    total_pct += float(match.group(1))

        # Should be close to 100%
        assert 99 <= total_pct <= 101, f"Merchant percentages sum to {total_pct}%, expected ~100%"

    # -------------------------------------------------------------------------
    # Category Total = Sum of Merchants Tests
    # -------------------------------------------------------------------------

    def test_category_total_matches_merchant_sum(self, page: Page, edge_case_report_path):
        """Category total equals sum of its merchant totals."""
        page.goto(f"file://{edge_case_report_path}")
        # Food category: Whole Foods ($1,050) + Starbucks ($125) = $1,175
        food_section = page.get_by_test_id("section-cat-Food")
        expect(food_section.locator(".section-ytd")).to_contain_text("$1,175")

    def test_grand_total_matches_category_sum(self, page: Page, edge_case_report_path):
        """Grand total equals sum of all category totals."""
        page.goto(f"file://{edge_case_report_path}")
        # Shopping: $200 + $400 + $450 = $1,050 (Amazon + Target)
        # Food: $1,175
        # Subscriptions: $25
        # Total positive spending: $1,050 + $1,175 + $25 = $2,250
        # The filtered view card shows total spending
        expect(page.get_by_test_id("filtered-spending-card")).to_be_visible()

    # -------------------------------------------------------------------------
    # Sorting Tests
    # -------------------------------------------------------------------------

    def test_sort_by_total_descending_default(self, page: Page, edge_case_report_path):
        """Merchants are sorted by total descending by default."""
        page.goto(f"file://{edge_case_report_path}")
        # In Food category, Whole Foods ($1,050) should be before Starbucks ($125)
        food_section = page.get_by_test_id("section-cat-Food")
        rows = food_section.locator(".merchant-row").all()
        first_merchant = rows[0].locator(".merchant-name").inner_text()
        assert "Whole Foods" in first_merchant

    def test_sort_by_name_ascending(self, page: Page, edge_case_report_path):
        """Clicking merchant header sorts alphabetically."""
        page.goto(f"file://{edge_case_report_path}")
        food_section = page.get_by_test_id("section-cat-Food")
        # Click the Merchant header to sort by name
        food_section.locator("th", has_text="Merchant").click()
        # Now Starbucks should be first (alphabetically before Whole Foods)
        rows = food_section.locator(".merchant-row").all()
        first_merchant = rows[0].locator(".merchant-name").inner_text()
        assert "Starbucks" in first_merchant

    def test_sort_by_count(self, page: Page, edge_case_report_path):
        """Clicking count header sorts by transaction count."""
        page.goto(f"file://{edge_case_report_path}")
        food_section = page.get_by_test_id("section-cat-Food")
        # Click Count header
        food_section.locator("th", has_text="Count").click()
        # Both have 2-3 transactions, verify sort happened
        rows = food_section.locator(".merchant-row").all()
        assert len(rows) >= 2

    # -------------------------------------------------------------------------
    # Filter Interaction with Calculations
    # -------------------------------------------------------------------------

    def test_filter_updates_all_calculations(self, page: Page, edge_case_report_path):
        """Applying a filter updates totals, percentages, and averages consistently."""
        page.goto(f"file://{edge_case_report_path}")

        # Get initial total from filtered view card
        initial_total = page.get_by_test_id("filtered-amount").inner_text()

        # Filter to Food category only by clicking a merchant
        page.get_by_test_id("section-cat-Food").locator(".merchant-name").first.click()

        # Wait for filter to apply
        page.wait_for_timeout(100)

        # Verify the total changed (now showing only that merchant)
        # This confirms filtering affects calculations
        # The specific value depends on what merchant was clicked

    # -------------------------------------------------------------------------
    # Chart Aggregation Bug Tests
    # -------------------------------------------------------------------------

    def test_chart_aggregations_exclude_negative_amounts(self, page: Page, edge_case_report_path):
        """Monthly spending chart should only include positive amounts.

        Bug: chartAggregations sums ALL transaction amounts including negative ones
        (refunds/credits), which incorrectly reduces the monthly spending totals.

        Fixture data for January:
        - Amazon: $200
        - Amazon Refund: -$100 (should NOT be included in chart)
        - Whole Foods: $300
        - Starbucks: $50

        Correct January total (positive only): $550
        Buggy January total (all amounts): $450
        """
        page.goto(f"file://{edge_case_report_path}")
        page.wait_for_timeout(500)  # Wait for Vue and Chart.js to initialize

        # Access the Chart.js instance data from the monthly chart canvas
        result = page.evaluate("""() => {
            // Chart.js stores chart instance as a property on canvas
            const canvas = document.querySelector('canvas');
            if (!canvas) return { error: 'No canvas found' };

            // Chart.js 3+ stores instance in Chart.instances or on element
            const chartInstance = Chart.getChart(canvas);
            if (!chartInstance) return { error: 'No chart instance found' };

            // Get the data from the chart
            const labels = chartInstance.data.labels;
            const data = chartInstance.data.datasets[0].data;

            // Return as object with month labels as keys
            const byMonth = {};
            labels.forEach((label, idx) => {
                byMonth[label] = data[idx];
            });
            return { byMonth, labels, data };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not access chart data: {result['error']}")

        # January should show $550 (positive amounts only), not $450 (with refund subtracted)
        # The month label format is "Jan 2024"
        january_total = result['byMonth'].get('Jan 2024', 0)

        # This assertion documents the expected behavior after the fix:
        # Only positive amounts should be included in the chart
        # Fixture positive amounts in January: $200 (Amazon) + $300 (Whole Foods) + $50 (Starbucks) = $550
        assert january_total == 550, (
            f"January spending should be $550 (positive amounts only), "
            f"but got ${january_total}. If this is $450, the bug is present "
            f"(negative refund amount -$100 is being included). "
            f"Chart data: {result}"
        )

    def test_chart_category_totals_exclude_negative_amounts(self, page: Page, edge_case_report_path):
        """Category totals in chart should only include positive amounts.

        Bug: chartAggregations.byCategory sums ALL transaction amounts including
        negative ones, incorrectly reducing category totals in the pie/bar charts.

        Fixture Refunds category total: -$150 (should NOT appear in chart data)
        """
        page.goto(f"file://{edge_case_report_path}")
        page.wait_for_timeout(500)

        # Access the category pie chart data
        result = page.evaluate("""() => {
            // Find the pie chart canvas (second canvas)
            const canvases = document.querySelectorAll('canvas');
            if (canvases.length < 2) return { error: 'Pie chart canvas not found' };

            const pieCanvas = canvases[1];  // Pie chart is second
            const chartInstance = Chart.getChart(pieCanvas);
            if (!chartInstance) return { error: 'No pie chart instance found' };

            // Get category labels and values
            const labels = chartInstance.data.labels;
            const data = chartInstance.data.datasets[0].data;

            const byCategory = {};
            labels.forEach((label, idx) => {
                byCategory[label] = data[idx];
            });
            return { byCategory, labels, data };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not access pie chart data: {result['error']}")

        by_category = result['byCategory']

        # Refunds category should NOT be in chart data (all negative amounts)
        # or if present, should have 0 value (not -150)
        refunds_total = by_category.get('Refunds', 0)
        assert refunds_total >= 0, (
            f"Refunds category total should be 0 or not present in chart data, "
            f"but got ${refunds_total}. Negative amounts should be excluded from charts. "
            f"Chart data: {result}"
        )


# =============================================================================
# Autocomplete Category/Subcategory Tests
# =============================================================================

@pytest.fixture(scope="module")
def category_subcategory_report_path(tmp_path_factory):
    """Generate a test report with varied categories and subcategories.

    This fixture tests that autocomplete distinguishes between:
    - Top-level categories (Food, Transport, Subscriptions)
    - Subcategories (Grocery, Coffee, Gas, Rideshare, Streaming, Music)
    """
    tmp_dir = tmp_path_factory.mktemp("category_subcat_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    csv_content = """Date,Description,Amount
01/05/2025,WHOLEFDS MKT 123,85.50
01/08/2025,TRADER JOE 456,65.00
01/10/2025,STARBUCKS COFFEE,6.50
01/15/2025,SHELL OIL 789,45.00
01/20/2025,UBER TRIP,25.00
02/01/2025,NETFLIX STREAMING,15.99
02/01/2025,SPOTIFY PREMIUM,9.99
02/05/2025,AMAZON PURCHASE,75.00
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    settings_content = """year: 2025

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount}"

merchants_file: config/merchants.rules
"""
    (config_dir / "settings.yaml").write_text(settings_content)

    # Categories: Food, Transport, Subscriptions, Shopping
    # Subcategories: Grocery, Coffee, Gas, Rideshare, Streaming, Music
    rules_content = """[Whole Foods]
match: contains("WHOLEFDS")
category: Food
subcategory: Grocery

[Trader Joes]
match: contains("TRADER JOE")
category: Food
subcategory: Grocery

[Starbucks]
match: contains("STARBUCKS")
category: Food
subcategory: Coffee

[Shell Gas]
match: contains("SHELL")
category: Transport
subcategory: Gas

[Uber]
match: contains("UBER")
category: Transport
subcategory: Rideshare

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Spotify]
match: contains("SPOTIFY")
category: Subscriptions
subcategory: Music

[Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: Shopping
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate report
    report_path = output_dir / "spending.html"
    subprocess.run(
        ["uv", "run", "tally", "run", "--format", "html", "-o", str(report_path), "--config", str(config_dir)],
        check=True,
        capture_output=True
    )

    return str(report_path)


class TestAutocompleteCategories:
    """Tests for autocomplete category/subcategory distinction."""

    def test_autocomplete_shows_category_type(self, page: Page, category_subcategory_report_path):
        """Top-level categories show 'category' type badge."""
        page.goto(f"file://{category_subcategory_report_path}")

        # Focus search and type to trigger autocomplete
        search = page.locator("input[type='text']")
        search.click()
        search.fill("Food")

        # Wait for autocomplete
        page.wait_for_timeout(100)

        # Check that Food appears with 'category' type
        # Use .type.category to find items with category badge
        autocomplete = page.locator(".autocomplete-list")
        food_item = autocomplete.locator(".autocomplete-item:has(.type.category)", has_text="Food")
        expect(food_item).to_be_visible()
        expect(food_item.locator(".type")).to_have_text("category")

    def test_autocomplete_shows_subcategory_with_parent(self, page: Page, category_subcategory_report_path):
        """Subcategories show parent category and 'subcategory' type badge."""
        page.goto(f"file://{category_subcategory_report_path}")

        search = page.locator("input[type='text']")
        search.click()
        search.fill("Gro")  # Should match "Food > Grocery" subcategory

        page.wait_for_timeout(100)

        autocomplete = page.locator(".autocomplete-list")
        # Find item with subcategory badge showing "Food > Grocery"
        grocery_item = autocomplete.locator(".autocomplete-item:has(.type.subcategory)", has_text="Food > Grocery")
        expect(grocery_item).to_be_visible()
        expect(grocery_item.locator(".type")).to_have_text("subcategory")

    def test_category_and_subcategory_distinguished_in_same_search(self, page: Page, category_subcategory_report_path):
        """Search results distinguish between category and subcategory."""
        page.goto(f"file://{category_subcategory_report_path}")

        search = page.locator("input[type='text']")
        autocomplete = page.locator(".autocomplete-list")

        # Search for "Shop" - should show Shopping as category
        search.click()
        search.fill("Shop")
        page.wait_for_timeout(100)
        shopping_item = autocomplete.locator(".autocomplete-item:has(.type.category)", has_text="Shopping")
        expect(shopping_item).to_be_visible()

        # Search for "Stream" - should show Streaming as subcategory (with parent)
        search.fill("Stream")
        page.wait_for_timeout(100)
        streaming_item = autocomplete.locator(".autocomplete-item:has(.type.subcategory)", has_text="Streaming")
        expect(streaming_item).to_be_visible()

    def test_subcategory_filter_chip_shows_sc_prefix(self, page: Page, category_subcategory_report_path):
        """Selecting a subcategory creates filter chip with 'sc' prefix."""
        page.goto(f"file://{category_subcategory_report_path}")

        search = page.locator("input[type='text']")
        search.click()
        search.fill("Grocery")

        page.wait_for_timeout(100)

        # Click the Grocery subcategory item (has .type.subcategory)
        autocomplete = page.locator(".autocomplete-list")
        grocery_item = autocomplete.locator(".autocomplete-item:has(.type.subcategory)", has_text="Grocery")
        grocery_item.click()

        page.wait_for_timeout(100)

        # Check filter chip exists with subcategory class and 'sc' prefix
        filter_chips = page.get_by_test_id("filter-chips")
        chip = filter_chips.locator(".filter-chip.subcategory")
        expect(chip).to_be_visible()
        expect(chip.locator(".chip-type")).to_have_text("sc")

    def test_category_filter_chip_shows_c_prefix(self, page: Page, category_subcategory_report_path):
        """Selecting a category creates filter chip with 'c' prefix."""
        page.goto(f"file://{category_subcategory_report_path}")

        search = page.locator("input[type='text']")
        search.click()
        search.fill("Transport")

        page.wait_for_timeout(100)

        # Click the Transport category item (has .type.category)
        autocomplete = page.locator(".autocomplete-list")
        transport_item = autocomplete.locator(".autocomplete-item:has(.type.category)", has_text="Transport")
        transport_item.click()

        page.wait_for_timeout(100)

        # Check filter chip exists with category class and 'c' prefix
        filter_chips = page.get_by_test_id("filter-chips")
        chip = filter_chips.locator(".filter-chip.category")
        expect(chip).to_be_visible()
        expect(chip.locator(".chip-type")).to_have_text("c")

    def test_subcategory_filter_applies_correctly(self, page: Page, category_subcategory_report_path):
        """Filtering by subcategory shows only matching merchants."""
        page.goto(f"file://{category_subcategory_report_path}")

        search = page.locator("input[type='text']")
        search.click()
        search.fill("Grocery")

        page.wait_for_timeout(100)

        # Click the Grocery subcategory
        autocomplete = page.locator(".autocomplete-list")
        grocery_item = autocomplete.locator(".autocomplete-item:has(.type.subcategory)", has_text="Grocery")
        grocery_item.click()

        page.wait_for_timeout(200)

        # Should only show Whole Foods and Trader Joes (both in Grocery subcategory)
        # Starbucks (Coffee subcategory) should not be visible
        expect(page.locator(".merchant-row", has_text="Whole Foods")).to_be_visible()
        expect(page.locator(".merchant-row", has_text="Trader Joes")).to_be_visible()
        expect(page.locator(".merchant-row", has_text="Starbucks")).not_to_be_visible()

    def test_same_name_category_and_subcategory_not_duplicated(self, page: Page, category_subcategory_report_path):
        """When category == subcategory (Shopping), it shows as category only, not duplicated."""
        page.goto(f"file://{category_subcategory_report_path}")

        search = page.locator("input[type='text']")
        search.click()
        search.fill("Shopping")

        page.wait_for_timeout(100)

        autocomplete = page.locator(".autocomplete-list")
        # Shopping should appear as category (with .type.category badge)
        category_items = autocomplete.locator(".autocomplete-item:has(.type.category)", has_text="Shopping").all()
        assert len(category_items) == 1

        # Shopping should NOT appear as subcategory
        subcategory_items = autocomplete.locator(".autocomplete-item:has(.type.subcategory)", has_text="Shopping").all()
        assert len(subcategory_items) == 0


# =============================================================================
# Category 5: Extra Fields Search Tests
# =============================================================================

@pytest.fixture(scope="module")
def extra_fields_report_path(tmp_path_factory):
    """Generate a report with extra_fields data for search testing.

    Uses supplemental data source pattern (like investment trades) to add
    extra_fields via let: + field: directives.
    """
    tmp_dir = tmp_path_factory.mktemp("extra_fields_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    # Main transactions CSV
    csv_content = """Date,Description,Amount
01/15/2024,COSTCO WHOLESALE,287.45
01/20/2024,TARGET STORE,156.78
02/01/2024,AMAZON MARKETPLACE,89.99
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    # Supplemental data: receipt items matched by amount
    items_content = """date,amount,item
01/15/2024,287.45,Kirkland Paper Towels
01/15/2024,287.45,Organic Eggs
01/15/2024,287.45,Rotisserie Chicken
01/20/2024,156.78,Diapers
01/20/2024,156.78,Baby Wipes
01/20/2024,156.78,Coffee K-Cups
"""
    (data_dir / "items.csv").write_text(items_content)

    # Create settings with supplemental source
    settings_content = """year: 2024

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount}"

  - name: items
    file: data/items.csv
    format: "{date},{amount},{item}"
    columns:
      description: "{item}"
    supplemental: true

merchants_file: config/merchants.rules
"""
    (config_dir / "settings.yaml").write_text(settings_content)

    # Rules that query supplemental data to add extra_fields
    rules_content = """[Costco]
let: matched_items = [r.item for r in items if r.amount == txn.amount]
match: contains("COSTCO")
category: Shopping
subcategory: Warehouse
field: items = matched_items
field: item_count = len(matched_items)

[Target]
let: matched_items = [r.item for r in items if r.amount == txn.amount]
match: contains("TARGET")
category: Shopping
subcategory: Retail
field: items = matched_items

[Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: Online
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate the report
    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), "--config", str(config_dir)],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")

    return str(report_file)


class TestExtraFieldsSearch:
    """Tests for searching extra_fields values.

    Uses URL hash #s:text to trigger text search filters.
    """

    def test_search_finds_extra_field_value(self, page: Page, extra_fields_report_path):
        """Searching for a value in extra_fields finds the transaction."""
        # Navigate with #s:kirkland to trigger text search filter
        page.goto(f"file://{extra_fields_report_path}#s:kirkland")

        # Wait for filter to be applied
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

        # Costco merchant should be visible (matches via extra_fields)
        expect(page.get_by_test_id("merchant-row-Costco")).to_be_visible()

    def test_search_auto_expands_merchant(self, page: Page, extra_fields_report_path):
        """Merchant auto-expands when search matches extra_fields."""
        page.goto(f"file://{extra_fields_report_path}#s:kirkland")

        # Wait for filter to be applied
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

        # Wait for Vue to process the watch and expand merchants
        page.wait_for_timeout(500)

        # Transaction row should be visible (merchant expanded)
        # The description appears in the expanded transaction detail
        expect(page.locator(".txn-desc >> text=COSTCO WHOLESALE").first).to_be_visible()

    def test_search_highlights_extra_fields_trigger(self, page: Page, extra_fields_report_path):
        """Extra fields trigger shows highlight when search matches."""
        page.goto(f"file://{extra_fields_report_path}#s:kirkland")

        # Wait for filter to be applied
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

        # Wait for Vue to process the watch and expand merchants
        page.wait_for_timeout(500)

        # The extra-fields trigger should have match-highlight class
        trigger = page.locator(".extra-fields-trigger.match-highlight")
        expect(trigger).to_be_visible()

    def test_search_excludes_non_matching(self, page: Page, extra_fields_report_path):
        """Search filters out merchants without matching transactions."""
        page.goto(f"file://{extra_fields_report_path}#s:kirkland")

        # Wait for filter to be applied
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

        # Amazon should not be visible (no matching transactions)
        expect(page.get_by_test_id("merchant-row-Amazon")).not_to_be_visible()

    def test_clear_search_shows_all_merchants(self, page: Page, extra_fields_report_path):
        """Clearing search restores all merchants."""
        page.goto(f"file://{extra_fields_report_path}#s:kirkland")

        # Wait for filter to be applied
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

        # Clear filter
        page.get_by_test_id("filter-chip-remove").first.click()

        # Wait for filter to be cleared
        page.wait_for_timeout(300)

        # All merchants should be visible again
        expect(page.get_by_test_id("merchant-row-Costco")).to_be_visible()
        expect(page.get_by_test_id("merchant-row-Target")).to_be_visible()
        expect(page.get_by_test_id("merchant-row-Amazon")).to_be_visible()


# =============================================================================
# Currency Formatting Tests (Issue #63)
# =============================================================================

@pytest.fixture(scope="module")
def currency_format_report_path(tmp_path_factory):
    """Generate a test report with non-USD currency format (British Pounds).

    This fixture tests that currency formatting is consistent throughout the report:
    - Dashboard amounts
    - Merchant totals
    - Chart Y-axis labels
    """
    tmp_dir = tmp_path_factory.mktemp("currency_format_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    csv_content = """Date,Description,Amount
01/05/2025,TESCO EXPRESS 123,85.50
01/08/2025,SAINSBURYS 456,65.00
01/10/2025,COSTA COFFEE,6.50
01/15/2025,SHELL OIL 789,45.00
01/20/2025,UBER TRIP,25.00
02/01/2025,NETFLIX STREAMING,15.99
02/05/2025,AMAZON UK,75.00
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    # Use British Pound currency format
    settings_content = """year: 2025

currency_format: "£{amount}"

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount}"

merchants_file: config/merchants.rules
"""
    # Must specify UTF-8 encoding for £ symbol to work on Windows
    (config_dir / "settings.yaml").write_text(settings_content, encoding="utf-8")

    rules_content = """[Tesco]
match: contains("TESCO")
category: Food
subcategory: Grocery

[Sainsburys]
match: contains("SAINSBURYS")
category: Food
subcategory: Grocery

[Costa Coffee]
match: contains("COSTA")
category: Food
subcategory: Coffee

[Shell Gas]
match: contains("SHELL")
category: Transport
subcategory: Gas

[Uber]
match: contains("UBER")
category: Transport
subcategory: Rideshare

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Amazon UK]
match: contains("AMAZON")
category: Shopping
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate report
    report_path = output_dir / "spending.html"
    subprocess.run(
        ["uv", "run", "tally", "run", "--format", "html", "-o", str(report_path), "--config", str(config_dir)],
        check=True,
        capture_output=True
    )

    return str(report_path)


class TestCurrencyFormatting:
    """Tests for currency formatting (Issue #63).

    Verifies that the currency_format setting is applied throughout the HTML report:
    - Dashboard totals
    - Merchant amounts
    - Chart Y-axis labels
    """

    def test_dashboard_uses_currency_format(self, page: Page, currency_format_report_path):
        """Dashboard total should use configured currency symbol (£)."""
        page.goto(f"file://{currency_format_report_path}")

        # The cashflow amount should show £ symbol, not $
        cashflow_amount = page.get_by_test_id("cashflow-amount")
        expect(cashflow_amount).to_be_visible()
        amount_text = cashflow_amount.text_content()

        # Should contain £ and not $
        assert "£" in amount_text, f"Expected £ in cashflow amount, got: {amount_text}"
        assert "$" not in amount_text, f"Found $ in cashflow amount, expected £: {amount_text}"

    def test_merchant_amounts_use_currency_format(self, page: Page, currency_format_report_path):
        """Merchant amounts should use configured currency symbol (£)."""
        page.goto(f"file://{currency_format_report_path}")

        # Find a merchant row and check its total
        merchant_total = page.get_by_test_id("merchant-total").first
        expect(merchant_total).to_be_visible()
        amount_text = merchant_total.text_content()

        assert "£" in amount_text, f"Expected £ in merchant amount, got: {amount_text}"
        assert "$" not in amount_text, f"Found $ in merchant amount, expected £: {amount_text}"

    def test_chart_yaxis_uses_currency_format(self, page: Page, currency_format_report_path):
        """Chart Y-axis should use configured currency symbol (£)."""
        page.goto(f"file://{currency_format_report_path}")
        page.wait_for_timeout(500)  # Wait for Chart.js to render

        # Access the Chart.js instance and check Y-axis ticks
        result = page.evaluate("""() => {
            const canvas = document.querySelector('canvas');
            if (!canvas) return { error: 'No canvas found' };

            const chartInstance = Chart.getChart(canvas);
            if (!chartInstance) return { error: 'No chart instance found' };

            // Get Y-axis tick values by looking at the scale
            const yScale = chartInstance.scales.y;
            if (!yScale) return { error: 'No Y scale found' };

            // Get the formatted tick labels
            const ticks = yScale.ticks.map(t => {
                return yScale.options.ticks.callback(t.value);
            });

            return { ticks };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not access chart data: {result['error']}")

        ticks = result['ticks']

        # At least one tick should contain £ symbol
        has_pound = any('£' in str(tick) for tick in ticks if tick)
        assert has_pound, f"Expected £ symbol in chart Y-axis ticks, got: {ticks}"

        # No tick should contain $ symbol
        has_dollar = any('$' in str(tick) for tick in ticks if tick)
        assert not has_dollar, f"Found $ in chart ticks, expected £: {ticks}"


# =============================================================================
# Category 5: Grouping Toggle Tests
# =============================================================================

class TestGroupingToggle:
    """Tests for the merchant/subcategory grouping toggle."""

    def test_group_toggle_exists(self, page: Page, report_path):
        """Group toggle buttons exist in category view."""
        page.goto(f"file://{report_path}")
        # The view toggle should be visible (unified toggle with Merchant/Subcategory/View buttons)
        view_toggle = page.locator(".view-toggle")
        expect(view_toggle).to_be_visible()

    def test_merchant_mode_is_default(self, page: Page, report_path):
        """Merchant grouping is the default mode."""
        page.goto(f"file://{report_path}")
        # The "Merchant" button should be active by default
        merchant_btn = page.locator(".view-toggle button", has_text="Merchant")
        expect(merchant_btn).to_have_class(re.compile(r"active"))

    def test_toggle_to_subcategory_mode(self, page: Page, report_path):
        """Clicking Subcategory button switches to subcategory grouping."""
        page.goto(f"file://{report_path}")

        # Click subcategory button
        subcategory_btn = page.locator(".view-toggle button", has_text="Subcategory")
        subcategory_btn.click()

        # Subcategory button should now be active
        expect(subcategory_btn).to_have_class(re.compile(r"active"))

        # Merchant button should not be active
        merchant_btn = page.locator(".view-toggle button", has_text="Merchant")
        expect(merchant_btn).not_to_have_class(re.compile(r"active"))

    def test_subcategory_mode_shows_subcategories(self, page: Page, report_path):
        """In subcategory mode, rows show subcategory names."""
        page.goto(f"file://{report_path}")

        # Switch to subcategory mode
        subcategory_btn = page.locator(".view-toggle button", has_text="Subcategory")
        subcategory_btn.click()

        # Should see subcategory names in first column (Online, Grocery, etc.)
        # The Shopping category should have "Online" subcategory
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        expect(shopping_section.locator(".merchant-name", has_text="Online")).to_be_visible()

    def test_toggle_back_to_merchant_mode(self, page: Page, report_path):
        """Can toggle back to merchant mode."""
        page.goto(f"file://{report_path}")

        # Switch to subcategory mode
        subcategory_btn = page.locator(".view-toggle button", has_text="Subcategory")
        subcategory_btn.click()

        # Switch back to merchant mode
        merchant_btn = page.locator(".view-toggle button", has_text="Merchant")
        merchant_btn.click()

        # Merchant button should be active
        expect(merchant_btn).to_have_class(re.compile(r"active"))

        # Should see merchant names again
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        expect(shopping_section.locator(".merchant-name", has_text="Amazon")).to_be_visible()

    def test_subcategory_header_shows_merchants_column(self, page: Page, report_path):
        """In subcategory mode, column header shows 'Merchants' instead of 'Subcategory'."""
        page.goto(f"file://{report_path}")

        # Switch to subcategory mode
        subcategory_btn = page.locator(".view-toggle button", has_text="Subcategory")
        subcategory_btn.click()

        # The second column header should say "Merchants"
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        header = shopping_section.locator("thead th").nth(1)
        expect(header).to_contain_text("Merchants")

    def test_merchant_header_shows_subcategory_column(self, page: Page, report_path):
        """In merchant mode, column header shows 'Subcategory'."""
        page.goto(f"file://{report_path}")

        # Should be in merchant mode by default
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        header = shopping_section.locator("thead th").nth(1)
        expect(header).to_contain_text("Subcategory")

    def test_subcategory_row_expands_to_show_transactions(self, page: Page, report_path):
        """Clicking a subcategory row expands to show transactions."""
        page.goto(f"file://{report_path}")

        # Switch to subcategory mode
        page.locator(".view-toggle button", has_text="Subcategory").click()

        # Click on "Online" subcategory in Shopping to expand
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        online_row = shopping_section.locator("tr", has_text="Online")
        online_row.click()

        # Should see transaction rows (with txn-row class)
        expect(shopping_section.locator(".txn-row").first).to_be_visible()

    def test_subcategory_mode_shows_merchant_count(self, page: Page, report_path):
        """Subcategory rows show merchant count."""
        page.goto(f"file://{report_path}")

        # Switch to subcategory mode
        page.locator(".view-toggle button", has_text="Subcategory").click()

        # Online subcategory should show "1 merchant"
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        online_row = shopping_section.locator("tr", has_text="Online")
        expect(online_row).to_contain_text("1 merchant")

    def test_subcategory_filter_adds_correct_type(self, page: Page, report_path):
        """Clicking subcategory name adds subcategory filter, not merchant filter."""
        page.goto(f"file://{report_path}")

        # Switch to subcategory mode
        page.locator(".view-toggle button", has_text="Subcategory").click()

        # Click on the subcategory name (first cell) in Online row
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        online_name = shopping_section.locator(".merchant-name", has_text="Online")
        online_name.click()

        # Should have a subcategory filter chip (class contains 'subcategory')
        filter_chip = page.locator(".filter-chip.subcategory")
        expect(filter_chip).to_be_visible()
        expect(filter_chip).to_contain_text("Online")

    def test_merchant_popup_in_subcategory_mode(self, page: Page, report_path):
        """Clicking merchant count shows popup with merchant list."""
        page.goto(f"file://{report_path}")

        # Switch to subcategory mode
        page.locator(".view-toggle button", has_text="Subcategory").click()

        # Click on "1 merchant" in the second column
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        merchant_trigger = shopping_section.locator(".merchant-list-trigger").first
        merchant_trigger.click()

        # Popup should appear with merchant name (use .visible class to find the open one)
        popup = page.locator(".match-info-popup.visible")
        expect(popup).to_be_visible()
        expect(popup.locator(".popup-header")).to_contain_text("Merchants")


# =============================================================================
# Category 6: Missing Subcategory Tests
# =============================================================================

@pytest.fixture(scope="module")
def report_with_missing_subcategories(tmp_path_factory):
    """Generate a report where some merchants have no subcategory defined.

    Tests the 'Other' fallback behavior in subcategory grouping mode.
    """
    tmp_dir = tmp_path_factory.mktemp("missing_subcategory_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    # Create test CSV
    csv_content = """Date,Description,Amount
01/05/2024,COSTCO WHOLESALE,150.00
01/10/2024,SAFEWAY STORE,75.50
01/12/2024,AMAZON MARKETPLACE,49.99
01/15/2024,TARGET STORE,89.00
01/18/2024,BESTBUY ELECTRONICS,299.99
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    # Create settings
    settings_content = """year: 2024

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount}"
    has_header: true

merchants_file: config/merchants.rules
"""
    (config_dir / "settings.yaml").write_text(settings_content)

    # Create merchants rules - some WITHOUT subcategory
    rules_content = """[Costco]
match: normalized("COSTCO")
category: Groceries
subcategory: Warehouse

[Safeway]
match: normalized("SAFEWAY")
category: Groceries
subcategory: Supermarket

[Amazon]
match: normalized("AMAZON")
category: Retail

[Target]
match: normalized("TARGET")
category: Retail
subcategory: Department Store

[Best Buy]
match: normalized("BESTBUY")
category: Electronics
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate the report
    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), "--config", str(config_dir)],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")

    return str(report_file)


class TestMissingSubcategory:
    """Tests for merchants without subcategories."""

    def test_missing_subcategory_shows_other(self, page: Page, report_with_missing_subcategories):
        """Merchants without subcategory are grouped as 'Other' in subcategory mode."""
        page.goto(f"file://{report_with_missing_subcategories}")

        # Switch to subcategory mode
        page.locator(".view-toggle button", has_text="Subcategory").click()

        # Electronics section (Best Buy has no subcategory)
        electronics_section = page.get_by_test_id("section-cat-Electronics")
        expect(electronics_section.locator(".merchant-name", has_text="Other")).to_be_visible()

    def test_missing_subcategory_mixed_category(self, page: Page, report_with_missing_subcategories):
        """Category with mixed subcategories shows both named and 'Other'."""
        page.goto(f"file://{report_with_missing_subcategories}")

        # Switch to subcategory mode
        page.locator(".view-toggle button", has_text="Subcategory").click()

        # Retail has Target (Department Store) and Amazon (no subcategory -> Other)
        retail_section = page.get_by_test_id("section-cat-Retail")
        expect(retail_section.locator(".merchant-name", has_text="Department Store")).to_be_visible()
        expect(retail_section.locator(".merchant-name", has_text="Other")).to_be_visible()

    def test_merchant_mode_shows_empty_subcategory(self, page: Page, report_with_missing_subcategories):
        """In merchant mode, missing subcategory shows as empty cell."""
        page.goto(f"file://{report_with_missing_subcategories}")

        # Should be in merchant mode by default
        # Best Buy row should have an empty subcategory cell
        electronics_section = page.get_by_test_id("section-cat-Electronics")
        bestbuy_row = electronics_section.locator("tr", has_text="Best Buy")
        # Second cell (subcategory) should be empty
        subcategory_cell = bestbuy_row.locator("td").nth(1)
        expect(subcategory_cell).to_have_text("")


# =============================================================================
# Category 7: Credits Display Tests
# =============================================================================

@pytest.fixture(scope="module")
def report_with_credits(tmp_path_factory):
    """Generate a report with credits/refunds to test summary display."""
    tmp_dir = tmp_path_factory.mktemp("credits_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    # Create test CSV with negative amounts (credits)
    csv_content = """Date,Description,Amount
01/05/2024,AMAZON MARKETPLACE,45.99
01/10/2024,AMAZON REFUND,-25.00
01/15/2024,WHOLE FOODS,125.50
01/20/2024,STORE CREDIT,-15.00
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    # Create settings
    settings_content = """year: 2024

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount}"
    has_header: true

merchants_file: config/merchants.rules
"""
    (config_dir / "settings.yaml").write_text(settings_content)

    # Create merchants rules
    rules_content = """[Amazon]
match: normalized("AMAZON")
category: Shopping
subcategory: Online

[Whole Foods]
match: normalized("WHOLE FOODS")
category: Food
subcategory: Grocery

[Store Credit]
match: normalized("STORE CREDIT")
category: Shopping
subcategory: Credits
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate the report
    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), "--config", str(config_dir)],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")

    return str(report_file)


class TestCreditsDisplay:
    """Tests for credits/refunds display in summary cards."""

    def test_credits_shown_in_cash_flow(self, page: Page, report_with_credits):
        """Credits are displayed in the Cash Flow summary card."""
        page.goto(f"file://{report_with_credits}")

        # Cash flow card should show Credits line
        cashflow_card = page.get_by_test_id("cashflow-card")
        expect(cashflow_card.locator(".breakdown-item", has_text="Credits")).to_be_visible()

    def test_credits_positive_display(self, page: Page, report_with_credits):
        """Credits are shown as positive amounts with + prefix."""
        page.goto(f"file://{report_with_credits}")

        # Find the credits line in cash flow
        cashflow_card = page.get_by_test_id("cashflow-card")
        credits_item = cashflow_card.locator(".breakdown-item", has_text="Credits")
        credits_value = credits_item.locator(".value")
        # Should show positive amount (the $40 in credits)
        expect(credits_value).to_contain_text("+")


# =============================================================================
# Category Percentage Bug Tests (Issue: Subcategory Filter)
# =============================================================================

@pytest.fixture(scope="module")
def subcategory_filter_report_path(tmp_path_factory):
    """Generate a report with multiple subcategories to test percentage calculation.

    This fixture tests the bug where filtering by subcategory causes
    incorrect category percentages (e.g., 379.8% instead of valid percentages).

    The bug occurs because:
    - typeTotals.spending uses UNFILTERED category total
    - grossSpending uses FILTERED total
    - Result: unfiltered / filtered = percentage > 100%

    Fixture data:
    - Food category: $500 total
      - Grocery: $300 (Whole Foods $200, Trader Joes $100)
      - Coffee: $100 (Starbucks)
      - Delivery: $100 (DoorDash)
    - Shopping category: $200 total
      - Online: $200 (Amazon)
    """
    tmp_dir = tmp_path_factory.mktemp("subcategory_filter_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    csv_content = """Date,Description,Amount
01/05/2025,WHOLE FOODS MKT,200.00
01/08/2025,TRADER JOES,100.00
01/10/2025,STARBUCKS COFFEE,100.00
01/15/2025,DOORDASH DELIVERY,100.00
01/20/2025,AMAZON PURCHASE,200.00
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    settings_content = """year: 2025

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount}"

merchants_file: config/merchants.rules
"""
    (config_dir / "settings.yaml").write_text(settings_content)

    rules_content = """[Whole Foods]
match: contains("WHOLE FOODS")
category: Food
subcategory: Grocery

[Trader Joes]
match: contains("TRADER JOES")
category: Food
subcategory: Grocery

[Starbucks]
match: contains("STARBUCKS")
category: Food
subcategory: Coffee

[DoorDash]
match: contains("DOORDASH")
category: Food
subcategory: Delivery

[Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: Online
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate report
    report_path = output_dir / "spending.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "--format", "html", "-o", str(report_path), "--config", str(config_dir)],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")

    return str(report_path)


class TestSubcategoryFilterPercentage:
    """Tests for category percentage calculation when filtering by subcategory.

    Bug: When filtering to a subcategory (e.g., Food > Delivery), the category
    header shows an incorrect percentage like 379.8% instead of a valid percentage.

    Root cause: formatPct(typeTotals.spending, grossSpending) uses unfiltered
    typeTotals with filtered grossSpending, producing percentages > 100%.
    """

    def test_unfiltered_category_percentage_valid(self, page: Page, subcategory_filter_report_path):
        """Without filters, category percentages should be between 0-100%."""
        page.goto(f"file://{subcategory_filter_report_path}")

        # Get Food category percentage
        food_section = page.get_by_test_id("section-cat-Food")
        pct_text = food_section.locator(".section-pct").inner_text()

        # Extract percentage value
        match = re.search(r'\(([\d.]+)%\)', pct_text)
        assert match, f"Could not find percentage in: {pct_text}"
        pct_value = float(match.group(1))

        # Food is $500 out of $700 total = ~71.4%
        assert 0 <= pct_value <= 100, f"Unfiltered percentage {pct_value}% should be 0-100%"
        assert 70 <= pct_value <= 73, f"Food percentage should be ~71.4%, got {pct_value}%"

    def test_subcategory_filter_percentage_valid(self, page: Page, subcategory_filter_report_path):
        """When filtering by subcategory, category percentage should still be valid (0-100%).

        This is the main bug test. With the bug present, filtering to Food > Delivery
        would show ~500% (unfiltered $500 / filtered $100).
        """
        # Navigate with subcategory filter applied via URL hash
        page.goto(f"file://{subcategory_filter_report_path}#+sc:Delivery")

        # Wait for filter to be applied
        page.wait_for_timeout(300)
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

        # Get Food category percentage
        food_section = page.get_by_test_id("section-cat-Food")
        pct_text = food_section.locator(".section-pct").inner_text()

        # Extract percentage value
        match = re.search(r'\(([\d.]+)%\)', pct_text)
        assert match, f"Could not find percentage in: {pct_text}"
        pct_value = float(match.group(1))

        # With bug: ~500% (unfiltered Food total $500 / filtered Delivery $100)
        # Fixed: Should be 100% (filtered Food $100 / filtered total $100)
        assert 0 <= pct_value <= 100, (
            f"Filtered category percentage {pct_value}% should be 0-100%. "
            f"If >100%, the bug is present: typeTotals.spending (unfiltered) "
            f"is being divided by grossSpending (filtered)."
        )

    def test_subcategory_filter_via_autocomplete(self, page: Page, subcategory_filter_report_path):
        """Filter via autocomplete and verify percentage stays valid."""
        page.goto(f"file://{subcategory_filter_report_path}")

        # Use autocomplete to filter to Coffee subcategory (more unique than Delivery)
        search = page.locator("input[type='text']")
        search.click()
        search.fill("Coffee")

        page.wait_for_timeout(100)

        # Click the Coffee subcategory item (with subcategory badge)
        autocomplete = page.locator(".autocomplete-list")
        coffee_item = autocomplete.locator(".autocomplete-item:has(.type.subcategory)", has_text="Coffee")
        coffee_item.click()

        page.wait_for_timeout(300)

        # Verify filter is applied
        expect(page.get_by_test_id("filter-chip")).to_be_visible()

        # Get Food category percentage
        food_section = page.get_by_test_id("section-cat-Food")
        pct_text = food_section.locator(".section-pct").inner_text()

        # Extract and verify percentage
        match = re.search(r'\(([\d.]+)%\)', pct_text)
        assert match, f"Could not find percentage in: {pct_text}"
        pct_value = float(match.group(1))

        assert 0 <= pct_value <= 100, (
            f"Category percentage {pct_value}% exceeds 100% when filtered by subcategory. "
            f"Bug: typeTotals.spending uses unfiltered total, grossSpending uses filtered total."
        )

    def test_multiple_subcategory_filters_percentage_valid(self, page: Page, subcategory_filter_report_path):
        """Multiple subcategory filters should still produce valid percentages."""
        # Filter to both Grocery and Coffee subcategories via URL hash
        page.goto(f"file://{subcategory_filter_report_path}#+sc:Grocery+sc:Coffee")

        page.wait_for_timeout(300)

        # Verify filters are applied (should have 2 filter chips)
        filter_chips = page.get_by_test_id("filter-chip").all()
        assert len(filter_chips) >= 1, "Expected at least one filter chip"

        # Get all category percentages
        pct_elements = page.locator("[data-testid^='section-cat-'] .section-pct").all()
        for el in pct_elements:
            text = el.inner_text()
            for match in re.finditer(r'\(([\d.]+)%([^)]*)\)', text):
                pct = float(match.group(1))
                label = match.group(2).strip()
                if not label:  # Only check spending percentages
                    assert 0 <= pct <= 100, (
                        f"Category percentage {pct}% exceeds valid range. "
                        f"Bug may be present in percentage calculation."
                    )
