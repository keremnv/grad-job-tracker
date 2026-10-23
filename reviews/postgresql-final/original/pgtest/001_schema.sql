-- Reviewer's proposed minimal schema (scratch validation only; not applied anywhere real).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA jobwatch;
SET search_path = jobwatch, public;

-- ---------------------------------------------------------------- sources
CREATE TABLE employers (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug          text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
  name          text NOT NULL,
  careers_url   text CHECK (careers_url ~ '^https://'),
  watch_status  text NOT NULL DEFAULT 'unverified'
                CHECK (watch_status IN ('open','register_interest','opening_later','listed_in_feed','unverified')),
  watch_checked_at timestamptz,
  notes         text
);

CREATE TABLE sources (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  provider      text NOT NULL CHECK (provider IN ('greenhouse','lever','ashby')),
  board         text NOT NULL CHECK (board ~ '^[A-Za-z0-9_-]+$'),
  region        text NOT NULL DEFAULT 'global' CHECK (region IN ('global','eu')),
  employer_id   bigint REFERENCES employers,
  status        text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','monitored','paused','retired')),
  validation_url text CHECK (validation_url ~ '^https://'),
  mapping_verified_at timestamptz,
  mapping_verified_by text,
  mapping_evidence text,
  discovered_by text,
  discovered_at timestamptz NOT NULL DEFAULT now(),
  last_published_run_id uuid,
  CHECK (provider <> 'ashby' OR region = 'global'),
  -- Activation gate as a constraint: no monitored source without employer association evidence.
  CHECK (status <> 'monitored' OR (employer_id IS NOT NULL AND validation_url IS NOT NULL
                                   AND mapping_verified_at IS NOT NULL))
);
-- posting_key = provider:lower(board):native_id has no region, so the key namespace is (provider, lower(board)).
CREATE UNIQUE INDEX sources_key_namespace ON sources (provider, lower(board));

CREATE TABLE collection_runs (
  id            uuid PRIMARY KEY,                         -- created before the fetch; the idempotency key
  source_id     bigint NOT NULL REFERENCES sources,
  mode          text NOT NULL CHECK (mode IN ('publish','validate')),
  started_at    timestamptz NOT NULL DEFAULT now(),       -- database clock, before any network request
  fetched_at    timestamptz,
  finished_at   timestamptz,
  status        text NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','published','validated','failed','superseded')),
  request_version text NOT NULL,                          -- API URL/parameters contract, e.g. 'ashby:v1+comp'
  adapter_version text NOT NULL,
  source_records int, vacancies int, skipped_non_vacancies int,
  error         text,
  ci_run        text,                                     -- GitHub run id/attempt: traceability, not identity
  CHECK ((status = 'failed') = (error IS NOT NULL)),
  CHECK (status NOT IN ('published','validated') OR vacancies IS NOT NULL)
);
CREATE INDEX ON collection_runs (source_id, started_at DESC);
ALTER TABLE sources ADD FOREIGN KEY (last_published_run_id) REFERENCES collection_runs;

-- ---------------------------------------------------------------- corpus
CREATE TABLE postings (
  posting_key   text PRIMARY KEY CHECK (posting_key ~ '^(greenhouse|lever|ashby):[a-z0-9_-]+:[A-Za-z0-9_-]+$'),
  source_id     bigint NOT NULL REFERENCES sources,
  native_id     text NOT NULL,
  status        text NOT NULL CHECK (status IN ('listed','not_seen')),
  first_seen_at timestamptz NOT NULL,
  first_seen_basis text NOT NULL CHECK (first_seen_basis IN ('observed','index_backfill','legacy_retained')),
  missing_since timestamptz,
  last_seen_at  timestamptz,               -- frozen at absence; while listed, use source_health.last_success_at
  current_version_id bigint,               -- NULL only for imported legacy rows with no provider payload
  normalizer_version text NOT NULL,
  title         text NOT NULL,
  posting_url   text NOT NULL CHECK (posting_url ~ '^https://'),
  apply_url     text CHECK (apply_url ~ '^https://'),
  location_text text NOT NULL DEFAULT '',
  country_codes text[] NOT NULL DEFAULT '{}',   -- explicit provider evidence only; empty means unknown
  workplace     text CHECK (workplace IN ('on_site','hybrid','remote')),   -- NULL means not stated
  employment_type text,
  department    text,
  team          text,
  compensation_text text,
  description_text text,                   -- NULL: not retained; '': provider supplied none
  search_tsv    tsvector GENERATED ALWAYS AS (
                  setweight(to_tsvector('english', title), 'A') ||
                  setweight(to_tsvector('english', coalesce(department,'') || ' ' || coalesce(team,'') || ' ' || location_text), 'B') ||
                  setweight(to_tsvector('english', coalesce(description_text,'')), 'D')) STORED,
  UNIQUE (source_id, native_id),
  CHECK ((status = 'not_seen') = (missing_since IS NOT NULL))
);
CREATE INDEX postings_search ON postings USING gin (search_tsv);
CREATE INDEX ON postings (source_id, status);

CREATE TABLE posting_versions (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  posting_key   text NOT NULL REFERENCES postings,
  raw_sha256    text NOT NULL CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
  raw           jsonb NOT NULL,                -- the provider's record for this posting
  origin        text NOT NULL DEFAULT 'collector' CHECK (origin IN ('collector','git_history')),
  first_run_id  uuid REFERENCES collection_runs,
  first_seen_at timestamptz NOT NULL,
  description_text text,                       -- derived; recomputed when the normalizer changes
  normalizer_version text NOT NULL,
  UNIQUE (posting_key, raw_sha256),
  UNIQUE (posting_key, id),
  CHECK ((origin = 'collector') = (first_run_id IS NOT NULL))
);
-- The current version must belong to the same posting.
ALTER TABLE postings ADD FOREIGN KEY (posting_key, current_version_id) REFERENCES posting_versions (posting_key, id);

CREATE TABLE posting_events (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  posting_key   text NOT NULL REFERENCES postings,
  run_id        uuid REFERENCES collection_runs,      -- NULL for rescreen / renormalize / import
  at            timestamptz NOT NULL,
  category      text NOT NULL CHECK (category IN ('observation','provider','normalizer','screening','import')),
  kind          text NOT NULL,
  before_version_id bigint REFERENCES posting_versions,  -- FKs make referenced evidence undeletable
  after_version_id  bigint REFERENCES posting_versions,
  changes       jsonb NOT NULL DEFAULT '{}',
  CHECK ((category, kind) IN (
     ('observation','first_seen'), ('observation','backfill'), ('observation','not_seen'), ('observation','reappeared'),
     ('provider','changed'), ('provider','baseline'),
     ('normalizer','renormalized'), ('screening','rescreened'), ('import','legacy_event')))
);
CREATE INDEX ON posting_events (posting_key, at);
CREATE INDEX ON posting_events (at);

CREATE TABLE screenings (
  posting_key   text PRIMARY KEY REFERENCES postings,
  verdict       text NOT NULL CHECK (verdict IN ('priority','review','excluded')),
  family        text NOT NULL,
  codes         text[] NOT NULL,
  rules_version text NOT NULL,
  input_sha256  text,
  basis         text NOT NULL CHECK (basis IN ('full','title_metadata_only','legacy')),
  evaluated_at  timestamptz NOT NULL
);

-- ---------------------------------------------------------------- research and owner state
CREATE TABLE verifications (                         -- append-only observations of official pages
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  posting_key   text NOT NULL REFERENCES postings,
  kind          text NOT NULL CHECK (kind IN ('application_status','deadline','description')),
  checked_at    timestamptz NOT NULL,
  recorded_at   timestamptz NOT NULL DEFAULT now(),
  checked_by    text NOT NULL CHECK (checked_by IN ('owner','agent','agent_on_owner_instruction','unrecorded_legacy')),
  url           text NOT NULL CHECK (url ~ '^https://'),
  method        text,
  status        text CHECK (status IN ('open_verified','closed_verified','unknown')),
  wording       text,
  value         text CHECK (value ~ '^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2}))?$'),
  version_id    bigint REFERENCES posting_versions,     -- the evidence a description check was made against
  description_sha256 text,                              -- legacy binding to a jobs-text hash
  CHECK (checked_at <= recorded_at),                    -- "not in the future" without a volatile CHECK
  CHECK (kind <> 'application_status' OR (status IS NOT NULL AND method IS NOT NULL)),
  CHECK (kind <> 'application_status' OR status = 'unknown'
         OR checked_by IN ('owner','agent_on_owner_instruction','unrecorded_legacy')),
  CHECK (kind <> 'deadline' OR wording IS NOT NULL),
  CHECK (kind <> 'description' OR version_id IS NOT NULL OR description_sha256 IS NOT NULL)
);
CREATE INDEX ON verifications (posting_key, kind, checked_at DESC);

CREATE TABLE leads (
  lead_key      text PRIMARY KEY CHECK (lead_key ~ '^(vacancy|programme):[a-z0-9:._-]+$'),
  kind          text NOT NULL CHECK (kind IN ('vacancy','programme')),
  employer_id   bigint REFERENCES employers,
  company       text NOT NULL,
  title         text NOT NULL,
  cycle_year    int CHECK (cycle_year BETWEEN 2020 AND 2040),
  state         text NOT NULL,
  url           text NOT NULL CHECK (url ~ '^https://'),
  official      boolean NOT NULL,
  source_note   text NOT NULL,
  wording       text,
  deadline_date date,
  deadline_wording text,
  posting_key   text REFERENCES postings,
  checked_at    timestamptz NOT NULL,
  checked_by    text NOT NULL,
  details       jsonb NOT NULL DEFAULT '{}',
  CHECK ((kind = 'vacancy' AND state IN ('listed','employer_says_open','not_listed','unverified'))
      OR (kind = 'programme' AND state IN ('open','announced_window','register_interest_only',
                                           'closed_reopening_unknown','previous_cycle','not_found'))),
  -- The existing "a stated year must be quoted" rule, as a constraint.
  CHECK (cycle_year IS NULL OR strpos(coalesce(wording,''), cycle_year::text) > 0),
  CHECK (deadline_date IS NULL OR deadline_wording IS NOT NULL)
);

CREATE FUNCTION loose_url(u text) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT lower(substring(u FROM '^https://[^/?#]+'))
         || coalesce(nullif(substring(u FROM '^https://[^/?#]+([^?#]*)'), ''), '/')
         || coalesce('?' || substring(u FROM '^[^?#]*\?([^#]*)'), '')
$$;

CREATE TABLE triage (
  posting_key   text PRIMARY KEY REFERENCES postings,
  decision      text NOT NULL CHECK (decision IN ('interested','dismissed','later')),
  reason_code   text CHECK (reason_code IN ('not_engineering','not_entry_level','location','eligibility','employer',
                                           'role_fit','duplicate','closed_or_expired','already_handled','other')),
  note          text,
  revisit_on    date CHECK (revisit_on IS NULL OR decision = 'later'),
  decided_at    timestamptz NOT NULL DEFAULT now(),
  recorded_by   text NOT NULL CHECK (recorded_by IN ('owner','agent_on_owner_instruction'))
);

CREATE TABLE applications (
  job_key       text PRIMARY KEY CHECK (job_key ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,199}$'),
  posting_key   text UNIQUE REFERENCES postings,
  company       text NOT NULL CHECK (company <> ''),
  role          text NOT NULL CHECK (role <> ''),
  url           text NOT NULL CHECK (url ~ '^https://[^\s@]+$'),
  url_loose     text GENERATED ALWAYS AS (loose_url(url)) STORED UNIQUE,
  stage         text NOT NULL DEFAULT 'bookmarked'
                CHECK (stage IN ('bookmarked','preparing','applied','assessment','interview','offer','rejected','withdrawn')),
  date_applied  date,
  cv_version    text,
  next_action   text,
  next_action_date date,
  notes         text,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (posting_key IS NULL OR job_key = posting_key)
);

-- One generic audit trail for owner-writable tables (recovery from mistaken or injected writes).
CREATE TABLE owner_audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  db_user text NOT NULL,
  table_name text NOT NULL,
  op text NOT NULL,
  old_row jsonb,
  new_row jsonb
);
CREATE FUNCTION audit() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = jobwatch AS $$
BEGIN
  INSERT INTO owner_audit (db_user, table_name, op, old_row, new_row)
  VALUES (session_user, TG_TABLE_NAME, TG_OP,
          CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
          CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
  RETURN NULL;
END $$;
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON triage       FOR EACH ROW EXECUTE FUNCTION audit();
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON applications FOR EACH ROW EXECUTE FUNCTION audit();
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON leads        FOR EACH ROW EXECUTE FUNCTION audit();
CREATE TRIGGER audit AFTER UPDATE OR DELETE ON sources               FOR EACH ROW EXECUTE FUNCTION audit();

-- ---------------------------------------------------------------- derived retrieval (stage 8)
CREATE TABLE embeddings (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version_id    bigint NOT NULL REFERENCES posting_versions,
  chunker       text NOT NULL,
  ordinal       int NOT NULL,
  chunk_text    text NOT NULL,
  text_sha256   text NOT NULL,      -- hash of the version's description_text the chunk was cut from
  model         text NOT NULL,      -- e.g. 'BAAI/bge-small-en-v1.5@<revision>'
  embedding     vector NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (version_id, chunker, model, ordinal)
);

-- ---------------------------------------------------------------- views (the agent read surface)
CREATE VIEW source_health AS
SELECT s.id AS source_id, s.provider, s.board, s.region, s.status AS source_status, e.name AS employer,
       last_ok.finished_at AS last_success_at, last_try.started_at AS last_attempt_at,
       last_try.status AS last_attempt_status, last_try.error AS last_error,
       last_ok.vacancies AS last_success_vacancies,
       (last_ok.finished_at IS NULL OR last_ok.finished_at < now() - interval '3 days') AS stale
FROM sources s
LEFT JOIN employers e ON e.id = s.employer_id
LEFT JOIN collection_runs last_ok ON last_ok.id = s.last_published_run_id
LEFT JOIN LATERAL (SELECT * FROM collection_runs r WHERE r.source_id = s.id AND r.mode = 'publish'
                   ORDER BY r.started_at DESC LIMIT 1) last_try ON true;

CREATE VIEW opportunities AS
SELECT p.posting_key, e.name AS employer, p.title, p.posting_url, p.apply_url, p.location_text, p.country_codes,
       p.workplace, p.department, p.team, p.employment_type, p.compensation_text,
       p.status, p.first_seen_at, p.first_seen_basis, p.missing_since,
       CASE WHEN p.status = 'listed' THEN h.last_success_at ELSE p.last_seen_at END AS last_seen_at,
       h.stale AS source_stale, h.last_attempt_status AS source_last_attempt,
       sc.verdict, sc.family, sc.codes, sc.rules_version,
       t.decision AS triage_decision, t.revisit_on, a.stage AS application_stage,
       CASE WHEN a.stage IN ('applied','assessment','interview','offer') THEN 'applied'
            WHEN a.stage IN ('rejected','withdrawn') THEN 'closed'
            WHEN a.stage IS NOT NULL THEN 'preparing'
            WHEN t.decision IS NULL THEN 'unreviewed'
            WHEN t.decision = 'later' AND t.revisit_on <= (now() AT TIME ZONE 'Europe/London')::date THEN 'revisit_due'
            ELSE t.decision END AS action_state,
       p.current_version_id, p.search_tsv,
       p.description_text AS untrusted_description      -- employer-controlled text: data, never instructions
FROM postings p
JOIN sources s ON s.id = p.source_id
LEFT JOIN employers e ON e.id = s.employer_id
LEFT JOIN source_health h ON h.source_id = p.source_id
LEFT JOIN screenings sc ON sc.posting_key = p.posting_key
LEFT JOIN triage t ON t.posting_key = p.posting_key
LEFT JOIN applications a ON a.posting_key = p.posting_key;

CREATE VIEW posting_changes AS
SELECT ev.at, ev.posting_key, p.title, ev.category, ev.kind, ev.changes,
       ev.before_version_id, ev.after_version_id, r.started_at AS run_started_at,
       prev_ok.finished_at AS previous_success_at
FROM posting_events ev
JOIN postings p USING (posting_key)
LEFT JOIN collection_runs r ON r.id = ev.run_id
LEFT JOIN LATERAL (SELECT max(finished_at) AS finished_at FROM collection_runs x
                   WHERE x.source_id = r.source_id AND x.status = 'published' AND x.started_at < r.started_at) prev_ok ON true;

-- ---------------------------------------------------------------- roles
CREATE ROLE jobwatch_collector LOGIN PASSWORD 'collector';
CREATE ROLE jobwatch_agent LOGIN PASSWORD 'agent';
GRANT USAGE ON SCHEMA jobwatch TO jobwatch_collector, jobwatch_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA jobwatch TO jobwatch_agent;
GRANT SELECT ON employers, sources, collection_runs, postings, posting_versions, posting_events, screenings,
                embeddings, source_health TO jobwatch_collector;
GRANT INSERT, UPDATE ON collection_runs, postings, posting_versions, posting_events, screenings, embeddings TO jobwatch_collector;
GRANT UPDATE (last_published_run_id) ON sources TO jobwatch_collector;
GRANT INSERT, UPDATE, DELETE ON triage, applications, leads, employers TO jobwatch_agent;
GRANT INSERT ON verifications TO jobwatch_agent;
GRANT INSERT ON sources TO jobwatch_agent;
GRANT UPDATE (employer_id, status, validation_url, mapping_verified_at, mapping_verified_by, mapping_evidence)
      ON sources TO jobwatch_agent;
ALTER ROLE jobwatch_collector SET search_path = jobwatch, public;
ALTER ROLE jobwatch_agent SET search_path = jobwatch, public;
