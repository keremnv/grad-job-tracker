-- 0014: Discovery ETag caching, full-text + symbol-preserving discovery search, and remote Evidence MCP budget seed.
--
-- Part 1 — Conditional GET / ETag caching on `jobwatch.discovered_boards`:
--   Enables `If-None-Match` (`HTTP 304 Not Modified`) during steady-state discovery revisits,
--   eliminating redundant JSON transfers for unchanged boards.
--
-- Part 2 — Complete-description English FTS + symbol-preserving FTS on `jobwatch.discovery_candidates`:
--   Upgrades existing `search_tsv` (PG 17 `SET EXPRESSION`) to index complete `description_text`
--   (falling back to `description_excerpt`) while preserving `research_discovery_candidates` and
--   `discovery_candidates_search`, and adds generated stored `search_sym_tsv` + GIN index
--   matching `posting_versions` for fast `search(scope='discovery'|'all')`.
--
-- Part 3 — Default remote Evidence MCP submission budget seed:
--   Seeds `jobwatch_agent_evidence` (`20/hour`, `60/day`) only if not already configured.

SET LOCAL search_path = pg_catalog, jobwatch, pg_temp;

ALTER TABLE jobwatch.discovered_boards
  ADD COLUMN IF NOT EXISTS last_etag text,
  ADD COLUMN IF NOT EXISTS last_304_at timestamptz;

ALTER TABLE jobwatch.discovery_candidates
  ALTER COLUMN search_tsv SET EXPRESSION AS (
    pg_catalog.setweight(pg_catalog.to_tsvector('english'::regconfig, title), 'A') ||
    pg_catalog.setweight(pg_catalog.to_tsvector('english'::regconfig, company || ' ' || department || ' ' || team || ' ' || location_text), 'B') ||
    pg_catalog.setweight(pg_catalog.to_tsvector('english'::regconfig, coalesce(description_text, description_excerpt, '')), 'D')
  );

ALTER TABLE jobwatch.discovery_candidates
  ADD COLUMN IF NOT EXISTS search_sym_tsv tsvector GENERATED ALWAYS AS (
    pg_catalog.setweight(
      pg_catalog.to_tsvector(
        'simple'::regconfig,
        jobwatch.symbol_search_text(title)
      ),
      'A'
    ) ||
    pg_catalog.setweight(
      pg_catalog.to_tsvector(
        'simple'::regconfig,
        jobwatch.symbol_search_text(company || ' ' || department || ' ' || team || ' ' || location_text)
      ),
      'B'
    ) ||
    pg_catalog.setweight(
      pg_catalog.to_tsvector(
        'simple'::regconfig,
        jobwatch.symbol_search_text(coalesce(description_text, description_excerpt, ''))
      ),
      'D'
    )
  ) STORED;

CREATE INDEX IF NOT EXISTS discovery_candidates_search_sym_tsv_idx
  ON jobwatch.discovery_candidates USING gin (search_sym_tsv);

INSERT INTO jobwatch.agent_evidence_budgets (db_login, hourly_limit, daily_limit)
VALUES ('jobwatch_agent_evidence', 20, 60)
ON CONFLICT (db_login) DO NOTHING;
