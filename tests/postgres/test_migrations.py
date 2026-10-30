"""Real packaged migrations, non-superuser execution and connection boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from hashlib import sha256
import io
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import traceback
import unittest
from unittest.mock import patch

from jobwatch.postgres.connection import DatabaseConfig, DatabaseError, connect
from jobwatch.postgres.migrate import MIGRATION_LOCK, Migration, MigrationError, load_migrations, main, migrate
from support import ENABLED, Target


def extra(version, sql):
    return Migration(version, f'{version:04d}_test.sql', sha256(sql.encode()).hexdigest(), sql)


@unittest.skipUnless(ENABLED, 'Run scripts/test_postgres.sh for disposable PostgreSQL integration tests')
class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target = Target.verified()

    def setUp(self):
        self.target.reset_schema()

    def test_empty_database_migration_and_repeat(self):
        plan = load_migrations()
        with self.target.connect() as conn:
            privileges = conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
            self.assertEqual(set(privileges.values()), {False})
            self.assertEqual(migrate(conn), [m.filename for m in plan])
            first = conn.execute('SELECT * FROM jobwatch.schema_migrations ORDER BY version LIMIT 1').fetchone()
            self.assertEqual(migrate(conn), [])
            self.assertEqual(conn.execute('SELECT * FROM jobwatch.schema_migrations ORDER BY version LIMIT 1').fetchone(), first)
            self.assertEqual((first['version'], first['filename'], first['sha256']),
                             (1, plan[0].filename, plan[0].checksum))
            self.assertEqual(conn.execute("SELECT pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='jobwatch'").fetchone()['owner'], 'jobwatch_migrator')

    def test_lead_deadline_upgrade_preserves_existing_view_grants_and_evidence(self):
        plan=load_migrations()
        with self.target.connect() as conn:
            migrate(conn,plan[:4])
            conn.execute("SELECT set_config('jobwatch.actor','test:upgrade',false)")
            conn.execute("""INSERT INTO jobwatch.leads(lead_key,kind,company,title,state,url,official,
                source_note,deadline_date,deadline_wording,checked_at,checked_by)
                VALUES ('vacancy:upgrade','vacancy','Test','Role','listed','https://example.org/old',
                true,'Private source','2026-11-10','Apply by 10 November',now(),'owner')""")
            # Simulate the already-provisioned production view without the four new columns.
            access=(Path(__file__).resolve().parents[2]/'sql/postgresql/agent_access.sql').read_text()
            view=access.split('CREATE VIEW jobwatch.agent_leads AS',1)[1].split('CREATE VIEW jobwatch.agent_audit_summary',1)[0]
            view=view.replace(',deadline_source_url,deadline_official,deadline_checked_at,deadline_checked_by','')
            conn.execute('CREATE VIEW jobwatch.agent_leads AS'+view)
            conn.execute('GRANT SELECT ON jobwatch.agent_leads TO jobwatch_owner')
            old=conn.execute('SELECT * FROM jobwatch.leads').fetchone()
            audit=conn.execute('SELECT * FROM jobwatch.owner_audit').fetchall()
            self.assertEqual(migrate(conn),[m.filename for m in plan[4:]])
            after=conn.execute('SELECT * FROM jobwatch.leads').fetchone()
            self.assertEqual({k:after[k] for k in old},old)
            self.assertTrue(all(after[k] is None for k in ('deadline_source_url','deadline_official',
                'deadline_checked_at','deadline_checked_by')))
            self.assertEqual(conn.execute('SELECT * FROM jobwatch.owner_audit').fetchall(),audit)
            self.assertTrue(conn.execute("SELECT has_table_privilege('jobwatch_owner','jobwatch.agent_leads','SELECT') AS ok").fetchone()['ok'])
            self.assertIn('deadline_checked_by',conn.execute('SELECT * FROM jobwatch.agent_leads').fetchone())

    def test_posting_deadline_upgrade_keeps_legacy_verification_evidence(self):
        from support import actor,source,run,posting
        plan=load_migrations()
        with self.target.connect() as c:
            migrate(c,plan[:5])
            with c.transaction():
                actor(c);sid=source(c);attempt=run(c,sid);key,_=posting(c,sid,attempt['id'])
                c.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording,value) VALUES (%s,'deadline',now(),'owner','https://example.org/official','Apply by 12 November','2026-11-12')",(key,))
            old=c.execute('SELECT * FROM jobwatch.verifications').fetchone()
            audit=c.execute('SELECT * FROM jobwatch.owner_audit').fetchall()
            self.assertEqual(migrate(c),[plan[5].filename])
            after=c.execute('SELECT * FROM jobwatch.verifications').fetchone()
            self.assertEqual({k:after[k] for k in old},old)
            self.assertIsNone(after['deadline_supersedes'])
            self.assertIsNone(after['deadline_reason'])
            self.assertEqual(c.execute('SELECT * FROM jobwatch.owner_audit').fetchall(),audit)
            claims=c.execute('SELECT * FROM jobwatch.posting_deadline_claims').fetchall()
            self.assertEqual(len(claims),1)
            self.assertEqual(claims[0]['claim_id'],'verification:'+str(old['id']))
            self.assertEqual(claims[0]['superseded_by'],[])

    def test_deadline_upgrade_with_existing_agent_roles_applies_only_narrow_deltas(self):
        root=Path(__file__).resolve().parents[2]
        container=os.environ['JOBWATCH_PG_TEST_CONTAINER']
        def admin(sql):
            p=subprocess.run(['docker','exec','-i',container,'psql','-X','-U','postgres',
                '-d','jobwatch_foundation_test','-v','ON_ERROR_STOP=1','-f','-'],
                input=sql,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
        with self.target.connect() as c:
            migrate(c,load_migrations()[:4])
            if not c.execute("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jobwatch_agent_owner') AS present").fetchone()['present']:
                admin((root/'sql/postgresql/agent_roles.sql').read_text())
            original=(root/'sql/postgresql/agent_access.sql').read_text().replace(
                ',deadline_source_url,deadline_official,deadline_checked_at,deadline_checked_by','')
            admin(original)
            def privileges():
                return {(r['role'],r['table_name'],r['column_name'],r['privilege_type']) for r in c.execute("""
                    SELECT r.role,c.table_name,c.column_name,p.privilege_type
                    FROM (VALUES ('jobwatch_agent_owner'),('jobwatch_agent_readonly'),('jobwatch_calendar_reader')) r(role)
                    CROSS JOIN information_schema.columns c
                    CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('REFERENCES')) p(privilege_type)
                    WHERE c.table_schema='jobwatch' AND c.table_name IN ('leads','verifications','postings','posting_versions')
                    AND has_column_privilege(r.role,'jobwatch.'||c.table_name,c.column_name,p.privilege_type)
                """)}
            before=privileges()
            self.assertEqual(migrate(c),[m.filename for m in load_migrations()[4:]])
            for name in ('lead_deadline_access.sql','posting_deadline_access.sql'):
                admin((root/'sql/postgresql'/name).read_text())
            added=privileges()-before
            expected=set()
            for role in ('jobwatch_agent_owner','jobwatch_agent_readonly','jobwatch_calendar_reader'):
                expected.update((role,'leads',col,'SELECT') for col in
                    ('deadline_source_url','deadline_official','deadline_checked_at','deadline_checked_by'))
                expected.update((role,'verifications',col,'SELECT') for col in ('deadline_supersedes','deadline_reason'))
            expected.update(('jobwatch_agent_owner','leads',col,'UPDATE') for col in
                ('deadline_date','deadline_wording','deadline_source_url','deadline_official','deadline_checked_at','deadline_checked_by'))
            expected.update(('jobwatch_agent_owner','verifications',col,'INSERT') for col in
                ('id','posting_key','kind','checked_at','checked_by','url','wording','value','deadline_supersedes','deadline_reason'))
            self.assertEqual(added,expected)
            self.assertTrue(before<=privileges())
            self.assertEqual(migrate(c),[])
            # Reapplying only the delta files is idempotent and preserves the exact matrix.
            for name in ('lead_deadline_access.sql','posting_deadline_access.sql'):
                admin((root/'sql/postgresql'/name).read_text())
            self.assertEqual(privileges()-before,expected)

    def test_changed_migration_refused(self):
        original = load_migrations()
        changed = original[0].sql + '\n-- a changed applied file\n'
        with self.target.connect() as conn:
            migrate(conn)
            with self.assertRaisesRegex(MigrationError, 'has changed'):
                migrate(conn, [replace(original[0], sql=changed, checksum=sha256(changed.encode()).hexdigest())] + original[1:])
            self.assertEqual(conn.execute('SELECT sha256 FROM jobwatch.schema_migrations').fetchone()['sha256'], original[0].checksum)

    def test_renamed_and_unknown_history_refused(self):
        original = load_migrations()
        with self.target.connect() as conn:
            migrate(conn)
            with self.assertRaisesRegex(MigrationError, 'has changed'):
                migrate(conn, [replace(original[0], filename='0001_renamed.sql')] + original[1:])
            conn.execute("INSERT INTO jobwatch.schema_migrations(version,filename,sha256) VALUES (%s,'9999_unavailable.sql',repeat('a',64))", (len(original)+1,))
            with self.assertRaisesRegex(MigrationError, 'not a prefix'):
                migrate(conn)

    def test_failed_initialization_is_atomic(self):
        plan = load_migrations() + [extra(len(load_migrations())+1, 'CREATE TABLE jobwatch.failure_probe(id int); SELECT 1/0;')]
        with self.target.connect() as conn:
            with self.assertRaisesRegex(MigrationError, 'test.sql.*22012'):
                migrate(conn, plan)
            self.assertIsNone(conn.execute("SELECT to_regnamespace('jobwatch') AS schema").fetchone()['schema'])
            self.assertEqual(migrate(conn), [m.filename for m in load_migrations()])

    def test_failed_upgrade_preserves_existing_data_and_ledger(self):
        with self.target.connect() as conn:
            migrate(conn)
            conn.execute('CREATE TABLE jobwatch.preserved_test_row(value text)')
            conn.execute("INSERT INTO jobwatch.preserved_test_row VALUES ('keep')")
            with self.assertRaises(MigrationError):
                migrate(conn, load_migrations() + [extra(len(load_migrations())+1, "DELETE FROM jobwatch.preserved_test_row; SELECT 1/0;")])
            self.assertEqual(conn.execute('SELECT value FROM jobwatch.preserved_test_row').fetchone()['value'], 'keep')
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM jobwatch.schema_migrations').fetchone()['n'], len(load_migrations()))

    def test_unmanaged_schema_is_not_adopted(self):
        with self.target.connect() as conn:
            conn.execute('CREATE SCHEMA jobwatch')
            conn.execute('CREATE TABLE jobwatch.unmanaged(id int)')
            with self.assertRaisesRegex(MigrationError, 'refusing to adopt'):
                migrate(conn)
            self.assertIsNotNone(conn.execute("SELECT to_regclass('jobwatch.unmanaged') AS table").fetchone()['table'])
            self.assertIsNone(conn.execute("SELECT to_regclass('jobwatch.schema_migrations') AS table").fetchone()['table'])

    def test_concurrent_migration_invocations(self):
        def apply():
            with self.target.connect() as conn:
                return migrate(conn)

        with ThreadPoolExecutor(2) as pool, self.target.connect() as locker:
            with locker.transaction():
                locker.execute('SELECT pg_advisory_xact_lock(%s)', (MIGRATION_LOCK,))
                futures = [pool.submit(apply) for _ in range(2)]
                deadline = time.monotonic() + 3
                with self.target.connect('postgres') as observer:
                    while time.monotonic() < deadline:
                        blocked = observer.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() "
                            "AND wait_event_type='Lock' AND query LIKE 'SELECT pg_advisory_xact_lock%'").fetchone()['n']
                        if blocked == 2:
                            break
                        time.sleep(.01)
                    self.assertEqual(blocked, 2)
            self.assertEqual(sorted(len(f.result(5)) for f in futures), [0, len(load_migrations())])

    def test_migration_file_ordering_and_duplicate_detection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '0002_second.sql').write_text('SELECT 2;')
            (root / '0001_first.sql').write_text('SELECT 1;')
            self.assertEqual([m.version for m in load_migrations(root)], [1, 2])
            (root / '0001_duplicate.sql').write_text('SELECT 3;')
            with self.assertRaisesRegex(MigrationError, 'unique and consecutive'):
                load_migrations(root)

    def test_cli_and_distinct_migration_credentials(self):
        env = {**os.environ, 'JOBWATCH_MIGRATION_DATABASE_URL': self.target.config().dsn}
        result = subprocess.run([sys.executable, '-m', 'jobwatch.postgres.migrate'], env=env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Applied 0001_initial.sql', result.stdout)
        with self.target.connect('jobwatch_owner') as conn:
            with self.assertRaisesRegex(MigrationError, 'separate migration credentials'):
                migrate(conn)
        stderr, stdout = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'JOBWATCH_DATABASE_URL': 'runtime-secret'}, clear=True), redirect_stderr(stderr), redirect_stdout(stdout):
            self.assertEqual(main([]), 2)
        self.assertIn('JOBWATCH_MIGRATION_DATABASE_URL is required', stderr.getvalue())
        self.assertNotIn('runtime-secret', stdout.getvalue() + stderr.getvalue())

    def test_configuration_and_connection_errors_do_not_expose_credentials(self):
        config = DatabaseConfig("password='test-only-secret' invalid-key=bad")
        self.assertNotIn('test-only-secret', repr(config))
        try:
            connect(config)
        except DatabaseError as exc:
            self.assertNotIn('test-only-secret', ''.join(traceback.format_exception(exc)))
        else:
            self.fail('Invalid libpq key should be rejected before connecting')
        with self.assertRaisesRegex(DatabaseError, 'JOBWATCH_DB_CONNECT_TIMEOUT must be a positive integer') as failure:
            DatabaseConfig.from_env(environ={'JOBWATCH_DATABASE_URL': 'test-only-secret', 'JOBWATCH_DB_CONNECT_TIMEOUT': 'another-secret'})
        self.assertNotIn('another-secret', str(failure.exception))

    def test_idle_autocommit_required_and_connection_defaults(self):
        with self.target.connect() as conn:
            self.assertTrue(conn.autocommit)
            self.assertEqual(conn.execute('SHOW transaction_isolation').fetchone()['transaction_isolation'], 'read committed')
            self.assertEqual(conn.execute('SHOW statement_timeout').fetchone()['statement_timeout'], '10s')
            self.assertEqual(conn.execute('SHOW lock_timeout').fetchone()['lock_timeout'], '5s')
            with conn.transaction():
                with self.assertRaisesRegex(MigrationError, 'idle autocommit'):
                    migrate(conn)
        self.assertTrue(conn.closed)

    def test_stage_one_upgrade_preserves_existing_rows(self):
        from support import actor, source, run, posting
        with self.target.connect() as conn:
            plan = load_migrations()
            migrate(conn, plan[:1])
            with conn.transaction():
                actor(conn)
                sid = source(conn)
                attempt = run(conn, sid)
                key, vid = posting(conn, sid, attempt['id'])
            self.assertEqual(migrate(conn), [m.filename for m in plan[1:]])
            row = conn.execute('SELECT current_version_id,last_seen_run_id FROM jobwatch.postings WHERE posting_key=%s', (key,)).fetchone()
            self.assertEqual(row['current_version_id'], vid)
            self.assertIsNone(row['last_seen_run_id'])
