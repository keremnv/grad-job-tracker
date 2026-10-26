# Existing-host V1 maintenance — qualified, production NO-GO

26 October 2026, `feat/v1-release-readiness`, starting at
`57e633c66b2dbe030837349a80e586966881c25c`. This records separately authorised
maintenance of the existing Stockholm ARM64 host. Git remains authoritative.
No production import, ATS request, credential creation, scheduled job, Terraform
operation, infrastructure replacement or cutover occurred.

## Before and after

| Check | Before | Verified after maintenance |
|---|---|---|
| Application | `1b72d0d76f1ed15b56859bc891af9953458f85cd` | `43aab3b12e3277c96b3b0936839a6758ebda986b` |
| PostgreSQL | 17.11, `17.11-1.pgdg24.04+2` | Same version and postmaster start time; no restart |
| Runtime | ARM64 Ubuntu 24.04, Python 3.12.3, psycopg 3.2.13 | Same; imports passed for all four OS service identities |
| Extensions | plpgsql; vector unavailable | `public.vector` 0.8.7, owned by postgres |
| Migration ledger | Unchanged 0001–0003 | Only 0004 applied; repeat invocation applied nothing |
| Intended database | Eleven domain/audit tables empty | All twelve domain/audit/embedding tables empty; four ledger rows |
| SQL identities | Migrator, collector, owner only | Same three roles, flags, memberships and existing grants; no optional logins |
| Authentication/audit | Restricted Unix peer/loopback; five audit triggers | Configuration files byte-identical; original grant checks and triggers unchanged |
| Timers/services | Collection/backup disabled and inactive | Both timers still disabled/inactive; both operation services inactive |
| Backup timer definition | Daily, disabled | Six-hour V1 definition installed, **not enabled** |
| Infrastructure | Twenty protected Terraform resources | Same resource IDs and state object version/bytes; no lock or Terraform invocation |

The exact encrypted data volume, mount UUID and PGDATA match the baseline; no
root-storage fallback. EC2 termination protection, independent data-volume
preservation, IMDSv2, Standard CPU credits, zero inbound rules and instance IAM
policies are unchanged. Final free space: about 7.81 GiB root / 14.68 GiB data;
about 1.36 GiB available memory. EmbeddingGemma inference remains off this host.

## Recovery before installation

The installed format-1 implementation created one new **92,731-byte** PostgreSQL
custom archive of the empty intended database. The backup OS identity uploaded
archive first and completion manifest last to new versioned keys. Recovery-operator
retrieval selected the exact archive and manifest versions, verified AES256,
S3 SHA-256 receipts, local hashes and manifest-to-archive linkage. No existing
version was overwritten or deleted.

Pinned format-1 source from `1b72d0d`, its recovery bundle and PostgreSQL/runtime
requirements were retained privately; the old installed release was preserved.
The downloaded archive restored using that code into independent, network-isolated
local PostgreSQL 17.11. Exact rows, schema, ownership/grants/security and sequence
states matched; a populated-target retry was refused. Restore/verification phase
was approximately 0.57 seconds, **not an operational disaster-recovery RTO**.

## Release and extension verification

Installed code-only artifact SHA-256:

```text
f08b1911157428329485c27a94bf91388407444cc1d3c1fdcea208e3291ec31e
```

The existing installer verified the explicit commit, manifest inventory and every
file hash before installation. Upload used the existing private artifacts prefix,
AES256, a confirmed checksum/version and no overwrite. The installed archive digest
and all manifest files match. First and repeated installation passed; root-owned
code remains readable/executable by service identities and non-root write is denied.
Eight console-command help checks passed. The release contains base PostgreSQL
runtime dependencies, not MCP/model packages or weights; no model was downloaded.

Used the existing signed PGDG **noble/ARM64** repository, supported by the
[PostgreSQL Ubuntu instructions](https://www.postgresql.org/download/linux/ubuntu/)
and [PGDG documentation](https://wiki.postgresql.org/wiki/Apt). Verified the installed
repository key against the official HTTPS key, its full fingerprint, the cached
InRelease signature, package-index SHA-256 and the downloaded package SHA-256:

```text
package: postgresql-17-pgvector
version: 0.8.7-1.pgdg24.04+1
architecture: arm64
SHA-256: b40d2a8c94035a069169748a033238a2b347b37a81de1dbf9cc44d9e07b09ab3
```

The simulated dependency plan required exactly one new package, zero upgrades and
zero removals. Its maintainer scripts were absent. The existing PostgreSQL dictionary
trigger was inspected: its triggered branch updates dictionaries and exits before
service-management code. Installation used only the verified local deb; package
inventory comparison confirmed that one addition, package verification passed and
`vector.so` is native ARM64. No service restart or unrelated package upgrade occurred.

Early verification helpers encountered an absent default GPG home and an uncompressed
rather than lz4 apt index. Private inspection commands were corrected to use an
isolated verification home and the actual index; no installed software was repaired
or bypassed. The conservative pre-install trigger check paused for review before
installation. Native application/schema/recovery tests subsequently passed.

The administrator executed the reviewed `vector_extension.sql` through stdin;
`migrate_local.sh` then used the existing peer-authenticated migration OS/SQL
identity. Only the following ledger entry was added:

```text
0004_embeddings.sql
97a4688a6b8cffc4053c91f906e0d5001a73bbe1a10802198ee772d50c68c1d2
```

All four checksums match packaged/Git SQL. Original table grants stayed unchanged;
0004 added the reviewed derived table and owner SELECT grant. No cluster bootstrap,
optional worker/read-role provisioning, password, HBA or IAM change was performed.

## Actual native and recovery tests

Separate, named synthetic source/recovery databases were created within the existing
cluster and retained. Intended `jobwatch` was never populated with fixtures.
Qualification used existing postgres Unix-peer access followed by **SET SESSION
AUTHORIZATION** to each existing restricted SQL role; dump/restore utilities used
`--role=jobwatch_migrator`. This tests effective grants and attribution without new
credentials or HBA entries; it does not qualify a new remote owner/read login.

Executed on the actual ARM64 host:

- Fresh four-migration setup and repeat idempotence in the synthetic source.
- Actual `write_chunks` and SQL retrieval with explicitly synthetic 768-dimensional
  unit vectors/profile; no claim of model-generated embeddings or inference.
- Exact cosine distances, hybrid/BM25/FTS, excluded postings, missing coverage,
  historical retrieval and rejection of stale vectors as current evidence.
- Derived-input replay, vector constraints, native NUL retention and evidence/history
  inspection. Two postings, three immutable versions, one material event and three
  derived chunks were retained in the synthetic source.
- Owner application/triage writes, audit actor/login attribution, rollback, weekly
  report privacy and calendar projection. One application, one triage record and
  five audit rows; zero collection runs.
- Fourteen refused owner/collector operations, including evidence modification,
  audit deletion, embedding deletion, schema creation and collector private reads.

The new format-2 implementation produced a **100,517-byte** synthetic custom archive
with conservative `backup_started_at`, extension requirements and exact fingerprints.
The backup identity uploaded it with manifest-last semantics; an actual synthetic
backup read by that identity was denied. No destructive IAM test was called and
instance policies remained unchanged. Operator retrieval verified exact S3 versions,
AES256, both checksums and archive-version linkage.

The same verified archive bytes restored into the fresh hosted recovery database.
Rows/evidence, owner records/audit, schema, owners/grants, migrations, sequences and
views matched exactly; vector/hybrid queries and reports worked. Nine restored-role
write/private-read denials passed. Corrupt archives and populated targets were
refused. This hosted restore used a local copy whose hashes matched the independently
retrieved S3 objects; no backup-read permission was added to the instance.

An additional restore used the **downloaded S3 archive**, pinned V1 recovery source
and an independent PostgreSQL 17.11 / vector 0.8.7 container with `--network none` and
no published ports. Exact rows/schema/security/sequences matched without accessing
EC2; populated-target refusal passed. Restore/verification phase was about 0.43 seconds,
excluding provisioning and operator response. The real completion manifest/archive
also passed `backup_freshness.py` as fresh, explicitly **production_ready=false**.
These are synthetic tests, not an imported production-data restore or durable custody.

The complete offline/local suites recorded in the release checklist were not rerun
as hosted tests. No application, migration, infrastructure or test code changed in
this maintenance stage; repository changes are status documentation only.

## Stop state and remaining gates

Private commands, receipts, manifests, dumps, code bundles and detailed checks remain
outside Git. Synthetic databases and old backup versions were retained. No secrets,
private owner data or infrastructure identifiers are reproduced here.

No new infrastructure or recurring service was added. A roughly 5.14 MB code artifact,
two small archives and manifests add minor S3 request/storage/transfer usage; no fresh
monthly bill was established. The historical £17.53/month scenario is not a current
cost guarantee; external custody/alerts and the old Aurora snapshot remain separate.

**Production remains NO-GO.** The six-hour timer is installed but not activated;
12-hour recovery protection, independent encrypted custody/owner keys, freshness and
missing-run monitoring, delivered notifications and all-writer response are unqualified.
Nine live endpoint/mapping checks, historical-evidence acceptance, final Git freeze/C
selection, real import/reconciliation/independent restore, secure production agent
access, Proton hosting and explicit single-writer approval remain open. No collector,
backup service, GitHub Actions writer, main branch or ChatGPT tunnel was activated or
changed. AWS infrastructure configuration, Terraform state, protections and the
Aurora snapshot are unchanged; S3 writes were limited to the authorised code artifact
and two new backup/manifest pairs.

If later recovery is needed, preserve the current database and stop writers under
separate approval. Restore into a **new target**, never over the intended database:
format 1 requires the retained pre-vector bundle and a fresh target without vector;
format 2 requires the pinned V1 bundle, matching roles/database ACLs/settings and
administrator-provisioned public.vector 0.8.7 with matching ownership. Verify exact
fingerprints before considering promotion. Do not unapply migration 0004, restart
stale Git as a rollback after authoritative SQL writes, or replace EC2 to fix software.
