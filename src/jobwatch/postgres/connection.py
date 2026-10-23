"""Ordinary psycopg 3 connections, explicitly separated by credential purpose."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import TYPE_CHECKING, Literal, Mapping

if TYPE_CHECKING:
    import psycopg

Purpose = Literal['runtime', 'migration']
ENVIRONMENT = {'runtime': 'JOBWATCH_DATABASE_URL', 'migration': 'JOBWATCH_MIGRATION_DATABASE_URL'}


class DatabaseError(RuntimeError):
    """A safe configuration/connection message that contains no connection string."""


@dataclass(frozen=True)
class DatabaseConfig:
    dsn: str = field(repr=False)
    purpose: Purpose = 'runtime'
    connect_timeout: int = 10
    statement_timeout_ms: int = 30_000
    lock_timeout_ms: int = 5_000
    idle_transaction_timeout_ms: int = 60_000

    def __post_init__(self):
        if self.purpose not in ENVIRONMENT:
            raise DatabaseError('Database purpose must be runtime or migration')
        if not isinstance(self.dsn, str) or not self.dsn.strip():
            raise DatabaseError(f'{ENVIRONMENT[self.purpose]} is required')
        for name in ('connect_timeout', 'statement_timeout_ms', 'lock_timeout_ms', 'idle_transaction_timeout_ms'):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 2_147_483_647:
                raise DatabaseError(f'{name} must be a positive integer')

    @classmethod
    def from_env(cls, purpose: Purpose = 'runtime', environ: Mapping[str, str] | None = None):
        if purpose not in ENVIRONMENT:
            raise DatabaseError('Database purpose must be runtime or migration')
        env = os.environ if environ is None else environ
        defaults = {'connect_timeout': 10, 'statement_timeout_ms': 30_000,
                    'lock_timeout_ms': 5_000, 'idle_transaction_timeout_ms': 60_000}
        values = {}
        for name, default in defaults.items():
            try:
                values[name] = int(env.get('JOBWATCH_DB_' + name.upper(), default))
            except (TypeError, ValueError):
                raise DatabaseError(f'JOBWATCH_DB_{name.upper()} must be a positive integer') from None
        # Never fall back from migration credentials to runtime credentials or ambient PGDATABASE.
        return cls(env.get(ENVIRONMENT[purpose], ''), purpose, **values)


def connect(config: DatabaseConfig | None = None, *, purpose: Purpose = 'runtime') -> psycopg.Connection:
    """Return an autocommit connection; use `with conn.transaction():` for atomic writes.

    `with connect() as conn:` closes cleanly, including on errors. Queries return
    dict rows. The old file-backed collector never imports/calls this module.
    """
    config = config or DatabaseConfig.from_env(purpose)
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        raise DatabaseError('Install PostgreSQL support with: pip install -e ".[postgres]"') from None
    conn = None
    try:
        # Fixed numeric/constant options override DSN options intentionally. TLS/service/passfile
        # settings remain ordinary libpq DSN settings; no secret is ever interpolated into SQL.
        options = (f'-c statement_timeout={config.statement_timeout_ms} '
                   f'-c lock_timeout={config.lock_timeout_ms} '
                   f'-c idle_in_transaction_session_timeout={config.idle_transaction_timeout_ms} '
                   '-c search_path=jobwatch,pg_catalog')
        conn = psycopg.connect(config.dsn, autocommit=True, row_factory=dict_row,
                               connect_timeout=config.connect_timeout, options=options,
                               application_name=f'jobwatch-{config.purpose}')
        conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        return conn
    except (psycopg.Error, ValueError):
        if conn is not None:
            conn.close()
        # Driver messages may include hosts, URLs, passwords or service-file contents.
        raise DatabaseError(f'Could not open {config.purpose} database connection; check configuration and connectivity') from None
