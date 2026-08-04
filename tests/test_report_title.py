"""Tests for how the report title reaches the generated HTML.

The title is interpolated into the static loading shell as markup and handed to
the Vue app as data, which are two different escaping contexts. These tests pin
down that split, and that a non-string YAML value survives the trip.
"""
import json
import re

import pytest

from tally.report import write_summary_file_vue


MINIMAL_STATS = {
    'num_months': 1,
    'by_merchant': {},
    'income_total': 0,
    'spending_total': 0,
    'credits_total': 0,
    'cash_flow': 0,
    'transfers_in': 0,
    'transfers_out': 0,
    'transfers_net': 0,
    'investment_total': 0,
}


def generate(tmp_path, **kwargs):
    """Generate a report and return its HTML."""
    out = tmp_path / 'report.html'
    write_summary_file_vue(dict(MINIMAL_STATS), str(out), **kwargs)
    generate.last_dir = tmp_path
    return out.read_text(encoding='utf-8')


def embedded_data(html):
    """Pull window.spendingData back out of the last generated report.

    Embedded reports carry it inline; --no-embedded-html writes it to a
    sibling spending_data.js instead.
    """
    source = html
    if 'window.spendingData' not in source:
        source = (generate.last_dir / 'spending_data.js').read_text(encoding='utf-8')
    match = re.search(r'window\.spendingData = (\{.*\});', source)
    assert match, "spendingData assignment not found in report"
    return json.loads(match.group(1))


class TestReportTitle:
    def test_title_is_used_in_shell_and_data(self, tmp_path):
        html = generate(tmp_path, title='2025 Budget')

        assert '<title>2025 Budget</title>' in html
        assert embedded_data(html)['title'] == '2025 Budget'

    def test_default_title_is_the_same_in_shell_and_data(self, tmp_path):
        """The shell and the mounted app must not disagree about the name.

        Previously the shell fell back to 'Tally Spending Analysis' while the
        data carried None, so Vue mounted and renamed the report.
        """
        html = generate(tmp_path)

        assert '<title>Tally Spending Analysis</title>' in html
        assert embedded_data(html)['title'] == 'Tally Spending Analysis'

    @pytest.mark.parametrize('embedded_html', [True, False])
    def test_markup_in_title_is_escaped_in_the_shell(self, tmp_path, embedded_html):
        """A title carrying markup must not become markup in the report."""
        html = generate(
            tmp_path,
            title='</title><script>alert(1)</script>',
            embedded_html=embedded_html,
        )

        # Neither the shell (escaped as markup) nor the embedded data script
        # (where '</' would close the <script> element) may emit it verbatim.
        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
        assert embedded_data(html)['title'] == '</title><script>alert(1)</script>'

    def test_non_string_title_is_coerced(self, tmp_path):
        """`title: 2025` in YAML arrives as an int and must not raise."""
        html = generate(tmp_path, title=2025)

        assert '<title>2025</title>' in html
        assert embedded_data(html)['title'] == '2025'

    def test_empty_title_falls_back_to_default(self, tmp_path):
        html = generate(tmp_path, title='')

        assert '<title>Tally Spending Analysis</title>' in html
        assert embedded_data(html)['title'] == 'Tally Spending Analysis'
