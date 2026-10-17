import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from jobwatch.cli import scan, report, main
from jobwatch.feeds import collect_board, Posting, request_json
from jobwatch.state import merge, load_state

FIXTURES = Path(__file__).parent / 'fixtures'
ENTRIES = {
    'greenhouse': {'company': 'Monzo', 'provider': 'greenhouse', 'board': 'monzo'},
    'lever': {'company': 'Palantir', 'provider': 'lever', 'board': 'palantir'},
    'ashby': {'company': 'incident.io', 'provider': 'ashby', 'board': 'incident'},
}


def fixture(kind):
    return json.loads((FIXTURES / (kind + '.json')).read_text())


def posting():
    return Posting('Monzo', 'greenhouse', 'monzo', '1', 'Graduate Software Engineer', 'London',
                   'https://job-boards.greenhouse.io/monzo/jobs/1')


class ParserRegressionTests(unittest.TestCase):
    def test_live_response_shape_fixtures(self):
        for kind, entry in ENTRIES.items():
            with self.subTest(kind=kind):
                rows = collect_board(entry, lambda url: fixture(kind))
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0].raw)

    def test_greenhouse_eu_board_requires_explicit_region(self):
        eu = {'company': 'Riverlane', 'provider': 'greenhouse', 'board': 'riverlane', 'region': 'eu'}
        row = collect_board(eu, lambda _: fixture('greenhouse_eu'))[0]
        self.assertEqual(row.key, 'greenhouse:riverlane:5000685101')
        self.assertTrue(row.url.startswith('https://job-boards.eu.greenhouse.io/riverlane/jobs/'))
        visited = []
        collect_board(eu, lambda url: visited.append(url) or fixture('greenhouse_eu'))
        self.assertEqual(visited, ['https://boards-api.greenhouse.io/v1/boards/riverlane/jobs?content=true'])
        # An EU host on a board not declared EU, or the reverse, fails the board rather than vanishing.
        with self.assertRaises(ValueError):
            collect_board({**eu, 'region': 'global'}, lambda _: fixture('greenhouse_eu'))
        with self.assertRaises(ValueError):
            collect_board({**ENTRIES['greenhouse'], 'region': 'eu'}, lambda _: fixture('greenhouse'))
        data = fixture('greenhouse_eu')
        data['jobs'][0]['absolute_url'] = 'https://job-boards.eu.greenhouse.io/other/jobs/5000685101'
        with self.assertRaises(ValueError):
            collect_board(eu, lambda _: data)
        with self.assertRaises(ValueError):
            collect_board({**eu, 'region': 'us'}, lambda _: fixture('greenhouse_eu'))

    def test_source_date_semantics(self):
        gh = collect_board(ENTRIES['greenhouse'], lambda _: fixture('greenhouse'))[0]
        self.assertEqual(gh.published_at, gh.raw['first_published'])
        self.assertNotEqual(gh.published_at, gh.source_dates['updated_at'])
        lever = collect_board(ENTRIES['lever'], lambda _: fixture('lever'))[0]
        self.assertEqual(lever.published_at, '')
        self.assertIsInstance(lever.source_dates['createdAt'], int)
        ashby = collect_board(ENTRIES['ashby'], lambda _: fixture('ashby'))[0]
        self.assertEqual(ashby.source_dates['publishedAt'], ashby.published_at)

    def test_real_lever_nested_locations(self):
        data = fixture('lever')
        data[0]['categories']['allLocations'].append('Manchester, United Kingdom')
        row = collect_board(ENTRIES['lever'], lambda _: data)[0]
        self.assertIn('Manchester', row.location)
        self.assertEqual(row.country, 'GB')

    def test_ashby_secondary_countries_and_apply_fallback(self):
        data = fixture('ashby')
        j = data['jobs'][0]
        j.pop('jobUrl')
        j['secondaryLocations'] = [{'location': 'New York', 'address': {'addressCountry': 'USA'}}]
        row = collect_board(ENTRIES['ashby'], lambda _: data)[0]
        self.assertEqual(row.external_id, j['id'])
        self.assertEqual(row.countries, ('United Kingdom', 'USA'))

    def test_malformed_records_fail_entire_board(self):
        for kind, entry in ENTRIES.items():
            for bad in (None, 'oops', {}, {'id': None}, {'title': []}):
                with self.subTest(kind=kind, bad=bad):
                    data = fixture(kind)
                    (data if kind == 'lever' else data['jobs']).append(bad)
                    if kind == 'greenhouse': data['meta']['total'] += 1
                    with self.assertRaises(ValueError): collect_board(entry, lambda _: data)

    def test_invalid_url_title_identity_fail_not_disappear(self):
        for kind, entry in ENTRIES.items():
            for url in ('javascript:evil', 'https://jobs.lever.co.evil.test/palantir/id',
                        'https://user:pw@jobs.lever.co/palantir/id', 'https://jobs.lever.co/other/id',
                        'https://jobs.lever.co/palantir/id\nX', 'https://jobs.lever.co:abc/palantir/id'):
                data = fixture(kind)
                j = data[0] if kind == 'lever' else data['jobs'][0]
                j[{'lever': 'hostedUrl', 'greenhouse': 'absolute_url', 'ashby': 'jobUrl'}[kind]] = url
                with self.subTest(kind=kind, url=url), self.assertRaises(ValueError):
                    collect_board(entry, lambda _: data)
        data = fixture('greenhouse'); data['jobs'][0]['title'] = None
        with self.assertRaises(ValueError): collect_board(ENTRIES['greenhouse'], lambda _: data)

    def test_greenhouse_total_and_missing_prospect_field(self):
        data = fixture('greenhouse'); data['meta']['total'] = 2
        with self.assertRaises(ValueError): collect_board(ENTRIES['greenhouse'], lambda _: data)
        data['meta']['total'] = 1; data['jobs'][0].pop('internal_job_id')
        with self.assertRaises(ValueError): collect_board(ENTRIES['greenhouse'], lambda _: data)

    def test_duplicate_ids_and_unknown_ashby_version(self):
        for kind, entry in ENTRIES.items():
            data = fixture(kind); jobs = data if kind == 'lever' else data['jobs']
            jobs.append(copy.deepcopy(jobs[0]))
            if kind == 'greenhouse': data['meta']['total'] = 2
            with self.assertRaises(ValueError): collect_board(entry, lambda _: data)
        data = fixture('ashby'); data['apiVersion'] = '2'
        with self.assertRaises(ValueError): collect_board(ENTRIES['ashby'], lambda _: data)

    def test_full_lever_page_then_short_page_and_delay(self):
        base = fixture('lever')[0]
        pages = []
        for start, end in ((0, 100), (100, 101)):
            batch = []
            for n in range(start, end):
                j = copy.deepcopy(base); j['id'] = str(n); j['hostedUrl'] = 'https://jobs.lever.co/palantir/' + str(n)
                batch.append(j)
            pages.append(batch)
        get, pause = Mock(side_effect=pages), Mock()
        self.assertEqual(len(collect_board(ENTRIES['lever'], get, pause)), 101)
        self.assertIn('skip=100', get.call_args_list[1].args[0])
        pause.assert_called_once_with(1.0)

    def test_repeated_lever_page_is_failure(self):
        base = fixture('lever')[0]; batch = []
        for n in range(100):
            j = dict(base, id=str(n), hostedUrl='https://jobs.lever.co/palantir/' + str(n)); batch.append(j)
        with self.assertRaises(ValueError): collect_board(ENTRIES['lever'], lambda _: batch, lambda _: None)

    def test_nested_schema_drift_and_source_id_conflict_fail_board(self):
        for kind,field,value in [('greenhouse','location',[]),('greenhouse','application_deadline',42),('lever','categories',[]),('ashby','secondaryLocations',['bad']),('ashby','address',{'postalAddress':'bad'}),('ashby','id','different')]:
            data=fixture(kind);j=data[0] if kind=='lever' else data['jobs'][0];j[field]=value
            with self.subTest(kind=kind,field=field),self.assertRaises(ValueError):collect_board(ENTRIES[kind],lambda _:data)

    def test_request_limit_truncation_invalid_json_and_rate_limit(self):
        for raw, headers in ((b'x'*21, {}), (b'[]', {'Content-Length': '100'}), (b'{', {})):
            response = Mock(); response.read.return_value = raw; response.headers = headers; response.geturl.return_value = 'https://example.org/api'
            context = Mock(); context.__enter__ = Mock(return_value=response); context.__exit__ = Mock(return_value=False)
            with patch('jobwatch.feeds.urlopen', return_value=context), patch('jobwatch.feeds.MAX_RESPONSE', 20), self.assertRaises(ValueError):
                request_json('https://example.org/api')
        from urllib.error import HTTPError
        with patch('jobwatch.feeds.urlopen', side_effect=HTTPError('https://example.org', 429, 'Too Many Requests', {}, None)), self.assertRaises(HTTPError):
            request_json('https://example.org/api')


class ReliabilityStateTests(unittest.TestCase):
    def test_screening_change_does_not_mean_absence(self):
        s, _ = merge({'schema': 1, 'postings': {}}, {'greenhouse:monzo': [posting()]}, '2026-10-17T00:00:00Z')
        senior = Posting(**{**posting().__dict__, 'title': 'Senior Software Engineer'})
        s, events = merge(s, {'greenhouse:monzo': [senior]}, '2026-10-18T00:00:00Z')
        row = s['postings'][posting().key]
        self.assertEqual(row['feed_status'], 'listed')
        self.assertEqual(row['priority'], 'excluded')
        self.assertEqual(events[0]['changes']['title']['before'], posting().title)
        self.assertEqual(len(row['history']), 1)

    def test_manual_fields_and_verification_preserved(self):
        s, _ = merge({'schema': 1, 'postings': {}}, {'greenhouse:monzo': [posting()]}, '2026-10-17T00:00:00Z')
        row = s['postings'][posting().key]
        row['owner_note'] = 'private'; row['application_verification'] = {'status': 'open_verified', 'at': '2026-10-17T00:00:00Z'}
        s, _ = merge(s, {'greenhouse:monzo': [posting()]}, '2026-10-18T00:00:00Z')
        self.assertEqual(row['owner_note'], s['postings'][posting().key]['owner_note'])
        self.assertEqual(s['postings'][posting().key]['application_verification']['status'], 'open_verified')
        self.assertNotIn('private', report(s))

    def test_report_escapes_untrusted_fields_and_preserves_scan_failures(self):
        p = Posting(**{**posting().__dict__, 'title': '<script>_[x](bad)\n# boom', 'company': '<img src=x>'})
        s, _ = merge({'schema': 1, 'postings': {}}, {'greenhouse:monzo': [p]}, '2026-10-17T00:00:00Z')
        # excluded title: inject existing row to exercise renderer independently
        s['postings'][p.key] = {**posting().__dict__, 'priority': 'review', 'reason': '<b>reason</b>', 'first_seen': 'old', 'last_seen': 'old', 'feed_status': 'listed', 'title': p.title}
        s['last_scan'] = {'at': '2026-10-17T00:00:00Z', 'failures': ['<script>bad</script>'], 'events': []}
        out = report(s)
        self.assertNotIn('<script>', out); self.assertIn('&lt;script&gt;', out)
        self.assertIn('Failed boards: **1**', out); self.assertIn('2026-10-17T00:00:00Z', out)

    def test_markdown_escaping_does_not_break_apostrophes_or_html_entities(self):
        from jobwatch.render import text
        self.assertEqual(text("We're hiring & <ready>"), "We're hiring &amp; &lt;ready&gt;")
        self.assertEqual(text('"quoted"'), '"quoted"')
        self.assertNotIn('&\\#', text("We're hiring"))

    def test_scan_preserves_ledger_and_detects_concurrent_edits(self):
        with TemporaryDirectory() as d:
            d = Path(d); state=d/'jobs.json'; config=d/'config.json'; output=d/'report.md'; ledger=d/'applications.csv'
            config.write_text(json.dumps({'employers': [dict(ENTRIES['greenhouse'], enabled=True, careers_url='https://monzo.com/careers', validation_url='https://monzo.com/careers', mapping_verified_at='2026-10-17T00:00:00Z')]}))
            state.write_text('{"schema":1,"postings":{}}'); ledger.write_bytes(b'manual,private\r\n')
            def get(entry):
                state.write_text('OWNER EDIT')
                return [posting()]
            with patch('jobwatch.cli.collect_board', side_effect=get): self.assertEqual(scan(config,state,output),2)
            self.assertEqual(state.read_text(),'OWNER EDIT'); self.assertFalse(output.exists()); self.assertEqual(ledger.read_bytes(), b'manual,private\r\n')
            self.assertFalse(state.with_suffix('.json.scan.lock').exists())

    def test_scan_lock_refuses_second_writer(self):
        with TemporaryDirectory() as d:
            d = Path(d); state=d/'jobs.json'; lock=d/'jobs.json.scan.lock'; lock.write_text('')
            self.assertEqual(scan(d/'missing',state,d/'report'),2)
            self.assertTrue(lock.exists())

    def test_total_outage_preserves_last_success_and_observation(self):
        with TemporaryDirectory() as d:
            d=Path(d); state=d/'jobs.json'; config=d/'config.json'; output=d/'report.md'
            config.write_text(json.dumps({'employers': [dict(ENTRIES['greenhouse'], enabled=True, careers_url='https://monzo.com/careers', validation_url='https://monzo.com/careers', mapping_verified_at='2026-10-17T00:00:00Z')]}))
            with patch('jobwatch.cli.collect_board', return_value=[posting()]): self.assertEqual(scan(config,state,output),0)
            before=json.loads(state.read_text())
            with patch('jobwatch.cli.collect_board', side_effect=TimeoutError('timeout')): self.assertEqual(scan(config,state,output),2)
            after=json.loads(state.read_text()); self.assertEqual(before['postings'], after['postings'])
            self.assertEqual(before['boards']['greenhouse:monzo']['last_success'], after['boards']['greenhouse:monzo']['last_success'])
            self.assertIn('board failed', output.read_text())

    def test_invalid_state_refuses_overwrite(self):
        with TemporaryDirectory() as d:
            path=Path(d)/'state'; path.write_text('[]')
            with self.assertRaises(ValueError): load_state(path)
            self.assertEqual(path.read_text(),'[]')
