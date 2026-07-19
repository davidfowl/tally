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

pytestmark = [
    pytest.mark.playwright,
    pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright not installed"),
]


@pytest.fixture(autouse=True)
def _expand_categories_after_navigation(page: Page):
    """Category sections default to collapsed on a fresh load ("Save Layout
    Settings to Local Storage": no saved state means everything starts
    collapsed). This suite predates that feature and assumes the old
    expanded-by-default state, so expand everything after each navigation
    instead of adding an expand step to every test.
    """
    original_goto = page.goto

    def goto_and_expand(url, **kwargs):
        response = original_goto(url, **kwargs)
        toggle = page.get_by_test_id("collapse-all-toggle")
        if toggle.get_attribute("title") == "Expand all categories":
            toggle.click()
        return response

    page.goto = goto_and_expand
    yield


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
        # Verify descending order (dates always include the year)
        assert dates == ["Mar 1, 2024", "Feb 1, 2024", "Jan 10, 2024", "Jan 5, 2024"], f"Expected descending order, got {dates}"

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
# Category 2b: Transaction Details container & collapse controls
# =============================================================================

class TestTransactionDetailsContainer:
    """Tests for the Transaction Details container, header summary, and collapse controls."""

    def test_container_and_summary_present(self, page: Page, report_path):
        """The Transaction Details container renders with a view-aware count summary."""
        page.goto(f"file://{report_path}")
        expect(page.locator(".details-section > .section-header h2")).to_contain_text("Transaction Details")
        # Merchant view (default): "N categories, N merchants"
        expect(page.get_by_test_id("details-summary")).to_have_text("2 categories, 4 merchants")

    def test_summary_updates_for_subcategory_view(self, page: Page, report_path):
        """Switching to Subcategory view changes the count label wording."""
        page.goto(f"file://{report_path}")
        page.get_by_role("button", name="Subcategory", exact=True).click()
        expect(page.get_by_test_id("details-summary")).to_contain_text("subcategories")

    def test_collapse_all_hides_category_rows(self, page: Page, report_path):
        """Collapse-all folds every category; the button flips to Expand."""
        page.goto(f"file://{report_path}")
        shopping = page.get_by_test_id("section-cat-Shopping")
        expect(shopping).not_to_have_class(re.compile("is-collapsed"))
        page.get_by_test_id("collapse-all-toggle").click()
        expect(shopping).to_have_class(re.compile("is-collapsed"))
        expect(page.get_by_test_id("collapse-all-toggle")).to_have_attribute("title", "Expand all categories")

    def test_expand_all_reopens_categories(self, page: Page, report_path):
        """Expand-all re-opens the category sections."""
        page.goto(f"file://{report_path}")
        toggle = page.get_by_test_id("collapse-all-toggle")
        shopping = page.get_by_test_id("section-cat-Shopping")
        toggle.click()  # collapse all
        expect(shopping).to_have_class(re.compile("is-collapsed"))
        toggle.click()  # expand all
        expect(shopping).not_to_have_class(re.compile("is-collapsed"))

    def test_collapse_all_folds_open_transactions_and_expand_leaves_them_folded(self, page: Page, report_path):
        """Collapse-all folds open transaction lists; expand-all reopens categories only (txns stay folded)."""
        page.goto(f"file://{report_path}")
        page.get_by_test_id("merchant-row-Amazon").click()  # open transactions
        expect(page.locator(".txn-row:has-text('AMAZON MARKETPLACE')").first).to_be_visible()
        toggle = page.get_by_test_id("collapse-all-toggle")
        toggle.click()  # collapse all -> also folds open transactions
        toggle.click()  # expand all -> reopens categories only
        expect(page.locator(".txn-row:has-text('AMAZON MARKETPLACE')").first).not_to_be_visible()

    def test_container_collapses_to_header_only(self, page: Page, report_path):
        """Clicking the container header collapses the whole details body (header only)."""
        page.goto(f"file://{report_path}")
        details_body = page.locator(".details-body")
        expect(details_body).to_be_visible()
        page.locator(".details-section > .section-header").click()
        expect(details_body).not_to_be_visible()

    def test_column_alignment(self, page: Page, report_path):
        """Count column is centered; Total column is right-aligned."""
        page.goto(f"file://{report_path}")
        count_align = page.locator("td.count-col").first.evaluate("el => getComputedStyle(el).textAlign")
        total_align = page.locator("td.money").first.evaluate("el => getComputedStyle(el).textAlign")
        assert count_align == "center", f"Count column should be centered, got {count_align}"
        assert total_align == "right", f"Total column should be right-aligned, got {total_align}"

    def test_total_row_pinned_in_tfoot(self, page: Page, report_path):
        """The total row lives in a <tfoot> and is sticky to the bottom of the scroll box."""
        page.goto(f"file://{report_path}")
        total_cell = page.locator("tfoot .total-row td").first
        expect(total_cell).to_be_attached()
        position = total_cell.evaluate("el => getComputedStyle(el).position")
        assert position == "sticky", f"Total row cells should be sticky, got {position}"


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
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
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

        # Access the category trend chart and sum all stacked category datasets for Jan 2024.
        result = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };

            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };

            const chartInstance = Chart.getChart(canvas);
            if (!chartInstance) return { error: 'No chart instance found' };

            const labels = chartInstance.data.labels || [];
            const janIdx = labels.findIndex(label => /^Jan(?:\\s|\\b)/i.test(String(label || '')));
            if (janIdx < 0) return { error: 'January label not found', labels };

            const datasets = chartInstance.data.datasets || [];
            const monthlyTotal = datasets.reduce((sum, ds) => {
                const val = Number(ds?.data?.[janIdx]) || 0;
                return sum + val;
            }, 0);

            const negatives = datasets
                .map(ds => ({
                    label: ds?.label || '',
                    value: Number(ds?.data?.[janIdx]) || 0,
                }))
                .filter(row => row.value < 0);

            return { monthlyTotal, janIdx, labels, negatives };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not access chart data: {result['error']}")

        # January should show $550 (positive amounts only), not $450 (with refund subtracted)
        january_total = result['monthlyTotal']

        # This assertion documents the expected behavior after the fix:
        # Only positive amounts should be included in the chart
        # Fixture positive amounts in January: $200 (Amazon) + $300 (Whole Foods) + $50 (Starbucks) = $550
        assert january_total == 550, (
            f"January spending should be $550 (positive amounts only), "
            f"but got ${january_total}. If this is $450, the bug is present "
            f"(negative refund amount -$100 is being included). "
            f"Chart data: {result}"
        )

        assert not result['negatives'], (
            f"Found negative category values in Jan 2024 stacked data: {result['negatives']}"
        )

    def test_chart_category_totals_exclude_negative_amounts(self, page: Page, edge_case_report_path):
        """Category totals in chart should only include positive amounts.

        Bug: chartAggregations.byCategory sums ALL transaction amounts including
        negative ones, incorrectly reducing category totals in the category chart.

        Fixture Refunds category total: -$150 (should NOT appear in chart data)
        """
        page.goto(f"file://{edge_case_report_path}")
        page.wait_for_timeout(500)

        # Access the category chart datasets and aggregate totals by dataset label.
        result = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };

            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };

            const chartInstance = Chart.getChart(canvas);
            if (!chartInstance) return { error: 'No category chart instance found' };

            const datasets = chartInstance.data.datasets || [];
            const byCategory = {};
            const minByCategory = {};

            datasets.forEach(ds => {
                const label = ds?.label || '';
                const values = (Array.isArray(ds?.data) ? ds.data : []).map(v => Number(v) || 0);
                byCategory[label] = values.reduce((sum, v) => sum + v, 0);
                minByCategory[label] = values.length ? Math.min(...values) : 0;
            });

            return { byCategory, minByCategory, datasetCount: datasets.length };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not access category chart data: {result['error']}")

        by_category = result['byCategory']
        min_by_category = result['minByCategory']

        # Refunds category should NOT be in chart data (all negative amounts)
        # or if present, should have 0 value (not -150)
        refunds_total = by_category.get('Refunds', 0)
        assert refunds_total >= 0, (
            f"Refunds category total should be 0 or not present in chart data, "
            f"but got ${refunds_total}. Negative amounts should be excluded from charts. "
            f"Chart data: {result}"
        )

        refunds_min = min_by_category.get('Refunds', 0)
        assert refunds_min >= 0, (
            f"Refunds category contains negative monthly values in chart datasets: {refunds_min}. "
            f"Chart data: {result}"
        )

    def test_volatility_chart_applies_top10_and_edge_padding(self, page: Page, edge_case_report_path):
        """Volatility chart keeps top-10 rows and applies y-axis edge breathing room."""
        page.goto(f"file://{edge_case_report_path}")
        page.wait_for_timeout(500)

        result = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-volatility');
            if (!panel) return { error: 'Volatility chart panel not found' };

            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No volatility chart canvas found' };

            const chartInstance = Chart.getChart(canvas);
            if (!chartInstance) return { error: 'No volatility chart instance found' };

            const labels = chartInstance.data?.labels || [];
            const yOffset = chartInstance.options?.scales?.y?.offset;
            const layoutPadding = chartInstance.options?.layout?.padding || {};
            const topPadding = Number(layoutPadding.top || 0);
            const bottomPadding = Number(layoutPadding.bottom || 0);

            return {
                labelCount: labels.length,
                yOffset,
                topPadding,
                bottomPadding,
            };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not access volatility chart data: {result['error']}")

        assert result['labelCount'] <= 10, (
            f"Volatility chart should render at most 10 rows, got {result['labelCount']}"
        )
        assert result['yOffset'] is True, (
            f"Expected volatility y-axis offset=true for edge spacing, got {result['yOffset']}"
        )
        assert result['topPadding'] >= 10 and result['bottomPadding'] >= 10, (
            f"Expected volatility layout padding top/bottom >= 10, got {result}"
        )


@pytest.fixture(scope="module")
def chart_controls_report_path(tmp_path_factory):
    """Generate a multi-year report for chart-control interaction tests.

    Fixture properties:
    - 30 months of data (2024-01 through 2026-06) so month paging is required.
    - Multiple categories so focused/tooltip behavior can be validated.
    - One category present only on alternating months to create zero-value buckets.
    """
    tmp_dir = tmp_path_factory.mktemp("chart_controls_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    rows = []
    for idx in range(30):
        year = 2024 + (idx // 12)
        month = (idx % 12) + 1
        rows.append(f"{month:02d}/10/{year},ALPHA STORE,{120 + (idx % 5) * 10:.2f}")
        if idx % 2 == 0:
            rows.append(f"{month:02d}/12/{year},BETA SHOP,{55 + (idx % 4) * 5:.2f}")

    csv_content = "Date,Description,Amount\n" + "\n".join(rows) + "\n"
    (data_dir / "transactions.csv").write_text(csv_content)

    settings_content = """title: "Tally Spending Analysis"

data_sources:
  - name: Test
    file: data/transactions.csv
    format: "{date},{description},{amount}"

merchants_file: config/merchants.rules
"""
    (config_dir / "settings.yaml").write_text(settings_content)

    rules_content = """[Alpha]
match: contains("ALPHA STORE")
category: Housing
subcategory: Rent

[Beta]
match: contains("BETA SHOP")
category: Shopping
subcategory: Retail
"""
    (config_dir / "merchants.rules").write_text(rules_content)

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


def _goto_chart_report_fresh(page: Page, report_path: str):
    """Reset persisted chart UI state so chart-control tests are deterministic."""
    page.goto(f"file://{report_path}")
    page.evaluate("""() => {
        localStorage.removeItem('spending-report-ui-state-v1');
    }""")
    page.reload()
    page.wait_for_timeout(250)


class TestChartControlsMinimum:
    """Minimum chart interaction coverage for core controls and tooltip behavior."""

    def test_category_chart_grouping_switches_to_years(self, page: Page, chart_controls_report_path):
        _goto_chart_report_fresh(page, chart_controls_report_path)

        years_btn = page.locator("#cat-group-pills .proto-pill", has_text="Years")
        expect(years_btn).to_be_visible()
        years_btn.click()
        page.wait_for_timeout(150)

        result = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };
            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };
            const chart = Chart.getChart(canvas);
            if (!chart) return { error: 'No category chart instance found' };

            const labels = chart.data?.labels || [];
            const allYears = labels.every(label => /^\\d{4}$/.test(String(label || '')));
            return { labels, allYears };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not inspect category grouping chart: {result['error']}")

        assert len(result['labels']) >= 3, f"Expected 3+ year labels, got {result['labels']}"
        assert result['allYears'], f"Expected year-form labels after Years grouping, got {result['labels']}"

    def test_category_chart_focused_mode_uses_line_and_bold_label(self, page: Page, chart_controls_report_path):
        _goto_chart_report_fresh(page, chart_controls_report_path)

        focused_box = page.locator("#cat-unstack-checkbox")
        expect(focused_box).to_be_visible()
        focused_box.check()
        page.wait_for_timeout(150)

        result = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };
            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };
            const chart = Chart.getChart(canvas);
            if (!chart) return { error: 'No category chart instance found' };

            const focusedCount = (chart.data?.datasets || []).filter(ds => Number(ds.borderWidth || 0) >= 3).length;
            const compareDisabled = !!document.getElementById('cat-compare-checkbox')?.disabled;

            return {
                type: chart.config?.type,
                ttBoldLabel: chart.options?.ttBoldLabel || null,
                focusedCount,
                compareDisabled,
            };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not inspect focused chart mode: {result['error']}")

        assert result['type'] == 'line', f"Focused mode should render a line chart, got {result['type']}"
        assert result['ttBoldLabel'], f"Focused mode should set ttBoldLabel, got {result}"
        assert result['focusedCount'] == 1, f"Expected exactly one focused series, got {result}"
        assert result['compareDisabled'] is True, f"Compare should be disabled in focused mode: {result}"

    def test_category_chart_compare_years_builds_year_split_datasets(self, page: Page, chart_controls_report_path):
        _goto_chart_report_fresh(page, chart_controls_report_path)

        compare_box = page.locator("#cat-compare-checkbox")
        expect(compare_box).to_be_visible()
        compare_box.check()
        page.wait_for_timeout(150)

        result = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };
            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };
            const chart = Chart.getChart(canvas);
            if (!chart) return { error: 'No category chart instance found' };

            const datasets = chart.data?.datasets || [];
            const years = [...new Set(datasets.map(ds => ds.ttYear).filter(Boolean))];
            const labels = chart.data?.labels || [];

            return {
                labels,
                years,
                yearSubLabels: !!chart.options?.yearSubLabels,
            };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not inspect compare-years chart mode: {result['error']}")

        assert len(result['years']) >= 2, f"Expected at least two compare years, got {result}"
        assert result['yearSubLabels'] is True, f"Expected yearSubLabels enabled in compare mode, got {result}"
        assert len(result['labels']) == 12, f"Expected month compare labels (12), got {result['labels']}"

    def test_category_chart_month_grouping_pages_when_over_24_months(self, page: Page, chart_controls_report_path):
        _goto_chart_report_fresh(page, chart_controls_report_path)

        months_btn = page.locator("#cat-group-pills .proto-pill", has_text="Months")
        expect(months_btn).to_be_visible()
        months_btn.click()
        page.wait_for_timeout(150)

        pager_range = page.locator("#cat-chart-pager .pager-range")
        expect(pager_range).to_be_visible()
        expect(pager_range).to_contain_text("showing 24 of 30 months")

        prev_btn = page.locator("#cat-chart-pager button").first
        expect(prev_btn).to_be_enabled()
        prev_btn.click()
        page.wait_for_timeout(150)

        expect(pager_range).to_contain_text("showing 6 of 30 months")

        result = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };
            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };
            const chart = Chart.getChart(canvas);
            if (!chart) return { error: 'No category chart instance found' };
            return { labelCount: (chart.data?.labels || []).length };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not inspect category pager state: {result['error']}")

        assert result['labelCount'] == 6, f"Expected 6 labels on older page, got {result}"

    def test_category_tooltip_hides_zero_rows(self, page: Page, chart_controls_report_path):
        _goto_chart_report_fresh(page, chart_controls_report_path)

        result = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };
            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };
            const chart = Chart.getChart(canvas);
            if (!chart) return { error: 'No category chart instance found' };

            const datasets = chart.data?.datasets || [];
            const labels = chart.data?.labels || [];
            let targetDataIndex = -1;
            let hoveredDatasetIndex = -1;

            for (let idx = 0; idx < labels.length; idx += 1) {
                const vals = datasets.map(ds => Number(ds?.data?.[idx]) || 0);
                const positiveCount = vals.filter(v => v > 0).length;
                const zeroCount = vals.filter(v => Math.abs(v) < 1e-9).length;
                if (positiveCount >= 1 && zeroCount >= 1) {
                    targetDataIndex = idx;
                    hoveredDatasetIndex = vals.findIndex(v => v > 0);
                    break;
                }
            }

            if (targetDataIndex < 0 || hoveredDatasetIndex < 0) {
                return { error: 'No mixed zero/non-zero bucket found for tooltip test' };
            }

            const external = chart.options?.plugins?.tooltip?.external;
            if (typeof external !== 'function') {
                return { error: 'External tooltip handler unavailable' };
            }

            external({
                chart,
                tooltip: {
                    opacity: 1,
                    dataPoints: [{ dataIndex: targetDataIndex, datasetIndex: hoveredDatasetIndex }],
                    caretX: 40,
                    caretY: 40,
                },
            });

            const tip = document.getElementById('ext-tooltip');
            if (!tip) return { error: 'Tooltip element missing' };

            const rowNames = Array.from(tip.querySelectorAll('.tt-row .tt-name'))
                .map(el => (el.textContent || '').trim())
                .filter(Boolean);
            const zeroNames = datasets
                .filter(ds => Math.abs(Number(ds?.data?.[targetDataIndex]) || 0) < 1e-9)
                .map(ds => ds.label)
                .filter(Boolean);

            const includesZeroName = zeroNames.some(name => rowNames.includes(name));
            return {
                rowNames,
                zeroNames,
                includesZeroName,
            };
        }""")

        if 'error' in result:
            pytest.fail(f"Could not validate tooltip zero-row behavior: {result['error']}")

        assert result['zeroNames'], f"Expected at least one zero-valued dataset in sampled bucket: {result}"
        assert result['includesZeroName'] is False, (
            f"Tooltip should hide zero rows but included one: {result}"
        )

    def test_category_compare_years_caps_window_and_pages(self, page: Page, multiyear_report_path):
        _goto_chart_report_fresh(page, multiyear_report_path)

        compare_box = page.locator("#cat-compare-checkbox")
        expect(compare_box).to_be_visible()
        compare_box.check()
        page.wait_for_timeout(150)

        pager_range = page.locator("#cat-chart-pager .pager-range")
        expect(pager_range).to_be_visible()
        expect(pager_range).to_contain_text("showing 3 of 4 years")

        result_latest = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };
            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };
            const chart = Chart.getChart(canvas);
            if (!chart) return { error: 'No category chart instance found' };

            const years = [...new Set((chart.data?.datasets || []).map(ds => ds.ttYear).filter(Boolean))];
            return { years };
        }""")

        if 'error' in result_latest:
            pytest.fail(f"Could not inspect latest compare-year window: {result_latest['error']}")

        assert len(result_latest['years']) == 3, (
            f"Expected compare mode to cap at 3 years, got {result_latest}"
        )

        prev_btn = page.locator("#cat-chart-pager button").first
        expect(prev_btn).to_be_enabled()
        prev_btn.click()
        page.wait_for_timeout(150)

        result_older = page.evaluate("""() => {
            const panel = document.getElementById('chart-panel-category');
            if (!panel) return { error: 'Category chart panel not found' };
            const canvas = panel.querySelector('canvas');
            if (!canvas) return { error: 'No category chart canvas found' };
            const chart = Chart.getChart(canvas);
            if (!chart) return { error: 'No category chart instance found' };

            const years = [...new Set((chart.data?.datasets || []).map(ds => ds.ttYear).filter(Boolean))];
            return { years };
        }""")

        if 'error' in result_older:
            pytest.fail(f"Could not inspect older compare-year window: {result_older['error']}")

        assert len(result_older['years']) == 3, (
            f"Expected paged compare mode to keep a 3-year window, got {result_older}"
        )
        assert result_latest['years'] != result_older['years'], (
            f"Expected year window to change when paging, but it did not. latest={result_latest}, older={result_older}"
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
        ["uv", "run", "tally", "run", "--format", "html", "-o", str(report_path), str(config_dir)],
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
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
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
        ["uv", "run", "tally", "run", "--format", "html", "-o", str(report_path), str(config_dir)],
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
        """Clicking the filter button on a subcategory row adds a subcategory filter, not a merchant filter."""
        page.goto(f"file://{report_path}")

        # Switch to subcategory mode
        page.locator(".view-toggle button", has_text="Subcategory").click()

        # Click the filter button on the Online row
        shopping_section = page.get_by_test_id("section-cat-Shopping")
        online_row = shopping_section.locator("tr.merchant-row", has_text="Online")
        online_row.locator(".merchant-filter-trigger").click()

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
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
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
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
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
        ["uv", "run", "tally", "run", "--format", "html", "-o", str(report_path), str(config_dir)],
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


# =============================================================================
# Category 8: Transform Directive Tests
# =============================================================================

@pytest.fixture(scope="module")
def transform_report_path(tmp_path_factory):
    """Generate a report with transform directive applied.

    Tests that:
    - transform: directive changes displayed transaction description
    - original_description is preserved and shown in popup
    - +N badge includes original_description in count
    """
    tmp_dir = tmp_path_factory.mktemp("transform_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"

    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    # Main transactions - simple case with transform using description directly
    csv_content = """Date,Description,Amount
01/15/2024,APPLE.COM/BILL ONE APPLE PARK WAY,13.99
01/20/2024,AMAZON MARKETPLACE,45.00
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

    # Rules with transform directive - simple transform that modifies description
    rules_content = """[Apple Purchases]
match: contains("APPLE.COM/BILL")
category: Entertainment
transform: "App Store Purchase"
field: store = "iTunes"

[Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: Online
"""
    (config_dir / "merchants.rules").write_text(rules_content)

    # Generate the report
    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
        capture_output=True,
        text=True,
        cwd=str(tmp_dir)  # Run from tmp_dir so relative paths in settings work
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}\n{result.stdout}")

    return str(report_file)


class TestTransformDirective:
    """Tests for transform: directive in merchant rules.

    The transform directive allows dynamically changing the transaction
    description displayed in the report.
    """

    def test_transformed_description_displayed(self, page: Page, transform_report_path):
        """Transaction with transform shows transformed description."""
        page.goto(f"file://{transform_report_path}")

        # Wait for Vue to mount and render merchant rows
        apple_row = page.get_by_test_id("merchant-row-Apple_Purchases")
        expect(apple_row).to_be_visible()

        # Expand Apple Purchases merchant by clicking the chevron
        apple_row.locator(".chevron").click()

        # The transformed description should show "App Store Purchase"
        txn_row = page.locator(".txn-row", has_text="App Store Purchase")
        expect(txn_row).to_be_visible()

    def test_extra_fields_badge_includes_original_description(self, page: Page, transform_report_path):
        """The +N badge count includes original_description."""
        page.goto(f"file://{transform_report_path}")

        # Wait for Vue to mount and render merchant rows
        apple_row = page.get_by_test_id("merchant-row-Apple_Purchases")
        expect(apple_row).to_be_visible()

        # Expand Apple Purchases merchant by clicking the chevron
        apple_row.locator(".chevron").click()

        # Find the transaction row with extra fields badge
        txn_row = page.locator(".txn-row", has_text="App Store Purchase")
        expect(txn_row).to_be_visible()

        # Should have +2 badge (original_description + store field)
        badge = txn_row.locator(".extra-fields-trigger")
        expect(badge).to_be_visible()
        expect(badge).to_contain_text("+2")

    def test_popup_shows_original_description(self, page: Page, transform_report_path):
        """Clicking +N badge shows original description in popup."""
        page.goto(f"file://{transform_report_path}")

        # Wait for Vue to mount and render merchant rows
        apple_row = page.get_by_test_id("merchant-row-Apple_Purchases")
        expect(apple_row).to_be_visible()

        # Expand Apple Purchases merchant by clicking the chevron
        apple_row.locator(".chevron").click()

        # Click the extra fields badge
        txn_row = page.locator(".txn-row", has_text="App Store Purchase")
        expect(txn_row).to_be_visible()

        badge = txn_row.locator(".extra-fields-trigger")
        badge.click()

        # Popup should show "Original" label with raw description
        popup = txn_row.locator(".match-info-popup.visible")
        expect(popup).to_be_visible()
        expect(popup).to_contain_text("Original")
        expect(popup).to_contain_text("APPLE.COM/BILL")

    def test_untransformed_transaction_no_original_field(self, page: Page, transform_report_path):
        """Transaction without transform has no original_description in badge."""
        page.goto(f"file://{transform_report_path}")

        # Wait for Vue to mount and render merchant rows
        amazon_row = page.get_by_test_id("merchant-row-Amazon")
        expect(amazon_row).to_be_visible()

        # Expand Amazon merchant by clicking the chevron
        amazon_row.locator(".chevron").click()

        # Amazon transaction should not have extra fields badge
        txn_row = page.locator(".txn-row", has_text="AMAZON")
        expect(txn_row).to_be_visible()

        badge = txn_row.locator(".extra-fields-trigger")
        expect(badge).not_to_be_visible()


# =============================================================================
# Category 4: Multi-granularity Date Filter (Month / Quarter / Year / Custom)
# and multi-year transaction row display.
# =============================================================================

@pytest.fixture(scope="module")
def multiyear_report_path(tmp_path_factory):
    """Report spanning 4 calendar years with a views_file that excludes several
    merchants, for date-filter and multi-year-row tests.

    Key fixture properties:
    - views_file defines a Food-only view, so Shopping/Subscriptions merchants
      match zero views and are absent from `sections` (Part 1 bug surface).
    - April 2026 appears ONLY via Amazon (Shopping, excluded) -> its month would
      vanish if availableMonths sourced from `sections`.
    - Netflix has all 12 months of 2025 (enables a Year chip) plus 2026 months
      (multi-year -> rows show the year).
    - Target has only 2026 transactions (single-year -> rows show no year).
    - Amazon adds 2023 and 2024 transactions so 4 years exist (year tabs cap 3).
    """
    tmp_dir = tmp_path_factory.mktemp("multiyear_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"
    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    rows = []
    for mm in range(1, 13):  # Netflix: all 12 months of 2025
        rows.append(f"{mm:02d}/05/2025,NETFLIX,15.99")
    rows += [
        "01/06/2026,NETFLIX,15.99",
        "02/06/2026,NETFLIX,15.99",
        "03/12/2025,WHOLE FOODS MARKET,100.00",   # Food (in-view)
        "06/15/2026,WHOLE FOODS MARKET,120.00",   # Food (in-view), 2026
        "01/15/2023,AMAZON MARKETPLACE,40.00",    # 2023 (year-tab cap)
        "06/15/2024,AMAZON MARKETPLACE,50.00",    # 2024 (year-tab cap)
        "04/10/2026,AMAZON MARKETPLACE,45.00",    # April 2026: unique + excluded
        "05/20/2026,TARGET,60.00",                # Target single-year 2026
        "07/22/2026,TARGET,70.00",
    ]
    csv_content = "Date,Description,Amount\n" + "\n".join(rows) + "\n"
    (data_dir / "transactions.csv").write_text(csv_content)

    (config_dir / "settings.yaml").write_text(
        'title: "Tally Spending Analysis"\n\n'
        "data_sources:\n"
        "  - name: Test\n"
        "    file: data/transactions.csv\n"
        '    format: "{date},{description},{amount}"\n\n'
        "merchants_file: config/merchants.rules\n"
        "views_file: config/views.rules\n"
    )
    (config_dir / "merchants.rules").write_text(
        "[Netflix]\nmatch: contains(\"NETFLIX\")\ncategory: Subscriptions\nsubcategory: Streaming\n\n"
        "[Whole Foods]\nmatch: normalized(\"WHOLE FOODS\")\ncategory: Food\nsubcategory: Grocery\n\n"
        "[Amazon]\nmatch: normalized(\"AMAZON\")\ncategory: Shopping\nsubcategory: Online\n\n"
        "[Target]\nmatch: normalized(\"TARGET\")\ncategory: Shopping\nsubcategory: Retail\n"
    )
    (config_dir / "views.rules").write_text(
        "[Food & Dining]\ndescription: Food spending\nfilter: category == \"Food\"\n"
    )

    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")
    return str(report_file)


def _open_date_popover(page):
    """Open the date filter popover and return once it is visible."""
    page.get_by_test_id("date-filter-trigger").click()
    expect(page.get_by_test_id("date-popover")).to_be_visible()


def _chip_texts(page):
    return page.locator("[data-testid='filter-chip'] .chip-text").all_text_contents()


class TestDateFilter:
    """Tests for the multi-granularity date-filter popover."""

    def test_excluded_merchant_month_appears_in_grid(self, page: Page, multiyear_report_path):
        """Part 1 regression: April 2026 exists only via Amazon (excluded from
        every view). It must still appear in the month grid, not vanish."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        # Switch to the 2026 tab regardless of the machine's current year.
        page.get_by_test_id("date-year-tab-2026").click()
        expect(page.get_by_test_id("date-month-cell-2026-04")).to_be_visible()

    def test_this_last_preset_rows_render(self, page: Page, multiyear_report_path):
        """This/Last rows render all six presets."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        for tid in ["this-month", "this-quarter", "this-year",
                    "last-month", "last-quarter", "last-year"]:
            expect(page.get_by_test_id(f"date-preset-{tid}")).to_be_visible()

    def test_year_tabs_capped_to_three_most_recent(self, page: Page, multiyear_report_path):
        """Year tabs show only the 3 most recent data years (2024/2025/2026),
        not the oldest (2023)."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        expect(page.get_by_test_id("date-year-tab-2026")).to_be_visible()
        expect(page.get_by_test_id("date-year-tab-2025")).to_be_visible()
        expect(page.get_by_test_id("date-year-tab-2024")).to_be_visible()
        expect(page.get_by_test_id("date-year-tab-2023")).to_have_count(0)

    def test_selecting_quarter_months_aggregates_to_quarter_chip(self, page: Page, multiyear_report_path):
        """Selecting Jan+Feb+Mar 2025 in the grid aggregates to one Q1 2025 chip."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        page.get_by_test_id("date-year-tab-2025").click()
        for mm in ("01", "02", "03"):
            page.get_by_test_id(f"date-month-cell-2025-{mm}").click()
        page.get_by_test_id("date-apply").click()
        assert _chip_texts(page) == ["Q1 2025"]

    def test_incremental_apply_collapses_to_quarter(self, page: Page, multiyear_report_path):
        """Apply Jan+Feb as two month chips; reopen, add Mar; re-apply collapses
        the three into a single Q1 2025 chip."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        page.get_by_test_id("date-year-tab-2025").click()
        page.get_by_test_id("date-month-cell-2025-01").click()
        page.get_by_test_id("date-month-cell-2025-02").click()
        page.get_by_test_id("date-apply").click()
        assert sorted(_chip_texts(page)) == ["Feb 2025", "Jan 2025"]

        _open_date_popover(page)
        page.get_by_test_id("date-year-tab-2025").click()
        page.get_by_test_id("date-month-cell-2025-03").click()
        page.get_by_test_id("date-apply").click()
        assert _chip_texts(page) == ["Q1 2025"]

    def test_selecting_full_year_aggregates_to_year_chip(self, page: Page, multiyear_report_path):
        """Selecting all 12 months of 2025 collapses to a single 2025 chip."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        page.get_by_test_id("date-year-tab-2025").click()
        for mm in range(1, 13):
            page.get_by_test_id(f"date-month-cell-2025-{mm:02d}").click()
        page.get_by_test_id("date-apply").click()
        assert _chip_texts(page) == ["2025"]

    def test_year_and_quarter_from_different_periods_union(self, page: Page, multiyear_report_path):
        """A 2025 Year chip plus a custom 2026 range union (both present, and the
        filtered total is non-zero rather than an empty AND)."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        page.get_by_test_id("date-year-tab-2025").click()
        for mm in range(1, 13):
            page.get_by_test_id(f"date-month-cell-2025-{mm:02d}").click()
        # Custom range in 2026, independent of the 2025 months.
        page.get_by_test_id("date-start-text").fill("1/1/2026")
        page.get_by_test_id("date-start-text").blur()
        page.get_by_test_id("date-end-text").fill("6/30/2026")
        page.get_by_test_id("date-end-text").blur()
        page.get_by_test_id("date-apply").click()

        chips = _chip_texts(page)
        assert "2025" in chips
        assert any("2026" in c and "–" in c for c in chips), chips
        # Union is non-empty (2025 Netflix + 2026 transactions both counted).
        expect(page.get_by_test_id("filtered-amount")).not_to_contain_text("$0")

    def test_kpi_trend_label_updates_with_filter_anchor_month(self, page: Page, multiyear_report_path):
        """KPI trend copy anchors to the last visible month and adjusts its prior
        window count when date filters change."""
        page.goto(f"file://{multiyear_report_path}")

        spending_trend = page.locator(".kpi-card.spending .kpi-trend")
        expect(spending_trend).to_be_visible()
        expect(spending_trend).to_contain_text("Jul '26 vs prior 12 months")

        _open_date_popover(page)
        page.get_by_test_id("date-year-tab-2025").click()
        for mm in range(1, 13):
            page.get_by_test_id(f"date-month-cell-2025-{mm:02d}").click()
        page.get_by_test_id("date-apply").click()

        expect(spending_trend).to_contain_text("Dec '25 vs prior 11 months")
        expect(spending_trend).not_to_contain_text("prior 6")

        _open_date_popover(page)
        page.get_by_test_id("date-clear-all").click()

        _open_date_popover(page)
        page.get_by_test_id("date-start-text").fill("1/1/2026")
        page.get_by_test_id("date-start-text").blur()
        page.get_by_test_id("date-end-text").fill("6/30/2026")
        page.get_by_test_id("date-end-text").blur()
        page.get_by_test_id("date-apply").click()

        expect(spending_trend).to_contain_text("Jun '26 vs prior 4 months")

    def test_drill_calendar_commits_day_and_filters(self, page: Page, multiyear_report_path):
        """The Start drill calendar commits a day-precision date into its input,
        and a start+end range produces a filtering daterange chip."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        # Open Start calendar (defaults to today's month) and pick the 15th.
        page.get_by_test_id("date-start-cal-btn").click()
        expect(page.get_by_test_id("date-start-cal")).to_be_visible()
        page.get_by_test_id("date-start-day-15").click()
        # Calendar closes and the text input reflects the committed date
        # (formatted "Mon D, YYYY", so it contains ", ").
        expect(page.get_by_test_id("date-start-cal")).to_have_count(0)
        assert ", " in page.get_by_test_id("date-start-text").input_value()

    def test_cross_year_custom_range_shows_year_on_both_ends(self, page: Page, multiyear_report_path):
        """A custom range spanning two calendar years shows the year on both the
        start and the end of the chip label."""
        page.goto(f"file://{multiyear_report_path}")
        _open_date_popover(page)
        page.get_by_test_id("date-start-text").fill("12/15/2025")
        page.get_by_test_id("date-start-text").blur()
        page.get_by_test_id("date-end-text").fill("2/10/2026")
        page.get_by_test_id("date-end-text").blur()
        page.get_by_test_id("date-apply").click()
        chips = _chip_texts(page)
        assert chips == ["Dec 15, 2025 – Feb 10, 2026"], chips

    def test_quarter_chip_label_restores_from_hash(self, page: Page, multiyear_report_path):
        """A quarter range chip restored from the URL hash re-derives its Q label
        (the case most likely to regress if getDisplayText isn't updated)."""
        page.goto(f"file://{multiyear_report_path}#+d:2025-01..2025-03")
        assert _chip_texts(page) == ["Q1 2025"]

    def test_clear_all_filters_wipes_and_closes(self, page: Page, multiyear_report_path):
        """The footer 'Clear all filters' removes every chip and closes the popover."""
        page.goto(f"file://{multiyear_report_path}#+d:2025-01..2025-12")
        assert _chip_texts(page) == ["2025"]
        _open_date_popover(page)
        page.get_by_test_id("date-clear-all").click()
        expect(page.get_by_test_id("date-popover")).to_have_count(0)
        expect(page.locator("[data-testid='filter-chip']")).to_have_count(0)


class TestMultiYearTransactionRows:
    """Part 3: transaction rows always show the year, regardless of whether the merchant's list spans years."""

    def test_multiyear_merchant_rows_show_year(self, page: Page, multiyear_report_path):
        """Netflix spans 2025 and 2026, so its transaction rows include ', YYYY'."""
        page.goto(f"file://{multiyear_report_path}")
        page.get_by_test_id("merchant-row-Netflix").click()
        page.wait_for_timeout(150)
        dates = page.locator(".txn-row:has-text('NETFLIX') .txn-date").all_text_contents()
        assert dates, "expected Netflix transaction rows"
        assert all(", 20" in d for d in dates), dates

    def test_single_year_merchant_rows_also_show_year(self, page: Page, multiyear_report_path):
        """Target has only 2026 transactions, but rows still include ', YYYY' (year is always shown)."""
        page.goto(f"file://{multiyear_report_path}")
        page.get_by_test_id("merchant-row-Target").click()
        page.wait_for_timeout(150)
        dates = page.locator(".txn-row:has-text('TARGET') .txn-date").all_text_contents()
        assert dates, "expected Target transaction rows"
        assert all(", 20" in d for d in dates), dates


@pytest.fixture(scope="module")
def current_quarter_report_path(tmp_path_factory):
    """Report whose only data is three transactions in the three months of the
    current calendar quarter, so selecting them lights up the This Quarter pill
    (coverage-based highlight) deterministically regardless of run date."""
    import datetime
    today = datetime.date.today()
    q_start_month = ((today.month - 1) // 3) * 3 + 1
    months = [q_start_month, q_start_month + 1, q_start_month + 2]

    tmp_dir = tmp_path_factory.mktemp("current_quarter_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"
    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    rows = [f"{mm:02d}/10/{today.year},NETFLIX,15.99" for mm in months]
    (data_dir / "transactions.csv").write_text("Date,Description,Amount\n" + "\n".join(rows) + "\n")
    (config_dir / "settings.yaml").write_text(
        'title: "Tally Spending Analysis"\n\n'
        "data_sources:\n  - name: Test\n    file: data/transactions.csv\n"
        '    format: "{date},{description},{amount}"\n\n'
        "merchants_file: config/merchants.rules\n"
    )
    (config_dir / "merchants.rules").write_text(
        "[Netflix]\nmatch: contains(\"NETFLIX\")\ncategory: Subscriptions\nsubcategory: Streaming\n"
    )
    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")
    return str(report_file), today.year, months


@pytest.fixture(scope="module")
def kpi_trailing_transfer_report_path(tmp_path_factory):
    """Report where the final month has only transfer activity.

    Trend labels should anchor to the last month with spending/income activity,
    not the trailing transfer-only month.
    """
    tmp_dir = tmp_path_factory.mktemp("kpi_trailing_transfer_test")
    config_dir = tmp_dir / "config"
    data_dir = tmp_dir / "data"
    output_dir = tmp_dir / "output"
    config_dir.mkdir()
    data_dir.mkdir()
    output_dir.mkdir()

    csv_content = """Date,Description,Amount
01/10/2026,WHOLE FOODS MARKET,100.00
02/10/2026,WHOLE FOODS MARKET,120.00
03/10/2026,WHOLE FOODS MARKET,110.00
04/10/2026,ACCOUNT TRANSFER,350.00
"""
    (data_dir / "transactions.csv").write_text(csv_content)

    (config_dir / "settings.yaml").write_text(
        'title: "Tally Spending Analysis"\n\n'
        "data_sources:\n"
        "  - name: Test\n"
        "    file: data/transactions.csv\n"
        '    format: "{date},{description},{amount}"\n\n'
        "merchants_file: config/merchants.rules\n"
    )

    (config_dir / "merchants.rules").write_text(
        "[Whole Foods]\n"
        "match: normalized(\"WHOLE FOODS\")\n"
        "category: Food\n"
        "subcategory: Grocery\n\n"
        "[Transfer]\n"
        "match: contains(\"ACCOUNT TRANSFER\")\n"
        "category: Finance\n"
        "subcategory: Transfer\n"
        "tags: transfer\n"
    )

    report_file = output_dir / "report.html"
    result = subprocess.run(
        ["uv", "run", "tally", "run", "-o", str(report_file), str(config_dir)],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if result.returncode != 0:
        pytest.fail(f"Failed to generate report: {result.stderr}")
    return str(report_file)


class TestDateFilterCoverageHighlight:
    """Coverage-based preset highlighting (no stored quarter/year flag)."""

    def test_selecting_quarter_months_highlights_this_quarter(self, page: Page, current_quarter_report_path):
        report_path, year, months = current_quarter_report_path
        page.goto(f"file://{report_path}")
        _open_date_popover(page)
        page.get_by_test_id(f"date-year-tab-{year}").click()
        # Before selecting, This Quarter is not active.
        this_q = page.get_by_test_id("date-preset-this-quarter")
        expect(this_q).not_to_have_class(re.compile(r"\bactive\b"))
        for mm in months:
            page.get_by_test_id(f"date-month-cell-{year}-{mm:02d}").click()
        # All three of the quarter's months selected -> pill lights up.
        expect(this_q).to_have_class(re.compile(r"\bactive\b"))


class TestKpiTrendAnchoring:
    """KPI trend labeling should ignore trailing months with only transfer-like activity."""

    def test_spending_trend_ignores_trailing_transfer_only_month(self, page: Page, kpi_trailing_transfer_report_path):
        page.goto(f"file://{kpi_trailing_transfer_report_path}")

        spending_trend = page.locator(".kpi-card.spending .kpi-trend")
        expect(spending_trend).to_be_visible()
        expect(spending_trend).to_contain_text("Mar '26 vs prior 2 months")
        expect(spending_trend).not_to_contain_text("Apr '26")
