#!/usr/bin/env bash
# Future approved operator action. Downloads code only; never tracker/owner data.
set -euo pipefail
[[ ${EUID} == 0 && $# == 3 && $1 =~ ^artifacts/[a-zA-Z0-9._/-]+$ && $2 =~ ^[0-9a-f]{64}$ && $3 =~ ^[0-9a-f]{40}$ ]] || { echo 'Usage (root): fetch_release.sh artifacts/KEY SHA256 COMMIT' >&2; exit 2; }
[[ $1 != *..* ]] || exit 2
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
readarray -t settings < <(python3.12 -c 'import json; c=json.load(open("/etc/jobwatch/aws.json")); print(c["region"]); print(c["bucket"])')
aws s3api get-object --region "${settings[0]}" --bucket "${settings[1]}" --key "$1" "$staging/release.tar.gz" >/dev/null
# Do not execute anything from the archive until the independently reviewed hash is verified.
python3.12 /opt/jobwatch-bootstrap/deploy_release.py "$staging/release.tar.gz" --sha256 "$2" --commit "$3"
