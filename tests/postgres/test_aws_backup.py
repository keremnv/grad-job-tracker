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
import backup_freshness


@unittest.skipUnless(ENABLED,'Use scripts/test_postgres.sh; no arbitrary database URLs accepted')
class AWSBackupTests(unittest.TestCase):
    def test_independent_network_isolated_vector_restore(self):
        """New synthetic-only archive; no EC2, AWS credentials or existing private backup."""
        from psycopg.conninfo import make_conninfo
        from support import actor
        from jobwatch.postgres.evidence import encode_evidence
        from jobwatch.postgres.embeddings import chunks,write_chunks
        from test_embeddings import Tokenizer,unit
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'infra/aws/tests'))
        import recover_offhost
        target=Target.verified();container=os.environ['JOBWATCH_PG_TEST_CONTAINER']
        name='jobwatch_vector_offhost_'+uuid4().hex
        subprocess.run(['docker','exec',container,'createdb','-U','postgres','-O','postgres',name],check=True)
        def admin(sql):
            subprocess.run(['docker','exec','-i',container,'psql','-X','-U','postgres','-d',name,'-v','ON_ERROR_STOP=1'],input=sql.encode(),check=True,capture_output=True)
        # Same reviewed DB-level grants/settings, roles already exist on this test cluster.
        bootstrap=(Path(__file__).resolve().parents[2]/'sql/postgresql/bootstrap.sql').read_text()
        admin('\n'.join(l for l in bootstrap.splitlines() if not l.startswith('CREATE ROLE')))
        admin(f'GRANT CONNECT ON DATABASE {name} TO jobwatch_embedding_writer;')
        config=replace(target.config(),dsn=make_conninfo(target.config().dsn,dbname=name))
        old_umask=os.umask(0o077)
        try:
            with TemporaryDirectory() as d:
                archive=Path(d)/'synthetic.dump'
                with connect(config) as conn:
                    migrate(conn)
                    with conn.transaction():
                        actor(conn,'test:synthetic-vector-recovery')
                        e=conn.execute("INSERT INTO jobwatch.employers(slug,name) VALUES ('synthetic','Synthetic') RETURNING id").fetchone()['id']
                        s=conn.execute("INSERT INTO jobwatch.sources(provider,board,employer_id) VALUES ('greenhouse','synthetic',%s) RETURNING id",(e,)).fetchone()['id']
                        ids=[uuid4() for _ in range(3)]
                        for i in (1,2):
                            key=f'greenhouse:synthetic:{i}';vid=ids[i-1]
                            conn.execute("""INSERT INTO jobwatch.postings(posting_key,source_id,provider,board,native_id,status,
                                first_seen_at,first_seen_basis,current_version_id,verdict,family,codes,rules_version,screening_basis,screened_at)
                                VALUES (%s,%s,'greenhouse','synthetic',%s,'listed',now(),'legacy_retained',%s,'review','software','{}','test-v1','legacy',now())""",(key,s,str(i),vid))
                        for n,vid in enumerate(ids):
                            key=f'greenhouse:synthetic:{min(n+1,2)}'
                            raw=encode_evidence({'description':f'Synthetic Python\0services {n}'})
                            conn.execute("""INSERT INTO jobwatch.posting_versions(id,posting_key,source_id,origin,evidence,observed_at,
                                normalizer_version,normalized_sha256,company,title,posting_url,location_text,description_text)
                                VALUES (%s,%s,%s,'legacy_index',%s,now(),'synthetic-v1',%s,'Synthetic','Graduate Python',%s,'London','Python services')""",
                                (vid,key,s,raw,'a'*64,'https://example.org/jobs/'+key[-1]))
                        conn.execute("INSERT INTO jobwatch.posting_events(posting_key,source_id,observed_at,category,kind) VALUES ('greenhouse:synthetic:2',%s,now(),'import','legacy_event')",(s,))
                        conn.execute("INSERT INTO jobwatch.triage(posting_key,decision,note,recorded_by) VALUES ('greenhouse:synthetic:1','dismissed','SYNTHETIC PRIVATE NOTE ONLY','owner')")
                        conn.execute("INSERT INTO jobwatch.applications(job_key,posting_key,company,role,url,stage,notes) VALUES ('manual:synthetic','greenhouse:synthetic:1','Synthetic','Graduate Python','https://example.org/jobs/1','applied','SYNTHETIC PRIVATE NOTE ONLY')")
                    v=conn.execute('SELECT * FROM jobwatch.posting_versions ORDER BY id LIMIT 1').fetchone()
                    items=chunks(v,Tokenizer());write_chunks(conn,v,items,[unit()]*len(items))
                    def utility(args,*,input=None):
                        p=subprocess.run(['docker','exec','-i','-e','PGUSER=jobwatch_migrator','-e','PGDATABASE='+name,container,*args],input=input,capture_output=True)
                        if p.returncode:raise database_backup.BackupError('Disposable utility failed')
                        return p.stdout
                    database_backup.backup(conn,archive,execute=utility)
                # The restore target has --network none and never connects to the source DB.
                result=recover_offhost.recover(archive,archive.with_suffix('.json'),Path(d)/'independent')
                self.assertTrue(result['restore']['exact_rows_schema_and_sequences'])
                self.assertFalse(result['ec2_database_accessed'])
                self.assertEqual(result['network'],'none')
                self.assertGreater(result['fingerprint']['tables']['embedding_chunks']['count'],0)
        finally:
            os.umask(old_umask)
            subprocess.run(['docker','exec',container,'dropdb','-U','postgres',name],check=True)

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
                    from jobwatch.postgres.embeddings import chunks,write_chunks
                    from test_embeddings import Tokenizer,unit
                    v=source.execute('SELECT * FROM jobwatch.posting_versions ORDER BY id LIMIT 1').fetchone()
                    items=chunks(v,Tokenizer());write_chunks(source,v,items,[unit()]*len(items))
                    before=database_backup.fingerprint(source)
                    manifest=database_backup.backup(source,archive,execute=utility(names[0]))
                    # Real dump start, not upload/manifest time, bounds recoverable age.
                    from datetime import datetime
                    self.assertLessEqual(datetime.fromisoformat(manifest['backup_started_at']),
                                         datetime.fromisoformat(manifest['created_at']))
                    with self.assertRaisesRegex(ValueError,'completion_link'):
                        backup_freshness.inspect(archive,manifest)
                    def receipt_only(args,**kwargs):
                        # No AWS call: exercise the production manifest-last writer
                        # and offline validator with simulated PutObject receipts.
                        return subprocess.CompletedProcess(args,0,stdout=json.dumps({
                            'ChecksumSHA256':args[args.index('--checksum-sha256')+1],
                            'VersionId':'synthetic-completion-version'}))
                    database_backup.upload(archive,{'bucket':'synthetic','region':'eu-north-1'},run_command=receipt_only)
                    manifest=json.loads(archive.with_suffix('.json').read_text())
                    age=backup_freshness.inspect(archive,manifest)
                    self.assertEqual(age['status'],'fresh')
                    self.assertFalse(age['independent_custody_verified'])
                    # Receipt does not disclose private fixture notes; exact field hashes cover them.
                    self.assertNotIn('private application',json.dumps(manifest))
                    self.assertEqual(before,manifest['fingerprint'])
                    self.assertEqual(manifest['fingerprint']['tables']['applications']['count'],3)
                    self.assertGreater(manifest['fingerprint']['tables']['embedding_chunks']['count'],0)
                with connect(config(names[1])) as restored:
                    restored.execute("CREATE FUNCTION public.unexpected_restore_object() RETURNS int LANGUAGE SQL AS 'SELECT 1'")
                    with self.assertRaisesRegex(database_backup.BackupError,'unexpected public'):
                        database_backup.restore(restored,archive,manifest,execute=utility(names[1]))
                    restored.execute('DROP FUNCTION public.unexpected_restore_object()')
                    restored.execute('CREATE OPERATOR public.=== (LEFTARG=integer,RIGHTARG=integer,PROCEDURE=pg_catalog.int4eq)')
                    with self.assertRaisesRegex(database_backup.BackupError,'unexpected public'):
                        database_backup.restore(restored,archive,manifest,execute=utility(names[1]))
                    restored.execute('DROP OPERATOR public.=== (integer,integer)')
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
