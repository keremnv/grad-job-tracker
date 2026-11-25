-- ISOLATED PROPOSAL (NOT DEPLOYED TO PRODUCTION).
-- Final trust-boundary hardened agent-contributed public evidence ingestion & corroboration.
--
-- Security and authority guarantees enforced INSIDE PostgreSQL (independent of Python wrappers):
-- 1. Trusted derivation inside SECURITY DEFINER SQL:
--    - `submission_sha256`, `payload_sha256`, `complete_description_sha256`, `excerpt_sha256`,
--      `evidence_sha256`, and `candidate_key` are ALWAYS computed inside SQL — never trusted
--      from caller parameters. Direct SQL callers cannot forge hashes to hide materially
--      different submissions or cause false deduplication.
--    - Caller-supplied screening verdicts, reason codes, and rules versions are NOT accepted:
--      new agent-submitted vacancies enter with neutral `rules_version='unscreened-agent-submission'`,
--      `verdict='review'`, `family='unscreened'`, `codes=ARRAY['evidence.agent_submitted_unscreened']`
--      until the trusted deterministic collector screener runs.
-- 2. Authenticated identity & scoped idempotency:
--    - `db_login := session_user` separated from `logical_contributor` and untrusted `claimed_actor`.
--    - Idempotency is scoped strictly to `(db_login, idempotency_key)` so distinct logins can
--      never collide with or probe each other's keys.
--    - Hourly/daily ingestion budgets are enforced transactionally per `db_login` under
--      `pg_advisory_xact_lock`.
-- 3. Candidate identity consistency & anti-contamination:
--    - Validates HTTPS URLs (including literal private/IMDS/loopback IP refusal), provider/board/
--      URL consistency, and existing `discovery_candidates` identity consistency.
--    - Existing candidates (especially collector-discovered ones) are NEVER mutated or given
--      uncorroborated snapshots by `submit_agent_vacancy`; conflicting identity claims fail loudly.
-- 4. PostgreSQL SECURITY DEFINER hardening:
--    - Every function sets `search_path = pg_catalog, jobwatch, pg_temp` (`pg_temp` LAST!) and
--      qualifies `pg_catalog.` calls, defeating `pg_temp` temporary-schema shadowing attacks.
--    - Owned by non-superuser `jobwatch_migrator`; `REVOKE ALL FROM PUBLIC`.
-- 5. Append-only 6-state corroboration with target-vacancy binding, completeness, and provenance:
--    - Unbound pages (`target_binding_status = 'unbound_target'`) are forbidden by SQL CHECK
--      from ever reaching `overall_state = 'claim_corroborated'`.

SET LOCAL search_path = pg_catalog, jobwatch, pg_temp;

-- Configurable per-authenticated-login rate limits (never keyed on untrusted contributor strings).
CREATE TABLE IF NOT EXISTS jobwatch.agent_evidence_budgets (
  db_login text PRIMARY KEY CHECK (pg_catalog.btrim(db_login) <> ''),
  hourly_limit integer NOT NULL DEFAULT 60 CHECK (hourly_limit BETWEEN 1 AND 10000),
  daily_limit integer NOT NULL DEFAULT 300 CHECK (daily_limit BETWEEN 1 AND 50000),
  updated_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);

-- Helper: strict semantic YYYY-MM-DD calendar date parser (rejects 2026-02-30, 2026-13-01).
CREATE OR REPLACE FUNCTION jobwatch.parse_strict_iso_date(p_value text) RETURNS date
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, jobwatch, pg_temp AS $$
DECLARE
  v_y int;
  v_m int;
  v_d int;
BEGIN
  IF p_value !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$' THEN
    RAISE EXCEPTION 'Invalid ISO calendar date format: %', p_value USING ERRCODE = '22007';
  END IF;
  v_y := pg_catalog.substr(p_value, 1, 4)::int;
  v_m := pg_catalog.substr(p_value, 6, 2)::int;
  v_d := pg_catalog.substr(p_value, 9, 2)::int;
  RETURN pg_catalog.make_date(v_y, v_m, v_d);
END $$;

-- Helper: SQL-layer public HTTPS URL validator (blocks non-HTTPS, credentials, non-443 ports,
-- localhost/.local/.internal, and literal RFC1918/loopback/link-local/IMDS/CGNAT/v4-mapped IPs).
CREATE OR REPLACE FUNCTION jobwatch.validate_agent_https_url(p_url text) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, jobwatch, pg_temp AS $$
DECLARE
  v_url text := pg_catalog.btrim(p_url);
  v_authority text;
  v_host text;
  v_octets text[];
  v_o1 int;
  v_o2 int;
BEGIN
  IF v_url = '' OR pg_catalog.char_length(v_url) > 2048 THEN
    RAISE EXCEPTION 'URL missing or exceeds 2048 characters' USING ERRCODE = '22023';
  END IF;
  IF v_url !~ '^https://[^/\s?#]+(/[^\s]*)?$' THEN
    RAISE EXCEPTION 'Only public https:// URLs without whitespace are accepted' USING ERRCODE = '22023';
  END IF;
  v_authority := pg_catalog.split_part(pg_catalog.substr(v_url, 9), '/', 1);
  IF pg_catalog.strpos(v_authority, '@') > 0 THEN
    RAISE EXCEPTION 'URL credentials are forbidden' USING ERRCODE = '22023';
  END IF;
  -- Allow only default port or explicit :443.
  IF v_authority ~ '^\[.*\]:' THEN
    IF v_authority !~ '^\[.*\]:443$' THEN
      RAISE EXCEPTION 'Only HTTPS port 443 is allowed' USING ERRCODE = '22023';
    END IF;
    v_host := pg_catalog.lower(pg_catalog.regexp_replace(v_authority, '^\[(.*)\]:443$', '\1'));
  ELSIF v_authority ~ '^\[.*\]$' THEN
    v_host := pg_catalog.lower(pg_catalog.regexp_replace(v_authority, '^\[(.*)\]$', '\1'));
  ELSIF pg_catalog.strpos(v_authority, ':') > 0 THEN
    IF v_authority !~ ':443$' THEN
      RAISE EXCEPTION 'Only HTTPS port 443 is allowed' USING ERRCODE = '22023';
    END IF;
    v_host := pg_catalog.lower(pg_catalog.split_part(v_authority, ':', 1));
  ELSE
    v_host := pg_catalog.lower(v_authority);
  END IF;

  v_host := pg_catalog.rtrim(v_host, '.');
  IF v_host = '' OR v_host IN ('localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback')
     OR v_host ~ '\.(local|internal|localhost)$' THEN
    RAISE EXCEPTION 'Local or internal hostnames are forbidden' USING ERRCODE = '22023';
  END IF;

  -- Block IPv6 literals for non-global ranges (loopback, ULA, link-local, v4-mapped, unspecified).
  IF pg_catalog.strpos(v_host, ':') > 0 THEN
    IF v_host ~ '^(::1?|0*:0*:0*:0*:0*:0*:0*:[01]|f[cd]|fe[89ab]|::ffff:)' THEN
      RAISE EXCEPTION 'Non-global IPv6 literal is forbidden' USING ERRCODE = '22023';
    END IF;
  ELSIF v_host ~ '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' THEN
    v_octets := pg_catalog.string_to_array(v_host, '.');
    v_o1 := v_octets[1]::int;
    v_o2 := v_octets[2]::int;
    IF v_o1 IN (0, 10, 127)
       OR (v_o1 = 169 AND v_o2 = 254)
       OR (v_o1 = 172 AND v_o2 BETWEEN 16 AND 31)
       OR (v_o1 = 192 AND v_o2 = 168)
       OR (v_o1 = 100 AND v_o2 BETWEEN 64 AND 127)
       OR v_o1 >= 224 THEN
      RAISE EXCEPTION 'Non-global IPv4 literal is forbidden' USING ERRCODE = '22023';
    END IF;
  ELSIF pg_catalog.strpos(v_host, '.') = 0 THEN
    RAISE EXCEPTION 'Hostname must be a public FQDN' USING ERRCODE = '22023';
  END IF;

  -- Also verify compatibility with existing canonical url_identity().
  PERFORM jobwatch.url_identity(v_url, true);
  RETURN v_url;
END $$;

CREATE TABLE IF NOT EXISTS jobwatch.agent_evidence_observations (
  observation_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
  idempotency_key text NOT NULL CHECK (pg_catalog.char_length(idempotency_key) BETWEEN 8 AND 128),
  submission_sha256 text NOT NULL CHECK (submission_sha256 ~ '^[0-9a-f]{64}$'),
  kind text NOT NULL CHECK (kind IN (
    'new_vacancy',
    'official_source_link',
    'deadline_wording',
    'posting_evidence',
    'source_identity'
  )),
  db_login text NOT NULL CHECK (pg_catalog.btrim(db_login) <> ''),
  logical_contributor text NOT NULL CHECK (pg_catalog.char_length(pg_catalog.btrim(logical_contributor)) BETWEEN 3 AND 128),
  claimed_actor text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(claimed_actor) <= 128),
  observed_at timestamptz NOT NULL CHECK (observed_at <= pg_catalog.clock_timestamp() + interval '5 minutes'),
  recorded_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  source_url text NOT NULL CHECK (
    source_url ~ '^https://[^/\s?#]+(/[^\s]*)?$'
    AND pg_catalog.char_length(source_url) <= 2048
    AND pg_catalog.strpos(pg_catalog.split_part(pg_catalog.substr(source_url, 9), '/', 1), '@') = 0
  ),
  posting_key text REFERENCES jobwatch.postings(posting_key),
  candidate_key text REFERENCES jobwatch.discovery_candidates(candidate_key),
  board_key text REFERENCES jobwatch.discovered_boards(board_key),
  source_id bigint REFERENCES jobwatch.sources(id),
  lead_key text REFERENCES jobwatch.leads(lead_key),
  claim_summary text NOT NULL CHECK (pg_catalog.char_length(pg_catalog.btrim(claim_summary)) BETWEEN 3 AND 500),
  observed_wording text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(observed_wording) <= 4000),
  candidate_deadline_date date,
  payload jsonb NOT NULL DEFAULT '{}' CHECK (
    pg_catalog.jsonb_typeof(payload) = 'object'
    AND pg_catalog.octet_length(payload::text) <= 8192
  ),
  payload_sha256 text NOT NULL CHECK (
    payload_sha256 ~ '^[0-9a-f]{64}$'
    AND payload_sha256 = pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(payload::text, 'UTF8')), 'hex')
  ),
  UNIQUE (db_login, idempotency_key),
  CHECK (kind <> 'deadline_wording' OR pg_catalog.btrim(observed_wording) <> ''),
  CHECK (kind NOT IN ('posting_evidence', 'deadline_wording') OR num_nonnulls(posting_key, candidate_key, lead_key) >= 1)
);

CREATE INDEX IF NOT EXISTS agent_evidence_kind_recorded ON jobwatch.agent_evidence_observations (kind, recorded_at DESC);
CREATE INDEX IF NOT EXISTS agent_evidence_posting ON jobwatch.agent_evidence_observations (posting_key, recorded_at DESC) WHERE posting_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_evidence_candidate ON jobwatch.agent_evidence_observations (candidate_key, recorded_at DESC) WHERE candidate_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_evidence_login_rate ON jobwatch.agent_evidence_observations (db_login, recorded_at DESC);

-- Append-only corroboration history: preserves every independent check, its target-vacancy
-- binding, its explicit evidence completeness, and its provenance.
CREATE TABLE IF NOT EXISTS jobwatch.agent_evidence_corroborations (
  corroboration_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
  observation_id uuid NOT NULL REFERENCES jobwatch.agent_evidence_observations(observation_id),
  checked_at timestamptz NOT NULL CHECK (checked_at <= pg_catalog.clock_timestamp() + interval '5 minutes'),
  recorded_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  checked_by text NOT NULL CHECK (pg_catalog.char_length(pg_catalog.btrim(checked_by)) BETWEEN 3 AND 128),
  db_login text NOT NULL DEFAULT session_user CHECK (pg_catalog.btrim(db_login) <> ''),
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
  target_binding_status text NOT NULL DEFAULT 'not_applicable' CHECK (target_binding_status IN (
    'bound_exact_url',
    'bound_host_and_identity',
    'unbound_target',
    'not_applicable'
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
  evidence_completeness text NOT NULL DEFAULT 'unavailable' CHECK (evidence_completeness IN (
    'complete',
    'partial',
    'truncated',
    'unavailable'
  )),
  evidence_excerpt text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(evidence_excerpt) <= 2500),
  http_status integer,
  final_url text NOT NULL DEFAULT '',
  resolved_ip text NOT NULL DEFAULT '',
  body_sha256 text CHECK (body_sha256 IS NULL OR body_sha256 ~ '^[0-9a-f]{64}$'),
  bytes_read integer NOT NULL DEFAULT 0 CHECK (bytes_read >= 0),
  response_truncated boolean NOT NULL DEFAULT false,
  provenance jsonb NOT NULL DEFAULT '{}' CHECK (
    pg_catalog.jsonb_typeof(provenance) = 'object'
    AND pg_catalog.octet_length(provenance::text) <= 8192
  ),
  evidence_details jsonb NOT NULL DEFAULT '{}' CHECK (
    pg_catalog.jsonb_typeof(evidence_details) = 'object'
    AND pg_catalog.octet_length(evidence_details::text) <= 8192
  ),
  -- Enforce strict corroboration & vacancy-binding semantics at the database layer:
  CHECK (overall_state <> 'claim_corroborated' OR (retrieval_status = 'retrieved_200' AND claim_status = 'corroborated' AND target_binding_status <> 'unbound_target')),
  CHECK (overall_state <> 'wording_matched' OR (retrieval_status = 'retrieved_200' AND wording_status = 'matched')),
  CHECK (overall_state <> 'contradictory_evidence' OR (retrieval_status = 'retrieved_200' AND claim_status = 'contradicted')),
  CHECK (retrieval_status = 'retrieved_200' OR overall_state = 'evidence_unavailable'),
  CHECK (retrieval_status = 'retrieved_200' OR evidence_completeness = 'unavailable'),
  CHECK (NOT response_truncated OR evidence_completeness IN ('truncated', 'unavailable'))
);

CREATE INDEX IF NOT EXISTS agent_corroborations_obs_time
  ON jobwatch.agent_evidence_corroborations (observation_id, checked_at DESC, recorded_at DESC);

-- Helper: transactional per-authenticated-db_login budget guard (serialized via advisory lock).
CREATE OR REPLACE FUNCTION jobwatch.enforce_agent_evidence_budget(p_db_login text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, jobwatch, pg_temp AS $$
DECLARE
  v_hourly_limit int := 60;
  v_daily_limit int := 300;
  v_hourly_count int;
  v_daily_count int;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('jobwatch.evidence_budget:' || p_db_login, 0));
  SELECT hourly_limit, daily_limit INTO v_hourly_limit, v_daily_limit
  FROM jobwatch.agent_evidence_budgets WHERE db_login = p_db_login;
  IF NOT FOUND THEN
    v_hourly_limit := 60;
    v_daily_limit := 300;
  END IF;
  SELECT pg_catalog.count(*) INTO v_hourly_count
  FROM jobwatch.agent_evidence_observations
  WHERE db_login = p_db_login AND recorded_at >= pg_catalog.clock_timestamp() - interval '1 hour';
  IF v_hourly_count >= v_hourly_limit THEN
    RAISE EXCEPTION 'Hourly evidence ingestion budget exceeded for %', p_db_login USING ERRCODE = 'P0001';
  END IF;
  SELECT pg_catalog.count(*) INTO v_daily_count
  FROM jobwatch.agent_evidence_observations
  WHERE db_login = p_db_login AND recorded_at >= pg_catalog.clock_timestamp() - interval '24 hours';
  IF v_daily_count >= v_daily_limit THEN
    RAISE EXCEPTION 'Daily evidence ingestion budget exceeded for %', p_db_login USING ERRCODE = 'P0001';
  END IF;
END $$;

-- Controlled SECURITY DEFINER ingestion boundary for observations.
-- Computes payload_sha256 and submission_sha256 strictly inside SQL; scopes idempotency
-- to (session_user, idempotency_key); validates URLs and dates inside SQL.
CREATE OR REPLACE FUNCTION jobwatch.submit_agent_observation(
  p_idempotency_key text,
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
  p_payload jsonb DEFAULT '{}'::jsonb
) RETURNS TABLE(observation_id uuid, submission_sha256 text, status text, overall_state text, recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, jobwatch, pg_temp AS $$
#variable_conflict use_column
DECLARE
  v_login text := session_user;
  v_url text;
  v_date date := NULL;
  v_wording text := coalesce(p_observed_wording, '');
  v_summary text := pg_catalog.btrim(coalesce(p_claim_summary, ''));
  v_payload jsonb := coalesce(p_payload, '{}'::jsonb);
  v_payload_sha text;
  v_sub_sha text;
  v_existing record;
  v_oid uuid;
  v_rec timestamptz;
BEGIN
  IF p_idempotency_key IS NULL OR pg_catalog.char_length(pg_catalog.btrim(p_idempotency_key)) NOT BETWEEN 8 AND 128 THEN
    RAISE EXCEPTION 'idempotency_key must be 8..128 characters' USING ERRCODE = '22023';
  END IF;
  IF p_observed_at IS NULL OR p_observed_at > pg_catalog.clock_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'Missing or future observation timestamps are forbidden' USING ERRCODE = '22007';
  END IF;
  IF pg_catalog.jsonb_typeof(v_payload) <> 'object' OR pg_catalog.octet_length(v_payload::text) > 8192 THEN
    RAISE EXCEPTION 'payload must be a JSON object <= 8192 bytes' USING ERRCODE = '22023';
  END IF;

  v_url := jobwatch.validate_agent_https_url(p_source_url);
  IF p_candidate_deadline_date IS NOT NULL THEN
    v_date := jobwatch.parse_strict_iso_date(p_candidate_deadline_date);
  END IF;

  -- Compute hashes strictly inside the trusted SQL boundary (never caller-supplied).
  v_payload_sha := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(v_payload::text, 'UTF8')), 'hex');
  v_sub_sha := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'kind', p_kind,
          'source_url', v_url,
          'posting_key', p_posting_key,
          'candidate_key', p_candidate_key,
          'board_key', p_board_key,
          'source_id', p_source_id,
          'lead_key', p_lead_key,
          'claim_summary', v_summary,
          'observed_wording', v_wording,
          'candidate_deadline_date', v_date,
          'payload_sha256', v_payload_sha
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );

  -- Serialize idempotency check per (db_login, idempotency_key) so logins are isolated and retries never race.
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('jobwatch.evidence_idem:' || v_login || ':' || p_idempotency_key, 0));
  SELECT o.observation_id, o.submission_sha256, o.recorded_at INTO v_existing
  FROM jobwatch.agent_evidence_observations o
  WHERE o.db_login = v_login AND o.idempotency_key = p_idempotency_key;
  IF FOUND THEN
    IF v_existing.submission_sha256 <> v_sub_sha THEN
      RAISE EXCEPTION 'idempotency_key_conflict: key already bound to materially different submission'
        USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.observation_id, v_existing.submission_sha256, 'duplicate_replayed'::text, 'submission_recorded'::text, v_existing.recorded_at;
    RETURN;
  END IF;

  PERFORM jobwatch.enforce_agent_evidence_budget(v_login);

  INSERT INTO jobwatch.agent_evidence_observations(
    idempotency_key, submission_sha256, kind, db_login, logical_contributor, claimed_actor,
    observed_at, source_url, posting_key, candidate_key, board_key, source_id, lead_key,
    claim_summary, observed_wording, candidate_deadline_date, payload, payload_sha256
  ) VALUES (
    p_idempotency_key, v_sub_sha, p_kind, v_login, p_logical_contributor, coalesce(p_claimed_actor, ''),
    p_observed_at, v_url, p_posting_key, p_candidate_key, p_board_key, p_source_id, p_lead_key,
    v_summary, v_wording, v_date, v_payload, v_payload_sha
  ) RETURNING agent_evidence_observations.observation_id, agent_evidence_observations.recorded_at
  INTO v_oid, v_rec;

  RETURN QUERY SELECT v_oid, v_sub_sha, 'recorded'::text, 'submission_recorded'::text, v_rec;
END $$;

-- Atomic SECURITY DEFINER ingestion boundary for newly discovered public vacancies.
-- - Derives `candidate_key` and ALL SHA-256 hashes strictly inside SQL.
-- - Enforces provider/board/native_id/URL consistency and forbids contaminating existing candidates.
-- - Never accepts caller-supplied screening verdicts/codes/rules_version (always marks unscreened).
-- - Independently enforces evidence completeness & retention policy invariants.
CREATE OR REPLACE FUNCTION jobwatch.submit_agent_vacancy(
  p_idempotency_key text,
  p_logical_contributor text,
  p_claimed_actor text,
  p_observed_at timestamptz,
  p_provider text,
  p_board text,
  p_native_id text,
  p_company text,
  p_title text,
  p_posting_url text,
  p_location_text text DEFAULT '',
  p_country text DEFAULT '',
  p_countries text[] DEFAULT '{}'::text[],
  p_evidence_completeness text DEFAULT 'complete',
  p_evidence_retention_policy text DEFAULT 'complete_public_ats',
  p_description_text text DEFAULT NULL,
  p_description_excerpt text DEFAULT '',
  p_claim_summary text DEFAULT NULL,
  p_payload jsonb DEFAULT '{}'::jsonb
) RETURNS TABLE(
  candidate_key text,
  observation_id uuid,
  submission_sha256 text,
  complete_description_sha256 text,
  excerpt_sha256 text,
  status text,
  overall_state text,
  recorded_at timestamptz
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, jobwatch, pg_temp AS $$
#variable_conflict use_column
DECLARE
  v_login text := session_user;
  v_url text;
  v_url_exact text;
  v_host text;
  v_path_seg text;
  v_company text := pg_catalog.btrim(coalesce(p_company, ''));
  v_title text := pg_catalog.btrim(coalesce(p_title, ''));
  v_board text := nullif(pg_catalog.btrim(coalesce(p_board, '')), '');
  v_native_id text := nullif(pg_catalog.btrim(coalesce(p_native_id, '')), '');
  v_policy text := coalesce(p_evidence_retention_policy, 'complete_public_ats');
  v_completeness text := coalesce(p_evidence_completeness, 'complete');
  v_desc_text text := p_description_text;
  v_excerpt text := pg_catalog.btrim(coalesce(p_description_excerpt, ''));
  v_complete_sha text := NULL;
  v_excerpt_sha text := NULL;
  v_evidence_sha text;
  v_candidate_key text;
  v_summary text;
  v_payload jsonb := coalesce(p_payload, '{}'::jsonb);
  v_payload_sha text;
  v_sub_sha text;
  v_existing_obs record;
  v_existing_cand record;
  v_snap_id uuid;
  v_oid uuid;
  v_rec timestamptz;
BEGIN
  IF p_idempotency_key IS NULL OR pg_catalog.char_length(pg_catalog.btrim(p_idempotency_key)) NOT BETWEEN 8 AND 128 THEN
    RAISE EXCEPTION 'idempotency_key must be 8..128 characters' USING ERRCODE = '22023';
  END IF;
  IF p_observed_at IS NULL OR p_observed_at > pg_catalog.clock_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'Missing or future observation timestamps are forbidden' USING ERRCODE = '22007';
  END IF;
  IF v_company = '' OR v_title = '' THEN
    RAISE EXCEPTION 'company and title are required' USING ERRCODE = '22023';
  END IF;
  IF p_provider NOT IN ('greenhouse', 'lever', 'ashby', 'workable', 'other') THEN
    RAISE EXCEPTION 'Unsupported provider: %', p_provider USING ERRCODE = '22023';
  END IF;
  IF (v_board IS NULL) <> (v_native_id IS NULL) THEN
    RAISE EXCEPTION 'board and native_id must both be provided or both omitted' USING ERRCODE = '22023';
  END IF;
  IF v_board IS NOT NULL AND p_provider = 'other' THEN
    RAISE EXCEPTION 'provider=other cannot supply native ATS board/native_id' USING ERRCODE = '22023';
  END IF;
  IF v_board IS NOT NULL AND v_board !~ '^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$' THEN
    RAISE EXCEPTION 'Invalid board slug format' USING ERRCODE = '22023';
  END IF;
  IF v_native_id IS NOT NULL AND v_native_id !~ '^[A-Za-z0-9_.-]+$' THEN
    RAISE EXCEPTION 'Invalid native_id format' USING ERRCODE = '22023';
  END IF;
  IF v_policy NOT IN ('complete_public_ats', 'excerpt_only_terms_restricted', 'metadata_only') THEN
    RAISE EXCEPTION 'Invalid evidence_retention_policy: %', v_policy USING ERRCODE = '22023';
  END IF;
  IF v_completeness NOT IN ('complete', 'partial', 'truncated', 'unavailable') THEN
    RAISE EXCEPTION 'Invalid evidence_completeness: %', v_completeness USING ERRCODE = '22023';
  END IF;
  IF pg_catalog.jsonb_typeof(v_payload) <> 'object' OR pg_catalog.octet_length(v_payload::text) > 8192 THEN
    RAISE EXCEPTION 'payload must be a JSON object <= 8192 bytes' USING ERRCODE = '22023';
  END IF;

  v_url := jobwatch.validate_agent_https_url(p_posting_url);
  v_url_exact := jobwatch.url_identity(v_url, true);
  v_host := pg_catalog.lower(pg_catalog.split_part(pg_catalog.split_part(pg_catalog.substr(v_url, 9), '/', 1), ':', 1));
  v_path_seg := pg_catalog.lower(pg_catalog.split_part(pg_catalog.substr(v_url, 9), '/', 2));

  -- Enforce hosted ATS URL <-> provider/board consistency so callers cannot spoof mismatched ATS identities.
  IF v_host ~ '(^|\.)greenhouse\.io$' THEN
    IF p_provider <> 'greenhouse' THEN
      RAISE EXCEPTION 'URL host (%) disagrees with declared provider (%)', v_host, p_provider USING ERRCODE = '22023';
    END IF;
    IF v_board IS NOT NULL AND v_path_seg <> '' AND v_path_seg <> pg_catalog.lower(v_board) THEN
      RAISE EXCEPTION 'Greenhouse URL board (%) disagrees with declared board (%)', v_path_seg, v_board USING ERRCODE = '22023';
    END IF;
  ELSIF v_host ~ '(^|\.)ashbyhq\.com$' THEN
    IF p_provider <> 'ashby' THEN
      RAISE EXCEPTION 'URL host (%) disagrees with declared provider (%)', v_host, p_provider USING ERRCODE = '22023';
    END IF;
    IF v_board IS NOT NULL AND v_path_seg <> '' AND v_path_seg <> pg_catalog.lower(v_board) THEN
      RAISE EXCEPTION 'Ashby URL board (%) disagrees with declared board (%)', v_path_seg, v_board USING ERRCODE = '22023';
    END IF;
  ELSIF v_host ~ '(^|\.)lever\.co$' THEN
    IF p_provider <> 'lever' THEN
      RAISE EXCEPTION 'URL host (%) disagrees with declared provider (%)', v_host, p_provider USING ERRCODE = '22023';
    END IF;
    IF v_board IS NOT NULL AND v_path_seg <> '' AND v_path_seg <> pg_catalog.lower(v_board) THEN
      RAISE EXCEPTION 'Lever URL board (%) disagrees with declared board (%)', v_path_seg, v_board USING ERRCODE = '22023';
    END IF;
  ELSIF v_host ~ '(^|\.)workable\.com$' THEN
    IF p_provider <> 'workable' THEN
      RAISE EXCEPTION 'URL host (%) disagrees with declared provider (%)', v_host, p_provider USING ERRCODE = '22023';
    END IF;
  END IF;

  -- Independently enforce evidence completeness & retention policy invariants inside SQL.
  IF v_desc_text IS NOT NULL AND pg_catalog.char_length(v_desc_text) > 262144 THEN
    RAISE EXCEPTION 'description_text exceeds 262144-character safety cap' USING ERRCODE = '22023';
  END IF;
  IF v_policy = 'metadata_only' THEN
    IF v_completeness <> 'unavailable' OR v_desc_text IS NOT NULL OR v_excerpt <> '' THEN
      RAISE EXCEPTION 'metadata_only retention policy forbids description_text, excerpt, or non-unavailable completeness' USING ERRCODE = '22023';
    END IF;
  ELSIF v_policy = 'excerpt_only_terms_restricted' THEN
    IF v_completeness = 'complete' OR v_desc_text IS NOT NULL THEN
      RAISE EXCEPTION 'excerpt_only_terms_restricted policy forbids complete completeness or description_text' USING ERRCODE = '22023';
    END IF;
  END IF;

  IF v_completeness = 'unavailable' THEN
    IF v_desc_text IS NOT NULL OR v_excerpt <> '' THEN
      RAISE EXCEPTION 'evidence_completeness=unavailable forbids description_text or non-empty description_excerpt' USING ERRCODE = '22023';
    END IF;
  ELSIF v_completeness = 'complete' THEN
    IF v_desc_text IS NULL OR pg_catalog.btrim(v_desc_text) = '' THEN
      RAISE EXCEPTION 'evidence_completeness=complete requires non-empty description_text' USING ERRCODE = '22023';
    END IF;
    v_complete_sha := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(v_desc_text, 'UTF8')), 'hex');
    IF v_excerpt = '' THEN
      v_excerpt := pg_catalog.left(pg_catalog.btrim(pg_catalog.regexp_replace(v_desc_text, '\s+', ' ', 'g')), 2500);
    END IF;
  ELSE
    -- partial or truncated
    IF v_excerpt = '' AND v_desc_text IS NOT NULL AND pg_catalog.btrim(v_desc_text) <> '' THEN
      v_excerpt := pg_catalog.left(pg_catalog.btrim(pg_catalog.regexp_replace(v_desc_text, '\s+', ' ', 'g')), 2500);
    END IF;
    IF v_excerpt = '' THEN
      RAISE EXCEPTION 'partial/truncated evidence requires non-empty excerpt or description text' USING ERRCODE = '22023';
    END IF;
  END IF;

  IF pg_catalog.char_length(v_excerpt) > 2500 THEN
    RAISE EXCEPTION 'description_excerpt exceeds 2500 characters' USING ERRCODE = '22023';
  END IF;
  IF v_excerpt <> '' THEN
    v_excerpt_sha := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(v_excerpt, 'UTF8')), 'hex');
  END IF;

  -- Deterministically derive candidate_key strictly inside SQL.
  IF v_board IS NOT NULL AND v_native_id IS NOT NULL THEN
    v_candidate_key := p_provider || ':' || pg_catalog.lower(v_board) || ':' || v_native_id;
  ELSE
    v_candidate_key := 'agent:' || pg_catalog.substr(
      pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(v_url_exact, 'UTF8')), 'hex'), 1, 24
    );
  END IF;

  v_summary := pg_catalog.left(
    coalesce(nullif(pg_catalog.btrim(coalesce(p_claim_summary, '')), ''),
                        'Agent-submitted public vacancy: ' || v_company || ' - ' || v_title),
    500
  );

  v_evidence_sha := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'candidate_key', v_candidate_key,
          'provider', p_provider,
          'board', v_board,
          'native_id', v_native_id,
          'company', v_company,
          'title', v_title,
          'posting_url', v_url,
          'location_text', coalesce(p_location_text, ''),
          'country', coalesce(p_country, ''),
          'countries', coalesce(p_countries, '{}'::text[]),
          'evidence_completeness', v_completeness,
          'evidence_retention_policy', v_policy,
          'complete_description_sha256', v_complete_sha,
          'excerpt_sha256', v_excerpt_sha
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );

  v_payload := v_payload || pg_catalog.jsonb_build_object(
    'provider', p_provider,
    'board', v_board,
    'native_id', v_native_id,
    'evidence_completeness', v_completeness,
    'evidence_retention_policy', v_policy,
    'screening_status', 'unscreened_agent_submission'
  );
  IF pg_catalog.octet_length(v_payload::text) > 8192 THEN
    RAISE EXCEPTION 'Enriched payload exceeds 8192 bytes' USING ERRCODE = '22023';
  END IF;
  v_payload_sha := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(v_payload::text, 'UTF8')), 'hex');

  v_sub_sha := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'kind', 'new_vacancy',
          'candidate_key', v_candidate_key,
          'source_url', v_url,
          'claim_summary', v_summary,
          'evidence_sha256', v_evidence_sha,
          'payload_sha256', v_payload_sha
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );

  -- Scoped idempotency check per (db_login, idempotency_key).
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('jobwatch.evidence_idem:' || v_login || ':' || p_idempotency_key, 0));
  SELECT o.observation_id, o.submission_sha256, o.candidate_key, o.recorded_at INTO v_existing_obs
  FROM jobwatch.agent_evidence_observations o
  WHERE o.db_login = v_login AND o.idempotency_key = p_idempotency_key;
  IF FOUND THEN
    IF v_existing_obs.submission_sha256 <> v_sub_sha THEN
      RAISE EXCEPTION 'idempotency_key_conflict: key already bound to materially different submission'
        USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing_obs.candidate_key, v_existing_obs.observation_id, v_existing_obs.submission_sha256,
                        v_complete_sha, v_excerpt_sha, 'duplicate_replayed'::text, 'submission_recorded'::text, v_existing_obs.recorded_at;
    RETURN;
  END IF;

  PERFORM jobwatch.enforce_agent_evidence_budget(v_login);

  -- Anti-contamination check against ANY existing discovery_candidates row matching key, URL, or native identity.
  SELECT dc.candidate_key, dc.provider, dc.board, dc.native_id, dc.url_exact, dc.posting_url
  INTO v_existing_cand
  FROM jobwatch.discovery_candidates dc
  WHERE dc.candidate_key = v_candidate_key
     OR dc.url_exact = v_url_exact
     OR (v_board IS NOT NULL AND dc.provider = p_provider AND pg_catalog.lower(dc.board) = pg_catalog.lower(v_board) AND dc.native_id = v_native_id)
  LIMIT 1;

  IF FOUND THEN
    -- Reject any attempt to attach mismatched provider/board/URL onto an existing candidate identity!
    IF v_existing_cand.provider <> p_provider
       OR (v_existing_cand.url_exact <> v_url_exact AND NOT (
            v_board IS NOT NULL AND v_existing_cand.board IS NOT NULL
            AND pg_catalog.lower(v_existing_cand.board) = pg_catalog.lower(v_board)
            AND v_existing_cand.native_id = v_native_id
          ))
       OR (v_board IS NOT NULL AND v_existing_cand.board IS NOT NULL AND (
            pg_catalog.lower(v_existing_cand.board) <> pg_catalog.lower(v_board)
            OR v_existing_cand.native_id <> v_native_id
          )) THEN
      RAISE EXCEPTION 'candidate_identity_conflict: submission conflicts with existing candidate %', v_existing_cand.candidate_key
        USING ERRCODE = '23505';
    END IF;
    -- Existing candidate is consistent: NEVER overwrite or contaminate its candidate row or snapshots!
    v_candidate_key := v_existing_cand.candidate_key;
  ELSE
    -- Create new candidate with strictly unscreened neutral markers (never caller-supplied verdict/codes/rules).
    INSERT INTO jobwatch.discovery_candidates(
      candidate_key, provider, board, native_id, company, title, posting_url,
      location_text, country, countries,
      description_text, description_excerpt, evidence_completeness, evidence_retention_policy,
      complete_description_sha256, excerpt_sha256, description_sha256, evidence_sha256,
      state, first_discovered_at, last_discovered_at, observation_count, discovery_mechanisms, provenance,
      verdict, family, codes, rules_version, screening_input_sha256, screening_basis
    ) VALUES (
      v_candidate_key, p_provider, v_board, v_native_id, v_company, v_title, v_url,
      coalesce(p_location_text, ''), coalesce(p_country, ''), coalesce(p_countries, '{}'::text[]),
      v_desc_text, v_excerpt, v_completeness, v_policy,
      v_complete_sha, v_excerpt_sha, coalesce(v_complete_sha, v_excerpt_sha), v_evidence_sha,
      'new', p_observed_at, p_observed_at, 1, ARRAY['agent_submission'],
      pg_catalog.jsonb_build_object(
        'db_login', v_login,
        'logical_contributor', p_logical_contributor,
        'claimed_actor', coalesce(p_claimed_actor, ''),
        'observed_at', p_observed_at,
        'unverified_agent_submission', true
      ),
      'review', 'unscreened', ARRAY['evidence.agent_submitted_unscreened'], 'unscreened-agent-submission',
      NULL, 'title_metadata_only'
    );

    INSERT INTO jobwatch.discovery_candidate_snapshots(
      candidate_key, first_observed_at, last_observed_at, observation_count,
      discovery_mechanism, source_url, evidence_completeness, evidence_retention_policy,
      title, location_text, description_text, description_excerpt,
      complete_description_sha256, excerpt_sha256, evidence_sha256, provenance
    ) VALUES (
      v_candidate_key, p_observed_at, p_observed_at, 1,
      'agent_submission', v_url, v_completeness, v_policy,
      v_title, coalesce(p_location_text, ''), v_desc_text, v_excerpt,
      v_complete_sha, v_excerpt_sha, v_evidence_sha,
      pg_catalog.jsonb_build_object(
        'db_login', v_login,
        'logical_contributor', p_logical_contributor,
        'claimed_actor', coalesce(p_claimed_actor, ''),
        'unverified_agent_submission', true
      )
    ) RETURNING snapshot_id INTO v_snap_id;

    UPDATE jobwatch.discovery_candidates
    SET current_snapshot_id = v_snap_id
    WHERE discovery_candidates.candidate_key = v_candidate_key;
  END IF;

  INSERT INTO jobwatch.agent_evidence_observations(
    idempotency_key, submission_sha256, kind, db_login, logical_contributor, claimed_actor,
    observed_at, source_url, candidate_key, claim_summary, observed_wording,
    payload, payload_sha256
  ) VALUES (
    p_idempotency_key, v_sub_sha, 'new_vacancy', v_login, p_logical_contributor, coalesce(p_claimed_actor, ''),
    p_observed_at, v_url, v_candidate_key, v_summary, v_excerpt,
    v_payload, v_payload_sha
  ) RETURNING agent_evidence_observations.observation_id, agent_evidence_observations.recorded_at
  INTO v_oid, v_rec;

  RETURN QUERY SELECT v_candidate_key, v_oid, v_sub_sha, v_complete_sha, v_excerpt_sha, 'recorded'::text, 'submission_recorded'::text, v_rec;
END $$;

REVOKE ALL ON FUNCTION jobwatch.parse_strict_iso_date(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION jobwatch.validate_agent_https_url(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION jobwatch.enforce_agent_evidence_budget(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION jobwatch.submit_agent_observation(text,text,text,text,timestamptz,text,text,text,text,text,text,text,bigint,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION jobwatch.submit_agent_vacancy(text,text,text,timestamptz,text,text,text,text,text,text,text,text,text[],text,text,text,text,text,jsonb) FROM PUBLIC;

-- Inspectable research projection joining append-only corroboration history + conflict detection.
CREATE OR REPLACE VIEW jobwatch.research_agent_evidence AS
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
  latest.target_binding_status AS latest_target_binding_status,
  latest.wording_status AS latest_wording_status,
  latest.claim_status AS latest_claim_status,
  latest.evidence_completeness AS latest_evidence_completeness,
  latest.checked_at AS last_corroborated_at,
  latest.checked_by AS last_corroborated_by,
  (SELECT pg_catalog.count(*) FROM jobwatch.agent_evidence_corroborations c WHERE c.observation_id = o.observation_id) AS corroboration_attempts,
  (
    coalesce(latest.overall_state, 'submission_recorded') = 'contradictory_evidence'
    OR (
      o.kind = 'deadline_wording' AND o.posting_key IS NOT NULL AND o.candidate_deadline_date IS NOT NULL
      AND (
        EXISTS (
          SELECT 1 FROM jobwatch.agent_evidence_observations o2
          WHERE o2.posting_key = o.posting_key
            AND o2.kind = 'deadline_wording'
            AND o2.observation_id <> o.observation_id
            AND o2.candidate_deadline_date IS NOT NULL
            AND o2.candidate_deadline_date <> o.candidate_deadline_date
        )
        OR EXISTS (
          SELECT 1 FROM jobwatch.posting_deadline_claims c
          WHERE c.posting_key = o.posting_key
            AND c.superseded_by = '[]'::jsonb
            AND c.value IS NOT NULL
            AND pg_catalog.left(c.value, 10) <> o.candidate_deadline_date::text
        )
      )
    )
  ) AS has_conflict,
  true AS wording_is_untrusted
FROM jobwatch.agent_evidence_observations o
LEFT JOIN LATERAL (
  SELECT c.* FROM jobwatch.agent_evidence_corroborations c
  WHERE c.observation_id = o.observation_id
  ORDER BY c.checked_at DESC, c.recorded_at DESC
  LIMIT 1
) latest ON true;

CREATE OR REPLACE VIEW jobwatch.research_agent_corroborations AS
SELECT
  corroboration_id, observation_id, checked_at, recorded_at, checked_by, db_login,
  retrieval_status, target_binding_status, wording_status, claim_status, overall_state,
  evidence_completeness, evidence_excerpt, http_status, final_url, resolved_ip,
  body_sha256, bytes_read, response_truncated, provenance, evidence_details
FROM jobwatch.agent_evidence_corroborations;

GRANT SELECT ON jobwatch.agent_evidence_budgets, jobwatch.agent_evidence_observations, jobwatch.agent_evidence_corroborations,
  jobwatch.research_agent_evidence, jobwatch.research_agent_corroborations TO jobwatch_collector, jobwatch_owner;
GRANT INSERT ON jobwatch.agent_evidence_corroborations TO jobwatch_collector, jobwatch_owner;
GRANT INSERT, UPDATE ON jobwatch.agent_evidence_budgets TO jobwatch_owner;
GRANT EXECUTE ON FUNCTION jobwatch.submit_agent_observation(text,text,text,text,timestamptz,text,text,text,text,text,text,text,bigint,text,jsonb) TO jobwatch_owner;
GRANT EXECUTE ON FUNCTION jobwatch.submit_agent_vacancy(text,text,text,timestamptz,text,text,text,text,text,text,text,text,text[],text,text,text,text,text,jsonb) TO jobwatch_owner;
