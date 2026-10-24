#!/usr/bin/env bash
# Explicit operator step; never called by cloud-init, collection or deploy_release.
set -euo pipefail
[[ ${EUID} == 0 ]] || { echo 'Root required to select migration OS identity' >&2; exit 2; }
for unit in jobwatch-collect.timer jobwatch-backup.timer jobwatch-collect.service jobwatch-backup.service; do
  if systemctl is-active --quiet "$unit"; then echo 'Pause writers before schema migration' >&2; exit 2; fi
done
exec runuser -u jobwatch-migrator -- env \
  JOBWATCH_MIGRATION_DATABASE_URL='host=/run/jobwatch-postgresql dbname=jobwatch user=jobwatch_migrator' \
  /opt/jobwatch/current/venv/bin/jobwatch-db-migrate
