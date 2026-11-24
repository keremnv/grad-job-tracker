-- Lightweight board-level discovery registry for recurring employer-directory
-- discovery (Greenhouse, Ashby, Lever, Workable).
-- Stores public board observations, probe freshness, UK relevance counts, and
-- read-only employer identity verification evidence WITHOUT activating `sources`
-- or altering owner-curated tables (`leads`, `applications`, `triage`, `calendar_selections`).
SET LOCAL search_path = jobwatch, pg_catalog;

CREATE TABLE discovered_boards (
  board_key text PRIMARY KEY CHECK (board_key ~ '^(greenhouse|ashby|lever|workable):[a-z0-9_.-]+$'),
  provider text NOT NULL CHECK (provider IN ('greenhouse','ashby','lever','workable')),
  board text NOT NULL CHECK (board ~ '^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$'),
  region text NOT NULL DEFAULT 'global' CHECK (region IN ('global','eu')),
  company_hint text NOT NULL DEFAULT '',
  official_website text NOT NULL DEFAULT '',
  careers_url text NOT NULL DEFAULT '',
  uk_hint boolean NOT NULL DEFAULT false,
  directory_sources text[] NOT NULL CHECK (cardinality(directory_sources) >= 1),
  directory_provenance jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(directory_provenance) = 'object'),
  first_discovered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_directory_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_probed_at timestamptz,
  probe_count integer NOT NULL DEFAULT 0 CHECK (probe_count >= 0),
  probe_status text NOT NULL DEFAULT 'unprobed'
    CHECK (probe_status IN ('unprobed','active_uk_relevant','active_non_uk','empty','dead','error')),
  http_status integer,
  total_vacancies integer NOT NULL DEFAULT 0 CHECK (total_vacancies >= 0),
  uk_vacancies integer NOT NULL DEFAULT 0 CHECK (uk_vacancies >= 0),
  priority_vacancies integer NOT NULL DEFAULT 0 CHECK (priority_vacancies >= 0),
  review_vacancies integer NOT NULL DEFAULT 0 CHECK (review_vacancies >= 0),
  custom_host_urls integer NOT NULL DEFAULT 0 CHECK (custom_host_urls >= 0),
  identity_status text NOT NULL DEFAULT 'not_checked'
    CHECK (identity_status IN ('not_checked','verified_official_link','hosted_link_only','unverified_js_or_blocked','domain_mismatch')),
  identity_checked_at timestamptz,
  identity_evidence text NOT NULL DEFAULT '',
  source_id bigint REFERENCES sources(id),
  CHECK (board_key = provider || ':' || lower(board)),
  CHECK (last_directory_seen_at >= first_discovered_at)
);

CREATE UNIQUE INDEX discovered_boards_provider_board ON discovered_boards (provider, lower(board));
CREATE INDEX discovered_boards_probe_queue ON discovered_boards (probe_status, uk_hint DESC, last_probed_at ASC NULLS FIRST);
CREATE INDEX discovered_boards_uk_yield ON discovered_boards (priority_vacancies DESC, review_vacancies DESC, last_probed_at DESC NULLS LAST);

CREATE VIEW research_discovered_boards AS
SELECT b.board_key, b.provider, b.board, b.region, b.company_hint,
  b.official_website, b.careers_url, b.uk_hint,
  b.directory_sources, b.directory_provenance,
  b.first_discovered_at, b.last_directory_seen_at,
  b.last_probed_at, b.probe_count, b.probe_status, b.http_status,
  b.total_vacancies, b.uk_vacancies, b.priority_vacancies, b.review_vacancies,
  b.custom_host_urls, b.identity_status, b.identity_checked_at, b.identity_evidence,
  b.source_id, s.status AS source_status,
  (b.source_id IS NULL) AS unknown_employer_board,
  coalesce(c.candidate_count, 0) AS active_candidate_vacancies,
  coalesce(c.priority_candidate_count, 0) AS active_priority_candidates
FROM discovered_boards b
LEFT JOIN sources s ON s.id = b.source_id
LEFT JOIN LATERAL (
  SELECT count(*) AS candidate_count,
         count(*) FILTER (WHERE dc.verdict = 'priority') AS priority_candidate_count
  FROM discovery_candidates dc
  WHERE dc.provider = b.provider AND lower(dc.board) = lower(b.board)
    AND dc.state NOT IN ('dismissed', 'linked_posting')
) c ON true;

GRANT SELECT, INSERT, UPDATE ON discovered_boards TO jobwatch_collector;
GRANT SELECT ON research_discovered_boards TO jobwatch_collector;
GRANT SELECT, INSERT, UPDATE, DELETE ON discovered_boards TO jobwatch_owner;
GRANT SELECT ON research_discovered_boards TO jobwatch_owner;

COMMENT ON TABLE discovered_boards IS 'Public employer-directory discovery state for ATS boards outside or prior to monitored activation. Automated discovery never activates sources.';
COMMENT ON VIEW research_discovered_boards IS 'Public research projection of discovered ATS boards, UK yield, identity verification evidence, and active candidate vacancy counts.';
