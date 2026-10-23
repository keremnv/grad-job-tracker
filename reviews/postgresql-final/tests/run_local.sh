#!/usr/bin/env bash
# Review-only. Creates/removes its own two containers; accepts NO production DSN.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
repo=$(git -C "$here" rev-parse --show-toplevel)
scratch=$(mktemp -d /tmp/jobwatch-final-harness.XXXXXX)
out=${RESULTS_DIR:-$scratch/results}
mkdir -p "$out"
original="jw-original-review-$$"
final="jw-final-review-$$"
cleanup() { docker rm -fv "$original" "$final" >/dev/null 2>&1 || true; rm -rf "$scratch/venv"; }
trap cleanup EXIT
img=pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d
docker run -d --name "$original" --network none -e POSTGRES_HOST_AUTH_METHOD=trust "$img" >/dev/null
docker run -d --name "$final" -p 127.0.0.1::5432 -e POSTGRES_HOST_AUTH_METHOD=trust "$img" >/dev/null
for name in "$original" "$final"; do
  ready=false
  for attempt in {1..100}; do
    if docker exec "$name" pg_isready -U postgres >/dev/null 2>&1; then ready=true; break; fi
    sleep .1
  done
  "$ready"
done
docker exec -i "$original" psql -U postgres -v ON_ERROR_STOP=1 -f - < "$here/original/pgtest/001_schema.sql" > "$out/original-schema.txt" 2>&1
python3 - "$here" "$scratch" "$original" <<'PY'
from pathlib import Path
import sys
here,scratch,name=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
old='/tmp/claude-1000/-home-kerem-Desktop-Job-grad-job-tracker-starter-updated/57806037-cfdf-4bc3-8442-31cda8b03bb7/scratchpad/pgtest/e1.out'
for file in ('t1_constraints.sh','t2_concurrency.sh','urlcheck.py'):
    s=(here/'original/pgtest'/file).read_text().replace('jw-review-pg',name).replace(old,str(scratch/'e1.out'))
    (scratch/file).write_text(s)
PY
bash "$scratch/t1_constraints.sh" > "$out/original-constraints.txt" 2>&1
if rg -q '^BAD' "$out/original-constraints.txt"; then cat "$out/original-constraints.txt"; exit 1; fi
sed -i 's/[[:blank:]]*$//' "$out/original-constraints.txt"
bash "$scratch/t2_concurrency.sh" > "$out/original-concurrency.txt" 2>&1
python3 "$scratch/urlcheck.py" "$repo" > "$out/original-url.txt"
docker exec -i "$final" psql -U postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE final_review' >/dev/null
docker exec -i "$final" psql -U postgres -d final_review -v ON_ERROR_STOP=1 -f - < "$here/001_proposed_schema.sql" > "$out/final-schema.txt" 2>&1
python3 -m venv "$scratch/venv"
"$scratch/venv/bin/pip" install 'psycopg[binary]==3.2.13' > "$out/dependencies.txt" 2>&1
binding=$(docker port "$final" 5432/tcp)
export JOBWATCH_REVIEW_PORT=${binding##*:}
"$scratch/venv/bin/python" "$here/tests/test_review.py" > "$out/final-tests.txt" 2>&1
docker exec -i "$final" psql -U postgres -d final_review -v ON_ERROR_STOP=1 -f - < "$here/002_deferred_embeddings.sql" > "$out/deferred-schema.txt" 2>&1
"$scratch/venv/bin/python" "$here/tests/test_retrieval.py" > "$out/retrieval-tests.txt" 2>&1
(cd "$repo" && "$scratch/venv/bin/python" "$here/tests/reconcile_probe.py") > "$out/reconciliation.json"
docker exec "$final" psql -U postgres -d final_review -Atc 'SELECT version(); SELECT extname,extversion FROM pg_extension;' > "$out/versions.txt"
"$scratch/venv/bin/python" -c 'import platform,psycopg; print(platform.python_version()); print("psycopg",psycopg.__version__); print("libpq",psycopg.pq.version())' >> "$out/versions.txt"
docker image inspect "$img" --format '{{json .RepoDigests}}' >> "$out/versions.txt"
(cd "$repo" && PYTHONPATH="$repo/src" "$scratch/venv/bin/python" -m unittest discover -s tests -v) > "$out/offline-tests.txt" 2>&1
printf 'Review checks passed. Results: %s\n' "$out"
