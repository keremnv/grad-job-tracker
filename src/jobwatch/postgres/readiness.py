"""Concrete deployment diagnostics, never a hosted-deployment authorization or policy registry."""
import json

from .importer import check_migrations,logical_snapshot,schema_snapshot,primitive
from .import_plan import digest
from .collection import iso

EXTERNAL_GATES = (
    'Hosted database identity and approved encrypted transport verified (TLS or authenticated SSM with local peer/SCRAM)',
    'Restore capability rehearsed in that actual hosting environment',
    'Owner explicitly accepts unarchived native responses and unretained schema-1 exclusions',
    'Production connection secrets and the replacement schedule prepared separately',
    'Freeze window, final authoritative Git commit and stopped in-flight/owner writes confirmed',
    'Single-writer switch and post-write recovery approved; no automatic return to frozen files',
)


def inspect(conn,*,reconciliation=None,restore=None,phase='baseline'):
    if phase not in ('baseline','operational'):raise ValueError('Use baseline or operational readiness')
    checks=[]
    def record(name,passed,**detail):
        checks.append({'check':name,'ok':bool(passed),**detail})
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        now=iso(conn.execute('SELECT current_timestamp AS at').fetchone()['at'])
        version=conn.execute("SELECT current_setting('server_version_num')::int AS n,current_setting('server_version') AS version").fetchone()
        record('tested_postgresql_major',170000<=version['n']<180000,version=version['version'])
        check_migrations(conn);record('migration_checksums',True)
        roles=conn.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname IN ('jobwatch_owner','jobwatch_collector') ORDER BY rolname").fetchall()
        record('runtime_role_flags',len(roles)==2 and not any(r[k] for r in roles for k in ('rolsuper','rolcreatedb','rolcreaterole')))
        permissions=conn.execute("""SELECT
          NOT has_schema_privilege('jobwatch_owner','jobwatch','CREATE') AND NOT has_schema_privilege('jobwatch_collector','jobwatch','CREATE') AS no_schema_create,
          NOT has_database_privilege('jobwatch_owner',current_database(),'CREATE') AND NOT has_database_privilege('jobwatch_collector',current_database(),'CREATE') AS no_database_schema_create,
          NOT pg_has_role('jobwatch_owner',(SELECT nspowner FROM pg_namespace WHERE nspname='jobwatch'),'MEMBER')
            AND NOT pg_has_role('jobwatch_collector',(SELECT nspowner FROM pg_namespace WHERE nspname='jobwatch'),'MEMBER') AS not_schema_owner_member,
          NOT has_table_privilege('jobwatch_collector','jobwatch.applications','SELECT') AND NOT has_table_privilege('jobwatch_collector','jobwatch.triage','SELECT')
            AND NOT has_table_privilege('jobwatch_collector','jobwatch.owner_audit','SELECT') AS private_reads_denied,
          NOT has_column_privilege('jobwatch_collector','jobwatch.employers','details','SELECT') AS private_import_archive_denied,
          NOT has_any_column_privilege('jobwatch_owner','jobwatch.posting_versions','UPDATE') AND NOT has_any_column_privilege('jobwatch_collector','jobwatch.posting_versions','UPDATE')
            AND NOT has_table_privilege('jobwatch_owner','jobwatch.posting_versions','DELETE') AND NOT has_table_privilege('jobwatch_collector','jobwatch.posting_versions','DELETE') AS immutable_evidence,
          has_table_privilege('jobwatch_owner','jobwatch.operational_postings','SELECT') AND has_table_privilege('jobwatch_collector','jobwatch.research_postings','SELECT') AS research_reads,
          has_column_privilege('jobwatch_collector','jobwatch.postings','last_seen_run_id','UPDATE') AND has_column_privilege('jobwatch_collector','jobwatch.collection_runs','details','INSERT') AS publication_grants
        """).fetchone()
        for name,ok in permissions.items():record(name,ok)
        counts={t:conn.execute('SELECT count(*) AS n FROM jobwatch.'+t).fetchone()['n'] for t in ('postings','research_postings','opportunity_workflow','operational_postings')}
        record('report_views_cover_corpus',len(set(counts.values()))==1,counts=counts)
        conn.execute("SELECT count(*) FROM jobwatch.research_postings WHERE search_tsv @@ websearch_to_tsquery('english','software')").fetchone()
        record('full_text_query',True)
        anomalies=conn.execute("SELECT count(*) AS n FROM jobwatch.research_postings WHERE coverage_state='inconsistent_presence'").fetchone()['n']
        record('observation_consistency',anomalies==0,count=anomalies)
        sources=conn.execute("SELECT s.id,s.provider,s.board,s.status,s.endpoint_checked_at,s.mapping_verified_at,"
            "coalesce((e.details->'legacy_config'->>'enabled')::boolean,false) AS legacy_enabled FROM jobwatch.sources s LEFT JOIN jobwatch.employers e ON e.id=s.employer_id ORDER BY s.provider,s.board").fetchall()
        required=[s for s in sources if s['legacy_enabled'] or s['status']=='monitored']
        pending=[s['provider']+':'+s['board'] for s in required if s['status']!='monitored' or not s['endpoint_checked_at']]
        record('intended_sources_activated',bool(required) and not pending,pending=pending)
        endpoint_checks=conn.execute("SELECT DISTINCT ON(source_id) source_id,id,status,fetched_at,details->>'evidence_kind' AS evidence_kind "
            "FROM jobwatch.collection_runs WHERE mode='validate' ORDER BY source_id,run_order DESC").fetchall()
        required_ids={s['id'] for s in required}
        unhealthy=[str(r['source_id']) for r in endpoint_checks if r['source_id'] in required_ids and r['status']!='validated']
        record('no_unresolved_endpoint_check',not unhealthy,source_ids=unhealthy)
        health=conn.execute('SELECT provider,board,status,last_success_at,last_success_mode,last_attempt_status,absence_safe,stale FROM jobwatch.source_health ORDER BY provider,board').fetchall()
        # Freshness is operational state, not a claim that an import rehearsed a live scan.
        freshness=[{'source':h['provider']+':'+h['board'],'stale':h['stale'],'latest_attempt':h['last_attempt_status'],
                    'last_success_mode':h['last_success_mode'],'absence_safe':h['absence_safe']} for h in health]
        archive=conn.execute("SELECT details->>'legacy_import_json' AS archive FROM jobwatch.employers WHERE details ? 'legacy_import_json'").fetchall()
        imported=json.loads(archive[0]['archive']) if len(archive)==1 else None
        source_commit=imported.get('commit') if imported else None
        current=logical_snapshot(conn,derived=True)
        basic={**current,'posting_versions':[{k:v for k,v in r.items() if k!='search_tsv'} for r in current['posting_versions']]}
        logical=digest(basic)
        current_schema=digest(schema_snapshot(conn))
        good_reconciliation=bool(reconciliation and imported and reconciliation.get('source_commit')==source_commit
            and reconciliation.get('files')==imported.get('files') and reconciliation.get('reconciliation',{}).get('ok')
            and not reconciliation.get('reconciliation',{}).get('failures'))
        record('frozen_import_reconciliation',good_reconciliation,source_commit=source_commit,
            note='Evidence binds to the frozen import archive; this is not a claim that later mutable state is unchanged.')
        baseline_equal=bool(good_reconciliation and reconciliation['reconciliation'].get('logical_sha256')==logical)
        retained=baseline_equal
        if phase=='operational' and good_reconciliation:
            # Reuse Stage 3 per-row hashes, not a replay or reverse synchronization.
            # Current mutable workflow/observations may evolve; imported immutable
            # evidence/runs/events/checks, identities and initial audit images may not disappear.
            retained=True
            exact=reconciliation['reconciliation'].get('exact_matches',{})
            for table,key in (('posting_versions','id'),('posting_events','id'),('collection_runs','id'),('verifications','id')):
                by_id={str(r[key]):digest(r) for r in basic[table]}
                proof=exact.get(table,{})
                retained &= len(proof.get('rows',[]))==reconciliation.get('counts',{}).get(table,-1)
                retained &= all(r.get('status')=='exact' and r.get('expected_sha256')==r.get('actual_sha256')==by_id.get(str(r['identity'])) for r in proof.get('rows',[]))
            baseline_keys={r['identity'] for r in exact.get('postings',{}).get('rows',[])}
            retained &= len(baseline_keys)==reconciliation.get('counts',{}).get('postings',-1) and baseline_keys<={r['posting_key'] for r in basic['postings']}
            audit=[r for r in basic['owner_audit'] if r['actor']=='import:'+source_commit and r['op']=='INSERT']
            retained &= exact.get('owner_audit',{}).get('sha256')==digest(audit)
        record('import_baseline_retained',retained,phase=phase,current_exact_baseline=baseline_equal)
        good_restore=bool(restore and good_reconciliation and retained and restore.get('source_commit')==source_commit
            and restore.get('plan_sha256')==reconciliation.get('plan_sha256')
            and all(restore.get(k) is True for k in ('independent_imports_equal','restored_matches_import','restored_matches_source',
                 'schema_ownership_grants_equal','sequence_state_equal','runtime_queries_and_denials_passed'))
            and restore.get('schema_sha256')==current_schema
            and (phase=='operational' or restore.get('logical_sha256')==digest(current))
            and restore.get('backup',{}).get('format')=='PostgreSQL custom archive')
        record('local_restore_rehearsal',good_restore,note='Local proof only, matching this schema/baseline; not evidence of hosted restore/PITR.')
    return {'schema':'jobwatch.postgres.readiness/1','generated_at':now,'checks':checks,
        'local_checks_passed':all(c['ok'] for c in checks),'source_health':primitive(health),'freshness':freshness,
        'endpoint_checks':primitive(endpoint_checks),
        'external_gates':[{'requirement':g,'status':'not_verified_by_this_command'} for g in EXTERNAL_GATES],
        'deployment_ready':False,'note':'Readiness diagnostics never provision, activate, accept owner limitations, or authorize cutover.'}
