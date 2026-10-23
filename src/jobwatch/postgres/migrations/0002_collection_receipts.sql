SET LOCAL search_path = jobwatch, pg_catalog;
-- A version's first_run_id is provenance, not the most recent request contract.
ALTER TABLE postings ADD COLUMN last_seen_run_id uuid;
ALTER TABLE postings ADD CONSTRAINT postings_last_seen_run
  FOREIGN KEY (source_id,last_seen_run_id) REFERENCES collection_runs(source_id,id);
GRANT UPDATE(last_seen_run_id) ON postings TO jobwatch_collector;
-- Bind publication/replay to one validated payload and its original receipt time.
ALTER TABLE collection_runs ADD COLUMN payload_sha256 text CHECK (payload_sha256 ~ '^[0-9a-f]{64}$');
GRANT INSERT(details) ON collection_runs TO jobwatch_collector;
GRANT UPDATE(payload_sha256) ON collection_runs TO jobwatch_collector;
COMMENT ON COLUMN postings.last_seen_run_id IS 'Last actual positive observation, including its request contract; NULL for unknown legacy provenance.';
COMMENT ON COLUMN collection_runs.payload_sha256 IS 'Digest of validated observation including original receipt time. Publisher checks it before terminal-run replay.';
