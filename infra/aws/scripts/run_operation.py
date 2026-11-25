#!/usr/bin/env python3
"""Run the existing collector/backup; emit counts and exit status, never raw errors/notes."""
import json
from pathlib import Path
import subprocess
import sys
import time


OPERATIONS = ('collect', 'backup', 'discover-refresh', 'discover-probe', 'auto-stage', 'auto-validate', 'auto-activate')


def run(operation, *, config_path=Path('/etc/jobwatch/aws.json'), run_command=subprocess.run):
    config = json.loads(config_path.read_text())
    release = Path(__file__).resolve().parents[3]
    if operation == 'collect':
        command = [sys.executable, '-m', 'jobwatch.postgres.operations', 'scan',
                   '--actor', 'scheduled:systemd', '--format', 'json']
    elif operation == 'backup':
        command = [sys.executable, str(release / 'infra/aws/scripts/database_backup.py'), 'backup', '--upload',
                   '--receipt', '/var/lib/jobwatch-backup/latest.json']
    elif operation == 'discover-refresh':
        command = [sys.executable, '-m', 'jobwatch.postgres.directory_discovery',
                   '--refresh-only', '--providers', 'greenhouse,ashby,lever', '--max-requests', '10']
    elif operation == 'discover-probe':
        command = [sys.executable, '-m', 'jobwatch.postgres.directory_discovery',
                   '--no-refresh', '--providers', 'greenhouse,ashby,lever',
                   '--scheduler', 'prioritized', '--exploration-ratio', '0.60',
                   '--max-boards', '40', '--max-requests', '80', '--max-bytes', '33554432',
                   '--verify-limit', '8', '--delay', '0.5']
    elif operation == 'auto-stage':
        command = [sys.executable, '-m', 'jobwatch.postgres.auto_activation', '--phase', 'stage', '--apply']
    elif operation == 'auto-validate':
        command = [sys.executable, '-m', 'jobwatch.postgres.auto_activation', '--phase', 'validate']
    elif operation == 'auto-activate':
        command = [sys.executable, '-m', 'jobwatch.postgres.auto_activation', '--phase', 'activate', '--apply']
    else:
        raise ValueError(f'Unsupported operation: {operation!r}')
    started = time.monotonic()
    result = run_command(command, capture_output=True, text=True, timeout=2600)
    # The collector's exit code is authoritative; a logging error never masks a collection error.
    summary = {'operation': operation, 'exit_code': result.returncode,
               'elapsed_seconds': round(time.monotonic()-started)}
    if operation == 'collect':
        try:
            data = json.loads(result.stdout)
            # Deliberately allowlist counters. No source text, DSN, owner notes or exception strings.
            for field in ('counts', 'selected_sources', 'positive_only', 'absence_safe'):
                value = data.get(field)
                if isinstance(value, int) or (isinstance(value, dict) and all(isinstance(v, int) for v in value.values())):
                    summary[field] = value
            summary['sources'] = [
                {k: row.get(k) for k in ('source_id', 'status', 'vacancies', 'coverage')}
                for row in data.get('sources', [])[:1000]]
        except (ValueError, TypeError):
            summary['summary_available'] = False
    elif operation in ('discover-refresh', 'discover-probe'):
        try:
            data = json.loads(result.stdout)
            for field in ('boards_probed', 'candidates_inserted', 'candidates_updated',
                          'priority_vacancies', 'review_vacancies', 'requests_made', 'bytes_read'):
                value = data.get(field)
                if isinstance(value, int):
                    summary[field] = value
            for field in ('status_counts', 'identity_checks', 'selection_reasons', 'provider_probes'):
                value = data.get(field)
                if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, int) for k, v in value.items()):
                    summary[field] = value
            if data.get('scheduler') in ('prioritized', 'fifo'):
                summary['scheduler'] = data.get('scheduler')
            if data.get('halted_reason') in (None, 'max_requests_reached', 'max_bytes_reached', 'rate_limited_429'):
                summary['halted_reason'] = data.get('halted_reason')
            dirs = data.get('directories')
            if isinstance(dirs, dict):
                summary['directories'] = {
                    str(prov): {k: v for k, v in stats.items() if k in ('total', 'inserted', 'updated', 'already_registered', 'unknown_boards', 'skipped_invalid') and isinstance(v, int)}
                    for prov, stats in dirs.items() if isinstance(stats, dict)
                }
        except (ValueError, TypeError):
            summary['summary_available'] = False
    elif operation in ('auto-stage', 'auto-validate', 'auto-activate'):
        try:
            data = json.loads(result.stdout)
            for field in ('evaluated', 'checked'):
                if isinstance(data.get(field), int):
                    summary[field] = data[field]
            for list_field in ('eligible', 'staged', 'validated', 'failed', 'activated', 'deferred'):
                if isinstance(data.get(list_field), list):
                    summary[list_field + '_count'] = len(data[list_field])
            if isinstance(data.get('locked_by_concurrent_run'), bool):
                summary['locked_by_concurrent_run'] = data['locked_by_concurrent_run']
        except (ValueError, TypeError):
            summary['summary_available'] = False
    publish(summary, config, run_command=run_command)
    return result.returncode


def publish(summary, config, *, run_command=subprocess.run):
    """Publish an already allowlisted summary to the existing restricted log stream."""
    message = json.dumps(summary, sort_keys=True)
    print(message, flush=True)
    try:
        logged = run_command(['aws', 'logs', 'put-log-events', '--region', config['region'],
                              '--log-group-name', config['log_group'], '--log-stream-name', config['log_stream'],
                              '--log-events', json.dumps([{'timestamp': int(time.time()*1000), 'message': message}])],
                             capture_output=True, text=True, timeout=30)
        if logged.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    print('Operational log delivery failed; inspect local journal', file=sys.stderr)
    return False


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in OPERATIONS:
            raise ValueError('Expected collect, backup, discover-refresh or discover-probe')
        raise SystemExit(run(sys.argv[1]))
    except (OSError, ValueError, subprocess.SubprocessError):
        # Do not expose command environment, libpq or AWS error messages.
        print('Operation failed before a confirmed result; inspect database run state', file=sys.stderr)
        raise SystemExit(2)
