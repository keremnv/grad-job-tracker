-- ISOLATED PROPOSAL (NOT DEPLOYED TO PRODUCTION).
-- Minimal discovery evidence fidelity upgrade addressing the measured 93.7%
-- truncation rate of `discovery_candidates.description_excerpt` (2,500 chars).
--
-- Design choices:
-- 1. Does NOT reuse `posting_versions` directly (which binds strictly to
--    monitored `sources` and `collection_runs`), avoiding premature authoritative
--    native identities for unactivated discovered boards.
-- 2. Retains complete source descriptions in TOAST-compressed `description_text`
--    (PostgreSQL automatically compresses >2 KiB values; live 2,463-posting
--    measurement: avg 6,883 B raw -> 2,565 B gzip, ~12.2 MiB per 5,000 rows)
--    with a safety cap of 262,144 chars (256 KiB), NOT an arbitrary 2,500-char
--    truncation threshold.
-- 3. Explicitly separates `evidence_completeness` (`complete`, `partial`,
--    `truncated`, `unavailable`), `complete_description_sha256` (ONLY non-null
--    when complete), and `excerpt_sha256` (hash of stored excerpt).
-- 4. Preserves historical discovered-vacancy snapshots independently of
--    `priority`/`review`/`excluded` verdicts in append-only
--    `discovery_candidate_snapshots` so observations survive source edits or
--    disappearance, deduplicated by `(candidate_key, evidence_sha256)`.
-- 5. Avoids unnecessary duplication once linked to a monitored posting via
--    `research_discovery_evidence`, which prefers `posting_versions` when linked.

SET LOCAL search_path = jobwatch, pg_catalog;

ALTER TABLE discovery_candidates
  ADD COLUMN IF NOT EXISTS description_text text
    CHECK (description_text IS NULL OR char_length(description_text) <= 262144),
  ADD COLUMN IF NOT EXISTS evidence_completeness text NOT NULL DEFAULT 'truncated'
    CHECK (evidence_completeness IN ('complete', 'partial', 'truncated', 'unavailable')),
  ADD COLUMN IF NOT EXISTS evidence_retention_policy text NOT NULL DEFAULT 'complete_public_ats'
    CHECK (evidence_retention_policy IN ('complete_public_ats', 'excerpt_only_terms_restricted', 'metadata_only')),
  ADD COLUMN IF NOT EXISTS excerpt_sha256 text
    CHECK (excerpt_sha256 IS NULL OR excerpt_sha256 ~ '^[0-9a-f]{64}$'),
  ADD COLUMN IF NOT EXISTS complete_description_sha256 text
    CHECK (complete_description_sha256 IS NULL OR complete_description_sha256 ~ '^[0-9a-f]{64}$');

-- Enforce semantic hash fidelity:
-- - `complete_description_sha256` MUST be present and match `description_text` iff completeness = 'complete'.
-- - `description_text` MUST be NULL when completeness = 'unavailable'.
ALTER TABLE discovery_candidates
  DROP CONSTRAINT IF EXISTS discovery_candidates_fidelity_check,
  ADD CONSTRAINT discovery_candidates_fidelity_check CHECK (
    (evidence_completeness = 'complete'
      AND description_text IS NOT NULL
      AND complete_description_sha256 = encode(sha256(convert_to(description_text, 'UTF8')), 'hex'))
    OR (evidence_completeness IN ('partial', 'truncated')
      AND complete_description_sha256 IS NULL)
    OR (evidence_completeness = 'unavailable'
      AND description_text IS NULL
      AND complete_description_sha256 IS NULL)
  );

-- Backfill existing pre-0011 rows cleanly without fabricating complete hashes.
UPDATE discovery_candidates
SET
  excerpt_sha256 = CASE
    WHEN btrim(description_excerpt) <> '' THEN encode(sha256(convert_to(description_excerpt, 'UTF8')), 'hex')
    ELSE NULL
  END,
  evidence_completeness = CASE
    WHEN btrim(description_excerpt) = '' THEN 'unavailable'
    ELSE 'truncated'
  END
WHERE excerpt_sha256 IS NULL;

-- Append-only historical evidence snapshots for discovered candidates.
-- Preserves complete wording/provenance across edits and after source disappearance,
-- independently of screening verdict (`priority`/`review`/`excluded`).
CREATE TABLE IF NOT EXISTS discovery_candidate_snapshots (
  snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_key text NOT NULL REFERENCES discovery_candidates(candidate_key),
  observed_at timestamptz NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  discovery_mechanism text NOT NULL CHECK (btrim(discovery_mechanism) <> ''),
  source_url text NOT NULL,
  evidence_completeness text NOT NULL CHECK (evidence_completeness IN ('complete', 'partial', 'truncated', 'unavailable')),
  evidence_retention_policy text NOT NULL DEFAULT 'complete_public_ats'
    CHECK (evidence_retention_policy IN ('complete_public_ats', 'excerpt_only_terms_restricted', 'metadata_only')),
  title text NOT NULL CHECK (btrim(title) <> ''),
  location_text text NOT NULL DEFAULT '',
  description_text text CHECK (description_text IS NULL OR char_length(description_text) <= 262144),
  description_excerpt text NOT NULL DEFAULT '' CHECK (char_length(description_excerpt) <= 2500),
  complete_description_sha256 text CHECK (complete_description_sha256 IS NULL OR complete_description_sha256 ~ '^[0-9a-f]{64}$'),
  excerpt_sha256 text CHECK (excerpt_sha256 IS NULL OR excerpt_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  raw_evidence_gz bytea CHECK (raw_evidence_gz IS NULL OR octet_length(raw_evidence_gz) <= 524288),
  provenance jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(provenance) = 'object'),
  UNIQUE (candidate_key, evidence_sha256),
  CHECK (
    (evidence_completeness = 'complete'
      AND description_text IS NOT NULL
      AND complete_description_sha256 = encode(sha256(convert_to(description_text, 'UTF8')), 'hex'))
    OR (evidence_completeness IN ('partial', 'truncated')
      AND complete_description_sha256 IS NULL)
    OR (evidence_completeness = 'unavailable'
      AND description_text IS NULL
      AND complete_description_sha256 IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS discovery_snapshots_candidate_time
  ON discovery_candidate_snapshots (candidate_key, observed_at DESC);

-- Ordinary SQL inspectability view: resolves native posting_versions evidence once
-- linked (avoiding duplication), otherwise exposes full retained candidate evidence
-- plus explicit completeness and snapshot history count.
CREATE OR REPLACE VIEW research_discovery_evidence AS
SELECT
  dc.candidate_key,
  dc.provider,
  dc.board,
  dc.native_id,
  dc.company,
  dc.title,
  dc.posting_url,
  dc.location_text,
  dc.state,
  dc.verdict,
  dc.family,
  dc.codes,
  dc.first_discovered_at,
  dc.last_discovered_at,
  dc.observation_count,
  dc.linked_posting_key,
  CASE
    WHEN pv.id IS NOT NULL THEN 'linked_native_posting_version'
    ELSE dc.evidence_completeness
  END AS effective_completeness,
  dc.evidence_completeness AS candidate_completeness,
  dc.evidence_retention_policy,
  coalesce(pv.description_text, dc.description_text, nullif(dc.description_excerpt, '')) AS effective_description,
  dc.complete_description_sha256,
  dc.excerpt_sha256,
  dc.description_sha256 AS legacy_description_sha256,
  dc.evidence_sha256,
  (SELECT count(*) FROM discovery_candidate_snapshots s WHERE s.candidate_key = dc.candidate_key) AS snapshot_count,
  true AS wording_is_untrusted
FROM discovery_candidates dc
LEFT JOIN postings p ON p.posting_key = dc.linked_posting_key
LEFT JOIN posting_versions pv ON pv.id = p.current_version_id;

GRANT SELECT, INSERT ON discovery_candidate_snapshots TO jobwatch_collector;
GRANT SELECT ON research_discovery_evidence TO jobwatch_collector, jobwatch_owner;
GRANT SELECT, INSERT ON discovery_candidate_snapshots TO jobwatch_owner;
