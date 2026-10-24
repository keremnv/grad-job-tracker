#!/usr/bin/env python3
"""Root-only local installation of a hash-verified code artifact. Never touches DB data."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile


def verify(archive_path, expected_sha256, commit):
    if not re.fullmatch('[0-9a-f]{40}', commit) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('Full commit and SHA-256 required')
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError('Artifact SHA-256 mismatch')
    with tarfile.open(archive_path) as archive:
        names = set()
        for entry in archive:
            p = PurePosixPath(entry.name)
            if (not entry.isfile() or p.is_absolute() or '..' in p.parts or str(p) != entry.name
                    or entry.name in names or entry.size > 100_000_000):
                raise ValueError('Unsafe archive member')
            names.add(entry.name)
        if 'MANIFEST.json' not in names:
            raise ValueError('Missing release manifest')
        manifest = json.load(archive.extractfile('MANIFEST.json'))
        if (manifest['source_commit'] != commit or manifest['python'] != '3.12'
                or manifest['architecture'] != 'aarch64' or names != set(manifest['files']) | {'MANIFEST.json'}):
            raise ValueError('Artifact identity or inventory mismatch')
        for name, digest in manifest['files'].items():
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest() != digest:
                raise ValueError('Artifact member hash mismatch')
    return manifest


def deploy(archive_path, sha256, commit, *, installation_root=Path('/'), run_command=subprocess.run):
    if os.geteuid() != 0:
        raise ValueError('Root required for code installation')
    for unit in ('jobwatch-collect.timer', 'jobwatch-backup.timer', 'jobwatch-collect.service', 'jobwatch-backup.service'):
        # Never implicitly stop a writer/restart its timer during deployment.
        if run_command(['systemctl', 'is-active', '--quiet', unit]).returncode == 0:
            raise ValueError('Pause collection/backups explicitly before deployment')
        if unit.endswith('.timer') and run_command(['systemctl', 'is-enabled', '--quiet', unit], capture_output=True).returncode == 0:
            raise ValueError('Disable timers explicitly before deployment')
    manifest = verify(archive_path, sha256, commit)
    destination = installation_root / 'opt/jobwatch/releases' / commit
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if (destination/'MANIFEST.json').read_text() != json.dumps(manifest, sort_keys=True, indent=2)+'\n':
            raise ValueError('Existing release differs; never overwrite it')
        for name, digest in manifest['files'].items():
            if hashlib.sha256((destination/name).read_bytes()).hexdigest() != digest:
                raise ValueError('Installed release content changed; inspect it before retry')
        if not (destination/'venv/bin/jobwatch-pg-ops').is_file():
            raise ValueError('Incomplete release; inspect it before retry')
    else:
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.staging-') as temporary:
            staged = Path(temporary)
            with tarfile.open(archive_path) as archive:
                for entry in archive:
                    p = staged/entry.name
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(archive.extractfile(entry).read())
            # Install at final pathname: venv console-script shebangs must stay valid.
            staged.rename(destination)
        try:
            run_command(['python3.12', '-m', 'venv', str(destination/'venv')], check=True)
            run_command([str(destination/'venv/bin/pip'), 'install', '--no-index', '--no-deps', '--require-hashes',
                            '--find-links', str(destination/'wheels'), '-r', str(destination/'requirements.lock')], check=True)
            run_command([str(destination/'venv/bin/jobwatch-pg-ops'), '--help'], check=True, stdout=subprocess.DEVNULL)
        except BaseException:
            # No DB changes. Leave incomplete release for explicit inspection, never erase it.
            raise
    for unit in (destination/'infra/aws/systemd').glob('*.service'):
        shutil.copyfile(unit, installation_root/'etc/systemd/system'/unit.name)
    for unit in (destination/'infra/aws/systemd').glob('*.timer'):
        shutil.copyfile(unit, installation_root/'etc/systemd/system'/unit.name)
    link = installation_root/'opt/jobwatch/.next'
    link.unlink(missing_ok=True)
    link.symlink_to(destination)
    link.replace(installation_root/'opt/jobwatch/current')
    run_command(['systemctl', 'daemon-reload'], check=True)
    # No enable/start, migration/import or scan. All releases are root-owned read-only code.
    print('Release installed; timers remain disabled. Apply migrations separately.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    deploy(args.archive, args.sha256, args.commit)
