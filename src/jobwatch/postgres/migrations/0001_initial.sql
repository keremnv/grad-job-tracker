-- Initial PostgreSQL 17 schema. Applied transactionally by jobwatch.postgres.migrate.
-- The runner creates jobwatch and its private migration ledger first; bootstrap roles separately.
REVOKE ALL ON SCHEMA jobwatch FROM PUBLIC;
SET LOCAL search_path = jobwatch, pg_catalog;

-- Conservative URL identity, matching identity.py for supported HTTPS URLs.
-- Fragments are significant for linking; fragment-free identity only refuses duplicates.
-- This is not provider/host validation: keep feeds.public_url / _link at ingestion.
CREATE FUNCTION url_identity(u text, keep_fragment boolean) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
DECLARE m text[]; host text; address inet;
BEGIN
  IF u ~ '[[:space:][:cntrl:]<>\\]' THEN RAISE EXCEPTION 'invalid HTTPS URL'; END IF;
  m := regexp_match(u, '^https://([^/?#]+)([^?#]*)(\?[^#]*)?(#.*)?$', 'i');
  IF m IS NULL OR m[1] LIKE '%@%' THEN RAISE EXCEPTION 'invalid HTTPS authority'; END IF;
  host := lower(regexp_replace(m[1], ':(0*443)?$', ''));
  IF host = '' OR (host NOT LIKE '[%' AND strpos(host, ':') > 0)
     OR (host LIKE '[%' AND host !~ '^\[[0-9a-f:.]+\]$') THEN
    RAISE EXCEPTION 'invalid HTTPS host/port';
  END IF;
  IF host LIKE '[%' THEN
    BEGIN
      address := substring(host FROM 2 FOR length(host)-2)::inet;
    EXCEPTION WHEN invalid_text_representation THEN
      RAISE EXCEPTION 'invalid HTTPS IPv6 address';
    END;
    IF family(address) <> 6 THEN RAISE EXCEPTION 'invalid HTTPS IPv6 address'; END IF;
  END IF;
  -- Python loose_url historically omits IPv6 brackets; preserve that identity too.
  IF NOT keep_fragment THEN host := trim(both '[]' from host); END IF;
  -- urllib.urlunsplit drops empty query/fragment delimiters. Preserve that identity.
  RETURN 'https://' || host || coalesce(nullif(m[2], ''), '/') || coalesce(nullif(m[3], '?'), '')
         || CASE WHEN keep_fragment THEN coalesce(nullif(m[4], '#'), '') ELSE '' END;
END $$;

CREATE TABLE employers (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
  name text NOT NULL CHECK (btrim(name) <> ''),
  careers_url text CHECK (careers_url IS NULL OR url_identity(careers_url, true) IS NOT NULL),
  watch_status text NOT NULL DEFAULT 'unverified'
    CHECK (watch_status IN ('open','register_interest','opening_later','listed_in_feed','unverified')),
  watch_checked_at timestamptz,
  notes text,
  details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details)='object') -- labelled legacy config extras
);

CREATE TABLE sources (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  provider text NOT NULL CHECK (provider IN ('greenhouse','lever','ashby')),
  board text NOT NULL CHECK (board ~ '^[A-Za-z0-9_-]+$'),
  region text NOT NULL DEFAULT 'global' CHECK (region IN ('global','eu')),
  employer_id bigint REFERENCES employers,
  status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','monitored','paused','retired')),
  validation_url text CHECK (validation_url IS NULL OR url_identity(validation_url, true) IS NOT NULL),
  endpoint_checked_at timestamptz,
  mapping_verified_at timestamptz,
  mapping_verified_by text,
  mapping_evidence text,
  discovered_by text,
  discovered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  -- Increment/change only in a source-locked processing deployment/rescreen transaction.
  processing_revision text NOT NULL DEFAULT 'pipeline-v1',
  last_published_run_id uuid,
  CHECK (provider <> 'ashby' OR region = 'global'),
  CHECK (status <> 'monitored' OR (employer_id IS NOT NULL AND validation_url IS NOT NULL
    AND endpoint_checked_at IS NOT NULL AND mapping_verified_at IS NOT NULL
    AND nullif(btrim(mapping_verified_by),'') IS NOT NULL
    AND nullif(btrim(mapping_evidence),'') IS NOT NULL)),
  UNIQUE (id, provider, board)
);
-- Region is endpoint configuration, NOT another namespace for existing posting keys.
CREATE UNIQUE INDEX sources_namespace ON sources (provider, lower(board));

CREATE TABLE collection_runs (
  id uuid PRIMARY KEY,
  run_order bigint GENERATED ALWAYS AS IDENTITY UNIQUE NOT NULL,
  source_id bigint NOT NULL REFERENCES sources,
  mode text NOT NULL CHECK (mode IN ('publish','validate','import')),
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  fetched_at timestamptz,
  finished_at timestamptz,
  status text NOT NULL DEFAULT 'running'
    CHECK (status IN ('running','published','validated','failed','superseded','skipped','imported')),
  request_version text NOT NULL,
  processing_revision text NOT NULL,
  source_records integer CHECK (source_records >= 0),
  vacancies integer CHECK (vacancies >= 0),
  skipped_non_vacancies integer CHECK (skipped_non_vacancies >= 0),
  -- Protocol success need not prove a snapshot (e.g. unverified offset pagination).
  absence_safe boolean NOT NULL DEFAULT false,
  error text,
  ci_run text,
  details jsonb NOT NULL DEFAULT '{}', -- exact legacy health/provenance for explicitly imported baselines
  actor text NOT NULL DEFAULT nullif(current_setting('jobwatch.actor', true),'') CHECK (btrim(actor) <> ''),
  UNIQUE (source_id, id),
  CHECK ((status = 'running') = (finished_at IS NULL)),
  CHECK ((status = 'failed') = (error IS NOT NULL)),
  CHECK ((status = 'imported') = (mode = 'import')),
  CHECK (jsonb_typeof(details) = 'object'),
  CHECK (status <> 'published' OR (mode = 'publish' AND fetched_at IS NOT NULL
    AND source_records IS NOT NULL AND vacancies IS NOT NULL AND skipped_non_vacancies IS NOT NULL
    AND source_records = vacancies + skipped_non_vacancies)),
  CHECK (status <> 'validated' OR mode = 'validate')
);
CREATE INDEX runs_latest ON collection_runs (source_id, run_order DESC);
ALTER TABLE sources ADD CONSTRAINT sources_last_run
  FOREIGN KEY (id, last_published_run_id) REFERENCES collection_runs (source_id, id);

-- Identity, observation and CURRENT screening; source-derived values live on immutable versions.
-- Screening has a separate semantic lifecycle, but needs no separate current-state table.
CREATE TABLE postings (
  posting_key text PRIMARY KEY,
  source_id bigint NOT NULL,
  provider text NOT NULL,
  board text NOT NULL,
  native_id text NOT NULL CHECK (native_id ~ '^[A-Za-z0-9_.-]+$'),
  status text NOT NULL CHECK (status IN ('listed','not_seen')),
  first_seen_at timestamptz NOT NULL,
  first_seen_basis text NOT NULL CHECK (first_seen_basis IN ('observed','index_backfill','legacy_retained')),
  last_seen_at timestamptz, -- actual last positive fetch, NOT latest source success when absence_safe=false
  missing_since timestamptz,
  current_version_id uuid NOT NULL,
  verdict text NOT NULL CHECK (verdict IN ('priority','review','excluded')),
  family text NOT NULL,
  codes text[] NOT NULL,
  rules_version text NOT NULL,
  screening_input_sha256 text CHECK (screening_input_sha256 ~ '^[0-9a-f]{64}$'),
  screening_basis text NOT NULL CHECK (screening_basis IN ('full','title_metadata_only','legacy')),
  screened_at timestamptz NOT NULL,
  UNIQUE (source_id, native_id),
  UNIQUE (posting_key, source_id),
  FOREIGN KEY (source_id, provider, board) REFERENCES sources (id, provider, board),
  CHECK (posting_key = provider || ':' || lower(board) || ':' || native_id),
  CHECK ((status = 'not_seen') = (missing_since IS NOT NULL))
);
CREATE INDEX postings_source ON postings (source_id, status);

-- ONE immutable evidence/representation table. No independent description/section history.
CREATE TABLE posting_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  posting_key text NOT NULL,
  source_id bigint NOT NULL,
  origin text NOT NULL CHECK (origin IN ('collector','git_history','legacy_index')),
  evidence bytea NOT NULL,
  evidence_sha256 text GENERATED ALWAYS AS (encode(sha256(evidence),'hex')) STORED,
  -- evidence = deterministic ASCII JSON encoding of full parsed native record, or
  -- honestly labelled legacy snapshot. It is NOT claimed to be upstream HTTP bytes.
  first_run_id uuid,
  observed_at timestamptz NOT NULL,
  provenance jsonb NOT NULL DEFAULT '{}', -- import commit/path; no fabricated employer timestamp
  normalizer_version text NOT NULL,
  normalized_sha256 text NOT NULL CHECK (normalized_sha256 ~ '^[0-9a-f]{64}$'),
  nul_replaced boolean NOT NULL DEFAULT false,
  company text NOT NULL,
  title text NOT NULL CHECK (btrim(title) <> ''),
  posting_url text NOT NULL,
  url_exact text GENERATED ALWAYS AS (url_identity(posting_url, true)) STORED,
  location_text text NOT NULL,
  country text NOT NULL DEFAULT '', -- v1 provider spelling, NOT silently upgraded to ISO codes
  countries text[] NOT NULL DEFAULT '{}',
  department text NOT NULL DEFAULT '',
  team text NOT NULL DEFAULT '',
  employment_type text NOT NULL DEFAULT '',
  salary text NOT NULL DEFAULT '',
  published_at text NOT NULL DEFAULT '', -- v1 legacy label; use named source_dates for research
  source_dates jsonb NOT NULL DEFAULT '{}',
  deadlines jsonb NOT NULL DEFAULT '[]',
  description_text text, -- NULL = not retained, '' = supplied empty
  extra_fields jsonb NOT NULL DEFAULT '{}', -- later explicit locations/workplace/compensation projections
  search_tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english'::regconfig, title), 'A') ||
    setweight(to_tsvector('english'::regconfig, department || ' ' || team || ' ' || location_text), 'B') ||
    setweight(to_tsvector('english'::regconfig, coalesce(description_text,'')), 'D')) STORED,
  UNIQUE (posting_key, id),
  UNIQUE (posting_key, evidence_sha256, normalizer_version, normalized_sha256),
  FOREIGN KEY (posting_key, source_id) REFERENCES postings (posting_key, source_id),
  FOREIGN KEY (source_id, first_run_id) REFERENCES collection_runs (source_id, id),
  CHECK ((origin = 'collector') = (first_run_id IS NOT NULL)),
  CHECK (jsonb_typeof(provenance) = 'object' AND jsonb_typeof(extra_fields) = 'object'
    AND jsonb_typeof(source_dates) = 'object' AND jsonb_typeof(deadlines) = 'array')
);
CREATE INDEX versions_search ON posting_versions USING gin (search_tsv);
-- Deferred cycle permits INSERT posting -> INSERT version in one transaction using a prechosen UUID.
ALTER TABLE postings ADD CONSTRAINT postings_current_version
  FOREIGN KEY (posting_key, current_version_id) REFERENCES posting_versions (posting_key, id)
  DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE posting_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  posting_key text NOT NULL,
  source_id bigint NOT NULL,
  run_id uuid,
  observed_at timestamptz NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  category text NOT NULL CHECK (category IN ('observation','provider','normalizer','screening','import')),
  kind text NOT NULL,
  before_version_id uuid,
  after_version_id uuid,
  comparison_version text,
  changes jsonb NOT NULL DEFAULT '{}',
  actor text NOT NULL DEFAULT nullif(current_setting('jobwatch.actor', true),'') CHECK (btrim(actor) <> ''),
  FOREIGN KEY (posting_key, source_id) REFERENCES postings (posting_key, source_id),
  FOREIGN KEY (source_id, run_id) REFERENCES collection_runs (source_id, id),
  FOREIGN KEY (posting_key, before_version_id) REFERENCES posting_versions (posting_key, id),
  FOREIGN KEY (posting_key, after_version_id) REFERENCES posting_versions (posting_key, id),
  CHECK ((category,kind) IN (
    ('observation','first_seen'),('observation','backfill'),('observation','not_seen'),('observation','reappeared'),
    ('provider','changed'),('provider','baseline'),('normalizer','renormalized'),
    ('screening','rescreened'),('import','legacy_event'))),
  CHECK (category = 'import' OR after_version_id IS NOT NULL),
  CHECK (category NOT IN ('provider','normalizer') OR
    (before_version_id IS NOT NULL AND comparison_version IS NOT NULL)),
  CHECK (category <> 'observation' OR run_id IS NOT NULL),
  CHECK (jsonb_typeof(changes) = 'object')
);
CREATE INDEX events_posting ON posting_events (posting_key, id);
CREATE UNIQUE INDEX events_run_once ON posting_events (run_id, posting_key, category, kind) WHERE run_id IS NOT NULL;

CREATE TABLE verifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), -- caller supplies stable ID for uncertain-COMMIT retries
  posting_key text NOT NULL REFERENCES postings,
  kind text NOT NULL CHECK (kind IN ('application_status','deadline','description')),
  checked_at timestamptz NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor text NOT NULL DEFAULT nullif(current_setting('jobwatch.actor', true),'') CHECK (btrim(actor) <> ''),
  checked_by text NOT NULL CHECK (checked_by IN ('owner','agent','agent_on_owner_instruction','unrecorded_legacy')),
  url text NOT NULL CHECK (url_identity(url,true) IS NOT NULL),
  method text,
  status text CHECK (status IN ('open_verified','closed_verified','unknown')),
  wording text,
  value text, -- exact source date/time; no invented timezone; interpretation belongs in Python
  version_id uuid,
  description_sha256 text CHECK (description_sha256 ~ '^[0-9a-f]{64}$'), -- retained legacy binding if text unavailable
  legacy_details jsonb NOT NULL DEFAULT '{}',
  FOREIGN KEY (posting_key, version_id) REFERENCES posting_versions (posting_key, id),
  CHECK (checked_at <= recorded_at),
  CHECK (kind <> 'application_status' OR (status IS NOT NULL AND nullif(btrim(method),'') IS NOT NULL)),
  CHECK (kind <> 'application_status' OR status = 'unknown'
    OR checked_by IN ('owner','agent_on_owner_instruction','unrecorded_legacy')),
  CHECK (kind <> 'deadline' OR nullif(btrim(wording),'') IS NOT NULL),
  CHECK (kind <> 'description' OR version_id IS NOT NULL OR description_sha256 IS NOT NULL)
);
CREATE INDEX verifications_latest ON verifications (posting_key, kind, checked_at DESC, recorded_at DESC, id);

CREATE TABLE leads (
  lead_key text PRIMARY KEY CHECK (lead_key ~ '^(vacancy|programme):[a-z0-9:._-]+$'),
  kind text NOT NULL CHECK (kind IN ('vacancy','programme')),
  employer_id bigint REFERENCES employers,
  company text NOT NULL,
  title text NOT NULL,
  programme_key text, -- stable employer/programme slug, independent of its year's source URL
  cycle_year integer CHECK (cycle_year BETWEEN 2000 AND 2199),
  cycle_wording text,
  state text NOT NULL,
  url text NOT NULL,
  url_exact text GENERATED ALWAYS AS (url_identity(url,true)) STORED,
  official boolean NOT NULL,
  source_note text NOT NULL,
  wording text,
  deadline_date date,
  deadline_wording text,
  posting_key text REFERENCES postings,
  checked_at timestamptz NOT NULL,
  checked_by text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  CHECK (split_part(lead_key,':',1) = kind),
  CHECK ((kind = 'programme') = (programme_key IS NOT NULL)),
  CHECK (kind <> 'programme' OR posting_key IS NULL),
  CHECK ((kind='vacancy' AND state IN ('listed','employer_says_open','not_listed','unverified'))
    OR (kind='programme' AND state IN ('open','announced_window','register_interest_only',
      'closed_reopening_unknown','previous_cycle','not_found'))),
  CHECK (cycle_year IS NULL OR strpos(coalesce(cycle_wording,''),cycle_year::text)>0),
  CHECK (state <> 'announced_window' OR nullif(details->>'window','') IS NOT NULL),
  CHECK (deadline_date IS NULL OR nullif(btrim(deadline_wording),'') IS NOT NULL)
);
CREATE UNIQUE INDEX leads_programme_cycle ON leads (programme_key, cycle_year) NULLS NOT DISTINCT WHERE kind='programme';
CREATE UNIQUE INDEX leads_vacancy_url ON leads (url_exact) WHERE kind='vacancy';

CREATE TABLE triage (
  posting_key text PRIMARY KEY REFERENCES postings,
  decision text NOT NULL CHECK (decision IN ('interested','dismissed','later')),
  reason_code text CHECK (reason_code IN ('not_engineering','not_entry_level','location','eligibility','employer',
    'role_fit','duplicate','closed_or_expired','already_handled','other')),
  note text,
  revisit_on date CHECK (revisit_on IS NULL OR decision='later'),
  decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  recorded_by text NOT NULL CHECK (recorded_by IN ('owner','agent_on_owner_instruction'))
);

CREATE TABLE applications (
  job_key text PRIMARY KEY CHECK (job_key ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,199}$'),
  posting_key text UNIQUE REFERENCES postings, -- may later link a manual job_key without changing it
  company text NOT NULL CHECK (btrim(company)<>''),
  role text NOT NULL CHECK (btrim(role)<>''),
  url text NOT NULL,
  url_exact text GENERATED ALWAYS AS (url_identity(url,true)) STORED,
  url_loose text GENERATED ALWAYS AS (url_identity(url,false)) STORED UNIQUE,
  stage text NOT NULL DEFAULT 'bookmarked'
    CHECK (stage IN ('bookmarked','preparing','applied','assessment','interview','offer','rejected','withdrawn')),
  date_applied date,
  cv_version text,
  next_action text,
  next_action_date date,
  notes text
);

-- Private recovery log for mutable human/research controls, never collector payloads.
CREATE TABLE owner_audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT clock_timestamp(),
  transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
  db_login text NOT NULL,
  actor text NOT NULL CHECK (btrim(actor)<>''),
  table_name text NOT NULL,
  op text NOT NULL CHECK (op IN ('INSERT','UPDATE','DELETE')),
  old_row jsonb,
  new_row jsonb
);
CREATE FUNCTION audit_owner_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE old_image jsonb; new_image jsonb; claimed_actor text;
BEGIN
  IF TG_OP <> 'INSERT' THEN old_image := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN new_image := to_jsonb(NEW); END IF;
  IF TG_TABLE_NAME = 'sources' THEN
    old_image := old_image - 'last_published_run_id' - 'processing_revision';
    new_image := new_image - 'last_published_run_id' - 'processing_revision';
    IF TG_OP = 'UPDATE' AND old_image = new_image THEN RETURN NULL; END IF;
  END IF;
  claimed_actor := nullif(btrim(current_setting('jobwatch.actor',true)),'');
  IF claimed_actor IS NULL THEN RAISE EXCEPTION 'SET LOCAL jobwatch.actor before owner/control writes'; END IF;
  IF TG_OP = 'UPDATE' AND old_image = new_image THEN RETURN NULL; END IF;
  INSERT INTO jobwatch.owner_audit(db_login,actor,table_name,op,old_row,new_row)
    VALUES(session_user,claimed_actor,TG_TABLE_NAME,TG_OP,old_image,new_image);
  RETURN NULL;
END $$;
CREATE TRIGGER audit_owner AFTER INSERT OR UPDATE OR DELETE ON employers FOR EACH ROW EXECUTE FUNCTION audit_owner_change();
CREATE TRIGGER audit_owner AFTER INSERT OR UPDATE OR DELETE ON sources FOR EACH ROW EXECUTE FUNCTION audit_owner_change();
CREATE TRIGGER audit_owner AFTER INSERT OR UPDATE OR DELETE ON leads FOR EACH ROW EXECUTE FUNCTION audit_owner_change();
CREATE TRIGGER audit_owner AFTER INSERT OR UPDATE OR DELETE ON triage FOR EACH ROW EXECUTE FUNCTION audit_owner_change();
CREATE TRIGGER audit_owner AFTER INSERT OR UPDATE OR DELETE ON applications FOR EACH ROW EXECUTE FUNCTION audit_owner_change();

CREATE VIEW source_health AS
SELECT s.id AS source_id,s.provider,s.board,s.region,s.status,
  ok.fetched_at AS last_success_at,ok.vacancies,ok.absence_safe,
  last_try.started_at AS last_attempt_at,last_try.status AS last_attempt_status,last_try.error,
  (ok.fetched_at IS NULL OR ok.fetched_at < now()-interval '3 days') AS stale
FROM sources s
LEFT JOIN collection_runs ok ON ok.id=s.last_published_run_id
LEFT JOIN LATERAL (SELECT r.* FROM collection_runs r WHERE r.source_id=s.id AND r.mode='publish'
  ORDER BY r.run_order DESC LIMIT 1) last_try ON true;

CREATE VIEW research_postings AS
SELECT p.*,v.company,v.title,v.posting_url,v.location_text,v.country,v.countries,
  v.department,v.team,v.employment_type,v.salary,v.source_dates,v.deadlines,
  v.description_text,v.normalizer_version,v.evidence_sha256,v.search_tsv,v.extra_fields,
  h.status AS source_status,h.stale,h.last_attempt_status
FROM postings p JOIN posting_versions v ON (v.posting_key,v.id)=(p.posting_key,p.current_version_id)
JOIN source_health h ON h.source_id=p.source_id;

-- Explicit links first, then exact URL only for an unlinked manual row and an unambiguous posting URL.
CREATE VIEW opportunity_workflow AS
SELECT r.*,t.decision,t.revisit_on,a.job_key AS application_job_key,a.stage,
  CASE WHEN a.stage IN ('applied','assessment','interview','offer') THEN 'applied'
       WHEN a.stage IN ('rejected','withdrawn') THEN 'closed'
       WHEN a.job_key IS NOT NULL THEN 'preparing'
       WHEN t.decision IS NULL THEN 'unreviewed'
       WHEN t.decision='later' AND t.revisit_on <= (now() AT TIME ZONE 'Europe/London')::date THEN 'revisit_due'
       ELSE t.decision END AS action_state
FROM research_postings r
JOIN posting_versions v ON v.id=r.current_version_id
LEFT JOIN triage t ON t.posting_key=r.posting_key
LEFT JOIN LATERAL (
  SELECT a.* FROM applications a
  WHERE a.posting_key=r.posting_key OR (a.posting_key IS NULL AND a.url_exact=v.url_exact
    AND NOT EXISTS (SELECT 1 FROM postings p2 JOIN posting_versions v2 ON v2.id=p2.current_version_id
      WHERE p2.posting_key<>r.posting_key AND v2.url_exact=v.url_exact))
  ORDER BY (a.posting_key IS NOT NULL) DESC LIMIT 1
) a ON true;

-- Explicit grants: NO schema ownership, CREATE, TRUNCATE, role membership or blanket future-table grants.
GRANT USAGE ON SCHEMA jobwatch TO jobwatch_collector,jobwatch_owner;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA jobwatch FROM PUBLIC;
GRANT EXECUTE ON FUNCTION url_identity(text,boolean) TO jobwatch_collector,jobwatch_owner;
GRANT SELECT (id,slug,name,careers_url,watch_status,watch_checked_at) ON employers TO jobwatch_collector;
GRANT SELECT ON sources,collection_runs,postings,posting_versions,posting_events,source_health,research_postings TO jobwatch_collector;
GRANT INSERT (id,source_id,mode,request_version,processing_revision,ci_run) ON collection_runs TO jobwatch_collector;
GRANT INSERT ON postings,posting_versions,posting_events TO jobwatch_collector;
GRANT UPDATE (fetched_at,finished_at,status,source_records,vacancies,skipped_non_vacancies,absence_safe,error) ON collection_runs TO jobwatch_collector;
GRANT UPDATE (status,last_seen_at,missing_since,current_version_id,verdict,family,codes,rules_version,
  screening_input_sha256,screening_basis,screened_at) ON postings TO jobwatch_collector;
GRANT UPDATE (last_published_run_id,processing_revision) ON sources TO jobwatch_collector;
GRANT USAGE ON SEQUENCE collection_runs_run_order_seq,posting_events_id_seq TO jobwatch_collector;
GRANT SELECT ON employers,sources,collection_runs,postings,posting_versions,posting_events,
  verifications,leads,triage,applications,owner_audit,source_health,research_postings,opportunity_workflow TO jobwatch_owner;
GRANT INSERT,UPDATE,DELETE ON employers,leads,triage,applications TO jobwatch_owner;
-- recorded_at/actor must use server defaults; a caller cannot move the future-time check's reference clock.
GRANT INSERT (id,posting_key,kind,checked_at,checked_by,url,method,status,wording,value,version_id,
  description_sha256,legacy_details) ON verifications TO jobwatch_owner;
GRANT INSERT (provider,board,region,employer_id,status,validation_url,endpoint_checked_at,mapping_verified_at,
  mapping_verified_by,mapping_evidence,discovered_by,discovered_at) ON sources TO jobwatch_owner;
GRANT UPDATE (employer_id,status,validation_url,endpoint_checked_at,mapping_verified_at,
  mapping_verified_by,mapping_evidence) ON sources TO jobwatch_owner;
GRANT USAGE ON SEQUENCE employers_id_seq,sources_id_seq TO jobwatch_owner;

COMMENT ON TABLE posting_versions IS 'Immutable full parsed native evidence (or labelled legacy snapshot) plus one derived representation; not original HTTP bytes.';
COMMENT ON COLUMN posting_versions.evidence IS 'ASCII JSON bytes, preserving source strings including escaped U+0000. Do not blindly cast NUL-bearing evidence to JSONB.';
COMMENT ON COLUMN posting_versions.description_text IS 'Untrusted source-derived text. NULL means unavailable; empty string means supplied empty. NUL replacement is flagged separately.';
COMMENT ON COLUMN postings.status IS 'listed/not_seen in a qualifying feed observation; not_seen is never verified closure.';
COMMENT ON COLUMN postings.first_seen_at IS 'Our retained observation time, not an employer publication date; interpret with first_seen_basis.';
COMMENT ON COLUMN collection_runs.run_order IS 'Database allocation order before fetch; authoritative stale-publication guard, independent of timestamp ties.';
COMMENT ON COLUMN collection_runs.absence_safe IS 'Protocol completed AND this collection contract may infer absence. False permits positive observations only.';
COMMENT ON COLUMN collection_runs.details IS 'Import health/provenance preserves unavailable counters and original outcome without pretending a new fetch occurred.';
COMMENT ON COLUMN sources.processing_revision IS 'Pinned processing release; changed only with source-locked processing deployment, not by daily scans.';
COMMENT ON TABLE owner_audit IS 'Private committed-change recovery for mutable controls. Actor is a claim; db_login is authenticated session_user. No statement logging.';
COMMENT ON VIEW research_postings IS 'Current source-derived facts and judgments for all verdicts, including excluded. Description text is untrusted external content.';
