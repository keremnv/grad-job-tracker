-- ISOLATED PROPOSAL (NOT DEPLOYED TO PRODUCTION).
-- Append-only public evidence observation log for agent-contributed evidence.
-- Strictly separated from owner-action authority (`applications`, `triage`,
-- `calendar_selections`, `leads`) and from authoritative `verifications`.
-- Agent observations start as `corroboration_status = 'recorded'` and NEVER
-- automatically qualify as verified calendar deadlines or owner decisions.

SET LOCAL search_path = jobwatch, pg_catalog;

CREATE TABLE agent_evidence_observations (
  observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key text NOT NULL UNIQUE CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
  kind text NOT NULL CHECK (kind IN (
    'new_vacancy',
    'official_source_link',
    'deadline_wording',
    'posting_evidence',
    'source_identity'
  )),
  contributor text NOT NULL CHECK (char_length(btrim(contributor)) BETWEEN 3 AND 128),
  db_login text NOT NULL DEFAULT session_user CHECK (btrim(db_login) <> ''),
  observed_at timestamptz NOT NULL CHECK (observed_at <= clock_timestamp() + interval '5 minutes'),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  source_url text NOT NULL CHECK (
    source_url ~ '^https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(/.*)?$'
    AND char_length(source_url) <= 2048
    AND position('@' in split_part(substr(source_url, 9), '/', 1)) = 0
  ),
  posting_key text REFERENCES postings(posting_key),
  candidate_key text REFERENCES discovery_candidates(candidate_key),
  board_key text REFERENCES discovered_boards(board_key),
  source_id bigint REFERENCES sources(id),
  lead_key text REFERENCES leads(lead_key),
  claim_summary text NOT NULL CHECK (char_length(btrim(claim_summary)) BETWEEN 3 AND 500),
  observed_wording text NOT NULL DEFAULT '' CHECK (char_length(observed_wording) <= 4000),
  candidate_deadline_date text CHECK (
    candidate_deadline_date IS NULL
    OR candidate_deadline_date ~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
  ),
  payload jsonb NOT NULL DEFAULT '{}' CHECK (
    jsonb_typeof(payload) = 'object'
    AND octet_length(payload::text) <= 8192
  ),
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  corroboration_status text NOT NULL DEFAULT 'recorded' CHECK (
    corroboration_status IN ('recorded', 'corroborated', 'unresolved_conflict', 'superseded')
  ),
  corroborated_by text,
  corroborated_at timestamptz,
  corroboration_evidence jsonb NOT NULL DEFAULT '{}' CHECK (
    jsonb_typeof(corroboration_evidence) = 'object'
    AND octet_length(corroboration_evidence::text) <= 8192
  ),
  CHECK (
    (corroboration_status = 'recorded' AND corroborated_at IS NULL AND corroborated_by IS NULL)
    OR (corroboration_status <> 'recorded' AND corroborated_at IS NOT NULL AND nullif(btrim(corroborated_by), '') IS NOT NULL)
  ),
  CHECK (kind <> 'deadline_wording' OR btrim(observed_wording) <> ''),
  CHECK (kind NOT IN ('posting_evidence', 'deadline_wording') OR num_nonnulls(posting_key, candidate_key, lead_key) >= 1)
);

CREATE INDEX agent_evidence_kind_recorded ON agent_evidence_observations (kind, recorded_at DESC);
CREATE INDEX agent_evidence_posting ON agent_evidence_observations (posting_key, recorded_at DESC) WHERE posting_key IS NOT NULL;
CREATE INDEX agent_evidence_candidate ON agent_evidence_observations (candidate_key, recorded_at DESC) WHERE candidate_key IS NOT NULL;
CREATE INDEX agent_evidence_board ON agent_evidence_observations (board_key, recorded_at DESC) WHERE board_key IS NOT NULL;
CREATE INDEX agent_evidence_contributor_rate ON agent_evidence_observations (contributor, recorded_at DESC);

-- Inspectable research projection surfacing contradictions across agent observations
-- and active posting deadline claims without silently overwriting or picking a winner.
CREATE VIEW research_agent_evidence AS
SELECT
  o.observation_id,
  o.idempotency_key,
  o.kind,
  o.contributor,
  o.db_login,
  o.observed_at,
  o.recorded_at,
  o.source_url,
  o.posting_key,
  o.candidate_key,
  o.board_key,
  o.source_id,
  o.lead_key,
  o.claim_summary,
  o.observed_wording,
  o.candidate_deadline_date,
  o.payload,
  o.payload_sha256,
  o.corroboration_status,
  o.corroborated_by,
  o.corroborated_at,
  o.corroboration_evidence,
  (
    o.corroboration_status = 'unresolved_conflict'
    OR (
      o.kind = 'deadline_wording' AND o.posting_key IS NOT NULL AND o.candidate_deadline_date IS NOT NULL
      AND (
        EXISTS (
          SELECT 1 FROM agent_evidence_observations o2
          WHERE o2.posting_key = o.posting_key
            AND o2.kind = 'deadline_wording'
            AND o2.observation_id <> o.observation_id
            AND o2.corroboration_status <> 'superseded'
            AND o2.candidate_deadline_date IS NOT NULL
            AND o2.candidate_deadline_date <> o.candidate_deadline_date
        )
        OR EXISTS (
          SELECT 1 FROM posting_deadline_claims c
          WHERE c.posting_key = o.posting_key
            AND c.superseded_by = '[]'::jsonb
            AND c.value IS NOT NULL
            AND left(c.value, 10) <> o.candidate_deadline_date
        )
      )
    )
  ) AS has_conflict,
  true AS wording_is_untrusted
FROM agent_evidence_observations o;

GRANT SELECT, INSERT, UPDATE (corroboration_status, corroborated_by, corroborated_at, corroboration_evidence)
  ON agent_evidence_observations TO jobwatch_collector, jobwatch_owner;
GRANT SELECT ON research_agent_evidence TO jobwatch_collector, jobwatch_owner;

COMMENT ON TABLE agent_evidence_observations IS
  'Append-only agent-contributed public evidence observations. Strictly separate from owner decisions and authoritative verifications.';
COMMENT ON VIEW research_agent_evidence IS
  'Inspectable view of agent-contributed observations with explicit conflict detection and corroboration state.';
