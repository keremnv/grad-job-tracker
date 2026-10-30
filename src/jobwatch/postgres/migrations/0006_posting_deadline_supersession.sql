-- Append-only verification edges. NULL marks legacy checks; [] marks a new check.
ALTER TABLE jobwatch.verifications
  ADD COLUMN deadline_supersedes jsonb,
  ADD COLUMN deadline_reason text,
  ADD CONSTRAINT deadline_supersession_shape CHECK (
    (deadline_supersedes IS NULL AND deadline_reason IS NULL)
    OR (deadline_supersedes IS NOT NULL AND kind='deadline' AND jsonb_typeof(deadline_supersedes)='array'
        AND jsonb_array_length(deadline_supersedes)<=100
        AND nullif(btrim(deadline_reason),'') IS NOT NULL));

-- A feed claim is its exact retained payload INCLUDING observed_at, not merely a
-- date or current version. Unchanged evidence copied to another version keeps its
-- identity; a later observation with a different timestamp gets a different ID.
CREATE VIEW jobwatch.posting_deadline_claims AS
WITH claims AS (
  SELECT v.posting_key,'verification:'||v.id::text AS claim_id,
    'independent_verification'::text AS provenance,v.url,v.wording,v.value,
    v.checked_at::text AS checked_at,v.checked_by,true AS structured
  FROM jobwatch.verifications v WHERE v.kind='deadline'
  UNION ALL
  SELECT DISTINCT p.posting_key,
    'feed:'||encode(sha256(convert_to(p.posting_key||E'\n'||d::text||E'\n'||coalesce(d->>'url',pv.posting_url),'UTF8')),'hex'),
    'feed_observation'::text AS provenance,coalesce(d->>'url',pv.posting_url),d->>'wording',d->>'value',
    d->>'observed_at',NULL::text,d->>'source_field'='application_deadline'
  FROM jobwatch.research_postings p JOIN jobwatch.posting_versions pv USING(posting_key)
    CROSS JOIN LATERAL jsonb_array_elements(pv.deadlines) d
)
SELECT c.*,coalesce((SELECT jsonb_agg('verification:'||v.id::text ORDER BY v.id)
  FROM jobwatch.verifications v WHERE v.posting_key=c.posting_key AND v.kind='deadline'
    AND v.deadline_supersedes ? c.claim_id),'[]'::jsonb) AS superseded_by
FROM claims c;

CREATE VIEW jobwatch.posting_deadline_state AS
SELECT p.posting_key,p.current_version_id,
  coalesce((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.claim_id)
    FROM jobwatch.posting_deadline_claims c WHERE c.posting_key=p.posting_key),'[]'::jsonb) AS claims
FROM jobwatch.postings p;
GRANT SELECT ON jobwatch.posting_deadline_claims,jobwatch.posting_deadline_state TO jobwatch_owner;

-- All deadline inserts serialize with the cookbook, including ordinary owner SQL.
-- This trigger also enforces same-posting, existing, active targets for SQL callers.
CREATE FUNCTION jobwatch.check_deadline_supersession() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('jobwatch.deadline:'||NEW.posting_key,0));
  IF NEW.deadline_supersedes IS NOT NULL THEN
    IF jsonb_typeof(NEW.deadline_supersedes)<>'array'
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.deadline_supersedes) x
          WHERE jsonb_typeof(x)<>'string')
       OR (SELECT count(*) FROM jsonb_array_elements(NEW.deadline_supersedes)) <>
          (SELECT count(DISTINCT x) FROM jsonb_array_elements(NEW.deadline_supersedes) x)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(NEW.deadline_supersedes) x
          WHERE NOT EXISTS (SELECT 1 FROM jobwatch.posting_deadline_claims c
            WHERE c.posting_key=NEW.posting_key AND c.claim_id=x AND c.superseded_by='[]'::jsonb)) THEN
      RAISE EXCEPTION 'Supersession requires distinct active claims on this posting' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION jobwatch.check_deadline_supersession() FROM PUBLIC;
CREATE TRIGGER check_deadline_supersession BEFORE INSERT ON jobwatch.verifications
  FOR EACH ROW WHEN (NEW.kind='deadline') EXECUTE FUNCTION jobwatch.check_deadline_supersession();
CREATE TRIGGER audit_deadline_check AFTER INSERT ON jobwatch.verifications
  FOR EACH ROW WHEN (NEW.deadline_supersedes IS NOT NULL)
  EXECUTE FUNCTION jobwatch.audit_owner_change();
