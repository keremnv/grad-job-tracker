# Reduced official response fixtures

Captured during the read-only API audit on 17 October 2026 UTC:

- `greenhouse.json`: [Monzo public Job Board list](https://boards-api.greenhouse.io/v1/boards/monzo/jobs?content=true).
- `lever.json`: [Palantir public postings list](https://api.lever.co/v0/postings/palantir?mode=json&skip=0&limit=100).
- `ashby.json`: [incident.io public posting list](https://api.ashbyhq.com/posting-api/job-board/incident?includeCompensation=true).

One actual record per board is retained to exercise nesting, dates, IDs and
provider URLs. Descriptions and duplicate narrative fields were omitted to keep
fixtures small; Greenhouse meta.total is reduced to the retained list length.
Ashby apiVersion is included using its documented version. Fixtures are shapes,
not current availability evidence. Tests synthesize additional malformed records,
secondary locations, deadlines and pagination; those are not alleged vacancies.

Do not replace these with applicant data or live network tests. Verify the
current official response and documentation before adding/enabling another feed.
