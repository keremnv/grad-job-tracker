#!/usr/bin/env python3
"""Local last-confirmed S3 backup health. No database, S3 reads, secrets or fencing."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from backup_freshness import age_report, inspect
from run_operation import publish


def validate(receipt, *, now=None):
    if not isinstance(receipt, dict) or receipt.get('schema') != 'jobwatch.backup-receipt/1':
        raise ValueError('invalid_receipt')
    health = age_report(receipt.get('backup_started_at'), receipt.get('completed_at'), now=now)
    for field, suffix in (('archive_object', '.dump'), ('manifest_object', '.json')):
        obj = receipt.get(field)
        if (not isinstance(obj, dict) or not isinstance(obj.get('version_id'), str)
                or obj['version_id'] in ('', 'null') or not isinstance(obj.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', obj['sha256'])
                or not isinstance(obj.get('key'), str) or not obj['key'].startswith('backups/')
                or not obj['key'].endswith(suffix)):
            raise ValueError('invalid_completion_link')
    if receipt['archive_object']['key'][:-5] != receipt['manifest_object']['key'][:-5]:
        raise ValueError('completion_pair_mismatch')
    return health


def record(archive, receipts, path, *, now=None):
    """Called only after the uploader confirms BOTH versions. Preserve last good on failure."""
    manifest_path = archive.with_suffix('.json')
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    inspect(archive, manifest, now=now)  # Keep every existing byte/hash/chronology check.
    if (len(receipts) != 2 or manifest['archive_object'] != receipts[0]
            or receipts[1]['sha256'] != sha256(raw).hexdigest()):
        raise ValueError('unconfirmed_completion')
    receipt = {'schema': 'jobwatch.backup-receipt/1',
               'backup_started_at': manifest['backup_started_at'],
               'completed_at': (now or datetime.now(timezone.utc)).isoformat(),
               'archive_object': receipts[0], 'manifest_object': receipts[1]}
    validate(receipt, now=now)
    parent = path.parent
    st = parent.stat()
    if parent.is_symlink() or st.st_uid != os.geteuid() or st.st_mode & 0o077 or path.is_symlink():
        raise ValueError('private_receipt_directory_required')
    fd, temporary = tempfile.mkstemp(prefix='.receipt-', dir=parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(receipt, stream, sort_keys=True)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def health(path, *, now=None):
    summary = {'operation': 'backup_health', 'backup_healthy': 0, 'status': 'invalid'}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            st = os.fstat(stream.fileno())
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                    or st.st_mode & 0o077 or st.st_size > 8192):
                raise ValueError('unsafe_receipt')
            data = validate(json.loads(stream.read(8193)), now=now)
        summary.update(status=data['status'],
                       conservative_snapshot_age_seconds=data['conservative_snapshot_age_seconds'],
                       backup_healthy=int(data['status'] == 'fresh'))
    except FileNotFoundError:
        summary['status'] = 'missing'
    except (OSError, ValueError, TypeError, KeyError):
        pass  # Never log paths, receipt keys/versions, raw data or exception text.
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, default=Path('/var/lib/jobwatch-backup/latest.json'))
    parser.add_argument('--config', type=Path, default=Path('/etc/jobwatch/aws.json'))
    args = parser.parse_args(argv)
    try:
        summary = health(args.receipt)
        delivered = publish(summary, json.loads(args.config.read_text()))
        return 0 if delivered and summary['backup_healthy'] else 2
    except (OSError, ValueError, TypeError, KeyError):
        print('Backup health delivery failed; private diagnostics omitted', file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
