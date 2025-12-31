"""Tests for analyzer module - CSV parsing and amount handling."""

import pytest
import tempfile
import os

from tally.analyzer import parse_amount, parse_generic_csv
from tally.format_parser import parse_format_string
from tally.merchant_utils import get_all_rules


class TestParseAmount:
    """Tests for parse_amount function with different locales."""

    def test_us_format_simple(self):
        """Parse simple US format amounts."""
        assert parse_amount('123.45') == 123.45
        assert parse_amount('0.99') == 0.99
        assert parse_amount('100') == 100.0

    def test_us_format_with_thousands(self):
        """Parse US format with thousands separator."""
        assert parse_amount('1,234.56') == 1234.56
        assert parse_amount('12,345.67') == 12345.67
        assert parse_amount('1,234,567.89') == 1234567.89

    def test_us_format_with_currency(self):
        """Parse US format with currency symbols."""
        assert parse_amount('$123.45') == 123.45
        assert parse_amount('$1,234.56') == 1234.56
        assert parse_amount('€100.00') == 100.0
        assert parse_amount('£50.00') == 50.0
        assert parse_amount('¥1000') == 1000.0

    def test_us_format_parenthetical_negative(self):
        """Parse parenthetical negatives (accounting format)."""
        assert parse_amount('(123.45)') == -123.45
        assert parse_amount('(1,234.56)') == -1234.56
        assert parse_amount('($50.00)') == -50.0

    def test_us_format_with_whitespace(self):
        """Parse amounts with leading/trailing whitespace."""
        assert parse_amount('  123.45  ') == 123.45
        assert parse_amount('\t$100.00\n') == 100.0

    def test_european_format_simple(self):
        """Parse simple European format amounts."""
        assert parse_amount('123,45', decimal_separator=',') == 123.45
        assert parse_amount('0,99', decimal_separator=',') == 0.99
        assert parse_amount('100', decimal_separator=',') == 100.0

    def test_european_format_with_thousands(self):
        """Parse European format with period as thousands separator."""
        assert parse_amount('1.234,56', decimal_separator=',') == 1234.56
        assert parse_amount('12.345,67', decimal_separator=',') == 12345.67
        assert parse_amount('1.234.567,89', decimal_separator=',') == 1234567.89

    def test_european_format_with_space_thousands(self):
        """Parse European format with space as thousands separator."""
        assert parse_amount('1 234,56', decimal_separator=',') == 1234.56
        assert parse_amount('12 345,67', decimal_separator=',') == 12345.67

    def test_european_format_with_currency(self):
        """Parse European format with currency symbols."""
        assert parse_amount('€1.234,56', decimal_separator=',') == 1234.56
        assert parse_amount('€123,45', decimal_separator=',') == 123.45
        assert parse_amount('$100,00', decimal_separator=',') == 100.0

    def test_european_format_parenthetical_negative(self):
        """Parse European parenthetical negatives."""
        assert parse_amount('(123,45)', decimal_separator=',') == -123.45
        assert parse_amount('(1.234,56)', decimal_separator=',') == -1234.56


class TestParseGenericCsvDecimalSeparator:
    """Tests for parse_generic_csv with decimal_separator option."""

    def test_us_format_csv(self):
        """Parse CSV with US number format (default)."""
        csv_content = """Date,Description,Amount
01/15/2025,GROCERY STORE,123.45
01/16/2025,COFFEE SHOP,5.99
01/17/2025,BIG PURCHASE,"1,234.56"
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            txns = parse_generic_csv(
                f.name,
                format_spec,
                rules
            )

            assert len(txns) == 3
            assert txns[0]['amount'] == 123.45
            assert txns[1]['amount'] == 5.99
            assert txns[2]['amount'] == 1234.56
        finally:
            os.unlink(f.name)

    def test_european_format_csv(self):
        """Parse CSV with European number format."""
        # Note: CSV is still comma-delimited, but amounts use European format
        csv_content = """Date,Description,Amount
15.01.2025,GROCERY STORE,"123,45"
16.01.2025,COFFEE SHOP,"5,99"
17.01.2025,BIG PURCHASE,"1.234,56"
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%d.%m.%Y},{description},{amount}')
            txns = parse_generic_csv(
                f.name,
                format_spec,
                rules,
                decimal_separator=','
            )

            assert len(txns) == 3
            assert txns[0]['amount'] == 123.45
            assert txns[1]['amount'] == 5.99
            assert txns[2]['amount'] == 1234.56
        finally:
            os.unlink(f.name)

    def test_european_format_with_negative(self):
        """Parse European CSV with negative amounts (credits/refunds)."""
        csv_content = """Date,Description,Amount
15.01.2025,REFUND,"-500,00"
16.01.2025,PURCHASE,"250,00"
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%d.%m.%Y},{description},{amount}')
            txns = parse_generic_csv(
                f.name,
                format_spec,
                rules,
                decimal_separator=','
            )

            assert len(txns) == 2
            # Negative amounts are preserved (credits/refunds)
            assert txns[0]['amount'] == -500.0
            assert txns[0]['is_credit'] == True
            # Positive amounts are expenses
            assert txns[1]['amount'] == 250.0
            assert txns[1]['is_credit'] == False
        finally:
            os.unlink(f.name)

    def test_mixed_sources_different_separators(self):
        """Simulate mixed sources with different decimal separators."""
        # US format CSV
        us_csv = """Date,Description,Amount
01/15/2025,US STORE,100.50
"""
        # European format CSV (amounts quoted to handle comma)
        eu_csv = """Date,Description,Amount
15.01.2025,EU STORE,"100,50"
"""
        us_f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        eu_f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            us_f.write(us_csv)
            us_f.close()
            eu_f.write(eu_csv)
            eu_f.close()

            rules = get_all_rules()

            # Parse US format
            us_format = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            us_txns = parse_generic_csv(
                us_f.name,
                us_format,
                rules,
                decimal_separator='.'
            )

            # Parse European format
            eu_format = parse_format_string('{date:%d.%m.%Y},{description},{amount}')
            eu_txns = parse_generic_csv(
                eu_f.name,
                eu_format,
                rules,
                decimal_separator=','
            )

            # Both should parse to same value
            assert us_txns[0]['amount'] == 100.50
            assert eu_txns[0]['amount'] == 100.50
        finally:
            os.unlink(us_f.name)
            os.unlink(eu_f.name)


class TestDateFormatWithSpaces:
    """Tests for date formats that include spaces (e.g., '%d %b %y' for '30 Dec 25')."""

    def test_australian_date_format_with_spaces(self):
        """Parse CSV with Australian date format containing spaces (issue #42)."""
        csv_content = """Date,Amount,Description
30 Dec 25,-66.08,ALDI STORES HORNSBY
31 Dec 25,-25.50,WOOLWORTHS SYDNEY
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Format with spaces in date: %d %b %y (e.g., "30 Dec 25")
            format_spec = parse_format_string('{date:%d %b %y},{amount},{description}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            # Verify dates are parsed correctly
            assert txns[0]['date'].day == 30
            assert txns[0]['date'].month == 12
            assert txns[0]['date'].year == 2025
            assert txns[1]['date'].day == 31
            assert txns[1]['date'].month == 12
            # Verify amounts are parsed
            assert txns[0]['amount'] == -66.08
            assert txns[1]['amount'] == -25.50
        finally:
            os.unlink(f.name)

    def test_date_format_without_spaces_still_strips_suffix(self):
        """Date format without spaces should still strip day suffix (e.g., '01/15/2025 Mon')."""
        csv_content = """Date,Description,Amount
01/15/2025  Mon,GROCERY STORE,123.45
01/16/2025  Tue,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Format WITHOUT spaces - should strip trailing day suffix
            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            # Verify dates are parsed correctly (day suffix stripped)
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].day == 15
            assert txns[0]['date'].year == 2025
        finally:
            os.unlink(f.name)

    def test_full_month_name_date_format(self):
        """Parse date format with full month name and spaces."""
        csv_content = """Date,Description,Amount
15 January 2025,GROCERY STORE,50.00
16 February 2025,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Full month name format: %d %B %Y (e.g., "15 January 2025")
            format_spec = parse_format_string('{date:%d %B %Y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].day == 15
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].year == 2025
            assert txns[1]['date'].day == 16
            assert txns[1]['date'].month == 2
        finally:
            os.unlink(f.name)


class TestCustomCaptures:
    """Tests for custom column captures with description templates."""

    def test_two_column_capture(self):
        """Capture two columns and combine with template."""
        csv_content = """Date,Type,Merchant,Amount
01/15/2025,Card payment,STARBUCKS COFFEE,25.50
01/16/2025,Transfer,JOHN SMITH,500.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{type},{merchant},{amount}',
                description_template='{merchant} ({type})'
            )
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            # Check raw_description contains combined value
            assert txns[0]['raw_description'] == 'STARBUCKS COFFEE (Card payment)'
            assert txns[1]['raw_description'] == 'JOHN SMITH (Transfer)'
        finally:
            os.unlink(f.name)

    def test_template_ordering(self):
        """Template can reorder captured columns."""
        csv_content = """Date,First,Second,Amount
01/15/2025,AAA,BBB,10.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Capture columns as 'first' and 'second', but template puts second first
            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{first},{second},{amount}',
                description_template='{second} - {first}'
            )
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert txns[0]['raw_description'] == 'BBB - AAA'
        finally:
            os.unlink(f.name)

    def test_mixed_mode_error(self):
        """Cannot mix {description} with custom captures."""
        with pytest.raises(ValueError) as exc_info:
            parse_format_string('{date},{description},{merchant},{amount}')

        assert 'Cannot mix {description}' in str(exc_info.value)

    def test_custom_captures_require_template(self):
        """Custom captures without template raises error."""
        with pytest.raises(ValueError) as exc_info:
            parse_format_string('{date},{type},{merchant},{amount}')

        assert 'require a description template' in str(exc_info.value)

    def test_template_references_missing_capture(self):
        """Template referencing non-captured field raises error."""
        with pytest.raises(ValueError) as exc_info:
            parse_format_string(
                '{date},{type},{merchant},{amount}',
                description_template='{vendor}'  # 'vendor' not captured
            )

        assert "'{vendor}'" in str(exc_info.value)
        assert 'not captured' in str(exc_info.value)

    def test_simple_description_still_works(self):
        """Mode 1 with {description} continues to work."""
        csv_content = """Date,Description,Amount
01/15/2025,STARBUCKS COFFEE,25.50
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert txns[0]['raw_description'] == 'STARBUCKS COFFEE'
        finally:
            os.unlink(f.name)


class TestCurrencyFormatting:
    """Tests for currency formatting functions."""

    def test_format_currency_default(self):
        """Test default USD formatting."""
        from tally.analyzer import format_currency
        assert format_currency(1234) == "$1,234"
        assert format_currency(0) == "$0"
        assert format_currency(1000000) == "$1,000,000"

    def test_format_currency_prefix(self):
        """Test prefix currency formats (Euro, Pound)."""
        from tally.analyzer import format_currency
        assert format_currency(1234, "€{amount}") == "€1,234"
        assert format_currency(1234, "£{amount}") == "£1,234"

    def test_format_currency_suffix(self):
        """Test suffix currency formats (Polish Złoty)."""
        from tally.analyzer import format_currency
        assert format_currency(1234, "{amount} zł") == "1,234 zł"
        assert format_currency(1234, "{amount} kr") == "1,234 kr"

    def test_format_currency_decimal(self):
        """Test currency formatting with decimals."""
        from tally.analyzer import format_currency_decimal
        assert format_currency_decimal(1234.56) == "$1,234.56"
        assert format_currency_decimal(1234.56, "€{amount}") == "€1,234.56"
        assert format_currency_decimal(1234.56, "{amount} zł") == "1,234.56 zł"

    def test_format_currency_negative(self):
        """Test negative amount formatting."""
        from tally.analyzer import format_currency
        assert format_currency(-1234) == "$-1,234"
        assert format_currency(-1234, "{amount} zł") == "-1,234 zł"


class TestTravelClassification:
    """Tests for travel classification based on category, not location codes."""

    def test_travel_classification_based_on_category(self):
        """Travel classification should be based on category='Travel' only."""
        from tally.analyzer import classify_by_occurrence

        # Merchant with Travel category should be classified as travel
        travel_data = {
            'category': 'Travel',
            'subcategory': 'Airline',
            'count': 2,
            'total': 500.0,
            'months_active': 2,
            'cv': 0.1,
            'max_payment': 300.0,
            'is_consistent': True,
        }
        classification, reasoning = classify_by_occurrence('Delta Airlines', travel_data, 12)
        assert classification == 'travel'
        assert 'category=Travel' in reasoning['trace'][1]

    def test_no_travel_classification_from_is_travel_flag(self):
        """Location-based is_travel flag should NOT trigger travel classification."""
        from tally.analyzer import classify_by_occurrence

        # Merchant with is_travel=True but non-Travel category should NOT be travel
        # This simulates the case where a location code like "FG" was misinterpreted
        # as international travel
        data_with_is_travel_flag = {
            'category': 'Bills',
            'subcategory': 'Electric',
            'count': 12,
            'total': 1200.0,
            'months_active': 12,
            'cv': 0.1,
            'max_payment': 100.0,
            'is_consistent': True,
            'is_travel': True,  # This should be ignored
        }
        classification, reasoning = classify_by_occurrence('Local Utility FG', data_with_is_travel_flag, 12)
        # Should be classified as monthly, not travel
        assert classification == 'monthly', f"Expected 'monthly' but got '{classification}'"

    def test_non_travel_category_with_location_code_not_travel(self):
        """Merchants with non-Travel category should not be classified as travel regardless of location."""
        from tally.analyzer import classify_by_occurrence

        # University with "TN" prefix - should NOT be travel even if location detection fired
        university_data = {
            'category': 'Education',
            'subcategory': 'Tuition',
            'count': 4,
            'total': 10000.0,
            'months_active': 4,
            'cv': 0.5,
            'max_payment': 3000.0,
            'is_consistent': False,
            'is_travel': True,  # Location detection may have set this
        }
        classification, reasoning = classify_by_occurrence('TN STATE UNIVERSITY', university_data, 12)
        # Should be classified as periodic (tuition), not travel
        assert classification == 'periodic', f"Expected 'periodic' but got '{classification}'"

    def test_retailer_with_ap_not_travel(self):
        """Retailer with 'AP' in name should not be classified as Asia-Pacific travel."""
        from tally.analyzer import classify_by_occurrence

        # Retailer with "AP" in description - should NOT be travel
        retailer_data = {
            'category': 'Shopping',
            'subcategory': 'Retail',
            'count': 5,
            'total': 500.0,
            'months_active': 3,
            'cv': 0.3,
            'max_payment': 150.0,
            'is_consistent': True,
            'is_travel': True,  # Location detection may have misinterpreted "AP"
        }
        classification, reasoning = classify_by_occurrence('APPLIANCE DEPOT AP', retailer_data, 12)
        # With total=$500 (under $1000), it won't meet one_off criteria, so should be variable
        assert classification == 'variable', f"Expected 'variable' but got '{classification}'"

    def test_travel_trace_no_longer_mentions_is_travel_flag(self):
        """The decision trace should no longer mention is_travel flag."""
        from tally.analyzer import classify_by_occurrence

        # Non-travel merchant
        data = {
            'category': 'Food',
            'subcategory': 'Restaurant',
            'count': 10,
            'total': 500.0,
            'months_active': 5,
            'cv': 0.2,
            'max_payment': 75.0,
            'is_consistent': True,
        }
        _, reasoning = classify_by_occurrence('Local Restaurant', data, 12)

        # Find the travel trace line
        travel_trace = [t for t in reasoning['trace'] if 'travel' in t.lower()]
        assert len(travel_trace) > 0, "Should have a travel trace line"

        # The trace should not mention is_travel=false
        for trace in travel_trace:
            assert 'is_travel=false' not in trace, f"Trace should not mention is_travel flag: {trace}"
            assert 'is_travel=true' not in trace, f"Trace should not mention is_travel flag: {trace}"


class TestRegexDelimiter:
    """Tests for regex-based delimiter parsing (for fixed-width formats like BOA)."""

    def test_regex_delimiter_basic(self):
        """Parse a fixed-width file using regex delimiter."""
        # BOA-style format: Date  Description  Amount  Balance
        txt_content = """01/15/2025  GROCERY STORE PURCHASE                          -123.45     1000.00
01/16/2025  COFFEE SHOP SEATTLE WA                            -5.99      994.01
01/17/2025  BIG PURCHASE FROM STORE                        -1234.56      -240.55
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        try:
            f.write(txt_content)
            f.close()

            rules = get_all_rules()
            # Regex to capture: date, description, amount (negative only), balance
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {-amount}, {_}')
            # Only match negative amounts (debits)
            format_spec.delimiter = r"regex:^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-[\d,]+\.\d{2})\s+([-\d,]+\.\d{2})$"
            format_spec.has_header = False

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 3
            # Amounts should be positive after negation
            assert txns[0]['amount'] == 123.45
            assert txns[1]['amount'] == 5.99
            assert txns[2]['amount'] == 1234.56
            # Descriptions should be captured
            assert 'GROCERY' in txns[0]['raw_description']
            assert 'COFFEE' in txns[1]['raw_description']
        finally:
            os.unlink(f.name)

    def test_regex_delimiter_skips_credits(self):
        """Regex pattern that only matches debits should skip credits."""
        txt_content = """01/15/2025  PAYCHECK DIRECT DEPOSIT                        1000.00     2000.00
01/16/2025  COFFEE SHOP                                        -5.99     1994.01
01/17/2025  TRANSFER IN                                       500.00     2494.01
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        try:
            f.write(txt_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {-amount}, {_}')
            # Only match negative amounts (debits) - note the - before [\d,]
            format_spec.delimiter = r"regex:^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-[\d,]+\.\d{2})\s+([\d,]+\.\d{2})$"
            format_spec.has_header = False

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            # Only the debit should be captured
            assert len(txns) == 1
            assert txns[0]['amount'] == 5.99
            assert 'COFFEE' in txns[0]['raw_description']
        finally:
            os.unlink(f.name)

    def test_tab_delimiter(self):
        """Parse a tab-separated file."""
        tsv_content = "Date\tDescription\tAmount\n01/15/2025\tGROCERY STORE\t123.45\n01/16/2025\tCOFFEE SHOP\t5.99\n"
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False)
        try:
            f.write(tsv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {amount}')
            format_spec.delimiter = 'tab'

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['amount'] == 123.45
            assert txns[1]['amount'] == 5.99
        finally:
            os.unlink(f.name)


class TestAccountType:
    """Tests for account_type presets (credit_card, bank, etc.)."""

    def test_account_type_presets_exist(self):
        """Verify account type presets are defined."""
        from tally.format_parser import ACCOUNT_TYPE_PRESETS, get_account_type_settings

        assert 'credit_card' in ACCOUNT_TYPE_PRESETS
        assert 'bank' in ACCOUNT_TYPE_PRESETS
        assert 'brokerage' in ACCOUNT_TYPE_PRESETS

        # Credit card: no negation, keep refunds
        cc = get_account_type_settings('credit_card')
        assert cc['negate_amount'] == False
        assert cc['skip_negative'] == False

        # Bank: negate amounts, skip income
        bank = get_account_type_settings('bank')
        assert bank['negate_amount'] == True
        assert bank['skip_negative'] == True

    def test_account_type_invalid(self):
        """Unknown account type raises error."""
        from tally.format_parser import get_account_type_settings

        with pytest.raises(ValueError) as exc_info:
            get_account_type_settings('invalid_type')
        assert 'Unknown account_type' in str(exc_info.value)

    def test_credit_card_keeps_signs(self):
        """Credit card account type keeps amounts as-is."""
        csv_content = """Date,Description,Amount
01/15/2025,PURCHASE,50.00
01/16/2025,REFUND,-25.00
01/17/2025,PAYMENT,-500.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {amount}')
            # Apply credit_card preset
            format_spec.negate_amount = False
            format_spec.skip_negative = False

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 3
            assert txns[0]['amount'] == 50.00   # Purchase: positive
            assert txns[1]['amount'] == -25.00  # Refund: negative (kept)
            assert txns[2]['amount'] == -500.00 # Payment: negative (kept)
        finally:
            os.unlink(f.name)

    def test_bank_negates_and_skips_income(self):
        """Bank account type negates amounts and skips income."""
        csv_content = """Date,Description,Amount
01/15/2025,GROCERY STORE,-50.00
01/16/2025,PAYCHECK,2000.00
01/17/2025,COFFEE,-5.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {amount}')
            # Apply bank preset
            format_spec.negate_amount = True
            format_spec.skip_negative = True

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            # Only debits (negated to positive), income skipped
            assert len(txns) == 2
            assert txns[0]['amount'] == 50.00  # -50 negated to +50
            assert txns[1]['amount'] == 5.00   # -5 negated to +5
            # Paycheck (2000 negated to -2000) is skipped
        finally:
            os.unlink(f.name)

    def test_explicit_settings_override_account_type(self):
        """Explicit negate_amount/skip_negative override account_type defaults."""
        from tally.config_loader import resolve_source_format

        source = {
            'name': 'Test',
            'file': 'test.csv',
            'format': '{date}, {description}, {amount}',
            'account_type': 'bank',  # Would set negate=True, skip=True
            'skip_negative': False,  # Override: keep income
        }

        resolved = resolve_source_format(source)
        spec = resolved['_format_spec']

        assert spec.negate_amount == True   # From account_type
        assert spec.skip_negative == False  # Overridden
