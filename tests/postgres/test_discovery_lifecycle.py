"""Disposable PostgreSQL tests for native Workable collection, discovery lifecycle, and Research MCP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import unittest

try:
    import psycopg
except ImportError:
    psycopg = None

from jobwatch.discovery import DiscoveredVacancy
from jobwatch.postgres import discovery, reports, research
from jobwatch.postgres.collection import Processing, fetch
from jobwatch.postgres.evidence import encode_evidence
from jobwatch.postgres.publisher import Collector
from jobwatch.postgres.read_tools import ReadTools
from support import DatabaseTest, actor, source

ROOT = Path(__file__).resolve().parents[2]


def _workable_widget_payload(description='<p>Python &amp; AI graduate role.</p>'):
    base = {
        'title': 'Data Science & AI Graduate',
        'shortcode': '1C9CCEA6B4',
        'employment_type': 'Full-time',
        'telecommuting': True,
        'department': 'Engineering',
        'url': 'https://apply.workable.com/j/1C9CCEA6B4',
        'shortlink': 'https://apply.workable.com/j/1C9CCEA6B4',
        'application_url': 'https://apply.workable.com/j/1C9CCEA6B4/apply',
        'published_on': '2026-11-23',
        'created_at': '2026-11-18',
        'description': description,
    }
    return {
        'name': 'Stalis',
        'jobs': [
            {**base, 'country': 'Ireland', 'locations': [{'country': 'Ireland', 'countryCode': 'IE'}]},
            {**base, 'country': 'United Kingdom', 'locations': [{'country': 'United Kingdom', 'countryCode': 'GB'}]},
        ],
    }


def _vacancy(
    candidate_key='discovery:workable:u-1',
    *,
    provider='workable',
    board=None,
    native_id=None,
    company='Stalis',
    title='Data Science & AI Graduate',
    posting_url='https://jobs.workable.com/view/abc/stalis-grad',
    location_text='United Kingdom, Remote',
    country='United Kingdom',
    countries=('United Kingdom',),
    description_full='Graduate Python and machine learning role.',
    mechanism='workable_search_v1',
):
    ev = sha256(encode_evidence({'k': candidate_key, 'u': posting_url, 'm': mechanism})).hexdigest()
    return DiscoveredVacancy(
        candidate_key=candidate_key,
        provider=provider,
        board=board,
        native_id=native_id,
        company=company,
        title=title,
        posting_url=posting_url,
        location_text=location_text,
        country=country,
        countries=countries,
        department='Engineering',
        team='',
        employment_type='Full-time',
        workplace_type='remote',
        published_at='2026-11-23',
        source_dates={'published_on': '2026-11-23'},
        deadlines=[],
        description_full=description_full,
        description_excerpt=description_full[:500],
        description_sha256=sha256(description_full.encode()).hexdigest(),
        evidence_sha256=ev,
        mechanism=mechanism,
        provenance={'query': 'graduate ai', 'authoritative_board_observation': False},
    )


class WorkableAndDiscoveryLifecycleTests(DatabaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        container = os.environ['JOBWATCH_PG_TEST_CONTAINER']
        with cls.target.connect() as conn:
            has_agent = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jobwatch_agent_readonly') AS ok").fetchone()['ok']
        if not has_agent:
            p = subprocess.run(
                ['docker', 'exec', '-i', container, 'psql', '-X', '-U', 'postgres',
                 '-d', 'jobwatch_foundation_test', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                input=(ROOT / 'sql/postgresql/agent_roles.sql').read_text(),
                text=True, capture_output=True,
            )
            if p.returncode:
                raise RuntimeError(p.stderr)
        for filename in ('agent_access.sql', 'lead_deadline_access.sql',
                         'posting_deadline_access.sql', 'calendar_selection_access.sql', 'discovery_access.sql'):
            p = subprocess.run(
                ['docker', 'exec', '-i', container, 'psql', '-X', '-U', 'postgres',
                 '-d', 'jobwatch_foundation_test', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                input=(ROOT / 'sql/postgresql' / filename).read_text(),
                text=True, capture_output=True,
            )
            if p.returncode:
                raise RuntimeError(p.stderr)

    def test_native_workable_collection_multi_country_and_material_history(self):
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            emp_id = conn.execute("INSERT INTO jobwatch.employers(slug,name) VALUES ('stalis','Stalis') RETURNING id").fetchone()['id']
            sid = conn.execute(
                "INSERT INTO jobwatch.sources(provider,board,region,employer_id,status,validation_url,"
                "endpoint_checked_at,mapping_verified_at,mapping_verified_by,mapping_evidence) "
                "VALUES ('workable','stalis','global',%s,'monitored','https://stalis.com/careers/',"
                "now(),now(),'test','official workable embed') RETURNING id",
                (emp_id,),
            ).fetchone()['id']

        collector = Collector(self.target.config('jobwatch_collector'), actor='collector:workable-test')
        run1 = collector.register(sid)
        obs1 = fetch(run1, collector.processing, get_json=lambda _: _workable_widget_payload())
        self.assertTrue(obs1.absence_safe)
        self.assertEqual(collector.publish(obs1), 'published')

        # Formatting-only HTML change creates zero material provider events.
        run2 = collector.register(sid)
        obs2 = fetch(run2, collector.processing, get_json=lambda _: _workable_widget_payload('<p class="x">Python &amp; AI graduate role.</p><p>&nbsp;</p>'))
        self.assertEqual(collector.publish(obs2), 'published')
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_events WHERE category='provider'"), 0)

        # Real wording change emits one provider/changed event.
        run3 = collector.register(sid)
        obs3 = fetch(run3, collector.processing, get_json=lambda _: _workable_widget_payload('<p>Python, SQL &amp; LLM systems.</p>'))
        self.assertEqual(collector.publish(obs3), 'published')
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_events WHERE category='provider' AND kind='changed'"), 1)

        with self.target.connect('jobwatch_owner') as conn:
            row = conn.execute("SELECT * FROM jobwatch.research_postings WHERE posting_key='workable:stalis:1C9CCEA6B4'").fetchone()
            self.assertEqual(row['verdict'], 'priority')
            self.assertEqual(row['family'], 'ml_ai')
            self.assertIn('location.uk_multi_country', row['codes'])

    def test_discovery_lifecycle_dedup_linking_promotion_and_mcp_visibility(self):
        t0 = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=6)

        # 1. Collector records initial unverified search discovery (state='new')
        v_initial = _vacancy()
        with self.target.connect('jobwatch_collector') as conn:
            c1 = discovery.record_discoveries(conn, [v_initial], observed_at=t0)
        self.assertEqual(c1['inserted'], 1)
        self.assertEqual(self.scalar("SELECT state FROM jobwatch.discovery_candidates WHERE candidate_key='discovery:workable:u-1'"), 'new')

        # 2. Second mechanism observes the same URL with verified native identity -> merges & upgrades state
        v_verified = _vacancy(
            candidate_key='workable:stalis:1C9CCEA6B4',
            board='stalis',
            native_id='1C9CCEA6B4',
            posting_url=v_initial.posting_url,  # same exact URL proves identity merge across mechanisms
            mechanism='workable_widget_corroboration_v1',
        )
        with self.target.connect('jobwatch_collector') as conn:
            c2 = discovery.record_discoveries(conn, [v_verified], observed_at=t1)
        self.assertEqual(c2['updated'], 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.discovery_candidates'), 1)
        with self.target.connect('jobwatch_owner') as conn:
            row = conn.execute('SELECT * FROM jobwatch.research_discovery_candidates').fetchone()
        self.assertEqual(row['state'], 'verified_identity')
        self.assertEqual(row['observation_count'], 2)
        self.assertEqual(set(row['discovery_mechanisms']), {'workable_search_v1', 'workable_widget_corroboration_v1'})
        self.assertTrue(row['unknown_employer_board'])

        # 3. Research MCP (`jobwatch_agent_readonly`) sees the unknown-employer vacancy via SQL and daily report,
        #    and CANNOT mutate discovery_candidates or leads.
        read = ReadTools(self.target.config('jobwatch_agent_readonly'), shared=True)
        q = read.call('sql_query', {
            'sql': "SELECT candidate_key, company, title, verdict, unknown_employer_board FROM jobwatch.research_discovery_candidates WHERE verdict='priority'",
            'limit': 10,
        })
        self.assertTrue(q['ok'], q)
        self.assertEqual(len(q['data']['rows']), 1)
        self.assertEqual(q['data']['rows'][0][1], 'Stalis')
        self.assertTrue(q['data']['rows'][0][4])

        rep = read.call('run_report', {'kind': 'daily', 'limit': 20})
        self.assertTrue(rep['ok'], rep)
        self.assertIn('Newly discovered external vacancies', rep['data']['text'])
        self.assertIn('Stalis', rep['data']['text'])

        self.rejected(psycopg.errors.ReadOnlySqlTransaction, "DELETE FROM jobwatch.discovery_candidates", user='jobwatch_agent_readonly')
        self.rejected(psycopg.errors.ReadOnlySqlTransaction, "UPDATE jobwatch.discovery_candidates SET state='dismissed'", user='jobwatch_agent_readonly')
        # Even if a session explicitly attempts READ WRITE, table grants still reject mutation.
        with self.target.connect('jobwatch_agent_readonly') as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute('SET TRANSACTION READ WRITE')
                conn.execute("DELETE FROM jobwatch.discovery_candidates")

        # 4. Collector cannot promote to owner-curated leads; owner CAN promote with audit.
        with self.target.connect('jobwatch_collector') as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                discovery.promote_to_lead(conn, 'discovery:workable:u-1', actor='collector:bad')

        with self.target.connect('jobwatch_owner') as conn:
            promo = discovery.promote_to_lead(conn, 'discovery:workable:u-1', actor='owner:review', official=True)
        self.assertEqual(promo['status'], 'promoted')
        self.assertEqual(self.scalar("SELECT state FROM jobwatch.discovery_candidates WHERE candidate_key='discovery:workable:u-1'"), 'promoted_lead')
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.owner_audit WHERE table_name='leads'"), 1)

        # 5. Offline rescreen from excerpt works cleanly without network calls.
        with self.target.connect('jobwatch_collector') as conn:
            changed = discovery.rescreen_candidates(conn)
        self.assertEqual(changed, 1)  # full -> excerpt basis recorded honestly
        self.assertEqual(self.scalar("SELECT screening_basis FROM jobwatch.discovery_candidates WHERE candidate_key='discovery:workable:u-1'"), 'excerpt')

        # 6. Auto-linking to a newly collected monitored posting works for a separate non-promoted candidate.
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            emp_id = conn.execute("INSERT INTO jobwatch.employers(slug,name) VALUES ('lmax','LMAX Group') RETURNING id").fetchone()['id']
            sid = conn.execute(
                "INSERT INTO jobwatch.sources(provider,board,region,employer_id,status,validation_url,"
                "endpoint_checked_at,mapping_verified_at,mapping_verified_by,mapping_evidence) "
                "VALUES ('workable','lmax','global',%s,'monitored','https://careers.lmax.com/',"
                "now(),now(),'test','official lmax workable') RETURNING id",
                (emp_id,),
            ).fetchone()['id']
        col = Collector(self.target.config('jobwatch_collector'), actor='collector:lmax')
        r = col.register(sid)
        lmax_widget = {'name': 'LMAX Group', 'jobs': [{
            'title': 'Junior Software Developer (Java)', 'shortcode': '572A2F5CB1',
            'url': 'https://apply.workable.com/j/572A2F5CB1', 'country': 'United Kingdom',
            'city': 'London', 'description': '<p>Java developer in London.</p>', 'published_on': '2026-11-20',
        }]}
        col.publish(fetch(r, col.processing, get_json=lambda _: lmax_widget))
        v_lmax = _vacancy(
            candidate_key='workable:lmax:572A2F5CB1',
            board='lmax', native_id='572A2F5CB1', company='LMAX Group',
            title='Junior Software Developer (Java)',
            posting_url='https://apply.workable.com/j/572A2F5CB1',
        )
        with self.target.connect('jobwatch_collector') as conn:
            res = discovery.record_discoveries(conn, [v_lmax], observed_at=t1)
        self.assertEqual(res['linked'], 1)
        self.assertEqual(
            self.scalar("SELECT linked_posting_key FROM jobwatch.discovery_candidates WHERE candidate_key='workable:lmax:572A2F5CB1'"),
            'workable:lmax:572A2F5CB1',
        )
        self.assertFalse(
            self.scalar("SELECT unknown_employer_board FROM jobwatch.research_discovery_candidates WHERE candidate_key='workable:lmax:572A2F5CB1'")
        )

    def test_dotted_ashby_source_allowed_by_migration_0008(self):
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            sid = source(conn, board='iwoca.co.uk', provider='ashby', employer_id=self.employer)
        self.assertIsNotNone(sid)
        self.rejected(
            psycopg.errors.CheckViolation,
            "INSERT INTO jobwatch.sources(provider,board,region) VALUES ('workable','stalis','eu')",
        )


if __name__ == '__main__':
    unittest.main()
