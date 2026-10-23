"""Explicit PostgreSQL development collector. Does not replace jobwatch.cli scan."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit, parse_qs

from .collection import Processing
from .connection import DatabaseError
from .publisher import Collector, CollectionError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit PostgreSQL collection/rescreening (no automatic migration)')
    parser.add_argument('--actor', required=True)
    parser.add_argument('--processing-revision', default='pipeline-v1')
    parser.add_argument('--advance-from', help='Explicit compare-and-set processing deployment from this revision')
    commands = parser.add_subparsers(dest='command', required=True)
    collect = commands.add_parser('collect')
    collect.add_argument('source_id', type=int)
    collect.add_argument('--fixture', type=Path, help='Use this JSON response instead of any employer requests')
    rescreen = commands.add_parser('rescreen')
    target = rescreen.add_mutually_exclusive_group(required=True)
    target.add_argument('--source', type=int)
    target.add_argument('--all', action='store_true')
    args = parser.parse_args(argv)
    try:
        collector = Collector(actor=args.actor, processing=Processing(revision=args.processing_revision))
        if args.command == 'rescreen':
            outcome = collector.rescreen_all(advance_from=args.advance_from) if args.all else collector.rescreen_source(args.source, advance_from=args.advance_from)
            print(json.dumps({'status': outcome}))
        else:
            kwargs = {}
            if args.fixture:
                response = json.loads(args.fixture.read_text(encoding='utf-8'))
                def get(url):
                    if isinstance(response, list):
                        query = parse_qs(urlsplit(url).query)
                        start = int(query.get('skip', ['0'])[0])
                        return response[start:start+int(query.get('limit', ['100'])[0])]
                    return response
                kwargs = {'get_json': get, 'pause': lambda _: None}
            rid, outcome = collector.collect_source(args.source_id, advance_from=args.advance_from, **kwargs)
            print(json.dumps({'run_id': str(rid), 'status': outcome}))
        return 0
    except CollectionError as exc:
        print(json.dumps({'error': str(exc), 'run_id': str(exc.run_id) if exc.run_id else None, 'outcome': exc.outcome, 'sqlstate': exc.sqlstate}), file=sys.stderr)
    except (DatabaseError, ValueError, OSError):
        print(json.dumps({'error': 'PostgreSQL command failed; check configuration/input (credentials omitted)'}), file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
