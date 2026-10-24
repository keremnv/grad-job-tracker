#!/usr/bin/env python3
"""Run the existing collector/backup; emit counts and exit status, never raw errors/notes."""
import json
from pathlib import Path
import subprocess
import sys
import time


def run(operation, *, config_path=Path('/etc/jobwatch/aws.json'), run_command=subprocess.run):
    config = json.loads(config_path.read_text())
    release = Path(__file__).resolve().parents[3]
    command = ([sys.executable, '-m', 'jobwatch.postgres.operations', 'scan',
                '--actor', 'scheduled:systemd', '--format', 'json'] if operation == 'collect' else
               [sys.executable, str(release / 'infra/aws/scripts/database_backup.py'), 'backup', '--upload'])
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
    message = json.dumps(summary, sort_keys=True)
    print(message, flush=True)
    try:
        logged = run_command(['aws', 'logs', 'put-log-events', '--region', config['region'],
                              '--log-group-name', config['log_group'], '--log-stream-name', config['log_stream'],
                              '--log-events', json.dumps([{'timestamp': int(time.time()*1000), 'message': message}])],
                             capture_output=True, text=True, timeout=30)
        if logged.returncode:
            print('Operational log delivery failed; inspect local journal', file=sys.stderr)
    except (OSError, subprocess.SubprocessError):
        print('Operational log delivery failed; inspect local journal', file=sys.stderr)
    return result.returncode


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ('collect', 'backup'):
            raise ValueError('Expected collect or backup')
        raise SystemExit(run(sys.argv[1]))
    except (OSError, ValueError, subprocess.SubprocessError):
        # Do not expose command environment, libpq or AWS error messages.
        print('Operation failed before a confirmed result; inspect database run state', file=sys.stderr)
        raise SystemExit(2)
