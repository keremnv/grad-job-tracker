# Local pgvector retrieval

This is an explicit development path. It does not change the file-backed runtime,
AWS, collection, screening or immutable evidence. PostgreSQL hosting and model
execution are independent; the 2 GiB EC2 host is not qualified for this model.

## Model and input contract

Selected by the owner: `google/embeddinggemma-2`, revision
`914f7f89142e33e77833254d9c9b90c3cef7303b`, native **768** dimensions. Inference uses
CPU float32; vision/audio configs are both None and no corresponding parameters
are loaded. AutoModel/AutoTokenizer load local files only with remote code disabled.
Masked mean pooling of projected token states and L2 normalisation produce vectors.
No float16, dimensional truncation, multimodal processor or external inference API.
See [Google's card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2).

Queries use `task: search result | query: {query}`. Each document chunk uses
`title: {real posting title} | text: {content}`; `none` is used only for missing
titles. The versioned recipe renders retained v1 metadata/description, replacing
derived-text NUL safely, without updating the persisted source representation.
NULL descriptions remain distinguishable from supplied empty text.

Chunks cover the complete rendered body with token offsets, at most 1024 encoded
tokens including the title prefix, and 128 body tokens of overlap. Oversized titles
and inputs fail explicitly; inference never silently truncates. These offsets refer
to the declared rendered recipe body, not original HTTP/HTML bytes.

Every row links an immutable posting version and records model/revision/dimensions,
runtime, input recipe, chunker, exact input text/SHA-256, source evidence and
normalised hashes, ordinal/count/offsets. PostgreSQL CHECKs verify input/profile
hashes and vector dimension/nonzero/finite properties. The worker verifies source
hashes and complete input sets. Vector values are derived, removable data; source
evidence/history are never overwritten. Do not promise bit equality across CPUs.

The table accepts future dimensions/profiles; ranking always selects one exact
profile. Another encoder/profile requires its own explicit implementation and pins,
not an update to existing source evidence or reuse of an incompatible vector.

## Install locally

Use a dedicated environment and the hash-locked CPU dependencies:

```sh
uv venv --python 3.12 /PRIVATE/retrieval-venv
uv pip install --python /PRIVATE/retrieval-venv/bin/python --require-hashes \
  --torch-backend cpu -r requirements/embeddings-cpu.lock
uv pip install --python /PRIVATE/retrieval-venv/bin/python --no-deps -e .
# Optional MCP adapter uses the separate existing chat-mcp extra.
# Optional explicit public download; no job/query text is sent to Hugging Face.
/PRIVATE/retrieval-venv/bin/jobwatch-embeddings download-model
```

Model acquisition is separate from inference; normal search never downloads files.
Use a private local `HF_HOME` for cache location. Runtime versions are checked before
loading; optional dependencies do not affect the base file-backed application.

An administrator provisions the reviewed extension package and runs
`sql/postgresql/vector_extension.sql` in the chosen **local** database first.
Run existing `jobwatch-db-migrate` as the migration identity, then optionally provision
the worker with `sql/postgresql/embedding_access.sql`. Existing role names fail
instead of being adopted/reset. Authentication is configured outside Git.
Migrations 0001–0003 are unchanged; 0004 requires public.vector 0.8.7.

The new worker can read postings/versions and SELECT/INSERT/DELETE derived chunks.
It cannot read applications/triage/audit, publish collection, mutate evidence, CREATE
schema or administer roles. Existing runtime authority is unchanged. Owner and
separately provisioned ChatGPT read login get SELECT only on the new table.

```sh
# Credential configuration is private; never place a literal password in a command.
JOBWATCH_EMBEDDING_DATABASE_URL='service=local_embedding_worker' \
  jobwatch-embeddings index --limit 100
# Repeat to resume. --history explicitly indexes retained historical versions too.
jobwatch-pg-ops search 'graduate Python systems' --method vector
jobwatch-pg-ops search 'graduate C++ engineer' --method hybrid --lexical-method bm25
```

Worker input selection ends before inference. A complete per-version/profile chunk
set is inserted atomically, with input/provenance checks on conflict. Concurrent
replays reuse rows; discrepancies roll back instead of overwriting. A late worker
can populate an old version, which current search excludes by joining the posting's
current version. Partial sets are excluded from search and revisited by the worker.

## SQL and tools

There is no mandatory Python/MCP query facade. For example, a SQL client can bind
its own vector and exact profile ID to this ordinary prepared query:

```sql
PREPARE research_nearest(public.vector,text) AS
WITH complete AS (
  SELECT version_id FROM jobwatch.embedding_chunks WHERE profile_id=$2
  GROUP BY version_id HAVING count(*)=max(chunk_count)
    AND min(chunk_count)=max(chunk_count)
)
SELECT p.posting_key,v.id AS version_id,
       min(e.embedding OPERATOR(public.<=>) $1) AS cosine_distance
FROM jobwatch.embedding_chunks e JOIN complete c USING(version_id)
JOIN jobwatch.posting_versions v ON v.id=e.version_id
JOIN jobwatch.postings p ON p.current_version_id=v.id
WHERE e.profile_id=$2 AND e.dimensions=768 AND p.status='listed'
GROUP BY p.posting_key,v.id ORDER BY cosine_distance,p.posting_key,v.id LIMIT 20;
```

`OPERATOR(public.<=>)` is deliberate: restricted roles' search paths exclude public.
No ANN index is installed. SQL joins, filters and research strategy remain yours.

The existing `search` read tool/CLI supports bm25 (unchanged default), fts, vector
and hybrid, retaining `history`, `include_not_seen`, all-verdict research and source
health. It returns embedding coverage even for lexical methods. Missing vectors do
not prevent SQL/BM25/FTS; missing models fail vector methods with a clear code.
Missing old-schema tables are reported without breaking lexical reads.

Hybrid combines top-100 vector and BM25/FTS version candidates with equal-weight
reciprocal-rank fusion, k=60, reporting component ranks and candidate bounds. It
keeps lexical-only matches when vectors are absent. It does not combine raw scores
or claim complete-corpus hybrid ranking. Defaults are conveniences, not agent policy.

Query inference is isolated in a CPU subprocess with a 60-second wall-clock limit
and one inference slot per process, before the existing 5-second read-only SQL
transaction. SQL response/row caps and untrusted-text handling remain. Search is
still a read-only MCP tool; model acquisition/indexing is never exposed through it.
Online transport/authentication and real ChatGPT access remain unapproved/unconfigured.

## Local validation and recovery

```sh
PYTHON=/PRIVATE/retrieval-venv/bin/python scripts/test_postgres.sh
# Include real, cache-only CPU inference and an official MCP vector call:
HF_HUB_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1 \
  PYTHON=/PRIVATE/retrieval-venv/bin/python scripts/test_postgres.sh
```

The harness builds the reviewed vector source (0.8.7 plus SHA-256) on pinned
PostgreSQL 17.11, resolves its immutable image ID, and creates only verified
disposable targets. It never consumes a caller's production database URL.

Backup format 2 includes derived rows, exact extension prerequisites and worker
role security. Restore requires preprovisioned matching extension/roles, rejects
populated targets and non-extension public objects, then verifies schema, rows,
grants and sequences. A schema-filtered pg_dump does not install public.vector.
The independent recovery harness uses a fresh network-none container. Tests use
only synthetic data; no existing EC2 backup or production Git records are required.
Recreate every source grantee before restore, including the optional ChatGPT login
if provisioned; restore does not create credentials or relax security checks.

Existing AWS backup-format-1 archives remain unchanged and recoverable with the
pinned pre-vector bundle at `b3ba87f` (its original PostgreSQL image/bootstrap/helper).
Do not run a new checker expecting migration 0004 against a historical three-migration
restore. First recover with that bundle; any subsequent upgrade needs its own test.
The new helper explicitly refuses format 1 rather than silently weakening checks.

No new AWS resources or inference charges. Vector payload is 3080 bytes per
768-dimensional row, before input text, row/index overhead and retained backups.
Measure dependency/model footprint, CPU time and memory before choosing a future
inference host. No hosted extension/model installation, timer or cutover is implied.
