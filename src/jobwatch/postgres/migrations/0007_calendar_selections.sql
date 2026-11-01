-- Owner-approved calendar membership on top of the file policy. Holds keys, never
-- dates or free text: published dates always derive from retained deadline evidence.
-- approved_deadline only records what the review displayed; it is not a calendar input.
CREATE TABLE jobwatch.calendar_selections(
  posting_key text REFERENCES jobwatch.postings(posting_key),
  lead_key text REFERENCES jobwatch.leads(lead_key),
  evidence_key text GENERATED ALWAYS AS (coalesce(posting_key,lead_key)) STORED PRIMARY KEY,
  mode text NOT NULL CHECK (mode IN ('include','exclude')),
  approved_deadline text,
  decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  recorded_by text NOT NULL CHECK (btrim(recorded_by)<>''),
  CHECK (num_nonnulls(posting_key,lead_key)=1),
  CHECK (mode='include' OR approved_deadline IS NULL));
CREATE TRIGGER audit_owner AFTER INSERT OR UPDATE OR DELETE ON jobwatch.calendar_selections
  FOR EACH ROW EXECUTE FUNCTION jobwatch.audit_owner_change();
GRANT SELECT ON jobwatch.calendar_selections TO jobwatch_owner;
GRANT INSERT (posting_key,lead_key,mode,approved_deadline,recorded_by),
  UPDATE (mode,approved_deadline,decided_at,recorded_by) ON jobwatch.calendar_selections TO jobwatch_owner;
