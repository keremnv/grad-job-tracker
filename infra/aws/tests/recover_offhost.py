#!/usr/bin/env python3
"""Synthetic-only off-host restore into a new, network-isolated local PostgreSQL 17.

No AWS calls, external database URLs, application migrations or production inputs.
Uses the real backup restore/verifier. All outputs remain private outside Git.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import shutil
import tempfile
import sys
import time
from uuid import uuid4

DATABASE = 'jobwatch_offhost_recovery'
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'infra/aws/scripts'))
import database_backup as backup
from jobwatch.postgres.connection import DatabaseConfig, connect
from jobwatch.postgres import research, reports


def recover(archive, manifest_path, output):
    os.umask(0o077)
    started = time.monotonic()
    manifest = json.loads(manifest_path.read_text())
    if sha256(archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
        raise ValueError('Archive SHA-256 differs from manifest')
    # This rehearsal is deliberately limited to the already approved synthetic backup.
    counts = manifest['fingerprint']['tables']
    if not (counts['postings']['count'] == 2 and counts['posting_versions']['count'] == 3
            and counts['applications']['count'] == 1 and counts['owner_audit']['count'] == 4
            and counts['collection_runs']['count'] == 0):
        raise ValueError('Expected the approved synthetic fixture; refusing other input')
    if output.exists():
        raise ValueError('Use a new private output directory')
    # Refuse remote Docker contexts as well as caller-provided database destinations.
    if os.environ.get('DOCKER_HOST') and not os.environ['DOCKER_HOST'].startswith('unix://'):
        raise ValueError('A local Unix-socket Docker engine is required')
    context = json.loads(subprocess.check_output(['docker', 'context', 'inspect']))[0]
    if not context['Endpoints']['docker']['Host'].startswith('unix://'):
        raise ValueError('Remote Docker contexts are not permitted')
    output.mkdir(mode=0o700, parents=True)
    socket_parent = Path(tempfile.mkdtemp(prefix='jw-offhost-'))
    socket = socket_parent / 'socket'
    socket.mkdir(mode=0o755)
    token = uuid4().hex
    name = 'jobwatch-offhost-' + token
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('PG', 'AWS_', 'JOBWATCH_'))}
    commands = []

    def run(args, *, input=None):
        commands.append(args)
        p = subprocess.run(args, input=input, capture_output=True, env=env, timeout=180)
        if p.returncode:
            (output / 'failure-private.txt').write_bytes(p.stderr)
            raise RuntimeError('Local utility failed; inspect private diagnostics')
        return p.stdout

    def verify_container():
        info = json.loads(run(['docker', 'inspect', name]))[0]
        if (info['Config']['Labels'].get('jobwatch.offhost-test') != token
                or info['Config']['Image'] != image or not info['State']['Running']
                or info['HostConfig']['NetworkMode'] != 'none'
                or info['HostConfig']['PortBindings']):
            raise RuntimeError('Disposable local target verification failed')
        return info

    def config(role):
        from psycopg.conninfo import make_conninfo
        return DatabaseConfig(make_conninfo(host=str(socket), hostaddr='', port=5432, dbname=DATABASE, user=role, sslmode='disable'),
                              purpose='migration' if role == 'jobwatch_migrator' else 'runtime')

    created = False
    success = False
    try:
        image = run(['docker','build','-q','-f',str(ROOT/'tests/postgres/Dockerfile.vector'),str(ROOT)]).decode().strip()
        if not image.startswith('sha256:'):
            raise RuntimeError('Expected an immutable local vector image ID')
        run(['docker', 'run', '-d', '--name', name, '--network', 'none',
             '--label', 'jobwatch.offhost-test=' + token,
             '--mount', f'type=bind,src={socket},dst=/var/run/postgresql',
             '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=' + DATABASE,
             '-e', 'POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C.UTF-8',
             image, '-c', 'listen_addresses='])
        created = True
        verify_container()
        for _ in range(100):
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '/var/run/postgresql', '-U', 'postgres'],
                                   capture_output=True, env=env, timeout=5)
            pid = run(['docker', 'exec', name, 'cat', '/proc/1/comm']).strip()
            if ready.returncode == 0 and pid == b'postgres':
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Disposable PostgreSQL did not become ready')
        verify_container()
        run(['docker', 'exec', '-i', name, 'psql', '-X', '-U', 'postgres', '-d', DATABASE,
             '-v', 'ON_ERROR_STOP=1', '-f', '-'], input=(ROOT / 'sql/postgresql/bootstrap.sql').read_bytes())
        for prerequisite in ('vector_extension.sql','embedding_access.sql'):
            run(['docker','exec','-i',name,'psql','-X','-U','postgres','-d',DATABASE,
                 '-v','ON_ERROR_STOP=1','-f','-'],input=(ROOT/'sql/postgresql'/prerequisite).read_bytes())
        if manifest.get('security_requirements'):
            with connect(config('postgres')) as admin:
                backup.prepare_recovery_roles(admin, manifest)
        provisioned = time.monotonic()

        def utility(args, *, input=None):
            verify_container()
            return run(['docker', 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql',
                        '-e', 'PGUSER=jobwatch_migrator', '-e', 'PGDATABASE=' + DATABASE, name, *args], input=input)

        with connect(config('jobwatch_migrator')) as conn:
            restored = backup.restore(conn, archive, manifest, execute=utility)
            restored_at = time.monotonic()
            actual = backup.fingerprint(conn)
            assert actual == manifest['fingerprint']
            assert actual['tables']['triage']['count'] == 1
            assert actual['tables']['posting_events']['count'] == 1
            native = conn.execute('SELECT evidence FROM jobwatch.posting_versions').fetchall()
            assert all('\0' in json.loads(bytes(r['evidence']))['description'] for r in native)
            views = {r['viewname']: conn.execute('SELECT count(*) AS n FROM jobwatch.' + r['viewname']).fetchone()['n']
                     for r in conn.execute("SELECT viewname FROM pg_views WHERE schemaname='jobwatch'")}
            try:
                backup.restore(conn, archive, manifest, execute=utility)
            except backup.BackupError as exc:
                assert 'fresh' in str(exc)
            else:
                raise AssertionError('Populated target accepted')
            version = conn.execute('SELECT version() AS version').fetchone()['version']
        roles = {}
        import psycopg
        for role in ('jobwatch_collector', 'jobwatch_owner'):
            with connect(config(role)) as conn:
                assert len(research.search(conn, 'Python')['items']) == 2
                assert len(research.search(conn, 'Python', method='fts')['items']) == 2
                queries = ["UPDATE jobwatch.posting_versions SET title='forbidden'", 'DELETE FROM jobwatch.owner_audit']
                if role == 'jobwatch_collector':
                    queries += ['SELECT * FROM jobwatch.applications', 'SELECT * FROM jobwatch.triage',
                                'SELECT * FROM jobwatch.owner_audit']
                else:
                    assert conn.execute('SELECT count(*) AS n FROM jobwatch.applications').fetchone()['n'] == 1
                    assert conn.execute('SELECT count(*) AS n FROM jobwatch.owner_audit').fetchone()['n'] == 4
                    data = reports.snapshot(conn, since='2020-01-01T00:00:00Z')
                    assert data['attention_total'] == 1
                    for weekly in (True, False):
                        assert 'SYNTHETIC PRIVATE NOTE ONLY' not in reports.markdown(data, weekly=weekly)
                for query in queries:
                    try:
                        with conn.transaction():
                            conn.execute(query)
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError('Restricted SQL action accepted')
                roles[role] = {'reads_and_denials': True}
        if 'agent_audit_summary' in views:
            for role in ('jobwatch_agent_readonly','jobwatch_agent_owner','jobwatch_calendar_reader'):
                with connect(config(role)) as conn:
                    assert conn.execute('SELECT count(*) AS n FROM jobwatch.applications').fetchone()['n']==1
                    assert conn.execute('SELECT count(*) AS n FROM jobwatch.agent_audit_summary').fetchone()['n']==4
                    assert len(research.search(conn,'Python')['items'])==2
                    for query in ('SELECT notes FROM jobwatch.applications','SELECT old_row FROM jobwatch.owner_audit',
                                  "UPDATE jobwatch.posting_versions SET title='forbidden'"):
                        try:
                            with conn.transaction():
                                conn.execute('SET TRANSACTION READ WRITE');conn.execute(query)
                        except psycopg.errors.InsufficientPrivilege:pass
                        else:raise AssertionError('Restored integration role exceeded its authority')
                    roles[role]={'reads_and_denials':True}
        finished = time.monotonic()
        result = {'schema': 'jobwatch.offhost-recovery/1', 'archive_sha256': manifest['archive_sha256'],
                  'archive_bytes': archive.stat().st_size, 'restore': restored, 'fingerprint': actual,
                  'views': views, 'roles': roles, 'nul_evidence': True, 'populated_target_refused': True,
                  'postgres': version, 'python': platform.python_version(), 'psycopg': psycopg.__version__,
                  'image': image, 'architecture': platform.machine(), 'network': 'none', 'ports_published': False,
                  'ec2_database_accessed': False, 'production_custody_established': False,
                  'seconds': {'startup_and_security_provision': round(provisioned - started, 3),
                              'restore_and_core_equality': round(restored_at - provisioned, 3),
                              'total_with_runtime_checks': round(finished - started, 3)}}
        (output / 'result.json').write_text(json.dumps(result, indent=2))
        success = True
        return result
    finally:
        (output / 'commands.json').write_text(json.dumps(commands, indent=2))
        if created and success:
            verify_container()
            # Return ownership of this private temporary socket mount to its creator.
            run(['docker', 'exec', '--user', 'root', name, 'chown', '-hR',
                 f'{os.getuid()}:{os.getgid()}', '/var/run/postgresql'])
            run(['docker', 'rm', '-fv', name])  # Only this new, verified disposable local target.
            shutil.rmtree(socket_parent)
            (output / 'commands.json').write_text(json.dumps(commands, indent=2))
        elif created:
            (output / 'FAILED_CONTAINER_RETAINED.txt').write_text(name + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--confirm-synthetic', action='store_true', required=True)
    args = parser.parse_args()
    result = recover(args.archive, args.manifest, args.output)
    print(json.dumps({'exact_restore': result['restore']['exact_rows_schema_and_sequences'],
                      'network': 'none', 'ec2_database_accessed': False, 'seconds': result['seconds']}))
