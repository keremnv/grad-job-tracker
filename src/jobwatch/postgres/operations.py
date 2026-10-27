"""Explicit PostgreSQL conveniences. Ordinary reads/owner writes remain normal SQL."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs,urlsplit

from .connection import connect,DatabaseError
from .git_snapshot import ImportProblem
from .importer import primitive
from .publisher import Collector,CollectionError
from .endpoints import SourceCheckError,inspect_source,check_endpoint,activate_source
from . import reports,research,readiness
from .orchestrator import collect_monitored,actions_summary
from . import calendar_feed


def fixture_transport(path):
    """One response or source-keyed responses; pagination exercises the real adapter."""
    data=json.loads(path.read_text(encoding='utf-8'))
    def get(url):
        u=urlsplit(url);parts=u.path.strip('/').split('/')
        provider='greenhouse' if 'greenhouse.io' in u.hostname else 'lever' if 'lever.co' in u.hostname else 'ashby'
        board=parts[2] if provider=='greenhouse' else parts[-1] if provider=='ashby' else parts[2]
        response=data.get('sources',{}).get(provider+':'+board) if isinstance(data,dict) and 'sources' in data else data
        if response is None:raise ValueError('Missing fixture source')
        if isinstance(response,dict) and response.get('fixture_error'):raise ValueError('Fixture failure')
        if provider=='lever' and isinstance(response,list):
            query=parse_qs(u.query);start=int(query.get('skip',['0'])[0]);return response[start:start+int(query.get('limit',['100'])[0])]
        return response
    return get


def main(argv=None):
    p=argparse.ArgumentParser(description='Explicit PostgreSQL operations; no automatic migrations or backend switch')
    sub=p.add_subparsers(dest='command',required=True)
    scan=sub.add_parser('scan');scan.add_argument('--actor',required=True);scan.add_argument('--fixture',type=Path)
    scan.add_argument('--format',choices=('json','summary'),default='json');scan.add_argument('--max-sources',type=int,default=1000)
    report=sub.add_parser('report');report.add_argument('kind',choices=('daily','weekly','requirements','calendar'))
    report.add_argument('--since');report.add_argument('--limit',type=int,default=50);report.add_argument('--json',action='store_true')
    cal=sub.add_parser('calendar');cs=cal.add_subparsers(dest='calendar_command',required=True)
    snapshot=cs.add_parser('snapshot',help='Publish private derived ICS snapshots locally; no upload or schedule')
    snapshot.add_argument('--output-dir',type=Path,required=True);snapshot.add_argument('--initialize',action='store_true')
    snapshot.add_argument('--feeds',nargs='+',choices=('deadlines','actions'),default=['deadlines','actions'])
    snapshot.add_argument('--past-days',type=int,default=30);snapshot.add_argument('--future-days',type=int,default=366)
    snapshot.add_argument('--deadline-policy',type=Path,help='Read-only SQL selecting retained posting_key/lead_key as evidence_key')
    snapshot.add_argument('--max-bytes',type=int,default=calendar_feed.MAX_BYTES)
    status=cs.add_parser('status');status.add_argument('--output-dir',type=Path,required=True)
    status.add_argument('--max-age-hours',type=int,default=2)
    search=sub.add_parser('search');search.add_argument('query');search.add_argument('--method',choices=('bm25','fts','vector','hybrid'),default='bm25')
    search.add_argument('--lexical-method',choices=('bm25','fts'),default='bm25')
    search.add_argument('--history',action='store_true');search.add_argument('--include-not-seen',action='store_true');search.add_argument('--limit',type=int,default=20)
    source=sub.add_parser('source');ss=source.add_subparsers(dest='source_command',required=True)
    for command in ('inspect','check','activate'):
        s=ss.add_parser(command);s.add_argument('source_id',type=int)
        if command!='inspect':s.add_argument('--actor',required=True)
        if command=='check':s.add_argument('--fixture',type=Path)
        if command=='activate':s.add_argument('--check-run',required=True)
    ready=sub.add_parser('readiness');ready.add_argument('--reconciliation',type=Path);ready.add_argument('--restore',type=Path)
    ready.add_argument('--phase',choices=('baseline','operational'),default='baseline')
    args=p.parse_args(argv)
    try:
        if args.command=='calendar':
            if args.calendar_command=='status':
                if not 1<=args.max_age_hours<=168:raise ValueError('Invalid freshness bound')
                _,manifest=calendar_feed.current_snapshot(args.output_dir)
                result=calendar_feed.summary(manifest,max_age_hours=args.max_age_hours)
                code=0 if result['fresh'] else 1
            else:
                with connect() as conn:data=reports.calendar_data(conn,deadline_selection_sql=args.deadline_policy.read_text() if args.deadline_policy else None)
                result=calendar_feed.publish(data,args.output_dir,initialize=args.initialize,feeds=args.feeds,
                    past_days=args.past_days,future_days=args.future_days,max_bytes=args.max_bytes)
                code=1 if result['warnings'] else 0
            print(json.dumps(result,ensure_ascii=True));return code
        if args.command=='scan':
            c=Collector(actor=args.actor)
            kwargs={'get_json':fixture_transport(args.fixture),'pause':lambda _:None} if args.fixture else {}
            result=collect_monitored(c,max_sources=args.max_sources,**kwargs)
            print(actions_summary(result) if args.format=='summary' else json.dumps(primitive(result),ensure_ascii=True))
            return result['exit_code']
        if args.command=='source':
            if args.source_command=='check':
                kwargs={'get_json':fixture_transport(args.fixture),'pause':lambda _:None,'fixture':True} if args.fixture else {}
                result=check_endpoint(Collector(actor=args.actor),args.source_id,**kwargs)
                code=0 if result['status']=='validated' else 1
            else:
                with connect() as conn:
                    result=inspect_source(conn,args.source_id) if args.source_command=='inspect' else activate_source(conn,args.source_id,args.check_run,actor=args.actor)
                code=0
            print(json.dumps(primitive(result),ensure_ascii=True));return code
        if args.command=='search':
            from .embeddings import query_embedding
            vector=query_embedding(args.query) if args.method in ('vector','hybrid') else None
            with connect() as conn:
                result=research.search(conn,args.query,method=args.method,history=args.history,include_not_seen=args.include_not_seen,limit=args.limit,
                                       query_vector=vector,lexical_method=args.lexical_method)
            print(json.dumps(primitive(result),ensure_ascii=True));return 0
        if args.command=='readiness':
            reconciliation=json.loads(args.reconciliation.read_text()) if args.reconciliation else None
            restore=json.loads(args.restore.read_text()) if args.restore else None
            with connect(purpose='migration') as conn:result=readiness.inspect(conn,reconciliation=reconciliation,restore=restore,phase=args.phase)
            print(json.dumps(result,ensure_ascii=True));return 0 if result['local_checks_passed'] else 1
        with connect() as conn:
            data=reports.snapshot(conn,days=7 if args.kind=='weekly' else 1,since=args.since,limit=args.limit,
                                  requirements=args.kind in ('weekly','requirements'))
        if args.kind=='calendar':
            if args.json:raise ValueError('Calendar emits RFC 5545; use another report for JSON')
            blob,warnings=reports.calendar_export(data)
            sys.stdout.write(blob.decode('utf-8'))
            for warning in warnings:print(warning,file=sys.stderr)
            return 1 if warnings else 0
        print(json.dumps(primitive(data),ensure_ascii=True) if args.json else reports.markdown(data,weekly=args.kind=='weekly',requirements_only=args.kind=='requirements'))
        return 0
    except (DatabaseError,SourceCheckError,CollectionError,ImportProblem,calendar_feed.FeedError) as exc:
        # These are application-defined safe messages, never driver/provider exceptions.
        print(json.dumps({'error':str(exc),'outcome':'unconfirmed' if args.command in ('scan','source','calendar') else 'read_failed'}),file=sys.stderr)
    except Exception:
        print(json.dumps({'error':'PostgreSQL operation failed; inspect configuration/input or SQL privileges. Private driver/source details omitted.'}),file=sys.stderr)
    return 2


if __name__=='__main__':
    raise SystemExit(main())
