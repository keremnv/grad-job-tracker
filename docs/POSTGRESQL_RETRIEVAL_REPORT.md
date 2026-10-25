# V1 local pgvector implementation results

25 October 2026. Branch `feat/pgvector-retrieval`, based on roadmap commit
`4374716`, including Stage 4C `10d5f38` and recovery `b3ba87f`.
The owner-selected model is implemented, not an alternative-model comparison.

## Delivered

- Updated [the approved plan](PGVECTOR_IMPLEMENTATION_PLAN.md) and
  [V1 roadmap](V1_SCOPE_AND_ROADMAP.md); added [setup/tool/SQL/recovery instructions](POSTGRESQL_RETRIEVAL.md).
- Migration 0004 adds one version-linked derived chunk table, exact profile/input
  SHA checks, source hashes, dimension/nonzero checks and complete-set metadata.
  Migrations 0001–0003 remain byte-for-byte unchanged.
- Separate administrator extension/worker provisioning. Runtime collectors cannot
  write derived chunks; the new worker cannot publish observations, read private
  owner data, alter evidence or administer the schema/roles. Existing owner/collector
  and audit semantics are unchanged; owner/chat receive explicit derived SELECT.
- Explicit cache-only CPU inference and resumable per-version atomic indexing,
  duplicate-worker input checks, rollback and historical retention. No model loading
  or database operation on import; no collection hook, extra service or model registry.
- Exact cosine SQL, optional BM25/FTS reciprocal-rank fusion and coverage reporting;
  existing SQL/lexical methods remain usable without vectors/model files. Existing
  CLI/read-core/MCP search share the implementation and keep all-verdict research,
  history/not-seen options, source health, read-only limits and untrusted-text labels.
- Real vector-aware dump/restore and an independent network-none container restore
  including evidence, owner/audit rows, schema, extension, permissions and sequences.

## Selected model and measured local runtime

Model: `google/embeddinggemma-2` at
`914f7f89142e33e77833254d9c9b90c3cef7303b`, native 768 dimensions.
Pinned CPU runtime: Transformers 5.20.2, Torch 2.14.1+cpu, Safetensors 0.8.0,
Tokenizers 0.23.3, Hugging Face Hub 2.3.0; full hash-locked CPU dependencies under
`requirements/embeddings-cpu.lock`. Local MCP tests use SDK 1.30.0.

Direct AutoModel/AutoTokenizer loading disables both vision/audio configs, verifies
their parameters are absent and checks CPU/float32 parameters. Google's exact
retrieval query prefix and real-title document template feed projected-token masked
mean pooling and L2 normalisation. No float16 or output truncation. This avoids
SentenceTransformers' attempted multimodal processor loading; the final clean
environment contains neither SentenceTransformers nor Pillow.

Measured on this development machine, with cached files and two CPU threads:
model load **4.328 s**, query plus document and load **4.438 s**, peak RSS
**1,931,700 KiB** (about 1.84 GiB). Loaded parameters: **271,002,624**; both outputs
768-dimensional and finite. The upstream single checkpoint is 1,488,915,288 bytes;
only its text model is loaded. This small smoke test is not an EC2 memory/latency
qualification or a retrieval-quality benchmark. Inference remains independent of
database hosting; no model is installed on the 2 GiB AWS host.

## Narrow implementation refinements

- Variable-dimension pgvector values plus a checked dimensions field allow future
  explicitly distinct profiles without changing source evidence. V1 always uses
  the selected native 768-dimensional profile; ranks never mix profiles.
- Chunk count/ordinal constraints and completeness checks exclude partial sets;
  the resumable worker can finish matching partial derived data without rewriting
  old inputs. Profile/input hashes use CHECKs rather than falsely labelling UTF-8
  conversion immutable for a generated column. No extra hash function is needed.
- The importer explicitly recognises this derived table while keeping legacy data
  reconciliation independent of generated vectors and rejecting unexpected tables.
- Backup format 2 records extension prerequisites and fingerprints derived rows,
  role limits/defaults and security. Fresh restore permits only verified extension
  members in public and rejects other objects, including functions/operators.
- Independent restore exposed collation-dependent ACL ordering (`en_US` versus
  `C.UTF-8`). Canonical `COLLATE "C"` fixes the false mismatch; no grants or
  security comparison fields were discarded. Regression restore crosses those
  collations and uses a target without networking or source-DB access.
- Format-1 archives are not rewritten/adopted. Recover them with the pinned
  pre-vector `b3ba87f` bundle, then separately test an upgrade. A populated v3
  database upgrades to v4 without changing source, collection or owner/audit rows.

## Tests actually executed

All provider responses, owner records and import repositories in these executions
are synthetic. No real production Git dataset or AWS backup was imported/read.

| Check | Result |
|---|---|
| Offline unittest suite, Python 3.11.16 / 3.12.14 / 3.13.12 | **215 passed on each** (211 existing + 4 new) |
| Full disposable PostgreSQL suite with explicit cached real-model flag | **127 passed** (116 baseline + 11 new) |
| Real EmbeddingGemma worker/search and official MCP vector call | Passed, cache-only, float32 CPU |
| Frozen synthetic importer, independent repeat imports and actual pg_dump/pg_restore | Passed within full suite |
| Fresh separate network-none vector restore, exact security/data/sequence checks | Passed within full suite |
| Existing CLI/installer/destruction-protection/off-host guard tests | **19 passed**, local containers/dummy Terraform state only |
| Terraform fmt, validate and mock tests | Passed; **5 mock tests passed**; backend disabled, no AWS calls |
| Ruff F checks, compileall, shell syntax, whitespace and local doc links | Passed |
| CPU dependency hashes and clean install without image/audio processing packages | Passed |

Principal commands (environment paths refer to ignored/local development tools):

```sh
uv pip install --python "$RETRIEVAL_PY" --require-hashes --torch-backend cpu -r requirements/embeddings-cpu.lock
PYTHONPATH=src "$RETRIEVAL_PY" -m unittest discover -s tests -q
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -q
PYTHONPATH=src /tmp/jobwatch-ops-venv/bin/python -m unittest discover -s tests -q
HF_HUB_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1 PYTHON="$RETRIEVAL_PY" scripts/test_postgres.sh
JOBWATCH_CLI_TEST_ARCHIVE="$SIGNED_LOCAL_CLI_ZIP" PYTHONPATH=src "$RETRIEVAL_PY" -m unittest discover -s infra/aws/tests -q
terraform -chdir=infra/aws init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra/aws fmt -check
terraform -chdir=infra/aws validate -no-color
terraform -chdir=infra/aws test -no-color
```

`RETRIEVAL_PY` was `/home/kerem/.cache/jobwatch-retrieval-stage/venv/bin/python`,
Python 3.12.14. The final database run also supplied four deliberately unusable
ambient application/migration/chat/embedding URLs; the launcher removed them and
derived only its own labelled loopback target. PostgreSQL 17.11, pgvector 0.8.7,
psycopg 3.2.13/libpq 170006. Extension source archive SHA-256:
`cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85`.

Initial failures were corrected and rerun: missing PostgreSQL development headers
in the test image, invalid generated-column immutability assumptions, an overly
specific BM25-rank assertion and the ACL-ordering issue. The first standalone AWS
guard invocation lacked its signed public CLI archive/provider cache; after local
prerequisite setup all 19 passed. No grant weakening or hosted repair was used.

## Boundaries and next work

No AWS API/SSM operation, Terraform apply, resource/credential change, live ATS
request, real-data import, timer activation, workflow change, main merge or cutover.
Only local/disposable tests ran. Git remains authoritative and no additional cloud
resource/inference charge was introduced. New backup code is not installed on AWS;
do not reconcile helper/user-data differences by replacing the protected instance.

Remote main advanced to `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16` during development:
another observation-only commit after `b6e0167`. It was inspected read-only, not
merged or overwritten. Final integration must preserve both and later observations.

Hosted extension/package qualification, secure agent/ChatGPT connectivity,
calendar/authorised follow-up workflow, independent owner-held backup custody and
the existing source/import/single-writer cutover gates remain separate V1 work.
ANN, rerankers, tuned retrieval and extensive natural-language evaluation remain V2.
