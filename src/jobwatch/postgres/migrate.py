"""Explicit, transactional plain-SQL migrations; no provisioning, resets or adoption."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from hashlib import sha256
from importlib.resources import files
import re
import sys

from .connection import DatabaseError, connect

# Schema migration serialization, independent of the later collector's source-row locks.
MIGRATION_LOCK = 0x4A4F425741544348
FILENAME = re.compile(r'(\d{4})_[a-z0-9_]+\.sql')


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    checksum: str
    sql: str = field(repr=False)


def load_migrations(directory=None) -> list[Migration]:
    directory = files('jobwatch.postgres').joinpath('migrations') if directory is None else directory
    migrations = []
    try:
        paths = list(directory.iterdir())
    except OSError:
        raise MigrationError('Packaged migration directory is unavailable') from None
    for path in paths:
        if not path.name.endswith('.sql'):
            continue
        match = FILENAME.fullmatch(path.name)
        if match is None:
            raise MigrationError('Migration SQL filenames must match NNNN_name.sql')
        try:
            raw = path.read_bytes()
            sql = raw.decode('utf-8')
        except (OSError, UnicodeError):
            raise MigrationError(f'Could not read migration {path.name} as UTF-8 SQL') from None
        migrations.append(Migration(int(match[1]), path.name, sha256(raw).hexdigest(), sql))
    migrations.sort(key=lambda m: m.version)
    if not migrations or [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise MigrationError('Migration versions must be unique and consecutive, beginning at 0001')
    return migrations


def migrate(conn, migrations: list[Migration] | None = None) -> list[str]:
    """Atomically apply pending packaged migrations, checking the entire recorded prefix.

    Requires an idle autocommit connection using schema-owner credentials. All
    pending SQL and ledger updates commit together; any error rolls them all back.
    """
    import psycopg
    from psycopg.pq import TransactionStatus

    plan = load_migrations() if migrations is None else migrations
    if not plan or [m.version for m in plan] != list(range(1, len(plan) + 1)):
        raise MigrationError('Migration versions must be unique and consecutive, beginning at 0001')
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise MigrationError('Migrations require an idle autocommit connection')
    active = 'migration ledger'
    try:
        with conn.transaction():
            if conn.execute('SELECT current_user AS name').fetchone()['name'] in ('jobwatch_owner', 'jobwatch_collector'):
                raise MigrationError('Use separate migration credentials, not a runtime role')
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (MIGRATION_LOCK,))
            exists = conn.execute("SELECT to_regnamespace('jobwatch') IS NOT NULL AS present").fetchone()['present']
            if exists:
                if conn.execute("SELECT to_regclass('jobwatch.schema_migrations') IS NULL AS missing").fetchone()['missing']:
                    raise MigrationError('Existing jobwatch schema has no migration ledger; refusing to adopt it')
            else:
                conn.execute('CREATE SCHEMA jobwatch')
                conn.execute('REVOKE ALL ON SCHEMA jobwatch FROM PUBLIC')
                conn.execute('''CREATE TABLE jobwatch.schema_migrations (
                    version integer PRIMARY KEY CHECK (version > 0),
                    filename text NOT NULL UNIQUE,
                    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
                    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
                )''')
            applied = conn.execute('SELECT version,filename,sha256 FROM jobwatch.schema_migrations ORDER BY version').fetchall()
            if [r['version'] for r in applied] != [m.version for m in plan[:len(applied)]]:
                raise MigrationError('Database migration history is not a prefix of the available migrations')
            for row, migration in zip(applied, plan):
                if (row['filename'], row['sha256']) != (migration.filename, migration.checksum):
                    raise MigrationError(f'Applied migration {migration.version:04d} has changed; restore the original file')
            pending = plan[len(applied):]
            for migration in pending:
                active = migration.filename
                conn.execute(migration.sql, prepare=False)
                conn.execute('INSERT INTO jobwatch.schema_migrations(version,filename,sha256) VALUES (%s,%s,%s)',
                             (migration.version, migration.filename, migration.checksum))
        return [m.filename for m in pending]
    except psycopg.Error as exc:
        # SQLSTATE is useful; SQL text/DETAIL and connection settings need not enter logs.
        raise MigrationError(f'Failed applying {active} (SQLSTATE {exc.sqlstate or "connection error"}); reconnect and rerun to verify migration state') from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Apply packaged PostgreSQL migrations using JOBWATCH_MIGRATION_DATABASE_URL')
    parser.parse_args(argv)
    try:
        with connect(purpose='migration') as conn:
            applied = migrate(conn)
        for filename in applied:
            print(f'Applied {filename}')
        print('Database schema is up to date')
        return 0
    except (DatabaseError, MigrationError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
