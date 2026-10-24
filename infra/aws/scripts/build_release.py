#!/usr/bin/env python3
"""Build code-only Ubuntu ARM64/Python 3.12 artifact from an explicit committed revision.

No data/, config/, reports/, credentials, Git metadata, or working-tree files enter it.
Runs locally, never calls AWS. Third-party wheels are hash-locked in the artifact.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

PATHS = ('src', 'pyproject.toml', 'infra/aws/scripts', 'infra/aws/systemd', 'sql/postgresql/bootstrap.sql')


def build(repo, commit, output):
    if output.resolve().is_relative_to(repo.resolve()):
        raise ValueError('Keep release artifacts outside the source checkout')
    if output.exists():
        raise ValueError('Output exists; use a new private artifact path')
    resolved = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--verify', commit+'^{commit}'], text=True).strip()
    if resolved != commit:
        raise ValueError('Use a full frozen commit SHA')
    with tempfile.TemporaryDirectory(prefix='jobwatch-release-') as temporary:
        root = Path(temporary)
        # Extract only regular whitelisted code objects. Ignore directories; reject links.
        raw = subprocess.check_output(['git', '-C', str(repo), 'archive', resolved, *PATHS])
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive:
                if member.isdir():
                    continue
                target = root / member.name
                if not member.isfile() or not target.resolve().is_relative_to(root.resolve()):
                    raise ValueError('Unexpected archive member')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
        wheels = root / 'wheels'
        wheels.mkdir()
        epoch = subprocess.check_output(['git', '-C', str(repo), 'show', '-s', '--format=%ct', resolved], text=True).strip()
        subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(wheels), str(root)], check=True,
                       env={**os.environ, 'SOURCE_DATE_EPOCH': epoch}, stdout=subprocess.DEVNULL)
        subprocess.run(['uv', 'run', '--no-project', '--with', 'pip', 'python', '-m', 'pip', 'download',
                        '--only-binary=:all:', '--platform', 'manylinux2014_aarch64', '--python-version', '312',
                        '--implementation', 'cp', '--abi', 'cp312', '--dest', str(wheels),
                        'psycopg[binary]==3.2.13', 'typing_extensions==4.15.0'], check=True, stdout=subprocess.DEVNULL)
        requirements = []
        for wheel in sorted(wheels.glob('*.whl')):
            # Wheel names escape distribution hyphens with underscores.
            name, version = wheel.name.split('-')[:2]
            requirements.append(f'{name}=={version} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}')
        (root/'requirements.lock').write_text('\n'.join(requirements)+'\n')
        # Delete build directories/metadata, retain only the explicitly shipped code and wheels.
        for path in list(root.iterdir()):
            if path.name not in ('src','pyproject.toml','infra','sql','wheels','requirements.lock'):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
        manifest = {'source_commit': resolved, 'python': '3.12', 'architecture': 'aarch64',
                    'files': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(root.rglob('*')) if p.is_file()}}
        (root/'MANIFEST.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
        output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for path in sorted(root.rglob('*')):
                if path.is_file():
                    archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
    return {'source_commit': resolved, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.repo, args.commit, args.output), sort_keys=True))
