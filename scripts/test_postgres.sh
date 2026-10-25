#!/usr/bin/env bash
# Creates its own empty database, never consumes a user-supplied database URL.
set -euo pipefail
if [[ $# -ne 0 && ( $# -ne 4 || $1 != --rehearse-import ) ]]; then
  printf '%s\n' 'Usage: scripts/test_postgres.sh [--rehearse-import REPO COMMIT NEW_REPORT_DIRECTORY]' >&2
  exit 2
fi
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python=${PYTHON:-python3}
"$python" -c 'import psycopg' || { printf '%s\n' 'Install optional dependencies: pip install -e ".[postgres]"' >&2; exit 2; }
docker build -q -f "$repo/tests/postgres/Dockerfile.vector" -t jobwatch-postgres-vector:17.11-0.8.7 "$repo" >/dev/null
# An immutable local image ID, verified by the same labelled disposable harness.
image=$(docker image inspect jobwatch-postgres-vector:17.11-0.8.7 --format '{{.Id}}')
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
docker exec -i "$name" psql -X -U postgres -d jobwatch_foundation_test -v ON_ERROR_STOP=1 -f - \
  < "$repo/sql/postgresql/embedding_access.sql" >/dev/null
# All fresh test databases inherit only the reviewed extension, never application data.
docker exec -i "$name" psql -X -U postgres -d template1 -v ON_ERROR_STOP=1 -f - \
  < "$repo/sql/postgresql/vector_extension.sql" >/dev/null
docker exec -i "$name" psql -X -U postgres -d jobwatch_foundation_test -v ON_ERROR_STOP=1 -f - \
  < "$repo/sql/postgresql/vector_extension.sql" >/dev/null
# The test target is derived from the verified Docker binding, never these ambient settings.
unset JOBWATCH_DATABASE_URL JOBWATCH_MIGRATION_DATABASE_URL JOBWATCH_EMBEDDING_DATABASE_URL JOBWATCH_CHAT_DATABASE_URL PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGSERVICE PGSERVICEFILE PGPASSWORD PGPASSFILE PGOPTIONS
export JOBWATCH_PG_TEST_CONTAINER="$name" JOBWATCH_PG_TEST_TOKEN="$token"
export PYTHONPATH="$repo/src${PYTHONPATH:+:$PYTHONPATH}"
"$python" -m unittest discover -s "$repo/tests/postgres" -v
if [[ $# -eq 4 ]]; then
  "$python" "$repo/tests/postgres/rehearsal.py" "$2" "$3" "$4"
fi
docker exec "$name" psql -X -U postgres -d jobwatch_foundation_test -Atc 'SELECT version();'
"$python" -c 'import platform,psycopg; print("Python",platform.python_version()); print("psycopg",psycopg.__version__); print("libpq",psycopg.pq.version())'
printf 'Image: %s\n' "$image"
