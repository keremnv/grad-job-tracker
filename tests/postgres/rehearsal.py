"""Verified disposable-container rehearsal of the real importer and PostgreSQL backup tools.

This is development/test infrastructure, not an operational database provisioner.
"""
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from jobwatch.postgres.connection import connect
from jobwatch.postgres.git_snapshot import ImportProblem
from jobwatch.postgres.import_cli import summary
from jobwatch.postgres.import_plan import build_plan, digest
from jobwatch.postgres.importer import apply_import, reconcile, logical_snapshot, schema_snapshot
from jobwatch.postgres.migrate import migrate
from jobwatch.postgres import reports as operational_reports,research,readiness
from jobwatch.postgres.publisher import Collector
from jobwatch.postgres.orchestrator import collect_monitored
from support import Target


def rehearse(repo, commit, output_dir=None):
    import psycopg
    from psycopg.conninfo import make_conninfo
    target = Target.verified()
    container = os.environ['JOBWATCH_PG_TEST_CONTAINER']
    plan = build_plan(repo,commit)
    token = uuid4().hex
    databases = ['jobwatch_import_'+token+'_'+suffix for suffix in ('a','repeat','restored')]
    archive = '/tmp/jobwatch-import-'+token+'.dump'
    commands=[]
    def utility(*args):
        commands.append(list(args))
        result = subprocess.run(['docker','exec',container,*args],capture_output=True)
        if result.returncode:
            raise ImportProblem('Disposable PostgreSQL utility failed: '+args[0]+' (private stderr omitted)')
        return result.stdout
    def config(db,user='jobwatch_migrator'):
        # All addressing originates in Target.verified(), never an arbitrary caller URL.
        original=target.config(user)
        return replace(original,dsn=make_conninfo(original.dsn,dbname=db))
    reports={}
    try:
        for name in databases:
            utility('createdb','-U','postgres','-O','jobwatch_migrator',name)
        snapshots,schemas={},{}
        import_role=None
        for name in databases[:2]:
            with connect(config(name)) as conn:
                import_role=dict(conn.execute('SELECT current_user AS name,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone())
                if any(import_role[k] for k in ('rolsuper','rolcreatedb','rolcreaterole')):
                    raise ImportProblem('Rehearsal must import with ordinary migration-owner privileges')
                migrate(conn)
                reports[name]=apply_import(conn,plan)
                checked=reconcile(conn,plan)
                if not checked['reconciliation']['ok']:
                    raise ImportProblem('Independent reconciliation failed')
                snapshots[name]=logical_snapshot(conn,derived=True)
                schemas[name]=schema_snapshot(conn)
        if snapshots[databases[0]] != snapshots[databases[1]] or schemas[databases[0]] != schemas[databases[1]]:
            raise ImportProblem('Independent imports differ logically or structurally')
        utility('pg_dump','-U','jobwatch_migrator','--format=custom','--schema=jobwatch','--file='+archive,databases[0])
        listing=utility('pg_restore','--list',archive)
        if b'TABLE DATA jobwatch postings' not in listing or b'ACL jobwatch' not in listing:
            raise ImportProblem('Backup lacks expected data/grant entries')
        backup=utility('cat',archive)
        with connect(config(databases[2])) as conn:
            if conn.execute("SELECT to_regnamespace('jobwatch') AS n").fetchone()['n'] is not None:
                raise ImportProblem('Restore target is not fresh')
        utility('pg_restore','-U','jobwatch_migrator','--exit-on-error','--single-transaction','--dbname='+databases[2],archive)
        with connect(config(databases[2])) as conn:
            if migrate(conn):
                raise ImportProblem('Restored migration ledger unexpectedly needed changes')
            reports[databases[2]]=reconcile(conn,plan)
            if not reports[databases[2]]['reconciliation']['ok']:
                raise ImportProblem('Restored data does not reconcile to frozen Git input')
            restored=logical_snapshot(conn,derived=True)
            restored_schema=schema_snapshot(conn)
            if restored != snapshots[databases[0]] or restored_schema != schemas[databases[0]]:
                different_tables=[t for t in restored if restored[t]!=snapshots[databases[0]][t]]
                different_schema=[t for t in restored_schema if restored_schema[t]!=schemas[databases[0]][t]]
                constraints=[(a,b) for a,b in zip(schemas[databases[0]]['constraints'],restored_schema['constraints']) if a!=b]
                raise ImportProblem('Restore mismatch; tables='+','.join(different_tables)+'; schema='+','.join(different_schema)+'; constraints='+json.dumps(constraints[:2]))
        # Exact sequence state must survive a dump, even though independent imports may allocate differently.
        sequence_states=[]
        for db in (databases[0],databases[2]):
            with connect(config(db)) as conn:
                names=[r['sequencename'] for r in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='jobwatch' ORDER BY sequencename")]
                sequence_states.append({n:dict(conn.execute('SELECT last_value,is_called FROM jobwatch.'+n).fetchone()) for n in names})
        if sequence_states[0]!=sequence_states[1]:
            raise ImportProblem('Restored sequence state differs')
        query_results=[]
        operational_results=[]
        for db in databases:
            with connect(config(db,'jobwatch_owner')) as conn:
                counts={'workflow':conn.execute('SELECT count(*) AS n FROM jobwatch.opportunity_workflow').fetchone()['n'],
                    'research':conn.execute('SELECT count(*) AS n FROM jobwatch.research_postings').fetchone()['n'],
                    'applications':conn.execute('SELECT count(*) AS n FROM jobwatch.applications').fetchone()['n'],
                    'triage':conn.execute('SELECT count(*) AS n FROM jobwatch.triage').fetchone()['n'],
                    'fts_software':conn.execute("SELECT count(*) AS n FROM jobwatch.research_postings WHERE search_tsv @@ websearch_to_tsquery('english','software')").fetchone()['n']}
                if counts['workflow']!=len(plan.rows['postings']):
                    raise ImportProblem('Owner workflow view lost rows')
                query_results.append(counts)
                data=operational_reports.snapshot(conn,since='1970-01-01T00:00:00Z',limit=1000,requirements=True)
                for weekly in (False,True):
                    rendered=operational_reports.markdown(data,weekly=weekly)
                    if not rendered or 'not_seen does not prove closure' not in rendered:
                        raise ImportProblem('Operational report did not retain its uncertainty labels')
                calendar_blob,calendar_warnings=operational_reports.calendar_export(data)
                if not calendar_blob.startswith(b'BEGIN:VCALENDAR'):
                    raise ImportProblem('SQL calendar rendering failed')
                operational_results.append({'corpus_rows':sum(r['count'] for r in data['corpus']),
                    'attention_rows':data['attention_total'],'deadline_claims':len(data['deadlines']),
                    'lead_rows':len(data['leads']),'material_events':len(data['material_history']),
                    'verified_requirement_samples':data['requirements']['sample_size'],
                    'bm25_python_results':len(research.search(conn,'Python')['items']),
                    'fts_python_results':len(research.search(conn,'Python',method='fts')['items']),
                    'calendar_events':calendar_blob.count(b'BEGIN:VEVENT'),'calendar_warnings':len(calendar_warnings)})
            with connect(config(db,'jobwatch_collector')) as conn:
                conn.execute('SELECT count(*) FROM jobwatch.research_postings').fetchone()
                for sql in ('SELECT * FROM jobwatch.applications','SELECT * FROM jobwatch.owner_audit',
                            'UPDATE jobwatch.posting_versions SET title=title','CREATE TABLE jobwatch.forbidden(id int)'):
                    try:
                        with conn.transaction():
                            conn.execute(sql)
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise ImportProblem('Runtime privileges weakened after import/restore')
        if query_results[0]!=query_results[1] or query_results[0]!=query_results[2]:
            raise ImportProblem('Restored SQL query results differ')
        if operational_results[0]!=operational_results[1] or operational_results[0]!=operational_results[2]:
            raise ImportProblem('Restored operational report/search projections differ')
        result={
            'schema':'jobwatch.restore-rehearsal/1','source_commit':commit,'plan_sha256':plan.sha256,
            'counts':plan.report()['counts'],'description_recovery':plan.manifest['description_recovery'],
            'distributions':plan.manifest['distributions'],'missing_evidence':plan.manifest['missing_evidence'],
            'activation_required':plan.manifest['activation_required'],'legacy_limitations':plan.manifest['legacy_limitations'],
            'independent_imports_equal':True,'restored_matches_import':True,'restored_matches_source':True,
            'schema_ownership_grants_equal':True,'sequence_state_equal':True,'runtime_queries_and_denials_passed':True,
            'import_role':import_role,'sql_query_counts':query_results[0],
            'operational_sql_checks':operational_results[0],
            'logical_sha256':digest(snapshots[databases[0]]),'schema_sha256':digest(schemas[databases[0]]),
            'backup':{'format':'PostgreSQL custom archive','bytes':len(backup),'sha256':sha256(backup).hexdigest(),
                      'dump_version':utility('pg_dump','--version').decode().strip(),
                      'restore_version':utility('pg_restore','--version').decode().strip()},
            'commands':commands,'ready_for_cutover':reports[databases[0]]['ready_for_cutover']}
        with connect(config(databases[0])) as conn:
            checked=readiness.inspect(conn,reconciliation=reports[databases[0]],restore=result)
            result['readiness']={'local_checks_passed':checked['local_checks_passed'],
                'deployment_ready':checked['deployment_ready'],'checks':checked['checks'],'external_gates':checked['external_gates']}
        if not any(s['status']=='monitored' for s in plan.rows['sources']):
            scan=collect_monitored(Collector(config(databases[0],'jobwatch_collector'),actor='collector:rehearsal-no-live'))
            if scan['selected_sources']!=0 or scan['warning']!='no_monitored_sources':
                raise ImportProblem('Unevidenced imported candidates must not be scheduled')
            result['no_monitored_source_scan']={'selected_sources':scan['selected_sources'],'exit_code':scan['exit_code'],'warning':scan['warning']}
        if output_dir is not None:
            directory=Path(output_dir).resolve()
            if directory.is_relative_to(Path(repo).resolve()):
                raise ImportProblem('Rehearsal reports must be outside the source checkout')
            directory.mkdir(mode=0o700,parents=True,exist_ok=False)
            for label,name in zip(('import-a','import-repeat','restored'),databases):
                with (directory/(label+'.json')).open('x',encoding='utf-8') as stream:
                    os.chmod(stream.name,0o600);json.dump(reports[name],stream,indent=2,sort_keys=True);stream.write('\n')
            with (directory/'rehearsal.json').open('x',encoding='utf-8') as stream:
                os.chmod(stream.name,0o600);json.dump(result,stream,indent=2,sort_keys=True);stream.write('\n')
            (directory/'summary.txt').write_text(summary(reports[databases[0]])+'\nIndependent import and pg_dump/pg_restore: PASS\n')
        return result
    finally:
        utility('rm','-f',archive)
        for name in databases:
            utility('dropdb','-U','postgres','--if-exists',name)


if __name__=='__main__':
    if len(sys.argv)!=4:
        raise SystemExit('usage: rehearsal.py REPO COMMIT NEW_REPORT_DIRECTORY (through scripts/test_postgres.sh only)')
    print(json.dumps(rehearse(*sys.argv[1:]),indent=2,sort_keys=True))
