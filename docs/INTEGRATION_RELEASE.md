# Agent and calendar integration release — 27 October 2026

**Operational.** Private Proton feeds and the two personal ChatGPT apps run on the
existing AWS host alongside Graduate Job Watch V1.0.0. The `v1.0.0` tag is unchanged;
this release was merged to `main` from `feat/agent-calendar-integrations`.

## Release identifiers

| Item | Identifier |
| --- | --- |
| Deployed application and agent-runtime code | `41f640ba463c6bfb58eef9d63de34b27163dafd2` (both code-only ARM64 artifacts, SHA-256 verified) |
| Owner GO | `54f1d99` (documentation-only difference from the deployed code) |
| Repository fixes after deployment | `b9cfb82` installer guard; `3a7ee0c` tunnel profile path (applied on the host as a systemd drop-in) |
| Integration Terraform root | Separate `integrations/terraform.tfstate`; 14 resources; final plan clean |

The host runs `41f640b` plus two host-only deltas that match the repository: the
`jobwatch-tunnel@` drop-in reading `/etc/jobwatch-tunnel/%i.yaml`, and the root-only
credential-entry helper (`infra/aws/integrations/jobwatch-tunnel-credential`). The
`b9cfb82` installer fix only affects future reinstalls and ships with the next release.

## Verified at closeout, 14:10 UTC

- Five alarms OK, including calendar health (OK since 13:17; healthy hourly runs at
  13:10 and 14:10). The startup ALARM/OK cycle is documented in the
  [deployment report](AGENT_CALENDAR_DEPLOYMENT_REPORT.md).
- Collection timer unchanged (next 06:16 UTC); backups 00:00/12:00 UTC (12:00 run
  verified in S3); fifteen-minute health checks succeeding. No schedule changed apart
  from adding the hourly calendar timer and owner refresh path.
- Read and owner MCP servers and both tunnels active without restarts. All five
  Research tools passed harmless native probes. Owner Actions exposes only
  `prepare_owner_change`/`commit_owner_change`, and was probed with an invalid recipe.
- The synthetic ChatGPT gate passed on a disposable database. Approve in ChatGPT's
  approval box produced exactly one audited write, and Cancel produced none. No
  production write was made during deployment or closeout.

## Not confirmed by the agent (owner-side)

- Delivery of the calendar-health and operation-failure ALARM/OK emails.
- The Proton subscriptions themselves and an observed refresh (normally 4–16 hours).
- That the OpenAI Platform shows no tunnel charge. This was assumed from the owner's
  instruction to proceed and the earlier synthetic milestone.
- Removal of superseded ChatGPT apps and of any unused runtime keys.
- Actual AWS charges. The estimate is about US$0.42–0.43/month plus usage, and
  CloudFront allowance eligibility is not verified.

## Known limitations

- Both tunnels currently share one runtime key, so revoking it disconnects both.
  Separate keys restore independent revocation (`jobwatch-tunnel-credential owner --rotate`).
- Pending owner reviews are in memory; restarting the owner server discards them.
- Capability URLs are bearer secrets; previously downloaded calendar copies cannot be revoked.
