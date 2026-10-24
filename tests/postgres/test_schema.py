"""Constraints, evidence, SQL views and runtime grants on the application migration."""
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

try:
    import psycopg
except ImportError:
    psycopg = None  # Unselected integration tests can be discovered without optional dependencies.

from jobwatch.identity import exact_url, loose_url
from jobwatch.postgres.evidence import encode_evidence
from support import DOMAIN_TABLES, DatabaseTest, actor, posting, run, source, version


class SchemaTests(DatabaseTest):
    def test_schema_objects_and_full_text_search(self):
        key, _, _ = self.seed(description='Distributed Python systems and compilers')
        with self.target.connect('jobwatch_owner') as conn:
            tables = {r['tablename'] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='jobwatch'")}
            self.assertEqual(tables, set(DOMAIN_TABLES) | {'schema_migrations'})
            views = {r['viewname'] for r in conn.execute("SELECT viewname FROM pg_views WHERE schemaname='jobwatch'")}
            self.assertEqual(views, {'source_health', 'research_postings', 'opportunity_workflow', 'operational_postings'})
            matches = conn.execute("SELECT posting_key,ts_rank_cd(search_tsv,websearch_to_tsquery('english','Python systems')) AS rank "
                "FROM jobwatch.research_postings WHERE search_tsv @@ websearch_to_tsquery('english','Python systems')").fetchall()
            self.assertEqual(matches[0]['posting_key'], key)
            self.assertGreater(matches[0]['rank'], 0)
            self.assertIsNotNone(conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='jobwatch' AND indexdef LIKE '%USING gin%'").fetchone())
            self.assertIsNone(conn.execute("SELECT extname FROM pg_extension WHERE extname='vector'").fetchone())

    def test_source_namespace_and_activation(self):
        self.rejected(psycopg.errors.UniqueViolation,
            "INSERT INTO jobwatch.sources(provider,board,region) VALUES ('greenhouse','Test','eu')")
        self.rejected(psycopg.errors.CheckViolation,
            "INSERT INTO jobwatch.sources(provider,board,status) VALUES ('lever','candidate','monitored')")
        self.rejected(psycopg.errors.CheckViolation,
            "INSERT INTO jobwatch.sources(provider,board,region) VALUES ('ashby','candidate','eu')")
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("UPDATE jobwatch.sources SET status='monitored',validation_url='https://example.org/careers',"
                "endpoint_checked_at=now(),mapping_verified_at=now(),mapping_verified_by='agent',mapping_evidence='official page' WHERE id=%s", (self.source,))

    def test_posting_identity_and_deferred_current_version(self):
        key, vid, _ = self.seed()
        self.rejected(psycopg.errors.CheckViolation,
            "UPDATE jobwatch.postings SET posting_key='greenhouse:wrong:1' WHERE posting_key=%s", params=(key,))
        self.rejected(psycopg.errors.ForeignKeyViolation,
            "UPDATE jobwatch.postings SET board='wrong',posting_key='greenhouse:wrong:1' WHERE posting_key=%s", params=(key,))
        self.rejected(psycopg.errors.ForeignKeyViolation,
            'UPDATE jobwatch.postings SET current_version_id=%s WHERE posting_key=%s', params=(uuid4(), key))
        with self.target.connect('jobwatch_collector') as conn, conn.transaction():
            actor(conn)
            attempt = run(conn, self.source)
            other, other_vid = posting(conn, self.source, attempt['id'], native_id='2')
        self.rejected(psycopg.errors.ForeignKeyViolation,
            'UPDATE jobwatch.postings SET current_version_id=%s WHERE posting_key=%s', params=(vid, other))

    def test_same_source_run_and_evidence_foreign_keys(self):
        key, _, first = self.seed()
        with self.target.connect() as conn, conn.transaction():
            actor(conn)
            sid = source(conn, board='other')
            second = run(conn, sid)
        self.rejected(psycopg.errors.ForeignKeyViolation,
            'UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s', params=(first['id'], sid))
        with self.target.connect('jobwatch_collector') as conn:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with conn.transaction():
                    actor(conn)
                    version(conn, key, self.source, second['id'], normalizer='v2')
        self.rejected(psycopg.errors.ForeignKeyViolation,
            "INSERT INTO jobwatch.posting_events(posting_key,source_id,run_id,observed_at,category,kind) "
            "VALUES (%s,%s,%s,now(),'import','legacy_event')", params=(key, self.source, second['id']))

    def test_material_event_references_and_retention(self):
        key, before, attempt = self.seed()
        other, other_vid, _ = self.seed(native_id='2')
        with self.target.connect('jobwatch_collector') as conn, conn.transaction():
            actor(conn)
            after = version(conn, key, self.source, attempt['id'], description='A real second wording')
            conn.execute('UPDATE jobwatch.postings SET current_version_id=%s WHERE posting_key=%s', (after, key))
            conn.execute("INSERT INTO jobwatch.posting_events(posting_key,source_id,run_id,observed_at,category,kind,"
                "before_version_id,after_version_id,comparison_version) VALUES (%s,%s,%s,now(),'provider','changed',%s,%s,'material-v1')",
                (key, self.source, attempt['id'], before, after))
        # Old evidence is no longer current, but its material event still prevents deletion.
        self.rejected(psycopg.errors.ForeignKeyViolation, 'DELETE FROM jobwatch.posting_versions WHERE id=%s', params=(before,))
        self.rejected(psycopg.errors.ForeignKeyViolation,
            "INSERT INTO jobwatch.posting_events(posting_key,source_id,observed_at,category,kind,after_version_id) "
            "VALUES (%s,%s,now(),'import','legacy_event',%s)", params=(key, self.source, other_vid))
        self.rejected(psycopg.errors.UniqueViolation,
            "INSERT INTO jobwatch.posting_events(posting_key,source_id,run_id,observed_at,category,kind,before_version_id,after_version_id,comparison_version) "
            "VALUES (%s,%s,%s,now(),'provider','changed',%s,%s,'material-v1')", params=(key, self.source, attempt['id'], before, after))

    def test_evidence_is_immutable_to_both_runtime_roles(self):
        self.seed()
        for user in ('jobwatch_owner', 'jobwatch_collector'):
            for sql in ("UPDATE jobwatch.posting_versions SET description_text='forged'",
                        "UPDATE jobwatch.posting_versions SET normalizer_version='forged'",
                        'DELETE FROM jobwatch.posting_versions', 'TRUNCATE jobwatch.posting_versions CASCADE'):
                with self.subTest(user=user, sql=sql):
                    self.rejected(psycopg.errors.InsufficientPrivilege, sql, user)

    def test_nul_source_evidence_and_reproducible_hash(self):
        raw = {'id': '1', 'description': 'before\0after\r\nExact wording', 'unicode': '£', 'nested': {'x': None}}
        self.seed(evidence=raw)
        with self.target.connect('jobwatch_owner') as conn:
            stored = conn.execute('SELECT evidence,evidence_sha256,description_text,nul_replaced FROM jobwatch.posting_versions').fetchone()
        self.assertEqual(bytes(stored['evidence']), encode_evidence(raw))
        self.assertEqual(stored['evidence_sha256'], sha256(encode_evidence(raw)).hexdigest())
        self.assertEqual(json.loads(bytes(stored['evidence'])), raw)
        self.assertEqual(stored['description_text'], 'before\ufffdafter\r\nExact wording')
        self.assertTrue(stored['nul_replaced'])
        self.rejected(psycopg.errors.UntranslatableCharacter, "SELECT '{\"x\":\"before\\u0000after\"}'::jsonb")

    def test_distinct_normalizer_versions_and_duplicate_rejection(self):
        raw = {'id': '1', 'description': '<p>Exact old output</p>'}
        key, before, attempt = self.seed(evidence=raw)
        with self.target.connect('jobwatch_collector') as conn, conn.transaction():
            actor(conn)
            after = version(conn, key, self.source, attempt['id'], evidence=raw, normalizer='v2', description='Exact old output')
            conn.execute("INSERT INTO jobwatch.posting_events(posting_key,source_id,observed_at,category,kind,before_version_id,"
                "after_version_id,comparison_version) VALUES (%s,%s,now(),'normalizer','renormalized',%s,%s,'material-v1')",
                (key, self.source, before, after))
        with self.target.connect() as conn:
            rows = conn.execute('SELECT evidence_sha256,normalizer_version,description_text FROM jobwatch.posting_versions ORDER BY normalizer_version').fetchall()
        self.assertEqual(len({r['evidence_sha256'] for r in rows}), 1)
        self.assertEqual([r['description_text'] for r in rows], ['<p>Exact old output</p>', 'Exact old output'])
        with self.target.connect('jobwatch_collector') as conn:
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    actor(conn)
                    version(conn, key, self.source, attempt['id'], evidence=raw)

    def test_actual_provider_variants_remain_retrievable(self):
        pairs = json.loads(Path(__file__).with_name('fixtures').joinpath('source_pairs.json').read_text())
        for pair in pairs['ashby'] + [pairs['greenhouse']]:
            provider, board, native_id = pair['key'].split(':')
            with self.subTest(key=pair['key']), self.target.connect() as conn, conn.transaction():
                actor(conn)
                existing = conn.execute('SELECT id FROM jobwatch.sources WHERE provider=%s AND board=%s', (provider, board)).fetchone()
                sid = existing['id'] if existing else source(conn, board, provider)
                attempt = run(conn, sid)
                key, before = posting(conn, sid, attempt['id'], native_id, provider, board, evidence=pair['before'])
                after = version(conn, key, sid, attempt['id'], evidence=pair['after'])
                stored = conn.execute('SELECT id,evidence FROM jobwatch.posting_versions WHERE id=ANY(%s::uuid[])', ([before, after],)).fetchall()
                actual = {row['id']: json.loads(bytes(row['evidence'])) for row in stored}
                self.assertEqual(actual[before], pair['before'])
                self.assertEqual(actual[after], pair['after'])
                self.assertNotEqual(encode_evidence(actual[before]), encode_evidence(actual[after]))
        # This checks preservation, not a new material-comparison/normalization implementation.

    def test_run_order_accounting_and_import_baselines(self):
        with self.target.connect('jobwatch_collector') as conn, conn.transaction():
            actor(conn)
            first, second = run(conn, self.source), run(conn, self.source)
            self.assertLess(first['run_order'], second['run_order'])
            self.assertFalse(first['absence_safe'])
        self.rejected(psycopg.errors.CheckViolation,
            "UPDATE jobwatch.collection_runs SET status='published',finished_at=now() WHERE id=%s", params=(first['id'],))
        with self.target.connect() as conn, conn.transaction():
            actor(conn)
            rid = uuid4()
            conn.execute("INSERT INTO jobwatch.collection_runs(id,source_id,mode,status,finished_at,request_version,processing_revision,vacancies) "
                "VALUES (%s,%s,'import','imported',now(),'legacy','legacy',7)", (rid, self.source))
            conn.execute('UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s', (rid, self.source))
            self.assertEqual(conn.execute('SELECT vacancies FROM jobwatch.source_health').fetchone()['vacancies'], 7)

    def test_runtime_grants_and_private_data_boundary(self):
        self.seed()
        for sql in ('SELECT * FROM jobwatch.applications', 'SELECT * FROM jobwatch.triage',
                    'SELECT * FROM jobwatch.verifications', 'SELECT * FROM jobwatch.leads',
                    'SELECT * FROM jobwatch.owner_audit', 'SELECT notes,details FROM jobwatch.employers',
                    'SELECT * FROM jobwatch.opportunity_workflow', 'SELECT * FROM jobwatch.schema_migrations'):
            self.rejected(psycopg.errors.InsufficientPrivilege, sql, 'jobwatch_collector')
        for user in ('jobwatch_owner', 'jobwatch_collector'):
            for sql in ('CREATE TABLE public.forbidden(id int)', 'CREATE TABLE jobwatch.forbidden(id int)',
                        'ALTER TABLE jobwatch.posting_versions DISABLE TRIGGER ALL',
                        'SET ROLE jobwatch_migrator', 'CREATE ROLE forbidden', 'DELETE FROM jobwatch.owner_audit',
                        'SELECT * FROM jobwatch.schema_migrations'):
                self.rejected(psycopg.errors.InsufficientPrivilege, sql, user)
        for sql in ('UPDATE jobwatch.sources SET last_published_run_id=NULL',
                    "UPDATE jobwatch.postings SET verdict='priority'", 'DELETE FROM jobwatch.postings',
                    "INSERT INTO jobwatch.sources(provider,board,last_published_run_id) VALUES ('lever','bad',NULL)"):
            self.rejected(psycopg.errors.InsufficientPrivilege, sql, 'jobwatch_owner')
        with self.target.connect('jobwatch_collector') as conn:
            self.assertIsNotNone(conn.execute('SELECT name FROM jobwatch.employers').fetchone())
            self.assertIsNotNone(conn.execute('SELECT * FROM jobwatch.research_postings').fetchone())

    def test_url_identity_matches_existing_python(self):
        urls = ['https://EXAMPLE.com:443', 'https://example.com:0443/x', 'https://example.com:/x',
                'https://example.com?', 'https://example.com#', 'https://example.com/a?#',
                'https://example.com/a?x=1#fragment', 'https://example.com/a#fragment?x=1',
                'https://[::1]:443/a', 'https://[2001:db8::1]/a', 'https://bücher.example/a@b']
        with self.target.connect() as conn:
            for url in urls:
                with self.subTest(url=url):
                    result = conn.execute('SELECT jobwatch.url_identity(%s,true) AS exact,jobwatch.url_identity(%s,false) AS loose', (url, url)).fetchone()
                    self.assertEqual(result, {'exact': exact_url(url), 'loose': loose_url(url)})
        for url in ('https://[:::]/a', 'https://example.com:80/a', 'https://user:pass@example.com/a'):
            self.rejected(psycopg.errors.RaiseException, 'SELECT jobwatch.url_identity(%s,true)', params=(url,))

    def test_manual_application_identity_and_later_link(self):
        key, _, _ = self.seed(url='https://example.org/job#one')
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("INSERT INTO jobwatch.applications(job_key,company,role,url,stage) VALUES "
                         "('manual:stable','Test','Graduate engineer','https://EXAMPLE.org:443/job#one','preparing')")
            self.assertEqual(conn.execute('SELECT application_job_key FROM jobwatch.opportunity_workflow').fetchone()['application_job_key'], 'manual:stable')
            conn.execute("UPDATE jobwatch.applications SET posting_key=%s WHERE job_key='manual:stable'", (key,))
        self.assertEqual(self.scalar('SELECT job_key FROM jobwatch.applications'), 'manual:stable')
        self.rejected(psycopg.errors.UniqueViolation,
            "INSERT INTO jobwatch.applications(job_key,company,role,url) VALUES ('manual:duplicate','Test','Grad','https://example.org/job#two')", 'jobwatch_owner')

    def test_workflow_precedence_excluded_reads_and_exact_url_ambiguity(self):
        key, _, _ = self.seed(url='https://example.org/shared')
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("INSERT INTO jobwatch.triage(posting_key,decision,recorded_by) VALUES (%s,'dismissed','owner')", (key,))
            conn.execute("INSERT INTO jobwatch.applications(job_key,company,role,url,stage) VALUES ('manual:test','Test','Grad','https://example.org/shared','applied')")
            self.assertEqual(conn.execute('SELECT action_state FROM jobwatch.opportunity_workflow').fetchone()['action_state'], 'applied')
        self.seed(native_id='2', url='https://example.org/shared')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.opportunity_workflow WHERE application_job_key IS NOT NULL'), 0)
        with self.target.connect('jobwatch_collector') as conn:
            conn.execute("UPDATE jobwatch.postings SET verdict='excluded'")
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.research_postings WHERE verdict='excluded'"), 2)

    def test_programme_cycles_and_verification_binding(self):
        key, vid, _ = self.seed()
        other, _, _ = self.seed(native_id='2')
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("INSERT INTO jobwatch.leads(lead_key,kind,company,title,programme_key,cycle_year,cycle_wording,state,url,official,source_note,checked_at,checked_by) "
                "VALUES ('programme:test:2027','programme','Test','Graduate','test:grad',2027,'2027 intake','open','https://example.org/programme',true,'official',now(),'agent')")
        self.rejected(psycopg.errors.CheckViolation, 'UPDATE jobwatch.leads SET cycle_year=2028', 'jobwatch_owner')
        self.rejected(psycopg.errors.ForeignKeyViolation,
            "INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,version_id) "
            "VALUES (%s,'description',now(),'agent','https://example.org',%s)", 'jobwatch_owner', (other, vid))
        self.rejected(psycopg.errors.CheckViolation,
            "INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording) "
            "VALUES (%s,'deadline',now()+interval '1 day','agent','https://example.org','Apply tomorrow')", 'jobwatch_owner', (key,))
