-- Discovery evidence fidelity upgrade (Stage A, migration 0011).
--
-- Distinguishes three core concepts cleanly without premature universal refactoring:
--   1. Vacancy identity: `discovery_candidates` (`candidate_key`, native/URL uniqueness,
--      overall `first_discovered_at`, `last_discovered_at`, `observation_count`, `current_snapshot_id`).
--   2. Distinct immutable evidence versions: `discovery_candidate_snapshots`
--      (`snapshot_id`, deduplicated per `(candidate_key, evidence_sha256)`), retaining
--      complete source wording in TOAST-compressed `description_text` (safety cap 256 KiB)
--      independently of screening verdict (`priority`/`review`/`excluded`).
--   3. Repeated observation occurrences: unchanged re-observations advance
--      `last_observed_at` and increment `observation_count` on the existing snapshot
--      without duplicating content; content changes append a new snapshot row.
--
-- Enforces strict PostgreSQL CHECK semantics (explicit IS NOT NULL guards against
-- three-valued SQL UNKNOWN acceptance), excerpt SHA-256 fidelity, retention-policy
-- invariants, honest legacy backfill (never claiming historically truncated excerpts
-- were complete), and single-version-consistent `research_discovery_evidence`.

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
    CHECK (complete_description_sha256 IS NULL OR complete_description_sha256 ~ '^[0-9a-f]{64}$'),
  ADD COLUMN IF NOT EXISTS current_snapshot_id uuid;

-- Normalize and backfill pre-0011 rows honestly BEFORE adding strict fidelity CHECKs.
-- Legacy rows only retained bounded <=2,500-char excerpts, so they are NEVER marked 'complete'.
UPDATE discovery_candidates
SET
  description_text = NULL,
  complete_description_sha256 = NULL,
  description_excerpt = CASE
    WHEN btrim(description_excerpt) = '' THEN ''
    ELSE description_excerpt
  END,
  excerpt_sha256 = CASE
    WHEN btrim(description_excerpt) <> '' THEN encode(sha256(convert_to(description_excerpt, 'UTF8')), 'hex')
    ELSE NULL
  END,
  evidence_completeness = CASE
    WHEN btrim(description_excerpt) = '' THEN 'unavailable'
    ELSE 'truncated'
  END
WHERE current_snapshot_id IS NULL;

-- Strict semantic hash fidelity (explicit IS NOT NULL guards prevent SQL 3VL UNKNOWN bypass).
ALTER TABLE discovery_candidates
  DROP CONSTRAINT IF EXISTS discovery_candidates_fidelity_check,
  ADD CONSTRAINT discovery_candidates_fidelity_check CHECK (
    (
      evidence_completeness = 'complete'
      AND description_text IS NOT NULL
      AND btrim(description_text) <> ''
      AND complete_description_sha256 IS NOT NULL
      AND complete_description_sha256 = encode(sha256(convert_to(description_text, 'UTF8')), 'hex')
    )
    OR (
      evidence_completeness IN ('partial', 'truncated')
      AND complete_description_sha256 IS NULL
    )
    OR (
      evidence_completeness = 'unavailable'
      AND description_text IS NULL
      AND complete_description_sha256 IS NULL
      AND btrim(description_excerpt) = ''
    )
  );

-- Exact excerpt hash fidelity (non-null and exact SHA-256 match iff description_excerpt is non-empty).
ALTER TABLE discovery_candidates
  DROP CONSTRAINT IF EXISTS discovery_candidates_excerpt_hash_check,
  ADD CONSTRAINT discovery_candidates_excerpt_hash_check CHECK (
    (
      btrim(description_excerpt) = ''
      AND excerpt_sha256 IS NULL
    )
    OR (
      btrim(description_excerpt) <> ''
      AND excerpt_sha256 IS NOT NULL
      AND excerpt_sha256 = encode(sha256(convert_to(description_excerpt, 'UTF8')), 'hex')
    )
  );

-- Retention-policy invariants: restricted policies can never claim or retain forbidden text.
ALTER TABLE discovery_candidates
  DROP CONSTRAINT IF EXISTS discovery_candidates_retention_policy_check,
  ADD CONSTRAINT discovery_candidates_retention_policy_check CHECK (
    (evidence_retention_policy = 'complete_public_ats')
    OR (
      evidence_retention_policy = 'excerpt_only_terms_restricted'
      AND evidence_completeness IN ('truncated', 'partial', 'unavailable')
      AND description_text IS NULL
      AND complete_description_sha256 IS NULL
    )
    OR (
      evidence_retention_policy = 'metadata_only'
      AND evidence_completeness = 'unavailable'
      AND description_text IS NULL
      AND complete_description_sha256 IS NULL
      AND btrim(description_excerpt) = ''
      AND excerpt_sha256 IS NULL
    )
  );

-- Distinct evidence versions for discovered candidates.
-- Content columns are immutable to runtime roles (no DELETE; column-level UPDATE ONLY on
-- `last_observed_at` and `observation_count` so repeated unchanged observations stay
-- inspectable without duplicating TOAST content).
CREATE TABLE IF NOT EXISTS discovery_candidate_snapshots (
  snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_key text NOT NULL REFERENCES discovery_candidates(candidate_key),
  first_observed_at timestamptz NOT NULL,
  last_observed_at timestamptz NOT NULL,
  observation_count integer NOT NULL DEFAULT 1 CHECK (observation_count >= 1),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  discovery_mechanism text NOT NULL CHECK (btrim(discovery_mechanism) <> ''),
  source_url text NOT NULL,
  evidence_completeness text NOT NULL
    CHECK (evidence_completeness IN ('complete', 'partial', 'truncated', 'unavailable')),
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
  UNIQUE (candidate_key, snapshot_id),
  CHECK (last_observed_at >= first_observed_at),
  CONSTRAINT discovery_snapshots_fidelity_check CHECK (
    (
      evidence_completeness = 'complete'
      AND description_text IS NOT NULL
      AND btrim(description_text) <> ''
      AND complete_description_sha256 IS NOT NULL
      AND complete_description_sha256 = encode(sha256(convert_to(description_text, 'UTF8')), 'hex')
    )
    OR (
      evidence_completeness IN ('partial', 'truncated')
      AND complete_description_sha256 IS NULL
    )
    OR (
      evidence_completeness = 'unavailable'
      AND description_text IS NULL
      AND complete_description_sha256 IS NULL
      AND btrim(description_excerpt) = ''
    )
  ),
  CONSTRAINT discovery_snapshots_excerpt_hash_check CHECK (
    (
      btrim(description_excerpt) = ''
      AND excerpt_sha256 IS NULL
    )
    OR (
      btrim(description_excerpt) <> ''
      AND excerpt_sha256 IS NOT NULL
      AND excerpt_sha256 = encode(sha256(convert_to(description_excerpt, 'UTF8')), 'hex')
    )
  ),
  CONSTRAINT discovery_snapshots_retention_policy_check CHECK (
    (evidence_retention_policy = 'complete_public_ats')
    OR (
      evidence_retention_policy = 'excerpt_only_terms_restricted'
      AND evidence_completeness IN ('truncated', 'partial', 'unavailable')
      AND description_text IS NULL
      AND complete_description_sha256 IS NULL
    )
    OR (
      evidence_retention_policy = 'metadata_only'
      AND evidence_completeness = 'unavailable'
      AND description_text IS NULL
      AND complete_description_sha256 IS NULL
      AND btrim(description_excerpt) = ''
      AND excerpt_sha256 IS NULL
    )
  )
);

CREATE INDEX IF NOT EXISTS discovery_snapshots_candidate_time
  ON discovery_candidate_snapshots (candidate_key, last_observed_at DESC, first_observed_at DESC);

-- Composite foreign key ensures `current_snapshot_id` always belongs to the same `candidate_key`.
ALTER TABLE discovery_candidates
  DROP CONSTRAINT IF EXISTS discovery_candidates_current_snapshot_fkey,
  ADD CONSTRAINT discovery_candidates_current_snapshot_fkey
    FOREIGN KEY (candidate_key, current_snapshot_id)
    REFERENCES discovery_candidate_snapshots (candidate_key, snapshot_id);

-- Backfill one honest baseline snapshot per pre-0011 candidate and link `current_snapshot_id`.
INSERT INTO discovery_candidate_snapshots (
  candidate_key, first_observed_at, last_observed_at, observation_count,
  discovery_mechanism, source_url, evidence_completeness, evidence_retention_policy,
  title, location_text, description_text, description_excerpt,
  complete_description_sha256, excerpt_sha256, evidence_sha256, provenance
)
SELECT
  dc.candidate_key,
  dc.first_discovered_at,
  dc.last_discovered_at,
  dc.observation_count,
  dc.discovery_mechanisms[1],
  dc.posting_url,
  dc.evidence_completeness,
  dc.evidence_retention_policy,
  dc.title,
  dc.location_text,
  dc.description_text,
  dc.description_excerpt,
  dc.complete_description_sha256,
  dc.excerpt_sha256,
  dc.evidence_sha256,
  dc.provenance || jsonb_build_object('legacy_pre_0011_snapshot', true)
FROM discovery_candidates dc
WHERE dc.current_snapshot_id IS NULL
ON CONFLICT (candidate_key, evidence_sha256) DO NOTHING;

UPDATE discovery_candidates dc
SET current_snapshot_id = s.snapshot_id
FROM discovery_candidate_snapshots s
WHERE dc.current_snapshot_id IS NULL
  AND s.candidate_key = dc.candidate_key
  AND s.evidence_sha256 = dc.evidence_sha256;

-- Single-version-consistent research evidence view:
-- Every `effective_*` column (source, version_id, completeness, retention_policy,
-- description, hashes, observation timestamps, provenance) refers strictly to ONE
-- consistent evidence version: the linked native `posting_versions` row when linked,
-- otherwise the current `discovery_candidate_snapshots` row (or inline candidate row).
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
  dc.observation_count AS candidate_observation_count,
  dc.linked_posting_key,
  dc.current_snapshot_id,
  CASE
    WHEN pv.id IS NOT NULL THEN 'native_posting_version'
    WHEN cs.snapshot_id IS NOT NULL THEN 'discovery_candidate_snapshot'
    ELSE 'discovery_candidate_inline'
  END AS effective_evidence_source,
  CASE
    WHEN pv.id IS NOT NULL THEN pv.id::text
    WHEN cs.snapshot_id IS NOT NULL THEN cs.snapshot_id::text
    ELSE dc.candidate_key
  END AS effective_version_id,
  CASE
    WHEN pv.id IS NOT NULL THEN
      CASE
        WHEN pv.description_text IS NOT NULL AND btrim(pv.description_text) <> '' THEN 'complete'
        ELSE 'unavailable'
      END
    WHEN cs.snapshot_id IS NOT NULL THEN cs.evidence_completeness
    ELSE dc.evidence_completeness
  END AS effective_completeness,
  CASE
    WHEN pv.id IS NOT NULL THEN 'complete_public_ats'
    WHEN cs.snapshot_id IS NOT NULL THEN cs.evidence_retention_policy
    ELSE dc.evidence_retention_policy
  END AS effective_retention_policy,
  CASE
    WHEN pv.id IS NOT NULL THEN pv.description_text
    WHEN cs.snapshot_id IS NOT NULL THEN coalesce(cs.description_text, nullif(cs.description_excerpt, ''))
    ELSE coalesce(dc.description_text, nullif(dc.description_excerpt, ''))
  END AS effective_description,
  CASE
    WHEN pv.id IS NOT NULL THEN
      CASE
        WHEN pv.description_text IS NOT NULL AND btrim(pv.description_text) <> ''
          THEN encode(sha256(convert_to(pv.description_text, 'UTF8')), 'hex')
        ELSE NULL
      END
    WHEN cs.snapshot_id IS NOT NULL THEN cs.complete_description_sha256
    ELSE dc.complete_description_sha256
  END AS effective_complete_description_sha256,
  CASE
    WHEN pv.id IS NOT NULL THEN NULL
    WHEN cs.snapshot_id IS NOT NULL THEN cs.excerpt_sha256
    ELSE dc.excerpt_sha256
  END AS effective_excerpt_sha256,
  CASE
    WHEN pv.id IS NOT NULL THEN pv.evidence_sha256
    WHEN cs.snapshot_id IS NOT NULL THEN cs.evidence_sha256
    ELSE dc.evidence_sha256
  END AS effective_evidence_sha256,
  CASE
    WHEN pv.id IS NOT NULL THEN pv.observed_at
    WHEN cs.snapshot_id IS NOT NULL THEN cs.first_observed_at
    ELSE dc.first_discovered_at
  END AS effective_first_observed_at,
  CASE
    WHEN pv.id IS NOT NULL THEN coalesce(p.last_seen_at, pv.observed_at)
    WHEN cs.snapshot_id IS NOT NULL THEN cs.last_observed_at
    ELSE dc.last_discovered_at
  END AS effective_last_observed_at,
  CASE
    WHEN pv.id IS NOT NULL THEN pv.provenance
    WHEN cs.snapshot_id IS NOT NULL THEN cs.provenance
    ELSE dc.provenance
  END AS effective_provenance,
  dc.evidence_completeness AS candidate_completeness,
  dc.evidence_retention_policy AS candidate_retention_policy,
  dc.complete_description_sha256 AS candidate_complete_description_sha256,
  dc.excerpt_sha256 AS candidate_excerpt_sha256,
  dc.description_sha256 AS legacy_description_sha256,
  dc.evidence_sha256 AS candidate_evidence_sha256,
  coalesce(snap.snapshot_count, 0) AS snapshot_count,
  coalesce(snap.total_snapshot_observations, 0) AS snapshot_observation_count,
  true AS wording_is_untrusted
FROM discovery_candidates dc
LEFT JOIN discovery_candidate_snapshots cs
  ON cs.candidate_key = dc.candidate_key
  AND cs.snapshot_id = dc.current_snapshot_id
LEFT JOIN postings p ON p.posting_key = dc.linked_posting_key
LEFT JOIN posting_versions pv ON pv.id = p.current_version_id
LEFT JOIN LATERAL (
  SELECT count(*) AS snapshot_count,
         coalesce(sum(s.observation_count), 0) AS total_snapshot_observations
  FROM discovery_candidate_snapshots s
  WHERE s.candidate_key = dc.candidate_key
) snap ON true;

-- Content-immutable snapshot grants: runtime roles may append new versions and update
-- observation occurrence counters on unchanged versions, never alter wording or delete history.
GRANT SELECT, INSERT ON discovery_candidate_snapshots TO jobwatch_collector, jobwatch_owner;
GRANT UPDATE (last_observed_at, observation_count) ON discovery_candidate_snapshots TO jobwatch_collector, jobwatch_owner;
GRANT SELECT ON research_discovery_evidence TO jobwatch_collector, jobwatch_owner;

COMMENT ON TABLE discovery_candidate_snapshots IS 'Content-immutable historical evidence versions for discovered vacancies. Repeated observations of an unchanged version increment observation_count and advance last_observed_at without duplicating TOAST content.';
COMMENT ON VIEW research_discovery_evidence IS 'Single-version-consistent research projection of discovery evidence, preferring linked native posting_versions when linked and current discovery snapshots otherwise.';
