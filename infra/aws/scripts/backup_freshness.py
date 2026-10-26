#!/usr/bin/env python3
"""Offline integrity/age check for a retrieved completed V1 backup, not a monitor.

No AWS calls, credentials, writes, notification delivery or custody attestation.
Retrieve exact manifest/archive versions through the separately approved recovery
path first. A local file cannot prove independent storage or trusted provenance.
"""
import argparse
from datetime import datetime, timezone
from hashlib import file_digest
import json
import math
from pathlib import Path


def _time(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise ValueError('invalid_timestamp') from None


def inspect(archive, manifest, *, now=None):
    """Start time bounds snapshot age conservatively; uploads never reset it."""
    if not isinstance(manifest, dict) or manifest.get('schema') != 'jobwatch.aws-backup/2':
        raise ValueError('unsupported_manifest')
    if 'backup_started_at' not in manifest:
        raise ValueError('backup_start_unproven')
    start = _time(manifest['backup_started_at'])
    created = _time(manifest.get('created_at'))
    checked = now or datetime.now(timezone.utc)
    if checked.tzinfo is None or checked.utcoffset() is None or not start <= created <= checked:
        raise ValueError('invalid_chronology')
    link = manifest.get('archive_object')
    if (not isinstance(link, dict) or not isinstance(link.get('version_id'), str)
            or not link['version_id'] or link['version_id'] == 'null'
            or not isinstance(link.get('key'), str) or not link['key'].startswith('backups/')
            or not link['key'].endswith('.dump')):
        raise ValueError('completion_link_missing')
    if archive.is_symlink() or not archive.is_file():
        raise ValueError('archive_not_regular')
    if type(manifest.get('archive_bytes')) is not int or archive.stat().st_size != manifest['archive_bytes']:
        raise ValueError('archive_size_mismatch')
    with archive.open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise ValueError('archive_format_invalid')
        stream.seek(0)
        digest = file_digest(stream, 'sha256').hexdigest()
    if digest != manifest.get('archive_sha256') or digest != link.get('sha256'):
        raise ValueError('archive_hash_mismatch')
    age = math.ceil((checked - start).total_seconds())
    status = ('breached' if age >= 12 * 3600 else 'critical' if age >= 10 * 3600
              else 'warning' if age >= 8 * 3600 else 'fresh')
    return {'schema': 'jobwatch.backup-freshness/1', 'status': status,
            'conservative_snapshot_age_seconds': age, 'maximum_age_seconds': 12 * 3600,
            'local_archive_matches_completion_manifest': True,
            'independent_custody_verified': False, 'alert_delivery_verified': False,
            'production_ready': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.archive, json.loads(args.manifest.read_text()))
    except (OSError, ValueError, TypeError, KeyError):
        # No paths, input data or exception text in unattended output.
        print(json.dumps({'schema': 'jobwatch.backup-freshness/1', 'status': 'invalid',
                          'production_ready': False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'fresh' else 1 if result['status'] == 'warning' else 2


if __name__ == '__main__':
    raise SystemExit(main())
