-- SQL cookbook, NOT a migration or an automatically executed script.
-- Use normal psql variables/psycopg parameters; copy only the desired statement.
-- Public research (jobwatch_owner or jobwatch_collector): exclusions are retained.
SELECT status,verdict,count(*) FROM jobwatch.research_postings GROUP BY status,verdict;
SELECT posting_key,title,verdict,status,coverage_state,last_seen_at,last_success_at,absence_safe
FROM jobwatch.research_postings ORDER BY posting_key;
SELECT * FROM jobwatch.source_health ORDER BY provider,board;

-- Owner attention. The view owns joins/precedence; this WHERE is a selection.
SELECT posting_key,title,verdict,action_state,application_link_basis,
       triage_application_conflict,ambiguous_posting_url,possible_application_match,
       verified_application_status,application_checked_at,coverage_state
FROM jobwatch.operational_postings
WHERE status='listed' AND verdict IN ('priority','review')
  AND action_state IN ('unreviewed','revisit_due','interested','preparing');
-- Recent observations are ours, not employer publication times. Backfill is separate.
SELECT posting_key,title,first_seen_at,verdict FROM jobwatch.research_postings
WHERE first_seen_basis='observed' AND first_seen_at>=now()-interval '7 days';

-- Material history. Legacy categories remain honest import annotations.
SELECT id,posting_key,observed_at,recorded_at,category,kind,
       changes->>'legacy_category' AS legacy_category,before_version_id,after_version_id,
       comparison_version,actor,changes
FROM jobwatch.posting_events WHERE posting_key=:'posting_key' ORDER BY id;
-- Current or historical representation, no word reconstruction or false current flag.
SELECT v.id,v.origin,v.normalizer_version,v.observed_at,v.evidence_sha256,
       v.description_text,v.nul_replaced,v.provenance,v.id=p.current_version_id AS is_current
FROM jobwatch.posting_versions v JOIN jobwatch.postings p USING(posting_key)
WHERE p.posting_key=:'posting_key' ORDER BY v.observed_at,v.id;
-- Exact parsed source values are bytea. Python: json.loads(bytes(row['evidence'])).
-- JSONB cannot represent decoded NUL; CASE protects the exceptional payload.
SELECT id,CASE WHEN position('\u0000' IN convert_from(evidence,'UTF8'))=0
  THEN convert_from(evidence,'UTF8')::jsonb ELSE NULL END AS inspectable_native,
  encode(evidence,'hex') AS bytes_if_needed
FROM jobwatch.posting_versions WHERE id=:'version_id'::uuid;

-- Native English FTS, current LISTED records, ALL verdicts. GIN is on versions.
-- English FTS folds C++ and C# to C; use the Python BM25 option for that distinction.
SELECT p.posting_key,v.title,p.verdict,ts_rank_cd(v.search_tsv,q.query) AS rank,
       h.stale,p.last_seen_at,h.last_success_at
FROM jobwatch.postings p JOIN jobwatch.posting_versions v ON v.id=p.current_version_id
JOIN jobwatch.source_health h ON h.source_id=p.source_id,
     websearch_to_tsquery('english',:'query') AS q(query)
WHERE p.status='listed' AND v.search_tsv @@ q.query ORDER BY rank DESC,p.posting_key;

-- Independent dated checks, not proof of current form openness or eligibility.
SELECT posting_key,kind,checked_at,checked_by,url,wording,value,status,version_id,description_sha256
FROM jobwatch.verifications ORDER BY posting_key,kind,checked_at DESC;
-- Feed-observed deadline claims remain separate.
SELECT posting_key,deadlines FROM jobwatch.research_postings WHERE deadlines<>'[]'::jsonb;
SELECT lead_key,kind,company,title,cycle_year,cycle_wording,state,official,wording,
       deadline_date,deadline_wording,checked_at,url FROM jobwatch.leads ORDER BY company,programme_key,cycle_year;
SELECT e.slug,e.name,e.careers_url,e.watch_status,e.watch_checked_at,s.provider,s.board,s.region,s.status
FROM jobwatch.employers e LEFT JOIN jobwatch.sources s ON s.employer_id=e.id ORDER BY e.name,s.id;
SELECT job_key,posting_key,company,role,stage,date_applied,next_action,next_action_date FROM jobwatch.applications;
SELECT posting_key,decision,reason_code,revisit_on,decided_at,recorded_by FROM jobwatch.triage;

-- Owner activation equivalent to the convenience helper. Requires a REAL successful
-- latest same-endpoint validation plus separately established mapping evidence.
-- Source lock also serializes trusted validation finalizers. Owner needs no UPDATE on runs.
BEGIN;
SELECT set_config('jobwatch.actor',:'actor',true);
SELECT id FROM jobwatch.sources WHERE id=:'source_id'::bigint FOR NO KEY UPDATE;
UPDATE jobwatch.sources s SET endpoint_checked_at=r.fetched_at,status='monitored'
FROM jobwatch.collection_runs r
WHERE s.id=:'source_id'::bigint AND r.id=:'check_run'::uuid AND r.source_id=s.id
  AND r.mode='validate' AND r.status='validated' AND r.fetched_at IS NOT NULL
  AND r.details->>'purpose'='endpoint_validation' AND r.details->>'evidence_kind'='live'
  AND r.details->'source'=jsonb_build_object('provider',s.provider,'board',s.board,'region',s.region)
  AND (s.endpoint_checked_at IS NULL OR r.fetched_at>=s.endpoint_checked_at)
  AND r.id=(SELECT id FROM jobwatch.collection_runs WHERE source_id=s.id AND mode='validate' ORDER BY run_order DESC LIMIT 1)
RETURNING s.id,s.provider,s.board,s.status,s.endpoint_checked_at;
-- Require exactly one returned row; otherwise roll back and inspect the facts.
-- Missing mapping facts cause the existing monitored-status CHECK to fail atomically.
COMMIT;

-- Ordinary owner writes: use a transaction-local actor, constraints and audit.
-- Preserve a manual application PK when deliberately linking it later.
BEGIN;
SELECT set_config('jobwatch.actor',:'actor',true);
UPDATE jobwatch.applications SET posting_key=:'posting_key' WHERE job_key=:'manual_job_key';
COMMIT;
-- Independently checked claims can be INSERTed into verifications through normal SQL.
-- Supply actual checked_at/checked_by/source wording and a stable UUID on retry;
-- never invent a check for unknown status, parse a prose date, or infer an application.
