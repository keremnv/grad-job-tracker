# AWS deployment preparation — NOT DEPLOYED

This code proposes one Stockholm EC2 host. The existing file-backed production workflow remains authoritative. Nothing here authorizes `terraform apply`, a live ATS request, importing private data to AWS, enabling a timer, or cutover. The baseline application and migrations 0001–0003 are unchanged.

## Resources and storage

`eu-north-1`, explicit Canonical Ubuntu 24.04 LTS **ARM64** AMI, `t4g.small`, Standard CPU credits, IMDSv2, 12 GiB encrypted gp3 OS disk and a **separate 16 GiB encrypted gp3 database volume**. One existing default-VPC public subnet with a direct Internet Gateway route; one public IPv4 for outbound HTTPS, **zero inbound security-group rules**, no SSH key, PostgreSQL loopback/socket only. No NAT, endpoint charges, load balancer, RDS or containers in production.

The instance, data volume and backup bucket have Terraform `prevent_destroy`. EC2 also has API termination protection; the volume requests a final snapshot on deliberately approved destruction; the bucket is versioned and never force-emptied. A routine replacement is intentionally refused. A reviewed replacement requires: freeze writers, verify an independent restore, preserve a current backup, deliberately change the guards, stop PostgreSQL and detach cleanly, attach the same volume in the same AZ, verify it before mounting, and remove old resources only after reconciliation. Never use force-detach, `terraform destroy`, or delete a data volume to fix a failed deployment. Root replacement loses OS/code, not the independent data volume. These guards prevent accidents, not an administrator deliberately removing protection.

The mount is identified by exact EBS volume serial/UUID. Bootstrap formats **only** a signature-free whole volume with an explicit initialization argument. It refuses unexpected filesystems, partitions, a mismatched mount, or nonempty uninitialised PGDATA. PostgreSQL requires the mounted volume before startup. No `nofail` mount, directory fallback or automatic major upgrade. A mount/attachment failure stops bootstrap; inspect it through SSM and rerun the same reviewed bootstrap only after diagnosing it.

## Local tools and read-only planning

Terraform 1.17.0 and AWS provider are locked in `.terraform.lock.hcl`. AWS CLI v2 supports `aws login`; Session Manager plugin is needed only for future sessions. Verify official tool signatures before installation. Profile `grad-jobwatch` uses browser login, **not permanent access keys**. AWS resource Region is `eu-north-1`; the installed Agent Toolkit endpoint remains `us-east-1`.

```sh
aws login --profile grad-jobwatch
terraform -chdir=infra/aws init -backend=false
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
```

Resolve Canonical's public SSM parameter `/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id` with a **read-only** `aws ssm get-parameter`, then inspect `ec2 describe-images`: owner `099720109477`, ARM64, noble 24.04. Pin the reviewed AMI ID rather than silently following the parameter in Terraform. Supply `expected_project_id` and `ami_id` through a private `0600` variable file **outside Git**. `allowed_account_ids` refuses a different project. The project identifier is not a credential, but plans contain infrastructure identifiers and must remain private.

```sh
umask 077
terraform -chdir=infra/aws plan -input=false -var-file=/PRIVATE/aws.tfvars.json -out=/PRIVATE/aws.tfplan
terraform -chdir=infra/aws show -json /PRIVATE/aws.tfplan > /PRIVATE/aws-plan.json
python3 infra/aws/scripts/check_plan.py /PRIVATE/aws-plan.json
```

Planning calls read APIs; no local-exec/provisioners/apply hooks. The initial-plan checker refuses unexpected inventory, mutations of existing resources, inbound ports, unencrypted disks and unbounded CPU credit charges. Terraform mock tests do not contact AWS. `.terraform/`, state, tfvars and saved plans are ignored. **Never commit a plan, state, dump or receipt containing private records.** Local state must be secured and backed up outside this repository after a future apply; use one authorized operator and no concurrent Terraform writes. A remote state backend is deliberately not auto-created.

The IAM skill's official baseline-policy generator can analyze the private plan locally:

```sh
uvx iam-policy-autopilot@latest generate-policies /PRIVATE/aws-plan.json --region eu-north-1 --account PROJECT_ID --pretty > /PRIVATE/deployer-baseline.txt
```

This generates a review baseline, not permission to upload/attach a policy. Review unsupported resources and narrow `iam:PassRole` to the instance role and EC2. Do not give coding agents the project's broad `/managed/` role permanently, and never modify the protected managed roles.

## Host, credentials and deployments (future approved operator steps)

Cloud-init installs PostgreSQL **17** from the signed PGDG noble repository and Python **3.12**, with SSM Agent from its signed Snap package and pinned AWS CLI 2.38.5 from the official signed ARM64 installer (published key fingerprint checked). It creates a dedicated cluster on the data volume and provisions the existing bootstrap SQL only once. No application migration/import or collection happens at boot. OS users `jobwatch-collector`, `jobwatch-owner`, `jobwatch-migrator` and `jobwatch-backup` are non-root, have no login shell, and use local PostgreSQL peer mappings. Existing SQL grants remain authoritative. The backup OS account maps to the migration role because it must read private owner records and grants; it is a trusted maintenance identity, never the collector.

No database password, AWS key or private input is in cloud-init, Terraform/state or the artifact. Loopback TCP accepts only the PostgreSQL owner with SCRAM; its password is initially **unset**. For future SQL tunnels the owner sets it interactively with `psql \password jobwatch_owner` during authorized secret setup. No secret appears in command arguments. Local peer collection needs no production DSN secret.

1. Build a **code-only** artifact from an explicit full commit. The script uses `git archive` over a fixed allowlist; it excludes `data/`, `config/`, reports, Git metadata and working-tree contents. Bundles a wheel and hash-locked ARM64 CPython 3.12 dependencies. A private artifact path must not already exist.

   ```sh
   python3 infra/aws/scripts/build_release.py --repo . --commit FULL_COMMIT --output /PRIVATE/release.tar.gz
   ```

2. After separate upload authorization, an operator uploads this code artifact under `s3://BUCKET/artifacts/COMMIT.tar.gz` with SSE-S3. No private Git deploy key or tracker data on the host is needed for application deployment. Record the independently computed SHA-256. Use the AWS CLI with temporary operator credentials; the instance cannot upload release code.
3. On the host, use the trusted `/opt/jobwatch-bootstrap/deploy_release.py` installed by cloud-init, or the reviewed `fetch_release.sh`, to fetch and verify the archive hash **before executing any archive contents**:

   ```sh
   sudo python3.12 /opt/jobwatch-bootstrap/deploy_release.py /PRIVATE/release.tar.gz --sha256 SHA256 --commit FULL_COMMIT
   ```

   The installer refuses active services or active/enabled timers, unsafe archive members, mismatched hashes/inventory and a conflicting existing release. It builds an offline venv at its permanent release path, installs root-owned code, atomically switches `/opt/jobwatch/current`, installs units and reloads systemd. No database deletion, migration, import, timer enable/start, or collection. An interrupted incomplete release is retained for diagnosis; never overwrite it silently. Repeating the same completed artifact is safe. Updating bootstrap/AMI/user-data proposes a protected instance replacement; application-only updates use artifacts instead.
4. Run `sudo bash /opt/jobwatch/current/infra/aws/scripts/migrate_local.sh` explicitly. It uses the existing `jobwatch-db-migrate` and Unix peer migration identity. Repeated migrations verify checksums; altered or divergent migrations fail. Then perform the separately approved frozen importer/reconciliation procedure, **not** an automatic startup import.
5. Both `jobwatch-collect.timer` and `jobwatch-backup.timer` are installed **disabled**. Confirm with `systemctl is-enabled`/`is-active`. Only the owner go/no-go may enable collection after all cutover gates. Backups can be enabled earlier only after an approved imported baseline and an independent restore. Services retry failed work at most three times per six hours with 15-minute backoff. Stage 2 transactions preserve per-source commits; failure retries are genuinely new scheduled fetch attempts. Daily timer misses are not replayed (`Persistent=false`).

## IAM, SSM and SQL-first agents

The instance role has only SSM registration/channel actions, `GetObject` on **artifacts/**, `PutObject` on **backups/** requiring AES256, and `PutLogEvents` to one pre-created stream. No backup reads/deletes, secret-store reads, IAM, SendCommand, S3 bucket listing, or owner credentials. This matters: any OS process can potentially use IMDS credentials. Even a compromised collector cannot download private backups through that role. SSM administration is privileged host access and can read the database as root; do not confuse it with an ordinary SQL owner login.

Ordinary research agents need only temporary AWS permission for `ssm:StartSession` on the one tagged instance and **AWS-StartPortForwardingSession**, plus their own `ResumeSession`/`TerminateSession` and channel connection permissions per AWS's documented user policy. Do not grant shell documents, Run Command, remote-host forwarding or deployment IAM privileges to a SQL-only agent. Use MFA/short sessions. A separately authorized administrator may use a shell session and sudo. Infrastructure deployment is a third role with reviewed Terraform resource permissions and scoped PassRole, not routine SQL credentials. No such human roles/policies are attached by this preflight.

```sh
# Future authenticated administrative access (separate administrator authorization):
aws ssm start-session --profile grad-jobwatch --region eu-north-1 --target INSTANCE_ID
# SQL-only agent: opens only localhost:15432 on the client, forwarding to host loopback:5432.
aws ssm start-session --profile grad-jobwatch --region eu-north-1 --target INSTANCE_ID \
  --document-name AWS-StartPortForwardingSession --parameters '{"portNumber":["5432"],"localPortNumber":["15432"]}'
psql -X 'host=127.0.0.1 port=15432 dbname=jobwatch user=jobwatch_owner sslmode=disable' -W
```

For psycopg use the same local tunnel/service/passfile. Here `sslmode=disable` covers **local loopback inside the authenticated encrypted SSM channel**, not a public network connection. The database never listens publicly. No public 5432/22 rule is needed. SSM Agent makes outbound HTTPS connections to `ssm`/`ssmmessages`; public subnet + IGW + IPv4 supports this without NAT/VPC endpoints. Keep the agent patched. Session tunnelling does **not** log SQL payloads in Session Manager; database attribution/audit is the existing narrow audit. Close the CLI tunnel/terminate the session, revoke temporary AWS access and rotate/revoke the PostgreSQL owner credential to withdraw access; removing a local tunnel alone does not revoke future access.

## Backups and recovery

`database_backup.py backup` takes an exported **REPEATABLE READ** snapshot, hashes every exact application/audit/ledger row, captures schema/functions/triggers/ownership/grants, and executes a real `pg_dump -Fc --schema=jobwatch --snapshot=...`. Every archived sequence state is extracted from the archive, not inferred from a concurrently advancing sequence. The receipt includes hashes/counts, not notes or source text. Files are `0600` in private directories. No database lock is held while uploading to S3.

The single-PutObject uploader bounds archives to 4 GB, sends AES256 and SHA-256, verifies S3's returned checksum **and version ID**, then uploads the manifest last. No `HeadObject`/backup-read grant is required. A complete manifest is the backup-completion marker. Failed upload/receipt verification is nonzero; partial objects expire. The bucket has public access blocked, ACLs disabled, TLS-only policy, SSE-S3, versioning, 30-day current-object expiration, 30-day noncurrent expiration and one-day incomplete multipart cleanup. Unique daily keys avoid overwrite; noncurrent copies can persist longer during expiry/version cleanup. This is encrypted retention, **not Object Lock or protection against a project administrator deleting it**.

Future local host invocation (peer maintenance account):

```sh
sudo -u jobwatch-backup env JOBWATCH_MIGRATION_DATABASE_URL='host=/run/jobwatch-postgresql dbname=jobwatch user=jobwatch_migrator' \
  /opt/jobwatch/current/venv/bin/python /opt/jobwatch/current/infra/aws/scripts/database_backup.py backup --upload
```

A restore requires a **separate fresh PostgreSQL 17 database** with the same three role names and database-level grants provisioned first; no application schema or public objects. Preserve ordinary ownership and ACLs, no `--clean`, `--no-owner`, `--no-acl`, trigger disabling or reset. Restore uses `pg_restore --exit-on-error --single-transaction`. It then compares exact row hashes, schema/owners/grants/triggers, migration checksums, exact archived sequences and all three views. A failed verification is a non-authoritative recovery target, never something to promote automatically. Run the Stage 3 frozen-source reconciliation and owner/runtime denial tests independently as well.

The CLI permits only the local Unix socket with peer migration identity; select a fresh database via `JOBWATCH_MIGRATION_DATABASE_URL` (for example `dbname=jobwatch_recovery_...`). Download archive + manifest using a separate approved recovery operator's `GetObjectVersion` permission; the instance runtime cannot recover backups itself. Verify S3 version and local SHA-256, then:

```sh
sudo -u jobwatch-migrator env JOBWATCH_MIGRATION_DATABASE_URL='host=/run/jobwatch-postgresql dbname=FRESH_RECOVERY_DB user=jobwatch_migrator' \
  /opt/jobwatch/current/venv/bin/python /opt/jobwatch/current/infra/aws/scripts/database_backup.py restore /PRIVATE/database.dump \
  --manifest /PRIVATE/database.json --confirm-fresh-target
```

Provision a recovery database as postgres with migration role ownership, revoke PUBLIC schema/database CREATE, grant runtime CONNECT and migration CONNECT/CREATE, set role search paths as in bootstrap. Add a **local peer HBA entry for that specific recovery database** deliberately; the live database HBA does not accept arbitrary new databases. No TCP migration permission is introduced. A recovery host must also reproduce/verify OS/HBA configuration; a schema dump deliberately excludes cluster-role passwords and OS files. If AWS is unavailable, an authorized operator must have a separately retained encrypted offline copy and documented role/bootstrap artifacts. **Before cutover**, choose and test this off-project backup custody/retention with the owner; same-project S3 alone does not cover project lockout. Do not commit dumps or embed backup encryption keys.

Keep the current DB before repairs. Stop all writers, take/preserve a current backup, restore separately, reconcile valid later evidence and owner decisions, and only then promote. After the first authoritative PostgreSQL write, restarting the frozen Git writer loses data and is prohibited as an automatic rollback. This implementation does not provide PITR/WAL archiving: daily dumps imply up to 24 hours' data loss unless additional verified backups survive. Owner acceptance of that RPO, manual pre-change backups, real AWS restore proof and an independent copy are cutover gates.

## Monitoring and costs

Basic EC2 metrics, console alarms for host status/low CPU credits/failed operations, 14-day CloudWatch summary logs; no application descriptions/notes/credentials are forwarded. Failed source counts and positive-only/absence-safe counts remain visible; nonzero exit never implies that other source commits rolled back. No notification recipient is created. Configure an approved notification and budget alert before production; neither is a spending cap. Disk and backup freshness must also be checked operationally; basic EC2 metrics do not measure filesystem usage.

See [AWS deployment report](../../docs/AWS_DEPLOYMENT_PREFLIGHT_REPORT.md) for executed checks, costs and untested hosting gates, and [cutover runbook](../../docs/POSTGRESQL_CUTOVER_RUNBOOK.md) for authority/freeze/recovery sequencing.
