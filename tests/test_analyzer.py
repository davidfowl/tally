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

    def test_iso_date_format(self):
        """Parse ISO date format (YYYY-MM-DD) without spaces."""
        csv_content = """Date,Description,Amount
2025-01-15,GROCERY STORE,50.00
2025-02-16,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%Y-%m-%d},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].year == 2025
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].day == 15
        finally:
            os.unlink(f.name)

    def test_date_with_time_component(self):
        """Parse date format with time component (spaces in format)."""
        csv_content = """Date,Description,Amount
2025-01-15 14:30,GROCERY STORE,50.00
2025-02-16 09:15,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Date-time format with space between date and time
            format_spec = parse_format_string('{date:%Y-%m-%d %H:%M},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].year == 2025
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].day == 15
            assert txns[0]['date'].hour == 14
            assert txns[0]['date'].minute == 30
        finally:
            os.unlink(f.name)

    def test_european_date_format_with_dots(self):
        """Parse European date format with dots (DD.MM.YYYY)."""
        csv_content = """Date,Description,Amount
15.01.2025,GROCERY STORE,50.00
16.02.2025,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%d.%m.%Y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].day == 15
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].year == 2025
        finally:
            os.unlink(f.name)

    def test_date_with_leading_trailing_spaces(self):
        """Dates with extra leading/trailing spaces should be trimmed."""
        csv_content = """Date,Description,Amount
  01/15/2025  ,GROCERY STORE,50.00
   01/16/2025   ,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].day == 15
        finally:
            os.unlink(f.name)

    def test_abbreviated_month_name_with_period(self):
        """Parse date with abbreviated month that includes period."""
        csv_content = """Date,Description,Amount
15 Jan. 2025,GROCERY STORE,50.00
16 Feb. 2025,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Format with abbreviated month and period: "15 Jan. 2025"
            format_spec = parse_format_string('{date:%d %b. %Y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].day == 15
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].year == 2025
        finally:
            os.unlink(f.name)

    def test_multiple_spaces_in_date_format(self):
        """Date format with multiple spaces should be handled correctly."""
        csv_content = """Date,Description,Amount
15  Jan  2025,GROCERY STORE,50.00
16  Feb  2025,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Format with double spaces: "15  Jan  2025"
            format_spec = parse_format_string('{date:%d  %b  %Y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].day == 15
            assert txns[0]['date'].month == 1
        finally:
            os.unlink(f.name)

    def test_date_with_day_suffix_multiple_spaces(self):
        """Date without spaces in format should strip suffix even with multiple spaces."""
        csv_content = """Date,Description,Amount
01/15/2025    Wednesday,GROCERY STORE,50.00
01/16/2025  Thu,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Format without spaces - should strip any suffix
            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].day == 15
        finally:
            os.unlink(f.name)

    def test_two_digit_year_format(self):
        """Parse date with two-digit year."""
        csv_content = """Date,Description,Amount
01/15/25,GROCERY STORE,50.00
12/31/25,COFFEE SHOP,5.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%y},{description},{amount}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['date'].month == 1
            assert txns[0]['date'].day == 15
            assert txns[0]['date'].year == 2025
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

    def test_mixed_mode_creates_extra_fields(self):
        """Mixing {description} with custom captures creates extra_fields for field.* access."""
        format_spec = parse_format_string('{date},{description},{merchant},{amount}')

        # {description} is captured directly
        assert format_spec.description_column == 1
        # Other captures become extra_fields for field.* access in rules
        assert format_spec.extra_fields == {'merchant': 2}
        # No custom_captures (that's only for template mode)
        assert format_spec.custom_captures is None

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

    def test_asterisk_skip_column_alias(self):
        """Test that {*} works as an alias for {_} to skip columns."""
        csv_content = """Date,Amount,Ignored1,Ignored2,Description
01/15/2025,25.50,*,,STARBUCKS COFFEE
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Use {*} instead of {_} to skip columns - should work
            format_spec = parse_format_string('{date:%m/%d/%Y},{amount},{*},{*},{description}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert txns[0]['raw_description'] == 'STARBUCKS COFFEE'
            assert txns[0]['amount'] == 25.50
        finally:
            os.unlink(f.name)

    def test_mixed_asterisk_and_underscore_skip(self):
        """Test that {*} and {_} can be mixed in the same format string."""
        csv_content = """Date,Amount,Skip1,Skip2,Description
01/15/2025,30.00,ignored,also_ignored,MERCHANT NAME
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Mix {*} and {_} - both should skip
            format_spec = parse_format_string('{date:%m/%d/%Y},{amount},{*},{_},{description}')
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert txns[0]['raw_description'] == 'MERCHANT NAME'
            assert txns[0]['amount'] == 30.00
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


class TestAmountSignHandling:
    """Tests for amount sign handling - signs flow through, no auto-exclusion."""

    def test_account_type_raises_error(self):
        """account_type setting is no longer supported."""
        from tally.config_loader import resolve_source_format

        source = {
            'name': 'Test',
            'file': 'test.csv',
            'format': '{date}, {description}, {amount}',
            'account_type': 'bank',
        }

        with pytest.raises(ValueError) as exc_info:
            resolve_source_format(source)
        assert 'no longer supported' in str(exc_info.value)
        assert '{-amount}' in str(exc_info.value)  # Suggests the alternative

    def test_skip_negative_raises_error(self):
        """skip_negative setting is no longer supported."""
        from tally.config_loader import resolve_source_format

        source = {
            'name': 'Test',
            'file': 'test.csv',
            'format': '{date}, {description}, {amount}',
            'skip_negative': True,
        }

        with pytest.raises(ValueError) as exc_info:
            resolve_source_format(source)
        assert 'no longer supported' in str(exc_info.value)

    def test_amount_signs_preserved(self):
        """Amounts keep their signs from the CSV."""
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

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 3
            assert txns[0]['amount'] == 50.00   # Positive preserved
            assert txns[1]['amount'] == -25.00  # Negative preserved
            assert txns[2]['amount'] == -500.00 # Negative preserved
            # No auto-exclusion
            assert txns[0].get('excluded') is None
            assert txns[1].get('excluded') is None
            assert txns[2].get('excluded') is None
        finally:
            os.unlink(f.name)

    def test_negate_amount_flips_signs(self):
        """Using {-amount} flips all signs."""
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
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {-amount}')

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 3
            assert txns[0]['amount'] == 50.00    # -50 negated to +50
            assert txns[1]['amount'] == -2000.00 # +2000 negated to -2000
            assert txns[2]['amount'] == 5.00     # -5 negated to +5
            # No auto-exclusion - all transactions included
            assert txns[0].get('excluded') is None
            assert txns[1].get('excluded') is None
            assert txns[2].get('excluded') is None
        finally:
            os.unlink(f.name)

    def test_abs_amount_makes_all_positive(self):
        """Using {+amount} takes absolute value of all amounts."""
        csv_content = """Date,Description,Amount
01/15/2025,MORTGAGE PAYMENT,500.00
01/16/2025,ESCROW TAX,-200.00
01/17/2025,INSURANCE PAYMENT,-150.00
01/18/2025,PRINCIPAL,300.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {+amount}')

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 4
            # All amounts should be positive (absolute value)
            assert txns[0]['amount'] == 500.00   # +500 stays +500
            assert txns[1]['amount'] == 200.00   # -200 becomes +200
            assert txns[2]['amount'] == 150.00   # -150 becomes +150
            assert txns[3]['amount'] == 300.00   # +300 stays +300
            # All should show as spending (positive), not credits
            assert txns[0]['is_credit'] == False
            assert txns[1]['is_credit'] == False
            assert txns[2]['is_credit'] == False
            assert txns[3]['is_credit'] == False
        finally:
            os.unlink(f.name)

    def test_abs_amount_with_small_amounts(self):
        """Using {+amount} handles small positive and negative amounts."""
        csv_content = """Date,Description,Amount
01/15/2025,SMALL POSITIVE,0.01
01/16/2025,SMALL NEGATIVE,-0.01
01/17/2025,ONE CENT,-0.99
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {+amount}')

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 3
            assert txns[0]['amount'] == 0.01  # Stays positive
            assert txns[1]['amount'] == 0.01  # Negative becomes positive
            assert txns[2]['amount'] == 0.99  # Negative becomes positive
        finally:
            os.unlink(f.name)

    def test_abs_amount_with_large_amounts(self):
        """Using {+amount} handles large positive and negative amounts."""
        csv_content = """Date,Description,Amount
01/15/2025,LARGE POSITIVE,"123,456.78"
01/16/2025,LARGE NEGATIVE,"-98,765.43"
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {+amount}')

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2
            assert txns[0]['amount'] == 123456.78  # Stays positive
            assert txns[1]['amount'] == 98765.43   # Negative becomes positive
        finally:
            os.unlink(f.name)

    def test_abs_amount_excludes_nothing(self):
        """Using {+amount} means nothing is excluded as income - all are spending."""
        csv_content = """Date,Description,Amount
01/15/2025,PRINCIPAL PAYMENT,500.00
01/16/2025,TAX FROM ESCROW,-200.00
01/17/2025,INSURANCE FROM ESCROW,-150.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {+amount}')

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 3
            # No transactions should be excluded
            for txn in txns:
                assert txn.get('excluded') is None
            # All amounts positive
            assert all(txn['amount'] > 0 for txn in txns)
        finally:
            os.unlink(f.name)

    def test_abs_amount_format_spec_flag(self):
        """Parse {+amount} sets abs_amount flag in FormatSpec."""
        format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {+amount}')
        assert format_spec.abs_amount == True
        assert format_spec.negate_amount == False

        format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {amount}')
        assert format_spec.abs_amount == False
        assert format_spec.negate_amount == False

        format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {-amount}')
        assert format_spec.abs_amount == False
        assert format_spec.negate_amount == True

    def test_is_credit_flag_set_correctly(self):
        """is_credit flag is True for negative amounts."""
        csv_content = """Date,Description,Amount
01/15/2025,PURCHASE,50.00
01/16/2025,REFUND,-25.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y}, {description}, {amount}')

            from tally.analyzer import parse_generic_csv
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert txns[0]['is_credit'] == False  # Positive = not credit
            assert txns[1]['is_credit'] == True   # Negative = credit
        finally:
            os.unlink(f.name)


class TestSpecialTags:
    """Tests for special tags that affect spending analysis (income, transfer)."""

    def _create_transactions(self, txn_list):
        """Helper to create transaction dicts for testing."""
        from datetime import datetime
        transactions = []
        for i, (desc, amount, category, tags) in enumerate(txn_list):
            transactions.append({
                'date': datetime(2025, 1, 15 + i),
                'description': desc,
                'raw_description': desc,
                'merchant': desc.split()[0],
                'amount': amount,
                'category': category,
                'subcategory': 'Test',
                'source': 'Test',
                'is_credit': amount < 0,
                'match_info': {'tags': tags} if tags else None,
                'tags': tags or [],
                'excluded': None,
            })
        return transactions

    def test_income_tag_excludes_from_spending(self):
        """Transactions with 'income' tag are excluded from spending analysis."""
        from tally.analyzer import analyze_transactions

        txns = self._create_transactions([
            ('GROCERY STORE', 50.00, 'Food', []),
            ('PAYCHECK DEPOSIT', 2000.00, 'Income', ['income']),
            ('COFFEE SHOP', 5.00, 'Food', []),
        ])

        stats = analyze_transactions(txns)

        # Income should be excluded
        assert stats['excluded_count'] == 1
        assert stats['excluded_transactions'][0]['merchant'] == 'PAYCHECK'
        # Spending should only include grocery + coffee = $55
        total_spending = sum(data['total'] for data in stats['by_category'].values())
        assert total_spending == 55.00

    def test_transfer_tag_excludes_from_spending(self):
        """Transactions with 'transfer' tag are excluded from spending analysis."""
        from tally.analyzer import analyze_transactions

        txns = self._create_transactions([
            ('GROCERY STORE', 50.00, 'Food', []),
            ('CC PAYMENT THANK YOU', 500.00, 'Finance', ['transfer']),
            ('COFFEE SHOP', 5.00, 'Food', []),
        ])

        stats = analyze_transactions(txns)

        # Transfer should be excluded
        assert stats['excluded_count'] == 1
        assert stats['excluded_transactions'][0]['merchant'] == 'CC'
        # Spending should only include grocery + coffee = $55
        total_spending = sum(data['total'] for data in stats['by_category'].values())
        assert total_spending == 55.00

    def test_refund_tag_not_special(self):
        """Refund tag is not special - refunds are regular transactions that net against spending."""
        from tally.analyzer import analyze_transactions

        txns = self._create_transactions([
            ('AMAZON PURCHASE', 100.00, 'Shopping', []),
            ('AMAZON REFUND', -25.00, 'Shopping', ['refund']),  # Just a regular tag
            ('GROCERY STORE', 50.00, 'Food', []),
        ])

        stats = analyze_transactions(txns)

        # Refund should NOT be excluded (refund tag is not special)
        assert stats['excluded_count'] == 0
        # Spending should include all: 100 - 25 + 50 = 125
        total_spending = sum(data['total'] for data in stats['by_category'].values())
        assert total_spending == 125.00

    def test_multiple_special_tags(self):
        """Multiple transactions with different special tags."""
        from tally.analyzer import analyze_transactions

        txns = self._create_transactions([
            ('GROCERY STORE', 50.00, 'Food', []),
            ('SALARY DEPOSIT', 3000.00, 'Income', ['income']),
            ('VENMO TRANSFER', 100.00, 'Finance', ['transfer']),
            ('AMAZON REFUND', -30.00, 'Shopping', []),  # Regular transaction
            ('COFFEE SHOP', 5.00, 'Food', []),
        ])

        stats = analyze_transactions(txns)

        # Income + transfer should be excluded
        assert stats['excluded_count'] == 2
        # Spending: grocery(50) + refund(-30) + coffee(5) = 25
        total_spending = sum(data['total'] for data in stats['by_category'].values())
        assert total_spending == 25.00

    def test_category_no_longer_excludes(self):
        """Categories like 'Transfers' no longer auto-exclude without tags."""
        from tally.analyzer import analyze_transactions

        txns = self._create_transactions([
            ('GROCERY STORE', 50.00, 'Food', []),
            ('VENMO PAYMENT', 100.00, 'Transfers', []),  # No transfer tag
            ('COFFEE SHOP', 5.00, 'Food', []),
        ])

        stats = analyze_transactions(txns)

        # Nothing should be excluded (no special tags)
        assert stats['excluded_count'] == 0
        # All spending included: 50 + 100 + 5 = 155
        total_spending = sum(data['total'] for data in stats['by_category'].values())
        assert total_spending == 155.00


class TestCustomFieldCaptures:
    """Tests for custom CSV fields captured and used in rule expressions."""

    def test_custom_field_stored_in_transaction(self):
        """Custom captures from CSV format are stored in transaction dict."""
        csv_content = """01/15/2025,WIRE,REF:12345,ACME CORP,1000.00
01/16/2025,ACH,PAYROLL,EMPLOYER,2500.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            # Format with custom captures: txn_type, memo, vendor
            # Must pass description_template to the function
            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{memo},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False  # No header row in test data

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2

            # First transaction should have captured fields
            assert txns[0]['field'] is not None
            assert txns[0]['field']['txn_type'] == 'WIRE'
            assert txns[0]['field']['memo'] == 'REF:12345'
            assert txns[0]['field']['vendor'] == 'ACME CORP'

            # Second transaction
            assert txns[1]['field']['txn_type'] == 'ACH'
            assert txns[1]['field']['memo'] == 'PAYROLL'
        finally:
            os.unlink(f.name)

    def test_simple_description_no_custom_field(self):
        """Simple {description} format has no custom field captures."""
        csv_content = """01/15/2025,AMAZON PURCHASE,50.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = get_all_rules()
            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            format_spec.has_header = False  # No header row in test data
            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            # No custom captures - field should be None or empty
            assert txns[0]['field'] is None or txns[0]['field'] == {}
        finally:
            os.unlink(f.name)

    def test_field_used_in_rule_matching(self):
        """Custom field can be used in merchant rule expressions."""
        csv_content = """01/15/2025,WIRE,REF:12345,BANK PAYMENT,1000.00
01/16/2025,ACH,PAYROLL,BANK PAYMENT,2500.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            # Create rules that use field.txn_type
            rules = [
                # Match expression, merchant, category, subcategory, parsed, source, tags
                ('field.txn_type == "WIRE"', 'Wire Transfer', 'Transfers', 'Wire', None, 'test', ['wire']),
                ('field.txn_type == "ACH"', 'ACH Transfer', 'Transfers', 'ACH', None, 'test', ['ach']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{memo},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False  # No header row in test data

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2

            # First should match WIRE rule
            assert txns[0]['merchant'] == 'Wire Transfer'
            assert txns[0]['category'] == 'Transfers'
            assert 'wire' in txns[0]['tags']

            # Second should match ACH rule
            assert txns[1]['merchant'] == 'ACH Transfer'
            assert txns[1]['category'] == 'Transfers'
            assert 'ach' in txns[1]['tags']
        finally:
            os.unlink(f.name)

    def test_field_with_contains_function(self):
        """Field value can be searched with contains()."""
        csv_content = """01/15/2025,Invoice #12345 Payment,VENDOR A,500.00
01/16/2025,Regular purchase,VENDOR B,100.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                ('contains(field.memo, "Invoice")', 'Invoice Payment', 'Bills', 'Invoice', None, 'test', ['invoice']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{memo},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False  # No header row in test data

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2

            # First matches the Invoice rule
            assert txns[0]['merchant'] == 'Invoice Payment'
            assert 'invoice' in txns[0]['tags']

            # Second doesn't match - should be Unknown
            assert txns[1]['category'] == 'Unknown'
        finally:
            os.unlink(f.name)

    def test_field_with_extract_function(self):
        """Extract function can parse data from field values."""
        csv_content = """01/15/2025,REF:ABC123,VENDOR A,500.00
01/16/2025,REF:XYZ789,VENDOR B,100.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                (r'extract(field.memo, "REF:(\\w+)") == "ABC123"', 'Specific Ref', 'Payments', 'Ref', None, 'test', []),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{memo},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False  # No header row in test data

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2

            # First matches the specific reference
            assert txns[0]['merchant'] == 'Specific Ref'

            # Second doesn't match
            assert txns[1]['merchant'] != 'Specific Ref'
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_from_field(self):
        """Tags can use {field.name} to get dynamic values."""
        csv_content = """01/15/2025,WIRE,REF:12345,BANK PAYMENT,1000.00
01/16/2025,ACH,PAYROLL,BANK PAYMENT,2500.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            # Rule with dynamic tag from field value
            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test', ['banking', '{field.txn_type}']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{memo},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2

            # First transaction: static "banking" + dynamic "wire" from field.txn_type
            assert 'banking' in txns[0]['tags']
            assert 'wire' in txns[0]['tags']  # Lowercased from "WIRE"

            # Second transaction: static "banking" + dynamic "ach" from field.txn_type
            assert 'banking' in txns[1]['tags']
            assert 'ach' in txns[1]['tags']  # Lowercased from "ACH"
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_with_extract(self):
        """Tags can use extraction functions for dynamic values."""
        csv_content = """01/15/2025,PROJ:ALPHA Payment,VENDOR A,500.00
01/16/2025,PROJ:BETA Invoice,VENDOR B,100.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            # Rule with dynamic tag using extract function
            # Match on field.memo since description is {vendor}
            rules = [
                ('contains(field.memo, "PROJ:")', 'Project Payment', 'Bills', 'Project', None, 'test',
                 ['project', r'{extract(field.memo, "PROJ:(\\w+)")}']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{memo},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2

            # First: "project" + "alpha" (extracted and lowercased)
            assert 'project' in txns[0]['tags']
            assert 'alpha' in txns[0]['tags']

            # Second: "project" + "beta"
            assert 'project' in txns[1]['tags']
            assert 'beta' in txns[1]['tags']
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_empty_value_skipped(self):
        """Dynamic tags with empty values are skipped."""
        csv_content = """01/15/2025,WIRE,BANK PAYMENT,1000.00
01/16/2025,,BANK PAYMENT,500.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            # Rule with dynamic tag - empty field value should be skipped
            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test', ['banking', '{field.txn_type}']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 2

            # First: has both tags
            assert 'banking' in txns[0]['tags']
            assert 'wire' in txns[0]['tags']

            # Second: only static tag (empty field.txn_type skipped)
            assert 'banking' in txns[1]['tags']
            assert len(txns[1]['tags']) == 1  # Only 'banking'
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_invalid_expression_skipped(self):
        """Dynamic tags with invalid expressions are silently skipped."""
        csv_content = """01/15/2025,WIRE,BANK PAYMENT,1000.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            # Rule with invalid dynamic tag expression
            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test',
                 ['banking', '{invalid_syntax(}']),  # Invalid expression
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            # Only static tag, invalid expression is skipped
            assert txns[0]['tags'] == ['banking']
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_nonexistent_field_skipped(self):
        """Dynamic tags referencing non-existent fields are skipped."""
        csv_content = """01/15/2025,WIRE,BANK PAYMENT,1000.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            # Rule with dynamic tag referencing field that doesn't exist
            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test',
                 ['banking', '{field.nonexistent}']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            # Only static tag, nonexistent field is skipped
            assert txns[0]['tags'] == ['banking']
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_multiple_dynamic(self):
        """Multiple dynamic tags in same rule."""
        csv_content = """01/15/2025,WIRE,OUT,BANK PAYMENT,1000.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            # Rule with multiple dynamic tags
            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test',
                 ['{field.txn_type}', '{field.direction}']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{direction},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert 'wire' in txns[0]['tags']
            assert 'out' in txns[0]['tags']
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_whitespace_only_skipped(self):
        """Dynamic tags that evaluate to whitespace-only are skipped."""
        csv_content = """01/15/2025,   ,BANK PAYMENT,1000.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test',
                 ['banking', '{field.txn_type}']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            # Only static tag (whitespace-only field.txn_type is skipped after trim)
            assert txns[0]['tags'] == ['banking']
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_empty_braces_skipped(self):
        """Empty braces {} are skipped."""
        csv_content = """01/15/2025,WIRE,BANK PAYMENT,1000.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test',
                 ['banking', '{}']),  # Empty braces
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert txns[0]['tags'] == ['banking']
        finally:
            os.unlink(f.name)

    def test_dynamic_tags_case_normalization(self):
        """Dynamic tag values are lowercased."""
        csv_content = """01/15/2025,WIRE_TRANSFER,BANK PAYMENT,1000.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test',
                 ['{field.txn_type}']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            # Mixed case becomes lowercase
            assert 'wire_transfer' in txns[0]['tags']
            assert 'WIRE_TRANSFER' not in txns[0]['tags']
        finally:
            os.unlink(f.name)


class TestFieldAccessEdgeCases:
    """Edge case tests for field access in rule expressions."""

    def test_field_comparison_case_sensitivity(self):
        """Field comparison is case-insensitive for string equality."""
        from tally.expr_parser import matches_transaction

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'code': 'WIRE'}
        }
        # Case insensitive comparison
        assert matches_transaction('field.code == "wire"', txn)
        assert matches_transaction('field.code == "WIRE"', txn)
        assert matches_transaction('field.code == "Wire"', txn)

    def test_field_with_special_characters(self):
        """Field values with special characters work correctly."""
        from tally.expr_parser import matches_transaction

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'memo': 'REF#12345-ABC/XYZ'}
        }
        assert matches_transaction('contains(field.memo, "REF#")', txn)
        assert matches_transaction('contains(field.memo, "-ABC/")', txn)

    def test_field_with_quotes_in_value(self):
        """Field values containing quotes work correctly."""
        from tally.expr_parser import matches_transaction

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'memo': 'Payment for "Project X"'}
        }
        assert matches_transaction('contains(field.memo, "Project")', txn)

    def test_field_empty_string_vs_missing(self):
        """Empty string field is different from missing field."""
        from tally.expr_parser import matches_transaction, ExpressionError
        import pytest

        # Empty string field
        txn_empty = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'code': ''}
        }
        assert matches_transaction('field.code == ""', txn_empty)
        assert not matches_transaction('exists(field.code)', txn_empty)

        # Missing field raises error
        txn_missing = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'other': 'value'}
        }
        with pytest.raises(ExpressionError):
            matches_transaction('field.code == ""', txn_missing)

    def test_field_numeric_string(self):
        """Field with numeric string value."""
        from tally.expr_parser import matches_transaction

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'ref_num': '12345'}
        }
        # String comparison
        assert matches_transaction('field.ref_num == "12345"', txn)
        # Can use in extract
        assert matches_transaction(r'extract(field.ref_num, "(\\d+)") == "12345"', txn)

    def test_exists_with_none_field_dict(self):
        """exists() returns False when field dict is None."""
        from tally.expr_parser import matches_transaction

        txn = {'description': 'TEST', 'amount': 100.00}
        # No 'field' key at all
        assert not matches_transaction('exists(field.anything)', txn)

    def test_exists_short_circuit_evaluation(self):
        """exists() allows short-circuit evaluation to prevent errors."""
        from tally.expr_parser import matches_transaction

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'code': 'ABC'}
        }
        # Safe pattern - exists guards the access
        assert matches_transaction('exists(field.code) and field.code == "ABC"', txn)
        # Missing field - exists returns False, short-circuits
        assert not matches_transaction('exists(field.missing) and field.missing == "X"', txn)

    def test_extraction_functions_return_strings(self):
        """Extraction functions always return strings."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'REF:12345', 'amount': 100.00}

        # extract returns string
        result = evaluate_transaction(r'extract("REF:(\\d+)")', txn)
        assert isinstance(result, str)
        assert result == '12345'

        # split returns string
        txn2 = {'description': 'A-B-C', 'amount': 100.00}
        result = evaluate_transaction('split("-", 1)', txn2)
        assert isinstance(result, str)
        assert result == 'B'

        # substring returns string
        result = evaluate_transaction('substring(0, 1)', txn2)
        assert isinstance(result, str)
        assert result == 'A'

    def test_split_negative_index(self):
        """split() with negative index returns empty string."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'A-B-C', 'amount': 100.00}
        result = evaluate_transaction('split("-", -1)', txn)
        assert result == ''

    def test_substring_negative_indices(self):
        """substring() with negative indices."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'ABCDEF', 'amount': 100.00}
        # Python allows negative indices in slicing
        result = evaluate_transaction('substring(-3, -1)', txn)
        # This is 'ABCDEF'[-3:-1] = 'DE'
        assert result == 'DE'

    def test_extract_multiple_groups_returns_first(self):
        """extract() with multiple capture groups returns first one."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': 'ORDER-ABC-12345-XYZ', 'amount': 100.00}
        result = evaluate_transaction(r'extract("ORDER-(\\w+)-(\\d+)-(\\w+)")', txn)
        # Should return first group only
        assert result == 'ABC'

    def test_trim_preserves_internal_whitespace(self):
        """trim() only removes leading/trailing whitespace."""
        from tally.expr_parser import evaluate_transaction

        txn = {'description': '  HELLO   WORLD  ', 'amount': 100.00}
        result = evaluate_transaction('trim()', txn)
        assert result == 'HELLO   WORLD'  # Internal spaces preserved


class TestSourceDynamicTags:
    """Tests for using source in dynamic tags and expressions."""

    def test_source_in_expression(self):
        """source variable is accessible in rule expressions."""
        from tally.expr_parser import matches_transaction

        txn = {
            'description': 'PURCHASE',
            'amount': 100.00,
            'source': 'Amex',
        }
        assert matches_transaction('source == "amex"', txn)
        assert matches_transaction('source == "Amex"', txn)
        assert not matches_transaction('source == "Chase"', txn)

    def test_source_dynamic_tag(self):
        """source can be used as a dynamic tag."""
        csv_content = """01/15/2025,AMAZON PURCHASE,50.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                ('contains("AMAZON")', 'Amazon', 'Shopping', 'Online', None, 'test', ['{source}']),
            ]

            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            format_spec.has_header = False
            format_spec.source_name = 'AmexGold'  # Custom source name

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert 'amexgold' in txns[0]['tags']  # Lowercased
        finally:
            os.unlink(f.name)

    def test_source_dynamic_tag_mixed_with_static(self):
        """source dynamic tag works with static tags."""
        csv_content = """01/15/2025,AMAZON PURCHASE,50.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                ('contains("AMAZON")', 'Amazon', 'Shopping', 'Online', None, 'test',
                 ['shopping', '{source}', 'online']),
            ]

            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            format_spec.has_header = False
            format_spec.source_name = 'Chase'

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert 'shopping' in txns[0]['tags']
            assert 'chase' in txns[0]['tags']
            assert 'online' in txns[0]['tags']
        finally:
            os.unlink(f.name)

    def test_source_empty_skipped(self):
        """Empty source value is skipped as tag."""
        csv_content = """01/15/2025,AMAZON PURCHASE,50.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                ('contains("AMAZON")', 'Amazon', 'Shopping', 'Online', None, 'test',
                 ['shopping', '{source}']),
            ]

            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            format_spec.has_header = False
            # source_name not set - will use default 'CSV' from parameter

            txns = parse_generic_csv(f.name, format_spec, rules, source_name='')

            assert len(txns) == 1
            # Empty source should be skipped, only static tag remains
            assert txns[0]['tags'] == ['shopping']
        finally:
            os.unlink(f.name)

    def test_source_with_field_combination(self):
        """source and field can be combined in tags."""
        csv_content = """01/15/2025,WIRE,BANK PAYMENT,1000.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                ('contains("BANK")', 'Bank Transfer', 'Transfers', 'Bank', None, 'test',
                 ['{source}', '{field.txn_type}']),
            ]

            format_spec = parse_format_string(
                '{date:%m/%d/%Y},{txn_type},{vendor},{amount}',
                description_template='{vendor}'
            )
            format_spec.has_header = False
            format_spec.source_name = 'WellsFargo'

            txns = parse_generic_csv(f.name, format_spec, rules)

            assert len(txns) == 1
            assert 'wellsfargo' in txns[0]['tags']
            assert 'wire' in txns[0]['tags']
        finally:
            os.unlink(f.name)

    def test_source_in_matching_expression(self):
        """source can be used to conditionally match by data source."""
        csv_content = """01/15/2025,AMAZON PURCHASE,50.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            rules = [
                # Only match for Amex source
                ('contains("AMAZON") and source == "Amex"', 'Amazon Amex', 'Shopping', 'Amex', None, 'test', ['amex-purchase']),
                # Fallback for other sources
                ('contains("AMAZON")', 'Amazon', 'Shopping', 'Online', None, 'test', ['generic-purchase']),
            ]

            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            format_spec.has_header = False

            # Test with Amex source
            format_spec.source_name = 'Amex'
            txns = parse_generic_csv(f.name, format_spec, rules)
            assert len(txns) == 1
            assert txns[0]['merchant'] == 'Amazon Amex'
            assert 'amex-purchase' in txns[0]['tags']

            # Test with Chase source - should fall through to second rule
            format_spec.source_name = 'Chase'
            txns = parse_generic_csv(f.name, format_spec, rules)
            assert len(txns) == 1
            assert txns[0]['merchant'] == 'Amazon'
            assert 'generic-purchase' in txns[0]['tags']
        finally:
            os.unlink(f.name)

    def test_source_none_handled_gracefully(self):
        """source being None doesn't cause errors."""
        from tally.expr_parser import TransactionContext, TransactionEvaluator, parse_expression

        ctx = TransactionContext(
            description='TEST',
            amount=100.00,
            source=None,  # Explicitly None
        )
        evaluator = TransactionEvaluator(ctx)

        # source == "" should work (None becomes empty string)
        tree = parse_expression('source == ""')
        assert evaluator.evaluate(tree) == True

        # Using source as tag when None should result in empty string
        assert ctx.source == ""

    def test_source_cardholder_tag_example(self):
        """Example: Use source to tag transactions by card holder."""
        csv_content = """01/15/2025,GROCERIES,150.00
01/16/2025,GAS STATION,50.00
"""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        try:
            f.write(csv_content)
            f.close()

            # Universal rules that tag by source (cardholder name)
            rules = [
                ('contains("GROCERIES")', 'Grocery Store', 'Food', 'Grocery', None, 'test', ['food', '{source}']),
                ('contains("GAS")', 'Gas Station', 'Transport', 'Gas', None, 'test', ['transport', '{source}']),
            ]

            format_spec = parse_format_string('{date:%m/%d/%Y},{description},{amount}')
            format_spec.has_header = False

            # Alice's Amex card
            format_spec.source_name = 'Alice-Amex'
            txns = parse_generic_csv(f.name, format_spec, rules)
            assert len(txns) == 2
            assert 'alice-amex' in txns[0]['tags']
            assert 'food' in txns[0]['tags']
            assert 'alice-amex' in txns[1]['tags']

            # Bob's Chase card
            format_spec.source_name = 'Bob-Chase'
            txns = parse_generic_csv(f.name, format_spec, rules)
            assert 'bob-chase' in txns[0]['tags']
            assert 'bob-chase' in txns[1]['tags']
        finally:
            os.unlink(f.name)

    def test_mixed_sources_field_missing(self):
        """Mixing sources where some have custom fields and others don't."""
        from tally.merchant_utils import normalize_merchant, _resolve_dynamic_tags

        # Transaction with field
        txn_with_field = {
            'description': 'BANK PAYMENT',
            'amount': 100.00,
            'field': {'txn_type': 'WIRE'},
            'source': 'BankA',
        }

        # Transaction without field (field is None)
        txn_without_field = {
            'description': 'BANK PAYMENT',
            'amount': 100.00,
            'field': None,
            'source': 'BankB',
        }

        # Tags that use both source and field
        tags = ['{source}', '{field.txn_type}']

        # With field - both resolve
        resolved = _resolve_dynamic_tags(tags, txn_with_field)
        assert 'banka' in resolved
        assert 'wire' in resolved

        # Without field - source resolves, field.txn_type fails gracefully
        resolved = _resolve_dynamic_tags(tags, txn_without_field)
        assert 'bankb' in resolved
        assert len(resolved) == 1  # Only source, field.txn_type was skipped

    def test_source_whitespace_only_skipped(self):
        """source with only whitespace is skipped as tag."""
        from tally.merchant_utils import _resolve_dynamic_tags

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'source': '   ',  # Only whitespace
        }

        tags = ['static', '{source}']
        resolved = _resolve_dynamic_tags(tags, txn)

        # Only static tag remains, whitespace source is skipped
        assert resolved == ['static']

    def test_field_whitespace_only_skipped(self):
        """field with only whitespace is skipped as tag."""
        from tally.merchant_utils import _resolve_dynamic_tags

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'type': '   '},  # Only whitespace
        }

        tags = ['static', '{field.type}']
        resolved = _resolve_dynamic_tags(tags, txn)

        # Only static tag remains
        assert resolved == ['static']

    def test_expression_result_stripped(self):
        """Expression results are stripped of whitespace."""
        from tally.merchant_utils import _resolve_dynamic_tags

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'field': {'type': '  WIRE  '},  # Whitespace around value
        }

        tags = ['{field.type}']
        resolved = _resolve_dynamic_tags(tags, txn)

        # Value is trimmed and lowercased
        assert resolved == ['wire']

    def test_source_with_leading_trailing_whitespace(self):
        """source with leading/trailing whitespace is trimmed."""
        from tally.merchant_utils import _resolve_dynamic_tags

        txn = {
            'description': 'TEST',
            'amount': 100.00,
            'source': '  Amex  ',
        }

        tags = ['{source}']
        resolved = _resolve_dynamic_tags(tags, txn)

        assert resolved == ['amex']
