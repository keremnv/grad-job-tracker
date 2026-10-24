"""Real production backup code against the verified disposable PostgreSQL container."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from jobwatch.postgres.connection import connect
from jobwatch.postgres.import_plan import build_plan
from jobwatch.postgres.importer import apply_import, reconcile
from jobwatch.postgres.migrate import migrate
from import_fixture import repository
from support import ENABLED, Target

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'infra/aws/scripts'))
import database_backup


@unittest.skipUnless(ENABLED,'Use scripts/test_postgres.sh; no arbitrary database URLs accepted')
class AWSBackupTests(unittest.TestCase):
    def test_snapshot_dump_exact_restore_private_owner_data_and_nonempty_refusal(self):
        from psycopg.conninfo import make_conninfo
        target=Target.verified()
        container=os.environ['JOBWATCH_PG_TEST_CONTAINER']
        names=['jobwatch_backup_'+uuid4().hex for _ in range(2)]
        def utility(db):
            def execute(args,*,input=None):
                # All utility addressing derives from Target.verified(), never ambient PG*.
                prefix=['docker','exec','-i','-e','PGUSER=jobwatch_migrator','-e','PGDATABASE='+db,container]
                result=subprocess.run(prefix+args,input=input,capture_output=True)
                if result.returncode:raise database_backup.BackupError('Disposable utility failed: '+args[0])
                return result.stdout
            return execute
        def config(db):
            original=target.config()
            return replace(original,dsn=make_conninfo(original.dsn,dbname=db),idle_transaction_timeout_ms=120000)
        for db in names:
            subprocess.run(['docker','exec',container,'createdb','-U','postgres','-O','jobwatch_migrator',db],check=True)
        try:
            with TemporaryDirectory() as d:
                repo,commit=repository(d)
                plan=build_plan(repo,commit)
                archive=Path(d)/'database.dump'
                with connect(config(names[0])) as source:
                    migrate(source);apply_import(source,plan)
                    before=database_backup.fingerprint(source)
                    manifest=database_backup.backup(source,archive,execute=utility(names[0]))
                    # Receipt does not disclose private fixture notes; exact field hashes cover them.
                    self.assertNotIn('private application',json.dumps(manifest))
                    self.assertEqual(before,manifest['fingerprint'])
                    self.assertEqual(manifest['fingerprint']['tables']['applications']['count'],3)
                with connect(config(names[1])) as restored:
                    result=database_backup.restore(restored,archive,manifest,execute=utility(names[1]))
                    self.assertTrue(result['exact_rows_schema_and_sequences'])
                    self.assertTrue(reconcile(restored,plan)['reconciliation']['ok'])
                    with self.assertRaisesRegex(database_backup.BackupError,'fresh'):
                        database_backup.restore(restored,archive,manifest,execute=utility(names[1]))
                archive.write_bytes(archive.read_bytes()+b'corrupt')
                with connect(config(names[1])) as restored:
                    with self.assertRaisesRegex(database_backup.BackupError,'checksum'):
                        database_backup.restore(restored,archive,manifest,execute=utility(names[1]))
        finally:
            for db in names:
                subprocess.run(['docker','exec',container,'dropdb','-U','postgres','--if-exists',db],check=True)
