"""Actual host bootstrap fragments in an isolated, network-disabled PostgreSQL 17 container.

No host disks, AWS credentials, systemd operations or application data are used.
The service's real User/Group/ExecStart are used; this does not emulate systemd
or claim to test ARM64 Ubuntu package installation, EBS or SSM.
"""
import configparser
from pathlib import Path
import shlex
import subprocess
import time
import unittest
from uuid import uuid4

from support import ENABLED, Target

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 'postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27'
CONFIGS = ('postgresql.conf', 'pg_hba.conf', 'pg_ident.conf')
ROLES = ('jobwatch_collector', 'jobwatch_owner', 'jobwatch_migrator')
SQL = '/opt/jobwatch-bootstrap/bootstrap.sql'
PGDATA = '/var/lib/jobwatch-postgresql/pgdata'


@unittest.skipUnless(ENABLED, 'Use scripts/test_postgres.sh; disposable Docker targets only')
class HostBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Target.verified()  # Same explicit disposable-test authorization as the other DB tests.
        source = (ROOT / 'infra/aws/scripts/bootstrap_host.sh').read_text()
        unit_marker = "cat > /etc/systemd/system/jobwatch-postgresql.service <<'UNIT'\n"
        cls.prepare = source[source.index('getent group jobwatch-db'):source.index(unit_marker)]
        cls.provision = source[source.index('psql_admin()'):source.index('# Pinned official AWS CLI')]
        unit = configparser.ConfigParser(interpolation=None)
        unit.read_string(source.split(unit_marker, 1)[1].split('\nUNIT\n', 1)[0])
        service = unit['Service']
        cls.identity = ['runuser', '-u', service['User'], '-g', service['Group'], '--']
        cls.start_command = cls.identity + shlex.split(service['ExecStart'])
        cls.name = 'jobwatch-bootstrap-test-' + uuid4().hex
        subprocess.run(['docker', 'run', '-d', '--name', cls.name, '--network', 'none',
                        '--label', 'jobwatch.test=aws-bootstrap', '--user', 'root',
                        '--mount', f'type=bind,src={ROOT}/sql/postgresql/bootstrap.sql,dst=/review/bootstrap.sql,readonly',
                        '--entrypoint', 'sleep', IMAGE, 'infinity'], check=True, capture_output=True)
        cls.addClassCleanup(subprocess.run, ['docker', 'rm', '-fv', cls.name],
                            check=True, capture_output=True)
        cls.shell('data_mount=/var/lib/jobwatch-postgresql\n' + cls.prepare)
        cls.shell(f'install -d -o {service["User"]} -g {service["Group"]} '
                  f'-m {service["RuntimeDirectoryMode"]} /run/{service["RuntimeDirectory"]}\n'
                  f'install -D -o root -g root -m 0600 /review/bootstrap.sql {SQL}')
        cls.start()

    @classmethod
    def command(cls, args, *, input=None, check=True):
        result = subprocess.run(['docker', 'exec', '-i', cls.name] + args, input=input,
                                capture_output=True, text=True, timeout=30)
        if check and result.returncode:
            raise AssertionError(f'Disposable host command failed: {result.stdout}{result.stderr}')
        return result

    @classmethod
    def shell(cls, script, *, check=True):
        return cls.command(['bash', '-s'], input='set -euo pipefail\numask 027\n' + script, check=check)

    @classmethod
    def start(cls):
        # Same identity and server arguments as ExecStart, without a systemd mock.
        cls.command(['bash', '-c', ' '.join(('exec', shlex.join(cls.start_command))) +
                     ' > /var/lib/jobwatch-postgresql/pgdata/server.log 2>&1 &'])
        for _ in range(50):
            ready = cls.command(['pg_isready', '-h', '/run/jobwatch-postgresql', '-U', 'postgres'], check=False)
            if ready.returncode == 0:
                return
            time.sleep(.1)
        raise AssertionError(cls.command(['cat', PGDATA + '/server.log']).stdout)

    @classmethod
    def stop(cls):
        cls.command(cls.identity + ['/usr/lib/postgresql/17/bin/pg_ctl', '-D', PGDATA, '-m', 'fast', 'stop'])

    @classmethod
    def scalar(cls, sql):
        return cls.command(cls.identity + ['psql', '-X', '-h', '/run/jobwatch-postgresql',
                           '-v', 'ON_ERROR_STOP=1', '-d', 'jobwatch', '-Atc', sql]).stdout.strip()

    @classmethod
    def drop_roles(cls):
        for role in ROLES:
            cls.scalar(f'DROP OWNED BY {role}; DROP ROLE {role}')

    def setUp(self):
        self.shell(self.provision)

    def test_service_identity_reads_actual_configs_and_server_uses_them(self):
        group = self.command(['getent', 'group', 'jobwatch-db']).stdout.split(':')[2]
        for name in CONFIGS:
            with self.subTest(file=name):
                path = '/etc/jobwatch/' + name
                self.assertEqual(self.command(['stat', '-c', '%u:%g:%a', path]).stdout.strip(), f'0:{group}:640')
                self.command(self.identity + ['test', '-r', path])
                self.assertEqual(self.command(self.identity + ['test', '-w', path], check=False).returncode, 1)
        self.assertEqual(self.scalar('SHOW config_file'), '/etc/jobwatch/postgresql.conf')
        self.assertEqual(self.scalar('SHOW hba_file'), '/etc/jobwatch/pg_hba.conf')
        self.assertEqual(self.scalar('SHOW ident_file'), '/etc/jobwatch/pg_ident.conf')
        self.assertEqual(self.scalar('SHOW listen_addresses'), '127.0.0.1')

    def test_application_identities_cannot_modify_configs_or_directory(self):
        for user in ('collector', 'owner', 'migrator', 'backup'):
            for path in ['/etc/jobwatch'] + ['/etc/jobwatch/' + name for name in CONFIGS]:
                with self.subTest(user=user, file=path):
                    result = self.command(['runuser', '-u', 'jobwatch-' + user, '--', 'test', '-w', path], check=False)
                    self.assertEqual(result.returncode, 1)

    def test_root_only_sql_stays_unreadable_to_postgres_and_roles_exist(self):
        self.assertEqual(self.command(['stat', '-c', '%u:%g:%a', SQL]).stdout.strip(), '0:0:600')
        self.assertEqual(self.command(self.identity + ['test', '-r', SQL], check=False).returncode, 1)
        self.assertEqual(self.scalar("SELECT string_agg(rolname,',' ORDER BY rolname) FROM pg_roles "
                                    "WHERE rolname LIKE 'jobwatch_%'"), ','.join(sorted(ROLES)))

    def test_role_privileges_and_actual_peer_mappings(self):
        flags = self.scalar("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'jobwatch_%' AND "
                            "(rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR NOT rolcanlogin)")
        self.assertEqual(flags, '0')
        self.assertEqual(self.scalar("SELECT count(*) FROM pg_authid WHERE rolname LIKE 'jobwatch_%' AND rolpassword IS NOT NULL"), '0')
        for name in ('collector', 'owner', 'migrator'):
            with self.subTest(role=name):
                result = self.command(['runuser', '-u', 'jobwatch-' + name, '--', 'psql', '-X',
                                       '-h', '/run/jobwatch-postgresql', '-U', 'jobwatch_' + name,
                                       '-d', 'jobwatch', '-Atc', 'SELECT current_user'])
                self.assertEqual(result.stdout.strip(), 'jobwatch_' + name)
                expected = 't' if name == 'migrator' else 'f'
                self.assertEqual(self.scalar(f"SELECT has_database_privilege('jobwatch_{name}','jobwatch','CREATE')"), expected)
        for name in ('collector', 'owner'):
            self.assertEqual(self.scalar(f"SELECT pg_has_role('jobwatch_{name}','jobwatch_migrator','MEMBER')"), 'f')

    def test_repeat_preserves_data_cluster_identity_and_postmaster(self):
        self.scalar("CREATE TABLE public.bootstrap_sentinel(value text); INSERT INTO public.bootstrap_sentinel VALUES ('retained evidence')")
        before = self.scalar("SELECT system_identifier || ':' || pg_postmaster_start_time() FROM pg_control_system()")
        self.shell('data_mount=/var/lib/jobwatch-postgresql\n' + self.prepare)
        result = self.shell(self.provision)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.scalar("SELECT system_identifier || ':' || pg_postmaster_start_time() FROM pg_control_system()"), before)
        self.assertEqual(self.scalar('TABLE public.bootstrap_sentinel'), 'retained evidence')

    def test_missing_root_only_sql_fails_without_creating_partial_roles(self):
        self.drop_roles()
        self.command(['mv', SQL, SQL + '.held'])
        try:
            self.assertNotEqual(self.shell(self.provision, check=False).returncode, 0)
            self.assertEqual(self.scalar("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'jobwatch_%'"), '0')
        finally:
            self.command(['mv', SQL + '.held', SQL])
            self.shell(self.provision)

    def test_on_error_stop_and_atomic_role_sql_without_sql_in_logs(self):
        self.drop_roles()
        original = (ROOT / 'sql/postgresql/bootstrap.sql').read_text()
        self.command(['bash', '-c', f'cat > {SQL}'], input=original.replace('COMMIT;', 'SELECT 1/0;\nCOMMIT;'))
        try:
            result = self.shell(self.provision, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertNotIn('CREATE ROLE', result.stderr)
            self.assertEqual(self.scalar("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'jobwatch_%'"), '0')
        finally:
            self.command(['bash', '-c', f'cat > {SQL}'], input=original)
            self.shell(self.provision)

    def test_partial_roles_refused_without_reprovisioning(self):
        self.scalar('DROP OWNED BY jobwatch_collector; DROP ROLE jobwatch_collector')
        try:
            result = self.shell(self.provision, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Partial role provisioning requires operator investigation', result.stderr)
            self.assertEqual(self.scalar("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'jobwatch_%'"), '2')
        finally:
            self.scalar('DROP OWNED BY jobwatch_owner,jobwatch_migrator; DROP ROLE jobwatch_owner,jobwatch_migrator')
            self.shell(self.provision)

    def test_missing_configuration_refuses_server_start(self):
        path = '/etc/jobwatch/postgresql.conf'
        self.stop()
        self.command(['mv', path, path + '.held'])
        try:
            self.assertNotEqual(self.command(self.start_command, check=False).returncode, 0)
        finally:
            self.command(['mv', path + '.held', path])
            self.start()

    def test_unexpected_cluster_files_refused_and_retained(self):
        for name, contents in [('nonempty', 'unavailable'), ('wrong-major', '18')]:
            with self.subTest(case=name):
                directory = '/tmp/' + name
                self.shell(f'mkdir -p {directory}/pgdata; printf retained > {directory}/pgdata/evidence')
                if contents == '18':
                    self.shell(f'printf 18 > {directory}/pgdata/PG_VERSION')
                result = self.shell('data_mount=' + directory + '\n' + self.prepare, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.command(['cat', directory + '/pgdata/evidence']).stdout, 'retained')


if __name__ == '__main__':
    unittest.main()
