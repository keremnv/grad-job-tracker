"""Actual root/non-root Linux access checks in pinned, network-disabled Docker.

Native-package installation, systemd and the ARM64 guard are test boundaries;
the installer, hashing, filesystem, venv interpreter and service UIDs are real.
No AWS credentials, host disks, database or network are passed to the container.
"""
from contextlib import redirect_stdout
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
import venv

IMAGE = 'python@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70'
COMMIT = 'a' * 40
USERS = tuple('jobwatch-' + name for name in ('collector', 'owner', 'migrator', 'backup'))


def inside(case):
    """Only invoked inside our newly created disposable root container."""
    if os.environ.get('JOBWATCH_INSTALLER_TEST') != 'disposable' or not Path('/.dockerenv').is_file():
        raise RuntimeError('Use the outer disposable Docker test runner')
    assert os.geteuid() == 0
    spec = importlib.util.spec_from_file_location('deploy_release', '/review/deploy_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    check = unittest.TestCase()
    os.umask(0o022)
    work = Path('/fixture')
    work.mkdir()
    subprocess.run(['groupadd', '--system', 'jobwatch-db'], check=True)
    for user in USERS:
        subprocess.run(['useradd', '--system', '--no-create-home', '--gid', 'jobwatch-db',
                        '--shell', '/usr/sbin/nologin', user], check=True)
    files = {'src/probe.py': b'print("permission-probe")\n'}
    for name in ('jobwatch-collect.service', 'jobwatch-backup.service',
                 'jobwatch-collect.timer', 'jobwatch-backup.timer'):
        files['infra/aws/systemd/' + name] = b'# synthetic unit; never loaded\n'
    manifest = {'source_commit': COMMIT, 'python': '3.12', 'architecture': 'aarch64',
                'files': {k: sha256(v).hexdigest() for k, v in files.items()}}
    files['MANIFEST.json'] = json.dumps(manifest, sort_keys=True, indent=2).encode() + b'\n'
    archive = work / 'release.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            stream.addfile(entry, io.BytesIO(data))
    digest = sha256(archive.read_bytes()).hexdigest()
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if args[0] == 'systemctl':
            check.assertIn(args[1], ('is-active', 'is-enabled', 'daemon-reload'))
            return SimpleNamespace(returncode=1 if args[1].startswith('is-') else 0)
        if args[:3] == ['python3.12', '-m', 'venv']:
            prefix = Path(args[3])
            venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
            cli = prefix / 'bin/jobwatch-pg-ops'
            cli.write_text('#!' + str(prefix / 'bin/python') + '\nprint("fixture-cli")\n')
            cli.chmod(0o700)
            return SimpleNamespace(returncode=0)
        if Path(args[0]).name == 'pip' or (args[-1] == 'import psycopg; assert psycopg.__version__ == "3.2.13"'):
            return SimpleNamespace(returncode=0)  # Native wheels belong to hosted qualification.
        return subprocess.run(args, **kwargs)

    def deploy(root):
        with patch.object(module.platform, 'machine', return_value='aarch64'), redirect_stdout(io.StringIO()):
            module.deploy(archive, digest, COMMIT, installation_root=root, run_command=run)

    if case in ('ordinary', 'restrictive', 'incomplete'):
        root = work / 'host'
        (root / 'etc/systemd/system').mkdir(parents=True)
        (root / 'opt').mkdir()  # Existing Ubuntu /opt, not an installer-created parent.
        private = root / 'etc/jobwatch/pg_hba.conf'
        private.parent.mkdir()
        private.write_bytes(b'private configuration')
        private.chmod(0o640)
        data = root / 'var/lib/jobwatch-postgresql/pgdata/evidence'
        data.parent.mkdir(parents=True)
        data.write_bytes(b'immutable synthetic evidence')
        data.chmod(0o600)
        os.umask(0o022 if case == 'ordinary' else 0o077)
        system_python_mode = Path(sys.executable).stat().st_mode
        for phase in ('first', 'repeat'):
            deploy(root)
            current = root / 'opt/jobwatch/current'
            destination = current.resolve()
            check.assertEqual(destination.stat().st_mode & 0o777, 0o755)
            check.assertEqual(Path(sys.executable).stat().st_mode, system_python_mode)
            for member in (destination, *destination.rglob('*')):
                info = member.lstat()
                check.assertEqual(info.st_uid, 0)
                if not member.is_symlink():
                    check.assertFalse(info.st_mode & 0o022)
                    check.assertIn(info.st_mode & 0o777, (0o644, 0o755))
            for user in USERS:
                result = subprocess.run(['runuser', '-u', user, '--', str(current / 'venv/bin/python'),
                                         str(current / 'src/probe.py')], capture_output=True, text=True, check=True)
                check.assertEqual(result.stdout.strip(), 'permission-probe')
                subprocess.run(['runuser', '-u', user, '--', str(current / 'venv/bin/jobwatch-pg-ops'), '--help'],
                               check=True, capture_output=True)
                # Actual open() permission enforcement, not a mocked mode or identity.
                denied = subprocess.run(['runuser', '-u', user, '--', sys.executable, '-c',
                    'import os,sys;os.open(sys.argv[1],os.O_WRONLY|os.O_APPEND)', str(current / 'src/probe.py')],
                    capture_output=True, text=True)
                check.assertNotEqual(denied.returncode, 0)
                check.assertIn('PermissionError', denied.stderr)
                check.assertNotEqual(subprocess.run(['runuser', '-u', user, '--', 'test', '-w', str(destination)]).returncode, 0)
            check.assertEqual(private.stat().st_mode & 0o777, 0o640)
            check.assertEqual(private.read_bytes(), b'private configuration')
            check.assertEqual(data.stat().st_mode & 0o777, 0o600)
            check.assertEqual(data.read_bytes(), b'immutable synthetic evidence')
        check.assertEqual(sum(args[:3] == ['python3.12', '-m', 'venv'] for args in commands), 1)
        if case == 'incomplete':
            (destination / 'INSTALL_COMPLETE').unlink()
            parents = (root / 'opt/jobwatch', root / 'opt/jobwatch/releases', destination)
            for parent in parents:
                parent.chmod(0o700)
            with check.assertRaisesRegex(ValueError, 'Incomplete'):
                deploy(root)
            check.assertTrue(all(parent.stat().st_mode & 0o777 == 0o700 for parent in parents))
    elif case == 'symlinks':
        code = work / 'code'
        code.mkdir(mode=0o700)
        external = work / 'external'
        external.mkdir(mode=0o700)
        secret = external / 'private'
        secret.write_bytes(b'outside the code tree')
        secret.chmod(0o600)
        (code / 'file-link').symlink_to(secret)
        (code / 'dir-link').symlink_to(external, target_is_directory=True)
        (code / 'python-link').symlink_to(sys.executable)
        interpreter_mode = Path(sys.executable).stat().st_mode
        module.make_code_readable(code)
        check.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        check.assertEqual(external.stat().st_mode & 0o777, 0o700)
        check.assertEqual(Path(sys.executable).stat().st_mode, interpreter_mode)
        with check.assertRaisesRegex(ValueError, 'symlink'):
            module.make_code_readable(code / 'dir-link')
    elif case == 'parents':
        for name in ('opt', 'jobwatch', 'releases', 'release'):
            root = work / name
            root.mkdir()
            outside = work / ('outside-' + name)
            outside.mkdir(mode=0o700)
            marker = outside / 'private'
            marker.write_bytes(b'preserved')
            marker.chmod(0o600)
            relative = {'opt': 'opt', 'jobwatch': 'opt/jobwatch', 'releases': 'opt/jobwatch/releases',
                        'release': 'opt/jobwatch/releases/' + COMMIT}[name]
            link = root / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside, target_is_directory=True)
            with check.assertRaisesRegex(ValueError, 'symlink'):
                deploy(root)
            check.assertEqual(list(outside.iterdir()), [marker])
            check.assertEqual(outside.stat().st_mode & 0o777, 0o700)
            check.assertEqual(marker.stat().st_mode & 0o777, 0o600)
    else:
        raise ValueError('Unknown disposable probe')
    return {'case': case, 'real_uid_permissions': True, 'root_owned_readonly_code': True,
            'no_systemd_start_or_enable': True, 'native_packages_and_architecture_guard_stubbed': True}


class InstallerPermissionsTests(unittest.TestCase):
    def container_probe(self, case):
        root = Path(__file__).resolve().parents[3]
        name = 'jobwatch-installer-test-' + uuid4().hex
        args = ['docker', 'run', '--rm', '--name', name, '--network', 'none', '--user', 'root',
                '--label', 'jobwatch.test=installer-permissions', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                '-e', 'JOBWATCH_INSTALLER_TEST=disposable',
                '--mount', f'type=bind,src={root}/infra/aws/scripts/deploy_release.py,dst=/review/deploy_release.py,readonly',
                '--mount', f'type=bind,src={Path(__file__).resolve()},dst=/review/test_permissions.py,readonly',
                IMAGE, 'python', '/review/test_permissions.py', '--inside', case]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)['real_uid_permissions'])
        finally:
            subprocess.run(['docker', 'rm', '-fv', name], capture_output=True)

    def test_ordinary_umask_first_and_repeat(self):
        self.container_probe('ordinary')

    def test_restrictive_umask_first_and_repeat(self):
        self.container_probe('restrictive')

    def test_incomplete_release_not_normalized(self):
        self.container_probe('incomplete')

    def test_external_symlinks_and_system_interpreter_unchanged(self):
        self.container_probe('symlinks')

    def test_symlinked_installation_parents_and_release_refused(self):
        self.container_probe('parents')

    def test_probe_refuses_unverified_execution(self):
        with patch.dict(os.environ, {'JOBWATCH_INSTALLER_TEST': ''}):
            with self.assertRaisesRegex(RuntimeError, 'disposable Docker'):
                inside('ordinary')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--inside':
        print(json.dumps(inside(sys.argv[2])))
    else:
        unittest.main()
