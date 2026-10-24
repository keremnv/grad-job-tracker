"""Domain presentation/security regressions; no database, operational files or live requests."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobwatch.postgres import research,reports
from jobwatch.postgres.operations import fixture_transport
from jobwatch.postgres.orchestrator import actions_summary,collect_monitored
from jobwatch.search import tokens


class OperationalPresentationTests(unittest.TestCase):
    def claim(self,**values):
        return {'identity':'greenhouse:test:1','posting_key':'greenhouse:test:1','company':'Fixture','title':'Graduate Engineer',
            'posting_url':'https://example.org/job','url':'https://example.org/deadline','wording':'Apply by 1 December',
            'value':'2026-12-01','checked_at':'2026-10-01T12:00:00Z','checked_by':'agent',
            'provenance':'independent_verification','status':'listed','action_state':'unreviewed',
            'structured':True,'ambiguous':False,'date_conflict':False,**values}

    def test_bm25_c_family_and_useful_late_snippet(self):
        self.assertEqual(tokens('C++ C# C'),['c++','c#','c'])
        body='Boilerplate words. '*100+'Build C++ systems with Linux. '+'Other text. '*100
        snippet=research.snippet(body,'C++')
        self.assertIn('C++',snippet);self.assertTrue(snippet.startswith('…'));self.assertLessEqual(len(snippet),242)

    def test_credential_urls_are_omitted_without_collapsing_fragments(self):
        self.assertIsNone(research.url('https://example.org/jobs?access_token=never-log'))
        self.assertIsNone(research.url('https://user:password@example.org/jobs'))
        self.assertEqual(research.url('https://example.org/jobs#one'),'https://example.org/jobs#one')
        self.assertNotEqual(research.url('https://example.org/jobs#one'),research.url('https://example.org/jobs#two'))

    def test_calendar_equivalent_timestamps_deduplicate_and_prose_never_becomes_date(self):
        claims=[self.claim(value='2026-12-01T12:00:00+01:00'),self.claim(value='2026-12-01T11:00:00Z'),
                self.claim(identity='unknown',value=None,wording='Apply next Friday')]
        data={'generated_at':'2026-10-24T12:00:00Z','deadlines':claims,'applications':[]}
        before=deepcopy(data)
        blob,warnings=reports.calendar_export(data)
        self.assertEqual(blob.count(b'\r\nBEGIN:VEVENT\r\n'),1)
        self.assertIn(b'DTSTART:20261201T110000Z',blob);self.assertNotIn(b'VALUE=DATE',blob)
        self.assertEqual(warnings,[]);self.assertEqual(data,before)

    def test_calendar_untrusted_text_cannot_create_components_or_leak_secret_urls(self):
        data={'generated_at':'2026-10-24T12:00:00Z','applications':[],
              'deadlines':[self.claim(wording='Deadline\nBEGIN:VEVENT\nATTENDEE:mallory@example.org')]}
        blob,warnings=reports.calendar_export(data)
        self.assertEqual(blob.count(b'\r\nBEGIN:VEVENT\r\n'),1)
        self.assertNotIn(b'\r\nATTENDEE:',blob)
        data['deadlines']=[self.claim(url='https://example.org/?token=never-log')]
        blob,warnings=reports.calendar_export(data)
        self.assertNotIn(b'never-log',blob);self.assertTrue(warnings)

    def test_public_actions_summary_ignores_private_extra_fields_and_escapes_source_labels(self):
        result={'selected_sources':1,'counts':{'failed':1},'absence_safe':0,'positive_only':0,'warning':None,
            'sources':[{'source':'<script>|bad\x1b[31m','status':'failed','coverage':'no_publication',
                'error':'password=secret','private_note':'private owner text'}]}
        output=actions_summary(result)
        for forbidden in ('<script>','\x1b','password=secret','private owner text'):
            self.assertNotIn(forbidden,output)
        self.assertIn('unknown',output)

    def test_fixture_transport_real_provider_shapes_and_offset_pages(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'feeds.json'
            data={'sources':{'greenhouse:test':{'jobs':[],'meta':{'total':0}},'lever:test':list(range(101)),
                             'ashby:test':{'jobs':[],'apiVersion':'1'}}}
            path.write_text(json.dumps(data));get=fixture_transport(path)
            self.assertEqual(get('https://boards-api.greenhouse.io/v1/boards/test/jobs?content=true')['meta']['total'],0)
            self.assertEqual(get('https://api.lever.co/v0/postings/test?skip=100&limit=100'),[100])
            self.assertEqual(get('https://api.ashbyhq.com/posting-api/job-board/test?includeCompensation=true')['apiVersion'],'1')

    def test_invalid_bounds_fail_before_accessing_a_database(self):
        with self.assertRaises(ValueError):research.search(None,'Python',limit=0)
        with self.assertRaises(ValueError):research.search(None,'x'*2001)
        with self.assertRaises(ValueError):collect_monitored(None,max_sources=0)
