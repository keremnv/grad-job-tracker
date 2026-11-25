# Graduate Job Watch — Final Production Rollout & Maintenance Transition Report

**Date:** 25 November 2026  
**Status:** **Planned development phase COMPLETE — transitioned to operational maintenance mode.**  
**Deployed production commit:** `1365c22e7687983c827b06731b5108187b0ebb5e` (app release & agent runtime)  
**Host / Region:** `i-0a21a9da641e3f22e` (`t4g.small`, ARM64, 2 GiB RAM, `eu-north-1`), PostgreSQL `17.11` + `pgvector 0.8.7`, schema migration `0014`.

---

## 1. Production Deployment Evidence

All four approved rollout components were deployed via the guarded release workflow (`deploy_release.py` + `install_agent_runtime.py` + `migrate_local.sh` + `agent_evidence_roles.sql`) after taking a pre-deploy streaming S3 backup:

| Artifact / Step | Verified Evidence |
|---|---|
| **Pre-deploy S3 backup** | `backups/2026/11/25/70f12b693cb242aab8ffae1c6c134e9d.dump` (`version_id: cthh2ypIwtzUcFJoME_0qIYtUIHPXYbA`, `sha256: 48c66f6fe648083d6ab81c26bacc2537cb151a29f59de857dcd571b8f21a3733`) |
| **App release (`app.tar.gz`)** | Commit `1365c22e7687983c827b06731b5108187b0ebb5e`, `sha256: 8b2db34c473799b0a381ed189c5345e40595704621b11abbef96808535084271`, symlink `/opt/jobwatch/current -> /opt/jobwatch/releases/1365c22e7687983c827b06731b5108187b0ebb5e` |
| **Agent runtime (`agent.tar.gz`)** | Commit `1365c22e7687983c827b06731b5108187b0ebb5e`, `sha256: fc7c6fa3e5a75500bc8e3e13c719d17d4f86a831bbdcb2f0c50a13d59bbf7ff5`, `model_inference: false`, symlink `/opt/jobwatch-agent-runtime -> /opt/jobwatch-agent/releases/1365c22e7687983c827b06731b5108187b0ebb5e/venv` |
| **Schema migration `0014`** | Applied cleanly (`14/14` migrations): adds `discovered_boards.last_etag` + `last_304_at`, upgrades `discovery_candidates.search_tsv` (PG 17 `SET EXPRESSION` over complete `description_text`), adds `discovery_candidates.search_sym_tsv` + GIN index (`400 kB`), seeds `jobwatch_agent_evidence` budget (`20/hr`, `60/day`). |
| **Systemd timers & services** | **9/9 timers active** (`jobwatch-collect`, `jobwatch-backup`, `jobwatch-discover-probe` at `03:30, 11:30, 19:30 UTC`, `jobwatch-discover-refresh`, `jobwatch-auto-activate`, `jobwatch-corroborator` at `*:05,35:00 UTC`, `jobwatch-calendar`, `jobwatch-backup-health`, `jobwatch-mcp-health`); **3/3 MCP services active** (`jobwatch-agent-read` 50.0 MiB, `jobwatch-agent-owner` 38.9 MiB, `jobwatch-agent-evidence` 44.9 MiB); **`0` failed units**. |
| **CloudWatch alarms** | **6/6 `OK`**: `grad-jobwatch-backup-health`, `grad-jobwatch-calendar-health`, `grad-jobwatch-cpu-credit-balance`, `grad-jobwatch-host-status`, `grad-jobwatch-mcp-health`, `grad-jobwatch-operation-failure`. |
| **Isolation & DB privileges** | Kernel `nftables` table `inet jobwatch_corroborator_egress` (UID 984) verified live (blocks `169.254.169.254:80` and `127.0.0.1:8765`); `jobwatch-tunnel-evidence` denied PostgreSQL socket (`EACCES`, errno 13); `jobwatch_agent_evidence` has **0** raw table write grants (strictly `SECURITY DEFINER` only); `jobwatch_corroborator` has **only** `INSERT` on `agent_evidence_corroborations`; `jobwatch-agent-evidence.service` enforces `RestrictAddressFamilies=AF_UNIX`. |

---

## 2. Exact ChatGPT Evidence MCP Connection & Revocation Instructions

The production Evidence MCP listener (`jobwatch-agent-evidence.service`, `/run/jobwatch-agent-evidence/mcp.sock`), dedicated tunnel OS user (`jobwatch-tunnel-evidence`, UID 983, NOT in `jobwatch-db`), and `/etc/jobwatch-tunnel/evidence.yaml` (`0640 root:jobwatch-tunnel-evidence`) are provisioned and live-verified.

To connect **"Graduate Job Watch Evidence"** to ChatGPT (requires personal OpenAI Platform tunnel creation):

1. In OpenAI Platform **Tunnels**, create a dedicated tunnel (separate from Research and Owner tunnels) and obtain its **Tunnel ID** (`tun_...`) and a **`Tunnels Read + Use`** API key.
2. On `i-0a21a9da641e3f22e` via SSM:
   ```bash
   sudo sed -i 's/REPLACE_WITH_EVIDENCE_INGESTION_TUNNEL_ID/<YOUR_TUNNEL_ID>/' /etc/jobwatch-tunnel/evidence.yaml
   sudo install -o root -g root -m 0600 /dev/null /etc/jobwatch/tunnel-evidence.env
   # Put CONTROL_PLANE_API_KEY=<YOUR_TUNNELS_KEY> inside /etc/jobwatch/tunnel-evidence.env
   sudo systemctl enable --now jobwatch-tunnel@evidence.service
   ```
3. In ChatGPT (**Settings → Apps & Connectors**), add the new tunnel connector. Verify it exposes **only** `submit_public_observation` and `submit_public_vacancy`, both requiring explicit write confirmation (`readOnlyHint=false`).
4. **Instant revocation:** `sudo systemctl disable --now jobwatch-tunnel@evidence.service` (leaves Research MCP, collection, discovery, corroboration, and backups completely unaffected).

Full runbook in [docs/MAINTENANCE_AND_RECOVERY_GUIDE.md](MAINTENANCE_AND_RECOVERY_GUIDE.md).

---

## 3. End-to-End Qualification Results

### Automated Regression & 10-Step Qualification Suite (Disposable PG 17.11)
- **Offline unit tests (`tests/`):** `325/325` passed.
- **AWS installer & Unix-socket isolation tests (`infra/aws/tests/`):** `12/12` passed.
- **Disposable PostgreSQL 17 integration suite (`tests/postgres/`):** `222/222` passed (2 optional local-model tests skipped), including the new 10-step qualification test [`tests/postgres/test_final_e2e_qualification_pg.py`](../tests/postgres/test_final_e2e_qualification_pg.py):

| Step | Verified Behaviour | Result |
|---|---|---|
| 1. Automatic discovery | Backfill probes unprobed board, verifies official identity, retains complete description + snapshot; HTTP `304` revisit preserves state with 0 bytes; advisory lock blocks overlaps; auto-transitions to steady state (`20`/window, `0.40` exploration) when unprobed `< 500` & `< 5%`. | **PASS** |
| 2. Research search of discovery evidence | `ReadTools(shared=True)` (`jobwatch_agent_readonly`) finds retained candidate via `search(query='C++ Systems', method='fts_symbol', scope='discovery'|'all')` with `evidence_completeness='complete'`. | **PASS** |
| 3. Bounded automatic activation & deduplication | Verified board stages, validates (`absence_safe=true`), activates, collects native postings, links candidate (`linked_posting_key`), and `search(scope='all')` omits the linked candidate from `discovery_items` (zero duplicates). | **PASS** |
| 4–5. Missing vacancy & deadline via Evidence MCP | Official MCP SDK over Unix socket submits missing vacancy (`unscreened_agent_submission`) and deadline wording; confirms ONLY 2 tools (`readOnlyHint=False`), idempotency replay, and SSRF refusal. | **PASS** |
| 6. Recurring corroborator & automatic admission | Advisory lock prevents overlap; transient `timeout` respects 6h backoff then retries once to `claim_corroborated`; terminal `403` is never retried; deterministic `admit_corroborated_vacancies()` screens corroborated vacancy without owner approval or owner-table writes. | **PASS** |
| 7–8. Research inspection & deadline recommendation | Research MCP inspects corroborated vacancy (`corroboration_status='claim_corroborated'`, `target_binding_status='bound_exact_url'`) and corroborated deadline in `posting_deadline_claims_distinct`. | **PASS** |
| 9. Explicit owner approval → calendar | Before approval: `0` published calendar events under explicit-only policy. After explicit `calendar_selections` insert: publishes `1` event (`DTSTART;VALUE=DATE:20261210`, `SEQUENCE:0`). | **PASS** |
| 10. Automatic calendar update on deadline change | Superseding official deadline (`2026-12-20`) automatically updates the **same stable UID** to `DTSTART;VALUE=DATE:20261220` with `SEQUENCE:1`. | **PASS** |

### Live Production Verification (Non-Mutating on Owner Records)
- **Live Evidence MCP Unix-socket probe (`/run/jobwatch-agent-evidence/mcp.sock`):** Confirmed tool allowlist = `{submit_public_observation, submit_public_vacancy}`, both `readOnlyHint: false`, `idempotentHint: true`; IMDS SSRF probe (`https://169.254.169.254/latest/meta-data/`) rejected with `ssrf_refused`.
- **Live Research MCP multi-scope `search()` probes (`/run/jobwatch-agent-read/mcp.sock`):**
  - `search(query='C++', method='fts_symbol', scope='all', limit=10)`: **`162.9 ms`**, `10` native postings, `7` unlinked discovery candidates, `0` overlap, explicit `cross_corpus_note`.
  - `search(query='Wayve', method='fts_symbol', scope='discovery', limit=10)`: **`48.5 ms`**, `9` discovery candidates.
  - `search(query='Anima', method='fts_symbol', scope='all', limit=10)`: **`103.5 ms`**, `10` native postings, `1` agent observation, `0` overlap.
- **Live `jobwatch-corroborator.service` run:** `Result=success`, `ExecMainStatus=0` under kernel `nftables` UID 984 jail.

---

## 4. Discovery Throughput & Resource Measurements

Measured from the first live production 100-board accelerated window (`jobwatch-discover-probe.service`, `2026-11-25 21:55:10 UTC`):

| Metric | Measured Value | Configured Cap / Headroom |
|---|---|---|
| Boards probed | **`100`** (`90` exploration, `10` priority; `40` Greenhouse, `30` Ashby, `30` Lever) | `100` / window (`300` / day) |
| Unique newly probed boards | **`100`** | — |
| Unique UK-relevant employers found | **`3`** (`1` `verified_official_link`, `2` `hosted_link_only`, `3` new complete candidates) | `verify-limit: 15` |
| HTTP requests / 429 rate limits | **`117` requests**, **`0` HTTP 429s** | `220` requests |
| Transfer volume | **`26.72 MiB`** (`26,719,048` bytes) | `32 MiB` |
| Wall-clock / CPU time | **`63 s`** wall (`2.658 s` CPU time) | `20 min` timeout, `30%` CPUQuota |
| ETag headers captured | **`98 / 100` (`98%`)** (`40/40` Greenhouse, `30/30` Ashby, `28/30` Lever; 2 missing were `404 dead`) | Enables `HTTP 304` on steady-state revisits |
| Unprobed backlog remaining | **`13,522 / 13,726`** unlinked directory boards | Auto steady-state at `< 500` & `< 5%` (`20`/window = `60`/day) |
| **Projected backfill completion** | **~44–49 days** at `270–300` newly probed boards/day (~6.5 weeks vs ~340 days previously) | Automatic transition, zero manual cutover |
| Host RAM / Disk post-window | **`1,012 MiB` available / `1,834 MiB`** (`55%` free); Disk **`5.7G / 11G`** (`54%` used) | DB size `234 MB`; new GIN index `400 kB` |

---

## 5. Final Coverage & Evidence-Quality Metrics

Snapshot from production `jobwatch` after the first accelerated window (owner tables untouched):

| Metric | Production Count |
|---|---|
| Monitored ATS sources (`status='monitored'`) | **`28`** (0 stale, 0 failures) |
| Listed native postings / total historical postings | **`2,482`** listed / **`2,655`** total |
| Immutable native `posting_versions` | **`6,898`** |
| Directory boards (`discovered_boards`) | **`13,749`** total (`206` probed, `13,522` unprobed, `98` cached ETags) |
| Discovered UK-relevant boards / verified official links | **`13`** UK-relevant / **`4`** `verified_official_link` |
| Discovery candidates (`discovery_candidates`) | **`75`** total — **`75 / 75` (`100%`) `complete`**, `3` linked to native postings |
| Immutable `discovery_candidate_snapshots` | **`103`** |
| Agent evidence observations / corroborations | **`2`** observations / **`3`** corroborations |
| Explicit `calendar_selections` | **`0`** (unchanged; zero auto-inclusion) |

---

## 6. Backup & Off-Host Restore Verification

- **Post-rollout streaming S3 backup:**
  - Archive: `s3://grad-jobwatch-backups-151e93c664eb68196d6dcdbe4f/backups/2026/11/25/afa889b2fb204a7aa7bf650e6373ae9e.dump` (`version_id: OBcRmLEXBU_KVp_wkTzY.PcwLP6wUy75`, `sha256: 1f1f581763d0bd06bc09d2333e7607a7639239eee718989ffc7dcfcf6bea9bab`)
  - Manifest: `s3://grad-jobwatch-backups-151e93c664eb68196d6dcdbe4f/backups/2026/11/25/afa889b2fb204a7aa7bf650e6373ae9e.json` (`version_id: DAc1xqQk6zxEKZDBIhK7F_ABGxCQ375n`, `sha256: 064c5fd77b007e27b48bc3b677f8332d72bf3a7cae9c7f17bfd16414a82914a8`)
- **Off-host `--network none` PostgreSQL 17.11 (`pgvector 0.8.7`) restore:**
  - Both archive and manifest SHA-256 checksums verified prior to restore.
  - Restored cleanly with `pg_restore --no-owner --role=jobwatch_migrator --exit-on-error --single-transaction`.
  - **Exact match across all `20 / 20` tables, all `5 / 5` sequences, `14 / 14` migrations, and `9 / 9` `jobwatch_*` roles.**
  - Verified restored `discovery_candidates.search_sym_tsv` GIN index directly (`9` hits for `wayve`). Temporary local download and container destroyed immediately after verification.

---

## 7. Remaining Known Limitations

### Operational Blockers
- **None (`0` blockers).** All stopping criteria are satisfied.

### Acceptable / Intentionally Deferred Operational Characteristics
1. **Owner OpenAI Platform tunnel binding for Evidence MCP:** Host listener (`jobwatch-agent-evidence.service`), OS user (`jobwatch-tunnel-evidence`), and profile template (`/etc/jobwatch-tunnel/evidence.yaml`) are live and verified; binding the personal OpenAI Platform tunnel ID + `Tunnels Read + Use` key is an intentional owner-only credential step (Section 2).
2. **Initial discovery backfill duration (~6.5 weeks):** 13,522 unprobed boards remain and will drain automatically at `300`/day across 3 windows before transitioning automatically to `60`/day steady state (`< 500` and `< 5%`).
3. **JS-rendered career portals & anti-bot WAFs:** Pages requiring full headless-browser execution or returning `403`/CAPTCHA cannot be independently corroborated by the static HTTPS corroborator and are honestly recorded as `js_shell_or_empty` or `blocked_or_403` (never faked or bypassed).
4. **Deferred adapters & host-side inference:** Unattended Workable-wide polling stays disabled; SmartRecruiters and new ATS adapters are deferred until justified by usage; embedding inference stays off the 2 GiB database host.
5. **External Proton fetch verification:** Calendar publication verifies local snapshot integrity and HTTPS serving; it cannot independently prove Proton Calendar's external polling interval.

---

## 8. Maintenance & Recovery Guide

See [docs/MAINTENANCE_AND_RECOVERY_GUIDE.md](MAINTENANCE_AND_RECOVERY_GUIDE.md) for scheduled units, ChatGPT Evidence MCP connection & revocation commands, routine checks, guarded deployment/rollback, disaster recovery, and incident playbooks.

---

## 9. Final Commit Identifiers & Maintenance Transition Declaration

- **Production deployed code & runtime commit:** `1365c22e7687983c827b06731b5108187b0ebb5e` (superseding `62f740219ea78fd56cbb8a908d6d16f33d6b4c34`; includes rollout commit `622a55fa06e199ec496b6606d3cddc3fcf3e41e4` + systemd `OnCalendar` fix `1365c22e7687983c827b06731b5108187b0ebb5e`).
- **Repository HEAD (with final handover & maintenance docs):** recorded on `origin/main` in the documentation commit immediately following `1365c22e7687983c827b06731b5108187b0ebb5e` (no runtime code changes).

**Declaration:** All acceptance and stopping criteria for the final planned rollout are satisfied. The planned feature-development phase of Graduate Job Watch is **complete**, and the system has **transitioned to operational maintenance mode**. Future changes should be limited to routine operational maintenance, defect fixes, or capabilities justified by observed production usage.
