import json
import re

from tally.report import write_summary_file_vue


def extract_spending_data(report_path):
    html = report_path.read_text(encoding='utf-8')
    match = re.search(r'window\.spendingData = (\{.*?\});', html, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def merchant_data(name, category, subcategory, amount):
    return {
        'name': name,
        'category': category,
        'subcategory': subcategory,
        'transactions': [
            {
                'date': '2025-01-15',
                'month': '2025-01',
                'description': name,
                'amount': amount,
                'source': 'Test',
                'tags': [],
            }
        ],
        'total': amount,
        'avg_when_active': amount,
        'count': 1,
        'months_active': 1,
        'is_consistent': True,
        'tags': set(),
    }


def collect_category_merchants(category_view):
    merchants = {}
    for category in category_view.values():
        for subcategory in category['subcategories'].values():
            merchants.update(subcategory['merchants'])
    return merchants


def test_report_uses_stable_unambiguous_merchant_ids(tmp_path):
    first = merchant_data('Acme_A', 'B', 'C', 25)
    second = merchant_data('Acme', 'A_B', 'C', 40)
    stats = {
        'num_months': 1,
        'by_merchant': {
            'first': first,
            'second': second,
        },
        'sections': {
            'Ambiguous IDs': {
                'merchants': [
                    ('Acme_A', first),
                    ('Acme', second),
                ]
            }
        },
    }

    first_report = tmp_path / 'report-first.html'
    second_report = tmp_path / 'report-second.html'

    write_summary_file_vue(stats, first_report)
    write_summary_file_vue(stats, second_report)

    first_data = extract_spending_data(first_report)
    second_data = extract_spending_data(second_report)

    section_key = 'ambiguous_ids'
    first_section_merchants = first_data['sections'][section_key]['merchants']
    second_section_merchants = second_data['sections'][section_key]['merchants']

    assert len(first_section_merchants) == 2
    assert set(first_section_merchants) == set(second_section_merchants)

    first_category_merchants = collect_category_merchants(first_data['categoryView'])
    second_category_merchants = collect_category_merchants(second_data['categoryView'])

    assert len(first_category_merchants) == 2
    assert set(first_category_merchants) == set(second_category_merchants)
    assert set(first_section_merchants) == set(first_category_merchants)
