-- Operational context only. No new authority, domain tables or observation writes.
SET LOCAL search_path=jobwatch,pg_catalog;
CREATE OR REPLACE VIEW source_health AS
SELECT s.id AS source_id,s.provider,s.board,s.region,s.status,
  ok.fetched_at AS last_success_at,ok.vacancies,ok.absence_safe,
  last_try.started_at AS last_attempt_at,last_try.status AS last_attempt_status,last_try.error,
  (ok.fetched_at IS NULL OR ok.fetched_at < now()-interval '3 days') AS stale,
  s.last_published_run_id,ok.mode AS last_success_mode,
  last_try.id AS last_attempt_run_id,last_try.finished_at AS last_attempt_finished_at,
  last_try.fetched_at AS last_attempt_fetched_at,last_try.absence_safe AS last_attempt_absence_safe,
  s.endpoint_checked_at,s.mapping_verified_at,s.processing_revision
FROM sources s
LEFT JOIN collection_runs ok ON ok.id=s.last_published_run_id
LEFT JOIN LATERAL (SELECT r.* FROM collection_runs r WHERE r.source_id=s.id AND r.mode='publish'
  ORDER BY r.run_order DESC LIMIT 1) last_try ON true;

-- Keep the existing column order; SELECT p.* was expanded before migration 0002.
CREATE OR REPLACE VIEW research_postings AS
SELECT p.posting_key,p.source_id,p.provider,p.board,p.native_id,p.status,p.first_seen_at,p.first_seen_basis,
  p.last_seen_at,p.missing_since,p.current_version_id,p.verdict,p.family,p.codes,p.rules_version,
  p.screening_input_sha256,p.screening_basis,p.screened_at,
  v.company,v.title,v.posting_url,v.location_text,v.country,v.countries,
  v.department,v.team,v.employment_type,v.salary,v.source_dates,v.deadlines,
  v.description_text,v.normalizer_version,v.evidence_sha256,v.search_tsv,v.extra_fields,
  h.status AS source_status,h.stale,h.last_attempt_status,
  p.last_seen_run_id,h.last_success_at,h.absence_safe,h.last_published_run_id,h.last_success_mode,
  CASE WHEN p.status='not_seen' THEN
    CASE WHEN (h.last_success_mode='publish' AND h.absence_safe) OR EXISTS(SELECT 1 FROM posting_events e JOIN collection_runs r ON r.id=e.run_id
      WHERE e.posting_key=p.posting_key AND e.category='observation' AND e.kind='not_seen'
        AND e.observed_at=p.missing_since AND r.status='published' AND r.absence_safe) THEN 'not_seen_absence_safe' ELSE 'legacy_not_seen_unproven' END
    WHEN h.last_success_mode='import' THEN 'legacy_snapshot'
    WHEN p.last_seen_run_id=h.last_published_run_id THEN 'seen_in_latest_published_run'
    WHEN h.absence_safe=false AND h.last_success_mode='publish' THEN 'retained_after_positive_only_run'
    WHEN h.last_published_run_id IS NULL THEN 'observation_unknown'
    ELSE 'inconsistent_presence' END AS coverage_state,
  v.origin,v.nul_replaced,v.published_at
FROM postings p JOIN posting_versions v ON (v.posting_key,v.id)=(p.posting_key,p.current_version_id)
JOIN source_health h ON h.source_id=p.source_id;

-- Reuse the existing workflow's application/triage precedence and exact-URL join.
-- No private notes/CV labels are exposed in this convenience view.
CREATE VIEW operational_postings AS
SELECT r.*,w.decision,w.revisit_on,w.application_job_key,w.stage,w.action_state,
  CASE WHEN a.posting_key IS NOT NULL THEN 'explicit_posting_key'
       WHEN a.job_key IS NOT NULL THEN 'unambiguous_exact_url' END AS application_link_basis,
  (w.decision='dismissed' AND w.action_state IN ('applied','preparing')) IS TRUE AS triage_application_conflict,
  EXISTS(SELECT 1 FROM postings p2 JOIN posting_versions v2 ON v2.id=p2.current_version_id
    WHERE p2.posting_key<>r.posting_key AND v2.url_exact=v.url_exact) AS ambiguous_posting_url,
  EXISTS(SELECT 1 FROM applications a2 WHERE a2.posting_key IS NULL AND a2.url_loose=url_identity(v.posting_url,false)
    AND (w.application_job_key IS NULL OR a2.job_key<>w.application_job_key)) AS possible_application_match,
  coalesce(check_status.status,'unknown') AS verified_application_status,
  check_status.checked_at AS application_checked_at,check_status.checked_by AS application_checked_by,
  check_status.url AS application_check_url
FROM research_postings r JOIN opportunity_workflow w USING(posting_key)
JOIN posting_versions v ON v.id=r.current_version_id
LEFT JOIN applications a ON a.job_key=w.application_job_key
LEFT JOIN LATERAL(SELECT status,checked_at,checked_by,url FROM verifications
  WHERE posting_key=r.posting_key AND kind='application_status'
  ORDER BY checked_at DESC,recorded_at DESC,id DESC LIMIT 1) check_status ON true;
GRANT SELECT ON operational_postings TO jobwatch_owner;
COMMENT ON VIEW operational_postings IS 'Private SQL attention context; judgments, owner actions and dated independent checks remain distinct. Corpus retention is not filtered.';
