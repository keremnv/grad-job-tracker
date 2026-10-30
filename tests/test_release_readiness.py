"""Local release/recovery checks; no project API, source data or credentials."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'infra/aws/scripts'))
import backup_freshness
import build_release


class BackupFreshnessTests(unittest.TestCase):
    def fixture(self, directory, hours=1):
        path = Path(directory)/'synthetic.dump'
        path.write_bytes(b'PGDMP synthetic fixture, not a real archive')
        digest = sha256(path.read_bytes()).hexdigest()
        now = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)
        start = now - timedelta(hours=hours)
        return path, {'schema': 'jobwatch.aws-backup/2',
                      'backup_started_at': start.isoformat(),
                      'created_at': (start + timedelta(minutes=30)).isoformat(),
                      'archive_bytes': path.stat().st_size, 'archive_sha256': digest,
                      'archive_object': {'key': 'backups/synthetic.dump', 'version_id': 'synthetic-version',
                                         'sha256': digest}}, now

    def test_snapshot_age_boundaries_are_advisory_not_an_rpo(self):
        with TemporaryDirectory() as d:
            for hours, status in [(1, 'fresh'), (12, 'fresh'), (13.99, 'fresh'), (14, 'warning'),
                                  (25.99, 'warning'), (26, 'critical')]:
                with self.subTest(hours=hours):
                    archive, manifest, now = self.fixture(d, hours)
                    result = backup_freshness.inspect(archive, manifest, now=now)
                    self.assertEqual(result['status'], status)
                    self.assertGreaterEqual(result['conservative_snapshot_age_seconds'], hours*3600)
                    self.assertFalse(result['s3_availability_verified'])
                    self.assertFalse(result['maximum_data_loss_guaranteed'])
                    self.assertNotIn('maximum_age_seconds', result)
                    self.assertFalse(result['alert_delivery_verified'])
                    self.assertFalse(result['production_ready'])

    def test_delayed_upload_or_fresh_manifest_never_refreshes_old_snapshot(self):
        with TemporaryDirectory() as d:
            archive, manifest, now = self.fixture(d, 27)
            manifest['created_at'] = (now-timedelta(minutes=1)).isoformat()
            self.assertEqual(backup_freshness.inspect(archive, manifest, now=now)['status'], 'critical')

    def test_missing_start_or_completion_version_is_not_a_success(self):
        with TemporaryDirectory() as d:
            archive, original, now = self.fixture(d)
            for mutate in [lambda m: m.pop('backup_started_at'), lambda m: m.pop('archive_object'),
                           lambda m: m['archive_object'].update(version_id='null'),
                           lambda m: m.update(schema='jobwatch.aws-backup/1')]:
                manifest = json.loads(json.dumps(original));mutate(manifest)
                with self.assertRaises(ValueError):
                    backup_freshness.inspect(archive, manifest, now=now)

    def test_corruption_wrong_archive_link_size_and_symlink_rejected(self):
        with TemporaryDirectory() as d:
            archive, original, now = self.fixture(d)
            for change in [('archive_sha256', '0'*64), ('archive_bytes', True)]:
                manifest = dict(original);manifest[change[0]] = change[1]
                with self.assertRaises(ValueError): backup_freshness.inspect(archive, manifest, now=now)
            wrong = json.loads(json.dumps(original));wrong['archive_object']['sha256'] = '0'*64
            with self.assertRaises(ValueError): backup_freshness.inspect(archive, wrong, now=now)
            link = Path(d)/'link.dump';link.symlink_to(archive)
            with self.assertRaises(ValueError): backup_freshness.inspect(link, original, now=now)
            archive.write_bytes(b'X'+archive.read_bytes()[1:])
            with self.assertRaises(ValueError): backup_freshness.inspect(archive, original, now=now)
            # Recomputed hashes still cannot turn a non-custom archive into a backup.
            wrong_format = json.loads(json.dumps(original))
            wrong_format['archive_sha256'] = sha256(archive.read_bytes()).hexdigest()
            wrong_format['archive_object']['sha256'] = wrong_format['archive_sha256']
            with self.assertRaisesRegex(ValueError, 'archive_format_invalid'):
                backup_freshness.inspect(archive, wrong_format, now=now)

    def test_future_naive_and_reversed_timestamps_rejected(self):
        with TemporaryDirectory() as d:
            archive, original, now = self.fixture(d)
            for key, value in [('backup_started_at', now.isoformat()),
                               ('created_at', (now+timedelta(seconds=1)).isoformat()),
                               ('backup_started_at', '2026-10-25T18:00:00')]:
                manifest = dict(original);manifest[key] = value
                with self.assertRaises(ValueError): backup_freshness.inspect(archive, manifest, now=now)

    def test_cli_errors_are_nonzero_and_do_not_disclose_input(self):
        with TemporaryDirectory() as d:
            manifest = Path(d)/'private-marker.json';manifest.write_text('{private source or secret')
            output = io.StringIO()
            with redirect_stdout(output):
                code = backup_freshness.main(['--archive', str(Path(d)/'absent'), '--manifest', str(manifest)])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'invalid')
            self.assertNotIn('private', output.getvalue())


class ReleasePreparationTests(unittest.TestCase):
    def test_actual_git_archive_whitelist_contains_v1_sql_and_no_private_inputs(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            def git(*args):
                return subprocess.run(['git', '-C', d, *args], check=True, capture_output=True)
            git('init', '-q')
            files = ['src/jobwatch/postgres/migrations/0004_embeddings.sql', 'pyproject.toml',
                     'infra/aws/scripts/deploy_release.py', 'infra/aws/systemd/jobwatch-backup.timer',
                     'sql/postgresql/bootstrap.sql', 'sql/postgresql/vector_extension.sql',
                     'sql/postgresql/embedding_access.sql', 'sql/postgresql/chat_readonly.sql',
                     'sql/postgresql/owner_workflows.sql','sql/postgresql/agent_roles.sql',
                     'sql/postgresql/agent_access.sql','sql/postgresql/calendar_deadlines.sql',
                     'sql/postgresql/lead_deadline_access.sql','sql/postgresql/posting_deadline_access.sql',
                     'infra/aws/integrations/systemd/jobwatch-agent-read.service']
            for name in [*files, 'data/applications.csv', 'config/employers.json', '.env', 'reports/latest.md']:
                p = repo/name;p.parent.mkdir(parents=True, exist_ok=True);p.write_text('synthetic canary\n')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Synthetic only')
            raw = git('archive', 'HEAD', *build_release.PATHS).stdout
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                names = {m.name for m in archive if m.isfile()}
            self.assertEqual(names, set(files))

    def test_prepared_twelve_hour_schedule_avoids_random_delay_and_is_not_enabled_by_installer(self):
        timer = (ROOT/'infra/aws/systemd/jobwatch-backup.timer').read_text()
        self.assertIn('OnCalendar=*-*-* 00,12:00:00 UTC', timer)
        self.assertIn('RandomizedDelaySec=0', timer)
        self.assertIn('AccuracySec=1min', timer)
        # Existing deployment tests exercise successful/repeated installs and
        # refuse enabled timers. This change must not add any activation path.
        deploy = (ROOT/'infra/aws/scripts/deploy_release.py').read_text()
        self.assertNotIn("['systemctl', 'enable'", deploy)
        self.assertNotIn("['systemctl', 'start'", deploy)


if __name__ == '__main__':
    unittest.main()
