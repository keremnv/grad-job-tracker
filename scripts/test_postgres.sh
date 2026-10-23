#!/usr/bin/env bash
# Creates its own empty database, never consumes a user-supplied database URL.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python=${PYTHON:-python3}
"$python" -c 'import psycopg' || { printf '%s\n' 'Install optional dependencies: pip install -e ".[postgres]"' >&2; exit 2; }
image=postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27
name="jobwatch-foundation-test-$$"
token=$("$python" -c 'from uuid import uuid4; print(uuid4())')
cleanup() { docker rm -fv "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$name" --label jobwatch.test=postgres-foundation --label "jobwatch.test-token=$token" \
  -p 127.0.0.1::5432 -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=jobwatch_foundation_test "$image" >/dev/null
ready=false
for attempt in {1..100}; do
  # The image's initialization server is socket-only and briefly stops; wait for the final TCP server.
  if docker exec "$name" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then ready=true; break; fi
  sleep .1
done
if ! "$ready"; then printf '%s\n' 'Disposable PostgreSQL did not become ready' >&2; exit 1; fi
docker exec -i "$name" psql -X -U postgres -d jobwatch_foundation_test -v ON_ERROR_STOP=1 -f - \
  < "$repo/sql/postgresql/bootstrap.sql" >/dev/null
# The test target is derived from the verified Docker binding, never these ambient settings.
unset JOBWATCH_DATABASE_URL JOBWATCH_MIGRATION_DATABASE_URL PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGSERVICE PGSERVICEFILE PGPASSWORD PGPASSFILE PGOPTIONS
export JOBWATCH_PG_TEST_CONTAINER="$name" JOBWATCH_PG_TEST_TOKEN="$token"
export PYTHONPATH="$repo/src${PYTHONPATH:+:$PYTHONPATH}"
"$python" -m unittest discover -s "$repo/tests/postgres" -v
docker exec "$name" psql -X -U postgres -d jobwatch_foundation_test -Atc 'SELECT version();'
"$python" -c 'import platform,psycopg; print("Python",platform.python_version()); print("psycopg",psycopg.__version__); print("libpq",psycopg.pq.version())'
printf 'Image: %s\n' "$image"
