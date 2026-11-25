# Graduate Job Watch — Maintenance & Recovery Guide

**Operational status:** Production maintenance mode (planned feature-development phase complete as of 25 November 2026).  
**Production host:** `i-0a21a9da641e3f22e` (`t4g.small`, ARM64, 2 GiB RAM, `eu-north-1`).  
**Authoritative store:** Local PostgreSQL 17.11 (`pgvector 0.8.7`), database `jobwatch`, schema migration `0014`.  
**S3 backup & release bucket:** `grad-jobwatch-backups-151e93c664eb68196d6dcdbe4f` (`eu-north-1`, versioned).

---

## 1. Scheduled Production Units (9 Active Timers & 3 MCP Services)

| Unit | Schedule / Mode | Role / Purpose |
|---|---|---|
| `jobwatch-collect.timer` | Daily `06:15 UTC` (`±5m`) | Monitored ATS collection + post-collection `admit_corroborated_vacancies()` |
| `jobwatch-discover-probe.timer` | 3× daily `03:30, 11:30, 19:30 UTC` (`±5m`) | Bounded ATS discovery (`100` boards/window backfill; auto-reduces to `20`/window when unprobed `< 500` and `< 5%`) |
| `jobwatch-discover-refresh.timer` | Weekly Sunday `04:00 UTC` | Directory refresh (`greenhouse`, `ashby`, `lever`) |
| `jobwatch-auto-activate.timer` | Daily `04:50 UTC` | Bounded auto-stage → validate → activate (`cap: 2/run`, `60` boards, `12,000` postings) |
| `jobwatch-corroborator.timer` | Every 30 min (`*:05,35:00 UTC`, `±2m`) | Isolated `nftables`-jailed (UID 984) independent public evidence corroboration (`limit=10`, `30` req, `5 MiB`, 1 transient retry after 6h) |
| `jobwatch-calendar.timer` + `.path` | Hourly + on owner refresh hint | Explicit-selection ICS snapshot publisher (`calendar_selections` only) |
| `jobwatch-backup.timer` | Twice daily `00:00, 12:00 UTC` | Streaming `pg_dump -Fc` + manifest + SHA-256 upload to S3 |
| `jobwatch-backup-health.timer` | Every 15 min | Backup freshness & receipt check → CloudWatch |
| `jobwatch-mcp-health.timer` | Every 15 min | Research MCP socket & tunnel health check → CloudWatch |
| `jobwatch-agent-read.service` | Always-on (`AF_UNIX`) | Research MCP (`/run/jobwatch-agent-read/mcp.sock`, `jobwatch_agent_readonly`) |
| `jobwatch-agent-owner.service` | Always-on (`AF_UNIX`) | Owner Actions MCP (`/run/jobwatch-agent-owner/mcp.sock`, `jobwatch_agent_owner`) |
| `jobwatch-agent-evidence.service` | Always-on (`AF_UNIX` only, zero IP) | Evidence Ingestion MCP (`/run/jobwatch-agent-evidence/mcp.sock`, `jobwatch_agent_evidence`) |
| `jobwatch-tunnel@read.service` | Always-on (enabled) | OpenAI Secure Tunnel for Research MCP |
| `jobwatch-tunnel@owner.service` | Inactive/disabled by default | Enable only when intentionally connecting Owner Actions MCP |
| `jobwatch-tunnel@evidence.service` | Inactive/disabled until tunnel ID/key set | OpenAI Secure Tunnel for Evidence MCP (see Section 2) |

---

## 2. Connecting & Revoking Remote Evidence MCP in ChatGPT

The host-side Evidence MCP service (`jobwatch-agent-evidence.service`), dedicated OS user (`jobwatch-tunnel-evidence`, NOT in `jobwatch-db`), and profile template (`/etc/jobwatch-tunnel/evidence.yaml`) are already installed and live.

### One-time owner connection procedure

1. **Create a dedicated tunnel in OpenAI Platform** (separate from Research and Owner tunnels):
   - Open your OpenAI Platform **Tunnels** console and create a new tunnel named e.g. `grad-jobwatch-evidence`.
   - Record the generated **Tunnel ID** (`tun_...`) and generate a scoped API key with **`Tunnels Read + Use`** permission only.
2. **Configure the tunnel ID and root-only credential on EC2 via SSM** (never paste keys into Git, YAML, or chat transcripts):
   ```bash
   # 1. Replace placeholder tunnel_id in /etc/jobwatch-tunnel/evidence.yaml
   sudo sed -i 's/REPLACE_WITH_EVIDENCE_INGESTION_TUNNEL_ID/<YOUR_TUNNEL_ID>/' /etc/jobwatch-tunnel/evidence.yaml
   sudo chown root:jobwatch-tunnel-evidence /etc/jobwatch-tunnel/evidence.yaml
   sudo chmod 0640 /etc/jobwatch-tunnel/evidence.yaml

   # 2. Store CONTROL_PLANE_API_KEY in root-only 0600 env file (never printed)
   sudo install -o root -g root -m 0600 /dev/null /etc/jobwatch/tunnel-evidence.env
   # Write: CONTROL_PLANE_API_KEY=<YOUR_TUNNELS_KEY>
   sudo nano /etc/jobwatch/tunnel-evidence.env

   # 3. Enable and start the independently revocable Evidence tunnel
   sudo systemctl enable --now jobwatch-tunnel@evidence.service
   sudo systemctl status jobwatch-tunnel@evidence.service --no-pager
   ```
3. **Add the connector in ChatGPT:**
   - Settings → Apps & Connectors → Add MCP Server → select your `grad-jobwatch-evidence` tunnel.
   - Verify ChatGPT discovers **only** two tools: `submit_public_observation` and `submit_public_vacancy`, both flagged as write actions requiring explicit confirmation (`readOnlyHint=false`). Never disable or bypass confirmation prompts.

### Instant revocation (zero impact on Research MCP or collection)

```bash
# Immediate host-side cutoff:
sudo systemctl disable --now jobwatch-tunnel@evidence.service
# Optional: also stop the local Evidence MCP socket listener:
sudo systemctl disable --now jobwatch-agent-evidence.service
# Optional: rotate/revoke the tunnel key in OpenAI Platform and shred /etc/jobwatch/tunnel-evidence.env
```

---

## 3. Routine Health Checks (Monthly or On Alert)

```bash
# 1. Host units, timers, memory, disk, and nftables egress jail
systemctl --failed --no-pager
systemctl list-timers --all 'jobwatch*' --no-pager
free -m && df -h /
nft list table inet jobwatch_corroborator_egress

# 2. Backup receipt & CloudWatch alarms
cat /var/lib/jobwatch-backup/latest.json
aws cloudwatch describe-alarms --profile grad-jobwatch --region eu-north-1 \
  --query 'MetricAlarms[*].[AlarmName,StateValue]' --output table

# 3. Discovery backfill progress & steady-state transition check
su -s /bin/sh postgres -c "psql -X -h /run/jobwatch-postgresql -d jobwatch -c \"
SELECT count(*) AS total,
       count(*) FILTER (WHERE source_id IS NULL AND last_probed_at IS NULL) AS unprobed,
       count(*) FILTER (WHERE last_etag IS NOT NULL AND last_etag <> '') AS with_etag,
       count(*) FILTER (WHERE probe_status = 'active_uk_relevant') AS uk_relevant
FROM jobwatch.discovered_boards;\""
```

---

## 4. Guarded Release & Rollback Procedure

- **Never** edit files under `/opt/jobwatch/current` or `/opt/jobwatch-agent-runtime` in place; all releases are immutable commit-pinned directories under `/opt/jobwatch/releases/<commit>` and `/opt/jobwatch-agent/releases/<commit>`.
- **Before any future maintenance release:**
  1. Trigger a fresh pre-deploy backup: `systemctl start jobwatch-backup.service` and confirm `/var/lib/jobwatch-backup/latest.json`.
  2. Pause all timers and MCP/tunnel units before invoking `deploy_release.py` or `install_agent_runtime.py`.
  3. Apply migrations via `bash /opt/jobwatch/current/infra/aws/scripts/migrate_local.sh` (PostgreSQL schema changes are forward-only; never restart retired Git writers as rollback).
  4. Re-enable timers and services, run `systemctl start jobwatch-mcp-health.service`, and verify `systemctl --failed` is empty.
- **Code rollback (non-schema change):** Pause timers/integrations, repoint `/opt/jobwatch/current` (and `/opt/jobwatch-agent-runtime`) to the previous verified release directory under `/opt/jobwatch/releases/<prev_commit>`, run `systemctl daemon-reload`, and restart units.

---

## 5. Disaster Recovery (Off-Host Restore)

1. Identify the latest confirmed archive & manifest in `/var/lib/jobwatch-backup/latest.json` or S3 prefix `s3://grad-jobwatch-backups-151e93c664eb68196d6dcdbe4f/backups/`.
2. Download both `.dump` and `.json` using explicit S3 `--version-id` and verify both SHA-256 digests before touching any database.
3. Restore into a **separate** PostgreSQL 17 (`pgvector 0.8.7`) database:
   - Create database `jobwatch` owned by `postgres` and `CREATE EXTENSION vector VERSION '0.8.7' SCHEMA public;`.
   - Apply `sql/postgresql/bootstrap.sql`, `sql/postgresql/embedding_access.sql`, and create the five narrow runtime roles (`jobwatch_agent_readonly`, `jobwatch_agent_owner`, `jobwatch_calendar_reader`, `jobwatch_agent_evidence`, `jobwatch_corroborator`).
   - Run `pg_restore --no-owner --role=jobwatch_migrator --exit-on-error --single-transaction -U jobwatch_migrator -d jobwatch <backup.dump>`.
4. Reconcile all 20 table counts (`fingerprint.tables`), all 5 sequence values (`sequences`), and `schema_migrations` (`14`) against the manifest before promoting any replacement host. Never overwrite a live authoritative database in place.

---

## 6. Incident Quick Playbook

| Symptom / Alarm | First Action |
|---|---|
| `grad-jobwatch-backup-health` ALARM | Check `journalctl -u jobwatch-backup.service -n 40` and `cat /var/lib/jobwatch-backup/latest.json`; verify S3 connectivity and disk space (`df -h /`). |
| `grad-jobwatch-mcp-health` ALARM | Check `systemctl status jobwatch-agent-read.service jobwatch-tunnel@read.service`; if transient after restart, run `systemctl start jobwatch-mcp-health.service`. |
| `grad-jobwatch-operation-failure` ALARM | Inspect `journalctl -u jobwatch-collect.service -u jobwatch-discover-probe.service -u jobwatch-auto-stage.service -n 50`. Remember single-board HTTP failures are isolated per source and do not corrupt other boards. |
| `grad-jobwatch-cpu-credit-balance` ALARM | Temporarily stop `jobwatch-discover-probe.timer` (`systemctl disable --now jobwatch-discover-probe.timer`) until CPU credits recover; core collection and MCP reads continue unaffected. |
| Unexpected `AccessDenied` on AWS APIs | Check project spend limit status in **AWS Settings → Billing** (`settings.aws.com`). |
| Discovery rate-limiting (`rate_limited_429`) | Automatic halt-on-429 protects providers; check `journalctl -u jobwatch-discover-probe.service`. If persistent for one provider, increase `--delay` in `run_operation.py`. |
| Unwanted agent submissions | Revoke `jobwatch-tunnel@evidence.service` immediately (Section 2). Uncorroborated submissions never enter monitored feeds, deadlines, or calendars. |

---

## 7. Explicitly Deferred / Out of Scope in Maintenance Mode

- **Unattended Workable-wide polling:** Kept disabled (terms/rate-limit discipline).
- **SmartRecruiters or new ATS adapters:** Deferred unless demonstrated usage justifies an adapter.
- **Persistent LLM / subjective suitability scoring:** Explicitly forbidden by `docs/ARCHITECTURE_PRINCIPLES.md`.
- **Host-side embedding inference:** Remains disabled on the 2 GiB database host.
