-- Native Workable provider support, dotted board slugs (e.g. iwoca.co.uk),
-- and lightweight discovery_candidates lifecycle separate from authoritative
-- monitored-board evidence (posting_versions) and owner-curated leads.
SET LOCAL search_path = jobwatch, pg_catalog;

ALTER TABLE sources DROP CONSTRAINT sources_provider_check;
ALTER TABLE sources ADD CONSTRAINT sources_provider_check
  CHECK (provider IN ('greenhouse','lever','ashby','workable'));

ALTER TABLE sources DROP CONSTRAINT sources_board_check;
ALTER TABLE sources ADD CONSTRAINT sources_board_check
  CHECK (board ~ '^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$');

ALTER TABLE sources ADD CONSTRAINT sources_workable_region_check
  CHECK (provider <> 'workable' OR region = 'global');

CREATE TABLE discovery_candidates (
  candidate_key text PRIMARY KEY CHECK (candidate_key ~ '^[a-z0-9][A-Za-z0-9:._-]{2,255}$'),
  provider text NOT NULL CHECK (provider IN ('greenhouse','lever','ashby','workable','other')),
  board text CHECK (board IS NULL OR board ~ '^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$'),
  native_id text CHECK (native_id IS NULL OR native_id ~ '^[A-Za-z0-9_.-]+$'),
  company text NOT NULL CHECK (btrim(company) <> ''),
  title text NOT NULL CHECK (btrim(title) <> ''),
  posting_url text NOT NULL,
  url_exact text GENERATED ALWAYS AS (url_identity(posting_url, true)) STORED,
  location_text text NOT NULL DEFAULT '',
  country text NOT NULL DEFAULT '',
  countries text[] NOT NULL DEFAULT '{}',
  department text NOT NULL DEFAULT '',
  team text NOT NULL DEFAULT '',
  employment_type text NOT NULL DEFAULT '',
  workplace_type text NOT NULL DEFAULT '',
  published_at text NOT NULL DEFAULT '',
  source_dates jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(source_dates) = 'object'),
  deadlines jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(deadlines) = 'array'),
  -- Bounded plain-text excerpt avoids duplicating multi-KB raw HTML payloads outside posting_versions.
  description_excerpt text NOT NULL DEFAULT '' CHECK (char_length(description_excerpt) <= 2500),
  description_sha256 text CHECK (description_sha256 IS NULL OR description_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  state text NOT NULL DEFAULT 'new'
    CHECK (state IN ('new','observed','verified_identity','linked_posting','promoted_lead','dismissed')),
  first_discovered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_discovered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  observation_count integer NOT NULL DEFAULT 1 CHECK (observation_count >= 1),
  discovery_mechanisms text[] NOT NULL CHECK (cardinality(discovery_mechanisms) >= 1),
  provenance jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(provenance) = 'object'),
  verdict text NOT NULL CHECK (verdict IN ('priority','review','excluded')),
  family text NOT NULL,
  codes text[] NOT NULL,
  rules_version text NOT NULL,
  screening_input_sha256 text CHECK (screening_input_sha256 ~ '^[0-9a-f]{64}$'),
  screening_basis text NOT NULL CHECK (screening_basis IN ('full','title_metadata_only','excerpt')),
  screened_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  source_id bigint REFERENCES sources(id),
  linked_posting_key text REFERENCES postings(posting_key),
  promoted_lead_key text REFERENCES leads(lead_key),
  search_tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english'::regconfig, title), 'A') ||
    setweight(to_tsvector('english'::regconfig, company || ' ' || department || ' ' || team || ' ' || location_text), 'B') ||
    setweight(to_tsvector('english'::regconfig, description_excerpt), 'D')
  ) STORED,
  CHECK ((board IS NULL) = (native_id IS NULL)),
  CHECK (state <> 'verified_identity' OR (board IS NOT NULL AND native_id IS NOT NULL)),
  CHECK (state <> 'linked_posting' OR linked_posting_key IS NOT NULL),
  CHECK (state <> 'promoted_lead' OR promoted_lead_key IS NOT NULL),
  CHECK (last_discovered_at >= first_discovered_at)
);

CREATE UNIQUE INDEX discovery_candidates_native_identity
  ON discovery_candidates (provider, lower(board), native_id)
  WHERE board IS NOT NULL AND native_id IS NOT NULL;
CREATE UNIQUE INDEX discovery_candidates_url_exact
  ON discovery_candidates (url_exact);
CREATE INDEX discovery_candidates_verdict_recent
  ON discovery_candidates (verdict, last_discovered_at DESC);
CREATE INDEX discovery_candidates_search
  ON discovery_candidates USING gin (search_tsv);

CREATE VIEW research_discovery_candidates AS
SELECT c.candidate_key, c.provider, c.board, c.native_id, c.company, c.title,
  c.posting_url, c.url_exact, c.location_text, c.country, c.countries,
  c.department, c.team, c.employment_type, c.workplace_type, c.published_at,
  c.source_dates, c.deadlines, c.description_excerpt, c.description_sha256,
  c.evidence_sha256, c.state, c.first_discovered_at, c.last_discovered_at,
  c.observation_count, c.discovery_mechanisms, c.provenance,
  c.verdict, c.family, c.codes, c.rules_version, c.screening_basis, c.screened_at,
  c.source_id, s.status AS source_status,
  c.linked_posting_key, p.status AS linked_posting_status,
  c.promoted_lead_key,
  (c.source_id IS NULL) AS unknown_employer_board,
  c.search_tsv
FROM discovery_candidates c
LEFT JOIN sources s ON s.id = c.source_id
LEFT JOIN postings p ON p.posting_key = c.linked_posting_key;

GRANT SELECT, INSERT, UPDATE ON discovery_candidates TO jobwatch_collector;
GRANT SELECT ON research_discovery_candidates TO jobwatch_collector;
GRANT SELECT, INSERT, UPDATE, DELETE ON discovery_candidates TO jobwatch_owner;
GRANT SELECT ON research_discovery_candidates TO jobwatch_owner;

COMMENT ON TABLE discovery_candidates IS 'Public discovered vacancies outside or prior to authoritative monitored-board collection. Disappearing from discovery never proves closure or board absence.';
COMMENT ON VIEW research_discovery_candidates IS 'Public research projection of discovered candidates across known and previously unknown employers; no private owner notes.';
