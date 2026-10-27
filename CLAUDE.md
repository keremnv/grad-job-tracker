# Claude Code operating instructions

@AGENTS.md

## Claude Code specifics

Use ordinary PostgreSQL through psql/psycopg with the appropriate SQL identity.
`jobwatch_owner` supports authorised research and audited owner transactions;
collector and migrator credentials are separate operational capabilities.
Use the SQL cookbooks and explicit `jobwatch-pg-ops` read conveniences from
AGENTS.md. The legacy `jobwatch.cli` JSON/CSV path is a frozen import/rehearsal
facility, not the production interface. Never write it or sync it with PostgreSQL.

MCP is optional. Production ChatGPT access and Proton hosting are live within the
approved scope; widening it requires separate approval. Never expose real owner
records through the earlier synthetic tunnel.
Read `docs/DEVELOPMENT.md` only for owner-requested development, and use verified
disposable PostgreSQL targets for tests.
