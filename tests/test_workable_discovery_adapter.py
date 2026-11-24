"""Offline unit tests for the isolated Workable search discovery adapter."""
from __future__ import annotations

from datetime import datetime, timezone
import unittest

from jobwatch.discovery import WorkableSearchAdapter


def _search_job(jid: str, title: str = 'Data Science & AI Graduate', company: str = 'Stalis',
                slug: str = 'stalis', created: str = '2026-11-23T15:53:39Z'):
    return {
        'id': jid,
        'title': title,
        'department': 'Engineering',
        'employmentType': 'Full-time',
        'workplace': 'remote',
        'locations': ['TELECOMMUTE', 'United Kingdom'],
        'location': {'city': '', 'subregion': '', 'countryName': 'United Kingdom'},
        'created': created,
        'updated': created,
        'url': f'https://jobs.workable.com/view/abc/{jid}',
        'description': '<p>Graduate Python &amp; AI role in the UK.</p>',
        'requirementsSection': '<p>Computer Science degree.</p>',
        'benefitsSection': '',
        'company': {
            'id': 'comp-1',
            'title': company,
            'website': f'https://{slug}.example',
            'url': f'https://jobs.workable.com/company/xyz/jobs-at-{slug}',
        },
    }


def _multi_country_widget():
    base = {
        'title': 'Data Science & AI Graduate',
        'shortcode': '1C9CCEA6B4',
        'employment_type': 'Full-time',
        'telecommuting': True,
        'department': 'Engineering',
        'url': 'https://apply.workable.com/j/1C9CCEA6B4',
        'shortlink': 'https://apply.workable.com/j/1C9CCEA6B4',
        'application_url': 'https://apply.workable.com/j/1C9CCEA6B4/apply',
        'published_on': '2026-11-23',
        'created_at': '2026-11-18',
        'description': '<p>Graduate Python &amp; AI role in the UK.</p>',
    }
    return {
        'name': 'Stalis',
        'jobs': [
            {**base, 'country': 'Ireland', 'locations': [{'country': 'Ireland', 'countryCode': 'IE'}]},
            {**base, 'country': 'United Kingdom', 'locations': [{'country': 'United Kingdom', 'countryCode': 'GB'}]},
        ],
    }


class WorkableSearchAdapterTests(unittest.TestCase):
    def test_cross_query_and_canonical_shortcode_deduplication(self):
        # Two distinct search UUIDs (e.g. location fan-out) resolve to one canonical shortcode.
        def fake_get(url: str):
            if 'jobs.workable.com/api/v1/jobs' in url:
                return 200, {'jobs': [_search_job('uuid-1'), _search_job('uuid-2')], 'nextPageToken': None}, 1024
            if 'apply.workable.com/api/v1/widget/accounts/stalis' in url:
                return 200, _multi_country_widget(), 2048
            return 404, {}, 0

        adapter = WorkableSearchAdapter(
            queries=({'query': 'graduate ai', 'location': 'United Kingdom', 'max_pages': 1},
                     {'query': 'graduate data science', 'location': 'United Kingdom', 'max_pages': 1}),
            max_search_requests=5,
            max_widget_requests=5,
            search_delay_s=0,
            widget_delay_s=0,
            get_json=fake_get,
            sleep_fn=lambda _: None,
            clock=lambda: datetime(2026, 11, 24, 21, 0, tzinfo=timezone.utc),
        )
        report = adapter.discover()
        self.assertFalse(report.rate_limited)
        self.assertEqual(report.search_requests, 2)
        self.assertEqual(report.widget_requests, 1)  # cached across both UUIDs
        self.assertEqual(len(report.vacancies), 1)
        v = report.vacancies[0]
        self.assertEqual(v.candidate_key, 'workable:stalis:1C9CCEA6B4')
        self.assertEqual((v.board, v.native_id), ('stalis', '1C9CCEA6B4'))
        self.assertFalse(v.authoritative_board_observation)
        self.assertIn('United Kingdom', v.countries)
        self.assertIn('Ireland', v.countries)

    def test_halts_immediately_on_429_and_reports_failure(self):
        calls = []

        def fake_get(url: str):
            calls.append(url)
            return 429, {}, 0

        adapter = WorkableSearchAdapter(
            queries=({'query': 'q1', 'location': 'United Kingdom', 'max_pages': 2},
                     {'query': 'q2', 'location': 'United Kingdom', 'max_pages': 2}),
            search_delay_s=0,
            widget_delay_s=0,
            get_json=fake_get,
            sleep_fn=lambda _: None,
        )
        report = adapter.discover()
        self.assertTrue(report.rate_limited)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report.errors[0]['status'], 429)

    def test_rejects_unbounded_request_budgets(self):
        with self.assertRaises(ValueError):
            WorkableSearchAdapter(max_search_requests=500)
        with self.assertRaises(ValueError):
            WorkableSearchAdapter(max_widget_requests=500)


if __name__ == '__main__':
    unittest.main()
