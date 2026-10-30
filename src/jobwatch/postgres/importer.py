"""Explicit one-shot import and field-level reconciliation. Requires a migrated empty database."""
from datetime import date, datetime
from hashlib import sha256
import json
from uuid import UUID

from jobwatch.identity import exact_url, loose_url
from .collection import iso
from .evidence import encode_evidence
from .git_snapshot import ImportProblem
from .import_plan import TABLES, digest, stamp
from .migrate import MIGRATION_LOCK, load_migrations

JSON_COLUMNS = {'details','provenance','source_dates','deadlines','extra_fields','changes','legacy_details'}
KEYS = {'employers':'slug','sources':'identity','collection_runs':'id','postings':'posting_key',
        'posting_versions':'id','posting_events':'id','leads':'lead_key','verifications':'id',
        'triage':'posting_key','applications':'job_key'}
AUDITED = ('employers','sources','leads','triage','applications')


def primitive(value):
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).decode('ascii')
    if isinstance(value, dict):
        return {k:primitive(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [primitive(v) for v in value]
    return value


def check_migrations(conn):
    expected = [(m.version,m.filename,m.checksum) for m in load_migrations()]
    actual = [tuple(r.values()) for r in conn.execute('SELECT version,filename,sha256 FROM jobwatch.schema_migrations ORDER BY version')]
    if actual != expected:
        raise ImportProblem('Target must have exactly the approved packaged migrations; apply them separately')
    tables={r['relname'] for r in conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='jobwatch' AND c.relkind IN ('r','p')")}
    if tables != {*TABLES,'owner_audit','schema_migrations','embedding_chunks'}:
        raise ImportProblem('Unexpected tables in jobwatch schema; refusing import/reconciliation')


def _idle(conn):
    from psycopg.pq import TransactionStatus
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise ImportProblem('Import/reconciliation requires an idle autocommit connection')


def logical_snapshot(conn, *, derived=False):
    """Normalize surrogate references, retaining every domain field and relative event/run order.

    Only migration/import activity clocks, sequence offsets and audit transaction IDs
    are excluded. Original observation times and original JSON are always retained.
    """
    raw = {table:[primitive(r) for r in conn.execute('SELECT * FROM jobwatch.'+table)] for table in (*TABLES,'owner_audit')}
    employers = {r['id']:r['slug'] for r in raw['employers']}
    sources = {r['id']:r['provider']+':'+r['board'].lower() for r in raw['sources']}
    run_order = {r['id']:i+1 for i,r in enumerate(sorted(raw['collection_runs'],key=lambda r:r['run_order']))}
    event_order = {r['id']:i+1 for i,r in enumerate(sorted(raw['posting_events'],key=lambda r:r['id']))}
    def clean(table,row):
        row = dict(row)
        # to_jsonb(trigger row) spells timestamptz as +00:00; typed psycopg rows
        # use datetime. Normalize the same known columns without touching source JSON.
        for name in {'employers':('watch_checked_at',), 'sources':('endpoint_checked_at','mapping_verified_at'),
                     'leads':('checked_at','deadline_checked_at'), 'triage':('decided_at',)}.get(table,()):
            if row.get(name) is not None:
                row[name] = stamp(row[name])
        if table == 'verifications':
            row.setdefault('deadline_supersedes',None)
            row.setdefault('deadline_reason',None)
        if table == 'leads':
            for name in ('deadline_source_url','deadline_official','deadline_checked_at','deadline_checked_by'):
                row.setdefault(name,None)
        if table == 'employers':
            row.pop('id',None)
        if table == 'sources':
            row.pop('id',None); row.pop('discovered_at',None)
            row['identity'] = row['provider']+':'+row['board'].lower()
        if 'employer_id' in row and row['employer_id'] is not None:
            row['employer_id'] = employers[row['employer_id']]
        if 'source_id' in row:
            row['source_id'] = sources[row['source_id']]
        if table == 'collection_runs':
            row['run_order'] = run_order[row['id']]
            if row['mode'] == 'import':
                row.pop('started_at'); row.pop('finished_at')
        if table == 'posting_events':
            row['id'] = event_order[row['id']]
            if row['category'] == 'import':
                row.pop('recorded_at')
        if table == 'verifications':
            row.pop('recorded_at')  # new import activity, checked_at is retained
        if table == 'posting_versions' and not derived:
            row.pop('search_tsv')
        return row
    result = {table: sorted((clean(table,r) for r in raw[table]),key=lambda r:str(r[KEYS[table]])) for table in TABLES}
    result['owner_audit'] = []
    for r in sorted(raw['owner_audit'],key=lambda r:r['id']):
        result['owner_audit'].append({'table_name':r['table_name'],'op':r['op'],'db_login':r['db_login'],'actor':r['actor'],
            'old_row':clean(r['table_name'],r['old_row']) if r['old_row'] is not None else None,
            'new_row':clean(r['table_name'],r['new_row']) if r['new_row'] is not None else None})
    return result


def expected_rows(plan):
    expected = {t:[dict(r) for r in plan.rows[t]] for t in TABLES}
    for r in expected['verifications']:
        r.setdefault('deadline_supersedes',None)
        r.setdefault('deadline_reason',None)
    for r in expected['leads']:
        for name in ('deadline_source_url','deadline_official','deadline_checked_at','deadline_checked_by'):
            r.setdefault(name,None)
    for i,r in enumerate(expected['collection_runs']):
        r['run_order'] = i+1
    for i,r in enumerate(expected['posting_events']):
        r['id'] = i+1
    for t in ('posting_versions','leads','applications'):
        for r in expected[t]:
            r['url_exact'] = exact_url(r['posting_url'] if t=='posting_versions' else r['url'])
            if t=='applications':
                r['url_loose'] = loose_url(r['url'])
    return {t: sorted(rows,key=lambda r:str(r[KEYS[t]])) for t,rows in expected.items()}


def _reconcile(conn, plan):
    check_migrations(conn)
    actual = logical_snapshot(conn)
    expected = expected_rows(plan)
    failures, matches = [], {}
    for table in TABLES:
        key = KEYS[table]
        want = {str(r[key]):r for r in expected[table]}
        got = {str(r[key]):r for r in actual[table]}
        rows = []
        for identity in sorted(want.keys() | got.keys()):
            a,b = want.get(identity),got.get(identity)
            status = 'exact' if a == b else 'missing' if b is None else 'unexpected' if a is None else 'mismatch'
            fields = sorted(k for k in (a or {}).keys() | (b or {}).keys() if (a or {}).get(k)!=(b or {}).get(k)) if status=='mismatch' else []
            # Public posting keys/evidence hashes are useful; owner identities remain hashed.
            label = identity if table not in ('applications','triage') else sha256(identity.encode()).hexdigest()
            rows.append({'identity':label,'status':status,'expected_sha256':digest(a) if a is not None else None,
                         'actual_sha256':digest(b) if b is not None else None, 'fields':fields})
            if status != 'exact':
                failures.append({'table':table,'identity':label,'status':status,'fields':fields})
        matches[table] = {'exact':sum(r['status']=='exact' for r in rows),'rows':rows,
                          'expected_sha256':digest(expected[table]),'actual_sha256':digest(actual[table])}
    # Independently check trigger images against reconciled current rows. Never copy notes into a report.
    images = []
    for table in AUDITED:
        for row in expected[table]:
            image = dict(row)
            if table == 'sources':
                image.pop('last_published_run_id'); image.pop('processing_revision')
            images.append((table,digest(image)))
    observed = []
    for row in actual['owner_audit']:
        if row['op'] != 'INSERT' or row['old_row'] is not None or row['actor'] != 'import:'+plan.commit or not row['db_login']:
            failures.append({'table':'owner_audit','status':'unexpected_operation_or_attribution'})
        observed.append((row['table_name'],digest(row['new_row'])))
    if sorted(images) != sorted(observed):
        failures.append({'table':'owner_audit','status':'image_or_coverage_mismatch'})
    matches['owner_audit'] = {'count':len(observed),'expected_count':len(images),'images_match':sorted(images)==sorted(observed),
                             'sha256':digest(actual['owner_audit'])}
    return {**plan.report(), 'reconciliation':{'ok':not failures,'exact_matches':matches,
        'failures':failures,'logical_sha256':digest(actual)}, 'ready_for_cutover':not failures
        and not plan.manifest['activation_required'] and not plan.manifest['legacy_limitations']}


def reconcile(conn, plan):
    import psycopg
    _idle(conn)
    try:
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return _reconcile(conn, plan)
    except psycopg.Error as exc:
        code = exc.sqlstate if exc.sqlstate and len(exc.sqlstate)==5 and exc.sqlstate.isalnum() else 'connection'
        raise ImportProblem(f'Reconciliation could not read target (SQLSTATE {code}); private driver details omitted') from None


def apply_import(conn, plan):
    """One atomic import. No migration, deletion, truncate, trigger disabling or rescreen."""
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb
    _idle(conn)
    try:
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(%s)',(MIGRATION_LOCK,))
            check_migrations(conn)
            conn.execute('LOCK TABLE '+','.join('jobwatch.'+t for t in (*TABLES,'owner_audit'))+' IN SHARE ROW EXCLUSIVE MODE')
            if any(conn.execute('SELECT EXISTS(SELECT 1 FROM jobwatch.'+t+') AS present').fetchone()['present'] for t in (*TABLES,'owner_audit')):
                raise ImportProblem('Target contains domain/audit records; refusing repeat import or operational overwrite')
            conn.execute("SELECT set_config('jobwatch.actor',%s,true)",('import:'+plan.commit,))
            now = conn.execute('SELECT clock_timestamp() AS at').fetchone()['at']
            employers, sources = {}, {}
            for table in TABLES:
                for planned in plan.rows[table]:
                    row = dict(planned)
                    if table == 'sources':
                        sk = row.pop('identity'); row['last_published_run_id'] = None
                    if row.get('employer_id') is not None:
                        row['employer_id'] = employers[row['employer_id']]
                    if 'source_id' in row:
                        row['source_id'] = sources[row['source_id']]
                    if table == 'posting_versions':
                        row.pop('evidence_sha256'); row['evidence'] = row['evidence'].encode('ascii')
                    if table == 'collection_runs':
                        row['started_at'] = now; row['finished_at'] = now
                    values = [Jsonb(v) if k in JSON_COLUMNS else v for k,v in row.items()]
                    statement = sql.SQL('INSERT INTO jobwatch.{} ({}) VALUES ({})').format(sql.Identifier(table),
                        sql.SQL(',').join(map(sql.Identifier,row)),sql.SQL(',').join(sql.Placeholder() for _ in row))
                    if table in ('employers','sources'):
                        result = conn.execute(statement+sql.SQL(' RETURNING id'),values).fetchone()['id']
                        (employers if table=='employers' else sources)[row['slug'] if table=='employers' else sk] = result
                    else:
                        conn.execute(statement,values)
            for src in plan.rows['sources']:
                conn.execute('UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s',
                             (src['last_published_run_id'],sources[src['identity']]))
            conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
            report = _reconcile(conn,plan)
            if not report['reconciliation']['ok']:
                problem = ImportProblem('Import reconciliation failed; all logical records rolled back')
                problem.report = report
                raise problem
        return report
    except psycopg.Error as exc:
        code = exc.sqlstate if exc.sqlstate and len(exc.sqlstate)==5 and exc.sqlstate.isalnum() else 'connection'
        raise ImportProblem(f'Import failed (SQLSTATE {code}); reconcile after connection loss before assuming rollback or retrying') from None


def schema_snapshot(conn):
    """Restore/repeatability proof: definitions, ownership, grants, generated fields and ledger."""
    queries = {
        'namespace': "SELECT nspname,pg_get_userbyid(nspowner) AS owner,nspacl::text AS acl FROM pg_namespace WHERE nspname='jobwatch'",
        'relations': "SELECT c.relname,c.relkind,c.relrowsecurity,pg_get_userbyid(c.relowner) AS owner,c.relacl::text AS acl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='jobwatch' ORDER BY c.relname",
        'columns': "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,a.attidentity,a.attgenerated,a.attacl::text AS acl,pg_get_expr(d.adbin,d.adrelid) AS default FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON (d.adrelid,d.adnum)=(a.attrelid,a.attnum) WHERE n.nspname='jobwatch' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        # Pretty deparse preserves precedence while dropping redundant associative
        # OR parentheses introduced by IN(row,...); pg_dump/reparse flattens these.
        'constraints': "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid,true) AS definition FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='jobwatch' ORDER BY c.relname,k.conname",
        'indexes': "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='jobwatch' ORDER BY indexname",
        'views': "SELECT viewname,definition FROM pg_views WHERE schemaname='jobwatch' ORDER BY viewname",
        'functions': "SELECT p.proname,pg_get_functiondef(p.oid) AS definition,pg_get_userbyid(p.proowner) AS owner,p.proacl::text AS acl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='jobwatch' ORDER BY p.proname",
        'triggers': "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) AS definition,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='jobwatch' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        'migrations': 'SELECT version,filename,sha256 FROM jobwatch.schema_migrations ORDER BY version',
        'extensions': 'SELECT extname,extversion FROM pg_extension ORDER BY extname',
    }
    return {name:[primitive(r) for r in conn.execute(query)] for name,query in queries.items()}
