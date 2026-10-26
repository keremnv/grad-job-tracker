"""Completed-backup health and missing-heartbeat preparation; no AWS calls."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'infra/aws/scripts'))
import backup_monitor as monitor
import database_backup as backup
import run_operation


class BackupMonitorTests(unittest.TestCase):
    def fixture(self, directory):
        archive = Path(directory)/'fixture.dump'
        archive.write_bytes(b'PGDMP synthetic archive')
        checksum = sha256(archive.read_bytes()).hexdigest()
        now = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)
        obj = {'key': 'backups/synthetic.dump', 'sha256': checksum, 'version_id': 'synthetic-dump-version'}
        manifest = {'schema': 'jobwatch.aws-backup/2', 'backup_started_at': (now-timedelta(hours=1)).isoformat(),
                    'created_at': (now-timedelta(minutes=5)).isoformat(), 'archive_bytes': archive.stat().st_size,
                    'archive_sha256': checksum, 'archive_object': obj}
        raw = json.dumps(manifest).encode();archive.with_suffix('.json').write_bytes(raw)
        receipts = [obj, {'key': 'backups/synthetic.json', 'sha256': sha256(raw).hexdigest(),
                          'version_id': 'synthetic-manifest-version'}]
        return archive, receipts, Path(directory)/'latest.json', now

    def test_confirmed_completion_receipt_and_exact_schedule_age(self):
        with TemporaryDirectory() as d:
            archive, receipts, path, now = self.fixture(d)
            monitor.record(archive, receipts, path, now=now)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(monitor.health(path, now=now)['backup_healthy'], 1)
            # Snapshot began an hour before completion; upload time does not refresh it.
            warning = monitor.health(path, now=now+timedelta(hours=13))
            self.assertEqual((warning['status'], warning['backup_healthy']), ('warning', 0))
            self.assertEqual(monitor.health(path, now=now+timedelta(hours=25))['status'], 'critical')
            self.assertEqual(json.loads(path.read_text())['manifest_object'], receipts[1])

    def test_missing_malformed_and_unsafe_receipts_are_unhealthy_without_leaks(self):
        with TemporaryDirectory() as d:
            path = Path(d)/'status.json'
            self.assertEqual(monitor.health(path)['status'], 'missing')
            for data in ('private-marker-not-json', '{}', 'x'*8193):
                path.write_text(data);path.chmod(0o600)
                self.assertEqual(monitor.health(path)['backup_healthy'], 0)
                self.assertNotIn('private-marker', json.dumps(monitor.health(path)))
            _, _, good, now = self.fixture(d)
            good.write_text('{}');good.chmod(0o644)
            self.assertEqual(monitor.health(good, now=now)['status'], 'invalid')
            link=Path(d)/'symlink.json';link.symlink_to(good)
            self.assertEqual(monitor.health(link, now=now)['backup_healthy'], 0)

    def test_bad_archive_or_incomplete_upload_never_replaces_last_good(self):
        with TemporaryDirectory() as d:
            archive, receipts, path, now = self.fixture(d)
            monitor.record(archive, receipts, path, now=now);before=path.read_bytes()
            for bad in (receipts[:1], [receipts[0], {**receipts[1], 'version_id': 'null'}],
                        [receipts[0], {**receipts[1], 'key': 'backups/other.json'}],
                        [receipts[0], {**receipts[1], 'sha256': '0'*64}]):
                with self.assertRaises((ValueError,KeyError)): monitor.record(archive,bad,path,now=now)
                self.assertEqual(path.read_bytes(),before)
            archive.write_bytes(b'broken')
            with self.assertRaises(ValueError):monitor.record(archive,receipts,path,now=now)
            self.assertEqual(path.read_bytes(),before)

    def test_atomic_failure_and_symlinks_preserve_last_good(self):
        with TemporaryDirectory() as d:
            archive, receipts, path, now = self.fixture(d)
            monitor.record(archive,receipts,path,now=now);before=path.read_bytes()
            with patch('backup_monitor.os.replace',side_effect=OSError('private-marker')):
                with self.assertRaises(OSError):monitor.record(archive,receipts,path,now=now)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list(Path(d).glob('.receipt-*')),[])
            link=Path(d)/'link.json';link.symlink_to(path)
            with self.assertRaises(ValueError):monitor.record(archive,receipts,link,now=now)
            os.chmod(d,0o755)
            with self.assertRaises(ValueError):monitor.record(archive,receipts,path,now=now)
            self.assertEqual(path.read_bytes(),before)

    def test_heartbeat_logs_only_redacted_health_using_existing_log_permissions(self):
        with TemporaryDirectory() as d:
            archive,receipts,path,now=self.fixture(d);monitor.record(archive,receipts,path,now=now)
            cfg=Path(d)/'aws.json';cfg.write_text(json.dumps({'region':'eu-north-1','log_group':'synthetic','log_stream':'synthetic'}))
            for delivered in (True,False):
                calls=[]
                def run(args,**kwargs):
                    calls.append(args)
                    return subprocess.CompletedProcess(args,0 if delivered else 1,stdout='',stderr='secret-marker')
                output=io.StringIO()
                with patch('backup_monitor.health',return_value=monitor.health(path,now=now)),redirect_stdout(output):
                    # The real publisher defaults were bound at definition; inject at that boundary.
                    with patch('backup_monitor.publish',side_effect=lambda s,c:run_operation.publish(s,c,run_command=run)):
                        self.assertEqual(monitor.main(['--receipt',str(path),'--config',str(cfg)]),0 if delivered else 2)
                rendered=output.getvalue()+json.dumps(calls)
                for forbidden in ('synthetic-dump-version','synthetic-manifest-version','backups/synthetic','secret-marker'):
                    self.assertNotIn(forbidden,rendered)
                self.assertEqual(calls[0][1:3],['logs','put-log-events'])
                self.assertNotIn('exit_code',json.loads(output.getvalue()))

    def test_backup_wrapper_requests_receipt_but_never_collection_or_activation(self):
        with TemporaryDirectory() as d:
            cfg=Path(d)/'aws.json';cfg.write_text(json.dumps({'region':'eu-north-1','log_group':'synthetic','log_stream':'synthetic'}))
            calls=[]
            def run(args,**kwargs):
                calls.append(args);return subprocess.CompletedProcess(args,0,stdout='{}')
            with redirect_stdout(io.StringIO()):self.assertEqual(run_operation.run('backup',config_path=cfg,run_command=run),0)
            self.assertEqual(calls[0][-2:],['--receipt','/var/lib/jobwatch-backup/latest.json'])
            self.assertFalse(any('systemctl' in a or 'scan' in a for a in calls))
            with patch.object(sys,'argv',['database_backup.py','backup','--output','/unused','--receipt','/unused']),patch('database_backup.connect') as connection,redirect_stdout(io.StringIO()):
                self.assertEqual(backup.main(),2)
                connection.assert_not_called()

    def test_prepared_health_service_and_alarm_cover_absent_checker(self):
        service=(ROOT/'infra/aws/systemd/jobwatch-backup-health.service').read_text()
        timer=(ROOT/'infra/aws/systemd/jobwatch-backup-health.timer').read_text()
        self.assertIn('User=jobwatch-backup',service);self.assertIn('StateDirectoryMode=0700',service)
        self.assertNotIn('JOBWATCH_MIGRATION_DATABASE_URL',service)
        self.assertIn('OnCalendar=*-*-* *:00,15,30,45:00 UTC',timer)
        config=json.loads((ROOT/'infra/aws/backup-monitoring.example.json').read_text())
        self.assertEqual(config['alarm']['TreatMissingData'],'breaching')
        self.assertEqual(config['alarm']['Period'],900)
        self.assertEqual(config['alarm']['DatapointsToAlarm'],2)
        self.assertEqual(config['metric_filter']['metricTransformations'][0]['metricValue'],'$.backup_healthy')
        self.assertNotIn('defaultValue',config['metric_filter']['metricTransformations'][0])
