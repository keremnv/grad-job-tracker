"""Execute the user guide's actual SQL as owner in the verified disposable target."""
from datetime import date
import os
from pathlib import Path
import re
import subprocess

from support import DatabaseTest

GUIDE = (Path(__file__).resolve().parents[2] / 'docs/USER_GUIDE.md').read_text()


class UserGuideTests(DatabaseTest):
    def example(self, name, **values):
        block = GUIDE.split('<!-- BEGIN '+name+' -->', 1)[1].split('<!-- END '+name+' -->', 1)[0]
        sql = re.fullmatch(r'\s*```sql\n(.*?)\n```\s*', block, re.S).group(1)
        # Only interactive input is substituted. The documented SQL is unchanged.
        sql = re.sub(r'^\\prompt .*$', '', sql, flags=re.M)
        args = ['docker', 'exec', '-i', os.environ['JOBWATCH_PG_TEST_CONTAINER'],
                'psql', '-X', '-w', '-q', '-v', 'ON_ERROR_STOP=1',
                '-U', 'jobwatch_owner', '-d', 'jobwatch_foundation_test']
        for key, value in values.items():
            args.extend(['-v', key+'='+str(value)])
        return subprocess.run(args+['-f', '-'], input=sql, capture_output=True, text=True)

    def test_research_examples_include_exclusions_and_retained_evidence(self):
        key, _, _ = self.seed()
        with self.target.connect() as conn:
            conn.execute("UPDATE jobwatch.postings SET verdict='excluded' WHERE posting_key=%s", (key,))
        before = self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        for name in ('browse', 'evidence', 'actions'):
            result = self.example(name, posting_key=key)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('excluded', self.example('browse').stdout)
        self.assertIn('parsed_record_json', self.example('evidence', posting_key=key).stdout)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'), before)

    def test_bookmark_apply_replay_and_stale_refusal_preserve_evidence(self):
        key, _, _ = self.seed()
        versions = self.scalar('SELECT count(*) FROM jobwatch.posting_versions')
        for _ in range(2):
            result = self.example('bookmark', posting_key=key)
            self.assertEqual(result.returncode, 0, result.stderr)
        audit = self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.applications'), 1)
        values = dict(job_key=key, previous_stage='bookmarked', previous_date='', applied_date=date.today())
        for _ in range(2):
            result = self.example('applied', **values)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'), audit+1)
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            conn.execute("SET LOCAL jobwatch.actor='owner:guide-test'")
            conn.execute("UPDATE jobwatch.applications SET stage='interview' WHERE job_key=%s", (key,))
        self.assertEqual(self.example('applied', **values).returncode, 3)
        self.assertEqual(self.scalar('SELECT stage FROM jobwatch.applications'), 'interview')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), versions)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'), 0)

    def test_unknown_bookmark_fails_without_owner_record_or_audit(self):
        count = self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        self.assertEqual(self.example('bookmark', posting_key='greenhouse:test:missing').returncode, 3)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.applications'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'), count)
