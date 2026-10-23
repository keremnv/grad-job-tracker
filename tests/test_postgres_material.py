"""Pure provider comparison / v1 parity; does not require psycopg or a database."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from jobwatch import feeds, filtering, state
from jobwatch.postgres.collection import Processing, prepare, normalize_v1
from jobwatch.postgres.material import compare, words

FIXTURES = Path(__file__).parent / 'fixtures'
PAIRS = Path(__file__).parent / 'postgres/fixtures/source_pairs.json'


class MaterialTests(unittest.TestCase):
    def test_actual_incident_and_cambridge_pairs(self):
        pairs = json.loads(PAIRS.read_text())
        for pair in pairs['ashby']:
            self.assertEqual(compare('ashby', pair['before'], pair['after'], 'v1', 'v1'), ({}, {}))
        pair = pairs['greenhouse']
        changed, baseline = compare('greenhouse', pair['before'], pair['after'], 'v1', 'v1')
        self.assertEqual(set(changed), {'metadata'})
        self.assertEqual(baseline, {})

    def test_missing_null_empty_and_request_coverage(self):
        field = 'workplaceType'
        self.assertEqual(set(compare('ashby', {}, {field: None}, 'v1', 'v1')[1]), {field})
        self.assertEqual(set(compare('ashby', {field: None}, {field: ''}, 'v1', 'v1')[0]), {field})
        self.assertEqual(set(compare('ashby', {field: ''}, {}, 'v1', 'v1')[0]), {field})
        self.assertEqual(set(compare('ashby', {field: ''}, {}, 'v1', 'v2')[1]), {field})
        self.assertIn('isRemote', compare('ashby', {'isRemote': 1}, {'isRemote': True}, 'v1', 'v1')[0])

    def test_set_like_arrays_and_ordered_sections(self):
        raw = {'offices': [{'id': 2, 'name': 'B'}, {'id': 1, 'name': 'A'}]}
        other = {'offices': list(reversed(raw['offices']))}
        self.assertEqual(compare('greenhouse', raw, other, 'v1', 'v1'), ({}, {}))
        raw = {'lists': [{'text': 'Requirements', 'content': '<p>Python</p>'}, {'text': 'Benefits', 'content': '<p>Health</p>'}]}
        self.assertIn('lists', compare('lever', raw, {'lists': list(reversed(raw['lists']))}, 'v1', 'v1')[0])

    def test_changed_description_and_new_evidence_in_one_comparison(self):
        changed, baseline = compare('greenhouse', {'content': '<p>Old words</p>'},
            {'content': '<p>New words</p>', 'metadata': [{'name': 'Job Summary', 'value': 'New field'}]}, 'old-request', 'new-request')
        self.assertEqual(set(changed), {'description'})
        self.assertEqual(set(baseline), {'metadata'})

    def test_html_formatting_and_plain_code_wording(self):
        self.assertEqual(words('&lt;p&gt;Use &lt;strong&gt;Py&lt;/strong&gt;thon&amp;nbsp; now&lt;/p&gt;'), 'Use Python now')
        self.assertEqual(compare('greenhouse', {'content': '<p>Build Python</p>'},
            {'content': '<p style="margin-top:0px">Build Python</p><p>&nbsp;</p>'}, 'v1', 'v1'), ({}, {}))
        self.assertIn('description', compare('ashby', {'descriptionPlain': 'Use <vector>'}, {'descriptionPlain': 'Use <map>'}, 'v1', 'v1')[0])

    def test_volatile_dates_and_complete_lever_compensation_policy(self):
        self.assertEqual(compare('greenhouse', {'updated_at': 'a'}, {'updated_at': 'b'}, 'v1', 'v1'), ({}, {}))
        self.assertEqual(compare('ashby', {'publishedAt': 'a'}, {'publishedAt': 'b'}, 'v1', 'v1'), ({}, {}))
        old = {'salaryRange': {'min': 10}, 'categories': {'allLocations': ['London', 'Bristol']},
               'lists': [{'text': 'Requirements', 'content': '<p>Code</p>'}]}
        new = deepcopy(old); new['salaryRange']['min'] = 20
        new['categories']['allLocations'].reverse()
        self.assertEqual(set(compare('lever', old, new, 'v1', 'v1')[0]), {'salaryRange'})

    def test_v1_adapter_screening_and_deadline_parity(self):
        at = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        entries = [('greenhouse', 'monzo', 'Monzo'), ('lever', 'palantir', 'Palantir'), ('ashby', 'incident', 'incident.io')]
        for provider, board, company in entries:
            response = json.loads((FIXTURES / (provider + '.json')).read_text())
            if provider == 'greenhouse':
                response['jobs'][0].update(content='&lt;p&gt;Build Python. Apply by 1 November.&lt;/p&gt;', application_deadline='2026-11-01')
            posts = feeds.collect_board({'provider': provider, 'board': board, 'company': company}, get_json=lambda _: response)
            for p in posts:
                with self.subTest(key=p.key):
                    record = prepare(p, at, Processing())
                    n = json.loads(record.normalized)
                    legacy = state._source(p, state.text_hash(p.description), '2026-10-23T12:00:00Z')
                    for field, oldfield in (('company','company'),('title','title'),('posting_url','url'),('location_text','location'),
                            ('description_text',None),('country','country'),('countries','countries'),('department','department'),
                            ('team','team'),('employment_type','employment_type'),('salary','salary'),('source_dates','source_dates'),
                            ('published_at','published_at'),('deadlines','deadlines')):
                        self.assertEqual(n[field], p.description if oldfield is None else legacy[oldfield])
                    result = filtering.screen(filtering.inputs_from_posting(p))
                    result.pop('explanation')
                    self.assertEqual(json.loads(record.screening), result)
                    self.assertEqual(n, normalize_v1(p, '2026-10-23T12:00:00Z'))
