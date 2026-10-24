#!/usr/bin/env python3
"""Real PostgreSQL custom archives, snapshot-consistent receipts and verified fresh restores.

CLI defaults to local peer authentication. No credentials in commands or manifests.
S3 upload is explicit; the instance can write backups but cannot read/delete them.
"""
import argparse
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import uuid4

from jobwatch.postgres.connection import connect
from jobwatch.postgres.import_plan import TABLES, digest
from jobwatch.postgres.importer import check_migrations, primitive, schema_snapshot


class BackupError(RuntimeError):
    pass


def utility(arguments, *, input=None):
    # libpq obtains local identity/service settings from environment, never a DSN argument.
    process = subprocess.run(arguments, input=input, capture_output=True, timeout=2400)
    if process.returncode:
        raise BackupError('PostgreSQL utility failed; private stderr omitted')
    return process.stdout


def fingerprint(conn):
    check_migrations(conn)
    tables = {}
    for table in (*TABLES, 'owner_audit', 'schema_migrations'):
        rows = [primitive(r) for r in conn.execute('SELECT * FROM jobwatch.'+table)]
        # Exact row values, including audit IDs/clocks, generated search columns and import clocks.
        tables[table] = {'count': len(rows), 'sha256': digest(sorted(rows, key=digest))}
    security = {
        'database': conn.execute("""SELECT pg_get_userbyid(datdba) AS owner,
            ARRAY(SELECT a::text FROM unnest(COALESCE(datacl,acldefault('d',datdba))) a ORDER BY a::text) AS acl
            FROM pg_database WHERE datname=current_database()""").fetchone(),
        'role_settings': conn.execute("""SELECT pg_get_userbyid(setrole) AS role,setconfig FROM pg_db_role_setting
            WHERE setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database()) ORDER BY 1""").fetchall(),
        'roles': conn.execute("""SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls
            FROM pg_roles WHERE rolname IN ('jobwatch_owner','jobwatch_collector','jobwatch_migrator') ORDER BY rolname""").fetchall(),
        'memberships': conn.execute("""SELECT pg_get_userbyid(roleid) AS role,pg_get_userbyid(member) AS member,admin_option
            FROM pg_auth_members WHERE pg_get_userbyid(roleid) LIKE 'jobwatch_%' OR pg_get_userbyid(member) LIKE 'jobwatch_%'
            ORDER BY 1,2""").fetchall(),
    }
    return {'tables': tables, 'schema_sha256': digest(schema_snapshot(conn)), 'security_sha256': digest(security)}


def archived_sequences(data, execute):
    # pg_restore emits escaped COPY data, not executed SQL. Only fixed pg_dump setval lines
    # are parsed; no source/owner text enters the receipt or a log.
    sql = execute(['pg_restore', '--file=-', '--data-only', '--schema=jobwatch'], input=data).decode('utf-8')
    pattern = r"^SELECT pg_catalog\.setval\('jobwatch\.([a-z0-9_]+)', ([0-9]+), (true|false)\);$"
    pairs = re.findall(pattern, sql, re.MULTILINE)
    if len({p[0] for p in pairs}) != len(pairs):
        raise BackupError('Ambiguous archived sequence state')
    return {name: {'last_value': int(value), 'is_called': called == 'true'} for name, value, called in pairs}


def backup(conn, archive, *, execute=utility):
    if archive.exists() or archive.with_suffix('.json').exists():
        raise BackupError('Backup output already exists')
    if conn.info.server_version // 10000 != 17:
        raise BackupError('PostgreSQL 17 required')
    # pg_dump's external transaction uses precisely this snapshot. No lock during S3 upload.
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='30min'")
        conn.execute("SET LOCAL idle_in_transaction_session_timeout='30min'")
        before = fingerprint(conn)
        exported = conn.execute('SELECT pg_export_snapshot() AS id').fetchone()['id']
        data = execute(['pg_dump', '--format=custom', '--schema=jobwatch', '--snapshot='+exported])
    if not data.startswith(b'PGDMP'):
        raise BackupError('Not a PostgreSQL custom archive')
    sequence_state = archived_sequences(data, execute)
    expected = {r['sequencename'] for r in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='jobwatch'")}
    if set(sequence_state) != expected:
        raise BackupError('Archive sequence inventory mismatch')
    manifest = {'schema': 'jobwatch.aws-backup/1', 'created_at': datetime.now(timezone.utc).isoformat(),
                'archive_sha256': sha256(data).hexdigest(), 'archive_bytes': len(data),
                'postgres_version': conn.info.server_version, 'fingerprint': before,
                'sequences': sequence_state, 'dump_version': execute(['pg_dump', '--version']).decode().strip()}
    archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
    manifest_path = archive.with_suffix('.json')
    fd = os.open(manifest_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(manifest, stream, sort_keys=True, indent=2)
        stream.write('\n')
    return manifest


def restore(conn, archive, manifest, *, execute=utility):
    data = archive.read_bytes()
    if manifest.get('schema') != 'jobwatch.aws-backup/1' or sha256(data).hexdigest() != manifest['archive_sha256']:
        raise BackupError('Archive checksum/manifest mismatch')
    # Even an empty migrated schema is refused. No --clean, drop, truncate or trigger bypass.
    if conn.execute("SELECT count(*) AS n FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')").fetchone()['n']:
        raise BackupError('Restore target must be fresh, without an application schema')
    if conn.execute("SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'").fetchone()['n']:
        raise BackupError('Restore target contains public objects')
    if conn.execute("SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'").fetchone()['n']:
        raise BackupError('Restore target contains public functions')
    if conn.info.server_version // 10000 != 17:
        raise BackupError('PostgreSQL 17 required')
    execute(['pg_restore', '--exit-on-error', '--single-transaction', '--dbname='+conn.info.dbname], input=data)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        actual = fingerprint(conn)
        sequences = {r['sequencename']: dict(conn.execute('SELECT last_value,is_called FROM jobwatch.'+r['sequencename']).fetchone())
                     for r in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='jobwatch'")}
        if actual != manifest['fingerprint'] or sequences != manifest['sequences']:
            raise BackupError('Restored database differs; preserve this non-authoritative target for investigation')
        for view in ('research_postings', 'opportunity_workflow', 'source_health'):
            conn.execute('SELECT count(*) FROM jobwatch.'+view).fetchone()
    return {'schema': 'jobwatch.aws-restore/1', 'archive_sha256': manifest['archive_sha256'],
            'exact_rows_schema_and_sequences': True, 'ready_for_cutover': False}


def upload(archive, config, *, run_command=subprocess.run):
    """SSE-S3 + validated S3 SHA-256 receipts. No GetObject/HeadObject permission needed."""
    prefix = 'backups/'+datetime.now(timezone.utc).strftime('%Y/%m/%d/')+uuid4().hex
    receipts = []
    for path in (archive, archive.with_suffix('.json')):  # Manifest last marks a complete backup.
        data = path.read_bytes()
        if len(data) > 4_000_000_000:
            raise BackupError('Archive exceeds tested single-PutObject limit')
        checksum = base64.b64encode(sha256(data).digest()).decode('ascii')
        key = prefix+('.dump' if path == archive else '.json')
        result = run_command(['aws', 's3api', 'put-object', '--region', config['region'],
                              '--bucket', config['bucket'], '--key', key, '--body', str(path),
                              '--server-side-encryption', 'AES256', '--checksum-algorithm', 'SHA256',
                              '--checksum-sha256', checksum, '--output', 'json'],
                             capture_output=True, text=True, timeout=1200)
        if result.returncode:
            raise BackupError('S3 backup upload failed; private stderr omitted')
        receipt = json.loads(result.stdout)
        if receipt.get('ChecksumSHA256') != checksum or not receipt.get('VersionId'):
            raise BackupError('S3 did not confirm checksum and object version')
        receipts.append({'key': key, 'version_id': receipt['VersionId'], 'sha256': sha256(data).hexdigest()})
        if path == archive:
            # The completion manifest identifies the exact verified archive version for recovery.
            manifest_path = archive.with_suffix('.json')
            manifest = json.loads(manifest_path.read_text())
            manifest['archive_object'] = receipts[-1]
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('backup')
    b.add_argument('--output', type=Path)
    b.add_argument('--upload', action='store_true')
    r = sub.add_parser('restore')
    r.add_argument('archive', type=Path)
    r.add_argument('--manifest', type=Path, required=True)
    r.add_argument('--confirm-fresh-target', action='store_true', required=True)
    args = parser.parse_args()
    # Unix-peer production utility settings. No inherited password/host/service surprises.
    for key in tuple(os.environ):
        if key.startswith('PG'):
            os.environ.pop(key)
    os.environ.update(PGHOST='/run/jobwatch-postgresql', PGDATABASE='jobwatch', PGUSER='jobwatch_migrator')
    try:
        with connect(purpose='migration') as conn:
            if conn.info.host != '/run/jobwatch-postgresql' or conn.info.user != 'jobwatch_migrator':
                raise BackupError('CLI requires local peer migration identity')
            os.environ['PGDATABASE'] = conn.info.dbname
            if args.command == 'restore':
                print(json.dumps(restore(conn, args.archive, json.loads(args.manifest.read_text()))))
            else:
                if not args.output and not args.upload:
                    raise BackupError('Choose a private output path or --upload')
                with tempfile.TemporaryDirectory(prefix='jobwatch-backup-') as directory:
                    archive = args.output or Path(directory)/'database.dump'
                    manifest = backup(conn, archive)
                    result = {'archive_sha256': manifest['archive_sha256'], 'archive_bytes': manifest['archive_bytes']}
                    if args.upload:
                        config = json.loads(Path('/etc/jobwatch/aws.json').read_text())
                        upload(archive, config)
                        result['s3_checksum_and_version_confirmed'] = True
                    print(json.dumps(result))
        return 0
    except Exception:
        print('Backup/restore failed; private diagnostics omitted. Do not promote or overwrite a database.', file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
