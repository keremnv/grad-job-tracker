#!/usr/bin/env bash
# Local, disposable Ubuntu compatibility probe. No AWS API or credentials passed in.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
image=ubuntu@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
docker run --rm -i --network=host -v "$repo/infra/aws/aws-cli-signing-key.asc:/aws-cli-signing-key.asc:ro" --label jobwatch.test=aws-host --entrypoint bash "$image" -se <<'PROBE'
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl postgresql-common python3.12 python3.12-venv unzip gnupg
printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf
mkdir -p /usr/share/postgresql-common/pgdg
curl --fail --silent --show-error --proto '=https' https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
cat > /etc/apt/sources.list.d/pgdg.sources <<'PGDG'
Types: deb
URIs: https://apt.postgresql.org/pub/repos/apt
Suites: noble-pgdg
Components: main
Architectures: amd64 arm64
Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
PGDG
sed -i '/^Types: deb/a Architectures: amd64' /etc/apt/sources.list.d/ubuntu.sources
dpkg --add-architecture arm64
apt-get update -qq
apt-get install -y --no-install-recommends postgresql-17 postgresql-client-17
# Verify signed repository actually publishes the target ARM64 server/client packages.
apt-cache show postgresql-17:arm64 | grep -q '^Architecture: arm64$'
apt-cache show postgresql-client-17:arm64 | grep -q '^Architecture: arm64$'
# Test native package execution locally; this is not an ARM64 VM/systemd/SSM test.
python3.12 --version
psql --version
curl --fail --silent --show-error https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.38.5.zip -o /tmp/aws.zip
curl --fail --silent --show-error https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.38.5.zip.sig -o /tmp/aws.zip.sig
gpg --import /aws-cli-signing-key.asc
gpg --verify /tmp/aws.zip.sig /tmp/aws.zip
unzip -q /tmp/aws.zip -d /tmp
/tmp/aws/install
aws --version
aws --version | grep -q 'aws-cli/2\.'
[[ -z $(pg_lsclusters --no-header) ]]
runuser -u postgres -- /usr/lib/postgresql/17/bin/initdb -D /tmp/probe-pg --encoding=UTF8 --locale=C.UTF-8 >/dev/null
runuser -u postgres -- /usr/lib/postgresql/17/bin/pg_ctl -D /tmp/probe-pg -l /tmp/probe-pg.log -o "-k /tmp -h ''" -w start >/dev/null
runuser -u postgres -- psql -X -h /tmp -Atc 'SELECT version();'
runuser -u postgres -- /usr/lib/postgresql/17/bin/pg_ctl -D /tmp/probe-pg -m fast -w stop >/dev/null
printf 'Ubuntu 24.04 Python 3.12 / PostgreSQL 17 / AWS CLI v2 probe passed; ARM64 package metadata verified.\n'
PROBE
