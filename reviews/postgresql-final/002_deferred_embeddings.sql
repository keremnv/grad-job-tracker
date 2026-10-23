-- OPTIONAL LATER PROPOSAL. Not part of the first migration.
-- 384 is an example dimension; select/pin the actual model before applying.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE TABLE jobwatch.embedding_chunks (
  version_id uuid NOT NULL REFERENCES jobwatch.posting_versions(id),
  model_revision text NOT NULL, -- full immutable model/weights revision, not a mutable alias
  input_recipe text NOT NULL, -- defines fields/order and query/document preprocessing
  chunker_revision text NOT NULL,
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
  chunk_text text NOT NULL,
  embedding public.vector(384) NOT NULL CHECK (public.vector_norm(embedding) > 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (version_id,model_revision,input_recipe,chunker_revision,ordinal)
);
GRANT SELECT ON jobwatch.embedding_chunks TO jobwatch_owner,jobwatch_collector;
GRANT INSERT,DELETE ON jobwatch.embedding_chunks TO jobwatch_collector;
-- No UPDATE, ANN index, embedding job table, vector-service synchronization or stale boolean.
-- Worker computes input_sha256 from immutable version + pinned input_recipe and checks it on retries.
-- Query MUST filter exact model/input/chunker revisions and join current_version_id.
