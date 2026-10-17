# P5 notification design — proposal only

The owner subsequently authorised repository setup and activation of the existing
GitHub Actions workflow and its per-run summary (see `HOSTING.md`). No additional
notification destination has been selected; no email/issue delivery was added.
Scans and local reports work without choosing an additional channel.

| Choice | Benefit | Privacy/operational trade-off | Current state |
|---|---|---|---|
| Manually viewed Markdown | No destination, credentials or notifications needed | Owner must open the reports | Available locally |
| Existing GitHub Actions summary | Per-run public-vacancy report and visible failures in private repo | Requires private hosting setup; schedule is best effort, owner GitHub settings control workflow alerts | Active in the private repository; first manual run verified, first cron run still unobserved |
| Opt-in private GitHub issue | A place to review selected changes | External write, private-repo visibility, issue spam risk | Proposal; requires explicit owner choice |
| Opt-in email | Can deliver deadlines and failures outside GitHub | Recipient preference, credentials, private content exposure, retries/delivery monitoring | Proposal; requires explicit owner choice and setup |

Any later approved sender should use a persisted delivery ledger keyed by
provider/board/source ID, material change type and evidence fingerprint. Delivery
state must remain separate from vacancy observations and manual application
stages. Repeated observations must not resend unchanged jobs. Failed deliveries
must not be recorded as delivered; bounded retries must respect provider limits.
Deadlines need their original timezone and verification date. Outages and stale
observations must be distinguished from removed listings. Do not send owner
notes or personal application state by default.

No email, issue, webhook, LLM, paid service or ChatGPT scheduled task integration
was added. A GitHub schedule and any ChatGPT task remain separate systems.

Next: the owner may choose a channel in a later task. Until then, use the local
reports. Delivery implementation and tests remain deferred.
