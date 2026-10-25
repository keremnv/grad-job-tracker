-- Derived retrieval only. The administrator provisions public.vector separately.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
                 WHERE e.extname='vector' AND e.extversion='0.8.7' AND n.nspname='public') THEN
    RAISE EXCEPTION 'Provision public.vector 0.8.7 separately before migration 0004';
  END IF;
END $$;
CREATE TABLE jobwatch.embedding_chunks (
  version_id uuid NOT NULL REFERENCES jobwatch.posting_versions(id),
  model_id text NOT NULL CHECK (model_id <> '' AND model_id !~ '[\n\r]'),
  model_revision text NOT NULL CHECK (model_revision <> '' AND model_revision !~ '[\n\r]'),
  dimensions integer NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
  input_recipe text NOT NULL CHECK (input_recipe <> '' AND input_recipe !~ '[\n\r]'),
  chunker_revision text NOT NULL CHECK (chunker_revision <> '' AND chunker_revision !~ '[\n\r]'),
  runtime_revision text NOT NULL CHECK (runtime_revision <> '' AND runtime_revision !~ '[\n\r]'),
  profile_id text NOT NULL CHECK (profile_id = encode(sha256(convert_to(
    model_id || E'\n' || model_revision || E'\n' || dimensions::text || E'\n' ||
    input_recipe || E'\n' || chunker_revision || E'\n' || runtime_revision,'UTF8')),'hex')),
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  chunk_count integer NOT NULL CHECK (chunk_count > 0 AND ordinal < chunk_count),
  start_char integer NOT NULL CHECK (start_char >= 0),
  end_char integer NOT NULL CHECK (end_char >= start_char),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  normalized_sha256 text NOT NULL CHECK (normalized_sha256 ~ '^[0-9a-f]{64}$'),
  input_text text NOT NULL CHECK (input_text <> ''),
  input_sha256 text NOT NULL CHECK (input_sha256 = encode(sha256(convert_to(input_text,'UTF8')),'hex')),
  embedding public.vector NOT NULL CHECK (public.vector_dims(embedding)=dimensions AND public.vector_norm(embedding)>0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (version_id,profile_id,ordinal)
);
GRANT SELECT ON jobwatch.embedding_chunks TO jobwatch_owner;
-- Optional worker provisioning is separate; re-grant on migration in fresh test DBs.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='jobwatch_embedding_writer') THEN
    GRANT SELECT,INSERT,DELETE ON jobwatch.embedding_chunks TO jobwatch_embedding_writer;
    GRANT SELECT ON jobwatch.postings,jobwatch.posting_versions TO jobwatch_embedding_writer;
    GRANT USAGE ON SCHEMA jobwatch TO jobwatch_embedding_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='jobwatch_chat_readonly') THEN
    GRANT SELECT ON jobwatch.embedding_chunks TO jobwatch_chat_readonly;
  END IF;
END $$;
COMMENT ON TABLE jobwatch.embedding_chunks IS 'Reproducible derived retrieval; current queries join postings.current_version_id and exact profile. Never source evidence.';
