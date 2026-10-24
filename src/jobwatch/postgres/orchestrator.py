"""Sequential monitored-source collection. Each Stage 2 publication owns its transaction."""
from collections import Counter
from datetime import datetime,timezone

from jobwatch.render import text
from .collection import iso
from .publisher import Collector,CollectionError


def collect_monitored(collector,*,get_json=None,pause=None,max_sources=1000):
    if type(max_sources) is not int or not 1<=max_sources<=10000:
        raise ValueError('Source bound must be 1..10000')
    started=iso(datetime.now(timezone.utc))
    with collector._connection() as conn:
        sources=conn.execute("SELECT id,provider,board FROM jobwatch.sources WHERE status='monitored' ORDER BY id LIMIT %s",(max_sources+1,)).fetchall()
    if len(sources)>max_sources:
        raise ValueError('Monitored source bound exceeded; no collection started')
    outcomes=[]
    for source in sources:
        run_id=None
        error=None
        try:
            kwargs={}
            if get_json is not None:kwargs['get_json']=get_json
            if pause is not None:kwargs['pause']=pause
            run_id,status=collector.collect_source(source['id'],**kwargs)
        except CollectionError as exc:
            run_id,status=exc.run_id,exc.outcome
            error={'code':'collection_or_publication_failed','sqlstate':exc.sqlstate}
        except Exception:
            status='unknown';error={'code':'unexpected_source_error'}
        row={'source_id':source['id'],'source':source['provider']+':'+source['board'],
             'run_id':str(run_id) if run_id else None,'status':status,'error':error}
        try:
            with collector._connection() as conn:
                run=conn.execute('SELECT status,source_id,fetched_at,finished_at,source_records,vacancies,skipped_non_vacancies,absence_safe '
                    'FROM jobwatch.collection_runs WHERE id=%s',(run_id,)).fetchone() if run_id else None
            if run and run['source_id']==source['id']:
                row.update({k:v for k,v in run.items() if k!='source_id'})
            elif status not in ('unknown','running'):
                row['status']='unknown';row['error']={'code':'run_outcome_unconfirmed'}
        except Exception:
            # Prior committed sources remain valid; no speculative success after lost connectivity.
            row['status']='unknown';row['error']={'code':'run_outcome_unconfirmed'}
        if row['status']=='running':row['status']='unknown'
        if row['status']=='published' and row['error'] is not None:
            row['error']=None;row['recovered_after_uncertainty']=True
        row['coverage']='absence_safe' if row['status']=='published' and row.get('absence_safe') else 'positive_only' if row['status']=='published' else 'no_publication'
        outcomes.append(row)
    counts=dict(Counter(r['status'] for r in outcomes))
    published=counts.get('published',0)
    if counts.get('unknown') or (counts.get('failed') and not published):code=2
    elif not sources or any(r['status']!='published' for r in outcomes):code=1
    else:code=0
    return {'schema':'jobwatch.postgres.scan/1','started_at':started,'finished_at':iso(datetime.now(timezone.utc)),
        'selected_sources':len(sources),'counts':counts,'positive_only':sum(r['coverage']=='positive_only' for r in outcomes),
        'absence_safe':sum(r['coverage']=='absence_safe' for r in outcomes),'sources':outcomes,'exit_code':code,
        'warning':'no_monitored_sources' if not sources else None,
        'note':'Per-source commits are independent. Nonzero exit does not undo successful publications.'}


def actions_summary(result):
    lines=['# PostgreSQL collection summary','',
        f"Selected sources: {result['selected_sources']}; published: {result['counts'].get('published',0)}; absence-safe: {result['absence_safe']}; positive-only: {result['positive_only']}.",
        'No owner records are queried. not_seen is not verified closure.','',
        '| Source | Outcome | Validated vacancies | Coverage |','|---|---|---:|---|']
    for row in result['sources']:
        lines.append(f"| {text(row['source'])} | {text(row['status'])} | {text(row.get('vacancies') if row.get('vacancies') is not None else 'unknown')} | {text(row['coverage'])} |")
    if result['warning']:lines+=['',text(result['warning'])]
    return '\n'.join(lines)+'\n'
