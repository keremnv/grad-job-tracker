"""Explicit frozen-commit import/reconciliation, using separate migration credentials."""
import argparse
import json
import os
from pathlib import Path
import sys

from .connection import DatabaseError, connect
from .git_snapshot import ImportProblem
from .import_plan import build_plan
from .importer import apply_import, reconcile


def summary(report):
    lines = ['Frozen Git commit: '+report['source_commit'], 'Plan SHA-256: '+report['plan_sha256']]
    lines += [f'{table}: {count}' for table,count in report['counts'].items()]
    lines += ['Reconciliation: '+('PASS' if report['reconciliation']['ok'] else 'FAIL') if 'reconciliation' in report else 'Plan validated; no database writes',
              'Unavailable evidence references: '+str(len(report['missing_evidence'])),
              'Legacy limitations: '+str(len(report['legacy_limitations'])),
              'Source activation checks required: '+str(len(report['activation_required']))]
    for source in report['activation_required']:
        lines.append('  '+source['source']+': '+', '.join(source['missing']))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='One-time frozen Git import; never migrates or resets a target')
    parser.add_argument('command', choices=('plan','import','reconcile'))
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--report', type=Path, help='New private JSON file outside the source checkout')
    parser.add_argument('--max-history-commits',type=int,default=10000)
    parser.add_argument('--max-git-bytes',type=int,default=256*1024*1024)
    parser.add_argument('--max-git-objects',type=int,default=100000)
    args = parser.parse_args(argv)
    if args.command != 'plan' and args.report is None:
        parser.error('--report is required for import/reconcile')
    output = None
    try:
        plan = build_plan(args.repo,args.commit,max_commits=args.max_history_commits,
                          max_bytes=args.max_git_bytes,max_objects=args.max_git_objects)
        if args.report:
            dest = args.report.resolve()
            if dest.is_relative_to(args.repo.resolve()):
                raise ImportProblem('Reports must be outside the source checkout')
            # Reserve before the database write; never overwrite a report or tracked source file.
            fd = os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            output = os.fdopen(fd,'w',encoding='utf-8')
        if args.command == 'plan':
            report = plan.report()
        else:
            with connect(purpose='migration') as conn:
                report = apply_import(conn,plan) if args.command=='import' else reconcile(conn,plan)
        if output:
            json.dump(report,output,indent=2,sort_keys=True); output.write('\n'); output.flush(); os.fsync(output.fileno())
        print(summary(report))
        return 0 if report.get('reconciliation',{}).get('ok',True) else 2
    except (ImportProblem,DatabaseError) as exc:
        if output and getattr(exc,'report',None):
            json.dump(exc.report,output,indent=2,sort_keys=True); output.write('\n')
        print('ERROR: '+str(exc),file=sys.stderr)
    except (OSError,ValueError):
        print('ERROR: Input/report operation failed; database may already be committed. Reconcile before retrying; private values omitted.',file=sys.stderr)
    finally:
        if output:
            output.close()
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
