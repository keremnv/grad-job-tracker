-- ISOLATED PROPOSAL (NOT DEPLOYED TO PRODUCTION).
-- Hardened agent-contributed public evidence ingestion & append-only corroboration.
--
-- Strengthens authority, attribution, atomicity, concurrency and corroboration:
-- 1. Separates `db_login` (authenticated `session_user`), `logical_contributor`
--    (execution client identity), and `claimed_actor` (untrusted agent claim).
-- 2. Enforces per-`db_login` rolling hourly/daily ingestion budgets transactionally
--    under `pg_advisory_xact_lock`, preventing arbitrary contributor-name spoofing
--    and concurrent race bypasses.
-- 3. Controlled `SECURITY DEFINER` ingestion functions (`submit_agent_observation`,
--    `submit_agent_vacancy`) remove raw table INSERT privileges from
--    `jobwatch_agent_evidence`, enforcing semantic date checks (`make_date`),
--    strict idempotency payload-collision detection (`submission_sha256`), and
--    atomic candidate + snapshot + observation persistence.
-- 4. Preserves ALL independent corroboration attempts in append-only
--    `agent_evidence_corroborations`, distinguishing 6 states:
--      `submission_recorded`, `source_retrieved`, `wording_matched`,
--      `claim_corroborated`, `evidence_unavailable`, `contradictory_evidence`.

SET LOCAL search_path = jobwatch, pg_catalog;

-- Configurable per-authenticated-login rate limits (never keyed on untrusted contributor strings).
CREATE TABLE IF NOT EXISTS agent_evidence_budgets (
  db_login text PRIMARY KEY CHECK (btrim(db_login) <> ''),
  hourly_limit integer NOT NULL DEFAULT 60 CHECK (hourly_limit BETWEEN 1 AND 10000),
  daily_limit integer NOT NULL DEFAULT 300 CHECK (daily_limit BETWEEN 1 AND 50000),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS agent_evidence_observations (
  observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key text NOT NULL UNIQUE CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
  submission_sha256 text NOT NULL CHECK (submission_sha256 ~ '^[0-9a-f]{64}$'),
  kind text NOT NULL CHECK (kind IN (
    'new_vacancy',
    'official_source_link',
    'deadline_wording',
    'posting_evidence',
    'source_identity'
  )),
  db_login text NOT NULL CHECK (btrim(db_login) <> ''),
  logical_contributor text NOT NULL CHECK (char_length(btrim(logical_contributor)) BETWEEN 3 AND 128),
  claimed_actor text NOT NULL DEFAULT '' CHECK (char_length(claimed_actor) <= 128),
  observed_at timestamptz NOT NULL CHECK (observed_at <= clock_timestamp() + interval '5 minutes'),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  source_url text NOT NULL CHECK (
    source_url ~ '^https://[A-Za-z0-9.-]+(?::443)?(/.*)?$'
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
  candidate_deadline_date date,
  payload jsonb NOT NULL DEFAULT '{}' CHECK (
    jsonb_typeof(payload) = 'object'
    AND octet_length(payload::text) <= 8192
  ),
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (kind <> 'deadline_wording' OR btrim(observed_wording) <> ''),
  CHECK (kind NOT IN ('posting_evidence', 'deadline_wording') OR num_nonnulls(posting_key, candidate_key, lead_key) >= 1)
);

CREATE INDEX IF NOT EXISTS agent_evidence_kind_recorded ON agent_evidence_observations (kind, recorded_at DESC);
CREATE INDEX IF NOT EXISTS agent_evidence_posting ON agent_evidence_observations (posting_key, recorded_at DESC) WHERE posting_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_evidence_candidate ON agent_evidence_observations (candidate_key, recorded_at DESC) WHERE candidate_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_evidence_login_rate ON agent_evidence_observations (db_login, recorded_at DESC);

-- Append-only corroboration history: preserves every independent check and its provenance.
-- NEVER overwritten when a subsequent check runs.
CREATE TABLE IF NOT EXISTS agent_evidence_corroborations (
  corroboration_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  observation_id uuid NOT NULL REFERENCES agent_evidence_observations(observation_id),
  checked_at timestamptz NOT NULL CHECK (checked_at <= clock_timestamp() + interval '5 minutes'),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  checked_by text NOT NULL CHECK (char_length(btrim(checked_by)) BETWEEN 3 AND 128),
  db_login text NOT NULL DEFAULT session_user CHECK (btrim(db_login) <> ''),
  retrieval_status text NOT NULL CHECK (retrieval_status IN (
    'retrieved_200',
    'http_error',
    'blocked_or_403',
    'timeout',
    'js_shell_or_empty',
    'truncated_before_match',
    'network_error',
    'ssrf_refused'
  )),
  wording_status text NOT NULL CHECK (wording_status IN (
    'not_checked',
    'matched',
    'not_found',
    'not_applicable'
  )),
  claim_status text NOT NULL CHECK (claim_status IN (
    'unverified',
    'corroborated',
    'contradicted',
    'unavailable'
  )),
  overall_state text NOT NULL CHECK (overall_state IN (
    'source_retrieved',
    'wording_matched',
    'claim_corroborated',
    'evidence_unavailable',
    'contradictory_evidence'
  )),
  http_status integer,
  final_url text NOT NULL DEFAULT '',
  resolved_ip text NOT NULL DEFAULT '',
  body_sha256 text CHECK (body_sha256 IS NULL OR body_sha256 ~ '^[0-9a-f]{64}$'),
  bytes_read integer NOT NULL DEFAULT 0 CHECK (bytes_read >= 0),
  response_truncated boolean NOT NULL DEFAULT false,
  evidence_details jsonb NOT NULL DEFAULT '{}' CHECK (
    jsonb_typeof(evidence_details) = 'object'
    AND octet_length(evidence_details::text) <= 8192
  ),
  -- Enforce strict corroboration semantics at the database layer:
  -- HTTP 200 alone can NEVER be marked claim_corroborated without wording/claim proof;
  -- unavailable retrievals can NEVER be marked contradictory_evidence!
  CHECK (overall_state <> 'claim_corroborated' OR (retrieval_status = 'retrieved_200' AND claim_status = 'corroborated')),
  CHECK (overall_state <> 'wording_matched' OR (retrieval_status = 'retrieved_200' AND wording_status = 'matched')),
  CHECK (overall_state <> 'contradictory_evidence' OR (retrieval_status = 'retrieved_200' AND claim_status = 'contradicted')),
  CHECK (retrieval_status = 'retrieved_200' OR overall_state = 'evidence_unavailable')
);

CREATE INDEX IF NOT EXISTS agent_corroborations_obs_time
  ON agent_evidence_corroborations (observation_id, checked_at DESC, recorded_at DESC);

-- Helper: strict semantic YYYY-MM-DD calendar date parser (rejects 2026-02-30, 2026-13-01).
CREATE OR REPLACE FUNCTION parse_strict_iso_date(p_value text) RETURNS date
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
DECLARE
  v_y int;
  v_m int;
  v_d int;
BEGIN
  IF p_value !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$' THEN
    RAISE EXCEPTION 'Invalid ISO calendar date format: %', p_value USING ERRCODE = '22007';
  END IF;
  v_y := substr(p_value, 1, 4)::int;
  v_m := substr(p_value, 6, 2)::int;
  v_d := substr(p_value, 9, 2)::int;
  RETURN make_date(v_y, v_m, v_d);
END $$;

-- Helper: transactional per-authenticated-db_login budget guard (serialized via advisory lock).
CREATE OR REPLACE FUNCTION enforce_agent_evidence_budget(p_db_login text) RETURNS void
LANGUAGE plpgsql SET search_path = jobwatch, pg_catalog AS $$
DECLARE
  v_hourly_limit int := 60;
  v_daily_limit int := 300;
  v_hourly_count int;
  v_daily_count int;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('jobwatch.evidence_budget:' || p_db_login, 0));
  SELECT hourly_limit, daily_limit INTO v_hourly_limit, v_daily_limit
  FROM jobwatch.agent_evidence_budgets WHERE db_login = p_db_login;
  IF NOT FOUND THEN
    v_hourly_limit := 60;
    v_daily_limit := 300;
  END IF;
  SELECT count(*) INTO v_hourly_count
  FROM jobwatch.agent_evidence_observations
  WHERE db_login = p_db_login AND recorded_at >= clock_timestamp() - interval '1 hour';
  IF v_hourly_count >= v_hourly_limit THEN
    RAISE EXCEPTION 'Hourly evidence ingestion budget exceeded for %', p_db_login USING ERRCODE = 'P0001';
  END IF;
  SELECT count(*) INTO v_daily_count
  FROM jobwatch.agent_evidence_observations
  WHERE db_login = p_db_login AND recorded_at >= clock_timestamp() - interval '24 hours';
  IF v_daily_count >= v_daily_limit THEN
    RAISE EXCEPTION 'Daily evidence ingestion budget exceeded for %', p_db_login USING ERRCODE = 'P0001';
  END IF;
END $$;

-- Controlled SECURITY DEFINER ingestion boundary for observations.
-- Callers NEVER need raw table INSERT grants and CANNOT spoof db_login or bypass budgets/checks.
CREATE OR REPLACE FUNCTION submit_agent_observation(
  p_idempotency_key text,
  p_submission_sha256 text,
  p_kind text,
  p_logical_contributor text,
  p_claimed_actor text,
  p_observed_at timestamptz,
  p_source_url text,
  p_claim_summary text,
  p_observed_wording text DEFAULT '',
  p_candidate_deadline_date text DEFAULT NULL,
  p_posting_key text DEFAULT NULL,
  p_candidate_key text DEFAULT NULL,
  p_board_key text DEFAULT NULL,
  p_source_id bigint DEFAULT NULL,
  p_lead_key text DEFAULT NULL,
  p_payload jsonb DEFAULT '{}'::jsonb,
  p_payload_sha256 text DEFAULT NULL
) RETURNS TABLE(observation_id uuid, status text, overall_state text, recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = jobwatch, pg_catalog AS $$
#variable_conflict use_column
DECLARE
  v_login text := session_user;
  v_date date := NULL;
  v_existing record;
  v_oid uuid;
  v_rec timestamptz;
BEGIN
  IF p_candidate_deadline_date IS NOT NULL THEN
    v_date := jobwatch.parse_strict_iso_date(p_candidate_deadline_date);
  END IF;
  IF p_observed_at > clock_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'Future observation timestamps are forbidden' USING ERRCODE = '22007';
  END IF;

  -- Serialize idempotency check per key so concurrent retries never race.
  PERFORM pg_advisory_xact_lock(hashtextextended('jobwatch.evidence_idem:' || p_idempotency_key, 0));
  SELECT o.observation_id, o.submission_sha256, o.recorded_at INTO v_existing
  FROM jobwatch.agent_evidence_observations o
  WHERE o.idempotency_key = p_idempotency_key;
  IF FOUND THEN
    IF v_existing.submission_sha256 <> p_submission_sha256 THEN
      RAISE EXCEPTION 'idempotency_key_conflict: key already bound to materially different submission'
        USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.observation_id, 'duplicate_replayed'::text, 'submission_recorded'::text, v_existing.recorded_at;
    RETURN;
  END IF;

  PERFORM jobwatch.enforce_agent_evidence_budget(v_login);

  INSERT INTO jobwatch.agent_evidence_observations(
    idempotency_key, submission_sha256, kind, db_login, logical_contributor, claimed_actor,
    observed_at, source_url, posting_key, candidate_key, board_key, source_id, lead_key,
    claim_summary, observed_wording, candidate_deadline_date, payload, payload_sha256
  ) VALUES (
    p_idempotency_key, p_submission_sha256, p_kind, v_login, p_logical_contributor, coalesce(p_claimed_actor, ''),
    p_observed_at, p_source_url, p_posting_key, p_candidate_key, p_board_key, p_source_id, p_lead_key,
    p_claim_summary, coalesce(p_observed_wording, ''), v_date, coalesce(p_payload, '{}'::jsonb), p_payload_sha256
  ) RETURNING agent_evidence_observations.observation_id, agent_evidence_observations.recorded_at
  INTO v_oid, v_rec;

  RETURN QUERY SELECT v_oid, 'recorded'::text, 'submission_recorded'::text, v_rec;
END $$;

-- Atomic SECURITY DEFINER ingestion boundary for newly discovered public vacancies:
-- creates/links discovery_candidates + append-only discovery_candidate_snapshots +
-- agent_evidence_observations inside ONE single atomic transaction!
CREATE OR REPLACE FUNCTION submit_agent_vacancy(
  p_idempotency_key text,
  p_submission_sha256 text,
  p_logical_contributor text,
  p_claimed_actor text,
  p_observed_at timestamptz,
  p_candidate_key text,
  p_provider text,
  p_board text,
  p_native_id text,
  p_company text,
  p_title text,
  p_posting_url text,
  p_location_text text,
  p_country text,
  p_countries text[],
  p_evidence_completeness text,
  p_description_text text,
  p_description_excerpt text,
  p_complete_description_sha256 text,
  p_excerpt_sha256 text,
  p_evidence_sha256 text,
  p_verdict text,
  p_family text,
  p_codes text[],
  p_rules_version text,
  p_screening_input_sha256 text,
  p_screening_basis text,
  p_claim_summary text,
  p_payload jsonb,
  p_payload_sha256 text
) RETURNS TABLE(candidate_key text, observation_id uuid, status text, overall_state text, recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = jobwatch, pg_catalog AS $$
#variable_conflict use_column
DECLARE
  v_login text := session_user;
  v_existing record;
  v_oid uuid;
  v_rec timestamptz;
BEGIN
  IF p_observed_at > clock_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'Future observation timestamps are forbidden' USING ERRCODE = '22007';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended('jobwatch.evidence_idem:' || p_idempotency_key, 0));
  SELECT o.observation_id, o.submission_sha256, o.candidate_key, o.recorded_at INTO v_existing
  FROM jobwatch.agent_evidence_observations o
  WHERE o.idempotency_key = p_idempotency_key;
  IF FOUND THEN
    IF v_existing.submission_sha256 <> p_submission_sha256 THEN
      RAISE EXCEPTION 'idempotency_key_conflict: key already bound to materially different submission'
        USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.candidate_key, v_existing.observation_id, 'duplicate_replayed'::text, 'submission_recorded'::text, v_existing.recorded_at;
    RETURN;
  END IF;

  PERFORM jobwatch.enforce_agent_evidence_budget(v_login);

  -- Create candidate if absent (never overwrites existing owner-promoted/linked rows).
  INSERT INTO jobwatch.discovery_candidates(
    candidate_key, provider, board, native_id, company, title, posting_url,
    location_text, country, countries,
    description_text, description_excerpt, evidence_completeness,
    complete_description_sha256, excerpt_sha256, description_sha256, evidence_sha256,
    state, discovery_mechanisms, provenance,
    verdict, family, codes, rules_version, screening_input_sha256, screening_basis
  ) VALUES (
    p_candidate_key, p_provider, p_board, p_native_id, p_company, p_title, p_posting_url,
    coalesce(p_location_text, ''), coalesce(p_country, ''), coalesce(p_countries, '{}'::text[]),
    p_description_text, coalesce(p_description_excerpt, ''), p_evidence_completeness,
    p_complete_description_sha256, p_excerpt_sha256, coalesce(p_complete_description_sha256, p_excerpt_sha256), p_evidence_sha256,
    'new', ARRAY['agent_submission'],
    jsonb_build_object('db_login', v_login, 'logical_contributor', p_logical_contributor, 'claimed_actor', coalesce(p_claimed_actor, ''), 'observed_at', p_observed_at, 'unverified_agent_submission', true),
    p_verdict, p_family, p_codes, p_rules_version, p_screening_input_sha256, p_screening_basis
  ) ON CONFLICT (candidate_key) DO NOTHING;

  -- Preserve historical snapshot independently of screening verdict (deduplicated by evidence_sha256).
  INSERT INTO jobwatch.discovery_candidate_snapshots(
    candidate_key, observed_at, discovery_mechanism, source_url,
    evidence_completeness, title, location_text, description_text, description_excerpt,
    complete_description_sha256, excerpt_sha256, evidence_sha256, provenance
  ) VALUES (
    p_candidate_key, p_observed_at, 'agent_submission', p_posting_url,
    p_evidence_completeness, p_title, coalesce(p_location_text, ''), p_description_text, coalesce(p_description_excerpt, ''),
    p_complete_description_sha256, p_excerpt_sha256, p_evidence_sha256,
    jsonb_build_object('db_login', v_login, 'logical_contributor', p_logical_contributor, 'claimed_actor', coalesce(p_claimed_actor, ''))
  ) ON CONFLICT (candidate_key, evidence_sha256) DO NOTHING;

  INSERT INTO jobwatch.agent_evidence_observations(
    idempotency_key, submission_sha256, kind, db_login, logical_contributor, claimed_actor,
    observed_at, source_url, candidate_key, claim_summary, observed_wording,
    payload, payload_sha256
  ) VALUES (
    p_idempotency_key, p_submission_sha256, 'new_vacancy', v_login, p_logical_contributor, coalesce(p_claimed_actor, ''),
    p_observed_at, p_posting_url, p_candidate_key, p_claim_summary, coalesce(p_description_excerpt, ''),
    coalesce(p_payload, '{}'::jsonb), p_payload_sha256
  ) RETURNING agent_evidence_observations.observation_id, agent_evidence_observations.recorded_at
  INTO v_oid, v_rec;

  RETURN QUERY SELECT p_candidate_key, v_oid, 'recorded'::text, 'submission_recorded'::text, v_rec;
END $$;

REVOKE ALL ON FUNCTION parse_strict_iso_date(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION enforce_agent_evidence_budget(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION submit_agent_observation(text,text,text,text,text,timestamptz,text,text,text,text,text,text,text,bigint,text,jsonb,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION submit_agent_vacancy(text,text,text,text,timestamptz,text,text,text,text,text,text,text,text,text,text[],text,text,text,text,text,text,text,text,text[],text,text,text,text,jsonb,text) FROM PUBLIC;

-- Inspectable research projection joining append-only corroboration history + conflict detection.
CREATE OR REPLACE VIEW research_agent_evidence AS
SELECT
  o.observation_id,
  o.idempotency_key,
  o.submission_sha256,
  o.kind,
  o.db_login,
  o.logical_contributor,
  o.claimed_actor,
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
  coalesce(latest.overall_state, 'submission_recorded') AS overall_state,
  latest.retrieval_status AS latest_retrieval_status,
  latest.wording_status AS latest_wording_status,
  latest.claim_status AS latest_claim_status,
  latest.checked_at AS last_corroborated_at,
  latest.checked_by AS last_corroborated_by,
  (SELECT count(*) FROM agent_evidence_corroborations c WHERE c.observation_id = o.observation_id) AS corroboration_attempts,
  (
    coalesce(latest.overall_state, 'submission_recorded') = 'contradictory_evidence'
    OR (
      o.kind = 'deadline_wording' AND o.posting_key IS NOT NULL AND o.candidate_deadline_date IS NOT NULL
      AND (
        EXISTS (
          SELECT 1 FROM agent_evidence_observations o2
          WHERE o2.posting_key = o.posting_key
            AND o2.kind = 'deadline_wording'
            AND o2.observation_id <> o.observation_id
            AND o2.candidate_deadline_date IS NOT NULL
            AND o2.candidate_deadline_date <> o.candidate_deadline_date
        )
        OR EXISTS (
          SELECT 1 FROM posting_deadline_claims c
          WHERE c.posting_key = o.posting_key
            AND c.superseded_by = '[]'::jsonb
            AND c.value IS NOT NULL
            AND left(c.value, 10) <> o.candidate_deadline_date::text
        )
      )
    )
  ) AS has_conflict,
  true AS wording_is_untrusted
FROM agent_evidence_observations o
LEFT JOIN LATERAL (
  SELECT c.* FROM agent_evidence_corroborations c
  WHERE c.observation_id = o.observation_id
  ORDER BY c.checked_at DESC, c.recorded_at DESC
  LIMIT 1
) latest ON true;

CREATE OR REPLACE VIEW research_agent_corroborations AS
SELECT
  corroboration_id, observation_id, checked_at, recorded_at, checked_by, db_login,
  retrieval_status, wording_status, claim_status, overall_state,
  http_status, final_url, resolved_ip, body_sha256, bytes_read, response_truncated, evidence_details
FROM agent_evidence_corroborations;

GRANT SELECT ON agent_evidence_budgets, agent_evidence_observations, agent_evidence_corroborations,
  research_agent_evidence, research_agent_corroborations TO jobwatch_collector, jobwatch_owner;
GRANT INSERT ON agent_evidence_corroborations TO jobwatch_collector, jobwatch_owner;
GRANT INSERT, UPDATE ON agent_evidence_budgets TO jobwatch_owner;
GRANT EXECUTE ON FUNCTION submit_agent_observation(text,text,text,text,text,timestamptz,text,text,text,text,text,text,text,bigint,text,jsonb,text) TO jobwatch_owner;
GRANT EXECUTE ON FUNCTION submit_agent_vacancy(text,text,text,text,timestamptz,text,text,text,text,text,text,text,text,text,text[],text,text,text,text,text,text,text,text,text[],text,text,text,text,jsonb,text) TO jobwatch_owner;
