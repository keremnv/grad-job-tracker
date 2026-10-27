"""Real root/non-root installer permissions; native wheel/systemd boundaries stubbed."""
from hashlib import sha256
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
import venv

IMAGE='python@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70'


def inside(mask):
    if not Path('/.dockerenv').is_file() or os.geteuid()!=0:
        raise RuntimeError('Use the disposable runner')
    sys.path.insert(0,'/review/scripts')
    import install_agent_runtime as installer
    root=Path('/fixture');root.mkdir(mode=0o755)
    (root/'opt').mkdir();(root/'etc/systemd/system').mkdir(parents=True)
    files={name:b'synthetic\n' for name in ('requirements.lock','tunnel-runtime.json','tunnel/LICENSE','tunnel/NOTICE')}
    files['tunnel/bin/tunnel-client']=b'#!/bin/sh\nprintf synthetic-runtime\n'
    files.update({'systemd/'+n:b'# synthetic inert unit\n' for n in installer.UNITS})
    manifest={'source_commit':'a'*40,'python':'3.12','architecture':'aarch64','model_inference':False,
              'files':{k:sha256(v).hexdigest() for k,v in files.items()}}
    files['MANIFEST.json']=json.dumps(manifest).encode()
    archive=root/'release.tgz'
    with tarfile.open(archive,'w:gz') as stream:
        for name,body in files.items():
            info=tarfile.TarInfo(name);info.size=len(body);stream.addfile(info,io.BytesIO(body))
    digest=sha256(archive.read_bytes()).hexdigest();commands=[];enabled=set()
    def run(args,**kwargs):
        commands.append(args)
        if args[0]=='systemctl':
            assert args[1] in ('is-active','is-enabled','daemon-reload')
            if args[1]=='is-enabled':
                # Real systemctl exits 0 for a static unit; only the state text means enabled.
                state='enabled' if args[2] in enabled else 'static' if args[2]=='jobwatch-calendar.service' else 'disabled'
                return SimpleNamespace(returncode=int(state=='disabled'),stdout=state+'\n')
            return SimpleNamespace(returncode=int(args[1].startswith('is-')))
        if args[:3]==['python3.12','-m','venv']:
            dest=Path(args[3]);venv.EnvBuilder(with_pip=False,symlinks=True).create(dest)
            for name in ('jobwatch-read-mcp','jobwatch-owner-mcp'):
                path=dest/'bin'/name;path.write_text('#!/bin/sh\nexit 0\n');path.chmod(0o700)
        return SimpleNamespace(returncode=0)
    os.umask(mask)
    interpreter_mode=Path(sys.executable).stat().st_mode
    with patch.object(installer.platform,'machine',return_value='aarch64'):
        for _ in range(2):
            result=installer.install(archive,digest,'a'*40,installation_root=root,run_command=run)
            assert not result['units_started'] and not result['database_or_secret_changes']
            destination=(root/'opt/jobwatch-agent/releases'/('a'*40))
            for path in (destination,*destination.rglob('*')):
                info=path.lstat();assert info.st_uid==0
                if not path.is_symlink():assert not info.st_mode&0o022
            for uid in (1001,1002,1003,1004):
                child=os.fork()
                if child==0:
                    os.setgroups([]);os.setgid(uid);os.setuid(uid)
                    binary=root/'opt/jobwatch-tunnel/bin/tunnel-client'
                    result=subprocess.run([str(binary)],capture_output=True)
                    assert result.returncode==0 and result.stdout==b'synthetic-runtime'
                    assert os.access(root/'opt/jobwatch-agent-runtime/bin/python',os.X_OK)
                    assert not os.access(binary,os.W_OK)
                    os._exit(0)
                assert os.waitpid(child,0)[1]==0
        assert sum(a[:3]==['python3.12','-m','venv'] for a in commands)==1
        assert Path(sys.executable).stat().st_mode==interpreter_mode
        enabled.add('jobwatch-calendar.timer')
        try:installer.install(archive,digest,'a'*40,installation_root=root,run_command=run)
        except ValueError:pass
        else:raise AssertionError('Enabled integration timer accepted')
        enabled.clear()
        try:installer.install(archive,'0'*64,'a'*40,installation_root=root,run_command=run)
        except ValueError:pass
        else:raise AssertionError('Corrupt artifact accepted')
        (destination/'INSTALL_COMPLETE').unlink()
        try:installer.install(archive,digest,'a'*40,installation_root=root,run_command=run)
        except ValueError:pass
        else:raise AssertionError('Incomplete release adopted')
    print('First/repeated immutable install passed; non-root execution and write denials verified.')


class AgentRuntimeInstallationTests(unittest.TestCase):
    def test_real_permissions_under_ordinary_and_restrictive_umask(self):
        repo=Path(__file__).resolve().parents[3]
        for mask in ('0022','0077'):
            with self.subTest(mask=mask):
                result=subprocess.run(['docker','run','--rm','--network','none',
                    '-e','PYTHONDONTWRITEBYTECODE=1',
                    '--mount',f'type=bind,src={repo}/infra/aws/scripts,dst=/review/scripts,readonly',
                    '--mount',f'type=bind,src={Path(__file__).resolve()},dst=/review/test.py,readonly',
                    IMAGE,'python','/review/test.py','--inside',mask],capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':
    if sys.argv[1:2]==['--inside']:inside(int(sys.argv[2],8))
    else:unittest.main()
