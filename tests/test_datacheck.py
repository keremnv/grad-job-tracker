import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobwatch.cli import main
from jobwatch.feeds import Posting
from jobwatch.state import merge, save_state, text_dir

NOW = '2026-10-22T12:00:00Z'


class OperationalDataTests(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory(); self.root = Path(self.dir.name)
        data = self.root / 'data'; data.mkdir()
        self.state = data / 'jobs.json'
        p = Posting('E', 'lever', 'e', '1', 'Graduate Software Engineer', 'London, United Kingdom', 'https://jobs.lever.co/e/1',
                    description='Build software.', country='GB', countries=('GB',))
        s, _ = merge({'schema': 2, 'postings': {}}, {'lever:e': [p]}, NOW)
        save_state(self.state, s, NOW)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'employers': [{'company': 'E', 'provider': 'lever', 'board': 'e', 'enabled': True,
            'careers_url': 'https://e.example', 'validation_url': 'https://e.example', 'mapping_verified_at': NOW,
            'verification_notes': 'synthetic'}]}))
        (data / 'verifications.json').write_text('{"schema": 1, "postings": {}}')
        (data / 'leads.json').write_text('{"schema": 1, "vacancies": [], "programmes": []}')
        (data / 'role_sample.json').write_text('{"families": [], "postings": []}')
        self.data = data

    def tearDown(self):
        self.dir.cleanup()

    def run_check(self):
        d = self.data
        argv = ['validate-data', '--json', '--state', str(self.state), '--config', str(self.config),
                '--verifications', str(d / 'verifications.json'), '--leads', str(d / 'leads.json'),
                '--applications', str(d / 'applications.csv'), '--triage-file', str(d / 'triage.json'),
                '--role-sample', str(d / 'role_sample.json'), '--as-of', NOW]
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            code = main(argv)
        return code, json.loads(out.getvalue())

    def test_clean_data_passes(self):
        code, r = self.run_check()
        self.assertEqual((code, r['ok'], r['warnings']), (0, True, []))
        self.assertEqual(r['data']['files']['state']['schema_on_disk'], 2)
        self.assertEqual(r['data']['files']['triage']['status'], 'missing')

    def test_problems_are_reported_with_partial_results(self):
        (self.data / 'triage.json').write_text(json.dumps({'schema': 1, 'decisions': {'lever:e:gone': {
            'decision': 'dismissed', 'reason_code': None, 'note': None, 'revisit_on': None, 'decided_at': NOW, 'recorded_by': 'owner'}}}))
        (self.data / 'verifications.json').write_text(json.dumps({'schema': 1, 'postings': {'lever:e:1': {
            'status': 'unknown', 'description_sha256': '0' * 64,
            'deadlines': [{'value': '2026-10-29', 'wording': 'by 29 Oct', 'url': 'https://jobs.lever.co/e/1', 'verified_at': NOW},
                          {'value': '2026-10-30', 'wording': 'by 30 Oct', 'url': 'https://jobs.lever.co/e/1', 'verified_at': NOW}]}}}))
        code, r = self.run_check()
        self.assertEqual(code, 0)
        codes = sorted(w['code'] for w in r['warnings'])
        self.assertEqual(codes, ['calendar_evidence_problem', 'orphan_owner_record', 'stale_description_check'])
        next(text_dir(self.state).glob('*/*.txt')).write_text('tampered')
        code, r = self.run_check()
        self.assertEqual((code, r['ok']), (2, False))
        self.assertEqual(r['errors'][0]['file'], 'state')
        self.assertEqual(r['data']['files']['config']['status'], 'ok')  # partial report retained


if __name__ == '__main__':
    unittest.main()
