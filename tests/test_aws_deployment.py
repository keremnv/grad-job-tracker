"""Local deployment contracts; no AWS credentials, network calls or host changes."""
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
import shlex
import subprocess
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'infra/aws/scripts'
sys.path.insert(0, str(SCRIPTS))
import database_backup as backup
import deploy_release
import run_operation


class DeploymentTests(unittest.TestCase):
    def artifact(self, directory):
        root = Path(directory)
        commit = 'a'*40
        files = {'src/module.py': b'print("hello")\n'}
        manifest = {'source_commit': commit, 'python': '3.12', 'architecture': 'aarch64',
                    'files': {k: sha256(v).hexdigest() for k,v in files.items()}}
        files['MANIFEST.json'] = json.dumps(manifest, sort_keys=True, indent=2).encode()+b'\n'
        archive = root/'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            for name, data in files.items():
                info = tarfile.TarInfo(name); info.size=len(data)
                stream.addfile(info, io.BytesIO(data))
        return archive, sha256(archive.read_bytes()).hexdigest(), commit

    def test_release_hash_inventory_and_identity(self):
        with TemporaryDirectory() as d:
            archive, digest, commit = self.artifact(d)
            self.assertEqual(deploy_release.verify(archive, digest, commit)['source_commit'], commit)
            for bad_hash, bad_commit in [('0'*64, commit), (digest, 'b'*40)]:
                with self.assertRaises(ValueError):
                    deploy_release.verify(archive, bad_hash, bad_commit)

    def test_release_rejects_traversal_and_symlinks(self):
        with TemporaryDirectory() as d:
            archive = Path(d)/'evil.tar'
            for name, kind in [('../escape', tarfile.REGTYPE), ('link',tarfile.SYMTYPE)]:
                with tarfile.open(archive,'w') as stream:
                    entry=tarfile.TarInfo(name);entry.type=kind;entry.linkname='/etc/passwd'
                    stream.addfile(entry)
                with self.assertRaises(ValueError):
                    deploy_release.verify(archive,sha256(archive.read_bytes()).hexdigest(),'a'*40)

    def test_deployment_refuses_active_or_enabled_timers_before_any_write(self):
        with patch('deploy_release.os.geteuid',return_value=0),patch('deploy_release.platform.machine',return_value='aarch64'):
            with self.assertRaisesRegex(ValueError,'Pause collection'):
                deploy_release.deploy(Path('/missing'),'a'*64,'b'*40,
                    run_command=lambda *a,**kw:SimpleNamespace(returncode=0))
            with self.assertRaisesRegex(ValueError,'Disable timers'):
                deploy_release.deploy(Path('/missing'),'a'*64,'b'*40,
                    run_command=lambda args,**kw:SimpleNamespace(returncode=0 if args[1]=='is-enabled' else 3))

    def test_filesystem_deployment_replay_preserves_database_with_mocked_host_commands(self):
        with TemporaryDirectory() as d:
            archive,digest,commit=self.artifact(d)
            root=Path(d)/'host';(root/'etc/systemd/system').mkdir(parents=True)
            data=root/'var/lib/jobwatch-postgresql/pgdata/evidence'
            data.parent.mkdir(parents=True);data.write_bytes(b'immutable database evidence')
            commands=[]
            def run(args,**kwargs):
                commands.append(args)
                if args[0]=='systemctl':return SimpleNamespace(returncode=1 if args[1].startswith('is-') else 0)
                if args[:3]==['python3.12','-m','venv']:
                    venv=Path(args[3]);(venv/'bin').mkdir(parents=True)
                    (venv/'bin/jobwatch-pg-ops').write_text('fixture executable')
                return SimpleNamespace(returncode=0)
            with patch('deploy_release.os.geteuid',return_value=0),patch('deploy_release.platform.machine',return_value='aarch64'),redirect_stdout(io.StringIO()):
                deploy_release.deploy(archive,digest,commit,installation_root=root,run_command=run)
                first=list(commands);commands.clear()
                deploy_release.deploy(archive,digest,commit,installation_root=root,run_command=run)
            self.assertTrue(any(c[0]=='python3.12' for c in first))
            self.assertFalse(any(c[0]=='python3.12' for c in commands))
            self.assertFalse(any(c[0]=='systemctl' and c[1] in ('start','enable','restart')
                                 for c in first+commands))
            self.assertEqual((root/'opt/jobwatch/current').resolve(),root/'opt/jobwatch/releases'/commit)
            self.assertEqual(data.read_bytes(),b'immutable database evidence')
            failed=root.parent/'failed-host';(failed/'etc/systemd/system').mkdir(parents=True)
            def fail_install(args,**kwargs):
                if len(args)>1 and args[1]=='install':raise subprocess.CalledProcessError(1,args)
                return run(args,**kwargs)
            with patch('deploy_release.os.geteuid',return_value=0),patch('deploy_release.platform.machine',return_value='aarch64'):
                with self.assertRaises(subprocess.CalledProcessError):
                    deploy_release.deploy(archive,digest,commit,installation_root=failed,run_command=fail_install)
                with self.assertRaisesRegex(ValueError,'Incomplete'):
                    deploy_release.deploy(archive,digest,commit,installation_root=failed,run_command=run)

    def test_deployment_rejects_wrong_host_before_writes(self):
        with patch('deploy_release.os.geteuid',return_value=0),patch('deploy_release.platform.machine',return_value='x86_64'):
            with self.assertRaisesRegex(ValueError,'ARM64'):
                deploy_release.deploy(Path('/missing'),'a'*64,'b'*40)

    def test_s3_upload_requires_validated_checksum_and_version_without_read_permission(self):
        with TemporaryDirectory() as d:
            archive=Path(d)/'backup.dump';archive.write_bytes(b'PGDMP fixture')
            archive.with_suffix('.json').write_text('{"hash":"test"}')
            calls=[]
            def run(args,**kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=0,stdout=json.dumps({'ChecksumSHA256':args[args.index('--checksum-sha256')+1],'VersionId':'fixture'}))
            result=backup.upload(archive,{'region':'eu-north-1','bucket':'fixture'},run_command=run)
            self.assertEqual(len(result),2)
            self.assertEqual(json.loads(archive.with_suffix('.json').read_text())['archive_object']['version_id'],'fixture')
            self.assertTrue(all(c[1:3]==['s3api','put-object'] and 'AES256' in c for c in calls))
            def missing_receipt(*args,**kwargs):return SimpleNamespace(returncode=0,stdout='{}')
            with self.assertRaises(backup.BackupError):
                backup.upload(archive,{'region':'eu-north-1','bucket':'fixture'},run_command=missing_receipt)

    def test_safe_summary_preserves_partial_failure_and_positive_only(self):
        with TemporaryDirectory() as d:
            config=Path(d)/'aws.json';config.write_text(json.dumps({'region':'eu-north-1','log_group':'test','log_stream':'operations'}))
            calls=[]
            def run(args,**kwargs):
                calls.append(args)
                if args[0]=='aws':return SimpleNamespace(returncode=1,stdout='',stderr='private-secret')
                data={'counts':{'published':1,'failed':1},'selected_sources':2,'positive_only':1,'absence_safe':0,
                      'sources':[{'source_id':1,'status':'published','coverage':'positive_only','vacancies':3,'notes':'private-note'}]}
                return SimpleNamespace(returncode=1,stdout=json.dumps(data),stderr='password=private-secret')
            output=io.StringIO()
            with redirect_stdout(output):
                code=run_operation.run('collect',config_path=config,run_command=run)
            self.assertEqual(code,1)
            rendered=output.getvalue()+json.dumps(calls)
            self.assertNotIn('private-secret',rendered);self.assertNotIn('private-note',rendered)
            self.assertEqual(json.loads(output.getvalue())['positive_only'],1)

    def test_timer_and_host_scripts_have_no_automatic_cutover(self):
        root=SCRIPTS.parent
        deploy=(SCRIPTS/'deploy_release.py').read_text()
        self.assertNotIn("['systemctl', 'enable'",deploy)
        self.assertNotIn("['systemctl', 'start'",deploy)
        host=(SCRIPTS/'bootstrap_host.sh').read_text()
        self.assertIn("listen_addresses = '127.0.0.1'",host)
        self.assertIn('create_main_cluster = false',host)
        self.assertIn('RequiresMountsFor=/var/lib/jobwatch-postgresql',host)
        self.assertIn('Nonempty uninitialized PGDATA',host)
        collect=(root/'systemd/jobwatch-collect.service').read_text()
        self.assertIn('User=jobwatch-collector',collect)
        self.assertIn('Restart=on-failure',collect)
        self.assertNotIn('migrate',collect)
        self.assertNotIn('git ',collect)
        for name, variable, role in (
            ('jobwatch-collect.service','JOBWATCH_DATABASE_URL','jobwatch_collector'),
            ('jobwatch-backup.service','JOBWATCH_MIGRATION_DATABASE_URL','jobwatch_migrator')):
            lines=(root/'systemd'/name).read_text().splitlines()
            line=next(v for v in lines if v.startswith('Environment=') and variable in v)
            assignments=dict(v.split('=',1) for v in shlex.split(line[len('Environment='):]))
            self.assertEqual(assignments[variable],f'host=/run/jobwatch-postgresql dbname=jobwatch user={role}')


if __name__=='__main__':unittest.main()
