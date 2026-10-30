-- Separate deadline checks from the lead's original observation and legacy details.
ALTER TABLE jobwatch.leads
  ADD COLUMN deadline_source_url text,
  ADD COLUMN deadline_official boolean,
  ADD COLUMN deadline_checked_at timestamptz,
  ADD COLUMN deadline_checked_by text,
  ADD CONSTRAINT lead_deadline_provenance CHECK (
    (deadline_official IS NULL AND deadline_source_url IS NULL AND deadline_checked_at IS NULL AND deadline_checked_by IS NULL)
    OR (deadline_official IS NOT NULL AND deadline_source_url IS NOT NULL
        AND jobwatch.url_identity(deadline_source_url,true) IS NOT NULL
        AND deadline_checked_at IS NOT NULL
        AND deadline_checked_by IS NOT NULL AND btrim(deadline_checked_by) <> ''
        AND deadline_date IS NOT NULL AND nullif(btrim(deadline_wording),'') IS NOT NULL));

-- Preserve existing view grants; installations without agent provisioning skip it.
DO $$ BEGIN
  IF to_regclass('jobwatch.agent_leads') IS NOT NULL THEN
    CREATE OR REPLACE VIEW jobwatch.agent_leads AS
SELECT lead_key,kind,company,title,programme_key,cycle_year,cycle_wording,state,
  url,url_exact,official,NULL::text AS source_note,wording,deadline_date,deadline_wording,
  posting_key,checked_at,checked_by,
  jsonb_strip_nulls(jsonb_build_object(
    'window',coalesce(details->>'window',(details->>'legacy_json')::jsonb->>'window'),
    'deadline_source_url',coalesce(details#>>'{deadline,url}',((details->>'legacy_json')::jsonb)#>>'{deadline,url}'),
    'uncertainty',coalesce(details->>'uncertainty',(details->>'legacy_json')::jsonb->>'uncertainty')))
    AS details,deadline_source_url,deadline_official,deadline_checked_at,deadline_checked_by
FROM jobwatch.leads;

  END IF;
END $$;
