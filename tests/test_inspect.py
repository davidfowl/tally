"""Tests for inspect command - CSV sniffing and column analysis."""

import csv
import pytest
import tempfile
import os

from tally.commands.inspect import (
    _detect_column_type,
    _analyze_columns,
    _analyze_amount_column_detailed,
    _detect_currency_symbol,
)


class TestDetectColumnType:
    """Tests for _detect_column_type function."""

    def test_date_mm_dd_yyyy(self):
        """Detect US date format MM/DD/YYYY."""
        values = ['01/15/2025', '02/28/2025', '12/31/2025']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'date'
        assert fmt == '%m/%d/%Y'

    def test_date_yyyy_mm_dd(self):
        """Detect ISO date format YYYY-MM-DD."""
        values = ['2025-01-15', '2025-02-28', '2025-12-31']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'date'
        assert fmt == '%Y-%m-%d'

    def test_date_european(self):
        """Detect European date format DD.MM.YYYY."""
        values = ['15.01.2025', '28.02.2025', '31.12.2025']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'date'
        assert fmt == '%d.%m.%Y'

    def test_currency_with_symbol(self):
        """Detect currency values with symbols."""
        values = ['$123.45', '$1,234.56', '$99.00']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'currency'
        assert fmt == '$'

    def test_currency_euro(self):
        """Detect euro currency values."""
        values = ['€123.45', '€1,234.56', '€99.00']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'currency'
        assert fmt == '€'

    def test_currency_from_header(self):
        """Detect currency from header hint without symbol in values."""
        values = ['123.45', '-50.00', '1234.56']
        col_type, fmt, obs = _detect_column_type(values, header='Amount ($)')
        assert col_type == 'currency'

    def test_number_plain(self):
        """Detect plain numbers."""
        values = ['100', '200', '300', '400']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'number'

    def test_categorical_low_cardinality(self):
        """Detect categorical column with few distinct values."""
        values = ['BUY', 'SELL', 'BUY', 'DIVIDEND', 'BUY', 'SELL']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'categorical'
        assert '3 distinct values' in obs[0]

    def test_ticker_symbol(self):
        """Detect ticker symbols (short uppercase) with enough samples."""
        # Need enough values where ticker pattern dominates over categorical
        values = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA',
                  'IBM', 'ORCL', 'CSCO', 'INTC', 'AMD', 'QCOM', 'AVGO',
                  'TXN', 'MU', 'AMAT', 'LRCX', 'KLAC', 'ASML']
        col_type, fmt, obs = _detect_column_type(values)
        # With many distinct values, should detect as ticker/symbol
        assert col_type == 'ticker/symbol'

    def test_text_long(self):
        """Detect long text values."""
        values = [
            'This is a very long description that exceeds thirty characters',
            'Another long description with lots of words in it here',
        ]
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'text'
        assert 'Long text values' in obs

    def test_empty_values(self):
        """Handle empty value list."""
        col_type, fmt, obs = _detect_column_type([])
        assert col_type == 'empty'


class TestAnalyzeColumns:
    """Tests for _analyze_columns function."""

    def test_basic_csv(self):
        """Analyze a basic CSV with date, description, amount."""
        csv_content = """Date,Description,Amount
01/15/2025,GROCERY STORE,123.45
01/16/2025,COFFEE SHOP,-5.99
01/17/2025,GAS STATION,45.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            cols = _analyze_columns(tmpfile, has_header=True)
            assert len(cols) == 3

            # Date column
            assert cols[0]['header'] == 'Date'
            assert cols[0]['type'] == 'date'
            assert cols[0]['format'] == '%m/%d/%Y'

            # Description column
            assert cols[1]['header'] == 'Description'
            assert cols[1]['type'] in ('text', 'categorical')

            # Amount column
            assert cols[2]['header'] == 'Amount'
            assert cols[2]['type'] == 'currency'
        finally:
            os.unlink(tmpfile)

    def test_brokerage_csv(self):
        """Analyze a brokerage-style CSV with Symbol, Quantity columns."""
        # Need enough rows for categorical detection (min 5 values)
        csv_content = """Run Date,Action,Symbol,Description,Type,Quantity,Amount ($),Cash Balance ($)
06/03/2025,ROTH CONVERSION,,,Cash,0.000,7000,7002.04
06/06/2025,YOU BOUGHT,FFFFX,FIDELITY FREEDOM,Cash,350.000,-7002.04,0.00
06/10/2025,DIVIDEND,FFFFX,FIDELITY FREEDOM,Cash,0.000,2.04,2.04
06/15/2025,REINVESTMENT,FFFFX,FIDELITY FREEDOM,Cash,0.100,-2.04,0.00
06/20/2025,YOU BOUGHT,FFFFX,FIDELITY FREEDOM,Cash,50.000,-1000.00,-1000.00
06/25/2025,DIVIDEND,FFFFX,FIDELITY FREEDOM,Cash,0.000,5.00,5.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            cols = _analyze_columns(tmpfile, has_header=True)
            assert len(cols) == 8

            # Check Symbol column is detected
            symbol_col = next(c for c in cols if c['header'] == 'Symbol')
            # Could be ticker/symbol or categorical depending on values
            assert symbol_col['type'] in ('ticker/symbol', 'text', 'categorical')

            # Check Amount column
            amount_col = next(c for c in cols if 'Amount' in c['header'])
            assert amount_col['type'] == 'currency'

            # Check Action column is categorical with enough rows
            action_col = next(c for c in cols if c['header'] == 'Action')
            assert action_col['type'] == 'categorical'
            assert action_col['distinct_values'] is not None
        finally:
            os.unlink(tmpfile)

    def test_empty_columns_detected(self):
        """Detect columns that are mostly empty."""
        csv_content = """Date,Description,Notes,Amount
01/15/2025,GROCERY,,123.45
01/16/2025,COFFEE,,5.99
01/17/2025,GAS,,45.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            cols = _analyze_columns(tmpfile, has_header=True)
            notes_col = next(c for c in cols if c['header'] == 'Notes')
            assert notes_col['type'] == 'empty'
            assert notes_col['empty_pct'] == 100
        finally:
            os.unlink(tmpfile)


class TestAnalyzeAmountColumnDetailed:
    """Tests for _analyze_amount_column_detailed function."""

    def test_mixed_positive_negative(self):
        """Analyze amount column with both positive and negative values."""
        csv_content = """Date,Description,Amount
01/15/2025,ROTH CONVERSION,7000
01/16/2025,DIVIDEND RECEIVED,2.04
01/17/2025,YOU BOUGHT FUND,-7002.04
01/18/2025,ACCOUNT FEE,-25.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2, desc_col=1)

            assert result is not None
            assert result['positive_count'] == 2
            assert result['negative_count'] == 2
            assert result['positive_total'] == pytest.approx(7002.04)
            assert result['negative_total'] == pytest.approx(7027.04)

            # Check samples
            assert len(result['sample_positive']) == 2
            assert len(result['sample_negative']) == 2

            # Verify positive samples contain expected transactions
            positive_descs = [d for d, _ in result['sample_positive']]
            assert 'ROTH CONVERSION' in positive_descs
            assert 'DIVIDEND RECEIVED' in positive_descs

            # Verify negative samples
            negative_descs = [d for d, _ in result['sample_negative']]
            assert 'YOU BOUGHT FUND' in negative_descs
        finally:
            os.unlink(tmpfile)

    def test_format_observations_mixed_decimals(self):
        """Detect mixed decimal formatting (integers and decimals)."""
        csv_content = """Date,Description,Amount
01/15/2025,ROTH CONVERSION,7000
01/16/2025,DIVIDEND,2.04
01/17/2025,PURCHASE,-7002.04
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2)

            assert result is not None
            # Should detect that some values have decimals and some don't
            assert any('Mixed' in obs or 'integer' in obs.lower()
                       for obs in result['format_observations'])
        finally:
            os.unlink(tmpfile)

    def test_all_positive(self):
        """Analyze amount column with only positive values."""
        csv_content = """Date,Description,Amount
01/15/2025,EXPENSE 1,100.00
01/16/2025,EXPENSE 2,200.00
01/17/2025,EXPENSE 3,300.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2)

            assert result is not None
            assert result['positive_count'] == 3
            assert result['negative_count'] == 0
            assert len(result['sample_positive']) == 3
            assert len(result['sample_negative']) == 0
        finally:
            os.unlink(tmpfile)

    def test_parentheses_negative(self):
        """Detect parentheses notation for negative amounts."""
        csv_content = """Date,Description,Amount
01/15/2025,EXPENSE,(100.00)
01/16/2025,REFUND,50.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2)

            assert result is not None
            assert result['negative_count'] == 1
            assert result['positive_count'] == 1
            # Should note parentheses notation
            assert any('parentheses' in obs.lower()
                       for obs in result['format_observations'])
        finally:
            os.unlink(tmpfile)

    def test_currency_symbols_detected(self):
        """Detect currency symbols in values."""
        csv_content = """Date,Description,Amount
01/15/2025,EXPENSE,$100.00
01/16/2025,REFUND,$50.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2)

            assert result is not None
            assert any('currency symbol' in obs.lower()
                       for obs in result['format_observations'])
        finally:
            os.unlink(tmpfile)


class TestDetectColumnTypeAdditional:
    """Additional edge case tests for _detect_column_type."""

    def test_date_two_digit_year(self):
        """Detect date with 2-digit year."""
        values = ['01/15/25', '02/28/25', '12/31/25']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'date'
        assert fmt == '%m/%d/%y'

    def test_currency_pound(self):
        """Detect British pound currency."""
        values = ['£123.45', '£1,234.56', '£99.00']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'currency'
        assert fmt == '£'

    def test_currency_yen(self):
        """Detect Japanese yen currency."""
        values = ['¥1000', '¥2500', '¥500']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'currency'
        assert fmt == '¥'

    def test_number_with_thousands_separator(self):
        """Detect numbers with thousands separators."""
        values = ['1,234', '5,678', '9,012']
        col_type, fmt, obs = _detect_column_type(values, header='Quantity')
        # Should be number, not currency (no currency hint in header)
        assert col_type == 'number'

    def test_currency_negative_with_minus(self):
        """Detect currency with minus sign negatives."""
        values = ['-123.45', '456.78', '-789.00']
        col_type, fmt, obs = _detect_column_type(values, header='Amount')
        assert col_type == 'currency'
        assert 'Contains negative values' in obs

    def test_short_text(self):
        """Detect short text that's not categorical."""
        # Many distinct short values - not categorical, not long text
        values = ['abc', 'def', 'ghi', 'jkl', 'mno', 'pqr', 'stu', 'vwx',
                  'yza', 'bcd', 'efg', 'hij', 'klm', 'nop', 'qrs', 'tuv']
        col_type, fmt, obs = _detect_column_type(values)
        assert col_type == 'text'
        assert 'Long text values' not in obs


class TestAnalyzeColumnsAdditional:
    """Additional edge case tests for _analyze_columns."""

    def test_csv_without_headers(self):
        """Analyze CSV that has no header row."""
        csv_content = """01/15/2025,GROCERY STORE,123.45
01/16/2025,COFFEE SHOP,5.99
01/17/2025,GAS STATION,45.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            cols = _analyze_columns(tmpfile, has_header=False)
            # With has_header=False, first row is treated as data
            # Headers will be auto-generated
            assert len(cols) == 3
            # First column should be detected as date
            assert cols[0]['type'] == 'date'
        finally:
            os.unlink(tmpfile)

    def test_partially_empty_column(self):
        """Detect columns that are partially empty."""
        csv_content = """Date,Description,Notes,Amount
01/15/2025,GROCERY,bought milk,123.45
01/16/2025,COFFEE,,5.99
01/17/2025,GAS,filled tank,45.00
01/18/2025,LUNCH,,12.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            cols = _analyze_columns(tmpfile, has_header=True)
            notes_col = next(c for c in cols if c['header'] == 'Notes')
            # 50% empty - should not be classified as 'empty' type
            assert notes_col['type'] != 'empty'
            assert notes_col['empty_pct'] == 50.0
        finally:
            os.unlink(tmpfile)

    def test_quoted_values_with_commas(self):
        """Handle quoted values containing commas."""
        csv_content = """Date,Description,Amount
01/15/2025,"SMITH, JOHN - PAYMENT",123.45
01/16/2025,"ACME, INC.",5.99
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            cols = _analyze_columns(tmpfile, has_header=True)
            assert len(cols) == 3
            # Description should contain the full quoted value
            assert cols[1]['header'] == 'Description'
            assert 'SMITH, JOHN' in cols[1]['sample_values'][0]
        finally:
            os.unlink(tmpfile)

    def test_semicolon_delimiter_with_quotes(self):
        """Handle CSV with semicolon delimiter and quoted headers."""
        csv_content = '''"Extrait";"Date";"Date valeur";"compte";"Description";"Montant";"Devise"
"123";"01/15/2025";"01/15/2025";"BE12345";"GROCERY STORE";"123.45";"EUR"
"124";"01/16/2025";"01/16/2025";"BE12345";"COFFEE SHOP";"-5.99";"EUR"
"125";"01/17/2025";"01/17/2025";"BE12345";"GAS STATION";"45.00";"EUR"
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            # Sniff the dialect like cmd_inspect does
            with open(tmpfile, 'r', encoding='utf-8') as f:
                sample = f.read(4096)
                dialect = csv.Sniffer().sniff(sample)

            # Pass dialect to _analyze_columns
            cols = _analyze_columns(tmpfile, has_header=True, dialect=dialect)
            
            # Should detect 7 columns, not 1
            assert len(cols) == 7
            
            # Check column headers are correctly parsed
            assert cols[0]['header'] == 'Extrait'
            assert cols[1]['header'] == 'Date'
            assert cols[2]['header'] == 'Date valeur'
            assert cols[3]['header'] == 'compte'
            assert cols[4]['header'] == 'Description'
            assert cols[5]['header'] == 'Montant'
            assert cols[6]['header'] == 'Devise'
            
            # Check data is correctly parsed
            assert cols[1]['type'] == 'date'
            assert cols[1]['format'] == '%m/%d/%Y'
        finally:
            os.unlink(tmpfile)


class TestAnalyzeAmountColumnDetailedAdditional:
    """Additional edge case tests for _analyze_amount_column_detailed."""

    def test_all_negative(self):
        """Analyze amount column with only negative values (bank style)."""
        csv_content = """Date,Description,Amount
01/15/2025,CHECKCARD PURCHASE,-32.43
01/16/2025,ATM WITHDRAWAL,-100.00
01/17/2025,BILL PAY,-250.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2)

            assert result is not None
            assert result['positive_count'] == 0
            assert result['negative_count'] == 3
            assert len(result['sample_positive']) == 0
            assert len(result['sample_negative']) == 3
        finally:
            os.unlink(tmpfile)

    def test_zero_amounts_skipped(self):
        """Zero amounts should be skipped in analysis."""
        csv_content = """Date,Description,Amount
01/15/2025,REAL TRANSACTION,100.00
01/16/2025,ZERO BALANCE,0.00
01/17/2025,ANOTHER REAL,-50.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2)

            assert result is not None
            # Zero should not be counted
            assert result['positive_count'] == 1
            assert result['negative_count'] == 1
        finally:
            os.unlink(tmpfile)

    def test_thousands_separators(self):
        """Handle amounts with thousands separators."""
        csv_content = """Date,Description,Amount
01/15/2025,BIG PURCHASE,"1,234.56"
01/16/2025,HUGE PURCHASE,"12,345.67"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2)

            assert result is not None
            assert result['positive_count'] == 2
            assert result['positive_total'] == pytest.approx(13580.23)
        finally:
            os.unlink(tmpfile)

    def test_empty_amount_column(self):
        """Handle case where amount column is empty."""
        csv_content = """Date,Description,Amount
01/15/2025,NO AMOUNT,
01/16/2025,ALSO EMPTY,
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2)
            # Should return None when no valid amounts found
            assert result is None
        finally:
            os.unlink(tmpfile)

    def test_semicolon_delimiter_amount_analysis(self):
        """Analyze amount column with semicolon delimiter."""
        csv_content = '''"Date";"Description";"Montant";"Devise"
"01/15/2025";"GROCERY STORE";"123.45";"EUR"
"01/16/2025";"COFFEE SHOP";"-5.99";"EUR"
"01/17/2025";"GAS STATION";"45.00";"EUR"
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            # Sniff the dialect
            with open(tmpfile, 'r', encoding='utf-8') as f:
                sample = f.read(4096)
                dialect = csv.Sniffer().sniff(sample)

            # Pass dialect to _analyze_amount_column_detailed
            result = _analyze_amount_column_detailed(tmpfile, amount_col=2, dialect=dialect)

            assert result is not None
            assert result['positive_count'] == 2
            assert result['negative_count'] == 1
            assert result['positive_total'] == pytest.approx(168.45)  # 123.45 + 45.00
            assert result['negative_total'] == pytest.approx(5.99)
        finally:
            os.unlink(tmpfile)


class TestDetectFileFormat:
    """Tests for _detect_file_format function."""

    def test_csv_detection(self):
        """Detect standard CSV format."""
        from tally.commands.inspect import _detect_file_format

        csv_content = """Date,Description,Amount
01/15/2025,GROCERY STORE,123.45
01/16/2025,COFFEE SHOP,5.99
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            result = _detect_file_format(tmpfile)
            assert result['format_type'] == 'csv'
            assert result['has_header'] == True
        finally:
            os.unlink(tmpfile)

    def test_fixed_width_detection(self):
        """Detect fixed-width format (like BOA statements)."""
        from tally.commands.inspect import _detect_file_format

        # Simulate BOA fixed-width format
        fixed_content = """Description                                                                      Amount      Balance
Beginning balance as of 01/01/2025                                                            35,233.59
01/03/2025  CHECKCARD 1230 BRITISH PANTRY REDMOND WA                               -32.43     35,201.16
01/03/2025  First Tech Federal Credit Union Transfer                            -3,235.00     31,966.16
01/04/2025  CHECKCARD 0103 AMAZON PRIME                                           -14.99     31,951.17
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(fixed_content)
            tmpfile = f.name

        try:
            result = _detect_file_format(tmpfile)
            assert result['format_type'] == 'fixed_width'
            assert len(result['issues']) > 0
        finally:
            os.unlink(tmpfile)


class TestRothIraScenario:
    """Test the specific Roth IRA scenario from chatlog3."""

    def test_roth_ira_csv_analysis(self):
        """
        Test analyzing a Fidelity Roth IRA CSV similar to chatlog3.

        Key observations that should be surfaced:
        - Positive amounts include ROTH CONVERSION, DIVIDEND
        - Negative amounts include YOU BOUGHT
        - Mixed decimal formatting (7000 vs 7002.04)
        """
        csv_content = """Run Date,Action,Symbol,Description,Type,Price ($),Quantity,Commission ($),Fees ($),Accrued Interest ($),Amount ($),Cash Balance ($),Settlement Date
06/03/2025,"ROTH CONVERSION VS 249-535651-1 (Cash)",,"No Description",Cash,,0.000,,,,7000,7002.04,
06/06/2025,"YOU BOUGHT PROSPECTUS UNDER SEPARATE COVER FIDELITY FREEDOM 2040 (FFFFX) (Cash)",FFFFX,,Cash,20.01,350.000,,,,-7002.04,0.00,06/10/2025
06/10/2025,"DIVIDEND RECEIVED FIDELITY FREEDOM 2040 (FFFFX) (Cash)",FFFFX,,Cash,,,,,0,2.04,2.04,
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            # Test column analysis
            cols = _analyze_columns(tmpfile, has_header=True)

            # Verify we have the expected number of columns
            assert len(cols) == 13

            # Check Amount column
            amount_col = next(c for c in cols if 'Amount' in c['header'])
            assert amount_col['type'] == 'currency'

            # Check Action column shows categorical values
            action_col = next(c for c in cols if c['header'] == 'Action')
            assert action_col['distinct_values'] is not None

            # Test detailed amount analysis (column 10 is Amount)
            result = _analyze_amount_column_detailed(tmpfile, amount_col=10, desc_col=1)

            assert result is not None

            # Should have 2 positive (ROTH CONVERSION, DIVIDEND)
            # and 1 negative (YOU BOUGHT)
            assert result['positive_count'] == 2
            assert result['negative_count'] == 1

            # Verify the samples show the key transactions
            positive_descs = [d for d, _ in result['sample_positive']]
            negative_descs = [d for d, _ in result['sample_negative']]

            assert any('ROTH CONVERSION' in d for d in positive_descs)
            assert any('DIVIDEND' in d for d in positive_descs)
            assert any('YOU BOUGHT' in d for d in negative_descs)

            # Verify format observations include the mixed decimal note
            # (7000 is integer, 7002.04 has decimals)
            assert any('Mixed' in obs for obs in result['format_observations'])

        finally:
            os.unlink(tmpfile)


class TestDetectCurrencySymbol:
    """Tests for currency symbol detection."""

    def test_detect_dollar_symbol(self):
        """Test detecting $ symbol in amount column."""
        csv_content = """Date,Description,Amount
2024-01-01,Coffee,$12.50
2024-01-02,Lunch,$25.00
2024-01-03,Dinner,$45.99
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol == '$'
        finally:
            os.unlink(tmpfile)

    def test_detect_euro_symbol(self):
        """Test detecting € symbol in amount column."""
        csv_content = """Date,Description,Amount
2024-01-01,Coffee,€12.50
2024-01-02,Lunch,€25.00
2024-01-03,Dinner,€45.99
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol == '€'
        finally:
            os.unlink(tmpfile)

    def test_detect_pound_symbol(self):
        """Test detecting £ symbol in amount column."""
        csv_content = """Date,Description,Amount
2024-01-01,Coffee,£12.50
2024-01-02,Lunch,£25.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol == '£'
        finally:
            os.unlink(tmpfile)

    def test_detect_yen_symbol(self):
        """Test detecting ¥ symbol in amount column."""
        csv_content = """Date,Description,Amount
2024-01-01,Coffee,¥1250
2024-01-02,Lunch,¥2500
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol == '¥'
        finally:
            os.unlink(tmpfile)

    def test_no_symbol_returns_none(self):
        """Test that amounts without symbols return None."""
        csv_content = """Date,Description,Amount
2024-01-01,Coffee,12.50
2024-01-02,Lunch,25.00
2024-01-03,Dinner,45.99
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol is None
        finally:
            os.unlink(tmpfile)

    def test_negative_amounts_with_symbol(self):
        """Test detecting symbol with negative amounts."""
        csv_content = """Date,Description,Amount
2024-01-01,Refund,-$12.50
2024-01-02,Return,-$25.00
2024-01-03,Credit,($45.99)
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol == '$'
        finally:
            os.unlink(tmpfile)

    def test_symbol_after_amount(self):
        """Test detecting symbol when it comes after the amount (European style)."""
        csv_content = """Date,Description,Amount
2024-01-01,Coffee,12.50€
2024-01-02,Lunch,25.00€
2024-01-03,Dinner,45.99€
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol == '€'
        finally:
            os.unlink(tmpfile)

    def test_most_common_symbol_wins(self):
        """Test that the most common symbol is returned when multiple are present."""
        csv_content = """Date,Description,Amount
2024-01-01,Coffee,$12.50
2024-01-02,Lunch,$25.00
2024-01-03,Dinner,$45.99
2024-01-04,Euro,€10.00
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol == '$'  # 3 occurrences vs 1 for €
        finally:
            os.unlink(tmpfile)

    def test_currency_code_detection(self):
        """Test detecting currency codes like USD, EUR."""
        csv_content = """Date,Description,Amount
2024-01-01,Coffee,USD 12.50
2024-01-02,Lunch,USD 25.00
2024-01-03,Dinner,USD 45.99
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            symbol = _detect_currency_symbol(tmpfile, amount_col=2, has_header=True)
            assert symbol == 'USD'
        finally:
            os.unlink(tmpfile)


class TestAutoDetectCsvFormat:
    """Tests for auto_detect_csv_format function."""

    def test_semicolon_delimiter_with_quotes(self):
        """Test auto-detection of CSV with semicolon delimiter and quoted headers."""
        from tally.parsers import auto_detect_csv_format

        csv_content = '''"Extract";"Date";"Value Date";"Account";"Description";"Amount";"Currency"
"001";"01/15/2025";"01/15/2025";"BE12345";"GROCERY STORE";"123.45";"EUR"
"002";"01/16/2025";"01/16/2025";"BE12345";"COFFEE SHOP";"-5.99";"EUR"
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            spec = auto_detect_csv_format(tmpfile)

            # Should correctly detect the columns
            assert spec.date_column == 1  # "Date" column
            assert spec.description_column == 4  # "Description" column
            assert spec.amount_column == 5  # "Amount" column
            # Should detect semicolon delimiter
            assert spec.delimiter == ';'
        finally:
            os.unlink(tmpfile)

    def test_standard_comma_delimiter(self):
        """Test auto-detection of standard comma-delimited CSV."""
        from tally.parsers import auto_detect_csv_format

        csv_content = """Date,Description,Amount
01/15/2025,GROCERY STORE,123.45
01/16/2025,COFFEE SHOP,-5.99
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            spec = auto_detect_csv_format(tmpfile)

            assert spec.date_column == 0
            assert spec.description_column == 1
            assert spec.amount_column == 2
            # Comma delimiter returns None (default)
            assert spec.delimiter is None
        finally:
            os.unlink(tmpfile)

    def test_missing_required_column_raises(self):
        """Test that missing required columns raise ValueError."""
        from tally.parsers import auto_detect_csv_format

        # CSV without an amount column
        csv_content = '''"Date";"Description";"Category"
"01/15/2025";"GROCERY";"Food"
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            tmpfile = f.name

        try:
            with pytest.raises(ValueError) as excinfo:
                auto_detect_csv_format(tmpfile)

            # Should mention amount as missing
            assert 'amount' in str(excinfo.value).lower()
            # Should list detected headers correctly (not merged into one)
            assert 'Date' in str(excinfo.value)
            assert 'Description' in str(excinfo.value)
        finally:
            os.unlink(tmpfile)
