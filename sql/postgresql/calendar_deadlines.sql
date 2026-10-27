-- Read-only selection policy. All verdicts by default; no shortlist/application required.
-- This selects retained evidence keys, never dates or eligibility judgments.
-- A separately approved operator may replace this query to narrow sources/status,
-- join owner state, or choose programme cycles. Safety/provenance checks still run.
SELECT posting_key AS evidence_key FROM jobwatch.research_postings
UNION ALL
SELECT lead_key AS evidence_key FROM jobwatch.agent_leads
