"""Narrow audit semantics and the PostgreSQL primitives for later publication."""
from concurrent.futures import ThreadPoolExecutor
import time

try:
    import psycopg
except ImportError:
    psycopg = None

from support import DatabaseTest, actor, run, version


class AuditTests(DatabaseTest):
    def test_required_actor_and_transaction_local_scope(self):
        key, _, _ = self.seed()
        with self.target.connect('jobwatch_owner') as conn:
            with self.assertRaises(psycopg.errors.RaiseException):
                conn.execute("INSERT INTO jobwatch.triage(posting_key,decision,recorded_by) VALUES (%s,'interested','owner')", (key,))
            with conn.transaction():
                actor(conn, 'owner:test-session')
                conn.execute("INSERT INTO jobwatch.triage(posting_key,decision,recorded_by) VALUES (%s,'interested','owner')", (key,))
            with self.assertRaises(psycopg.errors.RaiseException):
                conn.execute("UPDATE jobwatch.triage SET decision='dismissed'")
        self.assertEqual(self.scalar('SELECT decision FROM jobwatch.triage'), 'interested')

    def test_login_actor_multirow_and_audit_rollback(self):
        key, _, _ = self.seed()
        other, _, _ = self.seed(native_id='2')
        before = self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        with self.target.connect('jobwatch_owner') as conn:
            with conn.transaction():
                actor(conn, 'agent:test/session-1')
                conn.execute("INSERT INTO jobwatch.triage(posting_key,decision,recorded_by) VALUES "
                    "(%s,'interested','agent_on_owner_instruction'),(%s,'later','agent_on_owner_instruction')", (key, other))
            rows = conn.execute("SELECT * FROM jobwatch.owner_audit WHERE table_name='triage'").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({r['db_login'] for r in rows}, {'jobwatch_owner'})
            self.assertEqual({r['actor'] for r in rows}, {'agent:test/session-1'})
            self.assertEqual(len({r['transaction_id'] for r in rows}), 1)
            with self.assertRaises(psycopg.errors.DivisionByZero):
                with conn.transaction():
                    actor(conn)
                    conn.execute("UPDATE jobwatch.triage SET decision='dismissed'")
                    self.assertEqual(conn.execute('SELECT count(*) AS n FROM jobwatch.owner_audit').fetchone()['n'], before + 4)
                    conn.execute('SELECT 1/0')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'), before + 2)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.triage WHERE decision='dismissed'"), 0)

    def test_control_coverage_private_recovery_and_no_collector_audit(self):
        key, _, attempt = self.seed()
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("UPDATE jobwatch.employers SET notes='private fixture note'")
            conn.execute("UPDATE jobwatch.sources SET status='paused'")
            conn.execute("INSERT INTO jobwatch.leads(lead_key,kind,company,title,state,url,official,source_note,checked_at,checked_by) "
                "VALUES ('vacancy:test','vacancy','Test','Grad','unverified','https://example.org/lead',true,'official',now(),'agent')")
            conn.execute("INSERT INTO jobwatch.applications(job_key,company,role,url) VALUES ('manual:test','Test','Grad','https://example.org/application')")
            conn.execute("INSERT INTO jobwatch.triage(posting_key,decision,note,recorded_by) VALUES (%s,'later','private fixture note','owner')", (key,))
            rows = conn.execute('SELECT table_name FROM jobwatch.owner_audit').fetchall()
            self.assertEqual({r['table_name'] for r in rows}, {'employers', 'sources', 'leads', 'applications', 'triage'})
            self.assertEqual(conn.execute("SELECT new_row->>'note' AS note FROM jobwatch.owner_audit WHERE table_name='triage'").fetchone()['note'], 'private fixture note')
        before = self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        with self.target.connect('jobwatch_collector') as conn:
            # These are deliberately outside generic audit; no actor setting is needed for a pointer-only update.
            conn.execute('UPDATE jobwatch.sources SET last_published_run_id=%s,processing_revision=%s WHERE id=%s',
                         (attempt['id'], 'pipeline-v2', self.source))
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute('UPDATE jobwatch.triage SET decision=decision')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'), before)


class TransactionTests(DatabaseTest):
    def test_related_corpus_updates_rollback_together(self):
        key, before_vid, _ = self.seed()
        with self.target.connect('jobwatch_collector') as conn, conn.transaction():
            actor(conn)
            attempt = run(conn, self.source)  # a previously committed registration survives a failed publication
        with self.target.connect('jobwatch_collector') as conn:
            with self.assertRaises(psycopg.errors.DivisionByZero):
                with conn.transaction():
                    actor(conn)
                    conn.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE', (self.source,))
                    after_vid = version(conn, key, self.source, attempt['id'], description='Changed evidence')
                    conn.execute('UPDATE jobwatch.postings SET current_version_id=%s WHERE posting_key=%s', (after_vid, key))
                    conn.execute("INSERT INTO jobwatch.posting_events(posting_key,source_id,run_id,observed_at,category,kind,before_version_id,after_version_id,comparison_version) "
                        "VALUES (%s,%s,%s,now(),'provider','changed',%s,%s,'material-v1')", (key, self.source, attempt['id'], before_vid, after_vid))
                    conn.execute("UPDATE jobwatch.collection_runs SET status='published',fetched_at=now(),finished_at=now(),source_records=1,"
                        "vacancies=1,skipped_non_vacancies=0,absence_safe=true WHERE id=%s", (attempt['id'],))
                    conn.execute('UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s', (attempt['id'], self.source))
                    conn.execute('SELECT 1/0')
        self.assertEqual(self.scalar('SELECT current_version_id FROM jobwatch.postings'), before_vid)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'), 0)
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.collection_runs WHERE id=%s', (attempt['id'],)), 'running')
        self.assertIsNone(self.scalar('SELECT last_published_run_id FROM jobwatch.sources'))

    def test_source_lock_waiter_observes_committed_state(self):
        def wait_for_source():
            with self.target.connect('jobwatch_collector') as conn, conn.transaction():
                return conn.execute('SELECT processing_revision FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE',
                                    (self.source,)).fetchone()['processing_revision']

        with ThreadPoolExecutor(1) as pool, self.target.connect('jobwatch_collector') as conn:
            with conn.transaction():
                conn.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE', (self.source,))
                conn.execute("UPDATE jobwatch.sources SET processing_revision='pipeline-v2' WHERE id=%s", (self.source,))
                future = pool.submit(wait_for_source)
                deadline = time.monotonic() + 3
                with self.target.connect('postgres') as observer:
                    while time.monotonic() < deadline:
                        blocked = observer.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() "
                            "AND wait_event_type='Lock' AND query LIKE 'SELECT processing_revision FROM jobwatch.sources%'").fetchone()['n']
                        if blocked:
                            break
                        time.sleep(.01)
                    self.assertEqual(blocked, 1, 'Waiter must demonstrably block on the source row')
            self.assertEqual(future.result(5), 'pipeline-v2')

    def test_owner_fk_insert_during_collector_nonkey_update(self):
        key, _, _ = self.seed()

        def owner_write():
            with self.target.connect('jobwatch_owner') as conn, conn.transaction():
                actor(conn)
                conn.execute("SET LOCAL statement_timeout='1s'")
                conn.execute("INSERT INTO jobwatch.triage(posting_key,decision,recorded_by) VALUES (%s,'interested','owner')", (key,))

        with ThreadPoolExecutor(1) as pool, self.target.connect('jobwatch_collector') as conn:
            with conn.transaction():
                conn.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE', (self.source,))
                conn.execute('UPDATE jobwatch.postings SET screened_at=clock_timestamp() WHERE posting_key=%s', (key,))
                pool.submit(owner_write).result(3)
        self.assertEqual(self.scalar('SELECT decision FROM jobwatch.triage'), 'interested')
