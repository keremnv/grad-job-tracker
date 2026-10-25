"""Signed native AWS CLI, real Linux users/NoNewPrivileges, no AWS/network access.

Set JOBWATCH_CLI_TEST_ARCHIVE to the pinned official x86_64 ZIP. Its detached
signature is verified with the committed signing key before any container runs.
The production bootstrap's exact normalization fragment is exercised unchanged.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4
import zipfile

IMAGE = 'python@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70'
USERS = tuple('jobwatch-' + name for name in ('collector', 'owner', 'migrator', 'backup'))


def inside(case):
    if os.environ.get('JOBWATCH_CLI_TEST') != 'disposable' or not Path('/.dockerenv').is_file():
        raise RuntimeError('Use the disposable Docker runner')
    check = unittest.TestCase()
    os.umask(0o022)
    for user in USERS:
        subprocess.run(['useradd', '--system', '--no-create-home', '--shell', '/usr/sbin/nologin', user], check=True)
    staging = Path('/installer')
    with zipfile.ZipFile('/review/aws.zip') as archive:
        for entry in archive.infolist():
            target = staging / entry.filename
            check.assertTrue(target.resolve().is_relative_to(staging))
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
                target.chmod((entry.external_attr >> 16) & 0o777 or 0o644)
    mask = 0o022 if case == 'ordinary' else 0o027
    os.umask(mask)
    subprocess.run(['/installer/aws/install'], check=True, capture_output=True)
    root = Path('/usr/local/aws-cli')
    private = Path('/etc/jobwatch/private')
    private.parent.mkdir(mode=0o750)
    private.write_bytes(b'private synthetic configuration')
    private.chmod(0o600)
    external = Path('/outside')
    external.mkdir(mode=0o700)
    secret = external / 'secret'
    secret.write_bytes(b'external synthetic content')
    secret.chmod(0o600)
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
    text = Path('/review/bootstrap_host.sh').read_text()
    fragment = text.split("python3.12 - <<'AWS_CLI_PERMISSIONS'\n", 1)[1].split('\nAWS_CLI_PERMISSIONS', 1)[0]
    if case in ('ordinary', 'restrictive'):
        if mask == 0o027:
            denied = subprocess.run(['runuser', '-u', 'jobwatch-backup', '--', '/usr/local/bin/aws', '--version'], capture_output=True)
            check.assertNotEqual(denied.returncode, 0)  # Demonstrates the original defect.
        for phase in ('first', 'matching-version-repeat'):
            if phase != 'first':
                # Exact production version condition skips installation, normalization still runs.
                p = subprocess.run(['/usr/local/bin/aws', '--version'], check=True, capture_output=True, text=True)
                check.assertIn('aws-cli/2.38.5 ', p.stdout)
            exec(compile(fragment, '<actual-bootstrap-cli-permissions>', 'exec'), {})
            for user in USERS:
                p = subprocess.run(['setpriv', '--no-new-privs', 'runuser', '-u', user, '--',
                                    '/usr/local/bin/aws', '--version'], capture_output=True, text=True, check=True)
                check.assertIn('aws-cli/2.38.5 ', p.stdout)
                probe = subprocess.run(['runuser', '-u', user, '--', sys.executable, '-c',
                    "import os;os.open('/usr/local/aws-cli/v2/2.38.5/dist/aws',os.O_WRONLY)"], capture_output=True, text=True)
                check.assertNotEqual(probe.returncode, 0)
                check.assertIn('PermissionError', probe.stderr)
            check.assertEqual({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in root.rglob('*') if p.is_file() and not p.is_symlink()}, before)
            for p in (root, *root.rglob('*')):
                check.assertEqual(p.lstat().st_uid, 0)
                if not p.is_symlink():
                    check.assertFalse(p.lstat().st_mode & 0o022)
                    check.assertIn(p.stat().st_mode & 0o777, (0o644, 0o755))
    else:
        if case == 'external-link':
            (root / 'v2/2.38.5/dist/unsafe-link').symlink_to(secret)
        elif case == 'ownership':
            import pwd
            os.chown(root / 'v2/2.38.5/dist/aws', pwd.getpwnam('jobwatch-backup').pw_uid, 0)
        elif case == 'special':
            os.mkfifo(root / 'v2/2.38.5/dist/unsafe-fifo')
        elif case == 'hardlink':
            os.link(secret, root / 'v2/2.38.5/dist/unsafe-hardlink')
        elif case == 'writable':
            (root / 'v2/2.38.5/dist/aws').chmod(0o777)
        elif case == 'layout':
            (root / '.aws').mkdir()
        else:
            raise ValueError('Unknown probe')
        snapshot = {str(p): p.lstat().st_mode for p in (root, *root.rglob('*'))}
        with check.assertRaises((RuntimeError, ValueError)):
            exec(compile(fragment, '<actual-bootstrap-cli-permissions>', 'exec'), {})
        check.assertEqual({str(p): p.lstat().st_mode for p in (root, *root.rglob('*'))}, snapshot)
    check.assertEqual(secret.stat().st_mode & 0o777, 0o600)
    check.assertEqual(secret.read_bytes(), b'external synthetic content')
    check.assertEqual(external.stat().st_mode & 0o777, 0o700)
    check.assertEqual(private.stat().st_mode & 0o777, 0o600)
    check.assertEqual(private.read_bytes(), b'private synthetic configuration')
    return {'case': case, 'real_cli_and_users': True}


class CLIPermissionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[3]
        cls.archive = Path(os.environ['JOBWATCH_CLI_TEST_ARCHIVE']).resolve()
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['gpg', '--homedir', directory, '--import', str(cls.root / 'infra/aws/aws-cli-signing-key.asc')],
                           check=True, capture_output=True)
            subprocess.run(['gpg', '--homedir', directory, '--verify', str(cls.archive) + '.sig', str(cls.archive)],
                           check=True, capture_output=True)

    def probe(self, case):
        name = 'jobwatch-cli-test-' + uuid4().hex
        try:
            p = subprocess.run(['docker', 'run', '--rm', '--name', name, '--network', 'none', '--user', 'root',
                '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'JOBWATCH_CLI_TEST=disposable',
                '--mount', f'type=bind,src={self.archive},dst=/review/aws.zip,readonly',
                '--mount', f'type=bind,src={self.root}/infra/aws/scripts/bootstrap_host.sh,dst=/review/bootstrap_host.sh,readonly',
                '--mount', f'type=bind,src={Path(__file__).resolve()},dst=/review/test.py,readonly',
                IMAGE, 'python', '/review/test.py', '--inside', case], capture_output=True, text=True, timeout=180)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue(json.loads(p.stdout)['real_cli_and_users'])
        finally:
            subprocess.run(['docker', 'rm', '-fv', name], capture_output=True)

    def test_umask_0022_first_and_matching_version_repeat(self): self.probe('ordinary')
    def test_umask_0027_first_and_matching_version_repeat(self): self.probe('restrictive')
    def test_external_link_refused_without_modifying_target(self): self.probe('external-link')
    def test_unexpected_ownership_refused(self): self.probe('ownership')
    def test_special_file_refused(self): self.probe('special')
    def test_external_hardlink_refused(self): self.probe('hardlink')
    def test_writable_contents_refused(self): self.probe('writable')
    def test_unexpected_layout_refused(self): self.probe('layout')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--inside':
        print(json.dumps(inside(sys.argv[2])))
    else:
        unittest.main()
