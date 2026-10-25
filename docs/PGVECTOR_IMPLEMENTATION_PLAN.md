# Approved stage: local pgvector retrieval

Status: approved for local/disposable implementation on `feat/pgvector-retrieval`,
based on roadmap commit `4374716`, including Stage 4C and recovery. The owner selected
EmbeddingGemma 2 explicitly. Keep the main-only observation commit untouched until
integration. No hosted installation, production data or AWS change is authorised.

## Approved choices and implementation defaults

| Choice | Recommended default | Consequence |
|---|---|---|
| Inference/data boundary | Owner-selected `google/embeddinggemma-2`, local text-only CPU inference; vision/audio encoders disabled, float32 only | No external text API or per-token bill. Do not embed owner notes/CVs. Inference is independent of database hosting. |
| Model/dimension | Native 768 dimensions, exact cosine; revision `914f7f89142e33e77833254d9c9b90c3cef7303b` | Store dimensions and exact profile identity with variable-dimension vectors; later profiles cannot mix during ranking or require changing source evidence. |
| Worker identity | A separate restricted `jobwatch_embedding_writer` login, provisioned only in disposable databases in this stage | Can read posting inputs and insert/delete derived chunks, but cannot publish observations, read owner tables, migrate or administer roles. Avoid borrowing broad collector/owner credentials. |
| Search | Exact vector search plus opt-in reciprocal-rank fusion, retaining BM25 as the existing default | Explicit methods and component ranks; no new imposed research policy or ANN infrastructure. |

The model selection is settled. The remaining defaults were approved with the local
implementation stage; exact pins, chunk budgets and SQL layout are implementation
details. No AWS deployment decision is required for this stage.

Follow the [official Google card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2):
retrieval queries use `task: search result | query: ...`; documents use
`title: {real posting title} | text: ...`. Do not use the generic title-none prompt
for a posting with a title. Disable `vision_config` and `audio_config`; never use
float16. The context budget is 8192 tokens; V1 chunks use a smaller bounded budget.
The compatible pinned runtime is Transformers 5.20.2, Torch 2.14.1 (CPU),
Safetensors 0.8.0, Tokenizers 0.23.3 and Hugging Face Hub 2.3.0. Direct text-only
AutoModel/AutoTokenizer loading avoids multimodal preprocessing dependencies;
masked mean pooling and L2 normalisation follow the checkpoint's architecture.
[pgvector](https://github.com/pgvector/pgvector) supports exact cosine search
without an ANN index. Pin a reviewed release (currently documented as 0.8.7)
and checksums in a test image built on the existing pinned PostgreSQL 17.11 base.

## Schema, evidence and lifecycle

- Add `0004_embeddings.sql`; never edit migrations 0001–0003. Adapt the review's
  deferred `embedding_chunks` example: FK to `posting_versions.id`; model identity
  and immutable revision; input-recipe and chunker revisions; ordinal; exact input
  text/SHA-256 and source hashes; finite, nonzero vector with checked dimensions;
  chunk count/ordinal/offsets and creation time. Composite primary key
  prevents duplicate version/profile/ordinal rows. No second evidence/event stream.
- Provision the extension separately as administrator; ordinary migration/runtime
  roles do not gain extension-installation privileges. Migration fails clearly if
  prerequisites are missing. Existing owner/collector/migrator/audit authority stays
  intact; grant owner/chat SELECT explicitly, worker derived writes only.
- Embed retained v1 company/title/location/department/team/employment type/salary
  and description. Any text rendering is a named retrieval recipe, not a rewritten
  normaliser. Preserve NULL-description provenance. Retain native NUL bytes untouched.
- Deterministic token-aware chunks cover the full available text: at most
  1024 tokens including the real-title prefix, 128-token body overlap. Enforce the real
  encoded limit, query-prefix recipe and stable offsets; never silently truncate.
- An explicit resumable CLI discovers missing tuples, computes outside transactions,
  then inserts a complete version/profile chunk set atomically. Duplicate workers
  validate matching inputs/provenance and reuse committed rows; conflicts fail, never
  overwrite evidence. Pin runtime details; do not promise cross-hardware bit equality.
- Current search joins `version_id = postings.current_version_id` and exact profile
  revisions. Late worker results remain historical. Report missing-vector coverage;
  no stale flag, vector publication pointer, queue, model registry or collector hook.
  Incomplete chunk sets are excluded and reported as missing coverage.
  Historical indexing is explicit, derived-row removal is explicit, evidence is kept.

## SQL, tools and hybrid behaviour

Expose ordinary parameterised SQL using `<=>`, version/posting/source-health joins,
all verdicts, listed/current defaults and existing historical/not-seen options.
Rank a version by its best chunk; return the actual chunk, cosine distance, profile,
version/provenance and freshness. Stable ties use domain/version/chunk identities.

Extend existing `search` with explicit `vector` and `hybrid` methods in CLI and
read-tool/MCP adapters, not a new query language or mandatory workflow. Query
encoding runs outside SQL transactions from a preloaded/local-only model cache,
with bounded input/concurrency/runtime. Missing models fail clearly; lexical SQL
and BM25/FTS remain usable. Preserve read-only sessions, limits and untrusted-text
labels. Ordinary SQL clients can supply vectors directly without the Python encoder.

Hybrid defaults to BM25 plus vector top-100 candidates per branch, deduplicated by
version before reciprocal-rank fusion (`k=60`, equal weights); FTS is an explicit
lexical alternative. Return component ranks and candidate bounds. Lexical-only
matches remain eligible when vectors are missing. No raw-score addition, reranker
or claim that a small candidate union exhaustively ranks the corpus.

## Implementation file inventory

New:

- `src/jobwatch/postgres/migrations/0004_embeddings.sql`
- `src/jobwatch/postgres/embeddings.py` (input recipe, local encoder, explicit worker)
- `sql/postgresql/vector_extension.sql`, `sql/postgresql/embedding_access.sql`
- `tests/postgres/Dockerfile.vector`, `tests/postgres/test_embeddings.py`
- `tests/test_embedding_inputs.py`, `docs/POSTGRESQL_RETRIEVAL.md`
- `requirements/embeddings-cpu.lock`, `docs/POSTGRESQL_RETRIEVAL_REPORT.md`

Modify:

- `pyproject.toml` (optional dependencies and worker entry point).
- `src/jobwatch/postgres/{research,operations,read_tools,read_mcp}.py`.
- `sql/postgresql/{chat_readonly,operational_queries}.sql` (explicit grants/examples).
- `src/jobwatch/postgres/importer.py`: its exact table-inventory check currently
  rejects added tables. Recognise the declared derived table without treating
  embeddings as legacy import data or accepting arbitrary unexpected tables.
- `infra/aws/scripts/database_backup.py`: fingerprint derived rows/profile/extension
  requirements. Its fresh-target guard currently refuses public functions; allow
  only the verified prerequisite extension's members, never arbitrary public objects.
  Provision extension before restore; a schema-only dump does not install it.
- `scripts/test_postgres.sh`, `tests/postgres/{support,test_migrations,test_importer,
  test_read_tools,test_aws_backup}.py`, `infra/aws/tests/recover_offhost.py`.
- `docs/{POSTGRESQL_DEVELOPMENT,CHATGPT_READ_TOOLS,V1_SCOPE_AND_ROADMAP}.md`.

Backup tooling changes are local tests/source only, not installation on AWS. Keep
existing archives recoverable with their pinned historical recovery bundle; prove
new vector archives restore independently with explicit extension prerequisites.

## Acceptance and tests

1. Fresh/repeated migration, missing-extension failure and unchanged old checksums;
   PostgreSQL 17 tests use only verified disposable targets.
2. Correct FK/dimension/nonfinite/zero/uniqueness enforcement; actor/role boundaries
   unchanged; embedding worker cannot read private data or publish collection.
3. Exact input hashes/chunk coverage, Unicode/NUL safety, long and absent text;
   replay, concurrent workers, mid-write rollback and late-worker stale exclusion.
4. Known-vector distance/order tests plus a small real pinned-model CPU smoke test
   with synthetic English queries; no large natural-language evaluation programme.
5. Vector/hybrid SQL, exclusions, historical/not-seen scope, missing-vector disclosure,
   component ranks, C++/C#/C lexical regressions and local MCP interoperability;
   existing denial/timeout/size tests remain passing.
6. Frozen importer/reconciliation and real dump/restore including vectors, extension
   identity, roles/grants, evidence, audit, owner records and sequences. Test the
   exact extension-aware empty-target guard, not a broadly weakened restore check.
7. Run the existing offline suite (last recorded: 211) on Python 3.11–3.13 and full
   PostgreSQL suite (116 baseline), plus import/restore rehearsal; record new counts,
   model pins, footprint and timing. No live ATS or AWS test is part of acceptance.

## Costs and exclusions

No new AWS resource or paid inference API. Local compute/download/storage is needed;
measure dependencies, model RSS and batch/query timing before considering the 2 GiB
host. A 768-dimensional vector is 3,080 bytes, approximately 29.4 MiB per 10,000
chunks before text, rows, indexes and backup copies; storage grows with retained
versions. This is not a revised hosted operating-cost quote or proof of EC2 fit.

Do not change normalisation/screening, production data, AWS bootstrap/user-data,
credentials, timers, workflows or authority. Hosted extension/model installation,
an API provider, ChatGPT deployment, real-data embedding and cutover remain separate
work/approvals. ANN, tuned fusion, rerankers and retrieval optimisation stay in V2.
