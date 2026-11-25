-- 0013: Distinct normalized deadline claims view & minimal targeted board investigation columns.
--
-- Part 1 — Distinct deadline claims queryability (`jobwatch.posting_deadline_claims_distinct`):
--   Preserves `jobwatch.posting_deadline_claims` and every underlying historical `claim_id`
--   unchanged (keeping `verifications.deadline_supersedes` 100% compatible) while deduplicating
--   repeated identical wording across posting versions and normalizing legacy HTML-escaped wording.
--   Never invents dates or modifies calendar selection rules.
--
-- Part 2 — Minimal targeted board investigation on `jobwatch.discovered_boards`:
--   Adds nullable `investigation_requested_at/by/reason` columns so an owner or operator
--   (or an unprobed `agent_evidence_observations.board_key` reference) can prioritize probing
--   a known directory board within existing bounded discovery budgets without activating it.

SET LOCAL search_path = pg_catalog, jobwatch, pg_temp;

CREATE OR REPLACE FUNCTION jobwatch.normalize_claim_wording(p_text text) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, jobwatch, pg_temp AS $$
DECLARE
  v text := p_text;
BEGIN
  -- Decode up to two passes of common HTML entities (handles Greenhouse double-escaped markup).
  FOR i IN 1..2 LOOP
    v := pg_catalog.replace(v, '&amp;', '&');
    v := pg_catalog.replace(v, '&lt;', '<');
    v := pg_catalog.replace(v, '&gt;', '>');
    v := pg_catalog.replace(v, '&quot;', '"');
    v := pg_catalog.replace(v, '&#39;', '''');
    v := pg_catalog.replace(v, '&apos;', '''');
    v := pg_catalog.replace(v, '&nbsp;', ' ');
    v := pg_catalog.replace(v, '&#160;', ' ');
  END LOOP;
  -- Strip inline formatting tags without injecting spaces (preserves `29<sup>th</sup>` -> `29th`).
  v := pg_catalog.regexp_replace(v, '</?(?:strong|b|em|i|u|sup|sub|span|small|mark|abbr|code)\b[^>]*>', '', 'gi');
  -- Replace block/other tags with spaces and collapse whitespace.
  v := pg_catalog.regexp_replace(v, '<[^>]+>', ' ', 'g');
  v := pg_catalog.replace(v, E'\u00a0', ' ');
  v := pg_catalog.btrim(pg_catalog.regexp_replace(v, '\s+', ' ', 'g'));
  RETURN v;
END $$;

REVOKE ALL ON FUNCTION jobwatch.normalize_claim_wording(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION jobwatch.normalize_claim_wording(text) TO jobwatch_collector, jobwatch_owner;

CREATE OR REPLACE VIEW jobwatch.posting_deadline_claims_distinct AS
WITH current_feed AS (
  SELECT p.posting_key,
         jobwatch.normalize_claim_wording(coalesce(d->>'wording', '')) AS norm_wording,
         coalesce(d->>'value', '') AS norm_value,
         (d->>'source_field' = 'application_deadline') AS is_structured
  FROM jobwatch.postings p
  JOIN jobwatch.posting_versions pv ON pv.id = p.current_version_id
  CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(pv.deadlines) d
),
enriched AS (
  SELECT
    c.posting_key,
    c.claim_id,
    c.provenance,
    c.structured,
    c.value,
    c.url,
    c.wording AS raw_wording,
    jobwatch.normalize_claim_wording(coalesce(c.wording, '')) AS normalized_wording,
    c.checked_at,
    c.checked_by,
    c.superseded_by,
    (c.superseded_by <> '[]'::jsonb) AS claim_superseded,
    CASE
      WHEN c.provenance <> 'feed_observation' THEN true
      ELSE EXISTS (
        SELECT 1 FROM current_feed cf
        WHERE cf.posting_key = c.posting_key
          AND cf.norm_wording = jobwatch.normalize_claim_wording(coalesce(c.wording, ''))
          AND cf.norm_value = coalesce(c.value, '')
          AND cf.is_structured = c.structured
      )
    END AS in_current_version
  FROM jobwatch.posting_deadline_claims c
)
SELECT
  posting_key,
  provenance,
  structured,
  value,
  normalized_wording,
  pg_catalog.min(url) AS sample_url,
  pg_catalog.count(*)::bigint AS observation_count,
  pg_catalog.array_agg(claim_id ORDER BY checked_at NULLS LAST, claim_id) AS claim_ids,
  pg_catalog.min(checked_at) AS first_observed_at,
  pg_catalog.max(checked_at) AS last_observed_at,
  pg_catalog.bool_or(in_current_version) AS present_in_current_version,
  pg_catalog.bool_and(claim_superseded) AS all_claims_superseded,
  pg_catalog.bool_or(claim_superseded) AS any_claim_superseded,
  true AS wording_is_untrusted
FROM enriched
GROUP BY posting_key, provenance, structured, value, normalized_wording;

GRANT SELECT ON jobwatch.posting_deadline_claims_distinct TO jobwatch_owner;

-- Minimal targeted-investigation request columns on `discovered_boards` (no separate task queue).
ALTER TABLE jobwatch.discovered_boards
  ADD COLUMN IF NOT EXISTS investigation_requested_at timestamptz,
  ADD COLUMN IF NOT EXISTS investigation_requested_by text,
  ADD COLUMN IF NOT EXISTS investigation_reason text NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS discovered_boards_investigation_pending
  ON jobwatch.discovered_boards (investigation_requested_at ASC)
  WHERE investigation_requested_at IS NOT NULL AND source_id IS NULL;

COMMENT ON VIEW jobwatch.posting_deadline_claims_distinct IS 'Deduplicated, HTML-normalized view over posting_deadline_claims preserving all underlying claim_ids, provenance, supersession status, and current-version presence.';
