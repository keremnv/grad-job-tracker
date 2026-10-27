"""Actual Linux UID/group isolation, no network, credentials or host user changes."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest

IMAGE='python@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70'


def inside():
    if not Path('/.dockerenv').is_file() or os.geteuid()!=0:
        raise RuntimeError('Use the network-disabled disposable runner')
    import ctypes
    import signal
    from jobwatch.postgres.unix_mcp import listener
    root=Path('/tmp/mcp-owner');root.mkdir(mode=0o750);os.chown(root,1001,1001)
    path=root/'mcp.sock'
    ready,notify=os.pipe()
    pid=os.fork()
    if pid==0:
        os.close(ready);os.setgroups([]);os.setgid(1001);os.setuid(1001)
        server=listener(path)
        os.write(notify,b'1');os.close(notify)
        signal.pause();server.close();os._exit(0)
    os.close(notify)
    try:
        assert os.read(ready,1)==b'1'
        # Only the corresponding tunnel group may reach the write socket.
        for uid,groups,allowed in [(2001,[1001],True),(2002,[1002],False),(2003,[999],False)]:
            child=os.fork()
            if child==0:
                os.setgroups(groups);os.setgid(uid);os.setuid(uid)
                assert ctypes.CDLL(None).prctl(38,1,0,0,0)==0  # NoNewPrivileges
                assert 'NoNewPrivs:\t1' in Path('/proc/self/status').read_text()
                try:
                    connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                    connection.connect(str(path));connection.close()
                    os._exit(0 if allowed else 1)
                except PermissionError:os._exit(1 if allowed else 0)
            assert os.waitpid(child,0)[1]==0
    finally:
        os.close(ready);os.kill(pid,signal.SIGTERM);os.waitpid(pid,0)
    print('Authorized tunnel connected; read tunnel and collector denied under NoNewPrivileges.')


class AgentSocketPermissionsTests(unittest.TestCase):
    def test_actual_unprivileged_connection_boundaries(self):
        repo=Path(__file__).resolve().parents[3]
        result=subprocess.run(['docker','run','--rm','--network','none','--read-only',
            '--tmpfs','/tmp:rw,nosuid,nodev','-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONPATH=/review/src',
            '--mount',f'type=bind,src={repo}/src,dst=/review/src,readonly',
            '--mount',f'type=bind,src={Path(__file__).resolve()},dst=/review/test.py,readonly',
            IMAGE,'python','/review/test.py','--inside'],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('collector denied',result.stdout)


if __name__=='__main__':
    if sys.argv[1:]==['--inside']:inside()
    else:unittest.main()
