#!/usr/bin/env bash
# Invoked by cloud-init only after a separately approved apply. No secrets here.
set -euo pipefail
umask 027
[[ ${EUID} == 0 && $# == 2 && $1 =~ ^vol-[0-9a-f]+$ && $2 == --initialize-empty-volume ]] || { echo 'Root, volume ID and explicit initialization flag required' >&2; exit 2; }
# Establish administrative access before waiting on or initializing database storage.
# The Canonical AMI normally includes this Snap; install it if absent.
export DEBIAN_FRONTEND=noninteractive
if ! command -v snap >/dev/null; then apt-get update -qq; apt-get install -y snapd; fi
snap list amazon-ssm-agent >/dev/null 2>&1 || snap install amazon-ssm-agent --classic
snap start --enable amazon-ssm-agent
volume_serial=${1//-/}
data_mount=/var/lib/jobwatch-postgresql
# Nitro device names are not stable. Match the exact EBS serial, never /dev/nvme1n1.
device=
for _attempt in {1..180}; do
  while read -r candidate serial; do
    if [[ ${serial//-/} == "$volume_serial" ]]; then device=$candidate; break; fi
  done < <(lsblk -dnpo NAME,SERIAL)
  [[ -n $device ]] && break
  sleep 2
done
[[ -b $device ]] || { echo 'Expected database volume not attached' >&2; exit 1; }
[[ $(lsblk -npo NAME "$device" | wc -l) == 1 ]] || { echo 'Partitioned data device requires operator review' >&2; exit 1; }
if findmnt -rn -S "$device" >/dev/null; then
  [[ $(findmnt -rn -S "$device" -o TARGET) == "$data_mount" ]] || { echo 'Data device already mounted elsewhere' >&2; exit 1; }
fi
fs_type=$(blkid -s TYPE -o value "$device" || true)
if [[ -z $fs_type ]]; then
  # A signature-free, never-mounted whole device only. Never reformat existing data.
  [[ -z $(wipefs --no-act --noheadings "$device") ]] || { echo 'Unrecognized disk signature' >&2; exit 1; }
  mkfs.ext4 -q -L jobwatch-pg "$device"
elif [[ $fs_type != ext4 ]]; then
  echo 'Unsupported existing filesystem; refusing to format' >&2; exit 1
fi
mkdir -p "$data_mount"
mountpoint -q "$data_mount" || [[ -z $(ls -A "$data_mount") ]] || { echo 'Refusing to hide existing database files under mount' >&2; exit 1; }
uuid=$(blkid -s UUID -o value "$device")
if ! awk -v p="$data_mount" '$2==p {found=1} END {exit !found}' /etc/fstab; then
  printf 'UUID=%s %s ext4 defaults 0 2\n' "$uuid" "$data_mount" >> /etc/fstab
fi
mountpoint -q "$data_mount" || mount "$data_mount"
[[ $(findmnt -nr -o UUID --target "$data_mount") == "$uuid" ]] || { echo 'Wrong mounted database volume' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl gnupg postgresql-common python3.12 python3.12-venv unzip snapd
# shellcheck disable=SC1091
[[ $(. /etc/os-release; echo "$VERSION_CODENAME") == noble && $(dpkg --print-architecture) == arm64 ]] || { echo 'Ubuntu 24.04 ARM64 required' >&2; exit 1; }
install -d /usr/share/postgresql-common/pgdg
curl --fail --silent --show-error --proto '=https' --tlsv1.2 https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
# A signed PGDG repository, explicit major. Ubuntu's default postgresql is not 17.
cat > /etc/apt/sources.list.d/pgdg.sources <<'PGDG'
Types: deb
URIs: https://apt.postgresql.org/pub/repos/apt
Suites: noble-pgdg
Components: main
Architectures: arm64
Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
PGDG
printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf
apt-get update -qq
apt-get install -y --no-install-recommends postgresql-17 postgresql-client-17
getent group jobwatch-db >/dev/null || groupadd --system jobwatch-db
for account in collector owner migrator backup; do
  id "jobwatch-$account" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin --gid jobwatch-db "jobwatch-$account"
done
usermod -a -G jobwatch-db postgres
install -d -o postgres -g postgres -m 0700 "$data_mount/pgdata"
if [[ -f $data_mount/pgdata/PG_VERSION ]]; then
  [[ $(cat "$data_mount/pgdata/PG_VERSION") == 17 ]] || { echo 'Wrong PostgreSQL major; no automatic upgrade' >&2; exit 1; }
else
  [[ -z $(ls -A "$data_mount/pgdata") ]] || { echo 'Nonempty uninitialized PGDATA; refusing to reset' >&2; exit 1; }
  runuser -u postgres -- /usr/lib/postgresql/17/bin/initdb -D "$data_mount/pgdata" --encoding=UTF8 --locale=C.UTF-8 --auth-local=peer --auth-host=scram-sha-256 >/dev/null
fi
install -d -o root -g jobwatch-db -m 0750 /etc/jobwatch
cat > /etc/jobwatch/postgresql.conf <<'PGCONF'
data_directory = '/var/lib/jobwatch-postgresql/pgdata'
hba_file = '/etc/jobwatch/pg_hba.conf'
ident_file = '/etc/jobwatch/pg_ident.conf'
listen_addresses = '127.0.0.1'
port = 5432
unix_socket_directories = '/run/jobwatch-postgresql'
unix_socket_group = 'jobwatch-db'
unix_socket_permissions = 0770
password_encryption = 'scram-sha-256'
shared_buffers = '128MB'
work_mem = '4MB'
maintenance_work_mem = '64MB'
max_connections = 20
max_wal_size = '512MB'
logging_collector = off
log_statement = 'none'
log_min_error_statement = 'panic'
log_error_verbosity = terse
PGCONF
cat > /etc/jobwatch/pg_hba.conf <<'HBA'
local all postgres peer
local jobwatch jobwatch_collector peer map=jobwatch
local jobwatch jobwatch_owner peer map=jobwatch
local jobwatch jobwatch_migrator peer map=jobwatch
# No collector/migrator TCP access; a future owner password must be set interactively.
host jobwatch jobwatch_owner 127.0.0.1/32 scram-sha-256
host all all 0.0.0.0/0 reject
host all all ::/0 reject
HBA
cat > /etc/jobwatch/pg_ident.conf <<'IDENT'
jobwatch jobwatch-collector jobwatch_collector
jobwatch jobwatch-owner jobwatch_owner
jobwatch jobwatch-migrator jobwatch_migrator
jobwatch jobwatch-backup jobwatch_migrator
IDENT
chmod 0640 /etc/jobwatch/{postgresql.conf,pg_hba.conf,pg_ident.conf}
cat > /etc/systemd/system/jobwatch-postgresql.service <<'UNIT'
[Unit]
Description=Job Watch PostgreSQL 17 on durable EBS
After=local-fs.target
RequiresMountsFor=/var/lib/jobwatch-postgresql
[Service]
Type=notify
User=postgres
Group=jobwatch-db
RuntimeDirectory=jobwatch-postgresql
RuntimeDirectoryMode=0750
ExecStartPre=/usr/bin/mountpoint -q /var/lib/jobwatch-postgresql
ExecStart=/usr/lib/postgresql/17/bin/postgres -D /var/lib/jobwatch-postgresql/pgdata -c config_file=/etc/jobwatch/postgresql.conf
ExecReload=/bin/kill -HUP $MAINPID
KillSignal=SIGINT
TimeoutStopSec=120
Restart=on-failure
RestartSec=10
OOMScoreAdjust=-500
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/jobwatch-postgresql /run/jobwatch-postgresql
[Install]
WantedBy=multi-user.target
UNIT
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\nRuntimeMaxUse=50M\nMaxRetentionSec=14day\n' > /etc/systemd/journald.conf.d/jobwatch.conf
systemctl restart systemd-journald
systemctl daemon-reload
systemctl enable --now jobwatch-postgresql.service
psql_admin() { runuser -u postgres -- psql -X -h /run/jobwatch-postgresql -v ON_ERROR_STOP=1 "$@"; }
# Database/roles are initialized once, never dropped or silently reprovisioned.
if [[ $(psql_admin -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname='jobwatch'") == 0 ]]; then
  runuser -u postgres -- createdb -h /run/jobwatch-postgresql jobwatch
fi
roles=$(psql_admin -d jobwatch -Atc "SELECT count(*) FROM pg_roles WHERE rolname IN ('jobwatch_migrator','jobwatch_collector','jobwatch_owner')")
if [[ $roles == 0 ]]; then
  psql_admin -d jobwatch -f /opt/jobwatch-bootstrap/bootstrap.sql >/dev/null
elif [[ $roles != 3 ]]; then
  echo 'Partial role provisioning requires operator investigation' >&2; exit 1
fi
[[ $(psql_admin -d jobwatch -Atc "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'jobwatch_%' AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)") == 0 ]] || { echo 'Unexpected elevated database role' >&2; exit 1; }
# Pinned official AWS CLI, verified against AWS's published signing key.
# Unlike Snap's CLI launcher, this works inside NoNewPrivileges services.
cli_version=2.38.5
if ! command -v aws >/dev/null || ! aws --version | grep -q "aws-cli/$cli_version "; then
  cli_temp=$(mktemp -d)
  install -d -m 0700 "$cli_temp/gpg"
  curl --fail --silent --show-error --proto '=https' --tlsv1.2 "https://awscli.amazonaws.com/awscli-exe-linux-aarch64-$cli_version.zip" -o "$cli_temp/aws.zip"
  curl --fail --silent --show-error --proto '=https' --tlsv1.2 "https://awscli.amazonaws.com/awscli-exe-linux-aarch64-$cli_version.zip.sig" -o "$cli_temp/aws.zip.sig"
  gpg --homedir "$cli_temp/gpg" --import /opt/jobwatch-bootstrap/aws-cli-signing-key.asc
  gpg --homedir "$cli_temp/gpg" --with-colons --fingerprint | grep -q 'FB5DB77FD5C118B80511ADA8A6310ACC4672475C'
  gpg --homedir "$cli_temp/gpg" --verify "$cli_temp/aws.zip.sig" "$cli_temp/aws.zip"
  unzip -q "$cli_temp/aws.zip" -d "$cli_temp"
  if [[ -d /usr/local/aws-cli ]]; then
    "$cli_temp/aws/install" --update
  else
    "$cli_temp/aws/install"
  fi
  rm -rf "$cli_temp"
fi
# No application code, migration, import or collection starts in cloud-init.
echo 'Host ready; deploy a verified code artifact, migrate explicitly, and leave timers disabled.'
